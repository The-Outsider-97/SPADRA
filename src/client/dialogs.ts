/** Dialogs: new/open project, unsaved changes, the model-check review, appearance. Built on the shared dialog kit. */
import { h, s, labelled } from "../../../tools/web/src/dom.js";
import { appearanceControls, applyAppearance, DEFAULT_APPEARANCE, normaliseAppearance, type Appearance } from "../../../tools/web/src/theme.js";
import { openDialog, promptDialog, toast } from "../../../tools/web/src/ui.js";
import { flatten, type Store } from "./store.js";

const KEY = "spadra.appearance";

/** Contextual '+' on a selected BIM face; Properties remains the editor for layers already placed. */
export async function addLayerDialog(store: Store, id: string, category: "wall" | "floor" | "roof", side: string): Promise<void> {
  const choices = store.properties?.groups.find((g) => g.title.startsWith("Layers ("))?.rows.find((r) => r.key?.endsWith(":material"))?.choices ?? [];
  const material = h("select", { attrs: { "aria-label": "Layer material" } },
    ...(choices.length ? choices : [{ value: "Concrete", label: "Concrete" }, { value: "Plasterboard", label: "Plasterboard" }]).map((c) => h("option", { value: c.value, text: c.label })));
  material.value = category === "wall" ? "Plasterboard" : material.options[0]?.value ?? "Concrete";
  const thickness = h("input", { type: "number", value: category === "wall" ? "12.5" : "50", min: "1", max: "5000", step: "any", attrs: { "aria-label": "Layer thickness in millimetres" } });
  const openings = h("select", { attrs: { "aria-label": "Opening inheritance" } }, h("option", { value: "all", text: "Inherit all openings and voids" }), h("option", { value: "none", text: "Do not inherit existing cuts" }));
  const boundary = category === "wall" ? ["start", "end", "bottom", "top"].map((edge) => [edge, h("input", { type: "number", value: "0", step: "any", attrs: { "aria-label": `${edge} edge offset in mm` } })] as const) : [];
  const joints = category === "wall" ? (["start", "end"] as const).map((edge) => [edge, h("select", { attrs: { "aria-label": `${edge} wall layer junction behavior` } },
    h("option", { value: "flush", text: "Stop at joined face (no overlap)" }),
    h("option", { value: "penetrate", text: "Allow deliberate penetration past joined face" }))] as const) : [];
  type Mesh = { vertices: number[][]; faces: number[][] };
  const preview = s("svg", { attrs: { viewBox: "0 0 300 210", role: "img", "aria-label": "Live model geometry preview" }, css: { width: "100%", height: "210px", background: "var(--surface)", border: "1px solid var(--hairline)", borderRadius: "10px" } });
  const previewStatus = h("p", { class: "muted", text: "Computing the exact layer geometry before committing…" });
  let requestSerial = 0;
  let pending: ReturnType<typeof setTimeout> | undefined;
  const payload = (): Record<string, unknown> | null => {
    const amount = Number(thickness.value);
    if (!Number.isFinite(amount) || amount < 1 || amount > 5000) return null;
    const edges: Record<string, number> = {};
    for (const [edge, input] of boundary) { const n = Number(input.value); if (!Number.isFinite(n)) return null; edges[edge] = n; }
    const jointPolicy = Object.fromEntries(joints.map(([edge, input]) => [`${edge}_joint`, input.value]));
    return { side, thickness: amount, material: material.value, openings: openings.value, ...edges, ...jointPolicy };
  };
  const drawPreview = (before: Mesh, after: Mesh): void => {
    const project = (v: number[]): [number, number] => [((v[0] ?? 0) - (v[1] ?? 0)) * 0.8660254, ((v[0] ?? 0) + (v[1] ?? 0)) * 0.5 - (v[2] ?? 0)];
    const a = before.vertices.map(project), b = after.vertices.map(project), pts = [...a, ...b];
    if (!pts.length) return;
    const minX = Math.min(...pts.map((p) => p[0])), maxX = Math.max(...pts.map((p) => p[0]));
    const minY = Math.min(...pts.map((p) => p[1])), maxY = Math.max(...pts.map((p) => p[1]));
    const scale = Math.min(274 / Math.max(0.001, maxX - minX), 184 / Math.max(0.001, maxY - minY));
    const toXY = (p: [number, number]): string => `${(150 + (p[0] - (minX + maxX) / 2) * scale).toFixed(2)},${(105 + (p[1] - (minY + maxY) / 2) * scale).toFixed(2)}`;
    const path = (m: Mesh, coords: Array<[number, number]>): string => m.faces.slice(0, 10000).map((face) => `M${face.map((i) => toXY(coords[i] as [number, number])).join("L")}Z`).join("");
    preview.replaceChildren(
      s("path", { attrs: { d: path(before, a), fill: "none", stroke: "var(--muted)", "stroke-width": 0.7, "stroke-dasharray": "3 3" } }),
      s("path", { attrs: { d: path(after, b), fill: "var(--select)", "fill-opacity": 0.09, stroke: "var(--select)", "stroke-width": 0.7 } }),
    );
  };
  const updatePreview = async (): Promise<boolean> => {
    const current = ++requestSerial, params = payload();
    if (!params) { preview.replaceChildren(); previewStatus.textContent = "Enter valid thickness and boundary offsets."; return false; }
    previewStatus.textContent = "Validating geometry and openings…";
    try {
      const result = await store.api.post<{ before: Mesh; after: Mesh; revision: number }>(`/model/${id}/layers/preview`, params);
      if (current !== requestSerial) return false;
      if (result.revision !== store.project?.revision) { previewStatus.textContent = "The project changed. Refresh this layer preview."; return false; }
      drawPreview(result.before, result.after);
      previewStatus.textContent = "Validated preview: grey = existing host, blue = updated geometry. No project changes have been made.";
      return true;
    } catch (error) {
      if (current === requestSerial) { preview.replaceChildren(); previewStatus.textContent = `Geometry cannot be created: ${error instanceof Error ? error.message : String(error)}`; }
      return false;
    }
  };
  const refresh = (): void => { if (pending) clearTimeout(pending); pending = setTimeout(() => { void updatePreview(); }, 180); };
  for (const control of [material, thickness, openings, ...boundary.map(([, input]) => input), ...joints.map(([, input]) => input)]) { control.addEventListener("input", refresh); control.addEventListener("change", refresh); }
  const handle = openDialog<true>({ title: `Add ${side} ${category} layer`, size: "normal",
    body: h("div", { css: { display: "grid", gap: ".65rem" } },
      labelled("Material", material), labelled("Thickness (mm)", thickness), labelled("Opening / void cuts", openings),
      ...boundary.map(([edge, input]) => labelled(`${edge[0]?.toUpperCase()}${edge.slice(1)} boundary (mm)`, input)),
      ...joints.map(([edge, input]) => labelled(`${edge[0]?.toUpperCase()}${edge.slice(1)} junction`, input)),
      preview, previewStatus),
    actions: [{ label: "Add layer", kind: "primary", run: async () => {
      if (pending) clearTimeout(pending);
      if (!await updatePreview()) { toast("Correct the preview error before adding this layer.", { kind: "error" }); return undefined; }
      return (await store.run("model.add_layer", { id, ...payload() })) ? true : undefined;
    } }],
  });
  void updatePreview();
  await handle.result;
  if (pending) clearTimeout(pending);
}

