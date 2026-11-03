/** Gutters and rainwater drainage: put gutters (and downpipes) on the eaves of the selected roof, add a downpipe to a gutter, and see the rain each
 *  gutter and downpipe takes. The server does all the work; these dialogs only gather the choices. */
import { h, labelled } from "../../../tools/web/src/dom.js";
import { openDialog, toast } from "../../../tools/web/src/ui.js";
import type { Store } from "./store.js";

export interface DrainageDownpipe { id: string; name: string; diameter_mm: number; flow_l_s: number }
export interface DrainageGutter { id: string; name: string; length_m: number; catchment_m2: number; rainfall_l_s_m2: number; flow_l_s: number; downpipes: DrainageDownpipe[] }
export interface DrainageRoof {
  id: string; name: string; roof: string; plan_area_m2: number; drained_m2: number; undrained_m2: number;
  eaves: Array<{ edge: number; length_m: number; gutter: string | null }>; gutters: DrainageGutter[];
}
export interface Drainage { roofs: DrainageRoof[]; note: string }

/** The nominal sizes a type starts from (mm); the server holds the same table and checks every value, and each can be changed afterwards. */
const TYPES: Array<{ key: string; label: string; width: number; depth: number }> = [
  { key: "half_round", label: "Half-round", width: 125, depth: 62.5 },
  { key: "ogee", label: "Ogee (K-style)", width: 125, depth: 100 },
  { key: "box", label: "Box", width: 100, depth: 100 },
  { key: "fascia", label: "Fascia", width: 115, depth: 90 },
];
const MATERIALS = ["Zinc", "Aluminium", "Steel", "Copper", "PVC"];

function selectedOf(store: Store, category: string): string | null {
  const sel = store.selection;
  if (sel?.kind !== "model" || sel.ids.length !== 1) return null;
  return store.properties?.category === category ? (sel.ids[0] as string) : null;
}

async function drainage(store: Store, roof?: string): Promise<Drainage | null> {
  try { return await store.api.post<Drainage>("/drainage", roof ? { roof } : {}); } catch (e) { toast(e instanceof Error ? e.message : "Could not read the drainage.", { kind: "error" }); return null; }
}

