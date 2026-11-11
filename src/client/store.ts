/** Application state and every call to the server. Views render from this and never talk to the network themselves. */
import { createClient, ApiError, type Client } from "../../../tools/web/src/api.js";
import { openDialog, openMenu, promptDialog, toast, type MenuEntry } from "../../../tools/web/src/ui.js";
import { h } from "../../../tools/web/src/dom.js";
import { Emitter } from "../../../tools/web/src/util.js";
import { oneLine, present, presentUnexpected } from "./errors.js";
import { closedPolylinePoints, UNIT_MM } from "./geom2d.js";
import { importDialog } from "./interchange.js";
import type {
  AnimationData, Interop, PoseData, InspectResult, Job, LinkTarget, SyncStatus,
  Bootstrap, CheckResult, CommandResult, DrawingData, Level, OpenRef, ProjectSummary, Properties, ScheduleData, Scene, TreeNode, TreeResponse,
} from "./types.js";

export type Tool =
  | "select" | "line" | "polyline" | "rectangle" | "circle" | "dimension" | "text" | "wall" | "curtainwall"
  | "offset" | "trim" | "extend" | "fillet" | "mirror" | "hatch"
  | "door" | "window" | "floor" | "roof" | "beam" | "column" | "room" | "section" | "grid" | "stair" | "family" | "tag" | "rotate" | "extrude" | "void" | "sweep";

export type Selection =
  | { kind: "model"; ids: string[] }
  | { kind: "drawing"; view: string; ids: string[] }
  | { kind: "sheet"; id: string }
  | { kind: "level"; id: string }
  | null;

export interface Tab { ref: OpenRef; title: string; format: "SGEO" | "SDRW" | "Schedule" }
const formatOf = (ref: OpenRef): Tab["format"] => (ref.kind === "3d" ? "SGEO" : ref.kind === "schedule" ? "Schedule" : "SDRW");

/** A true/false setting kept in this browser only (a convenience for this person; the project does not depend on it). */
function readFlag(key: string, fallback: boolean): boolean {
  try { const v = window.localStorage.getItem(key); return v === null ? fallback : v === "1"; } catch { return fallback; }
}
function writeFlag(key: string, value: boolean): void {
  try { window.localStorage.setItem(key, value ? "1" : "0"); } catch { /* storage unavailable (private window): the setting lasts for this page only */ }
}

/** A number setting kept in this browser only; null when it was never set or storage is unavailable. */
function readNumber(key: string): number | null {
  try { const v = window.localStorage.getItem(key); const n = v === null ? NaN : Number(v); return Number.isFinite(n) && n > 0 ? n : null; } catch { return null; }
}
function writeNumber(key: string, value: number | null): void {
  try { if (value === null) window.localStorage.removeItem(key); else window.localStorage.setItem(key, String(value)); } catch { /* storage unavailable: the setting lasts for this page only */ }
}

export class Store {
  readonly api: Client = createClient({ slug: "spadra" });
  readonly changed = new Emitter<void>();
  /** The picture alone changed (the playhead moved); nothing in the project did. */
  readonly frame = new Emitter<void>();
  /** The playhead moved (the picture for it may still be on its way). */
  readonly time = new Emitter<void>();

