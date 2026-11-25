/** Profiler's 2D viewport: zoom, pan, fit, grid, rulers, axes, the drawing itself, snapping feedback and window/crossing selection.
 *  It draws what the server's ``render`` says and asks the server for snaps and window selections, so the screen never has a geometry rule of its own. */
import { h } from "../../../tools/web/src/dom.js";
import type { PfRender, PfEdge, PfDim, Pt } from "./profiler_api.js";
import { cleanNumber, gridLines, gridSpec as gridSpecFor, type GridSpec } from "./geom2d.js";

export interface SnapResult { mode: string; point: Pt }
export interface CanvasTool {
  cursor?: string;
  /** The point is already snapped. Return true when the click was used. */
  click(pt: Pt, event: PointerEvent): void | Promise<void>;
  move?(pt: Pt): void;
  /** Extra drawing (rubber band) in world coordinates, after the sketch. */
  preview?(draw: PreviewApi, pt: Pt | null): void;
  /** True when the snap origin (the previous point) is known, for ortho/perpendicular snaps. */
  from?(): Pt | null;
  dblclick?(pt: Pt): void;
}
export interface PreviewApi { ctx: CanvasRenderingContext2D; sx(x: number): number; sy(y: number): number; scale: number; world(pt: Pt): Pt }
export interface Filters { edges: boolean; vertices: boolean; dimensions: boolean; comments: boolean; texts: boolean }
export interface Hit { kind: "edge" | "vertex" | "dimension" | "comment" | "text" | "hatch" | "constraint"; id: string }
export interface Hooks {
  snap(raw: Pt, from: Pt | null): Promise<SnapResult | null>;
  window(rect: [number, number, number, number], mode: "window" | "crossing", filters: Filters): Promise<string[]>;
  selectionChanged(): void;
  context(hit: Hit | null, at: Pt, clientX: number, clientY: number): void;
  open(hit: Hit): void;
  coords(pt: Pt | null, snap: SnapResult | null): void;
  /** What the grid shows and snaps to has changed (a zoom, a new spacing), so the status bar can say so. */
  grid?(g: GridSpec): void;
}

/** Millimetres in one drawing unit: the grid spacing is kept in millimetres, like everywhere else in SPADRA. */
const UNIT_TO_MM: Record<string, number> = { mm: 1, cm: 10, m: 1000, in: 25.4, ft: 304.8 };
/** The finest grid the Profiler draws or snaps to by itself. A fixed spacing can still be set finer. */
export const MIN_AUTO_GRID_MM = 0.1;

const BG = "#16181d", GRID_MINOR = "#1f2229", GRID_MAJOR = "#2a2e37", RULER_BG = "#1b1e24", RULER_INK = "#8b93a1";
export const SELECT_COLOR = "#4da3ff", HOVER_COLOR = "#9fcbff";
const MODE_COLOR: Record<string, string> = { driving: "#E7C686", formula: "#8fd6a5", reference: "#9aa3b2", temporary: "#d98bff" };
const GLYPH: Record<string, string> = { horizontal: "H", vertical: "V", parallel: "∥", perpendicular: "⊥", coincident: "●", point_on: "◦", collinear: "═", equal: "=", tangent: "T", tangent_at: "T", concentric: "◎",
  symmetric: "⇋", midpoint: "M", midpoint_at: "M", fixed: "⚓", distance: "d", hdistance: "d", vdistance: "d", angle: "∠", radius: "R", diameter: "Ø", point_line_distance: "d", equal_distance: "≡" };

function distSeg(p: Pt, a: Pt, b: Pt): number {
  const dx = b[0] - a[0], dy = b[1] - a[1], l2 = dx * dx + dy * dy;
  const t = l2 === 0 ? 0 : Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2));
  return Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy));
}
function niceStep(raw: number): number { const p = 10 ** Math.floor(Math.log10(raw)); const m = raw / p; return (m < 1.5 ? 1 : m < 3.5 ? 2 : m < 7.5 ? 5 : 10) * p; }

