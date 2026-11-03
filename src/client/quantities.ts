/** Quantities dialog (Analyze → Quantities). The numbers are SGEO's and the server's; this only lays them out. */
import { h, labelled } from "../../../tools/web/src/dom.js";
import { ApiError } from "../../../tools/web/src/api.js";
import { openDialog, saveBlob } from "../../../tools/web/src/ui.js";
import type { Store } from "./store.js";
import type { Quantities, QuantityRow } from "./types.js";

/** Numbers to useful precision; rounding noise around zero (a centre at -6e-19 m) reads as 0. */
export const sig = (n: number | null | undefined, digits = 4): string => (n === null || n === undefined ? "–" : Math.abs(n) < 1e-9 ? "0" : String(Number(n.toPrecision(digits))));
const point = (p: number[] | null): string => (p ? `${p.map((v) => sig(v, 4)).join(", ")} m` : "–");

/** What the quantity table shows for one element. */
export function rowCells(r: QuantityRow): string[] {
  return [r.name, r.category, r.material ?? "–", sig(r.volume_m3), sig(r.surface_area_m2), r.mass_kg === null ? "no density" : sig(r.mass_kg, 5), point(r.centroid_m)];
}

/** The densities a person has typed, as the request wants them: only valid positive numbers, by material. */
export function parseDensities(entries: Array<[string, string]>): { densities: Record<string, number>; error: string | null } {
  const densities: Record<string, number> = {};
  for (const [material, raw] of entries) {
    if (raw.trim() === "") continue;
    const v = Number(raw.replace(",", "."));
    if (!Number.isFinite(v) || v < 1 || v > 25000) return { densities, error: `The density of ${material} must be a number from 1 to 25000 kg/m³.` };
    densities[material] = v;
  }
  return { densities, error: null };
}

const DENSITY_KEY = "spadra.densities";

/** Densities the person typed earlier (this browser only). Anything that is not a density by material name is ignored. */
export function loadDensities(storage: Pick<Storage, "getItem"> | null = safeStorage()): Array<[string, string]> {
  try {
    const raw = JSON.parse(storage?.getItem(DENSITY_KEY) ?? "null") as unknown;
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return [];
    return Object.entries(raw as Record<string, unknown>).filter(([k, v]) => k.length > 0 && k.length <= 40 && typeof v === "string" && v.length <= 12) as Array<[string, string]>;
  } catch { return []; }
}
function safeStorage(): Storage | null { try { return window.localStorage; } catch { return null; } }
function rememberDensities(densities: Record<string, number>): void {
  try {
    const kept = Object.fromEntries(loadDensities());
    for (const [m, v] of Object.entries(densities)) kept[m] = String(v);
    window.localStorage.setItem(DENSITY_KEY, JSON.stringify(kept));
  } catch { /* a private window: the densities just last this dialog */ }
}

