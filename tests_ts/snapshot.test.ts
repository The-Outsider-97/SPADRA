import assert from "node:assert/strict";
import { test } from "node:test";
import { cameraEye, cameraMatrices, HOME_VIEW } from "../src/client/math3d.js";
import {
  ASPECTS, defaultSettings, MAX_PIXELS, meshArrays, qualityDefaults, RESOLUTIONS, sizeFor, snapshotCamera, snapshotFilename, type SnapshotInput,
} from "../src/client/snapshot_render.js";
import { sizeOf } from "../src/client/snapshot_ui.js";
import { defaultVisual } from "../src/client/visual_shading.js";
import type { Pt3 } from "../src/client/types.js";

const input = (over: Partial<SnapshotInput> = {}): SnapshotInput => ({
  items: [], bounds: [[0, 0, 0], [10, 6, 3]], camera: { ...HOME_VIEW, distance: 30, target: [1, 2, 3], ortho: true }, extent: 6, grid: null, axes: null, visual: defaultVisual(), viewHeight: 600,
  colours: { paper: [1, 1, 1], ink: [0, 0, 0], grid: [0.8, 0.8, 0.8], axisX: [1, 0, 0], axisY: [0, 1, 0] }, ...over,
});

test("the presets give the usual sizes: 1080p, 1440p and 4K are 16:9", () => {
  const edge = (id: string): number => RESOLUTIONS.find((r) => r.id === id)?.edge ?? 0;
  assert.deepEqual(sizeFor(edge("1080p"), 16 / 9), { width: 1920, height: 1080 });
  assert.deepEqual(sizeFor(edge("1440p"), 16 / 9), { width: 2560, height: 1440 });
  assert.deepEqual(sizeFor(edge("4k"), 16 / 9), { width: 3840, height: 2160 });
  assert.deepEqual(sizeFor(edge("1080p"), 1), { width: 1920, height: 1920 });
});

test("the long edge stays the long edge for a portrait ratio", () => {
  assert.deepEqual(sizeFor(1920, 9 / 16), { width: 1080, height: 1920 });
  const s = sizeFor(1920, 0);                                                         // a nonsense ratio falls back rather than producing NaN
  assert.ok(Number.isFinite(s.width) && s.width > 0 && Number.isFinite(s.height) && s.height > 0);
});

test("the picture size is independent of the view's size: only its aspect matters, and only when asked", () => {
  const base = { resolution: "1080p", aspect: "view", customW: 640, customH: 480, customRatioW: 3, customRatioH: 2 };
  assert.deepEqual(sizeOf(base, 1.6), { width: 1920, height: 1200 });
  assert.deepEqual(sizeOf({ ...base, aspect: "16:9" }, 1.6), { width: 1920, height: 1080 });
  assert.deepEqual(sizeOf({ ...base, aspect: "custom" }, 1.6), { width: 1920, height: 1280 });
  assert.deepEqual(sizeOf({ ...base, resolution: "custom" }, 1.6), { width: 640, height: 480 });
  assert.deepEqual(sizeOf({ ...base, resolution: "4k" }, 1.6), { width: 3840, height: 2400 });
  assert.ok(ASPECTS.some((a) => a.id === "view") && 3840 * 2400 < MAX_PIXELS);
});

test("quality presets: draft is fast (no occlusion), the rest have it; shadows are the mode's, not a quality option", () => {
  assert.deepEqual(qualityDefaults("draft"), { antialias: true, occlusion: false, toneMapping: true });
  for (const q of ["standard", "high", "maximum"] as const) assert.deepEqual(qualityDefaults(q), { antialias: true, occlusion: true, toneMapping: true });
  const d = defaultSettings(1920, 1080);
  assert.equal(d.format, "png"); assert.equal(d.background.kind, "paper"); assert.equal(d.overlays.edges, false);
  assert.equal("shadows" in d, false); assert.equal(d.visual, undefined);                         // the picture starts from the view's own mode
});