export class ProfilerCanvas {
  readonly el = h("div", { class: "pf-canvas" });
  private readonly cv = h("canvas", { class: "pf-cv", attrs: { tabindex: "0", "aria-label": "Profile drawing area" } });
  private readonly ctx: CanvasRenderingContext2D;
  render: PfRender | null = null;
  tool: CanvasTool | null = null;
  selection = new Set<string>();
  filters: Filters = { edges: true, vertices: true, dimensions: true, comments: true, texts: true };
  showConstraints = true;
  showDimensions = true;
  /** Grid lines drawn (the axes stay). Set by the workspace from SPADRA's own grid setting. */
  showGrid = true;
  /** A fixed grid spacing in millimetres, or null when it follows the zoom. Set by the workspace from SPADRA's own grid setting. */
  gridSpacingMm: number | null = null;
  private gridKey = "";
  private needFit = false;
  hidden = new Set<string>();
  hooks!: Hooks;
  private cx = 0; private cy = 0; private scale = 4;
  private width = 1; private height = 1; private dpr = 1;
  private cursor: Pt | null = null; private snap: SnapResult | null = null;
  private hover: Hit | null = null;
  private drag: { kind: "pan" | "box"; x0: number; y0: number; x: number; y: number; cx: number; cy: number } | null = null;
  private snapSeq = 0; private snapBusy = false; private pending: { raw: Pt; from: Pt | null } | null = null;
  private frame = 0; private panKey = false;

  constructor() {
    const ctx = this.cv.getContext("2d");
    if (!ctx) throw new Error("This browser cannot draw the profile (no 2D canvas).");
    this.ctx = ctx;
    this.el.append(this.cv);
    new ResizeObserver(() => this.resize()).observe(this.el);
    this.cv.addEventListener("pointerdown", (e) => void this.down(e));
    this.cv.addEventListener("pointermove", (e) => this.move(e));
    this.cv.addEventListener("pointerup", (e) => void this.up(e));
    this.cv.addEventListener("pointerleave", () => { this.cursor = null; this.snap = null; this.hooks?.coords(null, null); this.draw(); });
    this.cv.addEventListener("wheel", (e) => this.wheel(e), { passive: false });
    this.cv.addEventListener("dblclick", (e) => this.dbl(e));
    this.cv.addEventListener("contextmenu", (e) => e.preventDefault());
    window.addEventListener("keydown", (e) => { if (e.code === "Space" && document.activeElement === this.cv) { this.panKey = true; e.preventDefault(); } });
    window.addEventListener("keyup", (e) => { if (e.code === "Space") this.panKey = false; });
  }

