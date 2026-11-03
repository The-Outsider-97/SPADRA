/** The schedule view: a table of the model's doors, windows, walls, floors, roofs or beams, read from the elements themselves.
 *  Sorting is a view over the rows the server sent; nothing here edits the model. */
import { h, replace } from "../../../tools/web/src/dom.js";
import { glyph } from "../../../tools/web/src/icons.js";
import { saveBlob } from "../../../tools/web/src/ui.js";
import type { Store } from "./store.js";
import type { ScheduleColumn, ScheduleData } from "./types.js";

/** A cell as shown: numbers without trailing zeros (a thousandth is the finest the server reports), text as it is. */
export function formatCell(value: string | number | undefined): string {
  if (value === undefined || value === null) return "";
  if (typeof value === "number") return String(Math.round(value * 1000) / 1000);
  return value;
}

/** Rows ordered by a column. Numbers sort as numbers and text as text (digits inside names compare numerically: D2 before D10). */
export function sortRows<T extends Record<string, string | number>>(rows: readonly T[], key: string, direction: 1 | -1): T[] {
  const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
  return [...rows].sort((a, b) => {
    const x = a[key] ?? "", y = b[key] ?? "";
    const c = typeof x === "number" && typeof y === "number" ? x - y : collator.compare(String(x), String(y));
    return c * direction;
  });
}

export class ScheduleView {
  readonly el: HTMLElement;
  private data: ScheduleData | null = null;
  private sort: { key: string; direction: 1 | -1 } | null = null;
  private shownFor = "";

  constructor(private readonly store: Store) {
    this.el = h("div", { class: "schedule-wrap", hidden: true });
  }

  setData(data: ScheduleData | null): void {
    const id = data?.view?.id ?? "";
    if (id !== this.shownFor) { this.sort = null; this.shownFor = id; }       // a different schedule starts in its own order
    this.data = data;
    this.render();
  }

  private render(): void {
    const d = this.data;
    if (!d) { replace(this.el); return; }
    const selected = new Set(this.store.selection?.kind === "model" ? this.store.selection.ids : []);
    const rows = this.sort ? sortRows(d.rows, this.sort.key, this.sort.direction) : d.rows;
    const head = h("tr", {}, d.columns.map((c) => this.header(c)));
    const body = rows.map((r) => h("tr", { data: { id: String(r["id"] ?? "") }, attrs: { "aria-selected": String(selected.has(String(r["id"]))) }, on: { click: () => { void this.store.select({ kind: "model", ids: [String(r["id"])] }); } } },
      d.columns.map((c) => h("td", { class: c.align === "right" ? "num" : "", text: formatCell(r[c.key]) }))));
    const showTotals = d.rows.length > 0 && Object.keys(d.totals).length > 0;
    const foot = showTotals ? h("tfoot", {}, h("tr", {}, d.columns.map((c, i) => h("td", { class: c.align === "right" ? "num" : "", text: i === 0 ? "Total" : c.total ? formatCell(d.totals[c.key]) : "" })))) : null;
    const table = h("table", { class: "schedule", attrs: { "aria-label": d.title } }, h("thead", {}, head), h("tbody", {}, body), foot);
    replace(this.el,
      h("div", { class: "schedule-head" },
        h("h2", { text: d.title }), h("span", { class: "muted", text: `${d.count} ${d.count === 1 ? "row" : "rows"}` }), h("div", { class: "spacer" }),
        h("button", { type: "button", class: "tool-inline", on: { click: () => { void this.exportCsv(); } } }, glyph("export"), "Export CSV")),
      d.rows.length ? h("div", { class: "schedule-scroll" }, table) : h("p", { class: "note", text: `There are no ${d.category === "structural" ? "beams or columns" : `${d.category}s`} in the model yet.` }),
      h("p", { class: "note", text: d.note }));
  }

  private header(c: ScheduleColumn): HTMLElement {
    const active = this.sort?.key === c.key;
    const arrow = active ? (this.sort?.direction === 1 ? " ▲" : " ▼") : "";
    return h("th", { class: c.align === "right" ? "num" : "", attrs: { scope: "col", "aria-sort": active ? (this.sort?.direction === 1 ? "ascending" : "descending") : "none" } },
      h("button", { type: "button", attrs: { title: `Sort by ${c.label}` }, on: { click: () => { this.sort = this.sort?.key === c.key ? { key: c.key, direction: this.sort.direction === 1 ? -1 : 1 } : { key: c.key, direction: 1 }; this.render(); } },
        text: `${c.label}${c.unit ? ` (${c.unit})` : ""}${arrow}` }));
  }

  private async exportCsv(): Promise<void> {
    const file = await this.store.exportCsv();
    if (file) { saveBlob(file.blob, file.filename); this.store.setStatus(`Exported ${file.filename}`); }
  }
}