export function loadAppearance(): Appearance {
  try { return normaliseAppearance(JSON.parse(window.localStorage.getItem(KEY) ?? "null") as Partial<Appearance> | null); } catch { return DEFAULT_APPEARANCE; }
}
function saveAppearance(a: Appearance): void { try { window.localStorage.setItem(KEY, JSON.stringify(a)); } catch { /* private window: the choice just lasts this session */ } }

export function appearanceDialog(): void {
  openDialog({ title: "Appearance", size: "narrow", noCancel: true, cancelLabel: "Close", body: appearanceControls(loadAppearance(), (next) => { applyAppearance(next, "soft"); saveAppearance(next); }),
    actions: [{ label: "Done", kind: "primary", run: () => true }] });
}

/** Decide what happens to unsaved work before it is replaced. Resolves "clean" (nothing to lose, or it was just saved), "discard" (the person chose to
 *  throw it away; the server is told when the replacement is actually made, so cancelling afterwards loses nothing), or null (they cancelled). */
export async function ensureClean(store: Store): Promise<"clean" | "discard" | null> {
  if (!store.project?.dirty) return "clean";
  const handle = openDialog<"save" | "discard">({
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

export async function newProjectDialog(store: Store): Promise<void> {
  const state = await ensureClean(store);
  if (!state) return;
  const name = h("input", { type: "text", value: "Untitled project", attrs: { maxlength: 80, autocomplete: "off" } });
  const unit = h("select", {}, ...(store.boot?.units ?? ["mm", "cm", "m"]).map((u) => h("option", { value: u, text: u, selected: u === "mm" })));
  const handle = openDialog<true>({
    title: "New project", size: "narrow", focus: () => name,
    body: h("div", { css: { display: "grid", gap: "1rem" } }, labelled("Project name", name), labelled("Drawing unit", unit, { hint: "The unit drawings use. 3D models are always stored in metres." })),
    actions: [{ label: "Create", kind: "primary", run: async () => ((await store.newProject(name.value.trim() || "Untitled project", unit.value, state === "discard")) ? true : undefined) }],
  });
  await handle.result;
}

export async function openProjectDialog(store: Store): Promise<void> {
  const state = await ensureClean(store);
  if (!state) return;
  const recents = store.boot?.recent ?? [];
  const path = h("input", { type: "text", placeholder: "Folder ending in .spadra", attrs: { autocomplete: "off" } });
  let handle: ReturnType<typeof openDialog<true>> | null = null;
  const open = (value: string): Promise<boolean> => store.openProject(value, state === "discard");
  const list = recents.length
    ? h("ul", { class: "recent-list" }, recents.map((r) => h("li", {}, h("button", { type: "button", on: { click: () => { void open(r.path).then((ok) => { if (ok) handle?.close(true); }); } } },
        h("strong", { text: r.name }), h("span", { class: "path", text: r.path })))))
    : h("p", { class: "muted", text: "No recent projects yet." });
  handle = openDialog<true>({
    title: "Open project", size: "wide", focus: () => path,
    body: h("div", { css: { display: "grid", gap: "1rem" } }, h("div", {}, h("h3", { text: "Recent projects" }), list),
      labelled("Or open by path", path, { hint: `Projects are kept in ${store.boot?.storage.projectsDir ?? "the SPADRA data folder"}. A bare folder name is looked up there.` })),
    actions: [{ label: "Open", kind: "primary", run: async () => (path.value.trim() ? ((await open(path.value.trim())) ? true : undefined) : (toast("Choose a recent project or enter a path.", { kind: "info" }), undefined)) }],
  });
  await handle.result;
}

/** The row of the Project Browser that a check finding is about: an element, a view or a sheet. */
export function findingTarget(store: Store, ref: string): { select?: { model: string }; open?: import("./types.js").OpenRef; label: string } | null {
  const node = flatten(store.tree).find((n) => n.select?.model === ref || (n.open?.id === ref && !n.id.startsWith("model-") && !n.id.startsWith("scene-")));
  return node ? { ...(node.select ? { select: node.select } : {}), ...(node.open ? { open: node.open } : {}), label: node.label } : null;
}

const SEVERITY_TEXT: Record<string, string> = { error: "Error", warning: "Warning", info: "Note" };

/** The model check: what it found (each finding can be shown in the model or the drawing it is about), the counts, and what a check of this kind does not do. */
export function reviewDialog(store: Store): void {
  const check = store.check;
  if (!check) return;
  let handle: ReturnType<typeof openDialog<true>> | null = null;
  const body = check.issues.length
    ? h("ul", { class: "issue-list" }, check.issues.map((i) => {
      const target = findingTarget(store, i.ref);
      return h("li", { data: { severity: i.severity } }, h("strong", { text: `${SEVERITY_TEXT[i.severity] ?? i.severity}: ` }), i.message,
        i.category ? h("span", { class: "muted", text: `  (${i.category})` }) : null,
        target ? h("button", { class: "tool-inline", type: "button", text: "Show", attrs: { "aria-label": `Show ${target.label}` },
          on: { click: () => { handle?.close(); void store.follow({ ...(target.open ? { open: target.open } : {}), ...(target.select ? { select: target.select } : {}) }); } } }) : null);
    }))
    : h("p", { text: "No problems found." });
  const counts = check.counts ? `${check.counts["error"] ?? 0} error(s), ${check.counts["warning"] ?? 0} warning(s), ${check.counts["info"] ?? 0} note(s).` : "";
  handle = openDialog({ title: "Model check", eyebrow: `Revision ${check.revision}`, size: "wide", noCancel: true,
    body: h("div", { css: { display: "grid", gap: "1rem" } }, counts ? h("p", { text: counts }) : null, body, h("p", { class: "muted", text: check.scope }),
      check.limitations?.length ? h("details", {}, h("summary", { text: "What this check does not do" }), h("ul", { class: "issue-list" }, check.limitations.map((l) => h("li", { text: l })))) : null),
    actions: [{ label: "Close", kind: "primary", run: () => true }] });
}


/** Ask for a positive number (an offset distance, a fillet radius). Null if cancelled. Zero is allowed only when `allowZero`. */
export async function numberPrompt(options: { title: string; label: string; value: number; unit: string; allowZero?: boolean; confirmLabel?: string }): Promise<number | null> {
  const answer = await promptDialog({
    title: options.title, label: `${options.label} (${options.unit})`, value: String(options.value), confirmLabel: options.confirmLabel ?? "OK", maxLength: 24,
    validate: (v) => {
      const n = Number(v);
      if (!Number.isFinite(n)) return "Enter a number.";
      if (n < 0 || (n === 0 && !options.allowZero)) return options.allowZero ? "Enter zero or a positive number." : "Enter a number greater than zero.";
      return null;
    },
  });
  return answer === null ? null : Number(answer);
}

export interface HatchChoice { pattern: "solid" | "lines" | "cross"; angle: number; spacing: number | null }

/** Choose how to fill the picked shape. Spacing left empty means "a twentieth of the shape", chosen by the server. */
export async function hatchDialog(unit: string, last: HatchChoice | null): Promise<HatchChoice | null> {
  const pattern = h("select", {}, ...[["lines", "Lines"], ["cross", "Cross-hatch"], ["solid", "Solid"]].map(([v, l]) => h("option", { value: v, text: l, selected: v === (last?.pattern ?? "lines") })));
  const angle = h("input", { type: "text", value: String(last?.angle ?? 45), attrs: { autocomplete: "off", maxlength: 8 } });
  const spacing = h("input", { type: "text", value: last?.spacing ? String(last.spacing) : "", placeholder: "Automatic", attrs: { autocomplete: "off", maxlength: 16 } });
  const sync = (): void => { const solid = pattern.value === "solid"; angle.disabled = solid; spacing.disabled = solid; };
  pattern.addEventListener("change", sync); sync();
  const error = h("p", { class: "muted", attrs: { role: "alert" } });
  const handle = openDialog<HatchChoice>({
    title: "Hatch", size: "narrow", focus: () => pattern,
    body: h("div", { css: { display: "grid", gap: "1rem" } }, labelled("Pattern", pattern), labelled("Angle (degrees)", angle), labelled(`Spacing (${unit})`, spacing, { hint: "Leave empty to let SPADRA choose." }), error),
    actions: [{ label: "Hatch", kind: "primary", run: () => {
      const solid = pattern.value === "solid";
      const a = Number(angle.value), s = spacing.value.trim() === "" ? null : Number(spacing.value);
      if (!solid && (!Number.isFinite(a) || Math.abs(a) > 360)) { error.textContent = "The angle must be a number between -360 and 360."; return undefined; }
      if (!solid && s !== null && !(Number.isFinite(s) && s > 0)) { error.textContent = "The spacing must be a number greater than zero."; return undefined; }
      return { pattern: pattern.value as HatchChoice["pattern"], angle: solid ? 0 : a, spacing: solid ? null : s };
    } }],
  });
  return handle.result;
}

/** After picking the mirror line: keep the originals (a mirrored copy) or replace them. Null if cancelled. */
export async function mirrorKeepDialog(): Promise<boolean | null> {
  const handle = openDialog<boolean>({
    title: "Mirror", size: "narrow", body: h("p", { text: "Keep the original objects, or replace them with their mirror image?" }),
    actions: [{ label: "Replace originals", run: () => false }, { label: "Keep originals", kind: "primary", run: () => true }],
  });
  return handle.result;
}

/** Array: repeat the selected elements along X, Y, Z (or any mix) or round a point, and keep the pattern: the array is an element of its own that regenerates
 *  its copies when its count, spacing, direction or source change (edit it in Properties; "Release array" keeps the copies and drops the pattern). */
export async function arrayDialog(store: Store): Promise<void> {
  const sel = store.selection;
  if (!store.project || sel?.kind !== "model" || !sel.ids.length) { toast("Select what to repeat first (walls, columns, floors, extrusions …), then choose Array.", { kind: "info" }); return; }
  const unit = store.unit;
  const mode = h("select", {}, h("option", { value: "grid", text: "Rectangular: along X, Y and/or Z" }), h("option", { value: "radial", text: "Radial: round a point" }));
  const dimension = h("select", {}, ...["X", "Y", "Z", "XY", "XZ", "YZ", "XYZ"].map((d) => h("option", { value: d, text: d })));
  const number = (value: string, step = "any", min?: string): HTMLInputElement => h("input", { type: "number", value, step, ...(min ? { min } : {}), attrs: { autocomplete: "off" } });
  const counts = [number("3", "1", "1"), number("1", "1", "1"), number("1", "1", "1")];
  const spacings = [number("1000", "any"), number("1000", "any"), number("1000", "any")];
  const axisRows = ["X", "Y", "Z"].map((name, i) => h("div", { css: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.75rem" } },
    labelled(`${name}: number of items`, counts[i]!), labelled(`${name}: spacing (${unit})`, spacings[i]!)));
  const showAxes = (): void => { axisRows.forEach((row, i) => { row.hidden = !dimension.value.includes("XYZ"[i]!); }); };
  dimension.addEventListener("change", showAxes);
  const count = number("4", "1", "2");
  const cx = number("0"), cy = number("0"), angle = number("360");
  const linked = h("input", { type: "checkbox", checked: true, attrs: { "aria-label": "Linked to the source" } });
  const rect = h("div", { css: { display: "grid", gap: "0.75rem" } }, labelled("Directions", dimension, { hint: "X, Y and Z are the model's axes; a combination makes a two- or three-dimensional pattern. Any direction is possible in Properties afterwards." }), ...axisRows);
  const radial = h("div", { css: { display: "grid", gap: "0.75rem" }, hidden: true }, labelled("Number of items", count, { hint: "Counts the originals." }), labelled(`Centre X (${unit})`, cx), labelled(`Centre Y (${unit})`, cy),
    labelled("Angle (degrees)", angle, { hint: "360 spreads the items evenly round the circle. Any other angle puts the last item on that angle." }));
  mode.addEventListener("change", () => { rect.hidden = mode.value !== "grid"; radial.hidden = mode.value !== "radial"; });
  showAxes();
  const handle = openDialog<true>({
    title: "Array", size: "narrow", focus: () => counts[0]!,
    body: h("div", { css: { display: "grid", gap: "1rem" } }, labelled("Kind", mode), rect, radial,
      h("label", { css: { display: "flex", gap: "0.5rem", alignItems: "center" } }, linked, h("span", { text: "Linked: the copies follow the original when it changes" })),
      h("p", { class: "muted", text: "The array stays editable: select it (Project Browser, Arrays) to change its count, spacing or direction. Change a copy by hand and it becomes independent. A wall brings its doors and windows." })),
    actions: [{ label: "Make array", kind: "primary", run: async () => {
      const params: Record<string, unknown> = { ids: sel.ids, mode: mode.value, linked: linked.checked };
      if (mode.value === "grid") {
        const letters = dimension.value.split("");
        params["dimension"] = dimension.value;
        params["counts"] = letters.map((l) => Number(counts["XYZ".indexOf(l)]!.value));
        params["spacings"] = letters.map((l) => Number(spacings["XYZ".indexOf(l)]!.value));
      } else { params["count"] = Number(count.value); params["centre"] = [Number(cx.value), Number(cy.value)]; params["angle"] = Number(angle.value); }
      const out = await store.run("model.array", params);
      if (!out) return undefined;
      const ids = (out.result as { ids?: string[] } | undefined)?.ids;
      if (ids?.length) await store.select({ kind: "model", ids: [...sel.ids, ...ids] });
      return true;
    } }],
  });
  await handle.result;
}

/** Release the array of the selected element: the pattern goes, every copy stays as an ordinary element. */
export async function releaseArray(store: Store): Promise<void> {
  const sel = store.selection;
  if (!store.project || sel?.kind !== "model" || sel.ids.length !== 1) { toast("Select an array, or one of its copies, then choose Release array.", { kind: "info" }); return; }
  const out = await store.run("model.release_array", { id: sel.ids[0] });
  if (out) toast("The array is released: its copies are ordinary elements now.", { kind: "info" });
}


/** A single contextual editor for the existing SGEO/SDRW geometry kernels.
 *  Values shown in metres or degrees are explicitly converted at the boundary;
 *  there are no duplicate frontend implementations of any modifier. */
export function modifierDialog(store: Store, operation: "boolean" | "chamfer" | "weld" | "subdivision" | "bend" | "split"): void {
  const selection = store.selection;
  if (!selection || (selection.kind !== "model" && selection.kind !== "drawing") || !selection.ids.length) {
    toast("Select the geometry to modify first.", { kind: "info" }); return;
  }
  const is3d = selection.kind === "model";
  const drawingId = selection.kind === "drawing" ? selection.view : null;
  if ((operation === "weld" || operation === "subdivision" || operation === "split") && !is3d) {
    toast(`${operation} is available for SGEO geometry only.`, { kind: "info" }); return;
  }
  if (operation === "boolean" && selection.ids.length < 2) {
    toast("Select a source and an operand for Boolean.", { kind: "info" }); return;
  }
  const box = h("div", { css: { display: "grid", gap: ".8rem" } });
  const number = (label: string, initial: string, step = "any"): HTMLInputElement => {
    const field = h("input", { type: "number", value: initial, attrs: { step, "aria-label": label } }) as HTMLInputElement;
    box.append(labelled(label, field));
    return field;
  };
  const choice = (label: string, options: Array<[string, string]>): HTMLSelectElement => {
    const field = h("select", { attrs: { "aria-label": label } }, ...options.map(([value, text]) => h("option", { value, text }))) as HTMLSelectElement;
    box.append(labelled(label, field));
    return field;
  };
  const source = selection.ids[0] as string;
  let operand = selection.ids[1] ?? "";
  let mode: HTMLSelectElement | null = null;
  if (operation === "boolean") {
    mode = choice("Boolean operation", [["union", "Union"], ["subtract", "Difference / Subtract"], ["intersect", "Intersection"]]);
    box.append(h("p", { class: "muted", text: `First selected: ${source}; operand: ${operand}. Difference subtracts the second from the first.` }));
  }
  const amount = operation === "chamfer" ? number("First chamfer distance (m)", "0.05") : null;
  const depth = operation === "chamfer" ? number("Second chamfer distance (m)", "0.05") : null;
  const segmentCount = operation === "chamfer" || operation === "bend" ?
    number(operation === "bend" ? "Bend segments" : "Chamfer segments", operation === "bend" ? "16" : "1", "1") : null;
  const weldDecimals = operation === "weld" ? choice("Vertex matching", [["exact", "Exact coordinates"], ["12", "12 decimal places"], ["9", "9 decimal places"], ["6", "6 decimal places"]]) : null;
  const maxEdge = operation === "subdivision" ? number("Maximum mesh edge (m)", "0.5") : null;
  const axis = operation === "bend" ? choice("Bend axis", is3d ? [["x", "X"], ["y", "Y"], ["z", "Z"]] : [["x", "X"], ["y", "Y"]]) : null;
  const start = operation === "bend" ? number("Bend start coordinate (m)", "0") : null;
  const end = operation === "bend" ? number("Bend end coordinate (m)", "3") : null;
  const angle = operation === "bend" ? number("Bend angle (degrees)", "30") : null;
  const split = operation === "split" ? [
    number("Plane origin X (m)", "0"), number("Plane origin Y (m)", "0"), number("Plane origin Z (m)", "0"),
    number("Plane normal X", "1"), number("Plane normal Y", "0"), number("Plane normal Z", "0")
  ] : null;
  const side = operation === "split" ? choice("Keep split side", [["positive", "Positive side"], ["negative", "Negative side"]]) : null;
  const defaultPolicy = operation === "bend" ? "display_only" : "tolerance_controlled";
  const policy = operation === "boolean" || operation === "chamfer" || operation === "split"
    ? choice("Evaluation policy", [["tolerance_controlled", "Validated tolerance-controlled"], ["exact", "Exact (only when supported)"], ["display_only", "Display-grade approximation"]]) : null;
  if (policy) policy.value = defaultPolicy;
  const note = h("p", { class: "muted", text: is3d
    ? operation === "weld" || operation === "subdivision"
      ? "This mesh operation permanently updates the selected geometry and is reversible through Undo. BIM hosts cannot be modified this way."
      : "The native SGEO modifier remains editable and non-destructive. Only independent generic geometry is eligible."
    : "A native SDRW evaluation is committed as drawing geometry and can be undone." });
  box.append(note);
  type Mesh = { vertices: number[][]; faces: number[][] };
  type DrawingShape = { kind: string; geometry: Record<string, unknown> };
  type Preview = { revision: number; before: Mesh | DrawingShape; after: Mesh | DrawingShape;
    result: { diagnostics?: Record<string, unknown> } };
  const visual = s("svg", { attrs: { viewBox: "0 0 300 200", role: "img", "aria-label": "Computed modifier geometry preview" },
    css: { width: "100%", height: "200px", border: "1px solid var(--hairline)", background: "var(--surface)", borderRadius: "10px" } });
  const status = h("p", { class: "muted", text: "Computing the modifier geometry from a scratch project…" });
  box.append(visual, status);
  let serial = 0, timeout: ReturnType<typeof setTimeout> | undefined;

  const showPreview = (before: Mesh | DrawingShape, after: Mesh | DrawingShape): void => {
    const projected = (mesh: Mesh): Array<[number, number]> => mesh.vertices.map(v =>
      [(v[0] ?? 0) * 0.8660254 - (v[1] ?? 0) * 0.8660254,
       ((v[0] ?? 0) + (v[1] ?? 0)) * 0.5 - (v[2] ?? 0)]);
    const drawing = (shape: DrawingShape): { points: Array<[number, number]>; paths: number[][] } => {
      const g = shape.geometry;
      const coordinates = (raw: unknown): [number, number] =>
        Array.isArray(raw) && raw.length === 2 ? [Number(raw[0]), Number(raw[1])] : [0, 0];
      if (shape.kind === "line") return { points: [coordinates(g["start"]), coordinates(g["end"])], paths: [[0, 1]] };
      if (shape.kind === "polyline") {
        const points = (Array.isArray(g["points"]) ? g["points"] : []).map(coordinates);
        return { points, paths: [points.map((_, i) => i).concat(g["closed"] ? [0] : [])] };
      }
      if (shape.kind === "circle") {
        const center = coordinates(g["center"]), radius = Number(g["radius"]);
        const points: Array<[number, number]> = Array.from({ length: 64 }, (_, i) => [
          center[0] + radius * Math.cos(Math.PI * 2 * i / 64),
          center[1] + radius * Math.sin(Math.PI * 2 * i / 64)]);
        return { points, paths: [points.map((_, i) => i).concat([0])] };
      }
      if (shape.kind === "hatch") {
        const points: Array<[number, number]> = [], paths: number[][] = [];
        for (const loop of (Array.isArray(g["boundary"]) ? g["boundary"] : []) as Array<Record<string, unknown>>) {
          if (loop["kind"] !== "polygon" || !Array.isArray(loop["points"])) continue;
          const start = points.length;
          points.push(...loop["points"].map(coordinates));
          paths.push(Array.from({ length: points.length - start }, (_, i) => start + i).concat([start]));
        }
        return { points, paths };
      }
      return { points: [], paths: [] };
    };
    const renderData = (data: Mesh | DrawingShape) => {
      if ("vertices" in data) return { points: projected(data),
        paths: data.faces.slice(0, 2200).map(face => face.concat([face[0] ?? 0])) };
      return drawing(data);
    };
    const b = renderData(before), a = renderData(after);
    const all = [...b.points, ...a.points].filter(p => p.every(Number.isFinite));
    if (!all.length) { visual.replaceChildren(); return; }
    const minX = Math.min(...all.map(p => p[0])), maxX = Math.max(...all.map(p => p[0]));
    const minY = Math.min(...all.map(p => p[1])), maxY = Math.max(...all.map(p => p[1]));
    const scale = Math.min(272 / Math.max(1e-9, maxX - minX), 170 / Math.max(1e-9, maxY - minY));
    const xy = (p: [number, number]): string => `${(150 + (p[0] - (minX + maxX) / 2) * scale).toFixed(2)},${(100 + (p[1] - (minY + maxY) / 2) * scale).toFixed(2)}`;
    const add = (data: ReturnType<typeof renderData>, color: string, dash: string) => {
      const path = data.paths.filter(indices => indices.length > 1 && indices.every(i => data.points[i]?.every(Number.isFinite)))
        .map(indices => `M${indices.map(i => xy(data.points[i] as [number, number])).join("L")}`).join("");
      if (!path) return null;
      return s("path", { attrs: { d: path, fill: "none", stroke: color, "stroke-width": "1.1", "stroke-dasharray": dash } });
    };
    const paths = [add(b, "#8796a5", "3,3"), add(a, "#2677c9", "")];
    visual.replaceChildren(...paths.filter((node): node is SVGPathElement => node !== null));
  };

  const payload = (): Record<string, unknown> | null => {
    const finite = (field: HTMLInputElement | null): number => field ? Number(field.value) : NaN;
    let parameters: Record<string, unknown>;
    switch (operation) {
      case "boolean": parameters = { operation: mode?.value }; break;
      case "chamfer": parameters = { amount: finite(amount), depth: finite(depth), segments: finite(segmentCount) }; break;
      case "weld": parameters = { decimals: weldDecimals?.value === "exact" ? null : Number(weldDecimals?.value) }; break;
      case "subdivision": parameters = { max_edge: finite(maxEdge) }; break;
      case "bend": parameters = { axis: axis?.value, start: finite(start), end: finite(end),
        angle: finite(angle) * Math.PI / 180, segments: finite(segmentCount) }; break;
      case "split": parameters = { origin: split?.slice(0, 3).map(x => Number(x.value)),
        normal: split?.slice(3).map(x => Number(x.value)), side: side?.value }; break;
    }
    const values = Object.values(parameters).flatMap(v => Array.isArray(v) ? v : [v]);
    if (values.some(v => typeof v === "number" && !Number.isFinite(v))) return null;
    return { operation, id: source, ...(operand ? { operand } : {}), parameters,
      ...(policy ? { policy: policy.value } : {}),
      ...(drawingId ? { drawing: drawingId } : {}) };
  };
  const updatePreview = async (): Promise<boolean> => {
    const ticket = ++serial;
    const params = payload();
    if (!params) { status.textContent = "Enter valid finite numbers before applying the modifier."; visual.replaceChildren(); return false; }
    status.textContent = "Evaluating native geometry (read-only)…";
    try {
      const result = await store.api.post<Preview>("/modifier/preview", { kind: is3d ? "model" : "drawing", ...params });
      if (ticket !== serial) return false;
      if (result.revision !== store.project?.revision) {
        status.textContent = "Project revision changed; recompute before applying."; return false;
      }
      showPreview(result.before, result.after);
      status.textContent = `Verified scratch geometry: dashed = before, blue = after. ${JSON.stringify(result.result.diagnostics ?? {})}`;
      return true;
    } catch (error) {
      if (ticket === serial) { visual.replaceChildren(); status.textContent = `Geometry refused: ${error instanceof Error ? error.message : String(error)}`; }
      return false;
    }
  };
  const refresh = (): void => {
    if (timeout) clearTimeout(timeout);
    timeout = setTimeout(() => { void updatePreview(); }, 180);
  };
  box.querySelectorAll("input,select").forEach(control => {
    control.addEventListener("input", refresh); control.addEventListener("change", refresh);
  });
  const handle = openDialog({
    title: `${operation[0]?.toUpperCase()}${operation.slice(1)} geometry`,
    size: "normal", body: box,
    actions: [{ label: "Apply modifier", kind: "primary", run: async () => {
      if (timeout) clearTimeout(timeout);
      const ok = await updatePreview();
      if (!ok) { toast("Correct the geometry preview error before applying this modifier.", { kind: "error" }); return undefined; }
      const params = payload();
      if (!params) return undefined;
      const done = await store.run(is3d ? "model.modifier" : "draw.modifier", params);
      if (!done) return undefined;
      const result = done.result as { id?: string; mode?: string; diagnostics?: Record<string, unknown> };
      store.setStatus(`${operation} applied (${result.mode ?? "native"}). ${JSON.stringify(result.diagnostics ?? {})}`);
      if (result.id) {
        if (is3d) await store.select({ kind: "model", ids: [result.id] });
        else await store.select({ kind: "drawing", view: drawingId as string, ids: [result.id] });
      }
      return true;
    } }],
  });
  void updatePreview();
  void handle.result.then(() => { serial++; if (timeout) clearTimeout(timeout); });
}
