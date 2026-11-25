/** Profiler's side panels: Layers, Parameters, Constraints (dimensions, constraints, chains), Properties and Check. Each is a function of the current view;
 *  every control calls one server operation through the host and the whole panel is rebuilt from what comes back. */
import { h, replace } from "../../../tools/web/src/dom.js";
import { glyph } from "../../../tools/web/src/icons.js";
import { confirmDialog } from "../../../tools/web/src/ui.js";
import type { ProfilerCanvas } from "./profiler_canvas.js";
import type { PfCheck, PfDim, PfRender, PfView, Pt } from "./profiler_api.js";
import { formDialog, type Sel } from "./profiler_tools.js";

export interface PanelHost {
  view(): PfView; canvas: ProfilerCanvas; selection(): Sel;
  perform(op: string, args: Record<string, unknown>, label?: string): Promise<boolean>;
  say(message: string): void;
  currentLayer: string; setLayer(name: string): void;
  check: PfCheck | null; checkPurpose: string; runCheck(purpose: string): Promise<void>; repair(action: string, apply: boolean): Promise<void>;
  openComment(id: string): void;
  zoomTo(points: Pt[]): void;
}

const fmt = (n: unknown): string => (typeof n === "number" ? String(+n.toPrecision(8)) : String(n));
const btn = (label: string, run: () => unknown, icon?: string, title?: string): HTMLButtonElement =>
  h("button", { class: "btn tiny", type: "button", attrs: { title: title ?? label }, on: { click: () => { void run(); } } }, icon ? glyph(icon) : null, icon ? null : label);
const iconBtn = (icon: string, label: string, run: () => unknown, pressed?: boolean): HTMLButtonElement =>
  h("button", { class: "iconbtn tiny", type: "button", attrs: { "aria-label": label, title: label, ...(pressed === undefined ? {} : { "aria-pressed": String(pressed) }) }, on: { click: () => { void run(); } } }, glyph(icon));
function commit(input: HTMLInputElement, run: (value: string) => void | Promise<void>): void {
  let done = input.value;
  const go = (): void => { if (input.value !== done) { done = input.value; void run(input.value); } };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); go(); } else if (e.key === "Escape") { input.value = done; input.blur(); } });
  input.addEventListener("blur", go);
}

// ------------------------------------------------------------------------------------------------------------- layers
export function layersPanel(host: PanelHost): HTMLElement {
  const r = host.view().render;
  const rows = r.layers.map((l) => {
    const count = r.edges.filter((e) => e.layer === l.name).length;
    const name = h("input", { type: "text", value: l.name, attrs: { "aria-label": `Name of layer ${l.name}`, ...(l.name === "0" ? { disabled: "" } : {}) } });
    commit(name, (v) => { if (v.trim() && v !== l.name) void host.perform("layer_rename", { name: l.name, new_name: v.trim() }, "Rename layer"); });
    return h("div", { class: `pf-row${host.currentLayer === l.name ? " current" : ""}` },
      iconBtn(l.visible ? "pf-eye" : "pf-eye-off", l.visible ? `Hide ${l.name}` : `Show ${l.name}`, () => host.perform("layer_set", { name: l.name, visible: !l.visible }, "Layer visibility")),
      iconBtn(l.locked ? "lock" : "pf-unlock", l.locked ? `Unlock ${l.name}` : `Lock ${l.name}`, () => host.perform("layer_set", { name: l.name, locked: !l.locked }, "Layer lock")),
      name, h("span", { class: "pf-count", text: String(count), attrs: { title: `${count} lines on this layer` } }),
      btn("Use", () => host.setLayer(l.name), undefined, `New lines go on ${l.name}`),
      btn("Move here", () => { const s = host.selection(); if (!s.ids.length) return host.say("Select what to move to this layer first."); return void host.perform("move_to_layer", { ids: s.ids, layer: l.name }, "Move to layer"); }, undefined, "Move the selection to this layer"),
      l.name === "0" ? null : iconBtn("trash", `Delete layer ${l.name}`, async () => { if (await confirmDialog({ title: "Delete layer", message: `Delete layer ${l.name}? Its ${count} line${count === 1 ? "" : "s"} move to layer 0.`, confirmLabel: "Delete", danger: true })) await host.perform("layer_delete", { name: l.name, move_to: "0" }, "Delete layer"); }));
  });
  const add = h("input", { type: "text", placeholder: "New layer name", attrs: { "aria-label": "New layer name", maxlength: 64 } });
  const addRun = (): void => { const n = add.value.trim(); if (n) void host.perform("layer_add", { name: n }, "Add layer").then((ok) => { if (ok) add.value = ""; }); };
  add.addEventListener("keydown", (e) => { if (e.key === "Enter") addRun(); });
  return h("div", { class: "pf-panel-body" }, h("p", { class: "note", text: `New lines go on layer “${host.currentLayer}”. Locked layers cannot be drawn on or edited.` }), ...rows, h("div", { class: "pf-add" }, add, btn("Add", addRun)));
}

