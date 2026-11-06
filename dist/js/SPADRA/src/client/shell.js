/** The application shell: title bar, menu tabs, ribbon, document tabs, panels, status bar and the model-check toast. */
import { h, replace } from "../../../tools/web/src/dom.js";
import { glyph } from "../../../tools/web/src/icons.js";
import { promptDialog, saveBlob, toast } from "../../../tools/web/src/ui.js";
import { openMenu } from "./menus.js";
import { Browser, iconFor } from "./browser.js";
import { beamAnalysisDialog } from "./engineering.js";
import { AssistantPanel } from "./assistant.js";
import { agentsDialog, calculatorDialog, optimizationDialog, simulationDialog } from "./agents_ui.js";
import { quantitiesDialog } from "./quantities.js";
import { opensShortcutList, shortcutsDialog, toolHotkey } from "./shortcuts.js";
import { diagnosticsDialog, jobChip, jobsDialog } from "./jobs_ui.js";
import { exportDialog, importDialog } from "./interchange.js";
import { familiesDialog, issueDialog, levelsDialog, newReportDialog, reportDialog } from "./project_ui.js";
import { propertiesDialog, recoverDialog } from "./project_dialogs.js";
import { appearanceDialog, arrayDialog, releaseArray, ensureClean, newProjectDialog, openProjectDialog, reviewDialog } from "./dialogs.js";
import { downpipeDialog, drainageDialog, gutterDialog } from "./gutters.js";
import { foundationDialog, foundationReportDialog } from "./foundations.js";
import { renderProperties } from "./properties.js";
import { ScheduleView } from "./schedule.js";
import { SheetView } from "./sheet.js";
import { flatten, openable, store } from "./store.js";
import { DrawingView } from "./view2d.js";
import { ModelView } from "./view3d.js";
import { Timeline } from "./timeline.js";
const MENUS = ["File", "Edit", "View", "Insert", "Model", "Annotate", "Analyze", "Collaborate"];
const T = (id, label, icon, implemented, extra = {}) => ({ id, label, icon, implemented, ...extra });
const NOT_YET = "Not implemented yet.";
/** A control that is shown for the layout but does nothing yet, with the reason (or where the same thing is done today). */
const nope = (id, label, icon, note) => T(id, label, icon, false, note ? { note } : {});
export class Shell {
    menu = "Model";
    browser = new Browser(store);
    drawing = new DrawingView(store);
    model = new ModelView(store);
    sheet = new SheetView(store);
    schedule = new ScheduleView(store);
    assistant = new AssistantPanel(store);
    timeline = new Timeline(store, () => (scene) => this.model.capture(scene), () => this.model.hasWebGl);
    title = h("header", { class: "titlebar" });
    menubar = h("nav", { class: "menubar", attrs: { role: "tablist", "aria-label": "Menu" } });
    ribbon = h("div", { class: "ribbon", attrs: { role: "toolbar", "aria-label": "Tools" } });
    tabs = h("div", { class: "doc-tabs", attrs: { role: "tablist", "aria-label": "Open views" } });
    props = h("div", { class: "panel-body" });
    status = h("footer", { class: "statusbar" });
    toastEl = h("aside", { class: "assistant", hidden: true, attrs: { role: "status" } });
    area = h("div", { class: "canvas-area" });
    empty = h("div", { class: "empty-stage", text: "Open a view from the Project Browser." });
    el = h("div", { id: "app" });
    constructor() {
        this.area.append(this.drawing.el, this.model.el, this.sheet.el, this.schedule.el, this.empty);
        this.sheet.source = { camera: () => this.model.cameraDirection() };
        const stage = h("div", { class: "stage" }, this.tabs, this.area, this.timeline.el);
        const right = h("section", { class: "panel", attrs: { "aria-label": "Properties" } }, h("div", { class: "panel-head" }, h("span", { text: "Properties" })), this.props);
        this.el.append(this.title, this.menubar, this.ribbon, h("main", { class: "workspace" }, this.browser.el, stage, right), this.status, this.toastEl, this.assistant.el);
        store.openDialog = (name, ref) => this.openNamed(name, ref);
        store.changed.on(() => this.render());
        store.jobsChanged.on(() => this.renderStatus());
        store.frame.on(() => this.model.setScene(store.scene)); // the playhead moved: redraw the picture only
        store.time.on(() => this.timeline.syncTime());
        window.addEventListener("keydown", (e) => this.shortcut(e));
        window.addEventListener("beforeunload", (e) => { if (store.project?.dirty) {
            e.preventDefault();
        } });
        this.render();
    }
    // ---------------------------------------------------------------------------------------------------------- commands
    async save() { if (store.project && (await store.save()))
        toast("Project saved.", { kind: "ok", timeoutMs: 2500 }); }
    async saveAs() {
        if (!store.project)
            return;
        const name = await promptDialog({ title: "Save As", label: "Name of the copy", value: `${store.project.name} copy`, confirmLabel: "Save a copy", maxLength: 80,
            validate: (v) => (v.trim() ? null : "Enter a name.") });
        if (name && (await store.saveAs(name.trim())))
            toast(`Saved as “${store.project.name}”.`, { kind: "ok", timeoutMs: 2500 });
    }
    async newSheet() {
        const name = await promptDialog({ title: "New sheet", label: "Sheet name", placeholder: "Level 01 Plan & Section", confirmLabel: "Create" });
        if (name === null)
            return;
        const out = await store.run("sheet.create", { name });
        const id = out?.result["id"];
        if (typeof id === "string") {
            const node = flatten(store.tree).find((n) => n.open?.id === id);
            if (node?.open)
                await store.openTab(node.open, node.label);
        }
    }
    /** Create a view (an elevation or a schedule) and open it. */
    async addView(op, params, kind) {
        const out = await store.run(op, params);
        const made = out?.result;
        if (made?.id)
            await store.openTab({ kind, id: made.id }, made.name ?? "View");
    }
    /** Take a picture of the 3D view as it is now and put it on a sheet (an existing one, or a new one). */
    snapshotMenu(anchor) {
        const sheets = (flatten(store.tree).find((n) => n.id === "sheets")?.children ?? []).filter((n) => n.open?.kind === "sheet");
        const place = async (sheet, title) => {
            const picture = this.model.snapshot();
            if (!picture) {
                toast("The 3D view could not draw a picture. It needs WebGL 2, which this browser does not provide.", { kind: "error" });
                return;
            }
            const out = await store.run("sheet.place_snapshot", { id: sheet, data: picture, camera: this.model.cameraDirection(), title });
            if (!out)
                return;
            const node = flatten(store.tree).find((n) => n.open?.id === sheet);
            if (node?.open)
                await store.openTab(node.open, node.label);
        };
        const entries = [...sheets.map((n) => ({ label: `On sheet ${n.label}`, icon: "page", run: () => { void place(n.open?.id ?? "", ""); } })),
            { label: "On a new sheet…", icon: "page", run: () => {
                    void (async () => {
                        const name = await promptDialog({ title: "New sheet", label: "Sheet name", placeholder: "3D views", confirmLabel: "Create and place" });
                        if (name === null)
                            return;
                        const made = await store.run("sheet.create", { name });
                        const id = made?.result["id"];
                        if (typeof id === "string")
                            await place(id, "");
                    })();
                } }];
        openMenu(anchor, entries, { label: "Snapshot of the 3D view" });
    }
    elevationMenu(anchor) {
        const have = new Set(flatten(store.tree).filter((n) => n.view_kind === "elevation").map((n) => n.label.toLowerCase()));
        const sides = ["north", "south", "east", "west"].filter((side) => !have.has(side));
        const entries = sides.length
            ? sides.map((side) => ({ label: side[0]?.toUpperCase() + side.slice(1), icon: "elevation", run: () => { void this.addView("view.add_elevation", { side }, "drawing"); } }))
            : [{ label: "All four elevations already exist", disabled: true, run: () => undefined }];
        openMenu(anchor, entries, { label: "New elevation" });
    }
    scheduleMenu(anchor) {
        const have = new Set(flatten(store.tree).filter((n) => n.view_kind === "schedule").map((n) => n.label));
        const kinds = [["door", "Door Schedule"], ["window", "Window Schedule"], ["wall", "Wall Schedule"], ["floor", "Floor Schedule"], ["roof", "Roof Schedule"], ["gutter", "Gutter Schedule"], ["downpipe", "Downpipe Schedule"], ["foundation", "Foundation Schedule"], ["structural", "Beam & Column Schedule"], ["room", "Room Schedule"]];
        const entries = kinds.filter(([, label]) => !have.has(label)).map(([category, label]) => ({ label, icon: "table", run: () => { void this.addView("view.add_schedule", { category }, "schedule"); } }));
        openMenu(anchor, entries.length ? entries : [{ label: "Every schedule already exists", disabled: true, run: () => undefined }], { label: "New schedule" });
    }
    async deleteView() {
        const tab = store.tab;
        if (!tab || tab.ref.kind === "3d" || tab.ref.kind === "sheet")
            return;
        await store.run("view.delete", { id: tab.ref.id });
    }
    /** The dialogs the server and the other panels ask for by name (a Properties link, a Project Browser row, a failed job's "Details"). */
    openNamed(name, ref) {
        const sheet = ref ?? (store.selection?.kind === "sheet" ? store.selection.id : undefined);
        switch (name) {
            case "families":
                void familiesDialog(store, ref);
                break;
            case "levels":
                levelsDialog(store);
                break;
            case "report":
                if (ref)
                    void reportDialog(store, ref);
                break;
            case "issue":
                if (sheet)
                    issueDialog(store, sheet);
                break;
            case "arrange":
                if (sheet)
                    void store.arrangeSheet(sheet);
                break;
            case "jobs":
                jobsDialog(store);
                break;
            case "diagnostics":
                void diagnosticsDialog(store);
                break;
            default: toast(`There is no “${name}” dialog.`, { kind: "error" });
        }
    }
    async exportSheetFile() {
        const file = await store.exportSheet();
        if (file) {
            saveBlob(file.blob, file.filename);
            store.setStatus(`Exported ${file.filename}`);
        }
    }
    async exportCsv() {
        const file = await store.exportCsv();
        if (file) {
            saveBlob(file.blob, file.filename);
            store.setStatus(`Exported ${file.filename}`);
        }
    }
    async closeProject() { if (await ensureClean(store))
        await store.close(true); }
    shortcut(e) {
        const t = e.target;
        const field = (x) => x instanceof HTMLInputElement || x instanceof HTMLSelectElement || x instanceof HTMLTextAreaElement;
        if (opensShortcutList(e, field)) {
            e.preventDefault();
            shortcutsDialog();
            return;
        }
        if (field(t))
            return;
        if (e.key === "F9" && !e.ctrlKey && !e.metaKey && !e.altKey && store.project) {
            e.preventDefault();
            store.setSnapGrid(!store.snapGrid);
            return;
        }
        if (!(e.ctrlKey || e.metaKey)) {
            this.plainKey(e);
            return;
        }
        const k = e.key.toLowerCase();
        if (k === "z" && !e.shiftKey) {
            e.preventDefault();
            void store.undo();
        }
        else if ((k === "z" && e.shiftKey) || k === "y") {
            e.preventDefault();
            void store.redo();
        }
        else if (k === "s") {
            e.preventDefault();
            void (e.shiftKey ? this.saveAs() : this.save());
        }
    }
    /** Single keys: a tool by letter, "/" for search, 2 and 3 for the view mode, F to fit. Nothing happens while a dialog is open or a modifier is held. */
    plainKey(e) {
        if (e.altKey || e.defaultPrevented || document.querySelector("dialog[open]") || !store.project)
            return;
        const tool = toolHotkey(e);
        if (tool) {
            // The tool may live on another tab (Grid is under Insert, Tag under Annotate): show that tab so the pressed tool is visible.
            const home = [this.menu, "Model", "Insert", "Annotate"].find((m) => this.tools(m).flat().some((d) => d.tool === tool && d.implemented));
            if (home) {
                e.preventDefault();
                this.menu = home;
                store.setTool(tool);
                this.drawing.reset();
            }
            return;
        }
        if (e.key === "/") {
            e.preventDefault();
            this.browser.focusSearch();
        }
        else if (e.key === "2") {
            e.preventDefault();
            this.goto2D();
        }
        else if (e.key === "3") {
            e.preventDefault();
            this.goto3D();
        }
        else if (e.key === "f" || e.key === "F") {
            const kind = store.tab?.ref.kind;
            if (kind === "3d" || kind === "drawing") {
                e.preventDefault();
                if (kind === "3d")
                    this.model.fit();
                else
                    this.drawing.fit();
            }
        }
    }
    goto2D() {
        const index = store.tabs.findIndex((t) => t.ref.kind !== "3d");
        if (index >= 0) {
            void store.activate(index);
            return;
        }
        const plan = flatten(store.tree).find((n) => n.open?.kind === "drawing");
        if (plan?.open)
            void store.openTab(plan.open, plan.label);
    }
    goto3D() {
        const index = store.tabs.findIndex((t) => t.ref.kind === "3d");
        if (index >= 0) {
            void store.activate(index);
            return;
        }
        const m = flatten(store.tree).find((n) => n.open?.kind === "3d");
        if (m?.open)
            void store.openTab(m.open, m.label);
    }
    // -------------------------------------------------------------------------------------------------------------- menus
    fileMenu(anchor) {
        const has = !!store.project;
        const entries = [
            { label: "New project…", icon: "plus", run: () => { void newProjectDialog(store); } },
            { label: "Open project…", icon: "folder", run: () => { void openProjectDialog(store); } },
            { label: "Save", icon: "save", disabled: !has, run: () => { void this.save(); } },
            { label: "Save As…", icon: "save", disabled: !has, run: () => { void this.saveAs(); } },
            { label: "Close project", icon: "close", disabled: !has, run: () => { void this.closeProject(); } },
            "separator",
            { label: "Keyboard shortcuts…", icon: "info", run: () => shortcutsDialog() },
            { label: "Project properties…", icon: "info", disabled: !has, run: () => { void propertiesDialog(store, () => { void recoverDialog(store); }); } },
            { label: "Recover from a backup…", icon: "folder", disabled: !has, run: () => { void recoverDialog(store); } },
            "separator",
            { label: "Import into the model…", icon: "folder", disabled: !has, run: () => { void importDialog(store, "model"); } },
            { label: "Import into the drawing…", icon: "folder", disabled: store.tab?.ref.kind !== "drawing", run: () => { void importDialog(store, "drawing"); } },
            { label: "Export the model…", icon: "export", disabled: !has, run: () => { void exportDialog(store, "model"); } },
            { label: "Export the drawing…", icon: "export", disabled: store.tab?.ref.kind !== "drawing", run: () => { void exportDialog(store, "drawing"); } },
            { label: "Export the schedule as CSV", icon: "export", disabled: store.tab?.ref.kind !== "schedule", run: () => { void this.exportCsv(); } },
            { label: "Export the sheet as .sldc", icon: "export", disabled: store.tab?.ref.kind !== "sheet", run: () => { void this.exportSheetFile(); } },
            "separator",
            { label: "Operations…", icon: "clock", run: () => jobsDialog(store) },
            { label: "Diagnostics…", icon: "info", run: () => { void diagnosticsDialog(store); } },
        ];
        openMenu(anchor, entries, { label: "File" });
    }
    /** Turn automatic rooms on or off for the project; turning them on makes rooms for the spaces the walls already close. */
    async toggleAutoRooms() {
        const on = !store.project?.auto_rooms;
        const out = await store.run("model.set_auto_rooms", { on });
        const made = out?.result?.rooms;
        store.setStatus(on ? (made && made.added ? `Automatic rooms on: ${made.added} room${made.added === 1 ? "" : "s"} made` : "Automatic rooms on") : "Automatic rooms off: the rooms stay, but no longer follow the walls");
    }
    /** Attach the selected walls to the selected roof, or extend them to the next level up. The server derives their height and says why when it cannot. */
    async attachWalls(to) {
        const sel = store.selection;
        if (sel?.kind !== "model" || !sel.ids.length) {
            toast(to === "roof" ? "Select the walls and a roof first." : "Select the walls first.", { kind: "info" });
            return;
        }
        const out = await store.run("model.attach_wall", { ids: sel.ids, to });
        const walls = out?.result?.walls;
        if (walls)
            store.setStatus(`${walls.length} wall${walls.length === 1 ? "" : "s"} ${to === "roof" ? "attached to the roof" : "extended to the upper level"}. Their height now follows it.`);
    }
    async detachWalls(from) {
        const sel = store.selection;
        if (sel?.kind !== "model" || !sel.ids.length) {
            toast("Select the walls first.", { kind: "info" });
            return;
        }
        const out = await store.run("model.detach_wall", { ids: sel.ids, from });
        const walls = out?.result?.walls;
        if (walls)
            store.setStatus(`${walls.length} wall${walls.length === 1 ? "" : "s"} freed. They keep the height they had.`);
    }
    /** Turn the selected extrusions into voids: they then cut the floors, extrusions and walls they pass through. */
    async makeVoid() {
        const sel = store.selection;
        if (sel?.kind !== "model" || !sel.ids.length) {
            toast("Select an extrusion first, then choose Make void.", { kind: "info" });
            return;
        }
        const out = await store.run("model.make_void", { ids: sel.ids, void: true });
        const done = out?.result?.ids;
        if (done)
            store.setStatus(`${done.length} extrusion${done.length === 1 ? "" : "s"} made into void${done.length === 1 ? "" : "s"}. Properties shows what each one cuts.`);
    }
    tools(menu = this.menu) {
        const kind = store.tab?.ref.kind;
        const sel = T("select", "Select", "select", true, { tool: "select" });
        const onPlan = kind === "drawing" && store.drawing?.view.kind === "plan";
        const isDoc = (kind === "drawing" && (store.drawing?.view.kind === "section" || store.drawing?.view.kind === "elevation")) || kind === "schedule"; // a view made from the model
        const on3dOrPlan = kind === "3d" || onPlan;
        switch (menu) {
            case "Edit": return [[
                    T("undo", "Undo", "undo", true, { run: () => store.undo(), note: store.project?.can_undo ? store.project.undo_label ?? "" : "Nothing to undo" }),
                    T("redo", "Redo", "redo", true, { run: () => store.redo() }),
                    T("delview", "Delete view", "close", isDoc, { run: () => this.deleteView(), note: isDoc ? "Delete this section, elevation or schedule" : "Open a section, elevation or schedule to delete it" }),
                ]];
            case "View": return [[
                    T("home", "Home view", "home", kind === "3d", { run: () => this.model.home() }),
                    T("fit", "Zoom to fit", "fit", kind === "3d" || kind === "drawing", { run: () => { if (kind === "3d")
                            this.model.fit();
                        else
                            this.drawing.fit(); } }),
                    T("zin", "Zoom in", "zoomin", kind === "drawing", { run: () => this.drawing.zoom(1.5) }),
                    T("zout", "Zoom out", "zoomout", kind === "drawing", { run: () => this.drawing.zoom(1 / 1.5) }),
                    T("proj", this.model.cam.ortho ? "Perspective" : "Orthographic", "cube", kind === "3d", { run: () => this.model.setOrtho(!this.model.cam.ortho) }),
                    T("snap", store.snapGrid ? "Grid snap: on" : "Grid snap: off", "grid", kind === "3d" || kind === "drawing", { run: () => store.setSnapGrid(!store.snapGrid), note: "Snap the pointer to the grid (F9)" }),
                    T("theme", "Appearance", "palette", true, { run: () => appearanceDialog() }),
                ]];
            case "Insert": return [[T("wall", "Wall", "wall", on3dOrPlan, { tool: "wall" }), T("sheet", "New sheet", "page", !!store.project, { run: () => this.newSheet() }),
                    T("door", "Door", "door", on3dOrPlan, { tool: "door" }), T("window", "Window", "window", on3dOrPlan, { tool: "window" }),
                    T("floor", "Floor", "floor", on3dOrPlan, { tool: "floor" }), T("roof", "Roof", "roof", kind === "3d", { tool: "roof" }),
                    T("gutter", "Gutter", "gutter", !!store.project, { run: () => gutterDialog(store), note: "Select a roof, then hang gutters (and downpipes) on its eaves" }),
                    T("downpipe", "Downpipe", "downpipe", !!store.project, { run: () => downpipeDialog(store), note: "Select a gutter, then add a downpipe under it" }),
                    T("drainage", "Drainage", "report", !!store.project, { run: () => drainageDialog(store), note: "The rain each roof sends to its gutters and downpipes" }),
                    T("foundation", "Foundation", "foundation", !!store.project, { run: () => { void foundationDialog(store); }, note: "Pads, strips, foundation beams, rafts and ground-bearing slabs; select walls or columns to stand them on" }),
                    T("foundationqty", "Foundation quantities", "report", !!store.project, { run: () => { void foundationReportDialog(store); }, note: "Concrete, build-up, thermal resistance and indicative bars of the selected foundation" }),
                    T("beam", "Beam", "beam", kind === "3d", { tool: "beam" }), T("column", "Column", "column", on3dOrPlan, { tool: "column" }),
                    T("room", "Room", "room", onPlan, { tool: "room" }),
                    T("autorooms", store.project?.auto_rooms ? "Auto rooms: on" : "Auto rooms: off", "room", !!store.project, { run: () => this.toggleAutoRooms(), note: "Make a room wherever walls close a space (no roof needed), and keep it in step with the walls" }),
                    T("stair", "Stair", "stair", onPlan, { tool: "stair" }), T("grid", "Grid", "grid", onPlan, { tool: "grid" }),
                    T("family", "Family", "family", onPlan, { tool: "family" })],
                [T("extrude", "Extrude", "extrude", onPlan, { tool: "extrude", note: "Draw an outline and stand it up to a depth" }),
                    T("void", "Void", "void", onPlan, { tool: "void", note: "Draw an outline that cuts the floors, extrusions and walls it passes through" }),
                    T("sweep", "Sweep", "sweep", onPlan, { tool: "sweep", note: "Carry a profile along a path drawn in plan" }),
                    T("attachroof", "Attach to roof", "attach-roof", !!store.project, { run: () => this.attachWalls("roof"), note: "Select the walls and a roof: the top of each wall follows the underside of the roof" }),
                    T("detachroof", "Detach from roof", "detach-roof", !!store.project, { run: () => this.detachWalls("roof"), note: "Free the selected walls from the roof; they keep the height they had" }),
                    T("extendlevel", "Extend to upper level", "extend-level", !!store.project, { run: () => this.attachWalls("level"), note: "Raise the selected walls to the next level up and keep them there" }),
                    T("disconnectlevel", "Disconnect from level", "disconnect-level", !!store.project, { run: () => this.detachWalls("level"), note: "Free the selected walls from the level; they keep the height they had" }),
                    T("makevoid", "Make void", "void", !!store.project, { run: () => this.makeVoid(), note: "Turn the selected extrusions into voids that cut" }),
                    T("array", "Array", "array", !!store.project, { run: () => arrayDialog(store), note: "Repeat the selection along X, Y, Z or round a point, and keep the pattern editable" }), T("releasearray", "Release array", "array", !!store.project, { run: () => releaseArray(store), note: "Keep the copies of the selected array as ordinary elements and drop the pattern" })],
                [T("section", "Section", "section", on3dOrPlan, { tool: "section" }), T("elevation", "Elevation", "elevation", !!store.project, { run: () => this.elevationMenu(this.ribbon) }),
                    T("schedule", "Schedule", "table", !!store.project, { run: () => this.scheduleMenu(this.ribbon) })],
                [T("levels", "Levels", "levels", !!store.project, { run: () => levelsDialog(store), note: "Add, rename or move the building's levels" }),
                    T("families", "Families", "family", !!store.project, { run: () => { void familiesDialog(store); }, note: "Define parametric families and see where they are used" })]];
            case "Annotate": return [[T("dim", "Dimension", "dimension", kind === "drawing", { tool: "dimension" }), T("text", "Text", "text", kind === "drawing", { tool: "text" }),
                    T("tag", "Tag", "tag", kind === "drawing", { tool: "tag", note: "Tag an element: its mark, name, type or size follows the model" })]];
            case "Analyze": {
                const beam = store.selection?.kind === "model" && store.properties?.kind === "model" && store.properties.category === "structural" ? store.selection.ids[0] : undefined;
                const engineering = !!store.boot?.capabilities.engineering;
                const simulation = !!store.boot?.capabilities.simulation, optimization = !!store.boot?.capabilities.optimization, calculator = !!store.boot?.capabilities.calculator;
                const picked = store.selection?.kind === "model" ? store.selection.ids : [];
                return [[T("check", "Check model", "check-circle", !!store.project, { run: async () => { await store.loadCheck(); reviewDialog(store); } }),
                        T("report", "Report", "report", !!store.project, { run: () => newReportDialog(store), note: "A snapshot of the project as an SLDC document" }),
                        T("rebuild", "Rebuild", "refresh", !!store.project, { run: async () => { const job = await store.runJob("rebuild", {}, { refresh: true }); if (job?.status === "done")
                                store.setStatus("Model rebuilt"); }, note: "Regenerate every element from its parameters" }),
                        T("quantities", "Quantities", "table", !!store.project, { run: () => quantitiesDialog(store, picked.length ? picked : null, picked.length ? `${picked.length} selected` : "Whole model"),
                            ...(store.project ? {} : { note: "Open a project first." }) })],
                    [T("beamcalc", "Beam analysis", "beam", engineering && !!beam, { run: () => { if (beam)
                                beamAnalysisDialog(store, beam, store.properties?.title ?? "Beam"); },
                            ...(engineering && beam ? {} : { note: engineering ? "Select a beam first." : "Needs the SLAI STEM Agent in this installation." }) }),
                        T("simulate", "Simulation", "clock", simulation && !!beam, { run: () => { if (beam)
                                simulationDialog(store, beam, store.properties?.title ?? "Beam"); },
                            note: simulation ? (beam ? "Dynamic response of the selected beam" : "Select a beam first.") : "Needs the SLAI Simulation and STEM Agents in this installation." }),
                        T("optimize", "Optimization", "filter", optimization && !!beam, { run: () => { if (beam)
                                optimizationDialog(store, beam, store.properties?.title ?? "Beam"); },
                            note: optimization ? (beam ? "Smallest section that meets the deflection limit" : "Select a beam first.") : "Needs the SLAI Optimization and STEM Agents in this installation." }),
                        T("calculator", "Calculator", "database", calculator && !!store.project, { run: () => calculatorDialog(store), note: calculator ? "Units, formulas, constraints and measurements" : "Needs the SLAI STEM Agent in this installation." })],
                    [T("assistant", "Assistant", "info", !!store.project && !!store.boot?.capabilities.assistant, { run: () => { void this.assistant.toggle(); }, note: "Ask about the model or describe a change" }),
                        T("agents", "Agents", "list", true, { run: () => { void agentsDialog(store); }, note: "Which SLAI agents SPADRA uses, and why the others are not" })]];
            }
            case "Collaborate": return [[nope("share", "Share", "link"), nope("comment", "Comments", "note")]];
            case "File": return [];
            default: break;
        }
        // Model: the tools of the active view, as in the design.
        if (kind === "3d")
            return [[sel], [T("wall", "Wall", "wall", true, { tool: "wall" }), T("door", "Door", "door", true, { tool: "door" }), T("window", "Window", "window", true, { tool: "window" })],
                [T("floor", "Floor", "floor", true, { tool: "floor" }), T("roof", "Roof", "roof", true, { tool: "roof" }),
                    T("gutter", "Gutter", "gutter", true, { run: () => gutterDialog(store), note: "Select a roof, then hang gutters (and downpipes) on its eaves" }),
                    T("downpipe", "Downpipe", "downpipe", true, { run: () => downpipeDialog(store), note: "Select a gutter, then add a downpipe under it" }),
                    T("drainage", "Drainage", "report", true, { run: () => drainageDialog(store), note: "The rain each roof sends to its gutters and downpipes" }),
                    T("foundation", "Foundation", "foundation", true, { run: () => { void foundationDialog(store); }, note: "Pads, strips, foundation beams, rafts and ground-bearing slabs; select walls or columns to stand them on" })], [T("beam", "Beam", "beam", true, { tool: "beam" }), T("column", "Column", "column", true, { tool: "column" })], [nope("dim3", "Dimension", "dimension")],
                [T("makevoid", "Make void", "void", true, { run: () => this.makeVoid(), note: "Turn the selected extrusions into voids that cut" }), T("array", "Array", "array", true, { run: () => arrayDialog(store), note: "Repeat the selection along X, Y, Z or round a point, and keep the pattern editable" }), T("releasearray", "Release array", "array", !!store.project, { run: () => releaseArray(store), note: "Keep the copies of the selected array as ordinary elements and drop the pattern" })],
                [T("snapshot", "Snapshot", "cube", true, { run: () => this.snapshotMenu(this.ribbon), note: "Put a picture of this 3D view on a sheet" })],
                [T("section", "Section", "section", true, { tool: "section" }), T("elevation", "Elevation", "elevation", true, { run: () => this.elevationMenu(this.ribbon) }), T("schedule", "Schedule", "table", true, { run: () => this.scheduleMenu(this.ribbon) })]];
        if (kind === "schedule")
            return [[T("csv", "Export CSV", "export", true, { run: () => this.exportCsv() }), T("delview", "Delete view", "close", true, { run: () => this.deleteView() })],
                [T("newschedule", "Schedule", "table", true, { run: () => this.scheduleMenu(this.ribbon) })]];
        if (kind === "sheet") {
            const sheet = store.tab?.ref.id;
            return [[sel], [T("placeview", "Place View", "place-view", true, { run: () => this.sheet.placeViewMenu(this.ribbon) }),
                    T("arrange", "Arrange", "place-view", !!sheet, { run: () => { if (sheet)
                            void store.arrangeSheet(sheet); }, note: "Lay the viewports out so none overlaps another or the title block. Locked ones and ones you placed by hand stay put" }),
                    T("exportsheet", "Export .sldc", "export", !!sheet, { run: () => this.exportSheetFile(), note: "Save this sheet as a .sldc file that keeps everything on it" })],
                [nope("viewport", "Viewport", "viewport", "Select a viewport on the sheet and edit its scale in Properties")],
                [nope("titleblock", "Title Block", "title-block", "Edit the title block in Properties with the sheet selected")],
                [nope("stext", "Text", "text", "Add notes to the sheet from Properties")],
                [nope("sdim", "Dimension", "dimension"), nope("legend", "Legend", "legend"),
                    T("revision", "Revision", "revision", !!sheet, { run: () => { if (sheet)
                            issueDialog(store, sheet); }, note: "Issue a revision of this sheet" })],
                [T("print", "Print / PDF", "print", true, { run: () => this.sheet.print(), note: "Print the sheet, or choose Save as PDF in the print dialog" })]];
        }
        return [[sel], [T("line", "Line", "line", kind === "drawing", { tool: "line" }), T("polyline", "Polyline", "polyline", kind === "drawing", { tool: "polyline" }),
                T("rectangle", "Rectangle", "rectangle", kind === "drawing", { tool: "rectangle" }), T("circle", "Circle", "circle", kind === "drawing", { tool: "circle" }),
                T("offset", "Offset", "offset", kind === "drawing", { tool: "offset" }), T("trim", "Trim", "trim", kind === "drawing", { tool: "trim" }),
                T("extend", "Extend", "extend", kind === "drawing", { tool: "extend" }), T("fillet", "Fillet", "fillet", kind === "drawing", { tool: "fillet" }),
                T("mirror", "Mirror", "mirror", kind === "drawing", { tool: "mirror" }), T("hatch", "Hatch", "hatch", kind === "drawing", { tool: "hatch" }),
                T("rotate", "Rotate", "rotate", onPlan, { tool: "rotate", note: "Turn the selected walls, columns, floors, rooms … about a point you pick" }),
                T("array", "Array", "array", !!store.project, { run: () => arrayDialog(store), note: "Repeat the selection along X, Y, Z or round a point, and keep the pattern editable" }), T("releasearray", "Release array", "array", !!store.project, { run: () => releaseArray(store), note: "Keep the copies of the selected array as ordinary elements and drop the pattern" })],
            [T("dimension", "Dimension", "dimension", kind === "drawing", { tool: "dimension" }), T("text", "Text", "text", kind === "drawing", { tool: "text" })],
            [T("wall", "Wall", "wall", onPlan, { tool: "wall" }), T("door", "Door", "door", onPlan, { tool: "door" }), T("window", "Window", "window", onPlan, { tool: "window" }), T("floor", "Floor", "floor", onPlan, { tool: "floor" })],
            [T("extrude", "Extrude", "extrude", onPlan, { tool: "extrude" }), T("void", "Void", "void", onPlan, { tool: "void" }), T("sweep", "Sweep", "sweep", onPlan, { tool: "sweep" }),
                T("makevoid", "Make void", "void", !!store.project, { run: () => this.makeVoid(), note: "Turn the selected extrusions into voids that cut" })],
            [T("section", "Section", "section", onPlan, { tool: "section" }), T("elevation", "Elevation", "elevation", true, { run: () => this.elevationMenu(this.ribbon) }), T("schedule", "Schedule", "table", true, { run: () => this.scheduleMenu(this.ribbon) }),
                ...(isDoc ? [T("delview", "Delete view", "close", true, { run: () => this.deleteView() })] : [])]];
    }
    // ------------------------------------------------------------------------------------------------------------- render
    render() {
        this.renderTitle();
        this.renderMenubar();
        this.renderRibbon();
        this.renderTabs();
        this.renderStage();
        this.browser.update();
        replace(this.props, renderProperties(store));
        this.renderStatus();
        this.renderToast();
    }
    renderTitle() {
        const p = store.project;
        const switcher = h("button", { class: "project-switch", type: "button", attrs: { "aria-label": "Project menu" }, on: { click: () => this.fileMenu(switcher) } }, h("span", { text: p?.name ?? "No project" }), glyph("chevron-down"));
        replace(this.title, h("div", { class: "brand" }, h("img", { attrs: { src: "/assets/img/spadra_main.png", alt: "" } }), h("span", { text: "SPADRA" })), switcher, p ? h("div", { class: "save-state", data: { dirty: String(p.dirty) }, attrs: { title: p.path ? `Saved to ${p.path}` : "Not saved yet" } }, glyph("cloud"), h("span", { text: p.dirty ? "Unsaved changes" : p.path ? "Saved" : "Not saved" })) : null, h("div", { class: "spacer" }));
    }
    renderMenubar() {
        replace(this.menubar, MENUS.map((m) => {
            const b = h("button", { type: "button", attrs: { role: "tab", "aria-selected": String(this.menu === m) }, text: m, on: { click: () => {
                        if (m === "File") {
                            this.fileMenu(b);
                            return;
                        }
                        this.menu = m;
                        this.render();
                    } } });
            return b;
        }));
    }
    renderRibbon() {
        const groups = this.tools().map((g) => h("div", { class: "group" }, g.map((d) => {
            const pressed = d.tool !== undefined && store.tool === d.tool;
            return h("button", {
                class: "tool", type: "button", disabled: !d.implemented, attrs: { "aria-pressed": d.tool ? String(pressed) : undefined, title: d.implemented ? d.note || d.label : `${d.label}: ${d.note ?? NOT_YET}` },
                on: { click: () => { if (d.tool) {
                        store.setTool(d.tool);
                        this.drawing.reset();
                    }
                    else
                        void d.run?.(); } },
            }, glyph(d.icon), h("span", { text: d.label }));
        })));
        const kind = store.tab?.ref.kind;
        const is3d = kind === "3d";
        const modeSwitch = h("div", { class: "mode-switch", attrs: { role: "group", "aria-label": "View mode" } }, h("button", { type: "button", disabled: !store.project, attrs: { "aria-pressed": String(is3d) }, on: { click: () => this.goto3D() } }, glyph("cube"), "3D"), h("button", { type: "button", disabled: !store.project, attrs: { "aria-pressed": String(!!kind && !is3d) }, on: { click: () => this.goto2D() } }, glyph("page"), "2D"));
        const side = h("div", { class: "side-tools" }, this.sideButton("cube", this.model.cam.ortho ? "Switch to perspective" : "Switch to orthographic", is3d, () => this.model.setOrtho(!this.model.cam.ortho)), this.sideButton("fit", "Zoom to fit", is3d || kind === "drawing", () => { if (is3d)
            this.model.fit();
        else
            this.drawing.fit(); }), this.sideButton("info", "SLAI Assistant", !!store.project && !!store.boot?.capabilities.assistant, () => { void this.assistant.toggle(); }), this.sideButton("check-circle", "Check the model", !!store.project, () => { void store.loadCheck().then(() => reviewDialog(store)); }), this.sideButton("settings", "Appearance", true, () => appearanceDialog()));
        replace(this.ribbon, h("div", { class: "tool-groups" }, groups), h("div", { class: "spacer" }), modeSwitch, side);
    }
    sideButton(icon, label, enabled, run) {
        return h("button", { class: "iconbtn", type: "button", disabled: !enabled, attrs: { "aria-label": label, title: enabled ? label : `${label} (not available here)` }, on: { click: run } }, glyph(icon));
    }
    renderTabs() {
        const items = store.tabs.map((t, i) => {
            const active = i === store.active;
            const icon = t.ref.kind === "3d" ? "cube" : t.ref.kind === "sheet" ? "page" : t.ref.kind === "schedule" ? "table" : this.viewIcon(t.ref.id);
            return h("div", { class: "doc-tab", attrs: { role: "tab", tabindex: "0", "aria-selected": String(active) },
                on: { click: () => { void store.activate(i); }, keydown: (e) => { if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        void store.activate(i);
                    } } } }, glyph(icon), h("span", { text: t.title }), active ? h("span", { class: "chip", text: t.format }) : null, h("button", { class: "close", type: "button", attrs: { "aria-label": `Close ${t.title}` }, on: { click: (e) => { e.stopPropagation(); void store.closeTab(i); } } }, glyph("close")));
        });
        const plus = h("button", { class: "iconbtn", type: "button", disabled: !store.project, attrs: { "aria-label": "Open another view" }, on: { click: () => this.openMenuFor(plus) } }, glyph("plus"));
        replace(this.tabs, items, plus);
    }
    viewIcon(id) {
        const kind = flatten(store.tree).find((n) => n.open?.id === id)?.view_kind;
        return kind === "section" ? "section" : kind === "elevation" ? "elevation" : "plan";
    }
    openMenuFor(anchor) {
        const open = new Set(store.tabs.map((t) => t.ref.id));
        const entries = openable(store.tree).filter((n) => n.open && !open.has(n.open.id)).map((n) => ({ label: n.label, icon: iconFor(n), run: () => { if (n.open)
                void store.openTab(n.open, n.label); } }));
        entries.push("separator", { label: "New sheet…", icon: "plus", run: () => { void this.newSheet(); } });
        openMenu(anchor, entries, { label: "Open view" });
    }
    renderStage() {
        const kind = store.tab?.ref.kind;
        this.drawing.el.hidden = kind !== "drawing";
        this.model.el.hidden = kind !== "3d";
        this.sheet.el.hidden = kind !== "sheet";
        this.schedule.el.hidden = kind !== "schedule";
        this.schedule.setData(kind === "schedule" ? store.schedule : null);
        this.empty.hidden = !!store.tab;
        this.empty.textContent = store.project ? "Open a view from the Project Browser." : "Create or open a project to begin.";
        this.drawing.setData(kind === "drawing" ? store.drawing : null);
        if (kind === "3d")
            this.model.setScene(store.scene);
        this.timeline.render();
        if (kind === "sheet" && store.tab)
            void this.sheet.show(store.tab.ref.id);
        if (kind !== "drawing")
            this.drawing.reset();
    }
    renderStatus() {
        const kind = store.tab?.ref.kind;
        const sel = store.selection;
        const count = sel?.kind === "sheet" ? 0 : sel?.ids.length ?? 0;
        const selection = sel?.kind === "sheet" ? "1 Sheet Selected" : count ? `${count} ${kind === "3d" ? (count === 1 ? "Object" : "Objects") : count === 1 ? "Element" : "Elements"} Selected` : "Nothing Selected";
        const mode = kind === "3d" ? (this.model.cam.ortho ? "Orthographic" : "Perspective") : kind === "sheet" ? "Sheet / 2D" : kind === "schedule" ? "Schedule" : kind === "drawing" ? `${store.tab?.title ?? ""} / 2D` : "";
        const modeBtn = kind === "3d" ? h("button", { type: "button", on: { click: () => this.model.setOrtho(!this.model.cam.ortho) }, attrs: { title: "Switch projection" } }, mode) : h("span", { text: mode });
        const gl = this.model.stats;
        replace(this.status, h("span", { text: store.status }), h("div", { class: "spacer" }), jobChip(store), this.syncIndicator(), store.project && (kind === "3d" || kind === "drawing") ? h("button", { type: "button", class: "snap-toggle", text: "Grid snap", attrs: { "aria-pressed": String(store.snapGrid), title: "Snap the pointer to the grid (F9). Snapping to ends, midpoints and intersections stays on." },
            on: { click: () => store.setSnapGrid(!store.snapGrid) } }) : null, store.project ? h("span", { attrs: { title: "Drawing unit" } }, store.unit) : null, modeBtn, store.project ? h("span", { text: selection }) : null, kind === "3d" && !gl.webgl ? h("span", { text: "WebGL unavailable" }) : null, h("button", { class: "iconbtn", type: "button", attrs: { "aria-label": "Appearance" }, on: { click: () => appearanceDialog() } }, glyph("settings")));
    }
    /** How the documentation stands against the model: sheets that differ from their last issue and tags whose element is gone. Silent when all is in step. */
    syncIndicator() {
        const sync = store.sync;
        if (!store.project || !sync)
            return null;
        const stale = sync.sheets.filter((x) => x.state === "changed"), detached = sync.detached_tags;
        if (!stale.length && !detached.length)
            return null;
        const parts = [stale.length ? `${stale.length} ${stale.length === 1 ? "sheet" : "sheets"} changed` : "", detached.length ? `${detached.length} detached ${detached.length === 1 ? "tag" : "tags"}` : ""].filter(Boolean);
        const button = h("button", { type: "button", class: "sync-chip", attrs: { title: "Documentation that no longer matches the model", "aria-label": `Documentation: ${parts.join(", ")}` }, text: parts.join(" · "),
            on: { click: () => this.syncMenu(button) } });
        return button;
    }
    syncMenu(anchor) {
        const sync = store.sync;
        if (!sync)
            return;
        const node = (id) => flatten(store.tree).find((n) => n.open?.id === id);
        const entries = [
            ...sync.sheets.filter((x) => x.state === "changed").map((x) => ({ label: `${x.number} ${x.name}: changed since ${x.revision || "issue"} (${x.changed.join(", ")})`, icon: "page", run: () => { const n = node(x.id); if (n?.open)
                    void store.openTab(n.open, n.label); } })),
            ...sync.detached_tags.map((t) => ({ label: `Tag in ${t.view_name}: its element was deleted`, icon: "tag", run: () => { const n = node(t.view); if (n?.open)
                    void store.openTab(n.open, n.label); } })),
        ];
        openMenu(anchor, entries, { label: "Documentation out of step" });
    }
    renderToast() {
        const c = store.check;
        const issues = c?.issues ?? [];
        const show = !!store.project && !!c && issues.length > 0 && store.assistantDismissedFor !== c.revision;
        this.toastEl.hidden = !show;
        if (!show || !c)
            return;
        replace(this.toastEl, h("div", {}, h("div", { class: "title" }, glyph("check-circle"), "Model check"), h("div", { class: "msg", text: `${issues.length} ${issues.length === 1 ? "issue needs" : "issues need"} attention` })), h("button", { class: "review", type: "button", text: "Review", on: { click: () => reviewDialog(store) } }), h("button", { class: "dismiss", type: "button", attrs: { "aria-label": "Dismiss" }, on: { click: () => { store.assistantDismissedFor = c.revision; this.renderToast(); } } }, glyph("close")));
    }
}
