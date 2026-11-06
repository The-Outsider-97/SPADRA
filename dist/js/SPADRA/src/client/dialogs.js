/** Dialogs: new/open project, unsaved changes, the model-check review, appearance. Built on the shared dialog kit. */
import { h, labelled } from "../../../tools/web/src/dom.js";
import { appearanceControls, applyAppearance, DEFAULT_APPEARANCE, normaliseAppearance } from "../../../tools/web/src/theme.js";
import { openDialog, promptDialog, toast } from "../../../tools/web/src/ui.js";
import { flatten } from "./store.js";
const KEY = "spadra.appearance";
/** Contextual '+' on a selected BIM face; Properties remains the editor for layers already placed. */
export async function addLayerDialog(store, id, category, side) {
    const choices = store.properties?.groups.find((g) => g.title.startsWith("Layers ("))?.rows.find((r) => r.key?.endsWith(":material"))?.choices ?? [];
    const material = h("select", { attrs: { "aria-label": "Layer material" } }, ...(choices.length ? choices : [{ value: "Concrete", label: "Concrete" }, { value: "Plasterboard", label: "Plasterboard" }]).map((c) => h("option", { value: c.value, text: c.label })));
    material.value = category === "wall" ? "Plasterboard" : material.options[0]?.value ?? "Concrete";
    const thickness = h("input", { type: "number", value: category === "wall" ? "12.5" : "50", min: "1", max: "5000", step: "any", attrs: { "aria-label": "Layer thickness in millimetres" } });
    const openings = h("select", { attrs: { "aria-label": "Opening inheritance" } }, h("option", { value: "all", text: "Inherit all openings and voids" }), h("option", { value: "none", text: "Do not inherit existing cuts" }));
    const boundary = category === "wall" ? ["start", "end", "bottom", "top"].map((edge) => [edge, h("input", { type: "number", value: "0", step: "any", attrs: { "aria-label": `${edge} edge offset in mm` } })]) : [];
    const handle = openDialog({ title: `Add ${side} ${category} layer`, size: "normal",
        body: h("div", { css: { display: "grid", gap: ".65rem" } }, labelled("Material", material), labelled("Thickness (mm)", thickness), labelled("Opening / void cuts", openings), ...boundary.map(([edge, input]) => labelled(`${edge[0]?.toUpperCase()}${edge.slice(1)} boundary (mm)`, input)), h("p", { class: "muted", text: "Preview the resulting geometry after adding. The layer's material, cut selection, and edge offsets remain editable in Properties." })),
        actions: [{ label: "Add layer", kind: "primary", run: async () => {
                    const amount = Number(thickness.value);
                    if (!Number.isFinite(amount) || amount < 1 || amount > 5000) {
                        toast("Thickness must be between 1 and 5000 mm.", { kind: "error" });
                        return undefined;
                    }
                    const edgeValues = {};
                    for (const [edge, input] of boundary) {
                        const n = Number(input.value);
                        if (!Number.isFinite(n)) {
                            toast(`Enter a valid ${edge} boundary.`, { kind: "error" });
                            return undefined;
                        }
                        edgeValues[edge] = n;
                    }
                    return (await store.run("model.add_layer", { id, side, thickness: amount, material: material.value, openings: openings.value, ...edgeValues })) ? true : undefined;
                } }],
    });
    await handle.result;
}
export function loadAppearance() {
    try {
        return normaliseAppearance(JSON.parse(window.localStorage.getItem(KEY) ?? "null"));
    }
    catch {
        return DEFAULT_APPEARANCE;
    }
}
function saveAppearance(a) { try {
    window.localStorage.setItem(KEY, JSON.stringify(a));
}
catch { /* private window: the choice just lasts this session */ } }
export function appearanceDialog() {
    openDialog({ title: "Appearance", size: "narrow", noCancel: true, cancelLabel: "Close", body: appearanceControls(loadAppearance(), (next) => { applyAppearance(next, "soft"); saveAppearance(next); }),
        actions: [{ label: "Done", kind: "primary", run: () => true }] });
}
/** Decide what happens to unsaved work before it is replaced. Resolves "clean" (nothing to lose, or it was just saved), "discard" (the person chose to
 *  throw it away; the server is told when the replacement is actually made, so cancelling afterwards loses nothing), or null (they cancelled). */