// --------------------------------------------------------------------------------------------------------- parameters
export function parametersPanel(host: PanelHost): HTMLElement {
  const v = host.view(); const rows = v.params.parameters;
  const list = rows.map((p) => {
    const value = h("input", { type: "text", value: p.expression ?? fmt(p.value), attrs: { "aria-label": `Value or formula of ${p.name}`, ...(p.read_only ? { disabled: "" } : {}) }, class: p.valid ? "" : "invalid" });
    commit(value, (txt) => {
      const t = txt.trim(); if (t === "") return;
      const numeric = /^[-+]?\d*\.?\d+(e[-+]?\d+)?$/i.test(t);
      void host.perform("param_set", numeric ? { name: p.name, value: Number(t) } : { name: p.name, expression: t }, `Set ${p.name}`);
    });
    const info = [p.expression ? `= ${fmt(p.value)} ${p.unit}` : p.unit, p.depends_on.length ? `uses ${p.depends_on.join(", ")}` : "", p.used_by.length ? `used by ${p.used_by.join(", ")}` : "", p.dimensions.length ? `drives ${p.dimensions.length} dimension${p.dimensions.length === 1 ? "" : "s"}` : ""].filter(Boolean).join(" · ");
    return h("div", { class: "pf-param" }, h("div", { class: "pf-row" }, h("strong", { text: p.name }), value,
      iconBtn("trash", `Delete parameter ${p.name}`, () => host.perform("param_remove", { name: p.name }, "Delete parameter"))),
      h("small", { class: p.valid ? "note" : "note error", text: p.error ?? info }));
  });
  const name = h("input", { type: "text", placeholder: "Name", attrs: { "aria-label": "New parameter name", maxlength: 40 } });
  const type = h("select", { attrs: { "aria-label": "Type" } }, ...["length", "angle", "number", "integer", "boolean"].map((t) => h("option", { value: t, text: t })));
  const val = h("input", { type: "text", placeholder: "Value or formula, e.g. Width / 3", attrs: { "aria-label": "Value or formula" } });
  const addRun = (): void => {
    const n = name.value.trim(), t = val.value.trim(); if (!n || !t) return host.say("Give the parameter a name and a value or formula.");
    const numeric = /^[-+]?\d*\.?\d+(e[-+]?\d+)?$/i.test(t);
    void host.perform("param_add", { name: n, type: type.value, ...(numeric ? { value: Number(t) } : t === "true" || t === "false" ? { value: t === "true" } : { expression: t }) }, "Add parameter").then((ok) => { if (ok) { name.value = ""; val.value = ""; } });
  };
  val.addEventListener("keydown", (e) => { if (e.key === "Enter") addRun(); });
  const variants = v.params.variants ?? [];
  const variantPick = h("select", { attrs: { "aria-label": "Saved variant" } }, h("option", { value: "", text: variants.length ? "Variants…" : "No saved variants" }), ...variants.map((n) => h("option", { value: n, text: n })));
  variantPick.addEventListener("change", () => { if (variantPick.value) void host.perform("variant_apply", { name: variantPick.value }, "Apply variant"); });
  // The colour of the part in SPADRA is a parameter too: it shows (and can be changed) in the element's Profile parameters there.
  const part = typeof v.params["colour"] === "string" ? String(v.params["colour"]) : "";
  const picker = h("input", { type: "color", value: part || "#B8BABF", attrs: { "aria-label": "Part colour" } });
  picker.addEventListener("change", () => { void host.perform("part_color", { color: picker.value }, "Part colour"); });
  const colourRow = h("div", { class: "pf-param" }, h("div", { class: "pf-row" }, h("strong", { text: "colour" }), picker,
    part ? iconBtn("close", "Remove the part colour", () => host.perform("part_color", { color: "" }, "Part colour")) : null),
    h("small", { class: "note", text: part ? `${part}: the colour of the part in SPADRA (lines you colour are painted on their own faces)` : "No part colour: the part keeps its material colour in SPADRA." }));
  return h("div", { class: "pf-panel-body" }, colourRow,
    rows.length ? null : h("p", { class: "note", text: "No number parameters yet. Add one, then link a driving dimension to it." }), ...list,
    h("div", { class: "pf-add col" }, h("div", { class: "pf-row" }, name, type), val, h("div", { class: "pf-row" }, btn("Add parameter", addRun))),
    h("div", { class: "pf-add" }, variantPick, btn("Save variant", async () => { const f = await formDialog("Save variant", [{ key: "name", label: "Variant name", kind: "text", value: "" }], "Save", "Stores every parameter that has no formula."); if (f && String(f["name"]).trim()) await host.perform("variant_save", { name: String(f["name"]).trim() }, "Save variant"); })));
}

