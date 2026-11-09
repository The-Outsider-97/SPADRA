/** Foundations: pads, strips, foundation beams, rafts and ground-bearing slabs. The server builds and checks everything (a foundation follows the wall or
 *  column it stands on, strips that meet are cut to each other); this dialog only gathers the choices. Reinforcement and concrete class are *declared*
 *  here, never designed or checked. */
import { clear, h, labelled } from "../../../tools/web/src/dom.js";
import { openDialog, toast } from "../../../tools/web/src/ui.js";
import type { Store } from "./store.js";

export interface FoundationReport {
  id: string; kind: string; plan_area_m2: number; concrete_m3: number; volume_m3: number; formwork_m2: number;
  layers: Array<{ name: string; material: string; thickness_m: number; volume_m3: number; concrete: boolean }>;
  thermal?: { r_m2k_w: number; complete: boolean; missing: string[]; u_w_m2k: number | null };
  reinforcement: { bars: number; length_m: number; mass_kg: number | null; note: string } | null; note: string;
  concrete_class?: string | null; steel?: string | null; cover_m?: number; display_material?: string;
  bars?: Array<{ mark?: string; diameter: number; role: string; face: string; spacing?: number; count?: number }>;
  formwork?: { thickness_m: number; material: string; plates: number; area_m2: number; volume_m3: number }; floors?: string;
}

export interface FloorReport { adjusted?: unknown[]; merged?: unknown[]; restored?: unknown[]; unresolved?: unknown[] }

const KINDS: Array<[string, string]> = [
  ["under", "Under the selection (a strip under each wall, a pad under each column)"],
  ["poer", "Pad (poer)"], ["strook", "Strip (strook)"], ["funderingsbalk", "Foundation beam (funderingsbalk)"],
  ["plaat", "Raft (plaat)"], ["vloer", "Ground-bearing slab (vloer op grondslag)"],
];
const LAYER_MATERIALS = ["EPS", "XPS", "PIR", "Mineral wool", "Foam glass", "Sand", "Gravel", "Blinding concrete", "Screed", "Concrete", "Floor finish", "Damp-proof membrane"];
const FORMWORK_MATERIALS = ["EPS", "XPS", "Timber", "Plywood", "Steel"];
const FLOOR_CHOICES: Array<[string, string]> = [
  ["adjust", "Adjust: cut the floor back to the foundation"],
  ["merge", "Merge: lower the foundation top so the floor runs over it"],
  ["keep", "Leave the floors as they are"],
];
const DIAMETERS = [6, 8, 10, 12, 14, 16, 20, 25, 28, 32, 40];
const ROLES: Record<string, string[]> = { poer: ["x", "y"], plaat: ["x", "y"], vloer: ["x", "y"], strook: ["long", "link"], funderingsbalk: ["long", "link"] };

interface Row { el: HTMLElement; read: () => Record<string, unknown> | null }
type BarSpec = NonNullable<FoundationReport["bars"]>[number];

const numberInput = (value: string, step = "any", min?: string): HTMLInputElement => h("input", { type: "number", value, step, ...(min ? { min } : {}), attrs: { autocomplete: "off" } });
const removerFor = (rows: Row[], row: Row): HTMLButtonElement => h("button", { class: "btn", type: "button", text: "Remove", attrs: { "aria-label": "Remove this row" }, on: { click: () => { rows.splice(rows.indexOf(row), 1); row.el.remove(); } } });

/** A list of rows the person adds to and removes from. */
function rowsBox(title: string, hint: string, add: () => Row): { box: HTMLElement; rows: Row[]; clearAll: () => void; append: (r: Row) => void } {
  const rows: Row[] = [];
  const list = h("div", { css: { display: "grid", gap: "0.4rem" } });
  const append = (r: Row): void => { rows.push(r); list.append(r.el); };
  const addBtn = h("button", { class: "btn", type: "button", text: "Add", on: { click: () => append(add()) } });
  const box = h("fieldset", { css: { display: "grid", gap: "0.4rem", border: "1px solid var(--line, #8884)", padding: "0.5rem 0.75rem" } }, h("legend", { text: title }), h("p", { class: "muted", text: hint }), list, addBtn);
  return { box, rows, clearAll: () => { rows.length = 0; clear(list); }, append };
}