export async function ensureClean(store) {
    if (!store.project?.dirty)
        return "clean";
    const handle = openDialog({
        title: "Unsaved changes", size: "narrow",
        body: h("p", { text: `“${store.project.name}” has changes that are not saved yet.` }),
        actions: [
            { label: "Discard changes", kind: "danger", run: () => "discard" },
            { label: "Save", kind: "primary", run: async () => ((await store.save()) ? "save" : undefined) },
        ],
    });
    const choice = await handle.result;
    return choice === "save" ? "clean" : choice === "discard" ? "discard" : null;
}
export async function newProjectDialog(store) {
    const state = await ensureClean(store);
    if (!state)
        return;
    const name = h("input", { type: "text", value: "Untitled project", attrs: { maxlength: 80, autocomplete: "off" } });
    const unit = h("select", {}, ...(store.boot?.units ?? ["mm", "cm", "m"]).map((u) => h("option", { value: u, text: u, selected: u === "mm" })));
    const handle = openDialog({
        title: "New project", size: "narrow", focus: () => name,
        body: h("div", { css: { display: "grid", gap: "1rem" } }, labelled("Project name", name), labelled("Drawing unit", unit, { hint: "The unit drawings use. 3D models are always stored in metres." })),
        actions: [{ label: "Create", kind: "primary", run: async () => ((await store.newProject(name.value.trim() || "Untitled project", unit.value, state === "discard")) ? true : undefined) }],
    });
    await handle.result;
}
export async function openProjectDialog(store) {
    const state = await ensureClean(store);
    if (!state)
        return;
    const recents = store.boot?.recent ?? [];
    const path = h("input", { type: "text", placeholder: "Folder ending in .spadra", attrs: { autocomplete: "off" } });
    let handle = null;
    const open = (value) => store.openProject(value, state === "discard");
    const list = recents.length
        ? h("ul", { class: "recent-list" }, recents.map((r) => h("li", {}, h("button", { type: "button", on: { click: () => { void open(r.path).then((ok) => { if (ok)
                    handle?.close(true); }); } } }, h("strong", { text: r.name }), h("span", { class: "path", text: r.path })))))
        : h("p", { class: "muted", text: "No recent projects yet." });
    handle = openDialog({
        title: "Open project", size: "wide", focus: () => path,
        body: h("div", { css: { display: "grid", gap: "1rem" } }, h("div", {}, h("h3", { text: "Recent projects" }), list), labelled("Or open by path", path, { hint: `Projects are kept in ${store.boot?.storage.projectsDir ?? "the SPADRA data folder"}. A bare folder name is looked up there.` })),
        actions: [{ label: "Open", kind: "primary", run: async () => (path.value.trim() ? ((await open(path.value.trim())) ? true : undefined) : (toast("Choose a recent project or enter a path.", { kind: "info" }), undefined)) }],
    });
    await handle.result;
}
/** The row of the Project Browser that a check finding is about: an element, a view or a sheet. */
export function findingTarget(store, ref) {
    const node = flatten(store.tree).find((n) => n.select?.model === ref || (n.open?.id === ref && !n.id.startsWith("model-") && !n.id.startsWith("scene-")));
    return node ? { ...(node.select ? { select: node.select } : {}), ...(node.open ? { open: node.open } : {}), label: node.label } : null;
}
const SEVERITY_TEXT = { error: "Error", warning: "Warning", info: "Note" };
/** The model check: what it found (each finding can be shown in the model or the drawing it is about), the counts, and what a check of this kind does not do. */
export function reviewDialog(store) {
    const check = store.check;
    if (!check)
        return;
    let handle = null;
    const body = check.issues.length
        ? h("ul", { class: "issue-list" }, check.issues.map((i) => {
            const target = findingTarget(store, i.ref);
            return h("li", { data: { severity: i.severity } }, h("strong", { text: `${SEVERITY_TEXT[i.severity] ?? i.severity}: ` }), i.message, i.category ? h("span", { class: "muted", text: `  (${i.category})` }) : null, target ? h("button", { class: "tool-inline", type: "button", text: "Show", attrs: { "aria-label": `Show ${target.label}` },
                on: { click: () => { handle?.close(); void store.follow({ ...(target.open ? { open: target.open } : {}), ...(target.select ? { select: target.select } : {}) }); } } }) : null);
        }))
        : h("p", { text: "No problems found." });
    const counts = check.counts ? `${check.counts["error"] ?? 0} error(s), ${check.counts["warning"] ?? 0} warning(s), ${check.counts["info"] ?? 0} note(s).` : "";
    handle = openDialog({ title: "Model check", eyebrow: `Revision ${check.revision}`, size: "wide", noCancel: true,
        body: h("div", { css: { display: "grid", gap: "1rem" } }, counts ? h("p", { text: counts }) : null, body, h("p", { class: "muted", text: check.scope }), check.limitations?.length ? h("details", {}, h("summary", { text: "What this check does not do" }), h("ul", { class: "issue-list" }, check.limitations.map((l) => h("li", { text: l })))) : null),
        actions: [{ label: "Close", kind: "primary", run: () => true }] });
}
/** Ask for a positive number (an offset distance, a fillet radius). Null if cancelled. Zero is allowed only when `allowZero`. */
export async function numberPrompt(options) {
    const answer = await promptDialog({
        title: options.title, label: `${options.label} (${options.unit})`, value: String(options.value), confirmLabel: options.confirmLabel ?? "OK", maxLength: 24,
        validate: (v) => {
            const n = Number(v);
            if (!Number.isFinite(n))
                return "Enter a number.";
            if (n < 0 || (n === 0 && !options.allowZero))
                return options.allowZero ? "Enter zero or a positive number." : "Enter a number greater than zero.";
            return null;
        },
    });
    return answer === null ? null : Number(answer);
}
/** Choose how to fill the picked shape. Spacing left empty means "a twentieth of the shape", chosen by the server. */
export async function hatchDialog(unit, last) {
    const pattern = h("select", {}, ...[["lines", "Lines"], ["cross", "Cross-hatch"], ["solid", "Solid"]].map(([v, l]) => h("option", { value: v, text: l, selected: v === (last?.pattern ?? "lines") })));
    const angle = h("input", { type: "text", value: String(last?.angle ?? 45), attrs: { autocomplete: "off", maxlength: 8 } });
    const spacing = h("input", { type: "text", value: last?.spacing ? String(last.spacing) : "", placeholder: "Automatic", attrs: { autocomplete: "off", maxlength: 16 } });
    const sync = () => { const solid = pattern.value === "solid"; angle.disabled = solid; spacing.disabled = solid; };
    pattern.addEventListener("change", sync);
    sync();
    const error = h("p", { class: "muted", attrs: { role: "alert" } });
    const handle = openDialog({
        title: "Hatch", size: "narrow", focus: () => pattern,
        body: h("div", { css: { display: "grid", gap: "1rem" } }, labelled("Pattern", pattern), labelled("Angle (degrees)", angle), labelled(`Spacing (${unit})`, spacing, { hint: "Leave empty to let SPADRA choose." }), error),
        actions: [{ label: "Hatch", kind: "primary", run: () => {
                    const solid = pattern.value === "solid";
                    const a = Number(angle.value), s = spacing.value.trim() === "" ? null : Number(spacing.value);
                    if (!solid && (!Number.isFinite(a) || Math.abs(a) > 360)) {
                        error.textContent = "The angle must be a number between -360 and 360.";
                        return undefined;
                    }
                    if (!solid && s !== null && !(Number.isFinite(s) && s > 0)) {
                        error.textContent = "The spacing must be a number greater than zero.";
                        return undefined;
                    }
                    return { pattern: pattern.value, angle: solid ? 0 : a, spacing: solid ? null : s };
                } }],
    });
    return handle.result;
}
/** After picking the mirror line: keep the originals (a mirrored copy) or replace them. Null if cancelled. */
export async function mirrorKeepDialog() {
    const handle = openDialog({
        title: "Mirror", size: "narrow", body: h("p", { text: "Keep the original objects, or replace them with their mirror image?" }),
        actions: [{ label: "Replace originals", run: () => false }, { label: "Keep originals", kind: "primary", run: () => true }],
    });
    return handle.result;
}
/** Array: repeat the selected elements along X, Y, Z (or any mix) or round a point, and keep the pattern: the array is an element of its own that regenerates
 *  its copies when its count, spacing, direction or source change (edit it in Properties; "Release array" keeps the copies and drops the pattern). */