  boot: Bootstrap | null = null;
  project: ProjectSummary | null = null;
  tree: TreeNode[] = [];
  levels: Level[] = [];
  tabs: Tab[] = [];
  active: number = -1;
  tool: Tool = "select";
  /** Whether the pointer snaps to the grid in the 2D and 3D views. Snapping to geometry (ends, midpoints, intersections) is separate and always on. */
  snapGrid = readFlag("spadra.snapGrid", true);
  showGrid = readFlag("spadra.showGrid", true);
  /** A fixed grid spacing in millimetres (the same for every 2D and 3D view and every drawing unit), or null for automatic: the 2D grid then follows the zoom. */
  gridSpacingMm: number | null = readNumber("spadra.gridSpacingMm");
  /** Whether the 3D view orbits about the selected element (otherwise about the point it looks at). */
  orbitSelection = readFlag("spadra.orbitSelection", true);
  /** What the next wall will be: height in metres, thickness in millimetres, and which line the drawn path is: the wall's near face (the right-hand one looking along
   *  the path), its centre, or its far face, plus an offset in millimetres toward the far side. Shared by the 2D and 3D wall tools. */
  wallOptions: { height: number; thickness: number; reference: "near" | "centre" | "far"; offset: number } = { height: 3.2, thickness: 250, reference: "centre", offset: 0 };
  /** One roof creation specification shared by plan and 3D modeling tools. */
  roofOptions: { kind: "flat" | "shed" | "gable" | "hip"; pitch: number; thickness: number } = { kind: "flat", pitch: 30, thickness: 200 };
  /** What the Extrude, Void and Sweep tools make: depth and base in metres above the level, the sweep's profile in millimetres. */
  formOptions: {
    depth: number; base: number; voidDepth: number; voidBase: number; sweepWidth: number; sweepHeight: number; sweepShape: "rectangle" | "round" | "user"; sweepBase: number;
    /** A user-defined sweep profile: the closed polygon picked in the drawing (drawing units) and how it sits on the path. */
    sweepPolygon: Array<[number, number]> | null; sweepPivot: string; sweepAngle: number; sweepOffset: [number, number, number]; sweepMirror: boolean; sweepRadius: number;
  } = { depth: 1, base: 0, voidDepth: 4, voidBase: -0.5, sweepWidth: 200, sweepHeight: 200, sweepShape: "rectangle", sweepBase: 0,
    sweepPolygon: null, sweepPivot: "center", sweepAngle: 0, sweepOffset: [0, 0, 0], sweepMirror: false, sweepRadius: 0 };
  selection: Selection = null;
  properties: Properties | null = null;
  drawing: DrawingData | null = null;
  scene: Scene | null = null;
  schedule: ScheduleData | null = null;
  /** The animation of the model, and the second being shown. Time 0 is the model as built. */
  animation: AnimationData | null = null;
  animTime = 0;
  /** The selected element's pose at ``animTime`` (null when nothing animatable is selected or no animation exists). */
  pose: PoseData | null = null;
  /** While true, dragging the selected element in the 3D view makes a key instead of orbiting. Set by the timeline. */
  dragToKey = false;
  /** Playback is running: poses are not read for every frame. */
  playing = false;
  /** Which file conversions this installation can do (asked once per project load). */
  interop: Interop | null = null;
  check: CheckResult | null = null;
  /** How the sheets and tags stand against the model; read after every change. */
  sync: SyncStatus | null = null;
  assistantDismissedFor = -1;
  status = "Ready";
  busy = 0;
  /** Background operations (imports, exports, reports, checks, simulations) this session has started, newest first. */
  jobs: Job[] = [];
  /** The picture of the jobs changed; nothing in the project did. */
  readonly jobsChanged = new Emitter<void>();
  /** The family the Family tool places. */
  family: { id: string; name: string } | null = null;
  /** Opens a dialog the server names (a Project Browser row or a link in Properties). Set by the shell, which owns the dialogs. */
  openDialog: ((name: string, ref?: string) => void) | null = null;
  /** A sketch started from a door/window context menu. The closed SDRW contour remains editable in its drawing and
   *  is associated with the opening after the user's next closed polyline command succeeds. */
  private pendingOpeningSketch: { opening: string; drawing: string; replace: boolean } | null = null;

  /** Model-element commands are shared by the 2D and 3D viewports and Project Browser. The menu belongs to the clicked
   *  element, not whichever model item happened to be previously selected. The server owns every geometry operation. */
  openModelContextMenu(id: string, category: string, anchor: HTMLElement): void {
    const entries: MenuEntry[] = [{ label: "Select element", icon: "select", run: () => { void this.select({ kind: "model", ids: [id] }); } }];
    if (category === "roof") {
      entries.push("separator",
        { label: "Add Ridge Beam", icon: "beam", run: () => { void this.run("model.add_roof_accessory", { roof: id, kind: "ridge_beam" }); } },
        { label: "Add Ridge Strap", icon: "beam", run: () => { void this.run("model.add_roof_accessory", { roof: id, kind: "ridge_strap" }); } },
        { label: "Add Roof Edge", icon: "line", run: () => { void (async () => {
          const value = await promptDialog({ title: "Add roof edge", label: "Boundary edge number (1, 2, …) or all", value: "all", confirmLabel: "Add roof edge",
            validate: (s) => /^(all|[1-9]\d*)$/i.test(s.trim()) ? null : "Enter an edge number or all." });
          if (value !== null) await this.run("model.add_roof_accessory", { roof: id, kind: "roof_edge", edge: value.trim().toLowerCase() });
        })(); } });
    }
    if (category === "door" || category === "window") {
      entries.push("separator", { label: "Add Opening Profile", icon: "rectangle", run: () => { void this.standardOpeningProfile(id, category); } },
      { label: "Use Existing SDRW Profile…", icon: "polyline", run: () => { void this.chooseOpeningDrawingProfile(id); } },
      { label: "Sketch New SDRW Profile…", icon: "polyline", run: () => { void this.startOpeningProfileSketch(id); } },
      { label: "Import SDRW Profile into Drawing…", icon: "folder", run: () => { void this.importOpeningDrawingProfile(); } },
      "separator",
      { label: "Replace Opening Profile…", icon: "rectangle", run: () => { void this.standardOpeningProfile(id, category, true); } },
      { label: "Replace Opening Profile from SDRW…", icon: "polyline", run: () => { void this.chooseOpeningDrawingProfile(id, true); } },
      { label: "Sketch Replacement Profile…", icon: "polyline", run: () => { void this.startOpeningProfileSketch(id, true); } },
      { label: "Remove Opening Profile", icon: "close", run: () => { void this.run("model.remove_opening_profile", { opening: id }); } });
    }
    openMenu(anchor, entries, { label: `${category} actions` });
  }

