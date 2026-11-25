/** The Profiler workspace: a 2D drafting editor in a pop-up window (about 75 % of the screen) opened from Plug-ins → Profiler. The window can be
 *  minimised to a small bar, maximised to the whole screen, restored, moved and resized, and it does not block SPADRA behind it, so a profile can be uploaded
 *  while the person looks at the model. It has its own six ribbons (File, Edit, Insert, Draw,
 *  Modifications, Analyze), a viewport, side panels, a command line and a status bar. It never changes the project until the person uploads a profile,
 *  and closing it leaves the project exactly as it was. */
import { h, replace } from "../../../tools/web/src/dom.js";
import { glyph } from "../../../tools/web/src/icons.js";
import { ApiError } from "../../../tools/web/src/api.js";
import { confirmDialog, openDialog, promptDialog, toast } from "../../../tools/web/src/ui.js";
import { openMenu, type MenuEntry } from "./menus.js";
import { oneLine, present, presentUnexpected } from "./errors.js";
import { agentsDialog } from "./agents_ui.js";
import { ProfilerApi, type PfCheck, type PfInfo, type PfOpResult, type PfRegion, type PfView, type Pt } from "./profiler_api.js";
import { ProfilerCanvas, type Filters, type Hit } from "./profiler_canvas.js";
import { checkPanel, checkSummary, constraintsPanel, editDimension, layersPanel, parametersPanel, propertiesPanel, PURPOSES, type PanelHost } from "./profiler_panels.js";
import { DRAW_TOOLS, formDialog, MODIFY_TOOLS, ToolRun, type Build, type Field, type RunHost, type Sel, type ToolDef } from "./profiler_tools.js";
import { store } from "./store.js";
import { formatGridSize } from "./geom2d.js";
import { clampGeometry as clampTo, defaultGeometry as defaultTo, toggledState, type Geometry, type WindowState } from "./profiler_window.js";

const RIBBONS = ["File", "Edit", "Insert", "Draw", "Modifications", "Analyze"] as const;
type RibbonName = (typeof RIBBONS)[number];
interface Btn { label: string; icon: string; run: (anchor: HTMLElement) => void | Promise<void>; enabled?: boolean; note?: string; tool?: string; pressed?: boolean }
type Group = Btn[];

const DIMENSIONS: Array<[string, string, number]> = [["horizontal", "Horizontal", 2], ["vertical", "Vertical", 2], ["aligned", "Aligned", 2], ["length", "Length of a line", 1], ["radius", "Radius", 1], ["diameter", "Diameter", 1],
  ["angular", "Angle between lines", 2], ["arc_length", "Arc length", 1], ["chord", "Chord", 1], ["spline_length", "Spline length", 1], ["center_distance", "Centre to centre", 2], ["min_distance", "Shortest distance", 2],
  ["perpendicular", "Point to line", 2], ["slope", "Slope", 1], ["ordinate", "Ordinate", 1], ["area", "Area of selection", 0], ["perimeter", "Perimeter of selection", 0], ["bounding", "Overall size of selection", 0]];
const CONSTRAINTS: Array<[string, string, number]> = [["horizontal", "Horizontal", 1], ["vertical", "Vertical", 1], ["parallel", "Parallel", 2], ["perpendicular", "Perpendicular", 2], ["collinear", "Collinear", 2], ["equal", "Equal length or radius", 2],
  ["tangent", "Tangent", 2], ["concentric", "Concentric", 2], ["coincident", "Coincident points", 2], ["point_on", "Point on line", 2], ["midpoint", "Midpoint", 2]];
const SNAPS = ["endpoint", "midpoint", "center", "intersection", "tangent", "perpendicular", "nearest", "quadrant", "grid"];
const ALIASES: Record<string, string> = { l: "line", line: "line", pl: "polyline", polyline: "polyline", rec: "rectangle", rect: "rectangle", rectangle: "rectangle", c: "circle", circle: "circle", el: "ellipse", ellipse: "ellipse",
  pol: "polygon", polygon: "polygon", slot: "slot", a: "arc3", arc: "arc3", spl: "spline_revit", spline: "spline_revit", f: "fillet", fillet: "fillet", cha: "chamfer", chamfer: "chamfer", cha2: "chamfer2", m: "move", move: "move",
  co: "copy", copy: "copy", ro: "rotate", rotate: "rotate", sc: "scale", scale: "scale", mi: "mirror", mirror: "mirror", tr: "trim", trim: "trim", ex: "extend", extend: "extend", o: "offset", offset: "offset", sp: "split", split: "split" };

const fmt = (n: number): string => String(+n.toPrecision(10));

const viewport = (): { w: number; h: number } => ({ w: window.innerWidth, h: window.innerHeight });
const defaultGeometry = (): Geometry => defaultTo(viewport());
const clampGeometry = (g: Geometry): Geometry => clampTo(g, viewport());
function readGeometry(el: HTMLElement): Geometry | null { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 ? { x: r.left, y: r.top, w: r.width, h: r.height } : null; }
const errorOf = (e: unknown): { message: string; status: number; details: unknown } => e instanceof ApiError ? { message: oneLine(present(e, store.boot?.errors)), status: e.status, details: e.details } : { message: oneLine(presentUnexpected(e, "Profiler")), status: 0, details: null };

/** The browser's own file chooser; null when the person cancels. */
const pickFile = (accept: string): Promise<File | null> => new Promise((resolve) => {
  const input = document.createElement("input"); input.type = "file"; input.accept = accept; input.style.display = "none";
  input.addEventListener("change", () => { resolve(input.files?.[0] ?? null); input.remove(); }); input.addEventListener("cancel", () => { resolve(null); input.remove(); });
  document.body.append(input); input.click();
});

export class ProfilerWorkspace {
  static current: ProfilerWorkspace | null = null;
  private static starting = false;                                   // the start dialog is open: a second click on Profiler must not open a second workspace
  /** How the window is shown: its normal pop-up size, the whole screen, or a small bar at the bottom of the screen (everything stays alive while minimised). */
  private state: WindowState = "normal";
  private geom: Geometry | null = null;                              // position and size of the normal window, kept while it is maximised or minimised
  private readonly minBtn = h("button", { class: "iconbtn", type: "button", attrs: { "aria-label": "Minimize Profiler", title: "Minimize: keep Profiler open as a small bar" }, on: { click: () => this.setState("min") } }, glyph("pf-minimize"));
  private readonly restoreBtn = h("button", { class: "iconbtn", type: "button", attrs: { "aria-label": "Restore Profiler", title: "Restore the window to its pop-up size" }, on: { click: () => this.setState("normal") } }, glyph("pf-restore"));
  private readonly maxBtn = h("button", { class: "iconbtn", type: "button", attrs: { "aria-label": "Maximize Profiler", title: "Maximize: fill the whole screen" }, on: { click: () => this.setState("max") } }, glyph("pf-maximize"));
  private readonly api = new ProfilerApi(store.api);
  private readonly canvas = new ProfilerCanvas();
  private readonly root = h("div", { class: "pf-root", attrs: { role: "dialog", "aria-modal": "false", "aria-label": "Profiler", "data-state": "normal" } });
  private readonly ribbonEl = h("div", { class: "pf-ribbon" });
  private readonly tabsEl = h("div", { class: "pf-tabs", attrs: { role: "tablist", "aria-label": "Profiler ribbons" } });
  private readonly leftEl = h("div", { class: "pf-side left" });
  private readonly rightEl = h("div", { class: "pf-side right" });
  private readonly titleEl = h("div", { class: "pf-title" });
  private readonly msgEl = h("div", { class: "pf-msg", attrs: { role: "status", "aria-live": "polite" } });
  private readonly cmd = h("input", { class: "pf-cmd", type: "text", placeholder: "Command or point: x,y  @dx,dy  distance<angle", attrs: { "aria-label": "Command line", autocomplete: "off", spellcheck: "false" } });
  private readonly statusEl = h("div", { class: "pf-status-bar" });
  private view!: PfView; private sid = ""; private ribbon: RibbonName = "Draw"; private run: ToolRun | null = null; private toolId = "";
  private leftTab: "layers" | "params" = "layers"; private rightTab: "props" | "cons" | "check" = "props";
  private layer = "0"; private checkResult: PfCheck | null = null; private checkPurpose = "drafting"; private regions: PfRegion[] = [];
  private snapModes = new Set(SNAPS); private coords = ""; private autosaveMinutes = 1; private timer = 0; private closed = false; private lastPoint: Pt | null = null;
  private info!: PfInfo; private keyHandler = (e: KeyboardEvent): void => this.keydown(e); private unloadHandler = (e: BeforeUnloadEvent): void => { if (this.view?.status.dirty) e.preventDefault(); };
  private opener: HTMLElement | null = document.activeElement instanceof HTMLElement ? document.activeElement : null;

  // ---------------------------------------------------------------------------------------------------------- start
  static async launch(): Promise<void> {
    const open = ProfilerWorkspace.current;
    if (open) { open.bringBack(); return; }                          // already open (maybe minimised): show it again instead of starting another one
    if (ProfilerWorkspace.starting) return;
    ProfilerWorkspace.starting = true;
    try {
      const ws = new ProfilerWorkspace();
      if (await ws.start()) { ProfilerWorkspace.current = ws; ws.mount(); }
    } finally { ProfilerWorkspace.starting = false; }
  }
  private async call<T>(work: () => Promise<T>, quiet = false): Promise<T | null> {
    try { return await work(); } catch (error) { const e = errorOf(error); if (!quiet) { toast(e.message, { kind: "error" }); this.say(e.message); } return null; }
  }
  private async start(): Promise<boolean> {
    const info = await this.call(() => this.api.info()); if (!info) return false;
    this.info = info;
    const choices: Array<[string, string]> = [["new", "A new profile"], ...info.profiles.map((f): [string, string] => [`file:${f.path}`, `Open “${f.name}”`]), ["browse", "Open a file from your computer…"], ...info.open.map((s): [string, string] => [`session:${s.id}`, `Continue “${s.name}”${s.dirty ? " (unsaved changes)" : ""}`]),
      ...info.recoverable.filter((r) => !r.damaged).map((r): [string, string] => [`recover:${r.path}`, `Recover “${r.name ?? "profile"}” (autosaved${r.saved_at ? " " + r.saved_at : ""})`])];
    const f = await formDialog("Profiler", [
      { key: "what", label: "Start with", kind: "select", value: "new", options: choices },
      { key: "template", label: "Template", kind: "select", value: "rectangle", options: info.templates.map((t): [string, string] => [t.id, t.label]), show: (v) => v["what"] === "new" },
      { key: "name", label: "Name", kind: "text", value: "Profile", show: (v) => v["what"] === "new" },
      { key: "unit", label: "Unit", kind: "select", value: info.project.unit ?? "mm", options: info.units.map((u): [string, string] => [u, u]), show: (v) => v["what"] === "new" },
      ], "Start",
      info.project.open ? undefined : "No project is open. You can draw and save profiles now; uploading to the model needs an open project.");
    if (!f) return false;
    const what = String(f["what"]); let view: PfView | null = null;
    if (what === "new") view = await this.call(() => this.api.create({ template: String(f["template"]), name: String(f["name"] || "Profile"), unit: String(f["unit"]) }));
    else if (what.startsWith("file:")) { const o = await this.call(() => this.api.open({ path: what.slice(5) })); if (o) { view = o; this.warnOpen(o.report.warnings); } }
    else if (what === "browse") { const file = await pickFile(".sdrw"); if (!file) return false; const o = await this.call(() => this.api.openUpload(file)); if (o) { view = o; this.warnOpen(o.report.warnings); } }
    else if (what.startsWith("session:")) view = await this.call(() => this.api.state(what.slice(8)));
    else if (what.startsWith("recover:")) { const o = await this.call(() => this.api.open({ recover: what.slice(8) })); if (o) { view = o; toast("Recovered the autosaved copy. Save it to keep it.", { kind: "info" }); } }
    if (!view) return false;
    this.view = view; this.sid = view.status.id; return true;
  }
  private warnOpen(w: string[]): void { for (const m of w) toast(m, { kind: "info", timeoutMs: 9000 }); }

