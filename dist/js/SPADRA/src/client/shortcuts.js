/** Keyboard shortcuts: one registry of the actions a key can run, the presets that bind them (SPADRA, Blender, 3ds Max, Revit, AutoCAD), the person's own
 *  changes on top, conflict detection, search, and the dialog that edits all of it. The shell runs whatever the keymap resolves; the drawing and 3D views
 *  keep their own focused-canvas keys (Esc, Enter, Delete, arrows ...), listed under BUILT_IN and refused as bindings so the two can never fight.
 *  The engine (everything above the dialog) has no DOM in it, so tests_ts/shortcuts.test.ts drives it directly. */
import { h } from "../../../tools/web/src/dom.js";
import { openDialog } from "../../../tools/web/src/ui.js";
const tool = (id, label, words = "") => ({ id: `tool.${id}`, label, group: "Tools", words });
export const ACTIONS = [
    { id: "file.save", label: "Save the project", group: "File", words: "write store" },
    { id: "file.saveAs", label: "Save As (a copy under a new name)", group: "File", words: "duplicate copy" },
    { id: "file.open", label: "Open a project…", group: "File", anywhere: true, words: "load" },
    { id: "edit.undo", label: "Undo", group: "Edit", words: "back revert" },
    { id: "edit.redo", label: "Redo", group: "Edit", words: "forward repeat" },
    { id: "view.fit", label: "Zoom to fit", group: "View", words: "extents all frame" },
    { id: "view.zoomIn", label: "Zoom in (2D)", group: "View", words: "magnify closer" },
    { id: "view.zoomOut", label: "Zoom out (2D)", group: "View", words: "farther" },
    { id: "view.home", label: "Home view (3D)", group: "View", words: "reset camera" },
    { id: "view.projection", label: "Perspective / orthographic (3D)", group: "View", words: "ortho camera" },
    { id: "view.to2d", label: "Go to a 2D view", group: "View", words: "plan top drawing" },
    { id: "view.to3d", label: "Go to the 3D model", group: "View", words: "perspective model" },
    { id: "view.grid", label: "Show or hide the grid", group: "View", words: "display lines" },
    { id: "view.snap", label: "Grid snap on / off", group: "View", words: "snapping" },
    { id: "ui.search", label: "Search the Project Browser", group: "Interface", words: "find" },
    { id: "ui.shortcuts", label: "Show and edit the keyboard shortcuts", group: "Interface", anywhere: true, words: "keys help keymap" },
    tool("select", "Select", "pointer pick"), tool("line", "Line"), tool("polyline", "Polyline"), tool("rectangle", "Rectangle", "rect box"), tool("circle", "Circle"),
    tool("dimension", "Dimension", "measure"), tool("text", "Text", "note label"), tool("wall", "Wall"), tool("curtainwall", "Curtain wall", "facade glazing mullion transom vliesgevel"), tool("curtainwallrun", "Connected curtain walls", "facade run corner chain"), tool("grid", "Grid line", "axis"), tool("hatch", "Hatch", "fill"),
    tool("mirror", "Mirror", "flip"), tool("rotate", "Rotate", "turn"), tool("offset", "Offset", "parallel"), tool("trim", "Trim", "cut"), tool("extend", "Extend"),
    tool("fillet", "Fillet", "round corner"), tool("door", "Door"), tool("window", "Window"), tool("floor", "Floor", "slab"), tool("roof", "Roof"),
    tool("column", "Column"), tool("beam", "Beam"), tool("room", "Room", "space"), tool("section", "Section", "cut"), tool("stair", "Stair", "steps"),
    tool("tag", "Tag", "annotate"), tool("extrude", "Extrude", "push pull"),
];
const ACTION_IDS = new Set(ACTIONS.map((a) => a.id));
export const actionById = (id) => ACTIONS.find((a) => a.id === id);
const IGNORED = new Set(["Control", "Shift", "Alt", "Meta", "AltGraph", "CapsLock", "NumLock", "ScrollLock", "OS", "Dead", "Process", "Unidentified"]);
const NAMED = {
    Escape: "Esc", " ": "Space", Spacebar: "Space", ArrowLeft: "Left", ArrowRight: "Right", ArrowUp: "Up", ArrowDown: "Down", Delete: "Delete", Backspace: "Backspace",
    Enter: "Enter", Tab: "Tab", Home: "Home", End: "End", PageUp: "PageUp", PageDown: "PageDown", Insert: "Insert", ContextMenu: "Menu",
};
const NUMPAD = { NumpadAdd: "Num+", NumpadSubtract: "Num-", NumpadMultiply: "Num*", NumpadDivide: "Num/", NumpadDecimal: "Num." };
/** The canonical name of one key press ("Ctrl+Shift+Z", "F9", "?", "Num5"), or null for a press of a modifier alone. Command (Mac) counts as Ctrl. */
export function tokenOf(e) {
    if (IGNORED.has(e.key))
        return null;
    const code = e.code ?? "";
    let name;
    if (code.startsWith("Numpad") && code !== "NumpadEnter")
        name = NUMPAD[code] ?? (/^Numpad\d$/.test(code) ? `Num${code.slice(6)}` : undefined);
    if (name === undefined) {
        if (e.key.length === 1)
            name = /[a-z]/i.test(e.key) ? e.key.toUpperCase() : e.key;
        else
            name = NAMED[e.key] ?? (/^F\d{1,2}$/.test(e.key) ? e.key : undefined);
    }
    if (name === undefined)
        return null;
    const letterLike = name.length === 1 && /[A-Z]/.test(name);
    const shift = !!e.shiftKey && (name.length > 1 || letterLike); // "?" and "+" already say they were shifted
    return `${e.ctrlKey || e.metaKey ? "Ctrl+" : ""}${e.altKey ? "Alt+" : ""}${shift ? "Shift+" : ""}${name}`;
}
const KEY_NAME = /^(?:Ctrl\+)?(?:Alt\+)?(?:Shift\+)?(?:[A-Z0-9]|[^\sA-Za-z0-9+]|\+|F(?:[1-9]|1[0-2])|Esc|Space|Left|Right|Up|Down|Delete|Backspace|Enter|Tab|Home|End|PageUp|PageDown|Insert|Menu|Num[0-9]|Num[+\-*/.])$/;
/** A binding is one key press or a chord: presses separated by spaces ("W A" means W, then A). Returns the presses, or null when it is not valid. */
export function parseBinding(binding) {
    const parts = binding.trim().split(/\s+/).filter(Boolean);
    if (parts.length === 0 || parts.length > 4)
        return null;
    return parts.every((p) => KEY_NAME.test(p)) ? parts : null;
}
export const formatBinding = (binding) => binding.split(" ").join(" then ");
/** Keys the drawing and 3D views, the browser tree and the timeline use themselves, so they cannot be bound; and keys a web page cannot take from the browser. */
const VIEW_KEYS = new Set(["Esc", "Enter", "Delete", "Backspace", "Space", "Left", "Right", "Up", "Down", "PageUp", "PageDown", "Tab", "Menu"]);
const BROWSER_KEYS = new Set(["F5", "F11", "F12", "Ctrl+W", "Ctrl+T", "Ctrl+N", "Ctrl+Shift+N", "Ctrl+Tab", "Ctrl+R", "Ctrl+Shift+R", "Alt+F4", "Ctrl+L", "Ctrl+Q", "Alt+Left", "Alt+Right", "Ctrl+Shift+W", "Ctrl+Shift+T"]);
export function reservedReason(binding) {
    for (const press of binding.split(" ")) {
        const bare = press.replace(/^(?:Ctrl\+)?(?:Alt\+)?(?:Shift\+)?/, "");
        if (VIEW_KEYS.has(bare))
            return `${press} is used by the drawing, the 3D view and the browser tree for editing and moving around, so it cannot be reassigned`;
        if (BROWSER_KEYS.has(press))
            return `${press} belongs to the browser: a web page cannot take it`;
        if (press === "Shift+F")
            return "Shift+F flips the selected section cut in the plan, so it cannot be reassigned";
    }
    return null;
}
const SPADRA_KEYS = {
    "file.save": ["Ctrl+S"], "file.saveAs": ["Ctrl+Shift+S"], "file.open": ["Ctrl+O"], "edit.undo": ["Ctrl+Z"], "edit.redo": ["Ctrl+Shift+Z", "Ctrl+Y"],
    "view.fit": ["F"], "view.to2d": ["2"], "view.to3d": ["3"], "view.grid": ["F7"], "view.snap": ["F9"], "ui.search": ["/"], "ui.shortcuts": ["?", "F1"],
    "tool.select": ["V"], "tool.line": ["L"], "tool.polyline": ["P"], "tool.rectangle": ["R"], "tool.circle": ["O"], "tool.dimension": ["D"], "tool.text": ["T"], "tool.wall": ["W"],
    "tool.grid": ["G"], "tool.hatch": ["H"], "tool.mirror": ["M"], "tool.rotate": ["Q"],
};
export const PRESETS = [
    { id: "spadra", name: "SPADRA", note: "The keys SPADRA has always had: single letters for tools.", keys: SPADRA_KEYS },
    { id: "autocad", name: "AutoCAD", note: "Command aliases typed as letters in a row (P then L is Polyline), Ctrl+Y to redo, F7 grid, F9 snap.", keys: {
            "file.save": ["Ctrl+S"], "file.saveAs": ["Ctrl+Shift+S"], "file.open": ["Ctrl+O"], "edit.undo": ["Ctrl+Z"], "edit.redo": ["Ctrl+Y"], "ui.shortcuts": ["F1"],
            "view.grid": ["F7"], "view.snap": ["F9"], "view.fit": ["Z E"],
            "tool.line": ["L"], "tool.polyline": ["P L"], "tool.rectangle": ["R E C"], "tool.circle": ["C"], "tool.dimension": ["D L I"], "tool.text": ["M T"], "tool.hatch": ["H"],
            "tool.offset": ["O"], "tool.trim": ["T R"], "tool.extend": ["E X"], "tool.fillet": ["F"], "tool.mirror": ["M I"], "tool.rotate": ["R O"]
        } },
    { id: "revit", name: "Revit", note: "Two-letter shortcuts typed in a row (W then A is Wall), as in Revit's own defaults.", keys: {
            "file.save": ["Ctrl+S"], "file.saveAs": ["Ctrl+Shift+S"], "file.open": ["Ctrl+O"], "edit.undo": ["Ctrl+Z"], "edit.redo": ["Ctrl+Y"], "ui.shortcuts": ["F1"],
            "view.fit": ["Z F", "Z E"], "view.to3d": ["3"],
            "tool.wall": ["W A"], "tool.door": ["D R"], "tool.window": ["W N"], "tool.room": ["R M"], "tool.grid": ["G R"], "tool.dimension": ["D I"], "tool.text": ["T X"], "tool.tag": ["T G"],
            "tool.mirror": ["M M"], "tool.rotate": ["R O"], "tool.trim": ["T R"], "tool.offset": ["O F"]
        } },
    { id: "blender", name: "Blender", note: "R rotates, E extrudes, Ctrl+M mirrors, Home frames everything, Numpad 5 flips perspective.", keys: {
            "file.save": ["Ctrl+S"], "file.saveAs": ["Ctrl+Shift+S"], "file.open": ["Ctrl+O"], "edit.undo": ["Ctrl+Z"], "edit.redo": ["Ctrl+Shift+Z"], "ui.shortcuts": ["F1"], "ui.search": ["F3"],
            "view.fit": ["Home"], "view.projection": ["Num5"], "view.zoomIn": ["Num+"], "view.zoomOut": ["Num-"],
            "tool.select": ["W"], "tool.rotate": ["R"], "tool.extrude": ["E"], "tool.mirror": ["Ctrl+M"]
        } },
    { id: "max", name: "3ds Max", note: "Q selects, E rotates, S toggles snap, G shows or hides the grid, Z zooms to extents, P and T switch to the perspective and top views.", keys: {
            "file.save": ["Ctrl+S"], "file.saveAs": ["Ctrl+Shift+S"], "file.open": ["Ctrl+O"], "edit.undo": ["Ctrl+Z"], "edit.redo": ["Ctrl+Y"], "ui.shortcuts": ["F1"],
            "view.fit": ["Z"], "view.grid": ["G"], "view.snap": ["S"], "view.to3d": ["P"], "view.to2d": ["T"], "view.projection": ["U"],
            "tool.select": ["Q"], "tool.rotate": ["E"]
        } },
];
export const presetById = (id) => PRESETS.find((p) => p.id === id);
/** What a preset binds: its own keys, plus SPADRA's for the actions that are not tools (save, undo, search ...) wherever they do not collide with the preset's own. */
const RESOLVED = new Map();
export function presetBindings(id) {
    const cached = RESOLVED.get(id);
    if (cached)
        return cached;
    const preset = presetById(id) ?? PRESETS[0];
    const out = {};
    for (const [action, keys] of Object.entries(preset.keys))
        out[action] = [...keys];
    if (preset.id !== "spadra") {
        const taken = Object.values(out).flat();
        for (const [action, keys] of Object.entries(SPADRA_KEYS)) {
            if (action in out || action.startsWith("tool."))
                continue;
            const free = keys.filter((k) => !taken.some((t) => collide(t, k)));
            out[action] = free;
            taken.push(...free);
        }
    }
    RESOLVED.set(id, out);
    return out;
}
/** Two bindings collide when they are the same or one is the first presses of the other (the shorter would fire before the longer could be typed). */
export function collide(a, b) {
    const x = a.split(" "), y = b.split(" ");
    const n = Math.min(x.length, y.length);
    for (let i = 0; i < n; i++)
        if (x[i] !== y[i])
            return false;
    return true;
}
export const KEYMAP_STORAGE_KEY = "spadra.keymap";
export class Keymap {
    storage;
    preset = "spadra";
    /** The person's own list for an action: it replaces the preset's list for that action (an empty list leaves it unbound). */
    overrides = {};
    constructor(storage = null) {
        this.storage = storage;
        this.load();
    }
    bindings(action) { return [...(this.overrides[action] ?? presetBindings(this.preset)[action] ?? [])]; }
    all() { return Object.fromEntries(ACTIONS.map((a) => [a.id, this.bindings(a.id)])); }
    isCustom(action) { return action in this.overrides; }
    hasCustom() { return Object.keys(this.overrides).length > 0; }
    /** Every pair of actions whose bindings collide. Empty for any preset; the person's changes can only create one by choosing "assign anyway". */
    conflicts() {
        const found = [];
        const entries = ACTIONS.flatMap((a) => this.bindings(a.id).map((b) => ({ id: a.id, binding: b })));
        for (let i = 0; i < entries.length; i++)
            for (let j = i + 1; j < entries.length; j++) {
                const p = entries[i], q = entries[j];
                if (p.id !== q.id && collide(p.binding, q.binding))
                    found.push({ binding: p.binding, with: q.binding, actions: [p.id, q.id] });
            }
        return found;
    }
    /** Can this action take this binding, and what would it clash with? Refuses what the views or the browser keep for themselves and what is not a key. */
    check(action, binding) {
        if (!ACTION_IDS.has(action))
            return { ok: false, reason: `There is no action "${action}"` };
        const parts = parseBinding(binding);
        if (!parts)
            return { ok: false, reason: `"${binding}" is not a key or a short run of keys` };
        const norm = parts.join(" ");
        const reason = reservedReason(norm);
        if (reason)
            return { ok: false, reason };
        const clashes = [];
        for (const a of ACTIONS) {
            if (a.id === action)
                continue;
            for (const b of this.bindings(a.id))
                if (collide(b, norm))
                    clashes.push({ binding: norm, with: b, actions: [action, a.id] });
        }
        return { ok: true, clashes };
    }
    /** Give an action a binding. ``mode`` says what to do about clashes: "refuse" (default) changes nothing and reports them, "steal" takes the binding from the others,
     *  "keep" assigns it anyway and leaves the clash for conflicts() to show. */
    assign(action, binding, mode = "refuse", replace) {
        const verdict = this.check(action, binding);
        if (!verdict.ok)
            return verdict;
        const norm = parseBinding(binding).join(" ");
        if (verdict.clashes.length && mode === "refuse")
            return verdict;
        if (verdict.clashes.length && mode === "steal") {
            for (const c of verdict.clashes)
                this.overrides[c.actions[1]] = this.bindings(c.actions[1]).filter((b) => b !== c.with);
        }
        const next = this.bindings(action).filter((b) => b !== replace && b !== norm);
        next.push(norm);
        this.overrides[action] = next;
        this.tidy();
        this.save();
        return verdict;
    }
    remove(action, binding) {
        this.overrides[action] = this.bindings(action).filter((b) => b !== binding);
        this.tidy();
        this.save();
    }
    reset(action) {
        if (action)
            delete this.overrides[action];
        else
            this.overrides = {};
        this.save();
    }
    /** Switch preset. The person's own changes are dropped, because they were changes to the old one. */
    usePreset(id) {
        if (!presetById(id))
            return;
        this.preset = id;
        this.overrides = {};
        this.save();
    }
    /** An override that says what the preset already says is no override. */
    tidy() {
        for (const [action, list] of Object.entries(this.overrides)) {
            const base = presetBindings(this.preset)[action] ?? [];
            if (list.length === base.length && list.every((b, i) => b === base[i]))
                delete this.overrides[action];
        }
    }
    /** The actions that match a search: words in the name, group or synonyms, or part of a key ("ctrl", "F9"). */
    search(query) {
        const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
        if (!terms.length)
            return [...ACTIONS];
        return ACTIONS.filter((a) => {
            const hay = `${a.label} ${a.group} ${a.words ?? ""} ${this.bindings(a.id).join(" ")}`.toLowerCase();
            return terms.every((t) => hay.includes(t));
        });
    }
    /** Which action a binding runs now (the first, if it is clashing). */
    actionFor(binding) {
        return ACTIONS.find((a) => this.bindings(a.id).includes(binding))?.id ?? null;
    }
    save() {
        try {
            this.storage?.setItem(KEYMAP_STORAGE_KEY, JSON.stringify({ version: 1, preset: this.preset, overrides: this.overrides }));
        }
        catch { /* storage unavailable: the change lasts for this page only */ }
    }
    load() {
        this.preset = "spadra";
        this.overrides = {};
        let raw = null;
        try {
            raw = this.storage?.getItem(KEYMAP_STORAGE_KEY) ?? null;
        }
        catch {
            raw = null;
        }
        if (raw)
            this.apply(raw);
    }
    /** Take settings from text (what is stored, or an imported file). Anything that is not valid is dropped; returns false when the text is not a keymap at all. */
    apply(raw) {
        let data;
        try {
            data = JSON.parse(raw);
        }
        catch {
            return false;
        }
        if (typeof data !== "object" || data === null)
            return false;
        const rec = data;
        const preset = typeof rec["preset"] === "string" && presetById(rec["preset"]) ? rec["preset"] : "spadra";
        const overrides = {};
        const given = rec["overrides"];
        if (typeof given === "object" && given !== null) {
            for (const [action, list] of Object.entries(given)) {
                if (!ACTION_IDS.has(action) || !Array.isArray(list))
                    continue;
                const clean = list.filter((b) => typeof b === "string").map((b) => parseBinding(b)?.join(" ")).filter((b) => !!b && !reservedReason(b));
                overrides[action] = [...new Set(clean)];
            }
        }
        this.preset = preset;
        this.overrides = overrides;
        this.tidy();
        return true;
    }
    export() { return JSON.stringify({ version: 1, preset: this.preset, overrides: this.overrides }, null, 2); }
}
export const CHORD_TIMEOUT_MS = 1500;
/** Reads key presses one at a time and says when they add up to a binding: a one-key binding runs at once, a chord waits for its next press (for 1.5 s). */
export class ChordReader {
    keymap;
    typed = [];
    at = 0;
    constructor(keymap) {
        this.keymap = keymap;
    }
    get pending() { return [...this.typed]; }
    reset() { this.typed = []; }
    feed(token, now) {
        if (this.typed.length && now - this.at > CHORD_TIMEOUT_MS)
            this.typed = [];
        for (const attempt of this.typed.length ? [[...this.typed, token], [token]] : [[token]]) {
            const joined = attempt.join(" ");
            const exact = this.keymap.actionFor(joined);
            if (exact) {
                this.typed = [];
                return { kind: "run", action: exact };
            }
            if (ACTIONS.some((a) => this.keymap.bindings(a.id).some((b) => b.startsWith(`${joined} `)))) {
                this.typed = attempt;
                this.at = now;
                return { kind: "wait", typed: attempt };
            }
        }
        this.typed = [];
        return { kind: "none" };
    }
}
/** Keys the views and the tree handle themselves while they have the focus: listed for reference, not editable (each is checked against its handler by tests_ts/shortcuts.test.ts). */
export const SHORTCUTS = [
    { keys: "Esc", does: "Cancel what is being drawn, or go back to the Select tool", where: "2D and 3D views" },
    { keys: "Enter", does: "Confirm a dimension after two points, or finish a polyline, floor or roof outline", where: "2D and 3D views" },
    { keys: "Shift+double-click", does: "Select all topologically connected walls in the current plan", where: "2D plan" },
    { keys: "Shift+F", does: "Flip the selected section cut direction", where: "2D plan" },
    { keys: "C", does: "Close a polyline (three points or more)", where: "2D view" },
    { keys: "Delete or Backspace", does: "Delete the selected items; Backspace removes the previous dimension point while measuring", where: "2D and 3D views" },
    { keys: "Shift (while drawing or dragging a handle)", does: "Keep a wall or a stretched end straight; turn in 15° steps instead of 1°", where: "2D view" },
    { keys: "Space (held)", does: "Pan with the mouse", where: "2D view" },
    { keys: "Arrow keys", does: "Pan the drawing", where: "2D view, when the drawing has focus" },
    { keys: "+  /  -  /  0", does: "Zoom in, zoom out, fit", where: "2D view, when the drawing has focus" },
    { keys: "Shift+F10 or the menu key", does: "Open the menu of the selected row (open, delete)", where: "Project Browser" },
    { keys: "↑ ↓ ← → Home End", does: "Move between rows; ← and → close and open a group", where: "Project Browser" },
    { keys: "Home", does: "Fit the whole model in view", where: "3D view" },
    { keys: "Arrow keys (Shift for bigger steps)", does: "Orbit the model, about the selected element if there is one", where: "3D view" },
    { keys: "Ctrl+Arrow keys", does: "Walk the camera forward, back and sideways", where: "3D view" },
    { keys: "Page Up / Page Down", does: "Raise or lower the camera", where: "3D view" },
    { keys: "Ctrl+drag", does: "Look around from where the camera is", where: "3D view" },
    { keys: "Space", does: "Play or pause the animation", where: "Timeline" },
    { keys: "← / →", does: "Step one frame back or forward", where: "Timeline" },
    { keys: "Shift+← / Shift+→", does: "Step one second back or forward", where: "Timeline" },
    { keys: "Home / End", does: "Go to the start or the end of the animation", where: "Timeline" },
    { keys: "Delete or Backspace", does: "Delete the selected key", where: "Timeline" },
];
// ------------------------------------------------------------------------------------------------------------------------------ the dialog
const keyboardStore = () => { try {
    return window.localStorage;
}
catch {
    return null;
} };
/** The keymap this page uses, loaded from this browser's storage. */
export const keymap = new Keymap(keyboardStore());
/** True while the dialog is recording a key, so the shell does not run the key it is about to record. */
export let recordingKey = false;
const chip = (text) => h("kbd", { class: "keycap", text });
/** Show the shortcuts and let the person change them. ``changed`` runs after every change so the shell can refresh tooltips and the status line. */
export function shortcutsDialog(map = keymap, changed = () => undefined) {
    let query = "";
    const body = h("div", { class: "keymap" });
    const notice = h("div", { class: "keymap-note", attrs: { role: "status", "aria-live": "polite" } });
    const list = h("div", { class: "keymap-list" });
    let recording = null;
    let pending = null;
    const done = () => { recording = null; recordingKey = false; draw(); changed(); };
    const say = (text) => { notice.textContent = text; };
    const record = (action, replace) => {
        recording = { action, replace };
        recordingKey = true;
        pending = null;
        let typed = [], timer = 0;
        const commit = () => {
            window.clearTimeout(timer);
            window.removeEventListener("keydown", onKey, true);
            const binding = typed.join(" ");
            typed = [];
            const target = recording;
            recording = null;
            recordingKey = false;
            if (!target || !binding) {
                draw();
                return;
            }
            const result = map.assign(target.action, binding, "refuse", target.replace);
            if (!result.ok)
                say(result.reason);
            else if (result.clashes.length) {
                pending = { action: target.action, binding: parseBinding(binding).join(" "), replace: target.replace, clashes: result.clashes };
                say(`${formatBinding(pending.binding)} is already used by ${[...new Set(result.clashes.map((c) => actionById(c.actions[1])?.label ?? c.actions[1]))].join(", ")}.`);
            }
            else
                say(`${actionById(target.action)?.label}: ${formatBinding(parseBinding(binding).join(" "))}`);
            draw();
            changed();
        };
        const onKey = (e) => {
            e.preventDefault();
            e.stopPropagation();
            e.stopImmediatePropagation();
            if (e.key === "Escape") {
                typed = [];
                window.clearTimeout(timer);
                window.removeEventListener("keydown", onKey, true);
                say("Cancelled.");
                done();
                return;
            }
            if (e.key === "Enter" && typed.length) {
                commit();
                return;
            }
            const token = tokenOf(e);
            if (!token)
                return;
            typed.push(token);
            window.clearTimeout(timer);
            say(`Recording: ${typed.join(" then ")} …`);
            const modified = /^(?:Ctrl|Alt)\+/.test(token) || token.length > 1; // a combination, a function key or a named key stands alone; a plain letter may start a chord
            if (modified || typed.length >= 4)
                commit();
            else
                timer = window.setTimeout(commit, 900);
        };
        window.addEventListener("keydown", onKey, true);
        say("Press the keys now (Esc to cancel). For a chord, press them one after another.");
        draw();
    };
    const row = (a) => {
        const mine = map.bindings(a.id);
        const keys = h("span", { class: "keymap-keys" }, mine.length ? mine.map((b) => h("span", { class: "keymap-bind" }, h("button", { class: "keycap-btn", type: "button", attrs: { title: `Change ${formatBinding(b)}`, "aria-label": `${a.label}: ${formatBinding(b)}. Change` }, on: { click: () => record(a.id, b) } }, chip(formatBinding(b))), h("button", { class: "iconbtn tiny", type: "button", text: "×", attrs: { title: `Remove ${formatBinding(b)}`, "aria-label": `Remove ${formatBinding(b)} from ${a.label}` }, on: { click: () => { map.remove(a.id, b); say(`${formatBinding(b)} removed from ${a.label}.`); draw(); changed(); } } }))) : [h("span", { class: "muted", text: "unassigned" })]);
        const mark = map.conflicts().some((c) => c.actions.includes(a.id)) ? h("span", { class: "keymap-clash", text: "conflict", attrs: { title: "Another action uses the same keys" } }) : null;
        return h("div", { class: `keymap-row${recording?.action === a.id ? " recording" : ""}`, attrs: { "data-action": a.id } }, h("span", { class: "keymap-name", text: a.label }), keys, mark, h("button", { class: "btn small", type: "button", text: recording?.action === a.id ? "Recording…" : "Add", attrs: { "aria-label": `Add a key to ${a.label}` }, on: { click: () => record(a.id) } }), map.isCustom(a.id) ? h("button", { class: "btn small", type: "button", text: "Reset", attrs: { "aria-label": `Reset ${a.label} to the preset` }, on: { click: () => { map.reset(a.id); say(`${a.label} is back to the ${presetById(map.preset)?.name} keys.`); draw(); changed(); } } }) : null);
    };
    function draw() {
        const preset = presetById(map.preset);
        const select = h("select", { attrs: { "aria-label": "Preset" }, on: { change: () => { map.usePreset(select.value); say(`${presetById(map.preset)?.name} keys are on.`); draw(); changed(); } } }, PRESETS.map((p) => h("option", { value: p.id, text: p.name })));
        select.value = map.preset;
        const search = h("input", { type: "search", placeholder: "Search actions or keys", value: query, attrs: { "aria-label": "Search shortcuts" }, on: { input: () => { query = search.value; const at = search.selectionStart; drawList(); search.focus(); if (at !== null)
                    search.setSelectionRange(at, at); } } });
        const clashes = map.conflicts();
        const importBox = h("input", { type: "file", attrs: { accept: "application/json,.json", hidden: "" }, on: { change: () => {
                    const file = importBox.files?.[0];
                    if (!file)
                        return;
                    void file.text().then((text) => { say(map.apply(text) ? "Shortcuts imported." : "That file is not a SPADRA keymap."); map.save(); draw(); changed(); });
                } } });
        const bar = h("div", { class: "keymap-bar" }, h("label", {}, h("span", { text: "Preset " }), select), search, h("button", { class: "btn small", type: "button", text: "Reset all", on: { click: () => { map.reset(); say("Every shortcut is back to the preset."); draw(); changed(); } } }), h("button", { class: "btn small", type: "button", text: "Export", on: { click: () => { const url = URL.createObjectURL(new Blob([map.export()], { type: "application/json" })); const a = h("a", { href: url, attrs: { download: "spadra-keymap.json" } }); document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url); } } }), h("button", { class: "btn small", type: "button", text: "Import", on: { click: () => importBox.click() } }), importBox);
        const resolve = pending ? h("div", { class: "keymap-resolve" }, h("span", { text: `Give ${formatBinding(pending.binding)} to ${actionById(pending.action)?.label} and take it from the other?` }), h("button", { class: "btn small primary", type: "button", text: "Reassign", on: { click: () => { const p = pending; pending = null; map.assign(p.action, p.binding, "steal", p.replace); say(`${formatBinding(p.binding)} now runs ${actionById(p.action)?.label}.`); draw(); changed(); } } }), h("button", { class: "btn small", type: "button", text: "Assign anyway", on: { click: () => { const p = pending; pending = null; map.assign(p.action, p.binding, "keep", p.replace); say("Assigned. Both actions now claim these keys."); draw(); changed(); } } }), h("button", { class: "btn small", type: "button", text: "Cancel", on: { click: () => { pending = null; say(""); draw(); } } })) : null;
        body.replaceChildren(bar, h("p", { class: "muted keymap-preset", text: `${preset?.note ?? ""}${map.hasCustom() ? "  Your changes are kept in this browser." : ""}` }), notice, resolve ?? "", clashes.length ? h("div", { class: "keymap-conflicts", attrs: { role: "alert" } }, `${clashes.length} conflict${clashes.length === 1 ? "" : "s"}: ${clashes.slice(0, 4).map((c) => `${formatBinding(c.binding)} (${actionById(c.actions[0])?.label} and ${actionById(c.actions[1])?.label})`).join("; ")}`) : "", list, h("details", { class: "keymap-builtin" }, h("summary", { text: "Keys that belong to the views (not editable)" }), h("table", { class: "schedule", attrs: { "aria-label": "Keys of the views" } }, h("thead", {}, h("tr", {}, ["Keys", "What it does", "Where"].map((c) => h("th", { text: c })))), h("tbody", {}, SHORTCUTS.map((s) => h("tr", {}, h("td", { class: "keys", text: s.keys }), h("td", { text: s.does }), h("td", { text: s.where })))))));
        drawList();
    }
    function drawList() {
        const found = map.search(query);
        const groups = [...new Set(found.map((a) => a.group))];
        list.replaceChildren(...(found.length ? groups.map((g) => h("section", { class: "keymap-group" }, h("h3", { text: g }), ...found.filter((a) => a.group === g).map(row))) : [h("p", { class: "muted", text: "No action or key matches that." })]));
    }
    draw();
    const handle = openDialog({ title: "Keyboard shortcuts", size: "wide", noCancel: true, cancelLabel: "Close", body, actions: [{ label: "Close", run: () => true }] });
    void handle.result.then(() => { recordingKey = false; });
}