/** One declared bar group: role, diameter, spacing or count, face. ``init`` fills it from what is already declared. */
function barRow(rows: Row[], kind: string, init?: BarSpec): Row {
  const roles = ROLES[kind] ?? ["x", "y"];
  const role = h("select", {}, ...roles.map((r) => h("option", { value: r, text: r }))) as HTMLSelectElement;
  const dia = h("select", {}, ...DIAMETERS.map((d) => h("option", { value: String(d), text: `Ø${d}` }))) as HTMLSelectElement;
  const by = h("select", {}, h("option", { value: "spacing", text: "at spacing (mm)" }), h("option", { value: "count", text: "number of bars" })) as HTMLSelectElement;
  const n = numberInput("200", "1", "1");
  const face = h("select", {}, h("option", { value: "bottom", text: "bottom" }), h("option", { value: "top", text: "top" }), h("option", { value: "both", text: "both" })) as HTMLSelectElement;
  dia.value = String(init?.diameter ?? 12);
  if (init) {
    if (roles.includes(init.role)) role.value = init.role;
    face.value = init.face;
    if (init.count !== undefined) { by.value = "count"; n.value = String(init.count); } else if (init.spacing !== undefined) n.value = String(init.spacing);
  }
  const row: Row = { el: null as unknown as HTMLElement, read: () => ({ diameter: Number(dia.value), role: role.value, face: face.value, ...(init?.mark ? { mark: init.mark } : {}), [by.value]: Number(n.value) }) };
  row.el = h("div", { css: { display: "grid", gridTemplateColumns: "0.8fr 0.8fr 1.2fr 0.8fr 0.8fr auto", gap: "0.4rem", alignItems: "end" } },
    labelled("Role", role), labelled("Bar", dia), labelled("Given as", by), labelled("Value", n), labelled("Face", face), removerFor(rows, row));
  return row;
}

/** What a floor adjustment did, in a sentence (empty when no floor was touched). */
function floorNote(f: FloorReport | undefined): string {
  if (!f) return "";
  const parts: string[] = [];
  if (f.adjusted?.length) parts.push(`${f.adjusted.length} floor${f.adjusted.length === 1 ? "" : "s"} cut back to the foundation`);
  if (f.merged?.length) parts.push(`${f.merged.length} floor${f.merged.length === 1 ? "" : "s"} merged with it`);
  if (f.unresolved?.length) parts.push(`${f.unresolved.length} floor${f.unresolved.length === 1 ? "" : "s"} could not be adjusted (see Check Model)`);
  return parts.length ? ` ${parts.join("; ")}.` : "";
}