  private mount(): void {
    const icon = h("span", { class: "pf-icon", attrs: { "aria-hidden": "true" } }, glyph("pf-profiler"));
    const header = h("header", { class: "pf-header" }, icon, this.titleEl, h("div", { class: "grow" }),
      h("button", { class: "btn pf-full", type: "button", on: { click: () => { void this.switchProfile(); } } }, glyph("list"), "Profiles"),
      h("button", { class: "btn", type: "button", attrs: { title: "Send the profile to the model and keep Profiler open" }, on: { click: () => { void this.upload(false); } } }, glyph("upload"), "Upload"),
      h("button", { class: "btn primary pf-full", type: "button", attrs: { title: "Send the profile to the model, then close Profiler" }, on: { click: () => { void this.upload(true); } } }, glyph("upload"), "Upload and close"),
      h("div", { class: "pf-winctl", attrs: { role: "group", "aria-label": "Window" } }, this.minBtn, this.restoreBtn, this.maxBtn,
        h("button", { class: "iconbtn", type: "button", attrs: { "aria-label": "Close Profiler", title: "Close Profiler" }, on: { click: () => { void this.closeWorkspace(); } } }, glyph("close"))));
    this.headerEl = header;
    header.addEventListener("pointerdown", (e) => this.dragStart(e));
    header.addEventListener("dblclick", (e) => { if (!(e.target as HTMLElement).closest("button")) this.setState(toggledState(this.state)); });
    const side = (el: HTMLElement): HTMLElement => el;
    const main = h("div", { class: "pf-main" }, side(this.leftEl), h("div", { class: "pf-stage" }, this.canvas.el, this.msgEl), side(this.rightEl));
    const cmdbar = h("div", { class: "pf-cmdbar" }, glyph("pf-measure"), this.cmd);
    this.root.append(header, this.tabsEl, this.ribbonEl, main, cmdbar, this.statusEl);
    document.body.append(this.root);
    this.geom = defaultGeometry();
    this.applyState();
    this.sizeWatch = new ResizeObserver(() => this.noteSize()); this.sizeWatch.observe(this.root);
    window.addEventListener("resize", this.fitToScreen);
    this.canvas.hooks = {
      snap: async (raw, from) => { const r = await this.call(() => this.api.op<{ snap: { mode: string; point: Pt } | null }>(this.sid, "snap", { cursor: raw, radius: 12 / Math.max(this.canvas.zoomPercent, 1e-9), grid: this.canvas.gridSpec().snap, ...(from ? { from } : {}), modes: [...this.snapModes] }), true); return r?.result.snap ?? null; },
      window: async (rect, mode, filters) => { const r = await this.call(() => this.api.op<{ edges: string[]; vertices?: string[]; dimensions?: string[]; comments?: string[] }>(this.sid, "select_window", { rect, mode, filters: this.filterNames(filters) }), true);
        return r ? [...(r.result.edges ?? []), ...(r.result.vertices ?? [])] : []; },
      selectionChanged: () => { this.renderPanels(); this.renderRibbon(); },
      context: (hit, at, x, y) => this.contextMenu(hit, at, x, y),
      open: (hit) => this.openHit(hit),
      grid: () => this.renderStatus(),
      coords: (pt, snap) => { this.coords = pt ? `X ${fmt(+pt[0].toFixed(4))}   Y ${fmt(+pt[1].toFixed(4))}${snap ? `   · ${snap.mode}` : ""}` : ""; this.renderStatus(); },
    };
    this.cmd.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); void this.command(this.cmd.value); this.cmd.value = ""; } else if (e.key === "Escape") { this.cmd.value = ""; this.cancelTool(); this.canvas.el.querySelector("canvas")?.focus(); } e.stopPropagation(); });
    window.addEventListener("keydown", this.keyHandler); window.addEventListener("beforeunload", this.unloadHandler);
    this.canvas.showGrid = store.showGrid; this.canvas.gridSpacingMm = store.gridSpacingMm;
    this.applyView(this.view, true);
    this.setAutosave(this.autosaveMinutes);
    this.canvas.el.querySelector("canvas")?.focus();
    this.say("Ready. Pick a tool in the ribbon, or select objects by clicking or dragging a window (left to right encloses, right to left touches).");
  }

  // -------------------------------------------------------------------------------------------------------- window
  private headerEl: HTMLElement | null = null;
  private sizeWatch: ResizeObserver | null = null;
  private drag: { id: number; dx: number; dy: number } | null = null;

  /** Show the window again if it was minimised, and put the cursor back on the drawing. */
  private bringBack(): void {
    if (this.state === "min") this.setState("normal"); else this.canvas.el.querySelector("canvas")?.focus();
  }
  /** Minimise, maximise or restore. Nothing is rebuilt or reloaded: the drawing, the selection and the running tool stay as they are. */
  private setState(next: WindowState): void {
    if (next === this.state || this.closed) return;
    if (this.state === "normal") this.geom = readGeometry(this.root) ?? this.geom;   // remember where the pop-up was
    this.state = next;
    this.applyState();
    window.requestAnimationFrame(() => {
      if (this.closed) return;
      if (next === "min") (this.restoreBtn.hidden ? this.maxBtn : this.restoreBtn).focus();
      else this.canvas.el.querySelector("canvas")?.focus();
    });
  }
  private applyState(): void {
    const st = this.root.style;
    this.root.dataset["state"] = this.state;
    if (this.state === "normal" && this.geom) { const g = clampGeometry(this.geom); this.geom = g; st.left = `${g.x}px`; st.top = `${g.y}px`; st.width = `${g.w}px`; st.height = `${g.h}px`; }
    else { st.removeProperty("left"); st.removeProperty("top"); st.removeProperty("width"); st.removeProperty("height"); }
    this.minBtn.hidden = this.state === "min";
    this.restoreBtn.hidden = this.state === "normal";
    this.maxBtn.hidden = this.state === "max";
    this.root.setAttribute("aria-label", this.state === "min" ? "Profiler (minimized)" : "Profiler");
  }
  /** The person resized the pop-up with its corner handle: keep that size for the next restore. */
  private noteSize(): void { if (this.state === "normal" && !this.closed) { const g = readGeometry(this.root); if (g) this.geom = g; } }
  private fitToScreen = (): void => { if (this.state === "normal" && this.geom) this.applyState(); };
  private dragStart(e: PointerEvent): void {
    if (this.state !== "normal" || e.button !== 0 || (e.target as HTMLElement).closest("button, input, select")) return;
    const r = this.root.getBoundingClientRect(); const header = this.headerEl; if (!header) return;
    this.drag = { id: e.pointerId, dx: e.clientX - r.left, dy: e.clientY - r.top };
    header.setPointerCapture(e.pointerId);
    const move = (m: PointerEvent): void => { if (!this.drag || m.pointerId !== this.drag.id || !this.geom) return;
      this.geom = clampGeometry({ ...this.geom, x: m.clientX - this.drag.dx, y: m.clientY - this.drag.dy }); this.root.style.left = `${this.geom.x}px`; this.root.style.top = `${this.geom.y}px`; };
    const end = (m: PointerEvent): void => { if (this.drag && m.pointerId === this.drag.id) { this.drag = null; header.removeEventListener("pointermove", move); header.removeEventListener("pointerup", end); header.removeEventListener("pointercancel", end); } };
    header.addEventListener("pointermove", move); header.addEventListener("pointerup", end); header.addEventListener("pointercancel", end);
  }

  // --------------------------------------------------------------------------------------------------------- state
  private applyView(v: Partial<PfView> & { status: PfView["status"] }, fit = false): void {
    this.view = { ...this.view, ...v } as PfView;
    if (v.render) this.canvas.setRender(v.render, !fit);
    this.renderTitle(); this.renderTabs(); this.renderRibbon(); this.renderPanels(); this.renderStatus();
    void this.loadRegions();
  }
  private async loadRegions(): Promise<void> { const r = await this.call(() => this.api.op<{ regions: PfRegion[] }>(this.sid, "regions"), true); if (r) this.regions = r.result.regions; }
  private say(m: string): void { this.msgEl.textContent = m; }
  private renderTitle(): void { const s = this.view.status; replace(this.titleEl, h("strong", { text: s.name }), h("span", { class: `chip${s.dirty ? " warn" : ""}`, text: s.dirty ? "Unsaved" : s.path ? "Saved" : "New" }), h("small", { class: "muted", text: s.path ?? "not saved yet" })); }
  private renderStatus(): void {
    const s = this.view.status, d = this.view.diagnostics;
    const dof = d.status === "full" ? "Fully constrained" : d.status === "under" ? `${d.dof} DoF free` : d.status === "conflicting" ? "Conflicting constraints" : "Invalid";
    const snaps = h("button", { class: "btn tiny", type: "button", attrs: { title: "Choose which snaps are active" }, on: { click: (e) => this.snapMenu(e.currentTarget as HTMLElement) } }, glyph("pin"), `Snaps ${this.snapModes.size}/${SNAPS.length}`);
    const snapOn = this.view.render.view.snap_grid, g = this.canvas.gridSpec(), unit = this.view.render.unit, mm = this.gridUnitMm();
    const size = h("button", { type: "button", class: "grid-size", text: `Grid = ${formatGridSize(g.snap, unit)}`,
      attrs: { title: g.auto ? "Grid spacing follows the zoom (never finer than 0.1 mm). Click to fix it." : g.collapsed ? `Fixed spacing ${formatGridSize((store.gridSpacingMm ?? 0) / mm, unit)} is too fine to aim at this zoom: lines and snapping use ${formatGridSize(g.display, unit)}. Click to change it.` : g.display === g.snap ? "Fixed grid spacing. Click to change it." : `Fixed grid spacing; lines are drawn every ${formatGridSize(g.display, unit)} at this zoom, the pointer snaps to every ${formatGridSize(g.snap, unit)}. Click to change it.`, "aria-label": `Grid size ${formatGridSize(g.snap, unit)}. Change` },
      on: { click: (e) => this.gridMenu(e.currentTarget as HTMLElement) } });
    const showBtn = h("button", { type: "button", class: "snap-toggle grid-show", text: "Grid", attrs: { "aria-pressed": String(store.showGrid), title: "Show or hide the grid lines (the axes stay)" }, on: { click: () => this.setShowGrid(!store.showGrid) } });
    const snapBtn = h("button", { type: "button", class: "snap-toggle", text: "Grid snap", attrs: { "aria-pressed": String(snapOn), title: "Snap the pointer to the grid. Snapping to ends, midpoints and the like is chosen under Snaps." }, on: { click: () => { void this.op("view", { snap_grid: !snapOn }, "View"); } } });
    replace(this.statusEl, h("span", { class: "pf-coords", text: this.coords || "—" }), snaps, size, showBtn, snapBtn, h("span", { class: `pf-dof ${d.status}`, text: dof }),
      h("span", { text: `${s.entities.edges} lines · ${s.entities.vertices} points · ${s.unit}` }), h("span", { class: "grow" }), this.run ? h("span", { class: "pf-active", text: `Tool: ${this.run.def.label}` }) : null,
      h("span", { text: `Autosave ${this.autosaveMinutes ? `every ${this.autosaveMinutes} min` : "off"}` }), h("span", { text: `Layer ${this.layer}` }));
  }
  private gridUnitMm(): number { return ({ mm: 1, cm: 10, m: 1000, in: 25.4, ft: 304.8 } as Record<string, number>)[this.view.render.unit] ?? 1; }
  /** SPADRA's own grid settings, shared with the main window: lines shown or hidden, and a fixed spacing or automatic (null). */
  private syncGrid(): void { this.canvas.showGrid = store.showGrid; this.canvas.gridSpacingMm = store.gridSpacingMm; this.canvas.schedule(); this.renderStatus(); }
  private setShowGrid(on: boolean): void { store.setShowGrid(on); this.syncGrid(); this.say(`Grid ${on ? "shown" : "hidden"}`); }
  private setGridSpacingMm(mm: number | null): void { store.setGridSpacingMm(mm); this.syncGrid(); }
  private gridMenu(anchor: HTMLElement): void {
    const unit = this.view.render.unit, f = this.gridUnitMm();
    const fix = (mm: number): MenuEntry => ({ label: `${formatGridSize(mm / f, unit)}${store.gridSpacingMm === mm ? "  ✓" : ""}`, icon: "grid", run: () => this.setGridSpacingMm(mm) });
    openMenu(anchor, [
      { label: `Automatic (follows the zoom)${store.gridSpacingMm === null ? "  ✓" : ""}`, icon: "grid", run: () => this.setGridSpacingMm(null) },
      "separator",
      ...[0.1, 0.5, 1, 5, 10, 50, 100, 500, 1000].map(fix),
      "separator",
      { label: "Set spacing…", icon: "edit", run: () => { void formDialog("Grid spacing", [{ key: "g", label: `Spacing (${unit})`, kind: "number", value: +((store.gridSpacingMm ?? 10) / f).toPrecision(6), min: 1e-6 }], "Apply").then((r) => { if (r && Number(r["g"]) > 0) this.setGridSpacingMm(Number(r["g"]) * f); }); } },
    ], { label: "Grid spacing" });
  }
  private snapMenu(anchor: HTMLElement): void {
    openMenu(anchor, SNAPS.map((m) => ({ label: `${this.snapModes.has(m) ? "✓ " : "   "}${m}`, run: () => { if (this.snapModes.has(m)) this.snapModes.delete(m); else this.snapModes.add(m); this.renderStatus(); } })));
  }
  private filterNames(f: Filters): string[] { return Object.entries(f).filter(([, on]) => on).map(([k]) => k); }
  private sel(): Sel {
    const r = this.view.render, s = this.canvas.selection;
    const edges = r.edges.filter((e) => s.has(e.id)).map((e) => e.id), vertices = r.vertices.filter((v) => s.has(v.id)).map((v) => v.id);
    return { ids: [...edges, ...vertices], edges, vertices, dims: r.dimensions.filter((d) => s.has(d.id)).map((d) => d.id), comments: r.comments.filter((c) => s.has(c.id)).map((c) => c.id), texts: r.texts.filter((t) => s.has(t.id)).map((t) => t.id) };
  }

  // -------------------------------------------------------------------------------------------------------- running
  private async perform(builds: Build[], label: string): Promise<boolean> {
    let last: (PfOpResult & { result: unknown }) | null = null;
    for (const b of builds) {
      const out = await this.call(() => this.api.op(this.sid, b.op, b.args, true)); if (!out) return false; last = out;
      this.applyOp(out);
      const released = ((out.result as { released?: Array<{ what: string }> } | null)?.released ?? []).filter((r) => r.what !== "added").length;
      if (released) toast(`${label}: ${released} horizontal/vertical or fixed-point setting${released === 1 ? " was" : "s were"} switched off so the shape can turn. They are still listed under Constraints; Undo restores everything.`, { kind: "info", timeoutMs: 9000 });
    }
    if (last) this.say(`${label}: done.`);
    return true;
  }
  private applyOp(out: PfOpResult): void { this.applyView({ status: out.status, ...(out.render ? { render: out.render } : {}), ...(out.params ? { params: out.params } : {}), ...(out.diagnostics ? { diagnostics: out.diagnostics } : {}) }); }
  private async op(op: string, args: Record<string, unknown>, label = op): Promise<boolean> { return this.perform([{ op, args }], label); }
  private async needSelection(what: string): Promise<Sel | null> { const s = this.sel(); if (!s.ids.length) { this.say(`${what}: select something first.`); return null; } return s; }

  private startTool(def: ToolDef): void {
    this.cancelTool(); const host = this.runHost();
    const run = new ToolRun(def, host); this.run = run; this.toolId = def.id; this.canvas.tool = run; this.renderRibbon(); this.renderStatus();
    void run.start();
  }
  private cancelTool(): void { this.run = null; this.toolId = ""; this.canvas.tool = null; this.renderRibbon(); this.renderStatus(); this.canvas.schedule(); }
  private runHost(): RunHost {
    return {
      selection: () => this.sel(), layer: () => this.layer,
      perform: (b, l) => this.perform(b, l), say: (m) => this.say(m),
      isVertex: (id) => this.view.render.vertices.some((v) => v.id === id),
      pickAt: (pt, kind) => { const r = this.view.render, tol = 8 / Math.max(this.canvas.zoomPercent, 1e-9);
        if (kind !== "edge") { let best: string | null = null, bd = tol; for (const v of r.vertices) { const d = Math.hypot(v.x - pt[0], v.y - pt[1]); if (d <= bd) { bd = d; best = v.id; } } if (best || kind === "vertex") return best; }
        let best: string | null = null, bd = tol; for (const e of r.edges) for (let i = 0; i + 1 < e.points.length; i++) { const a = e.points[i]!, b = e.points[i + 1]!, dx = b[0] - a[0], dy = b[1] - a[1], l2 = dx * dx + dy * dy;
          const t = l2 === 0 ? 0 : Math.max(0, Math.min(1, ((pt[0] - a[0]) * dx + (pt[1] - a[1]) * dy) / l2)); const d = Math.hypot(pt[0] - (a[0] + t * dx), pt[1] - (a[1] + t * dy)) + (e.construction ? tol * 0.01 : 0); if (d <= bd) { bd = d; best = e.id; } }
        return best; },
      regionAt: (pt) => { const inRing = (ring: Pt[]): boolean => { let c = false; for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) { const a = ring[i]!, b = ring[j]!; if ((a[1] > pt[1]) !== (b[1] > pt[1]) && pt[0] < ((b[0] - a[0]) * (pt[1] - a[1])) / (b[1] - a[1]) + a[0]) c = !c; } return c; };
        const hits = this.regions.filter((r) => inRing(r.outer.points) && !r.holes.some((hh) => inRing(hh.points))); hits.sort((a, b) => a.area - b.area); return hits[0]?.edges ?? null; },
      askNumber: async (title, label, value, min) => { const f = await formDialog(title, [{ key: "v", label, kind: "number", value, ...(min !== undefined ? { min } : {}) }], "OK"); const n = f ? Number(f["v"]) : NaN; return f && Number.isFinite(n) ? n : null; },
      askText: async (title, label, value) => { const f = await formDialog(title, [{ key: "v", label, kind: "text", value }], "OK"); return f ? String(f["v"]) : null; },
      askChoice: async (title, label, options, value) => { const f = await formDialog(title, [{ key: "v", label, kind: "select", options, ...(value ? { value } : {}) }], "OK"); return f ? String(f["v"]) : null; },
      ended: () => { if (this.run) this.cancelTool(); },
    };
  }

  // ------------------------------------------------------------------------------------------------------ command line
  private async command(text: string): Promise<void> {
    const t = text.trim(); if (!t) { await this.run?.enter(); return; }
    const word = t.toLowerCase();
    if (this.run && (word === "c" || word === "close")) { await this.run.enter(true); return; }
    if (word === "undo" || word === "u") return void this.doUndo();
    if (word === "redo") return void this.doRedo();
    if (word === "fit" || word === "zoom") { this.canvas.fit(); return; }
    if (!this.run && ["d", "dim", "dimension", "measure"].includes(word)) return this.startTool(this.dimAutoTool());
    if (!this.run) { const id = ALIASES[word]; const def = [...DRAW_TOOLS, ...MODIFY_TOOLS].find((d) => d.id === id); if (def) return this.startTool(def); this.say(`“${t}” is not a command. Try: line, rectangle, circle, move, trim … or pick a tool in the ribbon.`); return; }
    const r = await this.call(() => this.api.op<{ point?: Pt; number?: number }>(this.sid, "parse_point", { text: t, ...(this.lastPoint ? { last: this.lastPoint } : {}) }));
    const pt = r?.result.point; if (!pt) return;
    this.lastPoint = pt; await this.run.point(pt);
  }
  private keydown(e: KeyboardEvent): void {
    if (this.closed || this.state === "min" || document.querySelector("dialog[open]")) return;
    if (!(e.target instanceof Node) || !this.root.contains(e.target)) return;   // the window is not modal: keys typed in SPADRA behind it are not for Profiler
    const t = e.target as HTMLElement | null; const typing = !!t && (t.tagName === "INPUT" || t.tagName === "SELECT" || t.tagName === "TEXTAREA");
    if (e.key === "Escape") { if (this.run) this.cancelTool(); else this.canvas.setSelection([]); return; }
    if (typing) return;
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key.toLowerCase() === "z") { e.preventDefault(); void (e.shiftKey ? this.doRedo() : this.doUndo()); }
    else if (mod && e.key.toLowerCase() === "y") { e.preventDefault(); void this.doRedo(); }
    else if (mod && e.key.toLowerCase() === "s") { e.preventDefault(); void this.save(false); }
    else if (mod && e.key.toLowerCase() === "a") { e.preventDefault(); this.canvas.selectAll(); }
    else if (mod && e.key.toLowerCase() === "c") { e.preventDefault(); void this.op("copy_to_clipboard", { ids: this.sel().ids }, "Copy"); }
    else if (mod && e.key.toLowerCase() === "x") { e.preventDefault(); void this.cutSel(); }
    else if (mod && e.key.toLowerCase() === "v") { e.preventDefault(); void this.paste(); }
    else if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); void this.deleteSel(); }
    else if (e.key === "Enter") { e.preventDefault(); if (this.run) void this.run.enter(); else this.cmd.focus(); }
    else if (e.key === "f" || e.key === "F") this.canvas.fit();
    else if ((e.key === "d" || e.key === "D") && !mod && !this.run) { e.preventDefault(); this.startTool(this.dimAutoTool()); }
    else if (e.key.length === 1 && !mod && /[a-z]/i.test(e.key)) { this.cmd.focus(); }
  }

  // ----------------------------------------------------------------------------------------------------------- edit
  private async doUndo(): Promise<void> { const r = await this.call(() => this.api.undo(this.sid)); if (r) { this.applyView(r); this.say(`Undid: ${r.undone}`); } }
  private async doRedo(): Promise<void> { const r = await this.call(() => this.api.redo(this.sid)); if (r) { this.applyView(r); this.say(`Redid: ${r.redone}`); } }
  private async deleteSel(): Promise<void> {
    const s = this.sel();
    for (const d of s.dims) await this.op("dimension_remove", { dimension: d }, "Delete dimension");
    for (const c of s.comments) await this.op("comment_delete", { comment: c }, "Delete comment");
    for (const t of s.texts) await this.op("text_delete", { text_id: t }, "Delete text");
    if (s.ids.length) await this.op("delete", { ids: s.ids }, "Delete");
    else if (!s.dims.length && !s.comments.length && !s.texts.length) this.say("Nothing selected.");
  }
  private async cutSel(): Promise<void> { const s = await this.needSelection("Cut"); if (s) await this.op("cut_to_clipboard", { ids: s.ids }, "Cut"); }
  private async paste(): Promise<void> { if (!this.view.status.clipboard) return this.say("The clipboard is empty."); const c = this.canvas.toWorld(40, 40); await this.op("paste", { at: c }, "Paste"); }
  private filterMenu(anchor: HTMLElement): void {
    const f = this.canvas.filters;
    const labels: Array<[keyof Filters, string]> = [["edges", "Lines and arcs"], ["vertices", "Points"], ["dimensions", "Dimensions"], ["comments", "Comments"], ["texts", "Text"]];
    openMenu(anchor, labels.map(([k, l]) => ({ label: `${f[k] ? "✓ " : "   "}${l}`, run: () => { f[k] = !f[k]; this.say(`Selection filter: ${labels.filter(([kk]) => f[kk]).map(([, ll]) => ll).join(", ") || "nothing"}`); } })), { label: "Selection filters" });
  }

  // ----------------------------------------------------------------------------------------------------- file / upload
  private async newProfile(): Promise<void> { if (await this.start()) this.applyView(this.view, true); }
  private async switchProfile(): Promise<void> {
    const info = await this.call(() => this.api.info()); if (!info) return;
    const entries: MenuEntry[] = [{ label: "New or open another profile…", icon: "plus", run: () => { void this.newProfile(); } }, ...(info.open.length ? ["separator" as const] : []),
      ...info.open.map((s): MenuEntry => ({ label: `${s.id === this.sid ? "✓ " : ""}${s.name}${s.dirty ? " •" : ""}`, run: () => { void this.call(() => this.api.state(s.id)).then((v) => { if (v) { this.view = v; this.sid = s.id; this.applyView(v, true); } }); } }))];
    const anchor = this.root.querySelector<HTMLElement>(".pf-header .btn"); if (anchor) openMenu(anchor, entries, { label: "Profiles" });
  }
  private async save(asNew: boolean): Promise<boolean> {
    let path: string | undefined;
    if (asNew || !this.view.status.path) {
      const name = await promptDialog({ title: asNew ? "Save As" : "Save profile", label: `File name (.sdrw) or full path`, value: this.view.status.path ?? `${this.view.status.name}.sdrw`, confirmLabel: "Save", maxLength: 1024, validate: (v) => (v.trim().toLowerCase().endsWith(".sdrw") ? null : "The name must end in .sdrw") });
      if (!name) return false; path = name.trim();
    }
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        const r = await this.api.save(this.sid, { ...(path ? { path } : {}), ...(attempt ? { overwrite: true, force: true } : {}) });
        this.applyView({ status: r.status }); toast(`Saved ${r.path}`, { kind: "ok", timeoutMs: 3000 }); return true;
      } catch (error) {
        const e = errorOf(error);
        if (e.status === 409 && attempt === 0 && await confirmDialog({ title: "Replace the file?", message: "That file already exists or was changed by someone else since you opened it.", detail: e.message, confirmLabel: "Replace it", danger: true })) continue;
        if (e.status !== 409) toast(e.message, { kind: "error" });
        return false;
      }
    }
    return false;
  }
  private setAutosave(minutes: number): void {
    this.autosaveMinutes = minutes; window.clearInterval(this.timer); this.timer = 0;
    if (minutes > 0) this.timer = window.setInterval(() => { if (this.view.status.dirty) void this.call(() => this.api.autosave(this.sid), true).then((r) => { if (r) this.applyView({ status: r.status }); }); }, minutes * 60_000);
    this.renderStatus();
  }
  /** Import: pick a .sdrw, .dxf or .svg on your computer and it is added to this profile (to work on it as its own profile, use Open). */
  private async importDialog(): Promise<void> {
    const file = await pickFile(".sdrw,.dxf,.svg"); if (!file) return;
    const r = await this.call(() => this.api.importUpload(this.sid, file, true));
    if (!r) return; this.applyView(r, false); toast(`Added ${file.name} to this profile.`, { kind: "ok" });
  }
  private async exportDialog(kind: "project" | "drawing" | "report"): Promise<void> {
    const base = this.view.status.name;
    const f = await formDialog(kind === "project" ? "Export project" : kind === "drawing" ? "Export drawing" : "Export report", [
      ...(kind === "drawing" ? [{ key: "format", label: "Format", kind: "select" as const, value: "svg", options: [["svg", "SVG"], ["dxf", "DXF"]] as Array<[string, string]> }] : []),
      ...(kind === "report" ? [{ key: "format", label: "Format", kind: "select" as const, value: "html", options: [["html", "HTML"], ["md", "Markdown"], ["json", "JSON"]] as Array<[string, string]> },
        { key: "purpose", label: "Check it for", kind: "select" as const, value: this.checkPurpose, options: PURPOSES }] : []),
      { key: "path", label: "File name or full path", kind: "text", value: kind === "project" ? `${base}-copy.sdrw` : `${base}.${kind === "report" ? "html" : "svg"}`, note: "A name alone is saved in Profiler's profiles folder." },
      { key: "overwrite", label: "Replace the file if it exists", kind: "check", value: false }], "Export");
    if (!f) return; const path = String(f["path"]).trim(); const overwrite = f["overwrite"] === true;
    const r = kind === "project" ? await this.call(() => this.api.exportFile(this.sid, { format: "sdrw", path, overwrite }))
      : kind === "drawing" ? await this.call(() => this.api.exportFile(this.sid, { format: String(f["format"]), path, overwrite }))
        : await this.call(() => this.api.report(this.sid, { path, format: String(f["format"]), purpose: String(f["purpose"]), overwrite }));
    if (r) toast(`Exported ${r.path}`, { kind: "ok" });
  }
  private async closeWorkspace(): Promise<void> {
    if (this.view.status.dirty) {
      const choice = await new Promise<"save" | "discard" | null>((resolve) => { const d = openDialog<"save" | "discard">({ title: "Close Profiler", size: "narrow", body: h("p", { text: `“${this.view.status.name}” has unsaved changes.` }),
        actions: [{ label: "Discard changes", kind: "danger", run: () => "discard" }, { label: "Save", kind: "primary", run: () => "save" }] }); void d.result.then((r) => resolve(r)); });
      if (!choice) return;
      if (choice === "save" && !(await this.save(false))) return;
      await this.call(() => this.api.close(this.sid, true), true);
    } else await this.call(() => this.api.close(this.sid, false), true);
    this.teardown();
  }
  private teardown(): void {
    this.closed = true; window.clearInterval(this.timer); window.removeEventListener("keydown", this.keyHandler); window.removeEventListener("beforeunload", this.unloadHandler);
    window.removeEventListener("resize", this.fitToScreen); this.sizeWatch?.disconnect();
    this.root.remove(); ProfilerWorkspace.current = null; this.opener?.focus();
  }

  private async upload(close: boolean): Promise<void> {
    if (!store.project) { toast("Open or create a project first: a profile is uploaded into the project's model.", { kind: "error" }); return; }
    const info = await this.call(() => this.api.info()); if (!info) return;
    const picked = store.selection?.kind === "model" ? store.selection.ids[0] : undefined;
    const sel = this.sel();
    const f = await formDialog(close ? "Upload and close" : "Upload to SPADRA", [
      { key: "kind", label: "Make", kind: "select", value: "extrude", options: [["extrude", "Extrusion"], ["void", "Void (cuts other solids)"], ["sweep", "Sweep"], ["revolve", "Revolve"], ["opening", "Opening profile (door or window frame)"]] },
      { key: "name", label: "Name", kind: "text", value: this.view.status.name },
      { key: "mode", label: "Keep it linked to the profile?", kind: "select", value: this.view.status.path && !this.view.status.dirty ? "linked" : "embedded",
        options: [["embedded", "Embedded: the profile is stored inside the object"], ["linked", "Linked: follows the saved .sdrw file"], ["static", "Static: geometry only"]],
        note: "Embedded and linked keep the profile's parameters so the object can be updated later." },
      { key: "level", label: "Level", kind: "select", value: info.project.levels?.[0]?.id ?? "", options: (info.project.levels ?? []).map((l): [string, string] => [l.id, l.name]), show: (v) => v["kind"] !== "opening" },
      { key: "depth", label: "Depth / height (m)", kind: "number", value: 1, min: 0.001, show: (v) => v["kind"] === "extrude" || v["kind"] === "void" },
      { key: "base", label: "Base height (m)", kind: "number", value: 0, show: (v) => v["kind"] === "extrude" || v["kind"] === "void" || v["kind"] === "revolve" },
      { key: "angle", label: "Revolve angle (degrees)", kind: "number", value: 360, min: 1, show: (v) => v["kind"] === "revolve" },
      { key: "axis_angle", label: "Revolve axis direction (degrees, 90 = vertical in the profile)", kind: "number", value: 90, show: (v) => v["kind"] === "revolve" },
      { key: "role", label: "This profile is the sweep's", kind: "select", value: sel.edges.length ? "section" : "path", options: [["section", "Cross-section (path = the lines selected now)"], ["path", "Path (give a section shape)"]], show: (v) => v["kind"] === "sweep" },
      { key: "shape", label: "Section shape", kind: "select", value: "rectangle", options: [["rectangle", "Rectangle"], ["round", "Round"], ["i_beam", "I-beam"], ["channel", "Channel"], ["tube", "Tube"]], show: (v) => v["kind"] === "sweep" && v["role"] === "path" },
      { key: "width", label: "Section width (mm)", kind: "number", value: 200, min: 1, show: (v) => v["kind"] === "sweep" && v["role"] === "path" },
      { key: "height", label: "Section height (mm)", kind: "number", value: 200, min: 1, show: (v) => v["kind"] === "sweep" && v["role"] === "path" },
      { key: "region", label: "Which closed shape", kind: "select", value: "", options: [["", "The only one / outermost"], ...this.regions.map((r, i): [string, string] => [String(i), `Region ${i + 1} (area ${fmt(+r.area.toPrecision(6))})`])], show: (v) => v["kind"] !== "sweep" || v["role"] === "section" },
      ...(picked ? [{ key: "opening", label: `Door or window selected in SPADRA`, kind: "text" as const, value: picked, show: (v: Record<string, unknown>) => v["kind"] === "opening" }]
        : [{ key: "opening", label: "Door or window id (select it in SPADRA before opening Profiler)", kind: "text" as const, value: "", show: (v: Record<string, unknown>) => v["kind"] === "opening" }])], close ? "Upload and close" : "Upload",
      "The profile is checked first. If it is not suitable, nothing is changed and you are told what to fix.");
    if (!f) return;
    const kind = String(f["kind"]); const options: Record<string, unknown> = { mode: f["mode"], name: String(f["name"]).trim() || this.view.status.name };
    if (f["level"]) options["level"] = f["level"];
    if (kind === "extrude" || kind === "void") { options["depth"] = f["depth"]; options["base"] = f["base"]; }
    if (kind === "revolve") { options["angle"] = f["angle"]; options["base"] = f["base"]; options["axis"] = { point: [0, 0], angle: f["axis_angle"] }; }
    if (kind === "sweep") { options["role"] = f["role"]; if (f["role"] === "section") options["path_edges"] = sel.edges; else { options["shape"] = f["shape"]; options["width"] = f["width"]; options["height"] = f["height"]; if (sel.edges.length) options["path_edges"] = sel.edges; } }
    if (kind === "opening") options["opening"] = String(f["opening"]).trim();
    if (f["region"] !== undefined && f["region"] !== "" && kind !== "sweep") options["region_index"] = Number(f["region"]);
    if (kind === "sweep" && f["role"] === "section" && f["region"] !== "") options["region_index"] = Number(f["region"]);
    try {
      const r = await this.api.send(this.sid, { kind, options, close });
      await store.refresh();
      const n = r.result.ids.length;
      toast(`Uploaded: ${n} object${n === 1 ? "" : "s"} added to the model.${r.warnings.length ? " " + r.warnings.join(" ") : ""}`, { kind: "ok", timeoutMs: 5000 });
      if (r.close_blocked) toast(`Not closed: ${r.close_blocked}. Save it, then close.`, { kind: "info" });
      if (r.closed) this.teardown();
    } catch (error) { await this.showUploadError(error); }
  }
  private async showUploadError(error: unknown): Promise<void> {
    const e = errorOf(error); const issues = ((error instanceof ApiError ? error.details : null) as { issues?: Array<{ message: string; refs: string[] }> } | null)?.issues ?? [];
    this.say(e.message);
    openDialog({ title: "Nothing was uploaded", size: "narrow", body: h("div", {}, h("p", { text: e.message }), issues.length ? h("ul", { class: "pf-list" }, ...issues.map((i) => h("li", { text: i.message }))) : null,
      h("p", { class: "note", text: "The model is exactly as it was. Fix the profile and try again; Analyze → Check profile points at each problem." })), noCancel: true, actions: [{ label: "Open the check", kind: "primary", run: () => { this.rightTab = "check"; void this.runCheck("extrude"); return true; } }], cancelLabel: "Close" });
  }

  // ------------------------------------------------------------------------------------------------------ analyze
  private async runCheck(purpose: string): Promise<void> {
    this.checkPurpose = purpose; const r = await this.call(() => this.api.check(this.sid, purpose, purpose === "revolve" ? { axis: { point: [0, 0], angle: 90 } } : {})); if (!r) return;
    this.checkResult = r; this.rightTab = "check"; this.renderPanels(); this.say(checkSummary(r.summary));
  }
  private async repair(action: string, apply: boolean): Promise<void> {
    const r = await this.call(() => this.api.op<{ changes?: unknown; summary?: string; message?: string }>(this.sid, apply ? "repair" : "repair_preview", { action, purpose: this.checkPurpose }, apply));
    if (!r) return;
    if (apply) { this.applyOp(r); toast("Fixed. Undo brings the old geometry back.", { kind: "ok" }); await this.runCheck(this.checkPurpose); }
    else openDialog({ title: "What this fix would change", size: "narrow", body: h("pre", { class: "pf-pre", text: JSON.stringify(r.result, null, 2) }), noCancel: true, cancelLabel: "Close", actions: [{ label: "Apply the fix", kind: "primary", run: async () => { await this.repair(action, true); return true; } }] });
  }
  private async areas(): Promise<void> {
    await this.loadRegions();
    openDialog({ title: "Regions and areas", size: "narrow", noCancel: true, cancelLabel: "Close", body: this.regions.length ? h("table", { class: "pf-table" }, h("thead", {}, h("tr", {}, h("th", { text: "#" }), h("th", { text: "Area" }), h("th", { text: "Perimeter" }), h("th", { text: "Holes" }))),
      h("tbody", {}, ...this.regions.map((r, i) => h("tr", { on: { click: () => this.canvas.setSelection(r.edges) } }, h("td", { text: String(i + 1) }), h("td", { text: `${fmt(+r.area.toPrecision(8))} ${this.view.render.unit}²` }), h("td", { text: fmt(+r.perimeter.toPrecision(8)) }), h("td", { text: String(r.holes.length) }))))) : h("p", { text: "There is no closed region yet." }) });
  }

  // ----------------------------------------------------------------------------------------- insert: dims, notes etc.
  private dimTool(type: string, label: string, n: number): ToolDef {
    const refSteps = Array.from({ length: n }, (_, i) => ({ key: `r${i}`, kind: "ref" as const, prompt: n === 1 ? `Click the ${type === "length" ? "line" : "object"} to measure` : `Click ${i === 0 ? "the first" : "the second"} point or line` }));
    return { id: `dim_${type}`, label: `${label} dimension`, icon: "dimension", hint: "", steps: refSteps, needs: n === 0 ? "objects" : undefined as never,
      build: async (v, s) => {
        const refs = n === 0 ? s.edges : Array.from({ length: n }, (_, i) => String(v[`r${i}`]));
        const m = await this.call(() => this.api.op<{ value?: number; signed?: number; unit?: string }>(this.sid, "readout", { type, refs }));
        if (!m) return [];
        const measured = m.result.value ?? m.result.signed ?? 0; const drivable = ["aligned", "length", "center_distance", "horizontal", "vertical", "radius", "diameter", "angular", "perpendicular"].includes(type);
        const linear = ["aligned", "horizontal", "vertical", "length", "center_distance"].includes(type); const params = this.view.params.parameters;
        const f = await formDialog(`${label} dimension`, [
          { key: "mode", label: "Mode", kind: "select", value: "reference", options: [["reference", "Reference: only measures"], ...(drivable ? [["driving", "Driving: sets the size"], ["formula", "Formula-driven: size from a formula"]] as Array<[string, string]> : []), ["temporary", "Temporary: shown now, not kept"]] },
          { key: "value", label: `Value (now ${fmt(+measured.toPrecision(8))})`, kind: "number", value: +measured.toPrecision(8), show: (x) => x["mode"] === "driving" },
          { key: "expr", label: "Formula", kind: "text", value: "", note: "For example Width - Return", show: (x) => x["mode"] === "formula" },
          ...(linear ? [{ key: "anchor", label: "Which end stays fixed when it changes", kind: "select" as const, value: "none", options: [["none", "Neither: least movement"], ["start", "Start stays"], ["end", "End stays"], ["center", "Centre stays"]] as Array<[string, string]>, show: (x: Record<string, unknown>) => x["mode"] === "driving" || x["mode"] === "formula" }] : []),
          { key: "param", label: "Link to parameter", kind: "select", value: "", options: [["", "None"], ...params.filter((p) => !p.derived).map((p): [string, string] => [p.name, p.name])], show: (x) => x["mode"] === "driving" },
          { key: "newparam", label: "or create a parameter with this name", kind: "text", value: "", show: (x) => x["mode"] === "driving" }], "Add dimension");
        if (!f) return [];
        const mode = String(f["mode"]);
        if (mode === "temporary") { this.say(`${label}: ${fmt(+measured.toPrecision(8))} ${m.result.unit ?? ""}`); toast(`${label}: ${fmt(+measured.toPrecision(8))}`, { kind: "info", timeoutMs: 6000 }); return []; }
        const args: Record<string, unknown> = { type, refs, mode };
        if (mode === "driving") args["value"] = f["value"]; if (mode === "formula") args["value"] = null;
        if (linear && f["anchor"] && f["anchor"] !== "none") args["anchor"] = f["anchor"];
        const out = await this.call(() => this.api.op<{ dimension: string }>(this.sid, "dimension", args, true)); if (!out) return [];
        this.applyOp(out); const did = out.result.dimension;
        if (mode === "formula" && String(f["expr"]).trim()) await this.op("dimension_value", { dimension: did, expr: String(f["expr"]).trim() }, "Dimension formula");
        else if (mode === "driving" && f["param"]) await this.op("param_link", { dimension: did, name: f["param"] }, "Link parameter");
        else if (mode === "driving" && String(f["newparam"]).trim()) await this.op("param_from_dimension", { dimension: did, name: String(f["newparam"]).trim() }, "Dimension to parameter");
        return [];
      } };
  }
  /** One tool for every measurement: pick a line, arc, circle or point, then (optionally) a second object. The server chooses the dimension type and whether it
   *  drives or only measures. With one object already selected, the first click is the second object. */
  private dimAutoTool(): ToolDef {
    const NAMES: Record<string, string> = { length: "Length", radius: "Radius", diameter: "Diameter", aligned: "Distance", horizontal: "Horizontal distance", vertical: "Vertical distance", perpendicular: "Distance across",
      angular: "Angle", center_distance: "Centre to centre", min_distance: "Shortest distance", spline_length: "Length", slope: "Slope" };
    const add = async (refs: string[]): Promise<Build[]> => {
      const out = await this.call(() => this.api.op<{ dimension: string; type: string; mode: string; note: string | null; measure: { value?: number; unit?: string } }>(this.sid, "dimension_auto", { refs }, true)); if (!out) return [];
      this.applyOp(out); const r = out.result; const angle = r.type === "angular"; const val = angle ? `${fmt(+(((r.measure.value ?? 0) * 180) / Math.PI).toPrecision(6))}°` : `${fmt(+(r.measure.value ?? 0).toPrecision(8))} ${this.view.render.unit}`;
      const text = `${NAMES[r.type] ?? r.type}: ${val}${r.mode === "driving" ? " (drives the size)" : " (measure only)"}`;
      this.say(r.note ? `${text}: ${r.note}.` : text); toast(r.note ? `${text}. ${r.note}.` : text, { kind: r.note ? "info" : "ok", timeoutMs: 4000 }); return [];
    };
    return { id: "dim_auto", label: "Dimension", icon: "dimension", hint: "Click a line, arc, circle or point, then a second object. Enter measures the first one alone.", repeat: true,
      steps: [{ key: "r0", kind: "ref", prompt: "Dimension: click a line, arc, circle or point" }, { key: "r1", kind: "ref", optional: true, prompt: "Click a second point or line to measure to it (Enter: measure the first alone)" }],
      seed: (s) => { const ids = [...s.edges, ...s.vertices]; return ids.length === 1 && s.ids.length === 1 ? { values: { r0: ids[0]! }, step: 1 } : null; },
      quick: (s) => { const ids = [...s.edges, ...s.vertices]; return ids.length === 2 && s.ids.length === 2 ? [{ op: "dimension_auto", args: { refs: ids } }] : null; },
      build: (v) => add([v["r0"], v["r1"]].filter((x): x is string => typeof x === "string")) };
  }
  private dimButton(): Btn { const def = this.dimAutoTool(); return { label: "Dimension", icon: "dimension", tool: def.id, run: () => this.startTool(def), note: "Measure: click a line, arc, circle or point, then another (shortcut D)" }; }
  private chainTool(): ToolDef {
    return { id: "chain", label: "Chain dimension", icon: "dimension", hint: "", steps: [{ key: "vertices", kind: "points", prompt: "Click each point of the chain in order", need: 3 },
      { key: "along", kind: "choice", prompt: "Measure", label: "Measure the segments", value: "horizontal", options: [["horizontal", "Horizontally"], ["vertical", "Vertically"], ["aligned", "Along the points (true distance)"]] }],
      build: async (v) => { const pts = v["vertices"] as Pt[]; const ids: string[] = [];
        for (const p of pts) { const id = this.runHost().pickAt(p, "vertex"); if (!id) { this.say("Every click of a chain must be on an existing point."); return []; } ids.push(id); }
        return [{ op: "chain", args: { vertices: ids, with_total: true, along: String(v["along"] ?? "aligned") } }]; } };
  }
  private constraintTool(kind: string, label: string, n: number): ToolDef {
    return { id: `con_${kind}`, label, icon: "pf-constraint", hint: "", steps: Array.from({ length: n }, (_, i) => ({ key: `r${i}`, kind: "ref" as const, prompt: n === 1 ? "Click the line" : `Click ${i === 0 ? "the first" : "the second"} object` })),
      quick: (s) => { const refs = [...s.edges, ...s.vertices]; return refs.length === n && s.ids.length === n ? [{ op: "constrain", args: { kind, refs } }] : null; },
      build: (v) => [{ op: "constrain", args: { kind, refs: Array.from({ length: n }, (_, i) => String(v[`r${i}`])) } }] };
  }
  private commentAt(at: Pt, hit: Hit | null): void {
    void promptDialog({ title: "Add comment", label: "Comment", confirmLabel: "Add", maxLength: 2000, validate: (v) => (v.trim() ? null : "Write a comment.") }).then((text) => {
      if (!text) return;
      const anchor = hit?.kind === "edge" ? { edge: hit.id } : hit?.kind === "vertex" ? { vertex: hit.id } : {};
      void this.op("comment_add", { text: text.trim(), xy: at, ...anchor }, "Add comment");
    });
  }
  private openHit(hit: Hit): void { if (hit.kind === "comment") this.commentDialog(hit.id); else if (hit.kind === "dimension") { const d = this.view.render.dimensions.find((x) => x.id === hit.id); if (d && (d.mode === "driving" || d.mode === "formula")) void editDimension(this.panelHost(), d); } }
  private commentDialog(id: string): void {
    const c = this.view.render.comments.find((x) => x.id === id); if (!c) return;
    const reply = h("input", { type: "text", placeholder: "Reply…", attrs: { "aria-label": "Reply", maxlength: 2000 } });
    const sendReply = async (): Promise<boolean> => { const t = reply.value.trim(); if (!t) return false; const ok = await this.op("comment_reply", { comment: id, text: t }, "Reply"); if (ok) { dlg.close(); this.commentDialog(id); } return ok; };
    reply.addEventListener("keydown", (e) => { if (e.key === "Enter") void sendReply(); });
    const body = h("div", { class: "pf-thread" }, h("p", { class: "pf-comment", text: c.text }), h("small", { class: "muted", text: `${c.author} · ${c.created}${c.resolved ? " · resolved" : ""}` }),
      ...c.replies.map((r) => h("div", { class: "pf-reply" }, h("p", { text: r.text }), h("small", { class: "muted", text: `${r.author} · ${r.created}` }))), reply);
    const dlg = openDialog({ title: "Comment", size: "narrow", body, cancelLabel: "Close", actions: [
      { label: "Reply", run: async () => { await sendReply(); return undefined; } },
      { label: c.resolved ? "Reopen" : "Resolve", run: async () => (await this.op("comment_resolve", { comment: id, resolved: !c.resolved }, "Resolve comment")) ? true : undefined },
      { label: "Edit", run: async () => { const t = await promptDialog({ title: "Edit comment", label: "Comment", value: c.text, confirmLabel: "Save", maxLength: 2000 }); if (t?.trim()) await this.op("comment_edit", { comment: id, text: t.trim() }, "Edit comment"); return t ? true : undefined; } },
      { label: "Delete", kind: "danger", run: async () => (await this.op("comment_delete", { comment: id }, "Delete comment")) ? true : undefined }] });
  }
  private contextMenu(hit: Hit | null, at: Pt, x: number, y: number): void {
    const e: MenuEntry[] = []; const sel = this.sel();
    if (hit?.kind === "comment") e.push({ label: "Open comment", icon: "pf-comment", run: () => this.commentDialog(hit.id) }, { label: "Delete comment", icon: "trash", danger: true, run: () => { void this.op("comment_delete", { comment: hit.id }, "Delete comment"); } });
    else if (hit?.kind === "dimension") { const d = this.view.render.dimensions.find((q) => q.id === hit.id); if (d) e.push({ label: "Edit dimension…", icon: "edit", disabled: !(d.mode === "driving" || d.mode === "formula"), run: () => { void editDimension(this.panelHost(), d); } }, { label: "Delete dimension", icon: "trash", danger: true, run: () => { void this.op("dimension_remove", { dimension: d.id }, "Delete dimension"); } }); }
    else if (hit) e.push({ label: "Delete", icon: "trash", danger: true, run: () => { void this.deleteSel(); } }, { label: "Copy", icon: "copy", run: () => { void this.op("copy_to_clipboard", { ids: sel.ids }, "Copy"); } }, { label: "Cut", icon: "pf-cut", run: () => { void this.cutSel(); } },
      { label: "Toggle construction line", run: () => { void this.op("construction", { ids: sel.edges }, "Construction"); }, disabled: !sel.edges.length }, "separator");
    else e.push({ label: "Paste", icon: "pf-paste", disabled: !this.view.status.clipboard, run: () => { void this.paste(); } }, { label: "Select all", run: () => this.canvas.selectAll() }, { label: "Zoom to fit", icon: "fit", run: () => this.canvas.fit() }, "separator");
    if (hit?.kind !== "comment") e.push({ label: "Add comment here…", icon: "pf-comment", run: () => this.commentAt(at, hit) });
    const anchor = h("span", { css: { position: "fixed", left: `${x}px`, top: `${y}px`, width: "1px", height: "1px" } }); document.body.append(anchor); openMenu(anchor, e, { label: "Profile actions" }); window.setTimeout(() => anchor.remove(), 4000);
  }

  // ------------------------------------------------------------------------------------------------------- ribbons
  private tool(def: ToolDef, icon?: string): Btn { return { label: def.label, icon: icon ?? def.icon, tool: def.id, run: () => this.startTool(def), note: def.hint }; }
  private menuBtn(label: string, icon: string, entries: () => MenuEntry[], note?: string): Btn { return { label, icon, note: note ?? label, run: (a) => openMenu(a, entries(), { label }) }; }
  private find(id: string): ToolDef { const d = [...DRAW_TOOLS, ...MODIFY_TOOLS].find((t) => t.id === id); if (!d) throw new Error(`no tool ${id}`); return d; }
  private groups(name: RibbonName): Group[] {
    const st = this.view.status, has = this.sel().ids.length > 0;
    switch (name) {
      case "File": return [[
        { label: "New", icon: "plus", run: () => this.newProfile(), note: "Start a new profile from a template" },
        { label: "Open", icon: "pf-open", run: () => this.newProfile(), note: "Open a .sdrw file, a recovered copy, or another open profile" },
        { label: "Save", icon: "save", run: () => { void this.save(false); }, enabled: st.dirty || !st.path, note: "Save (Ctrl+S)" },
        { label: "Save as", icon: "save", run: () => { void this.save(true); } }], [
        this.menuBtn("Autosave", "clock", () => [0, 1, 2, 5, 10].map((m) => ({ label: `${this.autosaveMinutes === m ? "✓ " : "   "}${m ? `Every ${m} minute${m === 1 ? "" : "s"}` : "Off"}`, run: () => this.setAutosave(m) })), "Recovery copy of unsaved work, written beside your files, never over them")],
        [{ label: "Upload", icon: "upload", run: () => { void this.upload(false); }, enabled: !!store.project, note: store.project ? "Send the profile to the model; Profiler stays open" : "Open a project first" },
         { label: "Upload and close", icon: "upload", run: () => { void this.upload(true); }, enabled: !!store.project, note: store.project ? "Send the profile to the model, then close" : "Open a project first" }],
        [{ label: "Import", icon: "download", run: () => this.importDialog(), note: "Bring in a .sdrw, .dxf or .svg drawing" },
         this.menuBtn("Export", "download", () => [{ label: "Project (.sdrw copy)…", run: () => { void this.exportDialog("project"); } }, { label: "Drawing (SVG or DXF)…", run: () => { void this.exportDialog("drawing"); } }, { label: "Report…", run: () => { void this.exportDialog("report"); } }])],
        [{ label: "Close", icon: "close", run: () => this.closeWorkspace(), note: "Close Profiler (asks about unsaved changes)" }]];
      case "Edit": return [[
        { label: "Undo", icon: "undo", run: () => this.doUndo(), enabled: st.can_undo, note: st.undo_label ? `Undo ${st.undo_label}` : "Nothing to undo" },
        { label: "Redo", icon: "pf-redo", run: () => this.doRedo(), enabled: st.can_redo, note: st.redo_label ? `Redo ${st.redo_label}` : "Nothing to redo" },
        { label: "Repeat", icon: "repeat", run: async () => { const r = await this.call(() => this.api.repeat(this.sid)); if (r) this.applyOp(r); }, enabled: st.can_repeat, note: "Repeat the last change" }], [
        { label: "Cut", icon: "pf-cut", run: () => this.cutSel(), enabled: has }, { label: "Copy", icon: "copy", run: () => { void this.op("copy_to_clipboard", { ids: this.sel().ids }, "Copy"); }, enabled: has },
        { label: "Paste", icon: "pf-paste", run: () => this.paste(), enabled: st.clipboard }, { label: "Delete", icon: "trash", run: () => this.deleteSel(), enabled: has || this.sel().dims.length + this.sel().comments.length + this.sel().texts.length > 0 }], [
        { label: "Select all", icon: "check-square", run: () => this.canvas.selectAll() }, { label: "Deselect", icon: "close", run: () => this.canvas.setSelection([]) },
        { label: "Filters", icon: "filter", run: (a) => this.filterMenu(a), note: "Choose what a click or window can select" }], [
        { label: "Fit", icon: "fit", run: () => this.canvas.fit() }, { label: "Zoom in", icon: "pf-scale", run: () => this.canvas.zoomBy(1.5) }, { label: "Zoom out", icon: "zoomout", run: () => this.canvas.zoomBy(1 / 1.5) },
        this.menuBtn("View", "grid", () => { const v = this.view.render.view; return [
          { label: `${v.rulers ? "✓ " : "   "}Rulers`, run: () => { void this.op("view", { rulers: !v.rulers }, "View"); } }, { label: `${v.axes ? "✓ " : "   "}Axes`, run: () => { void this.op("view", { axes: !v.axes }, "View"); } },
          { label: `${store.showGrid ? "✓ " : "   "}Show grid`, run: () => this.setShowGrid(!store.showGrid) },
          { label: `${v.snap_grid ? "✓ " : "   "}Snap to grid`, run: () => { void this.op("view", { snap_grid: !v.snap_grid }, "View"); } }, { label: "Grid spacing…", run: () => this.gridMenu(this.statusEl.querySelector<HTMLElement>(".grid-size") ?? this.statusEl) },
          { label: `${this.canvas.showDimensions ? "✓ " : "   "}Show dimensions`, run: () => { this.canvas.showDimensions = !this.canvas.showDimensions; this.canvas.schedule(); } },
          { label: `${this.canvas.showConstraints ? "✓ " : "   "}Show constraint symbols`, run: () => { this.canvas.showConstraints = !this.canvas.showConstraints; this.canvas.schedule(); } }, { label: "Number of decimals…", run: () => { void formDialog("Decimals", [{ key: "p", label: "Decimals in dimension text", kind: "number", value: this.view.render.precision, min: 0 }], "Apply").then((f) => { if (f) void this.op("view", { precision: Number(f["p"]) }, "View"); }); } }]; })]];
      case "Insert": return [[
        this.dimButton(), this.menuBtn("Dimension types", "dimension", () => DIMENSIONS.map(([t, l, n]) => ({ label: l, run: () => this.startTool(this.dimTool(t, l, n)) })), "Choose the kind yourself: length, radius, angle, horizontal, vertical …"),
        { label: "Chain", icon: "dimension", run: () => this.startTool(this.chainTool()), note: "A row of dimensions with a total (for example 80 + 40 = 120)" },
        { label: "Style", icon: "settings", run: () => this.dimStyleDialog(), note: "Text height, arrows, decimals and colour of dimensions" }], [
        { label: "Comment", icon: "pf-comment", run: () => this.startTool({ id: "comment", label: "Comment", icon: "pf-comment", hint: "Click where the comment goes.", steps: [{ key: "at", kind: "point", prompt: "Click where the comment goes" }], build: (v) => { this.commentAt(v["at"] as Pt, null); return []; } }) },
        { label: "Text", icon: "text", run: () => this.startTool({ id: "text", label: "Text", icon: "text", hint: "Click where the text goes.", steps: [{ key: "at", kind: "point", prompt: "Click where the text goes" }, { key: "text", kind: "text", prompt: "Text", label: "Text", value: "" }],
          build: (v) => String(v["text"]).trim() ? [{ op: "text_add", args: { xy: v["at"], text: String(v["text"]).trim(), layer: this.layer } }] : [] }) },
        { label: "Hatch", icon: "hatch", run: () => this.startTool({ id: "hatch", label: "Hatch", icon: "hatch", hint: "Click inside a closed shape.", steps: [{ key: "region", kind: "region", prompt: "Click inside the shape to hatch" }],
          build: async (v) => { const f = await formDialog("Hatch", [{ key: "pattern", label: "Pattern", kind: "select", value: "lines", options: [["lines", "Lines"], ["cross", "Cross-hatch"]] }, { key: "angle", label: "Angle (degrees)", kind: "number", value: 45 }, { key: "spacing", label: `Spacing (${this.view.render.unit}, empty = automatic)`, kind: "number" }], "Hatch"); if (!f) return [];
            return [{ op: "hatch_add", args: { edges: v["region"], pattern: f["pattern"], angle: f["angle"], ...(f["spacing"] ? { spacing: f["spacing"] } : {}), layer: this.layer } }]; } }) }], [
        this.menuBtn("Block", "pf-group", () => { const blocks = this.view.params.blocks ?? []; return [{ label: "Define a block from the selection…", disabled: !has, run: () => { void formDialog("Define block", [{ key: "name", label: "Block name", kind: "text", value: "" }], "Define").then((f) => { if (f && String(f["name"]).trim()) { const b = this.view.render.bounds; void this.op("block_define", { ids: this.sel().ids, name: String(f["name"]).trim(), base: b ? [b[0], b[1]] : [0, 0] }, "Define block"); } }); } },
          ...(blocks.length ? ["separator" as const, ...blocks.map((b): MenuEntry => ({ label: `Insert “${b}”`, run: () => this.startTool({ id: "block", label: `Insert ${b}`, icon: "pf-group", hint: "Click where to place the block.", repeat: true, steps: [{ key: "at", kind: "point", prompt: "Insertion point" }, { key: "rot", kind: "number", prompt: "Rotation", label: "Rotation (degrees, multiples of 90 keep constraints)", value: 0 }], build: (v) => [{ op: "block_insert", args: { name: b, at: v["at"], rotation: Number(v["rot"]), scale: 1 } }] }) }))] : [])]; }, "Reusable parts"),
        { label: "Template", icon: "layout", run: () => this.newProfile(), note: "Start a new profile from a template" },
        this.menuBtn("Units", "settings", () => this.info.units.map((u) => ({ label: `${this.view.render.unit === u ? "✓ " : "   "}${u}`, run: () => { void confirmDialog({ title: `Change unit to ${u}`, message: "Convert the existing geometry and parameters to the new unit?", detail: "Choose Convert to keep the real sizes; Cancel leaves the unit as it is.", confirmLabel: "Convert" }).then((yes) => { if (yes) void this.op("set_unit", { unit: u, convert: true }, "Change unit"); }); } })), "Drawing unit")]];
      case "Draw": return [
        ["line", "polyline", "rectangle"].map((i) => this.tool(this.find(i))), [this.dimButton()], ["circle", "circle3", "ellipse", "polygon", "slot", "construction_line"].map((i) => this.tool(this.find(i))),
        ["arc_ser", "arc_ce", "arc3", "fillet", "chamfer", "chamfer2"].map((i) => this.tool(this.find(i))), ["spline_math", "spline_max", "spline_revit"].map((i) => this.tool(this.find(i))) ];
      case "Modifications": return [
        [{ ...this.tool(this.find("move")), icon: "pf-move" }, this.tool(this.find("copy")), { ...this.tool(this.find("rotate")), icon: "pf-rotate" }, { ...this.tool(this.find("scale")), icon: "pf-scale" }, this.tool(this.find("mirror")), this.tool(this.find("mirror_move"), "mirror"), this.tool(this.find("stretch"))
          , this.menuBtn("Align", "pf-align", () => (["left", "center_x", "right", "bottom", "center_y", "top"] as const).map((how) => ({ label: `Align ${how.replace("_", " ")}`, disabled: !has, run: () => this.startTool({ id: `align_${how}`, label: `Align ${how}`, icon: "pf-align", hint: "Click the line to align to.", steps: [{ key: "t", kind: "point", prompt: "Click the position to align to" }],
            build: (v, s) => [{ op: "align", args: { ids: s.ids, how, target: v["t"] } }] }) })), "Line up the selection's edge or centre with a point")],
        ["trim", "extend", "split", "cut", "offset", "join", "weld", "connect", "attach"].map((i) => this.tool(this.find(i))),
        [this.menuBtn("Array", "pf-array", () => [{ label: "Rectangular…", disabled: !has, run: () => { void this.arrayDialog("rect"); } }, { label: "Polar…", disabled: !has, run: () => { void this.arrayDialog("polar"); } }, { label: "Along a path…", disabled: !has, run: () => { void this.arrayDialog("path"); } }], "Repeat the selection")],
        [this.tool(this.find("group")), this.tool(this.find("group_sel")), { label: "Ungroup", icon: "pf-group", enabled: this.view.render.groups.length > 0, note: "Release a group", run: (a) => openMenu(a, this.view.render.groups.map((g) => ({ label: `${g.kind} group ${g.name ?? g.id.slice(0, 6)}`, run: () => { void this.op("ungroup", { group: g.id }, "Ungroup"); } })), { label: "Ungroup" }) },
          this.menuBtn("Component", "pf-group", () => [{ label: "Make a component from the selection…", disabled: !has, run: () => { void formDialog("Make component", [{ key: "name", label: "Component name", kind: "text", value: "" }], "Make").then((f) => { if (f && String(f["name"]).trim()) void this.op("make_component", { ids: this.sel().ids, name: String(f["name"]).trim() }, "Make component"); }); } },
            ...(this.view.params.components ?? []).map((c): MenuEntry => ({ label: `Insert “${c}”`, run: () => this.startTool({ id: "inst", label: `Insert ${c}`, icon: "pf-group", hint: "Click where to place it.", repeat: true, steps: [{ key: "at", kind: "point", prompt: "Insertion point" }], build: (v) => [{ op: "instantiate_component", args: { component: c, at: v["at"] } }] }) }))], "A parametric group that can be placed again")],
        [{ label: "Union", icon: "boolean", run: () => this.startTool(this.boolTool("union")), note: "Merge two regions into one" }, { label: "Intersect", icon: "pf-intersect", run: () => this.startTool(this.boolTool("intersect")), note: "Keep only where two regions overlap" }, { label: "Subtract", icon: "pf-subtract", run: () => this.startTool(this.boolTool("subtract")), note: "Remove the second region from the first" }],
        [this.menuBtn("Constrain", "pf-constraint", () => [...CONSTRAINTS.map(([k, l, n]): MenuEntry => ({ label: l, run: () => this.startTool(this.constraintTool(k, l, n)) })), "separator", { label: "Fix a point", run: () => this.startTool({ id: "pin", label: "Fix point", icon: "pin", hint: "Click the point to fix in place.", repeat: true, steps: [{ key: "v", kind: "vertex", prompt: "Click the point" }], build: (v) => [{ op: "pin", args: { vertex: v["v"] } }] }) }], "Geometric constraints"),
          this.tool(this.find("color"))]];
      case "Analyze": return [[
        { label: "Check profile", icon: "check-circle", run: () => this.runCheck(this.checkPurpose), note: "Open contours, crossings, duplicates, zero-length lines, broken references, constraint conflicts" },
        this.menuBtn("Check for…", "list", () => PURPOSES.map(([k, l]) => ({ label: `${this.checkPurpose === k ? "✓ " : "   "}${l}`, run: () => { void this.runCheck(k); } })), "Check whether the profile suits an operation")],
        [{ label: "Regions", icon: "pf-region", run: () => this.areas(), note: "Closed shapes, their areas and perimeters" },
         this.menuBtn("Measure", "pf-measure", () => DIMENSIONS.filter(([, , n]) => n > 0).map(([t, l, n]) => ({ label: l, run: () => this.startTool(this.measureTool(t, l, n)) })), "Measure without adding a dimension"),
         { label: "Parameters", icon: "pf-param", run: () => { this.leftTab = "params"; this.renderPanels(); }, note: "Named values and formulas that drive dimensions" },
         { label: "Constraints", icon: "pf-constraint", run: () => { this.rightTab = "cons"; this.renderPanels(); }, note: "Degrees of freedom, conflicts and redundancies" }],
        [{ label: "Assistant", icon: "info", run: () => this.assistantNote(), note: "What the SLAI Assistant can and cannot do here" },
         { label: "Agent", icon: "list", run: () => { void agentsDialog(store); }, note: "Which SLAI agents are available, and which ones SPADRA uses" }]];
    }
  }
  private boolTool(operation: "union" | "intersect" | "subtract"): ToolDef {
    return { id: `bool_${operation}`, label: operation[0]!.toUpperCase() + operation.slice(1), icon: "boolean", hint: "", steps: [{ key: "a", kind: "region", prompt: "Click inside the first region" }, { key: "b", kind: "region", prompt: "Click inside the second region" }],
      build: (v) => [{ op: "region_boolean", args: { operation, a: v["a"], b: v["b"], replace: true } }] };
  }
  private measureTool(type: string, label: string, n: number): ToolDef {
    return { id: `measure_${type}`, label: `Measure ${label.toLowerCase()}`, icon: "pf-measure", hint: "", repeat: true, steps: Array.from({ length: n }, (_, i) => ({ key: `r${i}`, kind: "ref" as const, prompt: i === 0 ? "Click the first point or line" : "Click the second point or line" })),
      build: async (v) => { const r = await this.call(() => this.api.op<{ value?: number; signed?: number; unit?: string }>(this.sid, "readout", { type, refs: Array.from({ length: n }, (_, i) => String(v[`r${i}`])) })); if (r) { const val = r.result.value ?? r.result.signed ?? 0; this.say(`${label}: ${fmt(+val.toPrecision(10))}`); toast(`${label}: ${fmt(+val.toPrecision(10))}`, { kind: "info", timeoutMs: 8000 }); } return []; } };
  }
  private async arrayDialog(kind: "rect" | "polar" | "path"): Promise<void> {
    const fields: Field[] = kind === "rect" ? [{ key: "count_x", label: "Copies across", kind: "number", value: 3, min: 1 }, { key: "count_y", label: "Copies up", kind: "number", value: 1, min: 1 }, { key: "dx", label: "Spacing across", kind: "number", value: 20 }, { key: "dy", label: "Spacing up", kind: "number", value: 20 }]
      : kind === "polar" ? [{ key: "count", label: "Number of copies", kind: "number", value: 6, min: 2 }, { key: "cx", label: "Centre X", kind: "number", value: 0 }, { key: "cy", label: "Centre Y", kind: "number", value: 0 }, { key: "angle", label: "Total angle (degrees)", kind: "number", value: 360 }]
        : [{ key: "count", label: "Number of copies", kind: "number", value: 5, min: 2 }];
    const f = await formDialog(`${kind === "rect" ? "Rectangular" : kind === "polar" ? "Polar" : "Path"} array`, [...fields, { key: "linked", label: "Keep the pattern linked: it updates when the source or a parameter changes", kind: "check", value: true }], "Create array"); if (!f) return;
    const ids = this.sel().ids; const spec: Record<string, unknown> = kind === "rect" ? { count_x: f["count_x"], count_y: f["count_y"], dx: f["dx"], dy: f["dy"] } : kind === "polar" ? { count: f["count"], center: [f["cx"], f["cy"]], angle: f["angle"] } : { count: f["count"] };
    if (kind !== "path") { await this.op("array", { ids, kind, spec, linked: f["linked"] === true }, "Array"); return; }
    this.say("Click the line the copies follow."); this.startTool({ id: "array_path", label: "Array along path", icon: "pf-array", hint: "Click the line the copies follow.", steps: [{ key: "path", kind: "edge", prompt: "Click the path line" }],
      build: (v) => [{ op: "array", args: { ids, kind: "path", spec: { ...spec, path_edge: v["path"] }, linked: f["linked"] === true } }] });
  }
  private async dimStyleDialog(): Promise<void> {
    const names = this.view.params.styles ?? ["default"];
    const f = await formDialog("Dimension style", [{ key: "name", label: "Style", kind: "select", value: "default", options: names.map((n): [string, string] => [n, n]) },
      { key: "text_height", label: "Text height", kind: "number", value: 3.5, min: 0.1 }, { key: "arrow_size", label: "Arrow size", kind: "number", value: 2.5, min: 0.1 }, { key: "precision", label: "Decimals", kind: "number", value: this.view.render.precision, min: 0 },
      { key: "color", label: "Colour", kind: "text", value: "#F6F6F6" }, { key: "unit_label", label: "Show the unit after the number", kind: "check", value: true }], "Apply", "Changes this style; dimensions using it update.");
    if (!f) return; await this.op("dimension_style", { name: f["name"], style: { text_height: f["text_height"], arrow_size: f["arrow_size"], precision: f["precision"], color: f["color"], unit_label: f["unit_label"] } }, "Dimension style");
  }
  private assistantNote(): void {
    openDialog({ title: "Assistant", size: "narrow", noCancel: true, cancelLabel: "Close", actions: [{ label: "OK", kind: "primary", run: () => true }], body: h("div", {},
      h("p", { text: "SPADRA's Assistant works on the project's model, not on a profile being drawn, so it cannot draw or edit here." }),
      h("p", { text: "Everything in Profiler is done by Profiler's own checks and solver. Upload the profile, then ask the Assistant about the model." }),
      h("p", { class: "note", text: "Analyze → Agent lists which SLAI agents this installation really has." })) });
  }

  // ------------------------------------------------------------------------------------------------- rendering bits
  private panelHost(): PanelHost {
    return { view: () => this.view, canvas: this.canvas, selection: () => this.sel(), perform: (op, args, label) => this.op(op, args, label ?? op), say: (m) => this.say(m), currentLayer: this.layer, setLayer: (n) => { this.layer = n; this.renderPanels(); this.renderStatus(); },
      check: this.checkResult, checkPurpose: this.checkPurpose, runCheck: (p) => this.runCheck(p), repair: (a, ap) => this.repair(a, ap), openComment: (id) => this.commentDialog(id), zoomTo: (pts) => { void pts; this.canvas.fit(); } };
  }
  private renderTabs(): void {
    replace(this.tabsEl, RIBBONS.map((r) => h("button", { class: "pf-tab", type: "button", attrs: { role: "tab", "aria-selected": String(r === this.ribbon) }, text: r, on: { click: () => { this.ribbon = r; this.renderTabs(); this.renderRibbon(); } } })));
  }
  private renderRibbon(): void {
    replace(this.ribbonEl, this.groups(this.ribbon).map((g) => h("div", { class: "pf-group" }, ...g.map((b) => {
      const pressed = b.tool !== undefined && b.tool === this.toolId;
      const el: HTMLButtonElement = h("button", { class: "pf-tool", type: "button", disabled: b.enabled === false, attrs: { title: b.note ?? b.label, ...(b.tool ? { "aria-pressed": String(pressed) } : {}) }, on: { click: () => { void b.run(el); } } }, glyph(b.icon), h("span", { text: b.label }));
      return el;
    }))));
  }
  private renderPanels(): void {
    const host = this.panelHost();
    const tab = (cur: string, id: string, label: string, set: () => void): HTMLButtonElement => h("button", { class: "pf-ptab", type: "button", attrs: { role: "tab", "aria-selected": String(cur === id) }, text: label, on: { click: set } });
    replace(this.leftEl, h("div", { class: "pf-ptabs", attrs: { role: "tablist" } }, tab(this.leftTab, "layers", "Layers", () => { this.leftTab = "layers"; this.renderPanels(); }), tab(this.leftTab, "params", "Parameters", () => { this.leftTab = "params"; this.renderPanels(); })),
      this.leftTab === "layers" ? layersPanel(host) : parametersPanel(host));
    replace(this.rightEl, h("div", { class: "pf-ptabs", attrs: { role: "tablist" } }, tab(this.rightTab, "props", "Properties", () => { this.rightTab = "props"; this.renderPanels(); }), tab(this.rightTab, "cons", "Constraints", () => { this.rightTab = "cons"; this.renderPanels(); }),
      tab(this.rightTab, "check", "Check", () => { this.rightTab = "check"; this.renderPanels(); })), this.rightTab === "props" ? propertiesPanel(host) : this.rightTab === "cons" ? constraintsPanel(host) : checkPanel(host));
  }
}

export function openProfiler(): Promise<void> { return ProfilerWorkspace.launch(); }