export async function arrayDialog(store) {
    const sel = store.selection;
    if (!store.project || sel?.kind !== "model" || !sel.ids.length) {
        toast("Select what to repeat first (walls, columns, floors, extrusions …), then choose Array.", { kind: "info" });
        return;
    }
    const unit = store.unit;
    const mode = h("select", {}, h("option", { value: "grid", text: "Rectangular: along X, Y and/or Z" }), h("option", { value: "radial", text: "Radial: round a point" }));
    const dimension = h("select", {}, ...["X", "Y", "Z", "XY", "XZ", "YZ", "XYZ"].map((d) => h("option", { value: d, text: d })));
    const number = (value, step = "any", min) => h("input", { type: "number", value, step, ...(min ? { min } : {}), attrs: { autocomplete: "off" } });
    const counts = [number("3", "1", "1"), number("1", "1", "1"), number("1", "1", "1")];
    const spacings = [number("1000", "any"), number("1000", "any"), number("1000", "any")];
    const axisRows = ["X", "Y", "Z"].map((name, i) => h("div", { css: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.75rem" } }, labelled(`${name}: number of items`, counts[i]), labelled(`${name}: spacing (${unit})`, spacings[i])));
    const showAxes = () => { axisRows.forEach((row, i) => { row.hidden = !dimension.value.includes("XYZ"[i]); }); };
    dimension.addEventListener("change", showAxes);
    const count = number("4", "1", "2");
    const cx = number("0"), cy = number("0"), angle = number("360");
    const linked = h("input", { type: "checkbox", checked: true, attrs: { "aria-label": "Linked to the source" } });
    const rect = h("div", { css: { display: "grid", gap: "0.75rem" } }, labelled("Directions", dimension, { hint: "X, Y and Z are the model's axes; a combination makes a two- or three-dimensional pattern. Any direction is possible in Properties afterwards." }), ...axisRows);
    const radial = h("div", { css: { display: "grid", gap: "0.75rem" }, hidden: true }, labelled("Number of items", count, { hint: "Counts the originals." }), labelled(`Centre X (${unit})`, cx), labelled(`Centre Y (${unit})`, cy), labelled("Angle (degrees)", angle, { hint: "360 spreads the items evenly round the circle. Any other angle puts the last item on that angle." }));
    mode.addEventListener("change", () => { rect.hidden = mode.value !== "grid"; radial.hidden = mode.value !== "radial"; });
    showAxes();
    const handle = openDialog({
        title: "Array", size: "narrow", focus: () => counts[0],
        body: h("div", { css: { display: "grid", gap: "1rem" } }, labelled("Kind", mode), rect, radial, h("label", { css: { display: "flex", gap: "0.5rem", alignItems: "center" } }, linked, h("span", { text: "Linked: the copies follow the original when it changes" })), h("p", { class: "muted", text: "The array stays editable: select it (Project Browser, Arrays) to change its count, spacing or direction. Change a copy by hand and it becomes independent. A wall brings its doors and windows." })),
        actions: [{ label: "Make array", kind: "primary", run: async () => {
                    const params = { ids: sel.ids, mode: mode.value, linked: linked.checked };
                    if (mode.value === "grid") {
                        const letters = dimension.value.split("");
                        params["dimension"] = dimension.value;
                        params["counts"] = letters.map((l) => Number(counts["XYZ".indexOf(l)].value));
                        params["spacings"] = letters.map((l) => Number(spacings["XYZ".indexOf(l)].value));
                    }
                    else {
                        params["count"] = Number(count.value);
                        params["centre"] = [Number(cx.value), Number(cy.value)];
                        params["angle"] = Number(angle.value);
                    }
                    const out = await store.run("model.array", params);
                    if (!out)
                        return undefined;
                    const ids = out.result?.ids;
                    if (ids?.length)
                        await store.select({ kind: "model", ids: [...sel.ids, ...ids] });
                    return true;
                } }],
    });
    await handle.result;
}
/** Release the array of the selected element: the pattern goes, every copy stays as an ordinary element. */
export async function releaseArray(store) {
    const sel = store.selection;
    if (!store.project || sel?.kind !== "model" || sel.ids.length !== 1) {
        toast("Select an array, or one of its copies, then choose Release array.", { kind: "info" });
        return;
    }
    const out = await store.run("model.release_array", { id: sel.ids[0] });
    if (out)
        toast("The array is released: its copies are ordinary elements now.", { kind: "info" });
}