  private async standardOpeningProfile(opening: string, category: string, replace = false): Promise<void> {
    const width = await promptDialog({ title: `${replace ? "Replace" : "Add"} ${category} opening profile`, label: "Profile face width (mm)", value: "50",
      confirmLabel: replace ? "Replace profile" : "Add profile",
      validate: (s) => Number.isFinite(Number(s)) && Number(s) >= 5 && Number(s) <= 250 ? null : "Enter 5–250 mm." });
    if (width !== null) await this.run("model.add_opening_profile", { opening, width: Number(width), replace });
  }

  private async importOpeningDrawingProfile(): Promise<void> {
    const drawing = flatten(this.tree).find((n) => n.open?.id === this.tab?.ref.id && n.open?.kind === "drawing")
      ?? flatten(this.tree).find((n) => n.open?.kind === "drawing");
    if (!drawing?.open) { toast("Create an SDRW drawing before importing a profile.", { kind: "info" }); return; }
    await this.openTab(drawing.open, drawing.label);
    toast("Import the SDRW file into this drawing, then right-click the opening and choose Use Existing SDRW Profile.", { kind: "info", timeoutMs: 9000 });
    await importDialog(this, "drawing");
  }

  /** Enumerate actual closed SDRW polygons, not arbitrary drawing objects: the model command validates the source again. */
  private async openingProfileSources(): Promise<Array<{ drawing: string; entity: string; label: string }>> {
    const sources: Array<{ drawing: string; entity: string; label: string }> = [];
    for (const node of flatten(this.tree)) {
      if (node.open?.kind !== "drawing") continue;
      // A referenced drawing may be temporarily unavailable. Keep usable views visible rather than rejecting the entire chooser.
      let data: DrawingData | null = null;
      try { data = this.drawing?.view.id === node.open.id ? this.drawing : await this.api.get<DrawingData>(`/drawing/${node.open.id}`); }
      catch { continue; }
      if (!data) continue;
      for (const entity of data.entities) {
        // Closed by its flag or drawn back to its first point; a drawing that is listed twice in the tree is offered once.
        if (!closedPolylinePoints(entity) || sources.some((s) => s.drawing === node.open?.id && s.entity === entity.id)) continue;
        sources.push({ drawing: node.open.id, entity: entity.id, label: `${node.label} · ${entity.layer} · ${entity.id.slice(0, 8)}` });
      }
    }
    return sources;
  }

  private async chooseOpeningDrawingProfile(opening: string, replace = false): Promise<void> {
    const sources = await this.openingProfileSources();
    if (!sources.length) {
      toast("No closed SDRW polyline is available. Draw a closed profile first, or use Sketch New SDRW Profile.", { kind: "info" });
      return;
    }
    const options = sources.map((source, index) => h("option", { value: String(index), text: source.label }));
    const select = h("select", { attrs: { "aria-label": "Closed SDRW profile" } }, ...options);
    const body = h("div", {}, h("label", { text: "Use a closed profile from a drawing" }), select,
      h("p", { class: "muted", text: "The model stores the drawing and entity reference; edit the source contour to retain a reusable, editable profile." }));
    const choice = await openDialog<number>({ title: replace ? "Replace opening profile" : "Use SDRW opening profile", size: "normal", body,
      actions: [{ label: replace ? "Replace profile" : "Add profile", kind: "primary", run: () => Number(select.value) }] }).result;
    if (choice === null) return;
    const source = sources[choice];
    if (source) await this.run("model.add_opening_profile", { opening, profile_drawing: source.drawing, profile_entity: source.entity, replace });
  }

  private async startOpeningProfileSketch(opening: string, replace = false): Promise<void> {
    const drawings = flatten(this.tree).filter((n) => n.open?.kind === "drawing");
    if (!drawings.length) { toast("Create an SDRW drawing before sketching an opening profile.", { kind: "info" }); return; }
    const selected = drawings.find((n) => n.open?.id === this.tab?.ref.id) ?? drawings[0];
    if (!selected?.open) return;
    this.pendingOpeningSketch = { opening, drawing: selected.open.id, replace };
    await this.openTab(selected.open, selected.label);
    this.setTool("polyline");
    toast("Sketch the profile cross-section in SDRW, then press C to close it. Escape cancels the association.", { kind: "info", timeoutMs: 9000 });
  }

  /** Called once at start-up: ask the server what exists. */
  async init(): Promise<void> {
    this.boot = await this.api.get<Bootstrap>("/bootstrap");
    this.project = this.boot.project;
    if (this.project) await this.loadProject(true);
    await this.loadJobs(true);                                       // the server keeps operations across a reload of the page
    this.changed.emit();
  }

  get tab(): Tab | null { return this.tabs[this.active] ?? null; }
  get unit(): "mm" | "cm" | "m" { return this.project?.unit ?? "mm"; }
  get saved(): boolean { return !!this.project && !this.project.dirty; }

  setStatus(text: string): void { this.status = text; this.changed.emit(); }

  /** Run a server call; any failure becomes a readable toast (what kind of problem, what happened, what to do) and a null result. */
  async guard<T>(work: () => Promise<T>, what?: string): Promise<T | null> {
    this.busy += 1;
    try {
      return await work();
    } catch (error) {
      if (error instanceof ApiError) {
        toast(oneLine(present(error, this.boot?.errors)), { kind: "error" });
        if (error.status === 409 && this.project) await this.refresh();
      } else {
        toast(oneLine(presentUnexpected(error, what ?? "That")), { kind: "error" });
      }
      return null;
    } finally {
      this.busy -= 1;
    }
  }

