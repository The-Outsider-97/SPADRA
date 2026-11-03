/** Importing and exporting: choose a file, see what is in it before anything changes, then run it as a background operation and read the conversion report.
 *  The server decides every fact shown here (the file's real type, whether it parses, its unit, what a format cannot carry); this module only presents it. */
import { h, labelled, replace } from "../../../tools/web/src/dom.js";
import { openDialog, saveBlob, toast } from "../../../tools/web/src/ui.js";
import { formatBytes } from "./project_dialogs.js";
import type { Store } from "./store.js";
import type { ConversionReport, DrawingFormat, FormatState, InspectResult, ModelFormat } from "./types.js";

export const IMPORT_UNITS = ["mm", "cm", "m", "in", "ft"] as const;
const MODEL_LABEL: Record<string, string> = { sgeo: "SGEO (native: keeps identity, parameters, families)", glb: "GLB (glTF binary)", stl: "STL (binary)", obj: "OBJ (Wavefront)", ifc: "IFC" };
const DRAWING_LABEL: Record<string, string> = { sdrw: "SDRW (native: keeps layers, line types, blocks)", svg: "SVG", dxf: "DXF" };

/** What a conversion report says, as titled lists, in the order a reader needs: what happened to the content, then what to be aware of. Empty lists are left out. */
export function reportSections(report: ConversionReport): Array<{ title: string; items: string[] }> {
  const converted = Object.entries(report.converted ?? {}).filter(([, n]) => Number.isFinite(n)).map(([k, n]) => { const label = k.replace(/_/g, " "); return `${label[0]?.toUpperCase() ?? ""}${label.slice(1)}: ${n}`; });
  const parts: Array<[string, string[] | undefined]> = [
    ["Carried over", converted], ["Not carried over", report.lost], ["Approximated", report.approximated], ["Not supported", report.unsupported],
    ["Errors", report.errors], ["Warnings", report.warnings], ["Assumed", report.assumptions],
  ];
  return parts.filter(([, items]) => items && items.length).map(([title, items]) => ({ title, items: items as string[] }));
}

export function statusText(report: ConversionReport): string {
  return report.status === "complete" ? "Everything was carried over." : report.status === "partial" ? "Some of the content could not be carried over. The lists below say what." : report.status === "failed" ? "The conversion did not complete." : "";
}

/** What a conversion cost: the server's own report, shown whole so nothing lost is hidden. */
export function conversionDialog(title: string, summary: string, report: ConversionReport): void {
  const sections = reportSections(report);
  const where = report.source && report.destination ? `${report.source.format.toUpperCase()} → ${report.destination.format.toUpperCase()}` : undefined;
  openDialog({ title, eyebrow: [where, report.accuracy_class ? `Accuracy: ${report.accuracy_class}` : null].filter(Boolean).join(" · ") || undefined, size: "wide", noCancel: true,
    body: h("div", { css: { display: "grid", gap: ".8rem" } }, h("p", { text: summary }), report.status ? h("p", { class: "report-status", data: { status: report.status }, text: statusText(report) }) : null,
      ...sections.map((s) => h("div", {}, h("strong", { text: s.title }), h("ul", { class: "issue-list" }, s.items.map((i) => h("li", { text: i }))))),
      report.adapter ? h("p", { class: "muted", text: `Converted by the SLAI ${report.adapter} adapter.` }) : null),
    actions: [{ label: "Close", kind: "primary", run: () => true }] });
}

/** Choose a file. The input is created, used once and removed, so nothing is left in the page. */
export function pickFile(accept: string, then: (file: File) => Promise<void>): void {
  const input = h("input", { type: "file", attrs: { accept, hidden: "", "aria-label": "Choose a file to import" } });
  input.addEventListener("change", () => { const file = input.files?.[0]; input.remove(); if (file) void then(file); });
  document.body.append(input);
  input.click();
}

export const extensionOf = (name: string): string => (name.split(".").pop() ?? "").toLowerCase();

/** The formats this installation can read or write for a target, in the order they are offered, with why any are unavailable. */
export function formatsFor(store: Store, kind: "model" | "drawing", direction: "import" | "export"): Array<{ id: string; label: string; state: FormatState }> {
  const interop = store.interop;
  const table: Record<string, FormatState> | undefined = interop ? (kind === "model" ? (direction === "import" ? interop.importModel : interop.exportModel) : direction === "import" ? interop.importDrawing : interop.exportDrawing) : undefined;
  const ids = kind === "model" ? (direction === "import" ? ["sgeo", "glb", "stl", "obj", "ifc"] : ["sgeo", "glb", "stl", "obj"]) : ["sdrw", "svg", "dxf"];
  const labels = kind === "model" ? MODEL_LABEL : DRAWING_LABEL;
  return ids.map((id) => ({ id, label: labels[id] ?? id.toUpperCase(), state: table?.[id] ?? { available: true } }));
}

