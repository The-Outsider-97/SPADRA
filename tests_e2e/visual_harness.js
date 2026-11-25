// A deterministic page for the 3D visualization tests: the real ModelView, Store and snapshot renderer on a small fixed scene, with a stand-in for the server
// (it applies view.set_visual with the same rules, from visual_shading.ts, and answers the project tree). Nothing here is a mock of the drawing code.
import { Store } from "/SPADRA/src/client/store.js";
import { ModelView } from "/SPADRA/src/client/view3d.js";
import { renderSnapshot, defaultSettings } from "/SPADRA/src/client/snapshot_render.js";
import { updatedVisual, normaliseVisual } from "/SPADRA/src/client/visual_shading.js";

const box = (id, name, category, [x0, y0, z0], [x1, y1, z1], color) => ({
  id, name, category, material: null, color, vertices: [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0], [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]],
  faces: [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]],
});
export const SCENE = {
  unit: "m", revision: 1, bounds: [[0, 0, -0.2], [8, 6, 3]],
  objects: [
    box("floor", "Floor", "floor", [0, 0, -0.2], [8, 6, 0], "#d8d0c0"),
    box("wall", "Wall", "wall", [2, 1, 0], [2.3, 5, 3], "#cc3333"),
    box("column", "Column", "column", [5, 3, 0], [5.4, 3.4, 2], "#3366cc"),
  ],
};

const state = { posts: [], gets: [], revision: 1, visuals: { v1: null, v2: null } };
const summary = () => ({ id: "p", name: "Harness", unit: "m", revision: state.revision, dirty: true, path: null, can_undo: true, can_redo: false, undo_label: null, warnings: [], auto_rooms: false });
const treeResponse = () => ({ project: summary(), levels: [], tree: { sections: [{ id: "views", label: "Views", children: [
  { id: "v1", label: "3D Model", view_kind: "3d", open: { kind: "3d", id: "v1" }, visual: normaliseVisual(state.visuals.v1) },
  { id: "v2", label: "3D View 2", view_kind: "3d", open: { kind: "3d", id: "v2" }, visual: normaliseVisual(state.visuals.v2) }] }] } });

const store = new Store();
store.api = {
  get: async (path) => { state.gets.push(path); if (path === "/project/tree") return treeResponse(); throw new Error(`unexpected GET ${path}`); },
  post: async (path, body) => {
    state.posts.push({ path, body });
    if (path === "/command" && body.op === "view.set_visual") {
      state.visuals[body.params.id] = updatedVisual(state.visuals[body.params.id], body.params.patch);
      state.revision += 1;
      return { revision: state.revision, result: {}, project: summary() };
    }
    throw new Error(`unexpected POST ${path}`);
  },
};
store.project = summary();
store.tree = treeResponse().tree.sections;
store.tabs = [{ ref: { kind: "3d", id: "v1" }, title: "3D Model", format: "SGEO" }];
store.active = 0;

const stage = document.getElementById("stage");
const view = new ModelView(store);
stage.append(view.el);
view.setScene(SCENE);
await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));

/** The picture the view draws now, as RGBA pixels (decoded from the view's own PNG snapshot). */
async function decode(src) {
  const img = new Image();
  img.src = src;
  await img.decode();
  const c = document.createElement("canvas");
  c.width = img.naturalWidth; c.height = img.naturalHeight;
  const g = c.getContext("2d", { willReadFrequently: true });
  g.drawImage(img, 0, 0);
  return g.getImageData(0, 0, c.width, c.height);
}
const keep = new Map();
const remember = (name, image) => { keep.set(name, image); return { width: image.width, height: image.height }; };

window.H = {
  store, view, state, SCENE,
  size: () => ({ width: view.el.getBoundingClientRect().width, height: view.el.getBoundingClientRect().height }),
  /** Where a world point is on screen, in CSS pixels. */
  at: (p) => view.project(p),
  async captureView(name) { return remember(name, await decode(view.snapshot())); },
  async captureSnapshot(name, patch = {}) {
    const s = { ...defaultSettings(800, 600), antialias: false, occlusion: false, toneMapping: false, ...patch };
    const blob = await renderSnapshot(view.snapshotInput(), s);
    return remember(name, await decode(URL.createObjectURL(blob)));
  },
  pixel(name, x, y) { const im = keep.get(name); const i = (Math.round(y) * im.width + Math.round(x)) * 4; return [im.data[i], im.data[i + 1], im.data[i + 2], im.data[i + 3]]; },
  /** The mean and the share of pixels that differ by more than ``tolerance`` (0..255, per channel, largest of the three) between two kept pictures. */
  compare(a, b, tolerance = 8) {
    const A = keep.get(a), B = keep.get(b);
    let sum = 0, over = 0;
    const n = A.width * A.height;
    for (let i = 0; i < n; i++) {
      let d = 0;
      for (let k = 0; k < 3; k++) d = Math.max(d, Math.abs(A.data[i * 4 + k] - B.data[i * 4 + k]));
      sum += d; if (d > tolerance) over++;
    }
    return { mean: sum / n, over: over / n, width: A.width, height: A.height };
  },
  /** The coverage (in pixels) of a dark vertical line crossing the row ``y`` near ``x``: the integral of how far each pixel is from the line's own background. */
  lineWidth(name, x, y, ink = 0.1, reach = 7) {
    const im = keep.get(name);
    const L = (px) => { const i = (Math.round(y) * im.width + px) * 4; return (0.2126 * im.data[i] + 0.7152 * im.data[i + 1] + 0.0722 * im.data[i + 2]) / 255; };
    const x0 = Math.round(x) - reach, x1 = Math.round(x) + reach;
    const left = L(x0), right = L(x1);
    let cover = 0;
    for (let px = x0; px <= x1; px++) {
      const base = left + ((right - left) * (px - x0)) / (x1 - x0);
      cover += Math.max(0, Math.min(1, (base - L(px)) / Math.max(1e-6, base - ink)));
    }
    return cover;
  },
  keep,
};
window.HARNESS_READY = true;