  // ------------------------------------------------------------------------------------------------------- project lifecycle
  async newProject(name: string, unit: string, discard = false): Promise<boolean> {
    const summary = await this.guard(() => this.api.post<ProjectSummary>("/project/new", { name, unit, discard }), "Creating the project");
    if (!summary) return false;
    this.project = summary;
    this.tabs = []; this.active = -1; this.selection = null; this.properties = null; this.drawing = null; this.scene = null;
    await this.loadProject(true);
    this.changed.emit();
    return true;
  }

  async openProject(path: string, discard = false): Promise<boolean> {
    const summary = await this.guard(() => this.api.post<ProjectSummary>("/project/open", { path, discard }), "Opening the project");
    if (!summary) return false;
    this.project = summary;
    this.tabs = []; this.active = -1; this.selection = null; this.properties = null; this.drawing = null; this.scene = null;
    await this.loadProject(true);
    for (const w of summary.warnings) toast(w, { kind: "info" });
    this.changed.emit();
    return true;
  }

  async save(): Promise<boolean> {
    if (!this.project) return false;
    const result = await this.guard(() => this.api.post<{ path: string; project: ProjectSummary }>("/project/save", this.project?.path ? {} : { name: this.project?.name }), "Saving");
    if (!result) return false;
    this.project = result.project;
    this.boot = this.boot ? { ...this.boot, recent: (await this.guard(() => this.api.get<Bootstrap>("/bootstrap")))?.recent ?? this.boot.recent } : this.boot;
    this.setStatus(`Saved to ${result.path}`);
    return true;
  }

  /** Save a copy under a new name and carry on with the copy. The server never overwrites another project. */
  async saveAs(name: string): Promise<boolean> {
    if (!this.project) return false;
    const result = await this.guard(() => this.api.post<{ path: string; project: ProjectSummary }>("/project/save", { name, save_as: true }), "Saving a copy");
    if (!result) return false;
    this.project = result.project;
    this.boot = this.boot ? { ...this.boot, recent: (await this.guard(() => this.api.get<Bootstrap>("/bootstrap")))?.recent ?? this.boot.recent } : this.boot;
    this.setStatus(`Saved a copy to ${result.path}`);
    return true;
  }

  /** Open one of this project's backups as a new, unsaved copy. The project it came from is not touched. */
  async recover(backup: string, discard = false): Promise<boolean> {
    const summary = await this.guard(() => this.api.post<ProjectSummary>("/project/recover", { backup, discard }), "Recovering the backup");
    if (!summary) return false;
    this.project = summary;
    this.tabs = []; this.active = -1; this.selection = null; this.properties = null; this.drawing = null; this.scene = null;
    await this.loadProject(true);
    for (const w of summary.warnings) toast(w, { kind: "info" });
    this.changed.emit();
    return true;
  }

  async close(discard: boolean): Promise<boolean> {
    const done = await this.guard(() => this.api.post<{ closed: boolean }>("/project/close", { discard }), "Closing");
    if (!done) return false;
    this.project = null; this.tree = []; this.levels = []; this.tabs = []; this.active = -1; this.selection = null; this.properties = null;
    this.drawing = null; this.scene = null; this.check = null; this.sync = null;
    this.boot = (await this.guard(() => this.api.get<Bootstrap>("/bootstrap"))) ?? this.boot;
    this.changed.emit();
    return true;
  }

  private async loadProject(openFirst: boolean): Promise<void> {
    const data = await this.guard(() => this.api.get<TreeResponse>("/project/tree"), "Reading the project");
    if (!data) return;
    this.project = data.project; this.tree = data.tree.sections; this.levels = data.levels;
    this.interop = await this.loadInterop();
    if (openFirst && this.tabs.length === 0) {
      // The tabs a project opens with: its 3D model and its first plan, as the screenshots show.
      const views = flatten(this.tree).filter((n) => n.open && n.open.kind !== "sheet");
      const model = views.find((n) => n.open?.kind === "3d");
      const plan = views.find((n) => n.open?.kind === "drawing");
      for (const n of [model, plan]) if (n?.open) this.tabs.push({ ref: n.open, title: n.label, format: formatOf(n.open) });
      this.active = this.tabs.length > 1 ? 1 : this.tabs.length - 1;
      await this.loadActive();
    }
    await this.loadCheck();
  }

  // ------------------------------------------------------------------------------------------------------------------ data
  /** Re-read everything that can have changed after an edit, undo, redo or another tab becoming active. */
  async refresh(): Promise<void> {
    if (!this.project) return;
    const data = await this.guard(() => this.api.get<TreeResponse>("/project/tree"));
    if (data) { this.project = data.project; this.tree = data.tree.sections; this.levels = data.levels; }
    // A tab whose view or sheet no longer exists is closed rather than left showing nothing.
    const nodes = flatten(this.tree);
    const ids = new Set(nodes.map((n) => n.open?.id).filter(Boolean));
    this.tabs = this.tabs.filter((t) => ids.has(t.ref.id));
    for (const t of this.tabs) t.title = nodes.find((n) => n.open?.id === t.ref.id)?.label ?? t.title;       // a renamed view keeps its tab in step
    this.active = Math.min(this.active, this.tabs.length - 1);
    await this.loadActive();
    this.dropVanishedSelection();
    await this.loadProperties();
    await this.loadCheck();
    this.changed.emit();
  }