const SEVERITY_LABEL: Record<string, string> = { error: "Problem", warning: "Check", info: "Note" };

function summaryRows(r: InspectResult): Array<[string, string]> {
  const rows: Array<[string, string]> = [["File", `${r.name} (${formatBytes(r.bytes)})`], ["Looks like", r.detected ? r.detected.toUpperCase() : "not a format SPADRA reads"]];
  if (r.version) rows.push(["Version", r.version]);
  if (r.unit) rows.push(["Unit", `${r.unit.value}${r.unit.declared ? " (stated in the file)" : " (assumed: the file does not say)"}`]);
  const c = r.counts;
  const n = (k: string): number | undefined => (typeof c[k] === "number" ? (c[k] as number) : undefined);
  if (r.target === "model") {
    if (n("objects") !== undefined) rows.push(["Objects", `${n("objects")} (${n("with_geometry") ?? 0} with geometry)`]);
    if (n("mesh_triangles")) rows.push(["Triangles", String(n("mesh_triangles"))]);
  } else {
    if (n("entities") !== undefined) rows.push(["Entities", String(n("entities"))]);
    if (n("layers") !== undefined) rows.push(["Layers", String(n("layers"))]);
  }
  if (r.extent) rows.push(["Overall size", `${r.extent.size.map((s) => Math.round(s * 1e3) / 1e3).join(" × ")} ${r.extent.unit}`]);
  return rows;
}

/** Import into the model, or into the open drawing: choose a file, read what the server found in it, then import it in the background. */
export async function importDialog(store: Store, kind: "model" | "drawing"): Promise<void> {
  const formats = formatsFor(store, kind, "import");
  const accept = formats.map((f) => `.${f.id}`).join(",");
  pickFile(accept, async (file) => {
    const format = extensionOf(file.name);
    const known = formats.find((f) => f.id === format);
    if (!known) { toast(`SPADRA reads ${formats.map((f) => f.id.toUpperCase()).join(", ")} files here. “${file.name}” is not one of them.`, { kind: "error" }); return; }
    const data = await store.readFile(file);
    if (!data) return;
    let unit: string = kind === "model" ? "mm" : store.unit;
    let found = await store.inspect(kind, format, data, file.name, unit);
    if (!found) return;
    const body = h("div", { css: { display: "grid", gap: ".9rem" }, attrs: { "aria-live": "polite" } });
    const unitSelect = h("select", { attrs: { "aria-label": "Length unit of the file" } }, IMPORT_UNITS.map((u) => h("option", { value: u, text: u, selected: u === unit })));
    const render = (): void => {
      if (!found) return;
      const r = found;
      const limits = known.state.limits ?? [];
      replace(body,
        h("table", { class: "schedule", attrs: { "aria-label": "What is in the file" } }, h("tbody", {}, summaryRows(r).map(([k, v]) => h("tr", {}, h("th", { attrs: { scope: "row" }, text: k }), h("td", { text: v }))))),
        r.unit && !r.unit.declared ? labelled("Unit of the file", unitSelect, { hint: "The file does not say. Choose what it was made in; the wrong unit scales everything." }) : null,
        r.issues.length ? h("ul", { class: "issue-list" }, r.issues.map((i) => h("li", { data: { severity: i.severity } }, h("strong", { text: `${SEVERITY_LABEL[i.severity] ?? i.severity}: ` }), i.message))) : h("p", { text: "Nothing wrong was found." }),
        limits.length ? h("details", {}, h("summary", { text: `What ${known.id.toUpperCase()} cannot carry` }), h("ul", { class: "issue-list" }, limits.map((l) => h("li", { text: l })))) : null,
        h("p", { class: "muted", text: kind === "model" ? "Importing adds the objects to the model in one step; Undo takes them all back." : "Importing adds the entities to the open drawing in one step; Undo takes them all back." }));
      const go = handle.el.querySelector<HTMLButtonElement>(".dialog-actions .btn.primary");
      if (go) go.disabled = !r.valid;
    };
    unitSelect.addEventListener("change", () => {
      unit = unitSelect.value;
      void store.inspect(kind, format, data, file.name, unit).then((again) => { if (again) { found = again; render(); } });
    });
    const handle = openDialog<true>({
      title: `Import ${file.name}`, eyebrow: kind === "model" ? "Into the model" : "Into the drawing", size: "wide", body,
      actions: [{ label: "Import", kind: "primary", run: async () => {
        if (!found?.valid) { toast(found?.issues.find((i) => i.severity === "error")?.message ?? "This file cannot be imported.", { kind: "error" }); return undefined; }
        const job = await store.importFile(kind, { format, data, name: file.name, ...(kind === "model" || (found.unit && !found.unit.declared) ? { unit } : {}) });
        if (!job) return undefined;
        if (job.status !== "done") return job.status === "cancelled" ? true : undefined;
        const result = job.result ?? {};
        const count = Number(result["objects"] ?? result["entities"] ?? 0);
        const noun = kind === "model" ? "object(s)" : "entit(ies)";
        store.setStatus(`Imported ${count} ${noun} from ${file.name}`);
        queueMicrotask(() => conversionDialog(`Imported ${file.name}`, `${count} ${noun} added. Undo removes them in one step.`, (result["report"] ?? {}) as ConversionReport));
        return true;
      } }],
    });
    render();
  });
}