/** Gutters on the eaves of the selected roof. */
export async function gutterDialog(store: Store): Promise<void> {
  if (!store.project) return;
  const roof = selectedOf(store, "roof");
  if (!roof) { toast("Select a roof first (click it in the 3D view or in the browser), then choose Gutter.", { kind: "info" }); return; }
  const info = (await drainage(store, roof))?.roofs[0];
  if (!info) return;
  const free = info.eaves.filter((e) => !e.gutter);
  if (!free.length) { toast("Every eave of this roof already has a gutter. Select a gutter to change it in Properties.", { kind: "info" }); return; }
  const number = (value: string, step = "any", min?: string): HTMLInputElement => h("input", { type: "number", value, step, ...(min ? { min } : {}), attrs: { autocomplete: "off" } });
  const kind = h("select", {}, ...TYPES.map((t) => h("option", { value: t.key, text: t.label })));
  const width = number("125", "1", "30"), depth = number("62.5", "0.5", "20");
  const fall = number("3", "0.5", "0");
  const material = h("select", {}, ...MATERIALS.map((m) => h("option", { value: m, text: m })));
  const pipe = h("input", { type: "checkbox", checked: true, attrs: { "aria-label": "Add a downpipe at the low end of each gutter" } }) as HTMLInputElement;
  const diameter = number("80", "5", "20");
  const boxes = free.map((e) => ({ edge: e.edge, box: h("input", { type: "checkbox", checked: true, attrs: { "aria-label": `Eave ${e.edge + 1}` } }) as HTMLInputElement }));
  kind.addEventListener("change", () => { const t = TYPES.find((x) => x.key === kind.value); if (t) { width.value = String(t.width); depth.value = String(t.depth); } });
  pipe.addEventListener("change", () => { diameter.disabled = !pipe.checked; });
  const handle = openDialog<true>({
    title: `Gutters on ${info.name}`, size: "narrow", focus: () => kind,
    body: h("div", { css: { display: "grid", gap: "0.9rem" } },
      labelled("Gutter type", kind, { hint: "Only the starting shape and size: everything can be changed afterwards in Properties, including the type." }),
      h("div", { css: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.75rem" } }, labelled("Width (mm)", width), labelled("Depth (mm)", depth)),
      h("div", { css: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.75rem" } }, labelled("Fall (mm per m)", fall), labelled("Material", material)),
      h("fieldset", { css: { display: "grid", gap: "0.35rem", border: "1px solid var(--line, #8884)", padding: "0.5rem 0.75rem" } }, h("legend", { text: "Eaves" }),
        ...boxes.map(({ edge, box }) => h("label", { css: { display: "flex", gap: "0.5rem", alignItems: "center" } }, box, `Eave ${edge + 1} · ${(info.eaves.find((e) => e.edge === edge)?.length_m ?? 0).toFixed(2)} m`))),
      h("label", { css: { display: "flex", gap: "0.5rem", alignItems: "center" } }, pipe, "Add a downpipe at the low end of each gutter"),
      labelled("Downpipe diameter (mm)", diameter)),
    actions: [{ label: "Add gutters", kind: "primary", run: async () => {
      const edges = boxes.filter((b) => b.box.checked).map((b) => b.edge);
      if (!edges.length) { toast("Tick at least one eave.", { kind: "info" }); return undefined; }
      const out = await store.run("model.add_gutters", { roof, edges, gutter: kind.value, width: Number(width.value), depth: Number(depth.value), fall: Number(fall.value),
        material: material.value, downpipe: pipe.checked, diameter: Number(diameter.value) });
      if (!out) return undefined;
      const ids = (out.result as { ids?: string[] } | undefined)?.ids;
      if (ids?.length) await store.select({ kind: "model", ids });
      return true;
    } }],
  });
  await handle.result;
}

/** One more downpipe under the selected gutter. */
export async function downpipeDialog(store: Store): Promise<void> {
  if (!store.project) return;
  const gutter = selectedOf(store, "gutter");
  if (!gutter) { toast("Select a gutter first, then choose Downpipe.", { kind: "info" }); return; }
  const number = (value: string, step = "any", min?: string): HTMLInputElement => h("input", { type: "number", value, step, ...(min ? { min } : {}), attrs: { autocomplete: "off" } });
  const offset = number("0.5", "0.05", "0"), diameter = number("80", "5", "20");
  const handle = openDialog<true>({
    title: "Add a downpipe", size: "narrow", focus: () => offset,
    body: h("div", { css: { display: "grid", gap: "0.9rem" } }, labelled("Distance along the gutter (m)", offset, { hint: "From the start of the eave. Drag the downpipe in the plan, or edit this in Properties, to move it later." }),
      labelled("Diameter (mm)", diameter), h("p", { class: "muted", text: "It runs down to 300 mm above the lowest level; change the discharge height in Properties." })),
    actions: [{ label: "Add downpipe", kind: "primary", run: async () => {
      const out = await store.run("model.add_downpipe", { gutter, offset: Number(offset.value), diameter: Number(diameter.value) });
      if (!out) return undefined;
      const id = (out.result as { id?: string } | undefined)?.id;
      if (id) await store.select({ kind: "model", ids: [id] });
      return true;
    } }],
  });
  await handle.result;
}

/** What each roof drains: area, flow and downpipes per gutter. */
export async function drainageDialog(store: Store): Promise<void> {
  if (!store.project) return;
  const data = await drainage(store);
  if (!data) return;
  if (!data.roofs.length) { toast("There is no roof yet. Draw a roof, then add gutters to its eaves.", { kind: "info" }); return; }
  const num = (v: number, d = 2): string => v.toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d });
  const rows: HTMLElement[] = [];
  for (const r of data.roofs) {
    rows.push(h("tr", {}, h("th", { attrs: { scope: "row", colspan: "5" }, text: `${r.name} · ${num(r.plan_area_m2)} m² in plan · ${num(r.drained_m2)} m² drained${r.undrained_m2 > 1e-6 ? ` · ${num(r.undrained_m2)} m² with no gutter` : ""}` })));
    for (const g of r.gutters) {
      const pipes = g.downpipes.length ? g.downpipes.map((p) => `${p.name} Ø${num(p.diameter_mm, 0)} · ${num(p.flow_l_s, 3)} l/s`).join("; ") : "no downpipe";
      rows.push(h("tr", {}, h("td", { text: g.name }), h("td", { class: "num", text: `${num(g.length_m)} m` }), h("td", { class: "num", text: `${num(g.catchment_m2)} m²` }),
        h("td", { class: "num", text: `${num(g.flow_l_s, 3)} l/s` }), h("td", { text: pipes })));
    }
  }
  openDialog({ title: "Rainwater drainage", size: "wide", noCancel: true, cancelLabel: "Close",
    body: h("div", { css: { display: "grid", gap: "0.75rem" } },
      h("table", { class: "schedule", attrs: { "aria-label": "Rainwater drainage" } }, h("thead", {}, h("tr", {}, ...["Gutter", "Length", "Roof area", "Design flow", "Downpipes"].map((t) => h("th", { text: t })))), h("tbody", {}, rows)),
      h("p", { class: "muted", text: data.note })),
    actions: [{ label: "Done", kind: "primary", run: () => true }] });
}