  /** After an edit, undo or delete, forget selected things that no longer exist instead of asking the server about them. */
  private dropVanishedSelection(): void {
    const sel = this.selection;
    if (!sel || sel.kind === "sheet" || sel.kind === "level") return;
    if (sel.kind === "model" && this.animation?.animation) return;       // an element the animation hides is not in the picture but is still there; loadProperties drops a really deleted one
    const listed = (): string[] => flatten(this.tree).map((n) => n.select?.model).filter((id): id is string => !!id);      // elements with no solid (grids, arrays) are not in the 3D scene, but the browser lists them
    const live = sel.kind === "drawing" ? this.drawing?.entities.map((e) => e.id) : this.scene ? [...this.scene.objects.map((o) => o.id), ...listed()] : undefined;
    if (!live) return;                                   // that view is not the one on screen, so there is nothing to compare with
    const ids = sel.ids.filter((id) => live.includes(id));
    this.selection = ids.length ? { ...sel, ids } as Selection : null;
  }

  async loadActive(): Promise<void> {
    const tab = this.tab;
    this.drawing = null; this.scene = null; this.schedule = null;
    if (!tab) return;
    if (tab.ref.kind === "3d") await this.loadAnimation();
    if (tab.ref.kind === "schedule") this.schedule = await this.guard(() => this.api.get<ScheduleData>(`/schedule/${tab.ref.id}`), "Reading the schedule");
    else if (tab.ref.kind === "3d") { this.scene = await this.guard(() => this.api.get<Scene>("/scene", this.sceneQuery()), "Reading the model"); await this.loadPose(); }
    else if (tab.ref.kind === "drawing") this.drawing = await this.guard(() => this.api.get<DrawingData>(`/drawing/${tab.ref.id}`), "Reading the drawing");
  }

  /** ``time`` is asked for whenever an animation exists, including at 0: a key at the start may already differ from the model as built. */
  sceneQuery(time = this.animTime): Record<string, string> | undefined {
    return this.animation?.animation ? { time: String(time) } : undefined;
  }

  async loadAnimation(): Promise<void> {
    if (!this.project || !this.boot?.capabilities.animation) { this.animation = null; this.animTime = 0; return; }
    this.animation = await this.guard(() => this.api.get<AnimationData>("/animation"), "Reading the animation");
    const duration = this.animation?.animation?.duration ?? 0;
    if (!this.animation?.animation) this.animTime = 0;
    else if (this.animTime > duration) this.animTime = duration;
  }

  /** The frame length in seconds, from the project's frame rate. */
  get frameLength(): number { return 1 / (this.animation?.settings.fps || 24); }

  /** Show the model as the animation has it at ``seconds`` (snapped to a whole frame). Only the picture changes; nothing is edited. */
  async seek(seconds: number): Promise<void> {
    const duration = this.animation?.animation?.duration ?? this.animation?.settings.duration ?? 0;
    const fps = this.animation?.settings.fps || 24;
    this.animTime = Math.max(0, Math.min(duration, Math.round(Math.round(seconds * fps) / fps * 1e6) / 1e6));
    this.time.emit();
    if (this.tab?.ref.kind !== "3d") return;
    const scene = await this.guard(() => this.api.get<Scene>("/scene", this.sceneQuery()), "Reading the model");
    if (scene) { this.scene = scene; this.frame.emit(); }
    if (!this.playing) { await this.loadPose(); this.time.emit(); }
  }

  /** Read the selected element's pose at the playhead from the Animation Agent's evaluation. */
  async loadPose(): Promise<void> {
    const sel = this.selection?.kind === "model" && this.selection.ids.length === 1 ? this.selection.ids[0] : undefined;
    if (!sel || !this.animation?.agent.available || this.tab?.ref.kind !== "3d") { this.pose = null; return; }
    try { this.pose = await this.api.get<PoseData>("/animation/pose", { id: sel, time: String(this.animTime) }); } catch { this.pose = null; }     // a vanished element has no pose
  }

  /** One animation command (``anim.<name>``), carried out through the SLAI Animation Agent. One revision, one undo step. */
  async animate(name: string, params: Record<string, unknown>): Promise<CommandResult | null> {
    return this.run(`anim.${name}`, params);
  }

  /** One raw edit of the animation, carried out by the SLAI Animation Agent. */
  async editAnimation(operation: string, payload: Record<string, unknown>): Promise<boolean> {
    return !!(await this.run("anim.edit", { operation, payload }));
  }

