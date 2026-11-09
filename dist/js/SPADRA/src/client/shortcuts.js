/** The keyboard shortcuts SPADRA actually handles, listed in one place so the reference dialog cannot drift from what the code does
 *  (each entry is checked against the handlers by tests_ts/shortcuts.test.ts and tests_e2e/project.e2e.test.mjs). */
import { h } from "../../../tools/web/src/dom.js";
import { openDialog } from "../../../tools/web/src/ui.js";
export const SHORTCUTS = [
    { keys: "Ctrl+S", does: "Save the project", where: "Anywhere" },
    { keys: "Ctrl+Shift+S", does: "Save As (a copy under a new name)", where: "Anywhere" },
    { keys: "Ctrl+Z", does: "Undo", where: "Anywhere" },
    { keys: "Ctrl+Shift+Z or Ctrl+Y", does: "Redo", where: "Anywhere" },
    { keys: "?  or  F1", does: "Show this list", where: "Anywhere outside a text box" },
    { keys: "Esc", does: "Cancel what is being drawn, or go back to the Select tool", where: "2D and 3D views" },
    { keys: "Enter", does: "Confirm a dimension after two points, or finish a polyline, floor or roof outline", where: "2D and 3D views" },
    { keys: "Shift+double-click", does: "Select all topologically connected walls in the current plan", where: "2D plan" },
    { keys: "Shift+F", does: "Flip the selected section cut direction", where: "2D plan" },
    { keys: "C", does: "Close a polyline (three points or more)", where: "2D view" },
    { keys: "Delete or Backspace", does: "Delete the selected items; Backspace removes the previous dimension point while measuring", where: "2D and 3D views" },
    { keys: "V  L  P  R  O", does: "Select, Line, Polyline, Rectangle, Circle", where: "Outside a text box" },
    { keys: "D  T  W  G  H  M  Q", does: "Dimension, Text, Wall, Grid, Hatch, Mirror, Rotate (when the tool is available in the view)", where: "Outside a text box" },
    { keys: "Shift (while drawing or dragging a handle)", does: "Keep a wall or a stretched end straight; turn in 15° steps instead of 1°", where: "2D view" },
    { keys: "/", does: "Search the Project Browser", where: "Outside a text box" },
    { keys: "F9", does: "Turn grid snap on or off (snapping to ends, midpoints and intersections stays on)", where: "2D and 3D views" },
    { keys: "2  or  3", does: "Go to a 2D view or to the 3D model", where: "Outside a text box" },
    { keys: "F", does: "Zoom to fit", where: "Outside a text box" },
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
/** True for the keys that open the reference: a question mark, or F1, when the person is not typing into a field. */
export function opensShortcutList(e, isField) {
    if (isField(e.target) || e.ctrlKey || e.metaKey || e.altKey)
        return false;
    return e.key === "?" || e.key === "F1";
}
/** The tool a plain letter key selects (or null). Whether the tool exists in the current view is the shell's business. */
const HOTKEYS = { v: "select", l: "line", p: "polyline", r: "rectangle", o: "circle", d: "dimension", t: "text", w: "wall", g: "grid", h: "hatch", m: "mirror", q: "rotate" };
export function toolHotkey(e) {
    if (e.ctrlKey || e.metaKey || e.altKey)
        return null;
    return HOTKEYS[e.key.toLowerCase()] ?? null;
}
export function shortcutsDialog() {
    openDialog({
        title: "Keyboard shortcuts", size: "wide", noCancel: true, cancelLabel: "Close",
        body: h("table", { class: "schedule", attrs: { "aria-label": "Keyboard shortcuts" } }, h("thead", {}, h("tr", {}, ["Keys", "What it does", "Where"].map((c) => h("th", { text: c })))), h("tbody", {}, SHORTCUTS.map((s) => h("tr", {}, h("td", { class: "keys", text: s.keys }), h("td", { text: s.does }), h("td", { text: s.where }))))),
        actions: [{ label: "Close", run: () => true }],
    });
}