  // ----------------------------------------------------------------------------------------------------------- view
  private resize(): void {
    const r = this.el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return;                          // hidden (the Profiler window is minimised): keep the drawing and the view as they are
    this.dpr = window.devicePixelRatio || 1;
    this.width = Math.max(1, Math.floor(r.width)); this.height = Math.max(1, Math.floor(r.height));
    this.cv.width = Math.floor(this.width * this.dpr); this.cv.height = Math.floor(this.height * this.dpr);
    this.cv.style.setProperty("width", `${this.width}px`); this.cv.style.setProperty("height", `${this.height}px`);
    if (this.needFit) this.fit();                                   // the first fit came before the canvas had a size: do it now
    this.schedule();
  }
  setRender(r: PfRender, keepView = true): void {
    const first = this.render === null;
    this.render = r;
    const alive = new Set<string>([...r.edges.map((e) => e.id), ...r.vertices.map((v) => v.id), ...r.dimensions.map((d) => d.id), ...r.comments.map((c) => c.id), ...r.texts.map((t) => t.id)]);
    for (const id of [...this.selection]) if (!alive.has(id)) this.selection.delete(id);
    if (first || !keepView) this.fit();
    this.hooks?.selectionChanged();
    this.schedule();
  }
  fit(): void {
    const b = this.render?.bounds;
    if (!b) { this.cx = 0; this.cy = 0; this.scale = 4; this.needFit = false; this.schedule(); return; }
    if (this.width < 150 || this.height < 150) { this.needFit = true; return; }       // not laid out yet (the size is 1 x 1): resize() fits as soon as it is
    this.needFit = false;
    const w = Math.max(b[2] - b[0], 1e-6), hh = Math.max(b[3] - b[1], 1e-6);
    this.cx = (b[0] + b[2]) / 2; this.cy = (b[1] + b[3]) / 2;
    this.scale = Math.max(1e-5, Math.min(2000, (this.width - 120) / w, (this.height - 120) / hh));
    this.schedule();
  }
  zoomBy(f: number, at?: [number, number]): void {
    const px = at?.[0] ?? this.width / 2, py = at?.[1] ?? this.height / 2;
    const before = this.toWorld(px, py);
    this.scale = Math.max(1e-5, Math.min(1e7, this.scale * f));
    const after = this.toWorld(px, py);
    this.cx += before[0] - after[0]; this.cy += before[1] - after[1];
    this.schedule();
  }
  get zoomPercent(): number { return this.scale; }
  toWorld(px: number, py: number): Pt { return [this.cx + (px - this.width / 2) / this.scale, this.cy - (py - this.height / 2) / this.scale]; }
  private sx(x: number): number { return (x - this.cx) * this.scale + this.width / 2; }
  private sy(y: number): number { return this.height / 2 - (y - this.cy) * this.scale; }
  private local(e: { clientX: number; clientY: number }): [number, number] { const r = this.cv.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }
  /** The nearest thing under a screen point, client side (for hover and clicks); radius in pixels. */
  hit(px: number, py: number, radius = 7): Hit | null {
    const r = this.render; if (!r) return null;
    const w = this.toWorld(px, py), tol = radius / this.scale;
    if (this.filters.comments) for (const c of r.comments) if (Math.hypot(this.sx(c.at[0]) - px, this.sy(c.at[1]) - py) <= 11) return { kind: "comment", id: c.id };
    if (this.filters.vertices) { let best: Hit | null = null, bd = tol; for (const v of r.vertices) { const d = Math.hypot(v.x - w[0], v.y - w[1]); if (d <= bd) { bd = d; best = { kind: "vertex", id: v.id }; } } if (best) return best; }
    if (this.filters.dimensions && this.showDimensions) for (const d of r.dimensions) { const g = d.geometry; const l = g.line; if (l && l.length >= 2) for (let i = 0; i + 1 < l.length; i++) if (distSeg(w, l[i]!, l[i + 1]!) <= tol) return { kind: "dimension", id: d.id };
      if (Math.hypot(this.sx(g.label[0]) - px, this.sy(g.label[1]) - py) <= 24) return { kind: "dimension", id: d.id }; }
    if (this.filters.edges) { let best: Hit | null = null, bd = tol; for (const e of r.edges) { if (this.hidden.has(e.id)) continue; const p = e.points; for (let i = 0; i + 1 < p.length; i++) { const d = distSeg(w, p[i]!, p[i + 1]!); if (d <= bd) { bd = d; best = { kind: "edge", id: e.id }; } } } if (best) return best; }
    if (this.filters.texts) for (const t of r.texts) if (Math.hypot(this.sx(t.xy[0]) - px, this.sy(t.xy[1]) - py) <= 14) return { kind: "text", id: t.id };
    return null;
  }

