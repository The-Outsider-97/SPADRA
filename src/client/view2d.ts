/** The 2D drawing editor: a canvas that shows an SDRW document and turns pointer input into commands.
 *  The canvas draws what the server returned; every change is a command the server validates and records for undo. */
import { h } from "../../../tools/web/src/dom.js";
import { promptDialog, toast } from "../../../tools/web/src/ui.js";
import { addLayerDialog, hatchDialog, mirrorKeepDialog, numberPrompt, type HatchChoice } from "./dialogs.js";
import { clamp } from "../../../tools/web/src/util.js";
import {
  alongFrom, angleBetween, closedLoop, distToSegment, dist, entityDistance, entityBounds, fitBox, formatDimension, gripAt, grips, hatchLoops, insideBox, mid, niceStep, normaliseRect, orthoConstrain,
  pointInPolygon, polygonDistance, polygonsBounds, rotateAbout, roundAngle, segments, snapPoint, textBox, wallBand, type Grip, type Snap,
} from "./geom2d.js";
import { cssColor, withAlpha } from "./palette.js";
import { cutStyle, cutWeight, hatchFill, patternPath, levelLabel, markArrows, markLabel, itemContains, projectionAt, snapPolygons, visibleLevels } from "./projection.js";
import type { Store, Tool } from "./store.js";
import type { DrawingData, Entity, GridLabel, HatchSpec, PlanSymbol, PlanWall, ProjItem, Projection, Pt, RoomLabel, SectionMark } from "./types.js";

const MM_PER = { mm: 1, cm: 10, m: 1000 } as const;
const SNAP_PX = 10;
const PICK_PX = 6;
const DRAG_PX = 4;
/** Tools that act on whatever curve is clicked, and the kinds each one accepts. */
const TOOL_KINDS: Record<string, readonly string[]> = {
  offset: ["line", "polyline", "circle"], trim: ["line", "polyline"], extend: ["line", "polyline"], fillet: ["line"],
};
const PAINT_ORDER = ["foundation", "floor", "roof", "room", "extrusion", "sweep", "generic", "stair", "wall", "structural", "window", "door", "opening_profile", "column", "gutter", "downpipe", "void", "grid"];
const CURVE_TOOLS = new Set<string>(["offset", "trim", "extend", "fillet", "hatch"]);

/** An edit being dragged with a handle: a wall end, a turn of the selection about its centre, or a section's cut line (an end, or all of it). */
type Edit =
  | { kind: "end"; id: string; end: "a" | "b"; axis: { a: Pt; b: Pt; thickness: number }; from: Pt; now: Pt; moved: boolean }
  | { kind: "outline"; id: string; points: Pt[]; corner: number; edge: number; from: Pt; now: Pt; moved: boolean }
  | { kind: "ridge"; id: string; segment: [Pt, Pt]; across: Pt; offset: number; from: Pt; now: Pt; moved: boolean }
  | { kind: "ridge_projection"; id: string; transverseDot: number; offsetMm: number; from: Pt; now: Pt; moved: boolean }
  | { kind: "turn"; ids: string[]; centre: Pt; from: Pt; now: Pt; raw: Pt; fine: boolean; moved: boolean }
  | { kind: "mark"; id: string; part: "a" | "b" | "body"; from: Pt; now: Pt; moved: boolean };
/** A handle drawn on the selection: where it is, its look, and the edit that dragging it starts. */
interface Handle { at: Pt; shape: "square" | "turn"; edit: () => Edit }
const TURN_OFFSET_PX = 30;
const PIVOTS = ["top-left", "top", "top-right", "left", "center", "right", "bottom-left", "bottom", "bottom-right"] as const;
const PIVOT_NAMES: Record<(typeof PIVOTS)[number], string> = { "top-left": "Top left", top: "Top", "top-right": "Top right", left: "Left", center: "Centre", right: "Right", "bottom-left": "Bottom left", bottom: "Bottom", "bottom-right": "Bottom right" };

interface Colors { paper: string; grid: string; gridMajor: string; ink: string; muted: string; select: string; accent: string; hover: string }

export class DrawingView {
  readonly el: HTMLElement;
  private readonly canvas: HTMLCanvasElement;
  private readonly ctx: CanvasRenderingContext2D;
  private readonly hint: HTMLElement;
  private data: DrawingData | null = null;
  private viewId = "";
  private scale = 0.05;                     // pixels per drawing unit
  private centre: Pt = [0, 0];
  private cursor: Pt | null = null;         // world position of the pointer, after snapping
  private snap: Snap | null = null;
  private pts: Pt[] = [];                   // points placed so far by the active tool
  private hover: string | null = null;
  private pickCycle: { at: Pt; ids: string[]; index: number } | null = null;
  private box: { from: Pt; to: Pt } | null = null;
  private drag: { start: Pt; now: Pt; moved: boolean; ids: string[] } | null = null;
  private pan: { x: number; y: number; centre: Pt } | null = null;
  private spaceDown = false;
  private frame = 0;
  private width = 1;
  private height = 1;
  private colors: Colors | null = null;
  private pendingFit = false;
  private target: { id: string; pick: Pt; distance?: number } | null = null;     // the first thing picked by offset or fillet
  private grip: { id: string; list: Grip[]; index: number; now: Pt; moved: boolean } | null = null;
  private lastOffset: number | null = null;
  private lastRadius = 0;
  private lastHatch: HatchChoice | null = null;
  private hoverMark: string | null = null;
  private tagOf: string | null = null;      // the element the Tag tool has picked; the next click says where its tag goes
  private projectionSnap: { of: Projection | null; polygons: Pt[][] } = { of: null, polygons: [] };
  private edit: Edit | null = null;
  private anchor: Pt | null = null;         // while an end is dragged: the end that stays, so Shift can keep the wall straight from it
  private markSel: string | null = null;    // the section whose cut line is selected on this plan
  private lengthBox: { x: number; y: number; w: number; h: number; id: string; a: Pt; b: Pt } | null = null;      // the length of the selected wall, where it can be clicked and typed over
  private readonly wallHud: HTMLElement;
  private readonly layerControls = h("div", { class: "layer-controls", attrs: { "aria-label": "Add a layer to the selected element" } });
  private readonly junctionControls = h("div", { class: "junction-controls", attrs: { "aria-label": "Wall corner connection controls" } });
  private openedJoin: { wall: string; other: string; endpoint: "start" | "end" } | null = null;
  private readonly contextAnchor = h("span", { class: "model-context-anchor", attrs: { "aria-hidden": "true" } });
  private readonly wallFields: { height: HTMLInputElement; thickness: HTMLInputElement };
  private readonly formHuds = new Map<Tool, { el: HTMLElement; sync: () => void }>();      // the option strips of the Extrude, Void and Sweep tools

  constructor(private readonly store: Store) {
    this.canvas = h("canvas", { class: "view-canvas", attrs: { tabindex: "0", "aria-label": "Drawing canvas" } });
    const ctx = this.canvas.getContext("2d");
    if (!ctx) throw new Error("This browser cannot draw on a canvas.");
    this.ctx = ctx;
    (this.canvas as HTMLCanvasElement & { spadraView?: DrawingView }).spadraView = this;      // read by the browser tests (see screenOf)
    this.hint = h("div", { class: "canvas-hint", attrs: { "aria-live": "polite" } });
    const option = (label: string, value: number, min: string, step: string, set: (n: number) => void): [HTMLLabelElement, HTMLInputElement] => {
      const input = h("input", { type: "number", value: String(value), min, step, attrs: { "aria-label": label } });
      input.addEventListener("input", () => { const n = Number(input.value); if (n > 0) set(n); });
      return [h("label", {}, label, input), input];
    };
    const [heightLabel, height] = option("Height (m)", this.store.wallOptions.height, "0.1", "0.1", (n) => { this.store.wallOptions.height = n; });
    const [thicknessLabel, thickness] = option("Thickness (mm)", this.store.wallOptions.thickness, "10", "10", (n) => { this.store.wallOptions.thickness = n; });
    this.wallFields = { height, thickness };
    this.wallHud = h("div", { class: "wall-hud", hidden: true }, heightLabel, thicknessLabel, h("span", { class: "muted", text: "Click for each corner. Shift keeps it straight. Double-click or Esc ends." }));
    this.buildFormHuds();
    this.el = h("div", { class: "canvas-wrap" }, this.canvas, this.layerControls, this.junctionControls, this.contextAnchor, this.wallHud, ...[...this.formHuds.values()].map((x) => x.el), this.hint);
    new ResizeObserver(() => this.resize()).observe(this.el);
    this.bind();
  }

  /** Reusable tool-option strips kept in the store and shared by 2D and 3D roof drawing. */
  private buildFormHuds(): void {
    const o = this.store.formOptions;
    const num = (label: string, get: () => number, set: (n: number) => void, step: string, positive: boolean): [HTMLLabelElement, () => void] => {
      const input = h("input", { type: "number", value: String(get()), step, attrs: { "aria-label": label } });
      input.addEventListener("input", () => { const n = Number(input.value); if (input.value !== "" && Number.isFinite(n) && (!positive || n > 0)) set(n); });
      return [h("label", {}, label, input), () => { input.value = String(get()); }];
    };
    const hud = (tool: Tool, note: string, fields: Array<[HTMLElement, () => void]>, extra: HTMLElement[] = []): void => {
      this.formHuds.set(tool, { el: h("div", { class: "wall-hud", hidden: true }, ...fields.map((f) => f[0]), ...extra, h("span", { class: "muted", text: note })), sync: () => fields.forEach((f) => f[1]()) });
    };
    const roof = this.store.roofOptions;
    const roofType = h("select", { attrs: { "aria-label": "Roof type" } },
      ...(["flat", "shed", "gable", "hip"] as const).map((value) => h("option", { value, text: value.charAt(0).toUpperCase() + value.slice(1) })));
    roofType.addEventListener("change", () => { roof.kind = roofType.value as typeof roof.kind; });
    hud("roof", "Draw the roof outline on this plan. Enter or double-click finishes; Esc cancels. Pitched roofs require four perpendicular corners.",
      [[h("label", {}, "Roof", roofType), () => { roofType.value = roof.kind; }],
        num("Roof pitch (°)", () => roof.pitch, (n) => { roof.pitch = n; }, "1", true),
        num("Roof thickness (mm)", () => roof.thickness, (n) => { roof.thickness = n; }, "10", true)]);
    const polygon = this.pickedPolygon.bind(this);
    const extrudeBtn = h("button", { type: "button", class: "tool-inline", text: "Use selected polygon", attrs: { title: "Stand the closed polygon selected in this drawing up to the depth" }, on: { click: () => { void this.extrudeSelected(); } } });
    hud("extrude", "Click each corner of the outline, or select a closed polygon and use it. Enter or double-click finishes, Esc cancels.",
      [num("Depth (m)", () => o.depth, (n) => { o.depth = n; }, "0.1", true), num("Base (m)", () => o.base, (n) => { o.base = n; }, "0.1", false)], [extrudeBtn]);
    hud("void", "Draw the outline of the cut. It cuts floors and extrusions it lies wholly inside, and walls it passes through square.",
      [num("Depth (m)", () => o.voidDepth, (n) => { o.voidDepth = n; }, "0.1", true), num("Base (m)", () => o.voidBase, (n) => { o.voidBase = n; }, "0.1", false)]);
    const shape = h("select", { attrs: { "aria-label": "Profile" } }, h("option", { value: "rectangle", text: "Rectangle" }), h("option", { value: "round", text: "Round" }), h("option", { value: "user", text: "User defined" }));
    const shapeLabel = h("label", {}, "Profile", shape);
    const [widthLabel, syncWidth] = num("Width (mm)", () => o.sweepWidth, (n) => { o.sweepWidth = n; }, "10", true);
    const [heightLabel, syncHeight] = num("Height (mm)", () => o.sweepHeight, (n) => { o.sweepHeight = n; }, "10", true);
    // User defined: the polygon picked in the drawing replaces width and height with where it sits on the path.
    const pick = h("button", { type: "button", class: "tool-inline", text: "Use selected polygon", attrs: { title: "Take the closed polygon selected in this drawing as the profile" }, on: { click: () => { const p = polygon(); if (p) { o.sweepPolygon = p; refresh(); } else toast("Select a closed polygon in this drawing first (a closed polyline, a circle or a hatch).", { kind: "info" }); } } });
    const picked = h("span", { class: "muted" });
    const grid = h("div", { class: "pivot-grid", attrs: { role: "radiogroup", "aria-label": "Pivot alignment" } });
    const cells = PIVOTS.map((k) => { const b = h("button", { type: "button", attrs: { role: "radio", title: PIVOT_NAMES[k], "aria-label": PIVOT_NAMES[k] }, on: { click: () => { o.sweepPivot = k; refresh(); } } }); grid.append(b); return [k, b] as const; });
    const pivotLabel = h("label", {}, "Pivot alignment", grid);
    const [angleLabel, syncAngle] = num("Angle (°)", () => o.sweepAngle, (n) => { o.sweepAngle = n; }, "1", false);
    const offs = (["X", "Y", "Z"] as const).map((a, i) => num(`Offset ${a} (mm)`, () => o.sweepOffset[i] as number, (n) => { o.sweepOffset[i] = n; }, "10", false));
    const mirror = h("input", { type: "checkbox", attrs: { "aria-label": "Mirror" } });
    mirror.addEventListener("change", () => { o.sweepMirror = mirror.checked; });
    const mirrorLabel = h("label", {}, "Mirror", mirror);
    const [radiusLabel, syncRadius] = num("Corner radius (mm)", () => o.sweepRadius, (n) => { o.sweepRadius = Math.max(0, n); }, "1", false);
    const userOnly = [pick, picked, pivotLabel, angleLabel, ...offs.map((x) => x[0]), mirrorLabel, radiusLabel];
    const refresh = (): void => {
      const user = o.sweepShape === "user";
      shape.value = o.sweepShape;
      widthLabel.hidden = heightLabel.hidden = user;
      for (const el of userOnly) el.hidden = !user;
      picked.textContent = o.sweepPolygon ? `${o.sweepPolygon.length} points` : "no polygon yet";
      for (const [k, b] of cells) b.setAttribute("aria-checked", String(o.sweepPivot === k));
      mirror.checked = o.sweepMirror;
      syncWidth(); syncHeight(); syncAngle(); syncRadius(); offs.forEach((x) => x[1]());
    };
    shape.addEventListener("change", () => { o.sweepShape = shape.value === "round" ? "round" : shape.value === "user" ? "user" : "rectangle"; refresh(); });
    hud("sweep", "Click along the path. Enter or double-click finishes, C closes it, Esc cancels. The path stays at one height.",
      [[shapeLabel, () => refresh()], [widthLabel, () => {}], [heightLabel, () => {}], [pivotLabel, () => {}], [angleLabel, () => {}], ...offs.map((x) => [x[0], () => {}] as [HTMLElement, () => void]),
        [mirrorLabel, () => {}], [radiusLabel, () => {}], num("Base (m)", () => o.sweepBase, (n) => { o.sweepBase = n; }, "0.1", false)], [pick, picked]);
    refresh();
  }

  /** The closed polygon of the one drawing entity selected on this plan, in the drawing's unit, or null. */
  private pickedPolygon(): Array<[number, number]> | null {
    const ids = this.selectedIds();
    const e = ids.length === 1 ? this.entities().find((en) => en.id === ids[0]) : undefined;
    const loop = e ? closedLoop(e) : null;
    return loop ? loop.map((q) => [q[0], q[1]] as [number, number]) : null;
  }

  /** Extrude: stand the selected closed polygon up to the depth. */
  private async extrudeSelected(): Promise<void> {
    const loop = this.pickedPolygon(), level = this.data?.view.level_id, o = this.store.formOptions;
    if (!loop) { toast("Select a closed polygon in this drawing first (a closed polyline, a circle or a hatch).", { kind: "info" }); return; }
    if (!level) { toast("Extrusions are made on a floor plan.", { kind: "info" }); return; }
    const out = await this.store.run("model.add_extrusion", { points: loop, level, void: false, depth: o.depth, base: o.base });
    const id = (out?.result as { id?: string } | undefined)?.id;
    if (id) await this.store.select({ kind: "model", ids: [id] });
  }