// -------------------------------------------------------------------------------------------------------- constraints
const CONSTRAINT_NAMES: Record<string, string> = { horizontal: "Horizontal", vertical: "Vertical", parallel: "Parallel", perpendicular: "Perpendicular", collinear: "Collinear", equal: "Equal", tangent: "Tangent", tangent_at: "Tangent at a point",
  concentric: "Concentric", coincident: "Coincident", point_on: "Point on line", midpoint: "Midpoint", midpoint_at: "Midpoint", symmetric: "Symmetric", fixed: "Fixed", distance: "Distance", hdistance: "Horizontal distance",
  vdistance: "Vertical distance", angle: "Angle", radius: "Radius", diameter: "Diameter", point_line_distance: "Distance to line", equal_distance: "Equal spacing" };
export function constraintsPanel(host: PanelHost): HTMLElement {
  const v = host.view(); const r = v.render, d = v.diagnostics;
  const statusText = d.status === "full" ? "Fully constrained" : d.status === "under" ? `Under-constrained: ${d.dof} degree${d.dof === 1 ? "" : "s"} of freedom` : d.status === "conflicting" ? "Conflicting constraints" : "Invalid";
  const head = h("div", { class: `pf-status ${d.status}` }, h("strong", { text: statusText }), d.message && d.status !== "full" ? h("small", { text: String(d.message) }) : null);
  const dimRows = r.dimensions.map((x) => dimensionRow(host, x));
  const cons = r.constraints.filter((k) => k.origin !== "temporary" && k.origin !== "dimension").map((k) =>
    h("div", { class: `pf-row${k.state === "ok" ? "" : " bad"}`, on: { click: () => host.canvas.setSelection(k.refs) } },
      iconBtn(k.enabled ? "check-circle" : "circle", k.enabled ? "Turn off" : "Turn on", () => host.perform("set_constraint_enabled", { constraint: k.id, enabled: !k.enabled }, "Constraint on/off")),
      h("span", { class: "grow", text: `${CONSTRAINT_NAMES[k.kind] ?? k.kind}${k.value !== null ? ` = ${fmt(k.value)}` : ""}${k.origin !== "user" ? ` (${k.origin})` : ""}` }),
      iconBtn("trash", "Delete constraint", () => host.perform("remove_constraint", { constraint: k.id }, "Delete constraint"))));
  const chains = Object.values(r.chains as Record<string, ChainRec>).map((c) => chainCard(host, c, r));
  return h("div", { class: "pf-panel-body" }, head,
    h("h4", { text: `Dimensions (${dimRows.length})` }), dimRows.length ? dimRows : h("p", { class: "note", text: "None. Use Insert → Dimension." }),
    chains.length ? h("h4", { text: `Dimension chains (${chains.length})` }) : null, ...chains,
    h("h4", { text: `Constraints (${cons.length})` }), cons.length ? cons : h("p", { class: "note", text: "None. Use Modifications → Constrain." }));
}
interface ChainRec { id: string; vertices: string[]; segments: string[]; total: string | null; eq: boolean; locked_vertices: string[] }
function dimensionRow(host: PanelHost, d: PfDim): HTMLElement {
  const label = h("span", { class: "grow", text: `${d.name ?? d.type}: ${d.text}${d.param ? `  [${d.param}]` : ""}${d.expr ? `  = ${d.expr}` : ""}` });
  const mode = h("select", { attrs: { "aria-label": `Mode of ${d.type} dimension` } }, ...[["reference", "Reference"], ["driving", "Driving"], ["formula", "Formula"], ["temporary", "Temporary"]].map(([m, t]) => h("option", { value: m, text: t, selected: m === d.mode })));
  mode.addEventListener("change", () => { void host.perform("dimension_mode", { dimension: d.id, mode: mode.value }, "Dimension mode"); });
  return h("div", { class: `pf-dim${d.state === "ok" && !d.unresolved ? "" : " bad"}` }, h("div", { class: "pf-row", on: { click: () => host.canvas.setSelection([d.id]) } }, label,
    iconBtn("trash", "Delete dimension", () => host.perform("dimension_remove", { dimension: d.id }, "Delete dimension"))), h("div", { class: "pf-row" }, mode,
    d.mode === "driving" || d.mode === "formula" ? btn("Edit…", () => editDimension(host, d)) : null),
    d.unresolved ? h("small", { class: "note error", text: "Its geometry was replaced by an edit; re-attach or delete it." }) : null);
}
export async function editDimension(host: PanelHost, d: PfDim): Promise<void> {
  const linear = ["aligned", "horizontal", "vertical", "length", "center_distance"].includes(d.type);
  const f = await formDialog("Edit dimension", [
    { key: "expr", label: "Value or formula", kind: "text", value: d.expr ?? String(+d.value.toPrecision(10)), note: "A number, or a formula such as Width - Return." },
    ...(linear ? [{ key: "anchor", label: "Which end stays fixed", kind: "select" as const, value: d.anchor, options: [["none", "Neither: least movement"], ["start", "Start stays"], ["end", "End stays"], ["center", "Centre stays"]] as Array<[string, string]> }] : [])], "Apply");
  if (!f) return;
  const t = String(f["expr"]).trim(); const numeric = /^[-+]?\d*\.?\d+(e[-+]?\d+)?$/i.test(t);
  await host.perform("dimension_value", { dimension: d.id, ...(numeric ? { value: Number(t) } : { expr: t }), ...(linear ? { anchor: f["anchor"] } : {}) }, "Edit dimension");
}
function chainCard(host: PanelHost, c: ChainRec, r: PfRender): HTMLElement {
  const segs = c.segments.map((id) => r.dimensions.find((x) => x.id === id)).filter((x): x is PfDim => !!x);
  const total = c.total ? r.dimensions.find((x) => x.id === c.total) : undefined;
  const totalIn = h("input", { type: "text", value: total ? String(+total.value.toPrecision(10)) : "", attrs: { "aria-label": "Chain total length" } });
  const policy = h("select", { attrs: { "aria-label": "How the change is shared" } }, ...[["min_change", "Change the last segment"], ["equal_growth", "Share equally"], ["proportional", "Share in proportion"]].map(([m, t]) => h("option", { value: m, text: t })));
  commit(totalIn, (txt) => { const n = Number(txt); if (Number.isFinite(n) && n > 0) void host.perform("chain_total", { chain: c.id, total: n, policy: policy.value }, "Chain total"); });
  const locked = new Set(c.locked_vertices);
  return h("div", { class: "pf-chain" }, h("div", { class: "pf-row" }, h("strong", { text: `Chain of ${segs.length}: ${segs.map((s) => fmt(+s.value.toPrecision(6))).join(" + ")}${total ? ` = ${fmt(+total.value.toPrecision(8))}` : ""}` })),
    h("div", { class: "pf-row" }, h("span", { text: "Total" }), totalIn, policy),
    h("div", { class: "pf-row wrap" }, ...segs.map((s, i) => h("label", { class: "check" }, h("input", { type: "checkbox", checked: s.mode === "driving", on: { change: (e) => { void host.perform("chain_lock_segment", { chain: c.id, index: i, locked: (e.target as HTMLInputElement).checked }, "Lock segment"); } } }), `Lock ${i + 1}`))),
    h("div", { class: "pf-row wrap" }, ...[0, c.vertices.length - 1].map((idx) => { const vid = c.vertices[idx]!; return h("label", { class: "check" }, h("input", { type: "checkbox", checked: locked.has(vid), on: { change: (e) => {
      const next = new Set(locked); if ((e.target as HTMLInputElement).checked) next.add(vid); else next.delete(vid); void host.perform("chain_end_locks", { chain: c.id, vertices: [...next] }, "End lock"); } } }), idx === 0 ? "Start fixed" : "End fixed"); }),
      h("label", { class: "check" }, h("input", { type: "checkbox", checked: c.eq, on: { change: (e) => { void host.perform("chain_eq", { chain: c.id, on: (e.target as HTMLInputElement).checked }, "Equal segments"); } } }), "Keep segments equal")));
}