export async function foundationDialog(store: Store): Promise<void> {
  if (!store.project) return;
  const picked = store.selection?.kind === "model" ? store.selection.ids as string[] : [];
  const number = numberInput;
  const kind = h("select", {}, ...KINDS.map(([v, l]) => h("option", { value: v, text: l }))) as HTMLSelectElement;
  if (!picked.length) kind.value = "poer";
  const level = h("select", {}, ...store.levels.map((l) => h("option", { value: l.id, text: l.name }))) as HTMLSelectElement;
  const onSelection = h("input", { type: "checkbox", checked: picked.length > 0, attrs: { "aria-label": "Stand on the selected wall or columns" } }) as HTMLInputElement;
  const px = number("0"), py = number("0"), sx = number("0"), sy = number("0"), ex = number("5000"), ey = number("0"), ax = number("0"), ay = number("0");
  const width = number("", "10", "50"), length = number("", "10", "100"), height = number("500", "10", "10"), thickness = number("250", "10", "10");
  const offset = number("0", "10");
  const shape = h("select", {}, h("option", { value: "rect", text: "Rectangular" }), h("option", { value: "round", text: "Round (width is the diameter)" })) as HTMLSelectElement;
  const top = number("", "0.05"), cover = number("50", "5", "10");
  const concreteClass = h("input", { type: "text", value: "", placeholder: "e.g. C25/30", attrs: { maxlength: 20, autocomplete: "off" } }) as HTMLInputElement;
  const steel = h("input", { type: "text", value: "", placeholder: "e.g. B500B", attrs: { maxlength: 20, autocomplete: "off" } }) as HTMLInputElement;
  const rectW = number("6000", "10", "100"), rectD = number("5000", "10", "100");
  const status = h("p", { class: "muted", attrs: { role: "status", "aria-live": "polite" } });
  const constrain = h("input", { type: "checkbox", checked: true, attrs: { "aria-label": "Constrained to the wall or column above" } }) as HTMLInputElement;
  const formworkOn = h("input", { type: "checkbox", checked: false, attrs: { "aria-label": "Add formwork" } }) as HTMLInputElement;
  const formworkT = number("50", "5", "5");
  const formworkMat = h("select", {}, ...FORMWORK_MATERIALS.map((m) => h("option", { value: m, text: m }))) as HTMLSelectElement;
  const floors = h("select", {}, ...FLOOR_CHOICES.map(([v, l]) => h("option", { value: v, text: l }))) as HTMLSelectElement;

  const layerRow = (rows: Row[], where: "above" | "below"): Row => {
    const material = h("select", {}, ...LAYER_MATERIALS.map((m) => h("option", { value: m, text: m }))) as HTMLSelectElement;
    const t = number("100", "5", "1"), k = number("", "0.001", "0.005");
    const row: Row = { el: null as unknown as HTMLElement, read: () => ({ material: material.value, thickness: Number(t.value), conductivity: k.value === "" ? undefined : Number(k.value), where }) };
    row.el = h("div", { css: { display: "grid", gridTemplateColumns: "1.4fr 1fr 1fr auto", gap: "0.4rem", alignItems: "end" } },
      labelled("Material", material), labelled("Thickness (mm)", t), labelled("λ (W/m·K)", k, { hint: "Leave empty if unknown" }), removerFor(rows, row));
    return row;
  };
  const below = rowsBox("Layers below the slab (nearest the slab first)", "Insulation, sand bed, blinding… Each is a solid of the slab's outline. The thermal resistance counts only layers that have a λ.", () => layerRow(below.rows, "below"));
  const above = rowsBox("Layers above the slab (nearest the slab first)", "Screed, finishes…", () => layerRow(above.rows, "above"));
  const bars = rowsBox("Reinforcement (declared)", "Straight bars the take-off counts between the covers. Nothing here is designed or checked against any code.", () => barRow(bars.rows, kind.value));

  const show = (el: HTMLElement, on: boolean): void => { el.hidden = !on; };
    const two = (a: HTMLElement, b: HTMLElement): HTMLElement => h("div", { css: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.75rem" } }, a, b);
  const levelRow = labelled("Level", level, { hint: "Only used when the foundation does not stand on a wall or column; otherwise it takes the level of what it carries." });
  const selectionRow = h("label", { css: { display: "flex", gap: "0.5rem", alignItems: "center" } }, onSelection, `Stand on the selection (${picked.length} selected)`);
  const pointBox = h("div", { css: { display: "grid", gap: "0.5rem" } }, two(labelled("X (drawing units)", px), labelled("Y (drawing units)", py)));
  const lineBox = h("div", { css: { display: "grid", gap: "0.5rem" } }, two(labelled("Start X", sx), labelled("Start Y", sy)), two(labelled("End X", ex), labelled("End Y", ey)), labelled("Eccentricity (mm)", offset, { hint: "Sideways from the line, to the left of start→end." }));
  const areaBox = h("div", { css: { display: "grid", gap: "0.5rem" } }, two(labelled("Corner X", ax), labelled("Corner Y", ay)), two(labelled("Width (mm)", rectW), labelled("Depth (mm)", rectD)),
    h("p", { class: "muted", text: "A rectangle from the corner. Other outlines can be made with the API or the assistant." }));
  const sizeBox = h("div", { css: { display: "grid", gap: "0.5rem" } });
  const constrainRow = h("label", { css: { display: "flex", gap: "0.5rem", alignItems: "center" } }, constrain,
    "Constrained to the wall or column above (it moves with it, sideways and up or down)");
  const formworkBox = h("div", { css: { display: "grid", gap: "0.5rem" } },
    h("label", { css: { display: "flex", gap: "0.5rem", alignItems: "center" } }, formworkOn, "Add formwork (lost formwork around the concrete)"),
    two(labelled("Formwork thickness (mm)", formworkT), labelled("Formwork material", formworkMat)));
  const syncFormwork = (): void => { formworkT.disabled = !formworkOn.checked; formworkMat.disabled = !formworkOn.checked; };
  formworkOn.addEventListener("change", syncFormwork);
  syncFormwork();
  const layersBox = h("div", { css: { display: "grid", gap: "0.5rem" } }, below.box, above.box);

  const fresh = (restartBars = false): void => {
    const k = kind.value;
    const stand = onSelection.checked && picked.length > 0 && k !== "plaat" && k !== "vloer";
    show(selectionRow, k !== "plaat" && k !== "vloer" && k !== "under" && picked.length > 0);
    show(levelRow, !stand && k !== "under");
    show(pointBox, k === "poer" && !stand);
    show(lineBox, (k === "strook" || k === "funderingsbalk") && !stand);
    show(areaBox, k === "plaat" || k === "vloer");
    show(constrainRow, k !== "plaat" && k !== "vloer" && (stand || k === "under"));
    show(layersBox, k === "plaat" || k === "vloer");
    clear(sizeBox);
    if (k === "poer") sizeBox.append(labelled("Shape", shape), two(labelled(shape.value === "round" ? "Diameter (mm)" : "Width (mm)", width), shape.value === "round" ? h("span") : labelled("Length (mm)", length)), labelled("Height (mm)", height));
    else if (k === "strook" || k === "funderingsbalk" || k === "under") {
      sizeBox.append(two(labelled("Width (mm)", width), labelled("Height (mm)", height)));
      if (k === "under" || (k === "strook" && stand && picked.length > 1)) sizeBox.append(h("p", { class: "muted", text: "A strip under each selected wall; strips that meet are joined at the corners. A pad under each selected column." }));
    }
    else sizeBox.append(labelled("Thickness (mm)", thickness));
    width.placeholder = k === "strook" || k === "under" ? "wall + 2 × 200" : k === "poer" ? "column + 2 × 200" : "600";
    length.placeholder = "column + 2 × 200";
    if (restartBars) bars.clearAll();                                          // the bar roles differ by kind, so a change of kind starts the bar list again
  };
  kind.addEventListener("change", () => fresh(true));
  shape.addEventListener("change", () => fresh());
  onSelection.addEventListener("change", () => fresh());

  const layers = (where: "above" | "below", box: { rows: Row[] }): Array<Record<string, unknown>> => box.rows.map((r) => r.read()).filter((r): r is Record<string, unknown> => !!r && r.where === where).map(({ where: _w, ...rest }) => rest);
  const handle = openDialog<true>({
    title: "Foundation", size: "wide", focus: () => kind,
    body: h("div", { css: { display: "grid", gap: "0.9rem" } },
      labelled("Kind", kind), levelRow, selectionRow,
      pointBox, lineBox, areaBox, sizeBox, layersBox,
      two(labelled("Top above the level (m)", top, { hint: "Empty: the base of what it stands on, or the level." }), labelled("Cover (mm)", cover)),
      constrainRow, formworkBox, labelled("Existing floors it overlaps", floors),
      two(labelled("Concrete class", concreteClass), labelled("Reinforcement steel", steel)),
      bars.box, status,
      h("p", { class: "muted", text: "Concrete class, cover and reinforcement are declared by you. SPADRA does not design foundations, check bearing, settlement or capacity, or claim compliance with any code." })),
    actions: [{ label: "Add foundation", kind: "primary", run: async () => {
      const k = kind.value;
      const standing = (k === "under") || (onSelection.checked && picked.length > 0 && k !== "plaat" && k !== "vloer");
      const common: Record<string, unknown> = { cover: Number(cover.value), floors: floors.value };
      if (!standing) common.level = level.value;                                   // a foundation on a wall takes the wall's level; the dropdown must not contradict it
      if (standing) common.constrain = constrain.checked;
      if (formworkOn.checked) Object.assign(common, { formwork: Number(formworkT.value), formwork_material: formworkMat.value });
      if (top.value !== "") common.top = Number(top.value);
      if (concreteClass.value.trim()) common.concrete_class = concreteClass.value.trim();
      if (steel.value.trim()) common.steel = steel.value.trim();
      const w = width.value === "" ? {} : { width: Number(width.value) };
      if (k === "under") {
        if (!picked.length) { toast("Select walls and columns first (in the plan, the 3D view or the browser).", { kind: "info" }); return undefined; }
        const out = await store.run("model.add_foundations_under", { ...common, ...w, supports: picked, height: Number(height.value) });
        const ids = (out?.result as { ids?: string[] } | undefined)?.ids;
        if (!out) return undefined;
        if (ids?.length) {
          await store.select({ kind: "model", ids });
          store.setStatus(`${ids.length} foundation${ids.length === 1 ? "" : "s"} made.${constrain.checked ? " They follow the walls and columns they stand on." : ""}${floorNote((out.result as { floors?: FloorReport }).floors)}`);
        }
        return true;
      }
      const params: Record<string, unknown> = { ...common, kind: k };
      const stand = onSelection.checked && picked.length > 0 && k !== "plaat" && k !== "vloer";
      if (stand) params.support = picked;
      if (k === "poer") {
        Object.assign(params, w, { height: Number(height.value), shape: shape.value });
        if (length.value !== "" && shape.value !== "round") params.length = Number(length.value);
        if (!stand) params.point = [Number(px.value), Number(py.value)];
      } else if (k === "strook" || k === "funderingsbalk") {
        Object.assign(params, w, { height: Number(height.value), offset: Number(offset.value) });
        if (!stand) { params.start = [Number(sx.value), Number(sy.value)]; params.end = [Number(ex.value), Number(ey.value)]; }
      } else {
        const ox = Number(ax.value), oy = Number(ay.value), rw = Number(rectW.value), rd = Number(rectD.value);
        Object.assign(params, { thickness: Number(thickness.value), points: [[ox, oy], [ox + rw, oy], [ox + rw, oy + rd], [ox, oy + rd]], below: layers("below", below), above: layers("above", above) });
      }
      const declared = bars.rows.map((r) => r.read()).filter((r): r is Record<string, unknown> => !!r);
      if (declared.length) params.bars = declared;
      const out = await store.run("model.add_foundation", params);
      if (!out) return undefined;
      const res = out.result as { id?: string; ids?: string[]; floors?: FloorReport } | undefined;
      const ids = res?.ids?.length ? res.ids : res?.id ? [res.id] : [];
      if (ids.length) await store.select({ kind: "model", ids });
      store.setStatus(`${ids.length === 1 ? "Foundation" : `${ids.length} foundations`} made.${floorNote(res?.floors)}`);
      return true;
    } }],
  });
  fresh(true);
  await handle.result;
}

/** Quantities, build-up and indicative bars of the selected foundation, as the server works them out. */
export async function foundationReportDialog(store: Store): Promise<void> {
  const sel = store.selection;
  if (sel?.kind !== "model" || sel.ids.length !== 1 || store.properties?.category !== "foundation") { toast("Select a foundation first.", { kind: "info" }); return; }
  let r: FoundationReport;
  try { r = await store.api.post<FoundationReport>("/foundation/report", { id: sel.ids[0] }); } catch (e) { toast(e instanceof Error ? e.message : "Could not read the foundation.", { kind: "error" }); return; }
  const line = (a: string, b: string): HTMLElement => h("div", { css: { display: "flex", justifyContent: "space-between", gap: "1rem" } }, h("span", { text: a }), h("span", { text: b }));
  const th = r.thermal;
  const handle = openDialog<true>({
    title: "Foundation quantities", size: "narrow", noCancel: true, actions: [{ label: "Close", kind: "primary", run: () => true }],
    body: h("div", { css: { display: "grid", gap: "0.4rem" } },
      line("Plan area", `${r.plan_area_m2.toFixed(3)} m²`), line("Concrete", `${r.concrete_m3.toFixed(3)} m³`), line("Whole build-up", `${r.volume_m3.toFixed(3)} m³`),
      line("Formwork (edges only)", `${r.formwork_m2.toFixed(2)} m²`),
      ...r.layers.map((l) => line(`${l.name} (${l.material}) ${(l.thickness_m * 1000).toFixed(0)} mm`, `${l.volume_m3.toFixed(3)} m³`)),
      ...(th ? [line("Thermal resistance of the build-up", th.complete ? `${th.r_m2k_w.toFixed(2)} m²K/W` : `${th.r_m2k_w.toFixed(2)} m²K/W so far (no λ for ${th.missing.join(", ")})`)] : []),
      ...(r.reinforcement ? [line("Bars (indicative)", `${r.reinforcement.bars} · ${r.reinforcement.length_m.toFixed(1)} m${r.reinforcement.mass_kg !== null ? ` · ${r.reinforcement.mass_kg.toFixed(0)} kg` : ""}`), h("p", { class: "muted", text: r.reinforcement.note })] : []),
      h("p", { class: "muted", text: r.note })),
  });
  await handle.result;
}

/** Reinforce (or change the reinforcement of) the selected foundation after it was made. The bars, concrete class, steel and cover are *declared* here; the server
 *  rebuilds the foundation, which is then shown, hatched and coloured as reinforced concrete. Nothing is designed or checked. */
export async function reinforceDialog(store: Store, ref?: string): Promise<void> {
  const sel = store.selection;
  const id = ref ?? (sel?.kind === "model" && sel.ids.length === 1 ? String(sel.ids[0]) : undefined);
  if (!id || (!ref && store.properties?.category !== "foundation")) { toast("Select one foundation first.", { kind: "info" }); return; }
  let r: FoundationReport;
  try { r = await store.api.post<FoundationReport>("/foundation/report", { id }); } catch (e) { toast(e instanceof Error ? e.message : "Could not read the foundation.", { kind: "error" }); return; }
  const kind = r.kind;
  const concreteClass = h("input", { type: "text", value: r.concrete_class ?? "", placeholder: "e.g. C25/30", attrs: { maxlength: 20, autocomplete: "off" } }) as HTMLInputElement;
  const steel = h("input", { type: "text", value: r.steel ?? "", placeholder: "e.g. B500B", attrs: { maxlength: 20, autocomplete: "off" } }) as HTMLInputElement;
  const cover = numberInput(String(Math.round((r.cover_m ?? 0.05) * 1000)), "5", "10");
  const bars = rowsBox("Reinforcement (declared)", "Straight bars the take-off counts between the covers. Nothing here is designed or checked against any code.", () => barRow(bars.rows, kind));
  for (const b of r.bars ?? []) bars.append(barRow(bars.rows, kind, b));
  if (!(r.bars ?? []).length) bars.append(barRow(bars.rows, kind));
  const apply = async (clearBars: boolean): Promise<true | undefined> => {
    const declared = clearBars ? [] : bars.rows.map((row) => row.read()).filter((row): row is Record<string, unknown> => !!row);
    const out = await store.run("model.set_foundation_data", { id, bars: declared, cover: Number(cover.value), concrete_class: concreteClass.value.trim(), steel: steel.value.trim() });
    if (!out) return undefined;
    store.setStatus(declared.length ? "Reinforcement declared. The foundation is now shown as reinforced concrete." : "Reinforcement removed. The foundation is shown as plain concrete again.");
    return true;
  };
  const handle = openDialog<true>({
    title: "Reinforce foundation", size: "wide", focus: () => concreteClass,
    body: h("div", { css: { display: "grid", gap: "0.9rem" } },
      h("p", { class: "muted", text: `Currently shown as ${r.display_material ?? "Concrete"}. Declaring bars changes its hatch in sections and its colour in 3D; removing them changes it back.` }),
      h("div", { css: { display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "0.75rem" } }, labelled("Concrete class", concreteClass), labelled("Reinforcement steel", steel), labelled("Cover (mm)", cover)),
      bars.box,
      h("p", { class: "muted", text: "Concrete class, cover and reinforcement are declared by you. SPADRA does not design foundations, check bearing, settlement or capacity, or claim compliance with any code." })),
    actions: [
      { label: "Apply", kind: "primary", run: () => apply(false) },
      ...((r.bars ?? []).length ? [{ label: "Remove reinforcement", kind: "danger" as const, run: () => apply(true) }] : []),
    ],
  });
  await handle.result;
}