  // ------------------------------------------------------------------------------------------------------------- public
  setData(data: DrawingData | null): void {
    const first = !!data && data.view.id !== this.viewId;
    this.data = data;
    if (data && first) { this.viewId = data.view.id; this.pts = []; this.fit(); }
    else if (data && !this.inView(data.bounds)) this.fit();       // the content (say, walls just placed in 3D) is off screen: bring it back
    this.updateHint();
    this.draw();
  }

  /** Reset the pending tool state (the tool changed or the tab did). */
  reset(): void { this.pts = []; this.box = null; this.drag = null; this.grip = null; this.edit = null; this.anchor = null; this.target = null; this.tagOf = null; this.updateHint(); this.draw(); }

  fit(): void {
    if (!this.measure()) { this.pendingFit = true; return; }       // hidden right now: fit as soon as it has a size
    const b = this.data?.bounds;
    if (b) {
      const f = fitBox(b[0], b[1], this.width, this.height, 64);
      this.scale = clamp(f.scale, 1e-6, 1e6); this.centre = f.centre;
    } else {
      const unit = MM_PER[this.store.unit];
      this.scale = Math.max(1e-9, (Math.min(this.width, this.height) - 96) / (20_000 / unit));   // a 20 m square, in the project's unit
      this.centre = [10_000 / unit, 7_500 / unit];
    }
    this.draw();
  }

  /** True when nothing is known to be drawn, or at least part of it is inside the viewport. */
  private inView(bounds: [Pt, Pt] | null | undefined): boolean {
    if (!bounds || !this.measure()) return true;
    const lo = this.toWorld(0, this.height), hi = this.toWorld(this.width, 0);
    return bounds[1][0] >= lo[0] && bounds[0][0] <= hi[0] && bounds[1][1] >= lo[1] && bounds[0][1] <= hi[1];
  }

  zoom(factor: number): void { this.scale = clamp(this.scale * factor, 1e-6, 1e6); this.draw(); }

  /** Move the view by a number of screen pixels (right and down are positive), as the arrow keys do. */
  panBy(dx: number, dy: number): void { this.centre = [this.centre[0] + dx / this.scale, this.centre[1] - dy / this.scale]; this.draw(); }

  // -------------------------------------------------------------------------------------------------------------- space
  private toWorld(px: number, py: number): Pt {
    return [this.centre[0] + (px - this.width / 2) / this.scale, this.centre[1] - (py - this.height / 2) / this.scale];
  }
  private toScreen(p: Pt): Pt { return [this.width / 2 + (p[0] - this.centre[0]) * this.scale, this.height / 2 - (p[1] - this.centre[1]) * this.scale]; }
  private local(e: PointerEvent | MouseEvent | WheelEvent): Pt {
    const r = this.canvas.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  }
  private gridStep(): number { return niceStep(24 / this.scale); }
  private entities(): Entity[] { return this.data?.entities ?? []; }
  /** What can be snapped to in a section or elevation: the corners of the cut and the ends of every drawn line. */
  private projectionPolygons(): Pt[][] {
    const pr = this.data?.projection ?? null;
    if (this.projectionSnap.of !== pr) this.projectionSnap = { of: pr, polygons: pr ? snapPolygons(pr) : [] };
    return this.projectionSnap.polygons;
  }
  /** The centre line of every wall as a two-point "polygon": snapping then finds the true ends and middles of walls, not just their outline corners. */
  private axisPolygons(): Pt[][] {
    const seen = new Set<string>(), out: Pt[][] = [];
    const own = this.edit?.kind === "end" ? this.edit.id : null;
    for (const w of this.data?.model ?? []) if (w.axis && !seen.has(w.id) && w.id !== own) { seen.add(w.id); out.push([w.axis.a, w.axis.b]); }
    return out;
  }
  private visible(e: Entity): boolean { return this.data?.layers.find((l) => l.name === e.layer)?.visible !== false; }

  private pointFor(e: PointerEvent | MouseEvent): { world: Pt; snap: Snap } {
    const [x, y] = this.local(e);
    const raw = this.toWorld(x, y);
    const snap = snapPoint(raw, this.entities().filter((en) => this.visible(en)), SNAP_PX / this.scale, this.store.snapGrid ? this.gridStep() : null,
      [...(this.data?.model ?? []).map((w) => w.polygon), ...this.axisPolygons(), ...this.projectionPolygons()]);
    let point = snap.point;
    const last = this.pts[this.pts.length - 1] ?? this.anchor;
    if (e.shiftKey && last && (this.store.tool !== "select" || this.anchor) && this.store.tool !== "circle") { point = orthoConstrain(last, raw); return { world: point, snap: { point, kind: "none" } }; }
    return { world: point, snap };
  }