  async loadProperties(): Promise<void> {
    const sel = this.selection;
    this.properties = null;
    let ref: Record<string, string> | null = null;
    if (!sel) {                                        // nothing selected: a plan, section, elevation or schedule describes itself
      const tab = this.tab;
      const own = tab?.ref.kind === "schedule" || (tab?.ref.kind === "drawing" && ["plan", "section", "elevation"].includes(this.drawing?.view.kind ?? ""));
      if (!tab || !own) return;
      ref = { view: tab.ref.id };
    } else if (sel.kind === "model" && sel.ids[0]) ref = { model: sel.ids[0] };
    else if (sel.kind === "drawing" && sel.ids[0]) ref = { drawing: sel.view, entity: sel.ids[0] };
    else if (sel.kind === "sheet") ref = { sheet: sel.id };
    else if (sel.kind === "level") ref = { level: sel.id };
    if (!ref) return;
    // The selected thing may be gone (deleted, or undone away). That is not an error to show: there is just nothing selected now.
    try {
      this.properties = await this.api.post<Properties>("/properties", ref);
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) { this.selection = null; this.properties = null; return; }
      this.selection = null;
      toast(error instanceof ApiError ? error.friendly : "Could not read the properties.", { kind: "error" });
    }
  }

  async loadCheck(): Promise<void> {
    this.check = await this.guard(() => this.api.get<CheckResult>("/check"));
    this.sync = await this.guard(() => this.api.get<SyncStatus>("/sync"));
  }

  // ----------------------------------------------------------------------------------------------------------- navigation
  async openTab(ref: OpenRef, title: string): Promise<void> {
    let index = this.tabs.findIndex((t) => t.ref.id === ref.id);
    if (index < 0) {
      this.tabs.push({ ref, title, format: formatOf(ref) });
      index = this.tabs.length - 1;
    }
    await this.activate(index);
  }

  async activate(index: number): Promise<void> {
    this.active = index;
    this.selection = null; this.properties = null;
    await this.loadActive();
    if (this.tab?.ref.kind === "sheet") this.selection = { kind: "sheet", id: this.tab.ref.id };
    await this.loadProperties();
    this.tool = "select";
    this.changed.emit();
  }

  async closeTab(index: number): Promise<void> {
    this.tabs.splice(index, 1);
    const next = Math.min(this.active >= index ? this.active - (this.active === index ? 0 : 1) : this.active, this.tabs.length - 1);
    await this.activate(next);
  }

  setTool(tool: Tool): void {
    if (this.pendingOpeningSketch && this.tool === "polyline" && tool !== "polyline") this.pendingOpeningSketch = null;
    this.tool = tool;
    this.changed.emit();
  }
  cancelPendingOpeningSketch(): void { this.pendingOpeningSketch = null; }
  setOrbitSelection(on: boolean): void { this.orbitSelection = on; writeFlag("spadra.orbitSelection", on); this.changed.emit(); }
  setShowGrid(on: boolean): void { this.showGrid = on; writeFlag("spadra.showGrid", on); this.changed.emit(); }
  setSnapGrid(on: boolean): void { this.snapGrid = on; writeFlag("spadra.snapGrid", on); this.changed.emit(); }
  /** Fix the grid spacing (millimetres, 0.001 to 1,000,000) or give null to let it follow the zoom. */
  setGridSpacingMm(mm: number | null): void {
    this.gridSpacingMm = mm !== null && Number.isFinite(mm) && mm >= 0.001 && mm <= 1e6 ? mm : null;
    writeNumber("spadra.gridSpacingMm", this.gridSpacingMm);
    this.changed.emit();
  }
  /** The fixed grid spacing in the open project's drawing unit, or null when automatic. */
  gridSpacing(): number | null { return this.gridSpacingMm === null ? null : this.gridSpacingMm / (UNIT_MM[this.unit] ?? 1); }

  async select(selection: Selection): Promise<void> {
    this.selection = selection;
    await this.loadProperties();
    await this.loadPose();
    this.changed.emit();
  }

  // --------------------------------------------------------------------------------------------------------------- edits
  /** Apply one command. The revision the user was looking at is sent so a stale view is refused rather than silently overwritten. */
  async run(op: string, params: Record<string, unknown>): Promise<CommandResult | null> {
    if (!this.project) return null;
    const rev = this.project.revision;
    const out = await this.guard(() => this.api.post<CommandResult>("/command", { op, params, expected_revision: rev }), "That change");
    if (!out) return null;
    this.project = out.project;
    if (op === "model.delete" && this.selection?.kind === "model") {      // the deleted element is no longer there to describe
      const gone = String(params["id"] ?? "");
      const left = this.selection.ids.filter((i) => i !== gone);
      this.selection = left.length ? { ...this.selection, ids: left } : null;
    }
    await this.refresh();
    if (op === "draw.polyline" && params["closed"] === true && this.pendingOpeningSketch?.drawing === params["drawing"] && typeof out.result["id"] === "string") {
      const pending = this.pendingOpeningSketch;
      this.pendingOpeningSketch = null;
      if (!pending) return out;
      const linked = await this.run("model.add_opening_profile", {
        opening: pending.opening, profile_drawing: pending.drawing, profile_entity: out.result["id"], replace: pending.replace,
      });
      if (linked) toast("Opening profile attached to the selected door or window.", { kind: "ok" });
    }
    return out;
  }

  async undo(): Promise<void> { await this.step("/undo"); }
  async redo(): Promise<void> { await this.step("/redo"); }
  private async step(path: "/undo" | "/redo"): Promise<void> {
    const out = await this.guard(() => this.api.post<{ label: string; project: ProjectSummary }>(path));
    if (!out) return;
    this.project = out.project;
    this.status = `${path === "/undo" ? "Undid" : "Redid"}: ${out.label}`;
    await this.refresh();
  }

  /** What conversions this installation can do. Asked when a menu needs it; a failure just means "unknown", and the menu then says so. */
  async loadInterop(): Promise<Interop | null> {
    try { return await this.api.get<Interop>("/interop"); } catch { return null; }
  }

  /** The file as base64 for the server, whose adapter does the parsing. Null (and a message) when the browser cannot read it. */
  async readFile(file: File): Promise<string | null> {
    const data = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(",", 2)[1] ?? "");
      reader.onerror = () => reject(reader.error ?? new Error("The file could not be read"));
      reader.readAsDataURL(file);
    }).catch(() => null);
    if (!data) toast("That file could not be read. Check that it still exists and can be opened, then choose it again.", { kind: "error" });
    return data;
  }

  /** Look into a file without importing it: its real type, whether it parses, its unit and contents, and anything suspicious. Changes nothing. */
  async inspect(target: "model" | "drawing", format: string, data: string, name: string, unit?: string): Promise<InspectResult | null> {
    return this.guard(() => this.api.post<InspectResult>("/import/inspect", { target, format, data, name, ...(unit ? { unit } : {}) }), "Checking the file");
  }

  // ---------------------------------------------------------------------------------------------------------- background jobs
  /** Read the operations the server holds (so a reloaded page still shows them). With ``follow``, those still running are followed to their end. */
  async loadJobs(follow = false): Promise<void> {
    let out: { jobs: Job[] };
    try { out = await this.api.get<{ jobs: Job[] }>("/jobs"); } catch { return; }          // a quiet refresh: a failure here is not the person's problem
    this.jobs = out.jobs;
    this.jobsChanged.emit();
    if (follow) for (const job of out.jobs) if (job.status === "queued" || job.status === "running") void this.waitFor(job, { refresh: true });
  }

  /** Start a background operation. The answer is immediate; ``waitFor`` follows it to the end. */
  async startJob(kind: string, params: Record<string, unknown>): Promise<Job | null> {
    const job = await this.guard(() => this.api.post<Job>("/jobs", { kind, params }), "Starting that");
    if (job) { this.jobs = [job, ...this.jobs.filter((j) => j.id !== job.id)]; this.jobsChanged.emit(); }
    return job;
  }

  /** Follow a job until it ends. A failure is shown once, as a toast with its category and what to do; the project is re-read when the job may have changed it. */
  async waitFor(job: Job, options: { refresh?: boolean } = {}): Promise<Job> {
    let now = job;
    while (now.status === "queued" || now.status === "running") {
      await new Promise((resolve) => setTimeout(resolve, 250));
      try { now = await this.api.get<Job>(`/jobs/${job.id}`); }
      catch (error) {
        if (error instanceof ApiError && error.status === 404) return { ...now, status: "cancelled", message: "It was dismissed" };
        toast(oneLine(error instanceof ApiError ? present(error, this.boot?.errors) : presentUnexpected(error, "Following that")), { kind: "error" });
        return now;
      }
      this.jobs = this.jobs.map((j) => (j.id === now.id ? now : j));
      this.jobsChanged.emit();
    }
    if (now.status === "failed" && now.error) toast(oneLine(now.error), { kind: "error", action: { label: "Details", run: () => this.openDialog?.("jobs") } });
    if (now.status === "cancelled") this.setStatus(`${now.label}: cancelled; nothing was changed`);
    if (options.refresh && now.status === "done") await this.refresh();
    return now;
  }

  /** Start a job and wait for it. */
  async runJob(kind: string, params: Record<string, unknown>, options: { refresh?: boolean } = {}): Promise<Job | null> {
    const job = await this.startJob(kind, params);
    return job ? this.waitFor(job, options) : null;
  }

  async cancelJob(id: string): Promise<void> {
    const job = await this.guard(() => this.api.post<Job>(`/jobs/${id}/cancel`), "Cancelling");
    if (job) { this.jobs = this.jobs.map((j) => (j.id === id ? job : j)); this.jobsChanged.emit(); }
  }

  async dismissJob(id: string): Promise<void> {
    if (await this.guard(() => this.api.post<{ dismissed: boolean }>(`/jobs/${id}/dismiss`), "Dismissing")) { this.jobs = this.jobs.filter((j) => j.id !== id); this.jobsChanged.emit(); }
  }

  get activeJobs(): Job[] { return this.jobs.filter((j) => j.status === "queued" || j.status === "running"); }

  /** Import a file (already inspected) as a background operation. Returns the finished job; its result holds the count and the conversion report. */
  async importFile(kind: "model" | "drawing", params: { format: string; data: string; name: string; unit?: string }): Promise<Job | null> {
    let extra: Record<string, unknown> = {};
    if (kind === "drawing") {
      const tab = this.tab;
      if (!tab || tab.ref.kind !== "drawing") { toast("Open a drawing to import into.", { kind: "info" }); return null; }
      extra = { drawing: tab.ref.id };
    }
    return this.runJob(kind === "model" ? "import.model" : "import.drawing", { ...params, ...extra }, { refresh: true });
  }

  /** Export as a background operation, then hand the file over. The report says what the format could not carry. */
  async exportAs(kind: "model" | "drawing", format: string, options: { ids?: string[]; view?: string } = {}): Promise<{ blob: Blob; filename: string; job: Job } | null> {
    const params: Record<string, unknown> = { format, ...(options.ids?.length ? { ids: options.ids } : {}), ...(kind === "drawing" ? { view: options.view } : {}) };
    const job = await this.runJob(kind === "model" ? "export.model" : "export.drawing", params);
    if (!job || job.status !== "done") return null;
    const file = await this.guard(() => this.api.download(`/jobs/${job.id}/file`), "Collecting the exported file");
    void this.dismissJob(job.id);
    return file ? { ...file, job } : null;
  }

  /** The schedule as CSV (small and immediate, so not a background job). */
  async exportCsv(): Promise<{ blob: Blob; filename: string } | null> {
    const tab = this.tab;
    if (!tab || tab.ref.kind !== "schedule") { toast("Open a schedule to export it as CSV.", { kind: "info" }); return null; }
    return this.guard(() => this.api.download(`/export/schedule/${tab.ref.id}`), "Exporting");
  }

  /** The open sheet as a .sldc file (the sheet as read, plus everything it draws, so that opening the file gives the sheet back). */
  async exportSheet(previewPng?: string, previewSvg?: string): Promise<{ blob: Blob; filename: string } | null> {
    const tab = this.tab;
    if (!tab || tab.ref.kind !== "sheet") { toast("Open a sheet to export it as .sldc.", { kind: "info" }); return null; }
    if (!previewPng || !previewSvg) { toast("The printable vector sheet and its image preview are required for SLDC export.", { kind: "error" }); return null; }
    return this.guard(async () => {
      const result = await this.api.post<{ data: string; filename: string }>(`/export/sheet/${tab.ref.id}`, { preview_png: previewPng, preview_svg: previewSvg, revision: this.project?.revision });
      const bytes = Uint8Array.from(atob(result.data), (ch) => ch.charCodeAt(0));
      return { blob: new Blob([bytes], { type: "application/x-slai-sldc+zip" }), filename: result.filename };
    }, "Exporting the sheet");
  }

  /** Lay out the sheet's viewports so that none overlaps another or the title block; what is locked or placed by hand stays where it is unless ``all``. */
  async arrangeSheet(id: string, all = false, rescale = false): Promise<boolean> {
    const done = await this.run("sheet.arrange", { id, mode: all ? "all" : "auto", rescale });
    if (done) toast("The viewports are arranged. Undo puts them back.", { kind: "ok" });
    return !!done;
  }

  // ------------------------------------------------------------------------------------------------------------------- links
  /** Follow a link from Properties or the Project Browser: open a view or sheet, select an element or a drawing entity, or open a dialog. */
  async follow(target: LinkTarget): Promise<void> {
    if (target.dialog) { this.openDialog?.(target.dialog); return; }
    if (target.open) {
      const node = flatten(this.tree).find((n) => n.open?.id === target.open?.id);
      await this.openTab(target.open, node?.label ?? "View");
    }
    const pick = target.select;
    if (pick?.model) {
      if (this.tab?.ref.kind !== "3d" && !target.open) {
        const model = flatten(this.tree).find((n) => n.open?.kind === "3d");
        if (model?.open) await this.openTab(model.open, model.label);
      }
      await this.select({ kind: "model", ids: [pick.model] });
    } else if (pick?.drawing && pick.entity) await this.select({ kind: "drawing", view: pick.drawing, ids: [pick.entity] });
  }

  /** The Project Browser row that stands for the current selection, so the browser can reveal it. */
  selectedNodeId(): string | null {
    const sel = this.selection;
    if (sel?.kind === "level") return sel.id;
    if (sel?.kind !== "model" || !sel.ids[0]) return null;
    return flatten(this.tree).find((n) => n.select?.model === sel.ids[0])?.id ?? null;
  }
}

export function flatten(nodes: TreeNode[]): TreeNode[] {
  const out: TreeNode[] = [];
  for (const n of nodes) { out.push(n); if (n.children) out.push(...flatten(n.children)); }
  return out;
}

/** The views, schedules, sheets and the 3D model that can be opened, each once. The Project Browser lists a view under Views, Levels and Drawings; a menu offers it once (first listing wins). */
export function openable(tree: TreeNode[]): TreeNode[] {
  const seen = new Set<string>();
  return flatten(tree).filter((n) => {
    const ref = n.open;
    if (!ref || ref.timeline || n.id.startsWith("model-") || n.id.startsWith("drawing-") || seen.has(ref.id)) return false;
    seen.add(ref.id);
    return true;
  });
}

export const store = new Store();