  // -------------------------------------------------------------------------------------------------- pointer events
  private async snapAt(raw: Pt): Promise<SnapResult | null> {
    return this.hooks.snap(raw, this.tool?.from?.() ?? null);
  }
  private pumpSnap(): void {
    if (this.snapBusy || !this.pending) return;
    const job = this.pending; this.pending = null; this.snapBusy = true;
    const seq = ++this.snapSeq;
    void this.snapAt(job.raw).then((s) => { if (seq === this.snapSeq) { this.snap = s; this.hooks.coords(s?.point ?? job.raw, s); this.draw(); } }).catch(() => undefined)
      .finally(() => { this.snapBusy = false; this.pumpSnap(); });
  }
  private move(e: PointerEvent): void {
    const [px, py] = this.local(e);
    if (this.drag) {
      this.drag.x = px; this.drag.y = py;
      if (this.drag.kind === "pan") { this.cx = this.drag.cx - (px - this.drag.x0) / this.scale; this.cy = this.drag.cy + (py - this.drag.y0) / this.scale; }
      this.schedule(); return;
    }
    const raw = this.toWorld(px, py);
    this.cursor = raw;
    this.hover = this.tool ? null : this.hit(px, py);
    this.cv.style.setProperty("cursor", this.tool ? (this.tool.cursor ?? "crosshair") : this.hover ? "pointer" : "default");
    this.tool?.move?.(this.snap?.point ?? raw);
    this.pending = { raw, from: this.tool?.from?.() ?? null }; this.pumpSnap();
    this.hooks.coords(raw, this.snap);
    this.schedule();
  }
  private async down(e: PointerEvent): Promise<void> {
    this.cv.focus();
    const [px, py] = this.local(e);
    if (e.button === 1 || (e.button === 0 && this.panKey)) { this.drag = { kind: "pan", x0: px, y0: py, x: px, y: py, cx: this.cx, cy: this.cy }; this.cv.setPointerCapture(e.pointerId); return; }
    if (e.button === 2) { const hit = this.hit(px, py); if (hit && !this.selection.has(hit.id)) { this.selection = new Set([hit.id]); this.hooks.selectionChanged(); this.draw(); } this.hooks.context(hit, this.toWorld(px, py), e.clientX, e.clientY); return; }
    if (e.button !== 0) return;
    if (this.tool) {
      const raw = this.toWorld(px, py);
      const s = this.snap && Math.hypot(this.sx(this.snap.point[0]) - px, this.sy(this.snap.point[1]) - py) < 24 ? this.snap : await this.snapAt(raw);
      await this.tool.click(s?.point ?? raw, e); this.schedule(); return;
    }
    const hit = this.hit(px, py);
    if (hit) { const additive = e.shiftKey || e.ctrlKey || e.metaKey;
      if (additive) { if (this.selection.has(hit.id)) this.selection.delete(hit.id); else this.selection.add(hit.id); } else this.selection = new Set([hit.id]);
      this.hooks.selectionChanged(); this.draw(); return; }
    this.drag = { kind: "box", x0: px, y0: py, x: px, y: py, cx: 0, cy: 0 }; this.cv.setPointerCapture(e.pointerId);
  }
  private async up(e: PointerEvent): Promise<void> {
    const d = this.drag; if (!d) return;
    this.drag = null;
    try { this.cv.releasePointerCapture(e.pointerId); } catch { /* already released */ }
    if (d.kind === "box") {
      const moved = Math.hypot(d.x - d.x0, d.y - d.y0) > 4;
      if (!moved) { if (!(e.shiftKey || e.ctrlKey || e.metaKey)) { this.selection.clear(); this.hooks.selectionChanged(); } this.draw(); return; }
      const a = this.toWorld(d.x0, d.y0), b = this.toWorld(d.x, d.y);
      const mode = d.x >= d.x0 ? "window" : "crossing";                     // left to right encloses, right to left touches
      const ids = await this.hooks.window([Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])], mode, this.filters);
      if (e.shiftKey || e.ctrlKey || e.metaKey) ids.forEach((i) => this.selection.add(i)); else this.selection = new Set(ids);
      this.hooks.selectionChanged();
    }
    this.draw();
  }
  private dbl(e: MouseEvent): void {
    const [px, py] = this.local(e);
    if (this.tool) { this.tool.dblclick?.(this.snap?.point ?? this.toWorld(px, py)); return; }
    const hit = this.hit(px, py); if (hit) this.hooks.open(hit);
  }
  private wheel(e: WheelEvent): void { e.preventDefault(); const [px, py] = this.local(e); this.zoomBy(e.deltaY < 0 ? 1.15 : 1 / 1.15, [px, py]); }
  setSelection(ids: string[]): void { this.selection = new Set(ids); this.hooks.selectionChanged(); this.draw(); }
  selectAll(): void {
    const r = this.render; if (!r) return;
    this.selection = new Set([...(this.filters.edges ? r.edges.filter((x) => !this.hidden.has(x.id)).map((x) => x.id) : []), ...(this.filters.vertices ? r.vertices.map((v) => v.id) : [])]);
    this.hooks.selectionChanged(); this.draw();
  }

  // ------------------------------------------------------------------------------------------------------- drawing
  schedule(): void { if (!this.frame) this.frame = requestAnimationFrame(() => { this.frame = 0; this.draw(); }); }
  /** What the grid shows and snaps to at this zoom, in drawing units. Automatic: lines every 1, 2 or 5 times a power of ten, never finer than 0.1 mm. Fixed: lines every
   *  multiple of the spacing that keeps them a readable distance apart, the pointer snapping to the spacing itself (see ``gridSpec`` in geom2d). */
  gridSpec(): GridSpec {
    const mm = UNIT_TO_MM[this.render?.unit ?? "mm"] ?? 1;
    const g = gridSpecFor(this.scale, this.gridSpacingMm === null ? null : this.gridSpacingMm / mm);
    const floor = cleanNumber(MIN_AUTO_GRID_MM / mm);
    return g.auto && g.display < floor ? { ...g, display: floor, snap: floor, major: cleanNumber(floor * 5) } : g;
  }
  private noteGrid(): GridSpec {
    const g = this.gridSpec(); const key = `${g.display}|${g.snap}|${g.auto}|${g.collapsed}`;
    if (key !== this.gridKey) { this.gridKey = key; this.hooks?.grid?.(g); }
    return g;
  }
  private drawGrid(c: CanvasRenderingContext2D, g: GridSpec): void {
    const w0 = this.toWorld(0, this.height), w1 = this.toWorld(this.width, 0);
    c.lineWidth = 1;
    const xs = gridLines(w0[0], w1[0], g.display), ys = gridLines(w0[1], w1[1], g.display);
    for (const kind of ["minor", "major"] as const) {
      c.strokeStyle = kind === "minor" ? GRID_MINOR : GRID_MAJOR; c.beginPath();
      for (const l of xs) if ((l.kind === "minor") === (kind === "minor")) { const px = Math.round(this.sx(l.value)) + 0.5; c.moveTo(px, 0); c.lineTo(px, this.height); }
      for (const l of ys) if ((l.kind === "minor") === (kind === "minor")) { const py = Math.round(this.sy(l.value)) + 0.5; c.moveTo(0, py); c.lineTo(this.width, py); }
      c.stroke();
    }
  }
  private drawRulers(c: CanvasRenderingContext2D): void {
    const T = 18; c.fillStyle = RULER_BG; c.fillRect(0, 0, this.width, T); c.fillRect(0, 0, T, this.height);
    c.fillStyle = RULER_INK; c.strokeStyle = RULER_INK; c.font = "10px system-ui, sans-serif"; c.lineWidth = 1;
    const step = niceStep(70 / this.scale);
    const w0 = this.toWorld(0, this.height), w1 = this.toWorld(this.width, 0);
    c.textBaseline = "top"; c.textAlign = "left"; c.beginPath();
    for (let i = Math.ceil(w0[0] / step); i * step <= w1[0]; i++) { const x = cleanNumber(i * step), px = Math.round(this.sx(x)) + 0.5; c.moveTo(px, T - 5); c.lineTo(px, T); c.fillText(String(x), px + 2, 2); }      // index times step: no running sum, so 0 is 0 and not 2.8e-17
    for (let i = Math.ceil(w0[1] / step); i * step <= w1[1]; i++) { const y = cleanNumber(i * step), py = Math.round(this.sy(y)) + 0.5; c.moveTo(T - 5, py); c.lineTo(T, py); c.save(); c.translate(2, py - 2); c.rotate(-Math.PI / 2); c.fillText(String(y), 0, 0); c.restore(); }
    c.stroke();
    c.fillStyle = RULER_BG; c.fillRect(0, 0, T, T);
    if (this.cursor) { c.strokeStyle = SELECT_COLOR; c.beginPath(); c.moveTo(this.sx(this.cursor[0]) + 0.5, 0); c.lineTo(this.sx(this.cursor[0]) + 0.5, T); c.moveTo(0, this.sy(this.cursor[1]) + 0.5); c.lineTo(T, this.sy(this.cursor[1]) + 0.5); c.stroke(); }
  }
  private strokeEdge(c: CanvasRenderingContext2D, e: PfEdge): void {
    const p = e.points; if (p.length < 2) return;
    c.beginPath(); c.moveTo(this.sx(p[0]![0]), this.sy(p[0]![1]));
    for (let i = 1; i < p.length; i++) c.lineTo(this.sx(p[i]![0]), this.sy(p[i]![1]));
    c.stroke();
  }
  private arrow(c: CanvasRenderingContext2D, from: Pt, to: Pt): void {
    const a = Math.atan2(this.sy(to[1]) - this.sy(from[1]), this.sx(to[0]) - this.sx(from[0])), x = this.sx(from[0]), y = this.sy(from[1]), s = 7;
    c.beginPath(); c.moveTo(x, y); c.lineTo(x + s * Math.cos(a + 0.35), y + s * Math.sin(a + 0.35)); c.moveTo(x, y); c.lineTo(x + s * Math.cos(a - 0.35), y + s * Math.sin(a - 0.35)); c.stroke();
  }
  private drawDim(c: CanvasRenderingContext2D, d: PfDim, selected: boolean): void {
    const g = d.geometry; const color = d.state !== "ok" || d.unresolved ? "#ff6b6b" : selected ? SELECT_COLOR : MODE_COLOR[d.mode] ?? "#9aa3b2";
    c.strokeStyle = color; c.fillStyle = color; c.lineWidth = selected ? 2 : 1; c.setLineDash(d.mode === "reference" ? [4, 3] : []);
    for (const seg of g.ext ?? []) { c.beginPath(); c.moveTo(this.sx(seg[0]![0]), this.sy(seg[0]![1])); c.lineTo(this.sx(seg[1]![0]), this.sy(seg[1]![1])); c.stroke(); }
    const l = g.line;
    if (l && l.length >= 2) { c.beginPath(); c.moveTo(this.sx(l[0]![0]), this.sy(l[0]![1])); for (let i = 1; i < l.length; i++) c.lineTo(this.sx(l[i]![0]), this.sy(l[i]![1])); c.stroke();
      if (g.kind === "linear" || g.kind === "radial") { c.setLineDash([]); this.arrow(c, l[0]!, l[1]!); this.arrow(c, l[l.length - 1]!, l[l.length - 2]!); } }
    if (g.arc && g.arc.length >= 2) { c.beginPath(); c.moveTo(this.sx(g.arc[0]![0]), this.sy(g.arc[0]![1])); for (const q of g.arc) c.lineTo(this.sx(q[0]), this.sy(q[1])); c.stroke(); }
    c.setLineDash([]); c.font = "12px system-ui, sans-serif"; c.textAlign = "center"; c.textBaseline = "bottom";
    const label = d.mode === "reference" ? `(${d.text})` : d.text;
    c.fillText(label, this.sx(g.label[0]), this.sy(g.label[1]) - 2);
  }
  draw(): void {
    const c = this.ctx; c.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    c.fillStyle = BG; c.fillRect(0, 0, this.width, this.height);
    const r = this.render; if (!r) return;
    const grid = this.noteGrid();
    if (this.showGrid) this.drawGrid(c, grid);                       // a hidden grid keeps its axes: the world zero stays marked
    if (r.view.axes) { c.lineWidth = 1; c.strokeStyle = "#b5524f"; c.beginPath(); c.moveTo(0, Math.round(this.sy(0)) + 0.5); c.lineTo(this.width, Math.round(this.sy(0)) + 0.5); c.stroke();
      c.strokeStyle = "#4f9b6a"; c.beginPath(); c.moveTo(Math.round(this.sx(0)) + 0.5, 0); c.lineTo(Math.round(this.sx(0)) + 0.5, this.height); c.stroke(); }
    for (const hch of r.hatches) { c.strokeStyle = hch.color; c.globalAlpha = 0.7; c.lineWidth = 1; c.beginPath(); for (const s of hch.segments) { c.moveTo(this.sx(s[0][0]), this.sy(s[0][1])); c.lineTo(this.sx(s[1][0]), this.sy(s[1][1])); } c.stroke(); c.globalAlpha = 1; }
    c.lineJoin = "round"; c.lineCap = "round";
    const hover = this.hover;
    for (const e of r.edges) {
      if (this.hidden.has(e.id)) continue;
      const sel = this.selection.has(e.id), hov = hover?.kind === "edge" && hover.id === e.id;
      c.strokeStyle = sel ? SELECT_COLOR : hov ? HOVER_COLOR : e.color; c.lineWidth = sel ? 2.4 : hov ? 2 : 1.4;
      c.globalAlpha = e.construction ? 0.55 : 1; c.setLineDash(e.construction ? [7, 5] : []);
      this.strokeEdge(c, e);
    }
    c.globalAlpha = 1; c.setLineDash([]);
    for (const v of r.vertices) {
      const sel = this.selection.has(v.id), hov = hover?.kind === "vertex" && hover.id === v.id;
      if (!sel && !hov && this.scale < 1.2) continue;
      const x = this.sx(v.x), y = this.sy(v.y), s = sel || hov ? 4 : 2.5;
      c.fillStyle = sel ? SELECT_COLOR : hov ? HOVER_COLOR : "#8b93a1"; c.fillRect(x - s, y - s, s * 2, s * 2);
    }
    if (this.showDimensions) for (const d of r.dimensions) this.drawDim(c, d, this.selection.has(d.id) || (hover?.kind === "dimension" && hover.id === d.id));
    if (this.showConstraints) { c.font = "11px system-ui, sans-serif"; c.textAlign = "center"; c.textBaseline = "middle";
      for (const k of r.constraints) { if (!k.enabled && k.origin === "temporary") continue;
        const x = this.sx(k.at[0]) + 8, y = this.sy(k.at[1]) - 8; c.fillStyle = k.state === "ok" ? (k.enabled ? "#6c7686" : "#444a55") : "#ff6b6b"; c.fillText(GLYPH[k.kind] ?? "·", x, y); } }
    c.font = "13px system-ui, sans-serif"; c.textAlign = "left"; c.textBaseline = "alphabetic";
    for (const t of r.texts) { c.save(); c.translate(this.sx(t.xy[0]), this.sy(t.xy[1])); c.rotate(-t.rotation * Math.PI / 180); c.fillStyle = this.selection.has(t.id) ? SELECT_COLOR : t.color ?? r.default_color;
      c.font = `${Math.max(7, t.height * this.scale)}px system-ui, sans-serif`; c.fillText(t.text, 0, 0); c.restore(); }
    for (const m of r.comments) { const x = this.sx(m.at[0]), y = this.sy(m.at[1]); c.beginPath(); c.arc(x, y, 9, 0, Math.PI * 2);
      c.fillStyle = m.resolved ? "#4b515c" : "#E7C686"; c.fill(); c.lineWidth = this.selection.has(m.id) ? 2.5 : 1; c.strokeStyle = this.selection.has(m.id) ? SELECT_COLOR : "#111"; c.stroke();
      c.fillStyle = "#111"; c.font = "bold 11px system-ui, sans-serif"; c.textAlign = "center"; c.textBaseline = "middle"; c.fillText(m.replies.length ? String(m.replies.length + 1) : "…", x, y); }
    if (this.tool?.preview) this.tool.preview({ ctx: c, sx: (x) => this.sx(x), sy: (y) => this.sy(y), scale: this.scale, world: (p) => p }, this.snap?.point ?? this.cursor);
    if (this.snap && this.tool) { const x = this.sx(this.snap.point[0]), y = this.sy(this.snap.point[1]); c.strokeStyle = "#ffd75e"; c.lineWidth = 1.5; c.strokeRect(x - 6, y - 6, 12, 12);
      c.fillStyle = "#ffd75e"; c.font = "11px system-ui, sans-serif"; c.textAlign = "left"; c.textBaseline = "top"; c.fillText(this.snap.mode, x + 9, y + 8); }
    if (this.drag?.kind === "box") { const d = this.drag, crossing = d.x < d.x0; c.strokeStyle = crossing ? "#8fd6a5" : SELECT_COLOR; c.fillStyle = crossing ? "rgba(143,214,165,.12)" : "rgba(77,163,255,.12)"; c.setLineDash(crossing ? [5, 4] : []);
      c.lineWidth = 1; c.fillRect(d.x0, d.y0, d.x - d.x0, d.y - d.y0); c.strokeRect(d.x0 + 0.5, d.y0 + 0.5, d.x - d.x0, d.y - d.y0); c.setLineDash([]); }
    if (r.view.rulers) this.drawRulers(c);
  }
}