// -------------------------------------------------------------------------------------------------------- properties
export function propertiesPanel(host: PanelHost): HTMLElement {
  const s = host.selection(); const r = host.view().render;
  if (!s.ids.length && !s.dims.length && !s.comments.length && !s.texts.length) {
    const b = r.bounds;
    return h("div", { class: "pf-panel-body" }, h("h4", { text: r.name }), kv("Unit", r.unit), kv("Lines", String(r.edges.length)), kv("Points", String(r.vertices.length)), kv("Size", b ? `${fmt(+(b[2] - b[0]).toPrecision(8))} × ${fmt(+(b[3] - b[1]).toPrecision(8))}` : "empty"),
      kv("Revision", String(r.revision)), h("p", { class: "note", text: "Select something to see its properties." }));
  }
  const out: Array<HTMLElement | null> = [];
  if (s.edges.length) {
    const es = r.edges.filter((e) => s.edges.includes(e.id));
    out.push(h("h4", { text: es.length === 1 ? `${es[0]!.type[0]!.toUpperCase()}${es[0]!.type.slice(1)}` : `${es.length} lines` }));
    if (es.length === 1) { const e = es[0]!; out.push(kv("Layer", e.layer), kv("Colour", e.color), e.radius !== undefined ? kv("Radius", fmt(+e.radius.toPrecision(8))) : null, kv("Length", fmt(+polyLength(e.points).toPrecision(8)))); }
    const allConstruction = es.every((e) => e.construction), allLocked = es.every((e) => e.locked);
    out.push(h("div", { class: "pf-row" }, h("label", { class: "check" }, h("input", { type: "checkbox", checked: allConstruction, on: { change: (ev) => { void host.perform("construction", { ids: s.edges, on: (ev.target as HTMLInputElement).checked }, "Construction"); } } }), "Construction line"),
      h("label", { class: "check" }, h("input", { type: "checkbox", checked: allLocked, on: { change: (ev) => { void host.perform("lock", { ids: s.edges, on: (ev.target as HTMLInputElement).checked }, "Lock"); } } }), "Locked")));
    const col = h("input", { type: "text", value: es.length === 1 ? es[0]!.color : "", placeholder: "#RRGGBB", attrs: { "aria-label": "Line colour" } });
    commit(col, (t) => { void host.perform("color", { ids: s.edges, color: t.trim() || null }, "Colour"); });
    out.push(h("div", { class: "pf-row" }, h("span", { text: "Colour" }), col, btn("Default", () => host.perform("color", { ids: s.edges, color: null }, "Default colour").then(() => undefined))));
  }
  if (s.vertices.length) { const vs = r.vertices.filter((x) => s.vertices.includes(x.id)); out.push(h("h4", { text: vs.length === 1 ? "Point" : `${vs.length} points` }));
    if (vs.length === 1) { const v = vs[0]!; const xi = h("input", { type: "text", value: fmt(+v.x.toPrecision(10)), attrs: { "aria-label": "X" } }), yi = h("input", { type: "text", value: fmt(+v.y.toPrecision(10)), attrs: { "aria-label": "Y" } });
      const go = (): void => { const x = Number(xi.value), y = Number(yi.value); if (Number.isFinite(x) && Number.isFinite(y) && (x !== v.x || y !== v.y)) void host.perform("drag_vertex", { vertex: v.id, to: [x, y] }, "Move point"); };
      xi.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); }); yi.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); }); xi.addEventListener("blur", go); yi.addEventListener("blur", go);
      out.push(h("div", { class: "pf-row" }, h("span", { text: "X" }), xi, h("span", { text: "Y" }), yi), btn("Fix this point", () => host.perform("pin", { vertex: v.id }, "Fix point").then(() => undefined))); } }
  for (const id of s.dims) { const d = r.dimensions.find((x) => x.id === id); if (d) out.push(h("h4", { text: `${d.type} dimension` }), dimensionRow(host, d)); }
  for (const id of s.comments) { const c = r.comments.find((x) => x.id === id); if (c) out.push(h("h4", { text: "Comment" }), h("p", { text: c.text }), btn("Open", () => host.openComment(c.id))); }
  for (const id of s.texts) { const t = r.texts.find((x) => x.id === id); if (t) out.push(h("h4", { text: "Text" }), kv("Text", t.text), kv("Height", fmt(t.height))); }
  return h("div", { class: "pf-panel-body" }, ...out);
}
const kv = (k: string, v: string): HTMLElement => h("div", { class: "pf-kv" }, h("span", { text: k }), h("span", { text: v }));
function polyLength(p: Pt[]): number { let t = 0; for (let i = 0; i + 1 < p.length; i++) t += Math.hypot(p[i + 1]![0] - p[i]![0], p[i + 1]![1] - p[i]![1]); return t; }