test("the snapshot camera is the view's own unless asked, and never changes the view's", () => {
  const i = input();
  const same = snapshotCamera(i, defaultSettings(1920, 1080));
  assert.deepEqual(same, i.camera);
  assert.notEqual(same, i.camera);
  assert.notEqual(same.target, i.camera.target);
  const persp = snapshotCamera(i, { ...defaultSettings(1920, 1080), projection: "perspective" });
  assert.equal(persp.ortho, false); assert.equal(i.camera.ortho, true);
  const fit = snapshotCamera(i, { ...defaultSettings(1920, 1080), framing: "fit" });
  assert.deepEqual(fit.target, [5, 3, 1.5]);
  assert.deepEqual(i.camera.target, [1, 2, 3]);
  assert.equal(i.camera.distance, 30);
});

test("fit framing puts every corner of the model inside the picture, for both projections and any aspect", () => {
  const i = input();
  const lo = i.bounds?.[0] as Pt3, hi = i.bounds?.[1] as Pt3;
  for (const projection of ["orthographic", "perspective"] as const) for (const [w, h] of [[1920, 1080], [1000, 1000], [1080, 1920]] as const) {
    const cam = snapshotCamera(i, { ...defaultSettings(w, h), projection, framing: "fit" });
    const { viewProjection } = cameraMatrices(cam, w, h, i.extent);
    for (let k = 0; k < 8; k++) {
      const p: Pt3 = [k & 1 ? hi[0] : lo[0], k & 2 ? hi[1] : lo[1], k & 4 ? hi[2] : lo[2]];
      const x = (viewProjection[0] ?? 0) * p[0] + (viewProjection[4] ?? 0) * p[1] + (viewProjection[8] ?? 0) * p[2] + (viewProjection[12] ?? 0);
      const y = (viewProjection[1] ?? 0) * p[0] + (viewProjection[5] ?? 0) * p[1] + (viewProjection[9] ?? 0) * p[2] + (viewProjection[13] ?? 0);
      const wv = (viewProjection[3] ?? 0) * p[0] + (viewProjection[7] ?? 0) * p[1] + (viewProjection[11] ?? 0) * p[2] + (viewProjection[15] ?? 0);
      assert.ok(Math.abs(x / wv) <= 1.0001 && Math.abs(y / wv) <= 1.0001, `${projection} ${w}x${h} corner ${k} is outside: ${x / wv}, ${y / wv}`);
    }
    assert.ok(cameraEye(cam).every(Number.isFinite));
  }
});

test("mesh arrays carry a unit flat normal per triangle and skip faces that refer to nothing", () => {
  const v: Pt3[] = [[0, 0, 0], [2, 0, 0], [0, 2, 0]];
  const { positions, edges } = meshArrays(v, [[0, 1, 2], [0, 1, 9]], [[0, 1], [1, 2], [0, 9]]);
  assert.equal(positions.length, 18);
  for (let k = 0; k < 3; k++) assert.deepEqual([positions[k * 6 + 3], positions[k * 6 + 4], positions[k * 6 + 5]], [0, 0, 1]);
  assert.equal(edges.length, 12);
});

test("a degenerate triangle does not produce NaN normals", () => {
  const { positions } = meshArrays([[0, 0, 0], [1, 0, 0], [2, 0, 0]], [[0, 1, 2]], []);
  assert.ok(Array.from(positions).every(Number.isFinite));
});

test("file names are safe and say the size", () => {
  assert.equal(snapshotFilename("Villa: Nord/Süd?", { width: 1920, height: 1080, format: "png" }), "Villa-NordSüd-3d-1920x1080.png");
  assert.equal(snapshotFilename("", { width: 640, height: 480, format: "jpeg" }), "spadra-3d-640x480.jpg");
  assert.ok(!snapshotFilename("a".repeat(500), { width: 1, height: 1, format: "png" }).includes("a".repeat(100)));
});