const csvCell = (v: string | number | null): string => {
  if (v === null) return "";
  if (typeof v === "number") return String(v);                                  // numbers stay numbers (a negative centre must not be quoted)
  return /[",\n\r;]/.test(v) || /^[=+\-@\t]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;      // text starting = + - @ is quoted so a spreadsheet cannot read it as a formula
};

/** The take-off as CSV with full-precision numbers and the unit in each heading. Elements that could not be measured keep their notes. */
export function quantitiesCsv(q: Quantities): string {
  const lines: string[] = [];
  const row = (cells: Array<string | number | null>): void => { lines.push(cells.map(csvCell).join(",")); };
  row(["Element", "Category", "Material", "Volume (m3)", "Surface area (m2)", "Size X (m)", "Size Y (m)", "Size Z (m)", "Density (kg/m3)", "Density source", "Mass (kg)",
    "Centre X (m)", "Centre Y (m)", "Centre Z (m)", "Notes"]);
  for (const r of q.rows) {
    row([r.name, r.category, r.material, r.volume_m3, r.surface_area_m2, ...(r.size_m ?? [null, null, null]), r.density_kg_m3, r.density_source, r.mass_kg,
      ...(r.centroid_m ?? [null, null, null]), r.notes.join("; ")]);
  }
  row([`Total (${q.totals.measured} of ${q.totals.count} measured)`, "", "", q.totals.volume_m3, q.totals.surface_area_m2, "", "", "", "", "", q.totals.mass_kg,
    ...(q.centre ? q.centre.point_m : [null, null, null]), q.centre ? q.centre.kind : ""]);
  row([]);
  row(["Material", "Elements", "Volume (m3)", "Surface area (m2)", "Mass (kg)"]);
  for (const b of q.by_material) row([b.material, b.count, b.volume_m3, b.surface_area_m2, b.mass_kg]);
  row([]);
  row([`Model revision ${q.revision}`, q.source]);
  return lines.join("\r\n") + "\r\n";
}

export function quantitiesDialog(store: Store, ids: string[] | null, scope: string): void {
  const entered = new Map<string, string>(loadDensities());
  let latest: Quantities | null = null;
  const body = h("div", { css: { display: "grid", gap: ".8rem" } });
  const message = h("p", { class: "muted", attrs: { role: "alert" } });

  const load = async (): Promise<void> => {
    message.textContent = "";
    const { densities, error } = parseDensities([...entered]);
    if (error) { message.textContent = error; return; }
    try {
      const q = await store.api.post<Quantities>("/quantities", { ids, densities });
      latest = q;
      rememberDensities(densities);
      render(q);
    } catch (e) {
      body.replaceChildren(message);
      message.textContent = e instanceof ApiError ? e.friendly : "The quantities could not be measured.";
    }
  };

  const render = (q: Quantities): void => {
    const t = q.totals;
    const rows = q.rows.map((r) => h("tr", { data: { id: r.id } }, ...rowCells(r).map((c, i) => h("td", { class: i >= 3 && i <= 5 ? "num" : "", text: c, attrs: { title: i === 0 ? r.notes.join("; ") : undefined } }))));
    const table = h("table", { class: "schedule", attrs: { "aria-label": "Quantities" } },
      h("thead", {}, h("tr", {}, ["Element", "Category", "Material", "Volume (m³)", "Surface (m²)", "Mass (kg)", "Centre"].map((c) => h("th", { text: c })))),
      h("tbody", {}, rows),
      h("tfoot", {}, h("tr", {}, h("th", { text: `Total (${t.measured} of ${t.count} measured)`, attrs: { colspan: 3 } }), h("td", { class: "num", text: sig(t.volume_m3) }), h("td", { class: "num", text: sig(t.surface_area_m2) }),
        h("td", { class: "num", text: t.mass_kg === null ? "no density" : `${sig(t.mass_kg, 5)}${t.mass_complete ? "" : " (partial)"}` }), h("td", { text: q.centre ? `${q.centre.kind}: ${point(q.centre.point_m)}` : "–" }))));
    const materials = h("table", { class: "schedule", attrs: { "aria-label": "Material quantities" } },
      h("thead", {}, h("tr", {}, ["Material", "Elements", "Volume (m³)", "Surface (m²)", "Mass (kg)"].map((c) => h("th", { text: c })))),
      h("tbody", {}, q.by_material.map((b) => h("tr", {}, h("td", { text: b.material }), h("td", { class: "num", text: String(b.count) }), h("td", { class: "num", text: sig(b.volume_m3) }), h("td", { class: "num", text: sig(b.surface_area_m2) }),
        h("td", { class: "num", text: b.mass_kg === null ? "no density" : sig(b.mass_kg, 5) })))));
    const engine = (m: string): boolean => q.rows.some((r) => r.material === m && r.density_source === "the SLAI engine's value");
    const editable = q.by_material.map((b) => b.material).filter((m) => m !== "(none)" && !engine(m));        // every material whose density is the person's to give
    const densities = editable.map((m) => {
      const input = h("input", { type: "text", id: `density-${m}`, placeholder: "kg/m³", attrs: { autocomplete: "off", maxlength: 8, inputmode: "decimal" } });
      input.value = entered.get(m) ?? "";
      input.addEventListener("input", () => entered.set(m, input.value));
      return labelled(`Density of ${m} (kg/m³)`, input);
    });
    const recalc = h("button", { type: "button", id: "quantities-recalc", class: "tool-inline", text: "Recalculate with these densities", on: { click: () => { void load(); } } });
    body.replaceChildren(
      h("p", { class: "note", text: q.note }),
      table,
      h("h3", { text: "By material" }), materials,
      ...(densities.length ? [h("h3", { text: q.needs_density.length ? "Mass needs a density" : "Densities used" }),
        h("p", { class: "muted", text: "The SLAI engine has a density for steel only. For other materials enter one; SPADRA does not assume it. Values you enter are remembered in this browser." }), ...densities, recalc] : []),
      message,
      h("p", { class: "muted", text: `Measured by: ${q.source}. Model revision ${q.revision}. Centre: volume-weighted centroid of each element's closed geometry; a centre of mass only where every element has a density.` }));
  };

  openDialog({
    title: "Quantities", eyebrow: scope, size: "wide", noCancel: true, cancelLabel: "Close", body,
    actions: [
      { label: "Download CSV", run: () => { if (latest) saveBlob(new Blob(["\ufeff", quantitiesCsv(latest)], { type: "text/csv;charset=utf-8" }), "quantities.csv"); return undefined; } },
      { label: "Close", run: () => true },
    ],
  });
  void load();
}