// ------------------------------------------------------------------------------------------------------------- check
export const checkSummary = (m: PfCheck["summary"]): string => {
  if (typeof m === "string") return m;
  const n = (x: number | undefined, one: string, many: string): string | null => (x === undefined ? null : `${x} ${x === 1 ? one : many}`);
  return [n(m.edges, "edge", "edges"), n(m.regions, "region", "regions"), m.open_ends ? n(m.open_ends, "open end", "open ends") : null, m.area !== undefined ? `area ${Number(m.area.toFixed(2))}` : null,
    m.dof === undefined ? null : m.dof === 0 ? "fully constrained" : `${m.dof} free`].filter(Boolean).join(" · ");
};
export const PURPOSES: Array<[string, string]> = [["drafting", "Drafting"], ["extrude", "Extrusion"], ["void", "Void"], ["opening", "Opening profile"], ["sweep_section", "Sweep section"], ["sweep_path", "Sweep path"], ["revolve", "Revolve"]];
export function checkPanel(host: PanelHost): HTMLElement {
  const sel = h("select", { attrs: { "aria-label": "What the profile is for" } }, ...PURPOSES.map(([v, t]) => h("option", { value: v, text: t, selected: v === host.checkPurpose })));
  sel.addEventListener("change", () => { void host.runCheck(sel.value); });
  const c = host.check;
  const body: Array<HTMLElement | null> = [h("div", { class: "pf-row" }, sel, btn("Check profile", () => host.runCheck(sel.value)))];
  if (!c) body.push(h("p", { class: "note", text: "Choose what the profile is for and run the check. It looks for open contours, crossings, duplicates, zero-length lines, broken references and constraint conflicts." }));
  else {
    body.push(h("div", { class: `pf-status ${c.suitable ? "full" : "conflicting"}` }, h("strong", { text: c.suitable ? `Suitable for ${PURPOSES.find(([k]) => k === c.purpose)?.[1] ?? c.purpose}` : "Not suitable yet" }), h("small", { text: checkSummary(c.summary) })));
    for (const i of c.issues) {
      body.push(h("div", { class: `pf-issue ${i.severity}`, on: { click: () => { if (i.refs.length) host.canvas.setSelection(i.refs); if (i.point) host.zoomTo([i.point]); } } },
        h("span", { class: "pf-sev", text: i.severity === "error" ? "Error" : i.severity === "warning" ? "Warning" : "Note" }), h("span", { class: "grow", text: i.message }),
        i.repair ? btn("Preview fix", () => host.repair(i.repair!, false), undefined, "Show what fixing this would change, without changing it") : null,
        i.repair ? btn("Fix", () => host.repair(i.repair!, true), undefined, "Apply the fix (it can be undone)") : null));
    }
    if (!c.issues.length) body.push(h("p", { class: "note", text: "No problems found." }));
  }
  return h("div", { class: "pf-panel-body" }, ...body);
}
export { replace };
