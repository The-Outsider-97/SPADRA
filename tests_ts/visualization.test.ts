import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  BASE_EDGE_PX, controlsFor, defaultVisual, edgeFactor, edgePixels, fitLight, MODES, normaliseVisual, renderPlan, shadowsOn, sunDirection, updatedVisual, VisualError,
  type Visual, type VisualPatch,
} from "../src/client/visual_shading.js";

interface Cases {
  defaults: Visual;
  normalise: Array<{ name: string; raw: unknown; expect: Visual }>;
  refused: Array<{ name: string; patch: unknown }>;
  shadows_on: Array<{ visual: unknown; expect: boolean }>;
  sun: Array<{ azimuth: number; elevation: number; expect: number[] }>;
}
// the same table visualization.py's tests read, so the two implementations cannot drift apart
const CASES = JSON.parse(readFileSync(new URL(process.env["VISUAL_CASES"] ?? "../tests/visual_cases.json", import.meta.url), "utf8")) as Cases;

test("the defaults are the table's defaults", () => assert.deepEqual(defaultVisual(), CASES.defaults));

test("normalising a stored value gives what the shared table says", () => {
  for (const c of CASES.normalise) assert.deepEqual(normaliseVisual(c.raw), c.expect, c.name);
});

test("every change the shared table refuses is refused, with a sentence, and the setting is left as it was", () => {
  const base = updatedVisual(null, { mode: "lined", edge_boost: 150 });
  const before = structuredClone(base);
  for (const c of CASES.refused) {
    assert.throws(() => updatedVisual(base, c.patch as VisualPatch), (e: unknown) => e instanceof VisualError && e.message.length > 10, c.name);
    assert.deepEqual(base, before, c.name);
  }
});

test("shadows: never in wireframe, always in realistic, chosen in the other three", () => {
  for (const c of CASES.shadows_on) assert.equal(shadowsOn(normaliseVisual(c.visual)), c.expect, JSON.stringify(c.visual));
});

test("the sun direction is the unit vector the table gives", () => {
  for (const c of CASES.sun) {
    const d = sunDirection(c.azimuth, c.elevation);
    for (let i = 0; i < 3; i++) assert.ok(Math.abs((d[i] ?? 9) - (c.expect[i] ?? 0)) < 1e-9, `${c.azimuth}/${c.elevation}`);
  }
});

test("edge thickness +50 % .. +200 % is 1.5x .. 3.0x", () => {
  assert.equal(edgeFactor(50), 1.5); assert.equal(edgeFactor(100), 2); assert.equal(edgeFactor(150), 2.5); assert.equal(edgeFactor(200), 3);
});

test("each mode asks for what the specification says", () => {
  const plan = (mode: string, extra: VisualPatch = {}) => renderPlan(updatedVisual(null, { mode: mode as never, ...extra }));
  const w = plan("wireframe"); assert.equal(w.faces, "none"); assert.equal(w.shadows, false); assert.ok(w.edges);
  const l = plan("lined", { edge_boost: 200 }); assert.equal(l.faces, "stylised"); assert.equal(l.edges?.factor, 3); assert.equal(l.shadows, false);
  assert.equal(plan("lined", { shadows: { lined: true } }).shadows, true);
  const r = plan("realistic"); assert.equal(r.faces, "lit"); assert.equal(r.shadows, true); assert.equal(r.edges, null); assert.equal(r.enhancements, true);
  const c = plan("colored"); assert.equal(c.faces, "solid"); assert.equal(c.shadows, false); assert.equal(c.enhancements, false);
  const g = plan("gray", { shadows: { gray: true } }); assert.equal(g.faces, "gray"); assert.equal(g.shadows, true);
  for (const mode of MODES) if (mode !== "realistic") assert.equal(plan(mode).enhancements, false, mode);
});

test("only the lined mode has edge thickness, and the sun is offered exactly when shadows are cast", () => {
  const v = (mode: string, shadows = false): Visual => updatedVisual(null, { mode: mode as never, shadows: { lined: shadows, colored: shadows, gray: shadows } });
  assert.deepEqual(controlsFor(v("wireframe")), { mode: true, edgeThickness: false, shadowChoice: false, sun: false });
  assert.deepEqual(controlsFor(v("lined")), { mode: true, edgeThickness: true, shadowChoice: true, sun: false });
  assert.deepEqual(controlsFor(v("lined", true)), { mode: true, edgeThickness: true, shadowChoice: true, sun: true });
  assert.deepEqual(controlsFor(v("realistic")), { mode: true, edgeThickness: false, shadowChoice: false, sun: true });
  assert.deepEqual(controlsFor(v("colored")), { mode: true, edgeThickness: false, shadowChoice: true, sun: false });
  assert.deepEqual(controlsFor(v("gray", true)), { mode: true, edgeThickness: false, shadowChoice: true, sun: true });
});

test("settings of the modes that are not shown are kept: Lined, Realistic, Lined finds Lined as it was left", () => {
  let v = updatedVisual(null, { mode: "lined", edge_boost: 175, shadows: { lined: true } });
  v = updatedVisual(v, { mode: "realistic" });
  v = updatedVisual(v, { mode: "lined" });
  assert.equal(v.edge_boost, 175); assert.equal(shadowsOn(v), true);
});

test("edge lines are the baseline weight times the factor, scaled with the picture", () => {
  const lined = renderPlan(updatedVisual(null, { mode: "lined", edge_boost: 100 }));
  assert.equal(edgePixels(lined), BASE_EDGE_PX * 2);
  assert.equal(edgePixels(lined, 2), BASE_EDGE_PX * 4);
  assert.equal(edgePixels(renderPlan(updatedVisual(null, { mode: "realistic" }))), 0);
});

test("the shadow map is fitted around the model and looks along the sun", () => {
  const fit = fitLight([[0, 0, 0], [10, 6, 3]], [0, 0, 1]);
  assert.deepEqual(fit.target, [5, 3, 1.5]);
  assert.ok(fit.eye[2] > fit.target[2]);
  assert.deepEqual(fit.up, [0, 1, 0]);                                  // straight down: the usual up would be parallel to the view
  assert.ok(fit.half >= Math.hypot(10, 6, 3) / 2);
});