/** Export the model (whole, or the selected elements) or the open drawing, in a format chosen here. Nothing is silently dropped: the formats list what they cannot carry,
 *  and "What will be lost" asks the server for the exact report before anything is written. */
export async function exportDialog(store: Store, kind: "model" | "drawing"): Promise<void> {
  const formats = formatsFor(store, kind, "export");
  const tab = store.tab;
  const view = kind === "drawing" && tab?.ref.kind === "drawing" ? tab.ref.id : undefined;
  if (kind === "drawing" && !view) { toast("Open a drawing to export it.", { kind: "info" }); return; }
  const selected = kind === "model" && store.selection?.kind === "model" ? store.selection.ids : [];
  const first = formats.find((f) => f.state.available)?.id ?? formats[0]?.id ?? "";
  const select = h("select", { attrs: { "aria-label": "Format" } }, formats.map((f) => h("option", { value: f.id, text: f.state.available ? f.label : `${f.label} - not available`, disabled: !f.state.available, selected: f.id === first })));
  const scope = selected.length ? h("select", { attrs: { "aria-label": "What to export" } }, h("option", { value: "all", text: "The whole model" }), h("option", { value: "selection", text: `The ${selected.length} selected element${selected.length === 1 ? "" : "s"}` })) : null;
  const info = h("div", { css: { display: "grid", gap: ".6rem" }, attrs: { "aria-live": "polite" } });
  const preview = h("div", {});
  const current = (): { id: string; state: FormatState } => formats.find((f) => f.id === select.value) ?? (formats[0] as { id: string; state: FormatState });
  const ids = (): string[] | undefined => (scope && scope.value === "selection" ? selected : undefined);
  const showInfo = (): void => {
    const f = current();
    preview.replaceChildren();
    replace(info,
      f.state.native ? h("p", { text: "Native SLAI format: everything the model holds is kept." }) : null,
      f.state.limits?.length ? h("div", {}, h("strong", { text: `What ${f.id.toUpperCase()} cannot carry` }), h("ul", { class: "issue-list" }, f.state.limits.map((l) => h("li", { text: l })))) : null,
      !f.state.available ? h("p", { class: "muted", text: f.state.reason ?? "This format is not available here." }) : null,
      h("button", { class: "tool-inline", type: "button", text: "Show what will be lost", on: { click: () => { void showLoss(f.id); } } }), preview);
  };
  const showLoss = async (format: string): Promise<void> => {
    const query: Record<string, string> = { format, report: "1", ...(ids() ? { ids: ids()?.join(",") ?? "" } : {}) };
    const out = await store.guard(() => store.api.get<{ report: ConversionReport }>(kind === "model" ? "/export/model" : `/export/drawing/${view}`, query), "Checking the export");
    if (!out) return;
    const sections = reportSections(out.report).filter((s) => s.title !== "Carried over");
    preview.replaceChildren(h("p", { class: "report-status", data: { status: out.report.status ?? "" }, text: statusText(out.report) }),
      ...sections.map((s) => h("div", {}, h("strong", { text: s.title }), h("ul", { class: "issue-list" }, s.items.map((i) => h("li", { text: i }))))));
  };
  select.addEventListener("change", showInfo);
  scope?.addEventListener("change", showInfo);
  showInfo();
  openDialog<true>({
    title: kind === "model" ? "Export the model" : "Export the drawing", size: "wide", focus: () => select,
    body: h("div", { css: { display: "grid", gap: ".9rem" } }, labelled("Format", select), scope ? labelled("What to export", scope) : null, info),
    actions: [{ label: "Export", kind: "primary", run: async () => {
      const f = current();
      if (!f.state.available) { toast(f.state.reason ?? "That format is not available here.", { kind: "error" }); return undefined; }
      const out = await store.exportAs(kind, f.id, { ...(ids() ? { ids: ids() as string[] } : {}), ...(view ? { view } : {}) });
      if (!out) return undefined;
      saveBlob(out.blob, out.filename);
      store.setStatus(`Exported ${out.filename}`);
      const report = (out.job.result?.["report"] ?? {}) as ConversionReport;
      if (report.status && report.status !== "complete") queueMicrotask(() => conversionDialog(`Exported ${out.filename}`, `The file was saved. Not everything could be carried into ${f.id.toUpperCase()}:`, report));
      else toast(`Exported ${out.filename}.`, { kind: "ok", timeoutMs: 3000 });
      return true;
    } }],
  });
}

export type { DrawingFormat, ModelFormat };