  // ---------------------------------------------------------------------------------------------------------- interaction
  private bind(): void {
    const c = this.canvas;
    c.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      if (this.pts.length) { this.finish(false); return; }
      if (this.store.tool === "select") this.openModelContextMenu(e);
    });
    c.addEventListener("wheel", (e) => {
      e.preventDefault();
      const [x, y] = this.local(e);
      const before = this.toWorld(x, y);
      this.scale = clamp(this.scale * Math.exp(-e.deltaY * 0.0015), 1e-6, 1e6);
      const after = this.toWorld(x, y);
      this.centre = [this.centre[0] + before[0] - after[0], this.centre[1] + before[1] - after[1]];
      this.draw();
    }, { passive: false });
    c.addEventListener("pointerdown", (e) => this.down(e));
    c.addEventListener("pointermove", (e) => this.move(e));
    c.addEventListener("pointerup", (e) => { void this.up(e); });
    c.addEventListener("pointerleave", (e) => {
      // The corner button is a sibling overlay. Preserve the hover join
      // while the pointer crosses from the canvas onto its actionable icon.
      if (e.relatedTarget instanceof Node && this.junctionControls.contains(e.relatedTarget)) return;
      this.cursor = null; this.snap = null; this.hover = null; this.draw();
    });
    this.el.addEventListener("pointerleave", () => { this.cursor = null; this.snap = null; this.hover = null; this.draw(); });
    c.addEventListener("dblclick", (e) => {
      if (this.store.tool === "polyline" || this.store.tool === "floor" || this.store.tool === "roof" || this.store.tool === "room" || this.store.tool === "extrude" || this.store.tool === "void" || this.store.tool === "sweep") { void this.finish(true); return; }
      if (this.store.tool === "wall") { void this.finish(false); return; }
      if (this.store.tool === "select" && e.shiftKey && !this.data?.projection) {
        const wall = this.wallAt(this.toWorld(...this.local(e)));
        if (wall) { e.preventDefault(); void this.store.select({ kind: "model", ids: this.connectedWalls(wall) }); return; }
      }
      const mark = this.store.tool === "select" ? this.markAt(this.toWorld(...this.local(e))) : null;       // double-click a section line to open that section
      if (mark) void this.store.openTab({ kind: "drawing", id: mark.id }, mark.name);
    });
    c.addEventListener("keydown", (e) => { void this.key(e); });
    c.addEventListener("keyup", (e) => { if (e.key === " ") this.spaceDown = false; });
  }

  private down(e: PointerEvent): void {
    this.canvas.focus();
    this.canvas.setPointerCapture(e.pointerId);
    const [x, y] = this.local(e);
    if (e.button === 1 || e.button === 2 || (e.button === 0 && this.spaceDown)) { this.pan = { x, y, centre: [...this.centre] as Pt }; return; }
    if (e.button !== 0 || !this.data) return;
    const { world } = this.pointFor(e);
    if (this.store.tool !== "select") return;       // drawing tools act on pointer-up, so a drag never leaves a stray point
    const raw = this.toWorld(x, y);
    const handle = this.handleAt(raw);
    if (handle) { this.edit = handle.edit(); this.anchor = this.edit.kind === "end" ? (this.edit.end === "a" ? this.edit.axis.b : this.edit.axis.a) : null; return; }
    if (this.lengthBox && x >= this.lengthBox.x && x <= this.lengthBox.x + this.lengthBox.w && y >= this.lengthBox.y && y <= this.lengthBox.y + this.lengthBox.h) { void this.typeLength(); return; }
    const handles = this.currentGrips();
    if (handles) {
      const index = gripAt(handles.list, raw, (PICK_PX + 3) / this.scale);
      if (index >= 0) { this.grip = { id: handles.id, list: handles.list, index, now: (handles.list[index] as Grip).at, moved: false }; return; }
    }
    const mark = this.data.projection ? null : this.markAt(raw);              // a section's cut line is picked before what lies under it
    if (mark && !e.shiftKey) {
      this.markSel = mark.id;
      if (this.store.selection) void this.store.select(null);
      this.edit = { kind: "mark", id: mark.id, part: "body", from: world, now: world, moved: false };
      this.draw();
      return;
    }
    const selected = this.allSelectedIds();
    // Once an obscured wall/foundation has been selected through cycling,
    // dragging that same shared footprint must move the selected element,
    // not revert to whichever surface is first in the hit stack.
    const picked = this.pick(raw);
    const hit = this.store.selection?.kind === "model"
      ? this.modelCandidates(raw).find((candidate) => selected.includes(candidate.id))?.id ?? picked
      : picked;
    // Dragging a selected element moves the whole selection; dragging one that is not selected moves just that one, as in Revit.
    if (hit && !e.shiftKey && !this.data.projection) { this.drag = { start: world, now: world, moved: false, ids: selected.includes(hit) ? selected : [hit] }; return; }      // a section is a picture of the model, not a place to move it
    if (hit) { this.drag = null; return; }
    this.box = { from: raw, to: raw };
  }

  private move(e: PointerEvent): void {
    const [x, y] = this.local(e);
    if (this.pan) {
      this.centre = [this.pan.centre[0] - (x - this.pan.x) / this.scale, this.pan.centre[1] + (y - this.pan.y) / this.scale];
      this.draw(); return;
    }
    const { world, snap } = this.pointFor(e);
    this.cursor = world; this.snap = snap;
    if (this.store.tool !== "select") this.canvas.style.setProperty("cursor", "crosshair");        // a drawing tool shows where it will act
    if (this.edit) {
      const ed = this.edit;
      const raw = this.toWorld(x, y);
      const now = ed.kind === "turn" ? raw : world;
      this.edit = { ...ed, now, ...(ed.kind === "turn" ? { raw, fine: !e.shiftKey } : {}), moved: ed.moved || dist(this.toScreen(ed.from), this.toScreen(now)) > DRAG_PX } as Edit;
    } else if (this.grip) {
      const from = (this.grip.list[this.grip.index] as Grip).at;
      this.grip = { ...this.grip, now: world, moved: this.grip.moved || dist(this.toScreen(from), this.toScreen(world)) > DRAG_PX };
    } else if (this.drag) {
      const moved = dist(this.toScreen(this.drag.start), this.toScreen(world)) > DRAG_PX;
      this.drag = { ...this.drag, now: world, moved: this.drag.moved || moved };
    } else if (this.box) {
      this.box = { from: this.box.from, to: this.toWorld(x, y) };
    } else if (this.store.tool === "select") {
      const raw = this.toWorld(x, y);
      const mark = this.data?.projection ? null : this.markAt(raw);
      this.hoverMark = mark?.id ?? null;
      this.hover = mark ? null : this.pick(raw);
      const onHandle = this.handleAt(raw);
      this.canvas.style.setProperty("cursor", onHandle ? (onHandle.shape === "turn" ? "alias" : "move") : mark ? "pointer" : "");
    } else if (this.store.tool === "tag" && !this.tagOf) {
      const hit = this.pick(this.toWorld(x, y));
      this.hover = hit && this.isModel(hit) ? hit : null;
    } else if (CURVE_TOOLS.has(this.store.tool)) {
      const raw = this.toWorld(x, y);
      this.hover = this.store.tool === "hatch" ? this.shapeAt(raw) : this.pickEntity(raw, TOOL_KINDS[this.store.tool]);
    }
    this.draw();
  }

  private async up(e: PointerEvent): Promise<void> {
    const [x, y] = this.local(e);
    if (this.pan) { this.pan = null; return; }
    if (e.button !== 0 || !this.data) return;
    const view = this.data.view.id;
    const { world } = this.pointFor(e);
    const tool = this.store.tool;
    if (this.edit) {
      const ed = this.edit;
      this.edit = null; this.anchor = null;
      if (ed.moved) await this.commitEdit(ed);
      this.draw();
      return;
    }
    if (this.grip) {
      const { id, list, index, now, moved } = this.grip;
      this.grip = null;
      if (moved) await this.store.run("draw.set_geometry", { drawing: view, id, geometry: (list[index] as Grip).geometry(now) });
      else this.draw();
      return;
    }
    if (tool === "select") {
      if (this.drag?.moved) {
        const { start, now, ids } = this.drag;
        this.drag = null;
        const dx = now[0] - start[0], dy = now[1] - start[1];
        const before = this.allSelectedIds();
        if (this.isModel(ids[0])) {                      // a wall moves in the model, in metres; the plan follows
          const f = MM_PER[this.store.unit] / 1000;
          const hangs = (id: string): boolean => this.data?.model.find((w) => w.id === id)?.category === "gutter";      // a gutter hangs on its roof: it moves with the roof
          for (const id of ids) if (!hangs(id) && !(await this.store.run("model.move", { id, dx: dx * f, dy: dy * f }))) break;
        } else await this.store.run("draw.move", { drawing: view, ids, dx, dy });
        if (ids.length !== before.length || ids.some((i) => !before.includes(i))) await this.store.select(this.isModel(ids[0]) ? { kind: "model", ids } : { kind: "drawing", view, ids });      // what was dragged is what is selected
        return;
      }
      this.drag = null;
      if (this.box && dist(this.toScreen(this.box.from), [x, y]) > DRAG_PX) {
        const lo: Pt = [Math.min(this.box.from[0], this.box.to[0]), Math.min(this.box.from[1], this.box.to[1])];
        const hi: Pt = [Math.max(this.box.from[0], this.box.to[0]), Math.max(this.box.from[1], this.box.to[1])];
        const ids = this.entities().filter((en) => this.visible(en) && insideBox(en, lo, hi)).map((en) => en.id);
        this.box = null; this.markSel = null;
        await this.store.select(ids.length ? { kind: "drawing", view, ids } : null);
        return;
      }
      this.box = null;
      this.markSel = null;
      const hit = e.shiftKey ? this.pick(this.toWorld(x, y)) : this.pickForSelection(this.toWorld(x, y));
      const current = this.allSelectedIds();
      let ids: string[] = hit ? [hit] : [];
      // Shift adds to the selection, but only things of the same kind: drawing entities and model walls are edited by different commands.
      if (hit && e.shiftKey && current.length && this.isModel(current[0]) === this.isModel(hit)) ids = current.includes(hit) ? current.filter((i) => i !== hit) : [...current, hit];
      await this.store.select(!ids.length ? null : this.isModel(ids[0]) ? { kind: "model", ids } : { kind: "drawing", view, ids });
      return;
    }
    await this.place(tool, world, view, this.toWorld(x, y));
  }

  private async place(tool: Tool, p: Pt, view: string, raw: Pt): Promise<void> {
    const last = this.pts[this.pts.length - 1];
    if (last && dist(last, p) < 1e-9 && tool !== "text") return;      // a double click never adds a zero-length piece
    switch (tool) {
      case "line":
        if (!last) { this.pts = [p]; break; }
        if (await this.store.run("draw.line", { drawing: view, start: last, end: p })) this.pts = [p];
        break;
      case "polyline": this.pts = [...this.pts, p]; break;
      case "rectangle": {
        if (!last) { this.pts = [p]; break; }
        const r = normaliseRect(last, p);
        if (r.width === 0 || r.height === 0) { toast("A rectangle needs two corners that differ in both directions.", { kind: "info" }); return; }
        if (await this.store.run("draw.rectangle", { drawing: view, corner: r.corner, width: r.width, height: r.height })) this.pts = [];
        break;
      }
      case "circle":
        if (!last) { this.pts = [p]; break; }
        if (await this.store.run("draw.circle", { drawing: view, center: last, radius: dist(last, p) })) this.pts = [];
        break;
      case "dimension":
        // Only Enter commits: another pointer-up must never finalize a dimension.
        if (this.pts.length < 2) this.pts = [...this.pts, p];
        break;
      case "text": {
        const value = await promptDialog({ title: "Add text", label: "Text", confirmLabel: "Place", maxLength: 500 });
        if (value) await this.store.run("draw.text", { drawing: view, position: p, text: value, height: 250 / MM_PER[this.store.unit] });
        break;
      }
      case "offset": await this.offsetClick(view, raw); break;
      case "trim": case "extend": {
        const id = this.pickEntity(raw, TOOL_KINDS[tool]);
        if (!id) { toast(tool === "trim" ? "Click the part of a line or polyline to remove." : "Click near the end of a line or open polyline to extend.", { kind: "info" }); break; }
        await this.store.run(tool === "trim" ? "draw.trim" : "draw.extend", { drawing: view, id, pick: raw });
        break;
      }
      case "fillet": await this.filletClick(view, raw); break;
      case "mirror": await this.mirrorClick(view, p); break;
      case "door": case "window": {
        const wall = this.wallAt(raw);
        if (!wall) { toast(`Click on a wall to place the ${tool}.`, { kind: "info" }); break; }
        const out = await this.store.run(`model.add_${tool}`, { wall, point: raw });
        const id = (out?.result as { id?: string } | undefined)?.id;
        if (id) await this.store.select({ kind: "model", ids: [id] });
        break;
      }
      case "floor": case "roof": case "room": this.pts = [...this.pts, p]; break;
      case "extrude": case "void": case "sweep":
        if (!this.data?.view.level_id) { toast("Extrusions, voids and sweeps are drawn on a floor plan.", { kind: "info" }); break; }
        this.pts = [...this.pts, p]; break;
      case "wall": {
        const level = this.data?.view.level_id;
        if (!level) { toast("Walls are drawn on a floor plan. Open a plan, or draw them in the 3D view.", { kind: "info" }); break; }
        if (!last) { this.pts = [p]; break; }
        const { height, thickness } = this.store.wallOptions;
        if (!(height > 0) || !(thickness > 0)) { toast("Enter a positive wall height and thickness.", { kind: "error" }); break; }
        if (await this.store.run("model.add_wall", { start: last, end: p, level, height, thickness })) this.pts = [p];      // the chain goes on from the new end
        break;
      }
      case "rotate": {
        const ids = this.turnable();
        if (!ids.length) { toast("Select what to rotate first (walls, columns, floors, rooms, stairs …), then choose Rotate. Doors and windows turn with their wall.", { kind: "info" }); break; }
        if (this.pts.length < 2) { this.pts = [...this.pts, p]; break; }
        const [centre, reference] = this.pts as [Pt, Pt];
        this.pts = [];
        const degrees = (angleBetween(centre, reference, p) * 180) / Math.PI;
        if (Math.abs(degrees) > 1e-9) await this.store.run("model.rotate", { ids, centre, angle: degrees });
        break;
      }
      case "column": {
        const level = this.data?.view.level_id;
        if (!level) { toast("Columns are placed on a floor plan.", { kind: "info" }); break; }
        const out = await this.store.run("model.add_column", { point: p, level });
        const id = (out?.result as { id?: string } | undefined)?.id;
        if (id) await this.store.select({ kind: "model", ids: [id] });
        break;
      }
      case "section": {
        if (!last) { this.pts = [p]; break; }
        const out = await this.store.run("view.add_section", { a: last, b: p });
        const made = out?.result as { id?: string; name?: string } | undefined;
        this.pts = [];
        if (made?.id) await this.store.openTab({ kind: "drawing", id: made.id }, made.name ?? "Section");
        break;
      }
      case "hatch": await this.hatchClick(view, raw); break;
      case "grid": {
        if (!last) { this.pts = [p]; break; }
        const out = await this.store.run("model.add_grid", { start: last, end: p });
        const id = (out?.result as { id?: string } | undefined)?.id;
        this.pts = [];
        if (id) await this.store.select({ kind: "model", ids: [id] });
        break;
      }
      case "stair": {
        const level = this.data?.view.level_id;
        if (!level) { toast("Stairs are placed on a floor plan.", { kind: "info" }); break; }
        if (!last) { this.pts = [p]; break; }
        const out = await this.store.run("model.add_stair", { start: last, end: p, level });
        const id = (out?.result as { id?: string } | undefined)?.id;
        this.pts = [];
        if (id) await this.store.select({ kind: "model", ids: [id] });
        break;
      }
      case "family": {
        const level = this.data?.view.level_id, family = this.store.family;
        if (!level) { toast("Family elements are placed on a floor plan.", { kind: "info" }); break; }
        if (!family) { toast("Choose a family first (Insert, then Family).", { kind: "info" }); break; }
        const out = await this.store.run("family.place", { family: family.id, level, point: p });
        const id = (out?.result as { id?: string } | undefined)?.id;
        if (id) await this.store.select({ kind: "model", ids: [id] });
        break;
      }
      case "tag": {
        if (!this.tagOf) {
          const hit = this.pick(raw);
          if (!hit || !this.isModel(hit)) { toast("Click the element to tag.", { kind: "info" }); break; }
          this.tagOf = hit; this.hover = hit;
          break;
        }
        const out = await this.store.run("draw.tag", { drawing: view, element: this.tagOf, point: p });
        const id = (out?.result as { id?: string } | undefined)?.id;
        this.tagOf = null; this.hover = null;
        if (id) await this.store.select({ kind: "drawing", view, ids: [id] });
        break;
      }
      default: break;
    }
    this.updateHint();
    this.draw();
  }

  /** Offset: pick the shape, say how far, then click the side to copy it to. */
  private async offsetClick(view: string, raw: Pt): Promise<void> {
    if (!this.target) {
      const id = this.pickEntity(raw, TOOL_KINDS["offset"]);
      if (!id) { toast("Click a line, polyline, rectangle or circle to offset.", { kind: "info" }); return; }
      this.target = { id, pick: raw };
      this.draw();
      const unit = this.store.unit;
      const distance = await numberPrompt({ title: "Offset", label: "Distance", unit, value: this.lastOffset ?? 200 / MM_PER[unit], confirmLabel: "Set distance" });
      if (distance === null) { this.target = null; return; }
      this.lastOffset = distance; this.target = { id, pick: raw, distance };
      return;
    }
    const distance = this.target.distance;
    if (distance === undefined) return;                 // the distance dialog is still open
    const id = this.target.id;
    this.target = null;
    await this.store.run("draw.offset", { drawing: view, id, distance, side: raw });
  }

  /** Fillet: click one line near the end to keep, then the other; the radius is asked for once both are known. */
  private async filletClick(view: string, raw: Pt): Promise<void> {
    const id = this.pickEntity(raw, TOOL_KINDS["fillet"]);
    if (!id) { toast("Click a line to fillet.", { kind: "info" }); return; }
    if (!this.target) { this.target = { id, pick: raw }; return; }
    if (this.target.id === id) { toast("Click a second, different line.", { kind: "info" }); return; }
    const first = this.target;
    const unit = this.store.unit;
    const radius = await numberPrompt({ title: "Fillet", label: "Radius", unit, value: this.lastRadius, allowZero: true, confirmLabel: "Fillet" });
    this.target = null;
    if (radius === null) return;
    this.lastRadius = radius;
    await this.store.run("draw.fillet", { drawing: view, ids: [first.id, id], picks: [first.pick, raw], radius });
  }

  /** Mirror the selected entities across a line given by two points. */
  private async mirrorClick(view: string, p: Pt): Promise<void> {
    const ids = this.selectedIds();
    if (!ids.length) { toast("Select what to mirror first, then choose Mirror.", { kind: "info" }); return; }
    if (this.pts.length === 0) { this.pts = [p]; return; }
    const a = this.pts[0] as Pt;
    this.pts = [];
    const keep = await mirrorKeepDialog();
    if (keep === null) return;
    await this.store.run("draw.mirror", { drawing: view, ids, p1: a, p2: p, copy: keep });
  }

  /** Hatch the closed shape under the click. */
  private async hatchClick(view: string, raw: Pt): Promise<void> {
    const id = this.shapeAt(raw);
    if (!id) { toast("Click inside a closed shape (a rectangle, closed polyline or circle) to hatch it.", { kind: "info" }); return; }
    const choice = await hatchDialog(this.store.unit, this.lastHatch);
    if (!choice) return;
    this.lastHatch = choice;
    await this.store.run("draw.hatch", { drawing: view, ids: [id], pattern: choice.pattern, angle: choice.angle, ...(choice.spacing !== null ? { spacing: choice.spacing } : {}) });
  }

  /** The selected model elements that can be turned: everything but doors and windows, which turn with their wall. */
  private turnable(): string[] {
    const sel = this.store.selection;
    if (sel?.kind !== "model") return [];
    const cats = new Map((this.data?.model ?? []).map((w) => [w.id, w.category] as const));
    return sel.ids.filter((id) => cats.has(id) && cats.get(id) !== "door" && cats.get(id) !== "window");
  }

  /** The handles of the selection: the ends of a selected wall, a turn handle above anything that can be turned, or the ends of a selected section line. */
  private handles(): Handle[] {
    const data = this.data;
    if (!data || this.store.tool !== "select") return [];
    if (data.projection) {
      const selected = this.store.selection;
      if (selected?.kind !== "model" || selected.ids.length !== 1) return [];
      const ridge = data.projection.items.find((item) => item.id === selected.ids[0] && item.category === "roof")?.ridge_handle;
      if (!ridge || Math.abs(ridge.transverse_dot) < 0.2) return [];
      return [{ at: ridge.point, shape: "square", edit: (): Edit => ({ kind: "ridge_projection", id: selected.ids[0] as string,
        transverseDot: ridge.transverse_dot, offsetMm: ridge.offset_mm, from: ridge.point, now: ridge.point, moved: false }) }];
    }
    if (this.markSel) {
      const m = data.section_marks.find((x) => x.id === this.markSel);
      return m ? (["a", "b"] as const).map((part) => ({ at: m[part], shape: "square" as const, edit: (): Edit => ({ kind: "mark", id: m.id, part, from: m[part], now: m[part], moved: false }) })) : [];
    }
    const ids = this.turnable();
    if (!ids.length) return [];
    const out: Handle[] = [];
    const wall = ids.length === 1 ? data.model.find((w) => w.id === ids[0] && w.axis && (w.category === "wall" || w.category === "grid")) : undefined;      // a strip foundation has an axis to snap to, but no ends to stretch
    if (wall?.axis) {
      const axis = wall.axis;
      for (const end of ["a", "b"] as const) out.push({ at: axis[end], shape: "square", edit: (): Edit => ({ kind: "end", id: wall.id, end, axis, from: axis[end], now: axis[end], moved: false }) });
    }
    const floor = ids.length === 1 ? data.model.find((w) => w.id === ids[0] && (w.category === "floor" || w.category === "roof") && w.outline && w.outline.length >= 3) : undefined;
    if (floor?.outline) {
      const pts = floor.outline;
      pts.forEach((at, i) => out.push({ at, shape: "square", edit: (): Edit => ({ kind: "outline", id: floor.id, points: pts, corner: i, edge: -1, from: at, now: at, moved: false }) }));       // a corner moves freely
      pts.forEach((a, i) => { const at = mid(a, pts[(i + 1) % pts.length] as Pt); out.push({ at, shape: "square", edit: (): Edit => ({ kind: "outline", id: floor.id, points: pts, corner: -1, edge: i, from: at, now: at, moved: false }) }); });       // an edge moves across itself
      if (floor.ridge && floor.ridge_across) {
        const at = mid(floor.ridge[0], floor.ridge[1]);
        out.push({ at, shape: "square", edit: (): Edit => ({ kind: "ridge", id: floor.id, segment: floor.ridge as [Pt, Pt], across: floor.ridge_across as Pt,
          offset: floor.ridge_offset ?? 0, from: at, now: at, moved: false }) });
      }
    }
    const box = polygonsBounds(data.model.filter((w) => ids.includes(w.id)).map((w) => w.polygon));
    if (box) {
      const centre: Pt = [(box[0][0] + box[1][0]) / 2, (box[0][1] + box[1][1]) / 2];
      const at: Pt = [centre[0], box[1][1] + TURN_OFFSET_PX / this.scale];
      out.push({ at, shape: "turn", edit: (): Edit => ({ kind: "turn", ids, centre, from: at, now: at, raw: at, fine: true, moved: false }) });
    }
    return out;
  }

  private handleAt(p: Pt): Handle | null {
    let best: Handle | null = null, bestD = (PICK_PX + 4) / this.scale;
    for (const hd of this.handles()) { const d = dist(hd.at, p); if (d <= bestD) { best = hd; bestD = d; } }
    return best;
  }

  /** The angle (radians) a turn edit currently stands for: whole degrees, or 15° steps while Shift is held. */
  private turnAngle(ed: Extract<Edit, { kind: "turn" }>): number {
    return roundAngle(angleBetween(ed.centre, ed.from, ed.raw), ed.fine ? 1 : 15);
  }

  /** Send a finished handle drag to the server. */
  private async commitEdit(ed: Edit): Promise<void> {
    if (ed.kind === "end") {
      const grid = this.data?.model.find((w) => w.id === ed.id)?.category === "grid";
      await this.store.run(grid ? "model.set_grid" : "model.set_wall_ends", { id: ed.id, [ed.end === "a" ? "start" : "end"]: ed.now });
    } else if (ed.kind === "outline") {
      const next = this.outlineAfter(ed);
      if (next) await this.store.run("model.set_boundary", { id: ed.id, points: next });
    } else if (ed.kind === "ridge") {
      const delta = (ed.now[0] - ed.from[0]) * ed.across[0] + (ed.now[1] - ed.from[1]) * ed.across[1];
      const mmPerUnit = this.store.unit === "m" ? 1000 : this.store.unit === "cm" ? 10 : 1;
      await this.store.run("model.set_property", { id: ed.id, key: "ridge_offset", value: (ed.offset + delta) * mmPerUnit });
    } else if (ed.kind === "ridge_projection") {
      // Screen-world X is along the section/elevation's horizontal axis.
      // Divide by the roof-transverse projection so the value is the same
      // actual horizontal ridge offset in any oblique valid view.
      const mmPerUnit = MM_PER[this.store.unit];
      const deltaMm = (ed.now[0] - ed.from[0]) * mmPerUnit / ed.transverseDot;
      if (Number.isFinite(deltaMm) && Math.abs(deltaMm) > 1e-7)
        await this.store.run("model.set_property", { id: ed.id, key: "ridge_offset", value: ed.offsetMm + deltaMm });
    } else if (ed.kind === "turn") {
      const degrees = (this.turnAngle(ed) * 180) / Math.PI;
      if (Math.abs(degrees) > 1e-9) await this.store.run("model.rotate", { ids: ed.ids, centre: ed.centre, angle: degrees });
    } else {
      const ends = this.markEnds(ed);
      if (ends) await this.store.run("view.set_section", { id: ed.id, a: ends[0], b: ends[1] });
    }
  }

  /** Where a section's cut line runs while (or after) it is dragged. */
  private markEnds(ed: Extract<Edit, { kind: "mark" }>): [Pt, Pt] | null {
    const m = this.data?.section_marks.find((x) => x.id === ed.id);
    if (!m) return null;
    if (ed.part === "a") return [ed.now, m.b];
    if (ed.part === "b") return [m.a, ed.now];
    const dx = ed.now[0] - ed.from[0], dy = ed.now[1] - ed.from[1];
    return [[m.a[0] + dx, m.a[1] + dy], [m.b[0] + dx, m.b[1] + dy]];
  }

  /** Click the length shown beside a selected wall and type the one it should have; the far end stays put. */
  private async typeLength(): Promise<void> {
    const box = this.lengthBox;
    if (!box) return;
    const unit = this.store.unit;
    const current = Math.round(dist(box.a, box.b) * 1000) / 1000;
    const grid = this.data?.model.find((w) => w.id === box.id)?.category === "grid";
    const length = await numberPrompt({ title: grid ? "Grid length" : "Wall length", label: "Length", unit, value: current, confirmLabel: "Set length" });
    if (length === null || Math.abs(length - current) < 1e-9) return;
    if (grid) await this.store.run("model.set_grid", { id: box.id, length });                            // from its start; the angle and the label are in Properties
    else await this.store.run("model.set_wall_ends", { id: box.id, end: alongFrom(box.a, box.b, length) });
  }

  /** The wall under a plan point, ignoring doors, windows, floors and beams. */
  private wallAt(p: Pt): string | null {
    let best: string | null = null, bestD = PICK_PX / this.scale;
    for (const w of this.data?.model ?? []) {
      if (w.category !== "wall") continue;
      const d = polygonDistance(p, w.polygon);
      if (d <= bestD) { best = w.id; bestD = d; }
    }
    return best;
  }

  /** Walk the server's real wall-join graph, not coincident screen pixels.
   *  The plan overlay contains only walls relevant to this view, so unrelated
   *  levels and hidden geometry never enter the resulting selection. */
  private connectedWalls(seed: string): string[] {
    const graph = new Map<string, Set<string>>();
    for (const wall of this.data?.model ?? []) if (wall.category === "wall") {
      if (!graph.has(wall.id)) graph.set(wall.id, new Set());
      for (const other of wall.joins ?? []) graph.get(wall.id)?.add(other);
    }
    for (const [id, neighbours] of graph) for (const other of neighbours) {
      if (graph.has(other)) graph.get(other)?.add(id);
    }
    const seen = new Set<string>(), pending = [seed];
    while (pending.length) {
      const id = pending.pop() as string;
      if (seen.has(id) || !graph.has(id)) continue;
      seen.add(id);
      for (const other of graph.get(id) ?? []) if (!seen.has(other)) pending.push(other);
    }
    return [...seen];
  }

  /** The smallest closed shape (closed polyline or circle) that the point is on or inside. */
  private shapeAt(p: Pt): string | null {
    let best: string | null = null, bestArea = Infinity;
    for (const e of this.entities()) {
      if (!this.visible(e)) continue;
      const g = e.geometry;
      let inside = false;
      if (e.kind === "polyline" && g["closed"] === true && Array.isArray(g["points"])) inside = pointInPolygon(p, g["points"] as Pt[]);
      else if (e.kind === "circle" && Array.isArray(g["center"]) && typeof g["radius"] === "number") inside = dist(p, g["center"] as Pt) <= g["radius"];
      if (!inside) continue;
      const b = entityBounds(e);
      const area = b ? (b[1][0] - b[0][0]) * (b[1][1] - b[0][1]) : Infinity;
      if (area < bestArea) { best = e.id; bestArea = area; }
    }
    return best;
  }

  /** The handles of the one selected drawing entity, when there is exactly one. */
  private currentGrips(): { id: string; list: Grip[] } | null {
    const ids = this.selectedIds();
    const e = ids.length === 1 ? this.entities().find((en) => en.id === ids[0]) : undefined;
    if (!e) return null;
    const list = grips(e);
    return list.length ? { id: e.id, list } : null;
  }

  /** Finish the active tool: a polyline is committed here (Enter, double click, or C to close it); other tools just stop. */
  private async finish(commit: boolean, closed = false): Promise<void> {
    // An in-place SDRW opening-profile sketch is explicitly cancelled when
    // Escape or right-click abandons its polyline. The shared Store owns the
    // pending target; never allow a later unrelated polyline to attach there.
    if (!commit && this.store.tool === "polyline") this.store.cancelPendingOpeningSketch();
    const view = this.data?.view.id;
    if (commit && this.store.tool === "polyline" && view) {
      const pts = this.pts.filter((p, i) => i === 0 || dist(p, this.pts[i - 1] ?? p) > 1e-9);
      if (pts.length >= 2) await this.store.run("draw.polyline", { drawing: view, points: pts, closed });
      else toast("A polyline needs at least two points.", { kind: "info" });
    }
    if (commit && (this.store.tool === "floor" || this.store.tool === "roof") && view) {
      const level = this.data?.view.level_id;
      const pts = this.pts.filter((p, i) => i === 0 || dist(p, this.pts[i - 1] ?? p) > 1e-9);
      if (pts.length >= 3 && level) {
        const roof = this.store.tool === "roof";
        const out = roof
          ? await this.store.run("model.add_roof", { points: pts, level, roof: this.store.roofOptions.kind, pitch: this.store.roofOptions.pitch, thickness: this.store.roofOptions.thickness })
          : await this.store.run("model.add_floor", { points: pts, level });
        const id = (out?.result as { id?: string } | undefined)?.id;
        if (id) await this.store.select({ kind: "model", ids: [id] });
      } else toast(`${this.store.tool === "roof" ? "A roof" : "A floor"} needs at least three corners on a floor plan.`, { kind: "info" });
    }
    if (commit && (this.store.tool === "extrude" || this.store.tool === "void") && view) {
      const level = this.data?.view.level_id;
      const isVoid = this.store.tool === "void";
      const o = this.store.formOptions;
      const pts = this.pts.filter((p, i) => i === 0 || dist(p, this.pts[i - 1] ?? p) > 1e-9);
      if (pts.length >= 3 && level) {
        const out = await this.store.run("model.add_extrusion", { points: pts, level, void: isVoid, depth: isVoid ? o.voidDepth : o.depth, base: isVoid ? o.voidBase : o.base });
        const id = (out?.result as { id?: string } | undefined)?.id;
        if (id) await this.store.select({ kind: "model", ids: [id] });
      } else toast(`${isVoid ? "A void" : "An extrusion"} needs an outline of at least three corners on a floor plan.`, { kind: "info" });
    }
    if (commit && this.store.tool === "sweep" && view) {
      const level = this.data?.view.level_id;
      const o = this.store.formOptions;
      const pts = this.pts.filter((p, i) => i === 0 || dist(p, this.pts[i - 1] ?? p) > 1e-9);
      if (pts.length >= (closed ? 3 : 2) && level) {
        if (o.sweepShape === "user" && !o.sweepPolygon) { toast("Pick the closed polygon the sweep uses as its profile first: select it, then choose Use selected polygon.", { kind: "info" }); this.pts = []; this.draw(); return; }
        const params = o.sweepShape === "user"
          ? { points: pts, closed, level, base: o.sweepBase, profile: "user", polygon: o.sweepPolygon, pivot: o.sweepPivot, angle: o.sweepAngle, offset_x: o.sweepOffset[0], offset_y: o.sweepOffset[1], offset_z: o.sweepOffset[2], mirror: o.sweepMirror, radius: o.sweepRadius }
          : { points: pts, closed, level, shape: o.sweepShape, width: o.sweepWidth, height: o.sweepHeight, base: o.sweepBase };
        const out = await this.store.run("model.add_sweep", params);
        const id = (out?.result as { id?: string } | undefined)?.id;
        if (id) await this.store.select({ kind: "model", ids: [id] });
      } else toast("A sweep needs a path of at least two points on a floor plan.", { kind: "info" });
    }
    if (commit && this.store.tool === "room" && view) {
      const level = this.data?.view.level_id;
      const pts = this.pts.filter((p, i) => i === 0 || dist(p, this.pts[i - 1] ?? p) > 1e-9);
      if (pts.length >= 3 && level) await this.store.run("model.add_room", { points: pts, level });
      else toast("A room needs at least three corners on a floor plan.", { kind: "info" });
    }
    this.pts = [];
    this.target = null;
    this.tagOf = null;
    this.updateHint();
    this.draw();
  }

  private async key(e: KeyboardEvent): Promise<void> {
    if (e.key === " ") { this.spaceDown = true; e.preventDefault(); return; }
    const arrow: Record<string, [number, number]> = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
    const step = arrow[e.key];
    if (step && !e.ctrlKey && !e.metaKey && !e.altKey) { e.preventDefault(); const px = e.shiftKey ? 240 : 60; this.panBy(step[0] * px, step[1] * px); return; }
    if ((e.key === "+" || e.key === "=") && !e.ctrlKey && !e.metaKey) { e.preventDefault(); this.zoom(1.25); return; }
    if (e.key === "-" && !e.ctrlKey && !e.metaKey) { e.preventDefault(); this.zoom(1 / 1.25); return; }
    if (e.key === "0" && !e.ctrlKey && !e.metaKey) { e.preventDefault(); this.fit(); return; }
    if (e.key === "Escape") { if (this.edit) { this.edit = null; this.anchor = null; this.draw(); } else if (this.grip) { this.grip = null; this.draw(); } else if (this.pts.length || this.target || this.tagOf) await this.finish(false); else this.store.setTool("select"); return; }
    if (e.key === "Enter" && this.store.tool === "dimension") {
      e.preventDefault();
      if (this.pts.length === 2 && this.cursor && this.data) {
        const [a, b] = this.pts as [Pt, Pt];
        const p = this.cursor;
        const offset = Math.abs((b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]));
        if (offset < 1e-9) { toast("Move the cursor away from the measured line before confirming.", { kind: "info" }); return; }
        if (await this.store.run("draw.dimension", { drawing: this.data.view.id, p1: a, p2: b, dimline_point: p })) this.pts = [];
        this.updateHint(); this.draw();
      }
      return;
    }
    if (e.key === "Backspace" && this.store.tool === "dimension" && this.pts.length) {
      e.preventDefault(); this.pts.pop(); this.updateHint(); this.draw(); return;
    }
    if (e.key === "Enter" && (this.store.tool === "polyline" || this.store.tool === "floor" || this.store.tool === "roof" || this.store.tool === "room" || this.store.tool === "extrude" || this.store.tool === "void" || this.store.tool === "sweep")) { await this.finish(true); return; }
    if ((e.key === "c" || e.key === "C") && (this.store.tool === "polyline" || this.store.tool === "sweep") && this.pts.length > 2) { await this.finish(true, true); return; }
    const sel = this.store.selection;
    if ((e.key === "Delete" || e.key === "Backspace") && sel?.kind === "drawing" && this.data) {
      e.preventDefault();
      await this.store.run("draw.delete", { drawing: this.data.view.id, ids: sel.ids });
    } else if ((e.key === "Delete" || e.key === "Backspace") && sel?.kind === "model") {
      e.preventDefault();
      for (const id of sel.ids) if (!(await this.store.run("model.delete", { id }))) break;
    }
  }

  private isModel(id: string | undefined): boolean {
    return !!id && ((this.data?.model ?? []).some((w) => w.id === id) || !!this.data?.projection?.items.some((i) => i.id === id));
  }

  /** The section whose cut line is under a plan point, if any. */
  private markAt(p: Pt): SectionMark | null {
    let best: SectionMark | null = null, bestD = PICK_PX / this.scale;
    for (const m of this.data?.section_marks ?? []) {
      const d = distToSegment(p, m.a, m.b);
      if (d <= bestD) { best = m; bestD = d; }
    }
    return best;
  }

  /** Selected drawing entities, or selected model walls when the selection is a wall. */
  private allSelectedIds(): string[] {
    const sel = this.store.selection;
    if (sel?.kind === "model") return sel.ids.filter((id) => this.isModel(id));
    return this.selectedIds();
  }

  private selectedIds(): string[] {
    const sel = this.store.selection;
    return sel?.kind === "drawing" && sel.view === this.data?.view.id ? sel.ids : [];
  }

  /** Every model object genuinely under the pointer, including those hidden by another cut.
   *  A wall should be selectable even when a foundation's cut fills the same area.
   *  The original projection depth order is otherwise preserved. */
  private modelCandidates(world: Pt): Array<{ id: string; category: string }> {
    const tolerance = PICK_PX / this.scale;
    const projection = this.data?.projection;
    if (projection) {
      const preferred = projectionAt(projection, world, tolerance);
      const hits = projection.items.filter((item) => itemContains(item, world, tolerance)).reverse();
      const ordered = [...(preferred ? hits.filter((item) => item.id === preferred) : []), ...hits.filter((item) => item.id !== preferred)];
      const candidates = ordered.filter((item, index) => ordered.findIndex((a) => a.id === item.id) === index)
        .map((item) => ({ id: item.id, category: item.category }));
      // At coincident wall/foundation cuts a foundation must not always mask
      // the wall. Repeated clicks still expose the foundation for editing.
      const foundation = candidates.findIndex((item) => item.category === "foundation");
      const wall = candidates.findIndex((item) => item.category === "wall");
      if (wall >= 0 && foundation >= 0 && foundation < wall) {
        const [w] = candidates.splice(wall, 1);
        if (w) candidates.splice(foundation, 0, w);
      }
      return candidates;
    }
    const priority = (category: string): number => ({ door: 0, window: 0, wall: 1, structural: 2, column: 2, foundation: 3,
      gutter: 4, downpipe: 4, floor: 5, roof: 5, room: 6 } as Record<string, number>)[category] ?? 4;
    const seen = new Set<string>();
    return (this.data?.model ?? []).map((item, index) => ({ item, index, distance: this.modelDistance(item, world) }))
      .filter((hit) => hit.distance <= tolerance)
      .sort((a, b) => priority(a.item.category) - priority(b.item.category) || a.distance - b.distance || b.index - a.index)
      .filter(({ item }) => { if (seen.has(item.id)) return false; seen.add(item.id); return true; })
      .map((hit) => ({ id: hit.item.id, category: hit.item.category }));
  }

  /** Repeated clicks at one place cycle through coincident model elements.
   *  A small pointer displacement or a different overlap stack starts anew. */
  private pickForSelection(world: Pt): string | null {
    const candidates = this.modelCandidates(world);
    const ids = candidates.map((item) => item.id);
    if (!ids.length) { this.pickCycle = null; return this.pick(world); }
    // Drawing annotations retain precedence over model geometry.
    const drawing = this.pickEntity(world);
    if (drawing) { this.pickCycle = null; return drawing; }
    const old = this.pickCycle;
    const same = old && dist(this.toScreen(old.at), this.toScreen(world)) < PICK_PX * 1.5 &&
      old.ids.length === ids.length && old.ids.every((id, index) => id === ids[index]);
    const index = same ? ((old.index + 1) % ids.length) : 0;
    this.pickCycle = { at: world, ids, index };
    this.canvas.title = ids.length > 1 ? `${ids.length} overlapping elements. Click again to select the next one.` : "";
    return ids[index] ?? null;
  }

  /** Right-click actions are owned by the shared model menu, in both 2D and 3D. */
  private openModelContextMenu(event: MouseEvent): void {
    const [x, y] = this.local(event);
    const candidates = this.modelCandidates(this.toWorld(x, y));
    const chosen = this.store.selection?.kind === "model" ? candidates.find((item) => this.store.selection?.kind === "model" && this.store.selection.ids.includes(item.id)) : undefined;
    const target = [chosen, ...candidates].find((item) => item && (item.category === "roof" || item.category === "door" || item.category === "window"));
    if (!target) return;
    this.contextAnchor.style.left = `${x}px`;
    this.contextAnchor.style.top = `${y}px`;
    this.store.openModelContextMenu(target.id, target.category, this.contextAnchor);
  }

  private pick(world: Pt): string | null {
    let best: string | null = null;
    let bestD = PICK_PX / this.scale;
    for (const e of this.entities()) {
      if (!this.visible(e) || e.kind === "hatch") continue;
      const d = entityDistance(e, world);
      if (d <= bestD) { best = e.id; bestD = d; }
    }
    if (best) return best;                              // drawn entities win over the wall they sit on
    const model = this.modelCandidates(world);
    if (model.length) return model[0]?.id ?? null;
    for (const e of this.entities()) if (this.visible(e) && e.kind === "hatch" && entityDistance(e, world) === 0) best = e.id;   // a hatch is picked by its fill, last
    return best;
  }

  /** How far a point is from a model element on the plan; a door is also hit on its open leaf. */
  private modelDistance(w: PlanWall, p: Pt): number {
    const s = w.symbol;
    const base = polygonDistance(p, w.polygon);
    return (w.category === "door" || w.category === "window") && s?.hinge && s.leaf_end ? Math.min(base, distToSegment(p, s.hinge, s.leaf_end)) : base;
  }

  /** The nearest drawn entity of the given kinds (never a wall or a hatch). */
  private pickEntity(world: Pt, kinds?: readonly string[]): string | null {
    let best: string | null = null;
    let bestD = PICK_PX / this.scale;
    for (const e of this.entities()) {
      if (!this.visible(e) || e.kind === "hatch" || (kinds && !kinds.includes(e.kind))) continue;
      const d = entityDistance(e, world);
      if (d <= bestD) { best = e.id; bestD = d; }
    }
    return best;
  }

  // ------------------------------------------------------------------------------------------------------------- render
  /** Read the element's real size and size the canvas to it. False while the view is hidden (zero size): nothing can be fitted yet. */
  private measure(): boolean {
    const r = this.el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const dpr = window.devicePixelRatio || 1;
    this.width = Math.round(r.width); this.height = Math.round(r.height);
    this.canvas.width = Math.round(this.width * dpr); this.canvas.height = Math.round(this.height * dpr);
    this.canvas.style.setProperty("width", `${this.width}px`); this.canvas.style.setProperty("height", `${this.height}px`);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return true;
  }

  private resize(): void {
    if (!this.measure()) return;
    if (this.pendingFit) { this.pendingFit = false; this.fit(); } else this.draw();
  }

  private readColors(): Colors {
    return { paper: cssColor("--paper", "#ffffff"), grid: cssColor("--grid", "#e4e7ec"), gridMajor: cssColor("--grid-major", "#cfd4dc"), ink: cssColor("--ink", "#1a1d23"),
      muted: cssColor("--muted", "#566070"), select: cssColor("--select", "#1a5fd1"), accent: cssColor("--accent", "#b89b00"), hover: cssColor("--hover-line", "#5b8def") };
  }

  draw(): void {
    if (this.frame) return;
    this.frame = requestAnimationFrame(() => { this.frame = 0; this.paint(); });
  }

  private paint(): void {
    const ctx = this.ctx;
    const k = (this.colors = this.readColors());
    ctx.save();
    ctx.fillStyle = k.paper; ctx.fillRect(0, 0, this.width, this.height);
    this.paintGrid(k);
    const sel = new Set(this.allSelectedIds());
    if (this.data?.projection) this.paintProjection(this.data.projection, sel, k);
    for (const cat of PAINT_ORDER) {
      for (const w of this.data?.model ?? []) if (w.category === cat) this.paintItem(w, sel.has(w.id) ? k.select : w.id === this.hover ? k.hover : k.ink, sel.has(w.id), k);
    }
    const ordered = [...this.entities().filter((e) => e.kind === "hatch"), ...this.entities().filter((e) => e.kind !== "hatch")];   // fills go under the lines
    for (const e of ordered) {
      if (!this.visible(e)) continue;
      const picked = sel.has(e.id) || this.target?.id === e.id;
      const state = picked ? "selected" : e.id === this.hover ? "hover" : "normal";
      this.paintEntity(e, state === "selected" ? k.select : state === "hover" ? k.hover : k.ink, state === "selected" ? 2.5 : 1.5);
    }
    this.paintSectionMarks(k);
    this.paintGrips(k);
    this.paintHandles(k);
    this.syncLayerControls();
    this.syncJunctionControls();
    this.paintPreview(k);
    if (this.drag?.moved) this.paintMoveGhost(k);
    if (this.edit?.moved) this.paintEditGhost(this.edit, k);
    this.syncWallHud();
    if (this.box) {
      const a = this.toScreen(this.box.from), b = this.toScreen(this.box.to);
      ctx.fillStyle = withAlpha(k.select, 0.13); ctx.strokeStyle = k.select; ctx.lineWidth = 1; ctx.setLineDash([5, 4]);
      ctx.fillRect(a[0], a[1], b[0] - a[0], b[1] - a[1]); ctx.strokeRect(a[0], a[1], b[0] - a[0], b[1] - a[1]);
    }
    if (this.snap && this.snap.kind !== "none" && this.cursor) this.paintSnap(this.snap, k);
    ctx.restore();
  }

  /** The '+' is anchored to the selected host in plans, sections and elevations.
   *  The projected roof/floor buttons are derived from real cut/surface geometry,
   *  not from a 2D plan polygon that projections do not have. */
  private syncLayerControls(): void {
    this.layerControls.replaceChildren();
    if (this.store.tool !== "select" || this.store.selection?.kind !== "model" || this.store.selection.ids.length !== 1) return;
    const id = this.store.selection.ids[0];
    const projection = this.data?.projection;
    const projected = projection?.items.find((item) => item.id === id && (item.category === "floor" || item.category === "roof"));
    const element = projection ? null : this.data?.model.find((item) => item.id === id && (item.category === "wall" || item.category === "floor" || item.category === "roof"));
    if (!projected && !element) return;
    let points: Array<{ at: Pt; side: string }> = [];
    if (projected) {
      const vertices: Pt[] = [...projected.cut.flat(), ...projected.tris.flatMap((tri) =>
        [[tri[0] ?? 0, tri[1] ?? 0], [tri[2] ?? 0, tri[3] ?? 0], [tri[4] ?? 0, tri[5] ?? 0]] as Pt[]),
      ...projected.edges.flatMap((edge) => [[edge[0] ?? 0, edge[1] ?? 0], [edge[2] ?? 0, edge[3] ?? 0]] as Pt[])];
      const bounds = vertices.length ? polygonsBounds([vertices]) : null;
      if (!bounds) return;
      const center = (bounds[0][0] + bounds[1][0]) / 2, pad = 24 / this.scale;
      const labels = projected.category === "roof" ? ["outside", "inside"] : ["top", "bottom"];
      points = [{ side: labels[0] as string, at: [center, bounds[1][1] + pad] }, { side: labels[1] as string, at: [center, bounds[0][1] - pad] }];
    } else if (element?.category === "wall" && element.axis) {
      const { a, b, thickness } = element.axis;
      const length = Math.max(dist(a, b), 1e-9), nx = -(b[1] - a[1]) / length, ny = (b[0] - a[0]) / length;
      const center = mid(a, b), offset = thickness / 2 + 20 / this.scale;
      points = [{ side: "left", at: [center[0] + nx * offset, center[1] + ny * offset] }, { side: "right", at: [center[0] - nx * offset, center[1] - ny * offset] }];
    } else if (element) {
      const bounds = polygonsBounds([element.polygon]);
      if (!bounds) return;
      const center: Pt = [(bounds[0][0] + bounds[1][0]) / 2, (bounds[0][1] + bounds[1][1]) / 2];
      const labels = element.category === "roof" ? ["outside", "inside"] : ["top", "bottom"];
      points = [{ side: labels[0] as string, at: [center[0], center[1] + 25 / this.scale] }, { side: labels[1] as string, at: [center[0], center[1] - 25 / this.scale] }];
    }
    for (const { at, side } of points) {
      const [x, y] = this.toScreen(at);
      const kind = (projected?.category ?? element?.category) as "wall" | "floor" | "roof";
      const button = h("button", { class: "layer-plus", type: "button", text: "+", attrs: { title: `Add layer on ${side} side`, "aria-label": `Add ${side} ${kind} layer` },
        on: { click: (event) => { event.stopPropagation(); void addLayerDialog(this.store, id as string, kind, side); } } });
      button.style.left = `${x}px`; button.style.top = `${y}px`;
      this.layerControls.appendChild(button);
    }
  }

  /** Four real wall-corner join policies, offered at the corner itself on hover or selection. */
  private syncJunctionControls(): void {
    // Canvas redraws must not discard keyboard focus from a corner control.
    const focused = this.junctionControls.contains(document.activeElement) ? document.activeElement?.getAttribute("data-focus-key") : null;
    this.junctionControls.replaceChildren();
    if (this.store.tool !== "select" || this.data?.projection) return;
    const chosen = this.store.selection?.kind === "model" ? this.store.selection.ids : [];
    const seen = new Set<string>();
    const choices = [
      { mode: "through_selected", label: "1 · Selected wall passes through", description: "The selected wall continues; the other terminates." },
      { mode: "through_other", label: "2 · Other wall passes through", description: "The connecting wall continues; the selected one terminates." },
      { mode: "stepped_open", label: "3 · Stepped / point-contact (open corner)", description: "An intentionally open, point-contact corner; this is not watertight." },
      { mode: "miter", label: "4 · Mitered corner", description: "Both walls terminate along a diagonal cut." },
    ] as const;
    for (const wall of this.data?.model ?? []) {
      if (wall.category !== "wall" || (wall.id !== this.hover && !chosen.includes(wall.id))) continue;
      for (const join of wall.corner_joins ?? []) {
        const key = `${wall.id}:${join.endpoint}:${join.other}`;
        if (seen.has(key)) continue;
        seen.add(key);
        const [x, y] = this.toScreen(join.point);
        const active = this.openedJoin?.wall === wall.id && this.openedJoin.other === join.other && this.openedJoin.endpoint === join.endpoint;
        const icon = h("button", { type: "button", class: "junction-button", text: "⋈", attrs: {
          title: `Wall corner join (${join.mode.replaceAll("_", " ")}). Select one of four connection types.`,
          "aria-label": `Edit wall corner connection at ${join.endpoint}`,
          "aria-expanded": active ? "true" : "false", "data-focus-key": `join:${key}` },
        on: { click: (e) => { e.stopPropagation(); this.openedJoin = active ? null : { wall: wall.id, other: join.other, endpoint: join.endpoint }; this.draw(); } } });
        icon.style.left = `${x}px`; icon.style.top = `${y}px`;
        this.junctionControls.appendChild(icon);
        if (!active) continue;
        const menu = h("div", { class: "junction-menu", attrs: { role: "group", "aria-label": "Wall corner connection type" } });
        for (const option of choices) {
          const selected = join.mode === option.mode;
          const button = h("button", { type: "button", class: `junction-option${selected ? " active" : ""}`,
            text: option.label, attrs: { title: option.description, "aria-pressed": selected ? "true" : "false", "data-focus-key": `join:${key}:${option.mode}` },
            on: { click: (e) => {
              e.stopPropagation();
              void (async () => {
                const changed = await this.store.run("model.set_property", { id: wall.id, key: `corner_join:${join.other}:${join.endpoint}`, value: option.mode });
                if (changed) { this.openedJoin = null; this.draw(); }
              })();
            }, keydown: (e) => {
              if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); this.openedJoin = null; this.draw(); }
            } } });
          menu.appendChild(button);
        }
        menu.style.left = `${Math.max(6, Math.min(this.width - 238, x + 18))}px`;
        menu.style.top = `${Math.max(6, Math.min(this.height - 152, y - 14))}px`;
        this.junctionControls.appendChild(menu);
      }
    }
    if (focused) {
      const next = [...this.junctionControls.querySelectorAll<HTMLElement>("[data-focus-key]")].find((node) => node.getAttribute("data-focus-key") === focused);
      next?.focus({ preventScroll: true });
    }
  }

  /** The wall options strip shows while the wall tool is active, with the values the 3D view's wall tool uses too. */
  private syncWallHud(): void {
    for (const [tool, hud] of this.formHuds) {
      const on = this.store.tool === tool;
      if (hud.el.hidden === on) { hud.el.hidden = !on; if (on) hud.sync(); }
    }
    const show = this.store.tool === "wall";
    if (this.wallHud.hidden === !show) return;
    this.wallHud.hidden = !show;
    if (show) {
      this.wallFields.height.value = String(this.store.wallOptions.height);
      this.wallFields.thickness.value = String(this.store.wallOptions.thickness);
    }
  }

  private mmPerUnit(): number { return MM_PER[this.store.unit]; }

  private tracePolygon(poly: readonly Pt[]): void {
    poly.forEach((p, i) => { const [x, y] = this.toScreen(p); if (i) this.ctx.lineTo(x, y); else this.ctx.moveTo(x, y); });
    this.ctx.closePath();
  }

  /** The handles on the selection (square for stretching, round for turning), the length of a selected wall, and the stem of the turn handle. */
  private paintHandles(k: Colors): void {
    this.lengthBox = null;
    const list = this.handles();
    if (!list.length) return;
    const ctx = this.ctx;
    ctx.setLineDash([]); ctx.lineWidth = 1.5; ctx.strokeStyle = k.select; ctx.fillStyle = k.paper;
    for (const hd of list) {
      const [x, y] = this.toScreen(hd.at);
      ctx.beginPath();
      if (hd.shape === "square") { ctx.rect(x - 5, y - 5, 10, 10); ctx.fill(); ctx.stroke(); continue; }
      ctx.moveTo(x, y + TURN_OFFSET_PX - 8); ctx.lineTo(x, y + 8); ctx.stroke();                 // the stem down to the selection
      ctx.beginPath(); ctx.arc(x, y, 8, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      ctx.beginPath(); ctx.arc(x, y, 3.6, -Math.PI * 0.2, Math.PI * 1.3); ctx.stroke();            // a turning arrow
      ctx.beginPath(); ctx.moveTo(x + 4, y - 5.6); ctx.lineTo(x + 4.6, y - 1.2); ctx.lineTo(x + 0.6, y - 3.6); ctx.stroke();
    }
    const ends = list.filter((x) => x.shape === "square");
    const wall = this.edit?.kind === "end" ? null : ends.length === 2 && !this.markSel ? this.data?.model.find((w) => w.axis && (w.category === "wall" || w.category === "grid") && this.allSelectedIds()[0] === w.id) : undefined;
    if (wall?.axis && !this.edit) this.paintLength(wall.axis.a, wall.axis.b, wall.axis.thickness, wall.id, k, true);
  }

  /** The temporary dimension of a wall: its length, drawn beside it. A selected wall's can be clicked to type a new length. */
  private paintLength(a: Pt, b: Pt, thickness: number, id: string, k: Colors, clickable: boolean): void {
    const ctx = this.ctx;
    const [ax, ay] = this.toScreen(a), [bx, by] = this.toScreen(b);
    const len = Math.hypot(bx - ax, by - ay) || 1;
    const off = thickness * this.scale / 2 + 18;
    const nx = (-(by - ay) / len) * off, ny = ((bx - ax) / len) * off;                       // the dimension line runs beside the wall, off its face
    const mx = (ax + bx) / 2 + nx, my = (ay + by) / 2 + ny;
    const text = `${dist(a, b).toLocaleString(undefined, { maximumFractionDigits: 1 })} ${this.store.unit}`;
    ctx.font = `12px ${getComputedStyle(document.documentElement).getPropertyValue("--font-ui") || "sans-serif"}`;
    const w = ctx.measureText(text).width + 12, hgt = 20;
    ctx.setLineDash([]); ctx.strokeStyle = k.select; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(ax + nx, ay + ny); ctx.lineTo(bx + nx, by + ny); ctx.lineTo(bx, by); ctx.stroke();
    ctx.fillStyle = k.paper; ctx.fillRect(mx - w / 2, my - hgt / 2, w, hgt);
    ctx.strokeRect(mx - w / 2 + 0.5, my - hgt / 2 + 0.5, w, hgt);
    ctx.fillStyle = k.select; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText(text, mx, my + 0.5);
    ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
    if (clickable) this.lengthBox = { x: mx - w / 2, y: my - hgt / 2, w, h: hgt, id, a, b };
  }

  /** A floor's outline as a corner or edge drag would leave it: a corner goes where the pointer is; an edge slides along its own normal, so its neighbours keep their directions. */
  private outlineAfter(ed: Extract<Edit, { kind: "outline" }>): Pt[] | null {
    const pts = ed.points, n = pts.length;
    if (ed.corner >= 0) return pts.map((q, i) => (i === ed.corner ? ed.now : q));
    const a = pts[ed.edge] as Pt, b = pts[(ed.edge + 1) % n] as Pt;
    const len = dist(a, b);
    if (len < 1e-9) return null;
    const nx = -(b[1] - a[1]) / len, ny = (b[0] - a[0]) / len;
    const d = (ed.now[0] - ed.from[0]) * nx + (ed.now[1] - ed.from[1]) * ny;
    return pts.map((q, i) => (i === ed.edge || i === (ed.edge + 1) % n ? [q[0] + nx * d, q[1] + ny * d] as Pt : q));
  }

  /** What a handle drag would leave behind if it were dropped now. */
  private paintEditGhost(ed: Edit, k: Colors): void {
    const ctx = this.ctx;
    ctx.save();
    ctx.setLineDash([6, 4]); ctx.lineWidth = 1.5; ctx.strokeStyle = k.select; ctx.fillStyle = withAlpha(k.select, 0.14);
    if (ed.kind === "end") {
      const a = ed.end === "a" ? ed.now : ed.axis.a, b = ed.end === "b" ? ed.now : ed.axis.b;
      if (dist(a, b) > 1e-9) {
        ctx.beginPath();
        if (ed.axis.thickness > 0) { this.tracePolygon(wallBand(a, b, ed.axis.thickness)); ctx.fill(); } else this.line(a, b);                       // a grid has no thickness: only its line
        ctx.stroke(); this.paintLength(a, b, ed.axis.thickness, ed.id, k, false);
      }
    } else if (ed.kind === "outline") {
      const next = this.outlineAfter(ed);
      if (next) { ctx.beginPath(); this.tracePolygon(next); ctx.fill(); ctx.stroke(); }
    } else if (ed.kind === "ridge") {
      const d = (ed.now[0] - ed.from[0]) * ed.across[0] + (ed.now[1] - ed.from[1]) * ed.across[1];
      const a: Pt = [ed.segment[0][0] + ed.across[0] * d, ed.segment[0][1] + ed.across[1] * d];
      const b: Pt = [ed.segment[1][0] + ed.across[0] * d, ed.segment[1][1] + ed.across[1] * d];
      ctx.beginPath(); this.line(a, b); ctx.stroke(); this.paintBadge(`Ridge ${((ed.offset + d) * (this.store.unit === "m" ? 1000 : this.store.unit === "cm" ? 10 : 1)).toFixed(0)} mm`, ed.now, k);
    } else if (ed.kind === "ridge_projection") {
      const at: Pt = [ed.now[0], ed.from[1]];
      ctx.beginPath(); this.line(ed.from, at); ctx.stroke();
      const targetMm = ed.offsetMm + (ed.now[0] - ed.from[0]) * MM_PER[this.store.unit] / ed.transverseDot;
      this.paintBadge(`Ridge ${targetMm.toFixed(0)} mm (height fixed)`, at, k);
    } else if (ed.kind === "turn") {
      const angle = this.turnAngle(ed);
      for (const w of this.data?.model ?? []) if (ed.ids.includes(w.id)) { ctx.beginPath(); this.tracePolygon(w.polygon.map((p) => rotateAbout(p, ed.centre, angle))); ctx.fill(); ctx.stroke(); }
      this.paintBadge(`${((angle * 180) / Math.PI).toFixed(0)}°`, ed.raw, k);
    } else {
      const ends = this.markEnds(ed);
      if (ends) { ctx.beginPath(); this.line(ends[0], ends[1]); ctx.stroke(); }
    }
    ctx.restore();
  }

  /** A small readout beside the pointer. */
  private paintBadge(text: string, at: Pt, k: Colors): void {
    const ctx = this.ctx;
    const [sx, sy] = this.toScreen(at);
    ctx.setLineDash([]); ctx.font = `12px ${getComputedStyle(document.documentElement).getPropertyValue("--font-ui") || "sans-serif"}`;
    const w = ctx.measureText(text).width + 10;
    ctx.fillStyle = k.paper; ctx.fillRect(sx + 14, sy + 14, w, 20);
    ctx.strokeStyle = k.select; ctx.strokeRect(sx + 14.5, sy + 14.5, w, 20);
    ctx.fillStyle = k.ink; ctx.textBaseline = "middle"; ctx.textAlign = "left"; ctx.fillText(text, sx + 19, sy + 24.5);
    ctx.textBaseline = "alphabetic";
  }

  /** A section or an elevation: the level lines, then each element far to near (its surfaces cover what is behind, then its lines), then every cut on top. */
  private paintProjection(pr: Projection, sel: Set<string>, k: Colors): void {
    const ctx = this.ctx;
    const mmPerUnit = MM_PER[this.store.unit];
    ctx.lineJoin = "round"; ctx.lineCap = "round";
    if (pr.bounds) {
      const pad = Math.max((pr.bounds[1][0] - pr.bounds[0][0]) * 0.05, 40 / this.scale);
      ctx.font = `12px ${getComputedStyle(document.documentElement).getPropertyValue("--font-ui") || "sans-serif"}`;
      ctx.textAlign = "right"; ctx.textBaseline = "bottom";
      for (const l of visibleLevels(pr, mmPerUnit)) {
        const [x0, y] = this.toScreen([pr.bounds[0][0] - pad, l.z]), [x1] = this.toScreen([pr.bounds[1][0] + pad, l.z]);
        ctx.strokeStyle = k.muted; ctx.lineWidth = 1; ctx.setLineDash([10, 5]);
        ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(x1, y); ctx.stroke(); ctx.setLineDash([]);
        ctx.fillStyle = k.muted; ctx.fillText(levelLabel(l.name, l.z, mmPerUnit), x0 - 6, y - 2);
      }
    }
    this.paintGridMarks(pr, k);
    const screen = (t: number[]): void => { for (let i = 0; i < t.length; i += 2) { const [x, y] = this.toScreen([t[i] ?? 0, t[i + 1] ?? 0]); if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y); } };
    for (const item of pr.items) {
      const picked = sel.has(item.id), hot = item.id === this.hover;
      ctx.beginPath(); for (const t of item.tris) { screen(t); ctx.closePath(); }
      ctx.fillStyle = k.paper; ctx.fill("nonzero");                                  // opaque: this is what hides the elements behind it
      if (picked || hot) { ctx.fillStyle = withAlpha(picked ? k.select : k.hover, picked ? 0.22 : 0.12); ctx.fill("nonzero"); }
      ctx.beginPath();
      for (const e of item.edges) { const [ax, ay] = this.toScreen([e[0] ?? 0, e[1] ?? 0]), [bx, by] = this.toScreen([e[2] ?? 0, e[3] ?? 0]); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); }
      ctx.setLineDash([]); ctx.lineWidth = picked ? 2.2 : 1.1; ctx.strokeStyle = picked ? k.select : k.ink; ctx.stroke();
      if (item.opening) {                                                            // opening direction: solid toward the viewer, dashed away from it, per panel
        for (const part of [item.opening, ...(item.opening.more ?? [])]) {
          ctx.beginPath();
          for (const l of part.lines) { const [ax, ay] = this.toScreen([l[0] ?? 0, l[1] ?? 0]), [bx, by] = this.toScreen([l[2] ?? 0, l[3] ?? 0]); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); }
          ctx.setLineDash(part.toward ? [] : [7, 5]); ctx.lineWidth = 1; ctx.stroke(); ctx.setLineDash([]);
        }
      }
    }
    for (const item of pr.items) {
      if (!item.cut.length) continue;
      const picked = sel.has(item.id);
      if (item.cut_material) { this.paintLayers(item, picked, k); continue; }                              // a foundation: each layer in its own material's hatch
      if (item.cut_hatches) {                                                                               // a layered wall, floor or roof: each loop is a layer with its own hatch
        const ink = picked ? k.select : k.ink;
        item.cut.forEach((loop, n) => {
          const hatch = item.cut_hatches?.[n] ?? null;
          ctx.beginPath(); loop.forEach((p, i) => { const [x, y] = this.toScreen(p); if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); }); ctx.closePath();
          ctx.fillStyle = withAlpha(ink, cutWeight(item.category) * hatchFill(hatch)); ctx.fill();
          ctx.lineWidth = picked ? 2.2 : 1.4; ctx.strokeStyle = ink; ctx.stroke();
          if (hatch) this.paintCutHatch([loop], hatch, ink);
        });
        continue;
      }
      ctx.beginPath();
      for (const loop of item.cut) { loop.forEach((p, i) => { const [x, y] = this.toScreen(p); if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); }); ctx.closePath(); }
      ctx.fillStyle = withAlpha(picked ? k.select : k.ink, cutWeight(item.category) * hatchFill(item.cut_hatch)); ctx.fill("evenodd");
      ctx.lineWidth = picked ? 2.5 : 1.8; ctx.strokeStyle = picked ? k.select : k.ink; ctx.stroke();
      if (item.cut_hatch) this.paintCutHatch(item.cut, item.cut_hatch, picked ? k.select : k.ink);
    }
  }

  /** A cut's hatch, drawn in screen units so its spacing reads the same at any zoom (the pattern's spacing on paper at about 4 px per mm). */
  private paintCutHatch(loops: readonly (readonly Pt[])[], hatch: HatchSpec, color: string): void {
    if (hatch.kind === "none" || hatch.kind === "solid") return;
    const ctx = this.ctx;
    const screen = loops.map((l) => l.map((p) => this.toScreen(p)));
    const d = patternPath(screen, hatch, Math.max(4, hatch.spacing_mm * 4), true);
    if (!d) return;
    ctx.save(); ctx.strokeStyle = color; ctx.lineWidth = 0.8; ctx.setLineDash([]);
    ctx.stroke(new Path2D(d)); ctx.restore();
  }

  /** The grids a section or elevation meets: a dashed vertical line across the picture with its bubble above. */
  private paintGridMarks(pr: Projection, k: Colors): void {
    if (!pr.bounds || !pr.grid_marks?.length) return;
    const ctx = this.ctx;
    const pad = Math.max((pr.bounds[1][1] - pr.bounds[0][1]) * 0.08, 30 / this.scale);
    const r = 11;
    ctx.font = `bold 12px ${getComputedStyle(document.documentElement).getPropertyValue("--font-ui") || "sans-serif"}`;
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    for (const g of pr.grid_marks) {
      const [x, y0] = this.toScreen([g.x, pr.bounds[0][1] - pad]), [, y1] = this.toScreen([g.x, pr.bounds[1][1] + pad]);
      ctx.strokeStyle = k.muted; ctx.lineWidth = 1; ctx.setLineDash([16, 4, 2, 4]);
      ctx.beginPath(); ctx.moveTo(x, y0); ctx.lineTo(x, y1 - r * 2); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = k.paper; ctx.beginPath(); ctx.arc(x, y1 - r, r, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      ctx.fillStyle = k.ink; ctx.fillText(g.name, x, y1 - r + 0.5);
    }
    ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
  }

  /** The cut of a build-up (a foundation): every loop is one layer, tinted and hatched by its material, outlined on top. */
  private paintLayers(item: ProjItem, picked: boolean, k: Colors): void {
    const ctx = this.ctx;
    const ink = picked ? k.select : k.ink;
    item.cut.forEach((loop, n) => {
      const style = cutStyle(item.cut_material?.[n] ?? "Concrete");
      const path = (): void => { ctx.beginPath(); loop.forEach((p, i) => { const [x, y] = this.toScreen(p); if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); }); ctx.closePath(); };
      path(); ctx.fillStyle = withAlpha(ink, style.alpha); ctx.fill();
      const xs = loop.map((p) => this.toScreen(p)[0]), ys = loop.map((p) => this.toScreen(p)[1]);
      const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
      if (style.hatch !== "none" && x1 - x0 > 2 && y1 - y0 > 2) {
        ctx.save(); path(); ctx.clip(); ctx.beginPath(); ctx.lineWidth = 0.8; ctx.strokeStyle = ink; ctx.setLineDash([]);
        const step = 8;
        if (style.hatch === "dots") { for (let y = Math.floor(y0 / step) * step; y < y1; y += step) for (let x = Math.floor(x0 / step) * step + ((Math.round(y / step) % 2) * step) / 2; x < x1; x += step) { ctx.moveTo(x + 1, y); ctx.arc(x, y, 1, 0, Math.PI * 2); } ctx.fillStyle = ink; ctx.fill(); }
        else {
          for (let t = -(y1 - y0); t < x1 - x0; t += step) { ctx.moveTo(x0 + t, y1); ctx.lineTo(x0 + t + (y1 - y0), y0); }
          if (style.hatch === "cross") for (let t = 0; t < x1 - x0 + (y1 - y0); t += step) { ctx.moveTo(x0 + t, y1); ctx.lineTo(x0 + t - (y1 - y0), y0); }
          ctx.stroke();
        }
        ctx.restore();
      }
      path(); ctx.lineWidth = picked ? 2.5 : 1.6; ctx.strokeStyle = ink; ctx.stroke();
    });
  }

  /** The cut lines of the sections, on a plan: a dash-dot line, an arrowhead at each end showing which way it looks, and a lettered bubble. */
  private paintSectionMarks(k: Colors): void {
    const marks = (this.data?.section_marks ?? []).map((m) => {
      const ed = this.edit;
      if (ed?.kind !== "mark" || ed.id !== m.id || !ed.moved) return m;
      const ends = this.markEnds(ed);
      if (!ends) return m;
      const len = dist(ends[0], ends[1]) || 1;
      return { ...m, a: ends[0], b: ends[1], look: [-(ends[1][1] - ends[0][1]) / len, (ends[1][0] - ends[0][0]) / len] as Pt };      // it looks to the left of the line
    });
    if (!marks.length) return;
    const ctx = this.ctx;
    ctx.font = `bold 12px ${getComputedStyle(document.documentElement).getPropertyValue("--font-ui") || "sans-serif"}`;
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    for (const m of marks) {
      const color = m.id === this.hoverMark || m.id === this.markSel ? k.select : k.ink;
      ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = m.id === this.markSel ? 2 : 1.2; ctx.setLineDash([14, 4, 2, 4]);
      ctx.beginPath(); this.line(m.a, m.b); ctx.stroke(); ctx.setLineDash([]);
      for (const a of markArrows(m, 26 / this.scale)) {
        const [fx, fy] = this.toScreen(a.from), [tx, ty] = this.toScreen(a.tip), [lx, ly] = this.toScreen(a.left), [rx, ry] = this.toScreen(a.right);
        ctx.lineWidth = 1.4; ctx.beginPath(); ctx.moveTo(fx, fy); ctx.lineTo(tx, ty); ctx.moveTo(lx, ly); ctx.lineTo(tx, ty); ctx.lineTo(rx, ry); ctx.stroke();
      }
      const r = 11;
      for (const base of [m.a, m.b]) {
        const [bx, by] = this.toScreen(base);
        const cx = bx - m.look[0] * r * 1.2, cy = by + m.look[1] * r * 1.2;           // behind the end, the opposite way to the arrow (screen y points down)
        ctx.fillStyle = k.paper; ctx.lineWidth = 1.2; ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
        ctx.fillStyle = color; ctx.fillText(markLabel(m.name), cx, cy + 0.5);
      }
    }
  }

  private paintGrid(k: Colors): void {
    const ctx = this.ctx;
    const step = this.gridStep();
    const lo = this.toWorld(0, this.height), hi = this.toWorld(this.width, 0);
    const major = step * 5;
    ctx.lineWidth = 1;
    for (const [axis, from, to] of [[0, lo[0], hi[0]], [1, lo[1], hi[1]]] as const) {
      for (let v = Math.floor(from / step) * step; v <= to + step; v += step) {
        const isMajor = Math.abs(v / major - Math.round(v / major)) < 1e-9;
        ctx.strokeStyle = isMajor ? k.gridMajor : k.grid;
        ctx.beginPath();
        if (axis === 0) { const x = Math.round(this.toScreen([v, 0])[0]) + 0.5; ctx.moveTo(x, 0); ctx.lineTo(x, this.height); }
        else { const y = Math.round(this.toScreen([0, v])[1]) + 0.5; ctx.moveTo(0, y); ctx.lineTo(this.width, y); }
        ctx.stroke();
      }
    }
  }

  /** One model element as the plan shows it: walls solid, floors a light tint, beams dashed, doors with their swing, windows with a glazing line. */
  private paintItem(w: PlanWall, color: string, selected: boolean, k: Colors): void {
    const ctx = this.ctx;
    const trace = (): void => {
      ctx.beginPath();
      for (const ring of [w.polygon, ...(w.holes ?? [])]) { ring.forEach((p, i) => { const [x, y] = this.toScreen(p); if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); }); ctx.closePath(); }
    };
    ctx.lineJoin = "round"; ctx.lineCap = "round";
    if (w.category === "wall") {
      this.paintWall(w.polygon, color, selected);
      if (w.bands?.length) {                                                                                // a layered wall: every layer's band with its own hatch and edges
        for (const band of w.bands) for (const poly of band.polygons) {
          ctx.beginPath(); poly.forEach((p, i) => { const [x, y] = this.toScreen(p); if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); }); ctx.closePath();
          ctx.lineWidth = 1; ctx.setLineDash([]); ctx.strokeStyle = color; ctx.stroke();
          if (band.hatch) this.paintCutHatch([poly], band.hatch, color);
        }
      } else if (w.hatch) this.paintCutHatch([w.polygon], w.hatch, color);
      return;
    }
    ctx.lineWidth = selected ? 2.5 : 1.5; ctx.strokeStyle = color; ctx.fillStyle = color;
    if (w.category === "floor" || w.category === "roof") {
      trace(); ctx.setLineDash([]); ctx.lineWidth = selected ? 2 : 1; ctx.fillStyle = withAlpha(color, selected ? 0.18 : 0.06); ctx.fill("evenodd");
      ctx.strokeStyle = selected ? color : withAlpha(k.muted, 0.9); ctx.stroke();
      if (w.ridge) { ctx.beginPath(); this.line(w.ridge[0], w.ridge[1]); ctx.setLineDash([5, 3]); ctx.strokeStyle = selected ? k.select : color; ctx.stroke(); ctx.setLineDash([]); }
      for (const poly of w.cut_polygons ?? []) {
        ctx.beginPath(); poly.forEach((p, i) => { const [x, y] = this.toScreen(p); if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); }); ctx.closePath();
        ctx.fillStyle = withAlpha(color, selected ? 0.35 : 0.2); ctx.fill(); ctx.strokeStyle = color; ctx.stroke();
        if (w.hatch) this.paintCutHatch([poly], w.hatch, color);
      }
      return;
    }
    if (w.category === "opening_profile") {
      // Each frame ring comes from a real horizontal SGEO intersection.
      for (const ring of w.cut_polygons ?? []) {
        ctx.beginPath();
        ring.forEach((p, i) => { const [x, y] = this.toScreen(p); if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); });
        ctx.closePath(); ctx.setLineDash([]);
        ctx.fillStyle = withAlpha(color, selected ? 0.5 : 0.3);
        ctx.fill("evenodd"); ctx.stroke();
      }
      return;
    }
    if (w.category === "foundation") {                                                                      // below the cut plane, so drawn as hidden outline with a light tint
      trace(); ctx.setLineDash([9, 4, 2, 4]); ctx.lineWidth = selected ? 2.2 : 1.2; ctx.fillStyle = withAlpha(color, selected ? 0.22 : 0.07); ctx.fill("evenodd"); ctx.stroke(); ctx.setLineDash([]);
      return;
    }
    if (w.category === "extrusion" || w.category === "sweep") {
      trace(); ctx.setLineDash([]); ctx.fillStyle = withAlpha(color, selected ? 0.5 : 0.32); ctx.fill("evenodd"); ctx.stroke();
      if (w.path && w.path.length > 1) {                                                                    // the path a sweep follows
        ctx.beginPath(); w.path.forEach((p, i) => { const [x, y] = this.toScreen(p); if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); }); if (w.closed) ctx.closePath();
        ctx.setLineDash([2, 4]); ctx.lineWidth = 1; ctx.stroke(); ctx.setLineDash([]);
      }
      return;
    }
    if (w.category === "void") {
      trace(); ctx.setLineDash(w.off ? [2, 4] : [7, 4]); ctx.fillStyle = withAlpha(color, selected ? 0.16 : 0.06); ctx.fill("evenodd"); ctx.stroke(); ctx.setLineDash([]);
      if (w.off) return;                                                                                    // cutting is off: outlined, not hatched
      const xs = w.polygon.map((p) => this.toScreen(p)[0]), ys = w.polygon.map((p) => this.toScreen(p)[1]);
      const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
      ctx.save(); trace(); ctx.clip();                                                                      // hatched, as a cut-out is drawn
      ctx.beginPath(); for (let t = -(y1 - y0); t < x1 - x0; t += 9) { ctx.moveTo(x0 + t, y1); ctx.lineTo(x0 + t + (y1 - y0), y0); }
      ctx.lineWidth = 0.8; ctx.stroke(); ctx.restore();
      return;
    }
    if (w.category === "room") { this.paintRoom(w, trace, color, selected, k); return; }
    if (w.category === "stair") { this.paintStair(w, trace, color, selected, k); return; }
    if (w.category === "grid") { this.paintGrid2(w, color, selected, k); return; }
    if (w.category === "generic") { trace(); ctx.setLineDash([]); ctx.fillStyle = withAlpha(color, selected ? 0.4 : 0.22); ctx.fill(); ctx.stroke(); return; }
    if (w.category === "gutter") { trace(); ctx.setLineDash([]); ctx.fillStyle = withAlpha(color, selected ? 0.45 : 0.25); ctx.fill(); ctx.stroke(); return; }
    if (w.category === "downpipe") {                                                                      // a ring with a dot: a pipe seen end-on, filled where it is cut
      trace(); ctx.setLineDash([]); ctx.fillStyle = withAlpha(k.paper, 1); ctx.fill(); ctx.stroke();
      const xs = w.polygon.map((p) => this.toScreen(p)[0]), ys = w.polygon.map((p) => this.toScreen(p)[1]);
      const cx = (Math.min(...xs) + Math.max(...xs)) / 2, cy = (Math.min(...ys) + Math.max(...ys)) / 2;
      ctx.beginPath(); ctx.arc(cx, cy, Math.max(1.5, (Math.max(...xs) - Math.min(...xs)) / 6), 0, Math.PI * 2); ctx.fillStyle = color; ctx.fill();
      return;
    }
    if (w.category === "column") {
      trace(); ctx.setLineDash([]); ctx.fillStyle = withAlpha(color, selected ? 0.55 : 0.4); ctx.fill(); ctx.stroke();
      const a = w.polygon[0], c = w.polygon[2];
      if (a && c) { ctx.beginPath(); this.line(a, c); const b = w.polygon[1], d = w.polygon[3]; if (b && d) this.line(b, d); ctx.lineWidth = 1; ctx.stroke(); }      // the cross of a column in plan
      return;
    }
    if (w.category === "structural") { trace(); ctx.setLineDash([8, 5]); ctx.stroke(); ctx.setLineDash([]); return; }
    ctx.setLineDash([]);
    const poly = w.polygon;
    const edge = (i: number, j: number): void => { const a = poly[i], b = poly[j]; if (a && b) this.line(a, b); };
    if ((w.category === "window" || w.category === "door") && w.cut === false) {
      // The wall is uncut at this elevation. Show an in-range opening as a
      // projected outline, without erasing the continuous wall behind it.
      trace(); ctx.setLineDash(w.overhead ? [6, 3] : [2, 4]); ctx.lineWidth = 1; ctx.stroke(); ctx.setLineDash([]);
      return;
    }
    if (w.category === "window" && w.symbol?.a && w.symbol.b) {
      trace(); ctx.fillStyle = withAlpha(k.paper, 1); ctx.fill(); ctx.stroke();
      ctx.beginPath(); this.line(w.symbol.a, w.symbol.b); ctx.stroke();
      this.paintSwing(w.symbol);                                                                    // a casement shows its sash and swing, a fixed window has none
      return;
    }
    if (w.category === "door") {
      ctx.fillStyle = withAlpha(k.paper, 1); trace(); ctx.fill();                                   // clear the wall behind the opening
      ctx.beginPath(); edge(3, 0); edge(1, 2); ctx.stroke();                                       // the jambs
      this.paintSwing(w.symbol);
    }
  }

  /** The opening direction of a door or casement on a plan: the open leaf and the arc it sweeps. Absent when the view hides it. */
  private paintSwing(s: PlanSymbol | undefined): void {
    if (!(s?.hinge && s.leaf_end && s.radius !== undefined && s.start_angle !== undefined && s.end_angle !== undefined)) return;
    const ctx = this.ctx;
    for (const l of [{ hinge: s.hinge, leaf_end: s.leaf_end, radius: s.radius, start_angle: s.start_angle, end_angle: s.end_angle }, ...(s.more ?? [])]) {      // one leaf per swinging panel
      ctx.beginPath(); this.line(l.hinge, l.leaf_end); ctx.stroke();                                // the open leaf
      const [cx, cy] = this.toScreen(l.hinge);
      ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(cx, cy, l.radius * this.scale, -l.end_angle, -l.start_angle, false); ctx.stroke();   // its swing
    }
  }

  /** A room in plan: a light tint inside its outline and, at its centre, its name, number and the area the outline gives. */
  private paintRoom(w: PlanWall, trace: () => void, color: string, selected: boolean, k: Colors): void {
    const ctx = this.ctx;
    trace(); ctx.setLineDash([2, 4]); ctx.lineWidth = selected ? 2 : 1; ctx.fillStyle = withAlpha(color, selected ? 0.16 : 0.05); ctx.fill();
    ctx.strokeStyle = selected ? color : withAlpha(k.muted, 0.9); ctx.stroke(); ctx.setLineDash([]);
    const label = w.label as RoomLabel | undefined;
    if (!label || !("number" in label)) return;
    const [x, y] = this.toScreen(label.at);
    const font = getComputedStyle(document.documentElement).getPropertyValue("--font-ui") || "sans-serif";
    ctx.fillStyle = selected ? color : k.ink; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.font = `bold 13px ${font}`; ctx.fillText(label.name, x, y - 9);
    if (label.valid === false || label.area_m2 === null || !Number.isFinite(label.area_m2)) {
      ctx.fillStyle = selected ? color : k.accent;
      ctx.font = `bold 12px ${font}`;
      ctx.fillText(`${label.number} · Check room boundary`, x, y + 8);
    } else {
      ctx.font = `12px ${font}`; ctx.fillText(`${label.number} · ${label.area_m2.toLocaleString(undefined, { maximumFractionDigits: 2 })} m²`, x, y + 8);
      if (label.volume_m3 !== undefined && label.volume_m3 !== null && Number.isFinite(label.volume_m3))
        ctx.fillText(`${label.volume_m3.toLocaleString(undefined, { maximumFractionDigits: 2 })} m³`, x, y + 22);
    }
    ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
  }

  /** A stair in plan: its outline, a line across every tread, and an arrow along its run labelled UP (the direction of climbing). */
  private paintStair(w: PlanWall, trace: () => void, color: string, selected: boolean, k: Colors): void {
    const ctx = this.ctx;
    trace(); ctx.setLineDash([]); ctx.lineWidth = selected ? 2.5 : 1.5; ctx.fillStyle = withAlpha(color, selected ? 0.16 : 0.05); ctx.fill(); ctx.stroke();
    ctx.lineWidth = 1; ctx.beginPath();
    for (const [a, b] of w.lines ?? []) this.line(a, b);
    ctx.stroke();
    const arrow = w.arrow;
    if (!arrow) return;
    const [fx, fy] = this.toScreen(arrow.from), [tx, ty] = this.toScreen(arrow.to);
    const angle = Math.atan2(ty - fy, tx - fx), head = 9;
    ctx.lineWidth = 1.4; ctx.beginPath(); ctx.moveTo(fx, fy); ctx.lineTo(tx, ty);
    ctx.moveTo(tx - head * Math.cos(angle - 0.45), ty - head * Math.sin(angle - 0.45)); ctx.lineTo(tx, ty); ctx.lineTo(tx - head * Math.cos(angle + 0.45), ty - head * Math.sin(angle + 0.45)); ctx.stroke();
    ctx.fillStyle = selected ? color : k.ink; ctx.font = `bold 11px ${getComputedStyle(document.documentElement).getPropertyValue("--font-ui") || "sans-serif"}`;
    ctx.textAlign = "center"; ctx.textBaseline = "bottom"; ctx.fillText(arrow.label, (fx + tx) / 2, (fy + ty) / 2 - 4); ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
  }

  /** A grid line: a long dash-dot line with a lettered bubble at its start. (Named apart from the background grid.) */
  private paintGrid2(w: PlanWall, color: string, selected: boolean, k: Colors): void {
    const ctx = this.ctx;
    const [a, b] = w.polygon;
    if (!a || !b) return;
    ctx.setLineDash([16, 4, 2, 4]); ctx.lineWidth = selected ? 2 : 1.2; ctx.beginPath(); this.line(a, b); ctx.stroke(); ctx.setLineDash([]);
    const label = w.label as GridLabel | undefined;
    if (!label || !("radius" in label)) return;
    const r = Math.max(10, label.radius * this.scale);
    for (const end of label.bubbles ?? ["start"]) {
      const [from, to] = end === "start" ? [a, b] : [b, a];
      const dx = from[0] - to[0], dy = from[1] - to[1], len = Math.hypot(dx, dy) || 1;
      const [bx, by] = this.toScreen(from);
      const cx = bx + (dx / len) * r, cy = by - (dy / len) * r;                    // beyond the end, away from the line (screen y points down)
      ctx.fillStyle = k.paper; ctx.lineWidth = selected ? 2 : 1.2; ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      ctx.fillStyle = selected ? color : k.ink; ctx.font = `bold ${Math.round(r * 1.05)}px ${getComputedStyle(document.documentElement).getPropertyValue("--font-ui") || "sans-serif"}`;
      ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText(label.name, cx, cy + 0.5); ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
    }
  }

  /** A model wall as seen in plan: its footprint, filled lightly so it reads as solid and is clearly the model's, not a drawn line. */
  private paintWall(poly: Pt[], color: string, selected: boolean): void {
    const ctx = this.ctx;
    ctx.beginPath();
    poly.forEach((p, i) => { const [x, y] = this.toScreen(p); if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); });
    ctx.closePath();
    ctx.setLineDash([]); ctx.lineWidth = selected ? 2.5 : 1.5; ctx.strokeStyle = color;
    ctx.fillStyle = withAlpha(color, selected ? 0.35 : 0.22);
    ctx.fill(); ctx.stroke();
  }

  private line(a: Pt, b: Pt): void {
    const [x1, y1] = this.toScreen(a), [x2, y2] = this.toScreen(b);
    this.ctx.moveTo(x1, y1); this.ctx.lineTo(x2, y2);
  }

  private paintEntity(e: Entity, color: string, width: number): void {
    const ctx = this.ctx;
    ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = width; ctx.setLineDash([]); ctx.lineJoin = "round"; ctx.lineCap = "round";
    const g = e.geometry;
    if (e.kind === "circle" && Array.isArray(g["center"]) && typeof g["radius"] === "number") {
      const [cx, cy] = this.toScreen(g["center"] as Pt);
      ctx.beginPath(); ctx.arc(cx, cy, g["radius"] * this.scale, 0, Math.PI * 2); ctx.stroke();
      return;
    }
    if (e.kind === "arc" && Array.isArray(g["center"]) && typeof g["radius"] === "number" && typeof g["start_angle"] === "number" && typeof g["end_angle"] === "number") {
      const [cx, cy] = this.toScreen(g["center"] as Pt);
      let end = g["end_angle"];
      while (end < g["start_angle"]) end += Math.PI * 2;
      ctx.beginPath(); ctx.arc(cx, cy, g["radius"] * this.scale, -end, -g["start_angle"], false); ctx.stroke();
      return;
    }
    if (e.kind === "hatch") { this.paintHatch(e, color, width); return; }
    const box = textBox(e);
    if (box) {
      const px = box.height * this.scale;
      if (px < 3) return;
      const [x, y] = this.toScreen(box.origin);
      ctx.font = `${px}px ${getComputedStyle(document.documentElement).getPropertyValue("--font-ui") || "sans-serif"}`;
      ctx.textBaseline = "alphabetic"; ctx.textAlign = "left";
      ctx.fillText(String(g["text"] ?? ""), x, y);
      return;
    }
    ctx.beginPath();
    for (const [a, b] of segments(e)) this.line(a, b);
    ctx.stroke();
    if (e.kind === "dimension" && e.text) this.paintDimensionText(e, color);
  }

  /** A hatch: a light solid fill, or its strokes (computed by the server) in a thin line. */
  private paintHatch(e: Entity, color: string, width: number): void {
    const ctx = this.ctx;
    if (e.geometry["pattern"] === "solid") {
      ctx.beginPath();
      for (const loop of hatchLoops(e)) {
        loop.forEach((p, i) => { const [x, y] = this.toScreen(p); if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); });
        ctx.closePath();
      }
      ctx.fillStyle = withAlpha(color, 0.3); ctx.fill("evenodd");
      return;
    }
    ctx.lineWidth = Math.min(width, 1); ctx.beginPath();
    for (const s of e.strokes ?? []) { const [a, b] = s; if (a && b) this.line(a, b); }
    ctx.stroke();
  }

  /** Square handles on the selected entity, and the entity as it would be if the grip being dragged were dropped now. */
  private paintGrips(k: Colors): void {
    const handles = this.currentGrips();
    if (!handles) return;
    const ctx = this.ctx;
    if (this.grip?.moved) {
      const e = this.entities().find((en) => en.id === this.grip?.id);
      if (e) {
        const g = (this.grip.list[this.grip.index] as Grip).geometry(this.grip.now);
        ctx.save(); ctx.setLineDash([6, 4]);
        this.paintEntity({ ...e, geometry: { ...e.geometry, ...g } }, k.select, 1.5);
        ctx.restore();
      }
    }
    ctx.setLineDash([]); ctx.lineWidth = 1.5; ctx.strokeStyle = k.select; ctx.fillStyle = k.paper;
    handles.list.forEach((gr, i) => {
      const [x, y] = this.toScreen(this.grip && this.grip.index === i && this.grip.moved ? this.grip.now : gr.at);
      ctx.beginPath(); ctx.rect(x - 4.5, y - 4.5, 9, 9); ctx.fill(); ctx.stroke();
    });
  }

  private paintDimensionText(e: Entity, color: string): void {
    const g = e.geometry;
    const p1 = g["p1"], p2 = g["p2"], at = g["dimline_point"];
    if (!Array.isArray(p1) || !Array.isArray(p2) || !Array.isArray(at)) return;
    const a = p1 as Pt, b = p2 as Pt;
    const dx = b[0] - a[0], dy = b[1] - a[1], len = Math.hypot(dx, dy) || 1;
    const nx = -dy / len, ny = dx / len;
    const off = (at[0] - a[0]) * nx + (at[1] - a[1]) * ny;                       // signed distance of the dimension line from the measured points
    const m: Pt = [mid(a, b)[0] + nx * off, mid(a, b)[1] + ny * off];
    const [sx, sy] = this.toScreen(m);
    const ctx = this.ctx;
    ctx.save();
    ctx.translate(sx, sy);
    let angle = -Math.atan2(dy, dx);
    if (angle > Math.PI / 2 || angle < -Math.PI / 2) angle += Math.PI;           // keep the figure readable, never upside down
    ctx.rotate(angle);
    ctx.fillStyle = color; ctx.font = `12px ${getComputedStyle(document.documentElement).getPropertyValue("--font-ui") || "sans-serif"}`;
    ctx.textAlign = "center"; ctx.textBaseline = "bottom";
    ctx.fillText(formatDimension(e.text ?? ""), 0, -3);
    ctx.restore();
  }

  /** The rubber band for the active tool: what the next click would create. */
  private paintPreview(k: Colors): void {
    const ctx = this.ctx;
    const tool = this.store.tool;
    const p = this.cursor;
    if (!p || this.pts.length === 0 || tool === "select") return;
    ctx.strokeStyle = k.select; ctx.fillStyle = k.select; ctx.lineWidth = 1.5; ctx.setLineDash([6, 4]);
    ctx.beginPath();
    const first = this.pts[0] as Pt;
    if (tool === "line" || tool === "polyline" || tool === "floor" || tool === "roof" || tool === "room" || tool === "section" || tool === "grid" || tool === "stair" || tool === "wall" || tool === "extrude" || tool === "void" || tool === "sweep") {
      const all = [...this.pts, p, ...((tool === "floor" || tool === "roof" || tool === "room" || tool === "extrude" || tool === "void") && this.pts.length > 1 ? [first] : [])];
      for (let i = 0; i + 1 < all.length; i++) this.line(all[i] as Pt, all[i + 1] as Pt);
      const from = this.pts[this.pts.length - 1] as Pt;
      if (tool === "wall" && dist(from, p) > 1e-9) { this.tracePolygon(wallBand(from, p, this.store.wallOptions.thickness / this.mmPerUnit())); ctx.fillStyle = withAlpha(k.select, 0.14); ctx.fill(); }
    } else if (tool === "rotate") {
      this.line(first, this.pts.length > 1 ? (this.pts[1] as Pt) : p);
      if (this.pts.length > 1) {
        this.line(first, p);
        const angle = angleBetween(first, this.pts[1] as Pt, p);
        ctx.stroke(); ctx.beginPath(); ctx.fillStyle = withAlpha(k.select, 0.14);
        for (const w of this.data?.model ?? []) if (this.turnable().includes(w.id)) this.tracePolygon(w.polygon.map((q) => rotateAbout(q, first, angle)));
        ctx.fill();
      }
    } else if (tool === "rectangle") {
      const r = normaliseRect(first, p);
      const [x, y] = this.toScreen([r.corner[0], r.corner[1] + r.height]);
      ctx.rect(x, y, r.width * this.scale, r.height * this.scale);
    } else if (tool === "circle") {
      const [cx, cy] = this.toScreen(first);
      ctx.arc(cx, cy, dist(first, p) * this.scale, 0, Math.PI * 2);
      this.line(first, p);
    } else if (tool === "mirror") {
      this.line(first, p);
    } else if (tool === "dimension") {
      if (this.pts.length === 1) this.line(first, p);
      else { const b = this.pts[1] as Pt; this.line(first, b); this.line(first, p); this.line(b, p); }
    }
    ctx.stroke();
    if (tool === "dimension") {
      // Temporary construction line: yellow is reserved for the active
      // measurement guide; it is not a persistent SDRW entity.
      ctx.save();
      ctx.beginPath();
      ctx.strokeStyle = "#FFD400"; ctx.lineWidth = 2; ctx.setLineDash([8, 4]);
      if (this.pts.length === 1) this.line(first, p);
      else {
        const b = this.pts[1] as Pt;
        const dx = b[0] - first[0], dy = b[1] - first[1];
        const len = Math.hypot(dx, dy);
        if (len > 1e-9) {
          const nx = -dy / len, ny = dx / len;
          const offset = (p[0] - first[0]) * nx + (p[1] - first[1]) * ny;
          this.line([first[0] + nx * offset, first[1] + ny * offset],
                    [b[0] + nx * offset, b[1] + ny * offset]);
        }
      }
      ctx.stroke(); ctx.restore();
    }
    const readout = this.previewReadout(first, p);
    if (readout) {
      const [sx, sy] = this.toScreen(p);
      ctx.setLineDash([]); ctx.font = `12px ${getComputedStyle(document.documentElement).getPropertyValue("--font-ui") || "sans-serif"}`;
      const w = ctx.measureText(readout).width + 10;
      ctx.fillStyle = k.paper; ctx.fillRect(sx + 14, sy + 14, w, 20);
      ctx.strokeStyle = k.select; ctx.strokeRect(sx + 14.5, sy + 14.5, w, 20);
      ctx.fillStyle = k.ink; ctx.textBaseline = "middle"; ctx.textAlign = "left"; ctx.fillText(readout, sx + 19, sy + 24.5);
    }
  }

  private previewReadout(first: Pt, p: Pt): string {
    const u = this.store.unit;
    const f = (n: number): string => `${n.toLocaleString(undefined, { maximumFractionDigits: 1 })} ${u}`;
    switch (this.store.tool) {
      case "rotate": return this.pts.length > 1 ? `${((angleBetween(first, this.pts[1] as Pt, p) * 180) / Math.PI).toFixed(1)}°` : "";
      case "line": case "polyline": case "floor": case "roof": case "room": case "grid": case "stair": case "wall": case "extrude": case "void": case "sweep": {
        const from = this.pts[this.pts.length - 1] as Pt;
        const deg = (Math.atan2(p[1] - from[1], p[0] - from[0]) * 180) / Math.PI;
        return `${f(dist(from, p))}  ${deg.toFixed(1)}°`;
      }
      case "rectangle": { const r = normaliseRect(first, p); return `${f(r.width)} × ${f(r.height)}`; }
      case "circle": return `R ${f(dist(first, p))}`;
      case "dimension": return this.pts.length === 1 ? f(dist(first, p)) : f(dist(this.pts[0] as Pt, this.pts[1] as Pt));
      default: return "";
    }
  }

  private paintMoveGhost(k: Colors): void {
    if (!this.drag) return;
    const dx = this.drag.now[0] - this.drag.start[0], dy = this.drag.now[1] - this.drag.start[1];
    const ctx = this.ctx;
    ctx.save();
    ctx.translate(dx * this.scale, -dy * this.scale);
    ctx.globalAlpha = 0.55;
    for (const e of this.entities()) if (this.drag.ids.includes(e.id)) this.paintEntity(e, k.select, 1.5);
    ctx.setLineDash([6, 4]); ctx.strokeStyle = k.select; ctx.fillStyle = withAlpha(k.select, 0.14); ctx.lineWidth = 1.5 / 1;
    for (const w of this.data?.model ?? []) if (this.drag.ids.includes(w.id) && w.category !== "grid") { ctx.beginPath(); this.tracePolygon(w.polygon); ctx.fill(); ctx.stroke(); }
    ctx.restore();
  }

  private paintSnap(snap: Snap, k: Colors): void {
    const [x, y] = this.toScreen(snap.point);
    const ctx = this.ctx;
    ctx.setLineDash([]); ctx.lineWidth = 1.5; ctx.strokeStyle = snap.kind === "grid" ? k.muted : k.accent;
    ctx.beginPath();
    if (snap.kind === "end") ctx.rect(x - 5, y - 5, 10, 10);
    else if (snap.kind === "mid") { ctx.moveTo(x, y - 6); ctx.lineTo(x + 6, y + 5); ctx.lineTo(x - 6, y + 5); ctx.closePath(); }
    else if (snap.kind === "center") ctx.arc(x, y, 5, 0, Math.PI * 2);
    else if (snap.kind === "intersection") { ctx.moveTo(x - 5, y - 5); ctx.lineTo(x + 5, y + 5); ctx.moveTo(x + 5, y - 5); ctx.lineTo(x - 5, y + 5); }
    else { ctx.moveTo(x - 4, y); ctx.lineTo(x + 4, y); ctx.moveTo(x, y - 4); ctx.lineTo(x, y + 4); }
    ctx.stroke();
  }

  private updateHint(): void {
    const n = this.pts.length;
    const hints: Record<Tool, string> = {
      select: this.data?.projection ? "Click an element to select it. Sections and elevations are drawn from the model: change the model to change them." : "Click to select, drag a window to select several, drag an element to move it. Drag the square grips to stretch a wall or a section line, the round one to rotate. Click a wall's length to type a new one. Double-click a section line to open that section.",
      line: n ? "Pick the next point. Shift keeps it horizontal or vertical. Esc ends the chain." : "Pick the first point of the line.",
      polyline: n ? "Pick the next point. Enter or double-click finishes, C closes it, Esc cancels." : "Pick the first point of the polyline.",
      rectangle: n ? "Pick the opposite corner." : "Pick the first corner.",
      circle: n ? "Pick a point on the circle." : "Pick the centre.",
      dimension: n === 0 ? "Pick the first point to measure." : n === 1 ? "Pick the second point." : "Move the yellow guide to position the dimension. Enter confirms; Backspace removes a point; Esc cancels.",
      text: "Click where the text starts.",
      wall: n ? "Pick the next corner of the wall chain. Shift keeps it straight. Double-click, right-click or Esc ends." : "Pick the start of the wall. Walls snap to the ends and middles of other walls.",
      rotate: !this.turnable().length ? "Select what to rotate first, then choose Rotate." : n === 0 ? "Pick the centre of rotation." : n === 1 ? "Pick a point to start the turn from." : "Pick where that direction should end up.",
      offset: this.target ? "Click the side to copy it to." : "Click a line, polyline, rectangle or circle, then enter the distance.",
      trim: "Click the part of a line or polyline to remove. It is cut back to the nearest crossing curves.",
      extend: "Click near the end of a line or open polyline to run it on to the next curve.",
      fillet: this.target ? "Click the second line (on the side to keep)." : "Click the first line near the end to keep. Radius 0 makes a sharp corner.",
      mirror: this.selectedIds().length ? (n ? "Pick the second point of the mirror line." : "Pick the first point of the mirror line.") : "Select what to mirror first, then choose Mirror.",
      hatch: "Click inside a closed shape (rectangle, closed polyline or circle) to hatch it.",
      door: "Click on a wall to place a door. Change its size in Properties.",
      window: "Click on a wall to place a window. Change its size and sill in Properties.",
      grid: n ? "Pick the other end of the grid line." : "Pick the first end of the grid line. Grids show on every floor plan.",
      stair: n ? "Pick the end of the run. The stair climbs from this level to the next one up." : "Pick where the stair starts. It runs from this level to the next one up.",
      family: this.store.family ? `Click to place “${this.store.family.name}”. Esc stops placing.` : "Choose a family first (Insert, then Family).",
      tag: this.tagOf ? "Click where the tag goes." : "Click the element to tag. The tag shows its data and follows it when the model changes.",
      floor: n ? "Pick the next corner. Enter or double-click finishes, Esc cancels." : "Pick the first corner of the floor.",
      room: n ? "Pick the next corner of the room. Enter or double-click finishes, Esc cancels." : "Pick the first corner of the room, along the walls.",
      column: "Click where the column goes. Change its size and height in Properties.",
      extrude: n ? "Pick the next corner. Enter or double-click finishes, Esc cancels." : "Pick the first corner of the outline to extrude. Set its depth and base above.",
      void: n ? "Pick the next corner of the cut. Enter or double-click finishes, Esc cancels." : "Pick the first corner of the void. It cuts what it passes through (see Properties).",
      sweep: n ? "Pick the next point of the path. Enter or double-click finishes, C closes it, Esc cancels." : "Pick the first point of the path the profile follows.",
      roof: n ? "Pick the next roof corner. Enter or double-click finishes, Esc cancels." : "Pick the first roof corner. Roof type, pitch, and thickness are above the drawing.", beam: "",
      section: n ? "Pick the end of the section line. It looks to the left of the line you draw (flip it in Properties)." : "Pick the start of the section line.",
    };
    this.hint.textContent = hints[this.store.tool];
  }

  /** Public for tests of the view's geometry: where a world point lands on screen. */
  screenOf(p: Pt): Pt { return this.toScreen(p); }
  /** Where the handles of the selection are on screen, and the temporary length label (read by the browser tests). */
  handleScreens(): Array<{ shape: string; at: Pt }> { return this.handles().map((hd) => ({ shape: hd.shape, at: this.toScreen(hd.at) })); }
  lengthLabelScreen(): Pt | null { const b = this.lengthBox; return b ? [b.x + b.w / 2, b.y + b.h / 2] : null; }
  /** The bounds of what is drawn, in world units. */
  contentBounds(): [Pt, Pt] | null {
    const all = this.entities().map(entityBounds).filter((b): b is [Pt, Pt] => !!b);
    if (!all.length) return null;
    return [[Math.min(...all.map((b) => b[0][0])), Math.min(...all.map((b) => b[0][1]))], [Math.max(...all.map((b) => b[1][0])), Math.max(...all.map((b) => b[1][1]))]];
  }
}
