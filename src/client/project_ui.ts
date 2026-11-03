/** Dialogs for what the BIM and documentation phases added: levels, families, project reports and issuing a sheet revision.
 *  Each shows what the server holds and sends commands; none keeps state of its own. */
import { h, labelled, replace } from "../../../tools/web/src/dom.js";
import { glyph } from "../../../tools/web/src/icons.js";
import { confirmDialog, openDialog, saveBlob, toast } from "../../../tools/web/src/ui.js";
import type { Store } from "./store.js";
import type { FamiliesResponse, FamilyInfo, ReportView } from "./types.js";

/** Keep a dialog's body in step with the project while it is open: re-draw on every change, stop when it closes. */
function live(store: Store, handle: { result: Promise<unknown> }, redraw: () => void): void {
  const off = store.changed.on(redraw);
  void handle.result.then(off);
}

// -------------------------------------------------------------------------------------------------------------------------- levels
export function levelsDialog(store: Store): void {
  const body = h("div", { css: { display: "grid", gap: "1rem" } });
  const name = h("input", { type: "text", placeholder: "Level name", attrs: { "aria-label": "New level name", maxlength: 40, autocomplete: "off" } });
  const elevation = h("input", { type: "number", placeholder: "Elevation (m)", attrs: { "aria-label": "New level elevation in metres", step: "any" } });
  const plan = h("input", { type: "checkbox", attrs: { "aria-label": "Make a floor plan for the new level" } });
  plan.checked = true;
  const draw = (): void => {
    const rows = store.levels.map((l) => {
      const nameIn = h("input", { type: "text", value: l.name, attrs: { "aria-label": `Name of ${l.name}`, maxlength: 40, autocomplete: "off" },
        on: { change: () => { void store.run("level.set", { id: l.id, name: nameIn.value }).then((ok) => { if (!ok) draw(); }); } } });
      const elevIn = h("input", { type: "number", value: String(l.elevation), attrs: { "aria-label": `Elevation of ${l.name} in metres`, step: "any" },
        on: { change: () => { const v = Number(elevIn.value); if (!Number.isFinite(v)) { toast("Enter the elevation as a number of metres.", { kind: "error" }); draw(); return; } void store.run("level.set", { id: l.id, elevation: v }).then((ok) => { if (!ok) draw(); }); } } });
      return h("tr", {}, h("td", {}, nameIn), h("td", {}, h("div", { class: "with-unit" }, elevIn, h("span", { class: "unit", text: "m" }))),
        h("td", {}, h("button", { class: "iconbtn", type: "button", attrs: { "aria-label": `Delete ${l.name}`, title: store.levels.length <= 1 ? "A project needs at least one level" : `Delete ${l.name}` }, disabled: store.levels.length <= 1,
          on: { click: () => { void store.run("level.delete", { id: l.id }); } } }, glyph("trash"))));
    });
    replace(body,
      h("table", { class: "schedule", attrs: { "aria-label": "Levels" } }, h("thead", {}, h("tr", {}, ["Name", "Elevation", ""].map((c) => h("th", { text: c })))), h("tbody", {}, rows)),
      h("p", { class: "muted", text: "Changing an elevation rebuilds everything that stands on the level, or refuses the change and says why. A level that elements stand on cannot be deleted." }),
      h("div", { class: "prop-add" }, name, elevation, h("label", { class: "check-inline" }, plan, "With a floor plan"),
        h("button", { class: "tool-inline", type: "button", text: "Add level", on: { click: () => { void add(); } } })));
  };
  const add = async (): Promise<void> => {
    const e = Number(elevation.value);
    if (!name.value.trim() || elevation.value.trim() === "" || !Number.isFinite(e)) { toast("Give the level a name and an elevation in metres.", { kind: "info" }); return; }
    if (await store.run("level.add", { name: name.value.trim(), elevation: e, plan: plan.checked })) { name.value = ""; elevation.value = ""; }
  };
  draw();
  const handle = openDialog({ title: "Levels", size: "wide", noCancel: true, body, focus: () => name, actions: [{ label: "Close", kind: "primary", run: () => true }] });
  live(store, handle, draw);
}

// ------------------------------------------------------------------------------------------------------------------------ families
export async function familiesDialog(store: Store, focusId?: string): Promise<void> {
  const body = h("div", { css: { display: "grid", gap: "1rem" } });
  let selected = focusId ?? "";
  let data: FamiliesResponse | null = null;
  const reload = async (): Promise<void> => { data = await store.guard(() => store.api.get<FamiliesResponse>("/families"), "Reading the families"); draw(); };
  const draw = (): void => {
    if (!data) { body.replaceChildren(h("p", { text: "The families could not be read." })); return; }
    const d = data;
    if (!d.families.some((f) => f.id === selected)) selected = d.families[0]?.id ?? "";
    const family = d.families.find((f) => f.id === selected);
    const list = h("ul", { class: "recent-list", attrs: { "aria-label": "Families" } }, d.families.map((f) => h("li", {}, h("button", { type: "button", attrs: { "aria-current": String(f.id === selected) }, on: { click: () => { selected = f.id; draw(); } } },
      h("strong", { text: f.name }), h("span", { class: "path", text: `${f.shape} · ${f.instances} placed` })))));
    replace(body, d.families.length ? list : h("p", { class: "muted", text: "The project has no families yet. Define one below." }),
      family ? familyEditor(store, family, () => reload(), () => handle?.close()) : null, defineForm(store, d, (id) => { selected = id; void reload(); }));
  };
  let handle: ReturnType<typeof openDialog<true>> | null = null;
  await reload();
  handle = openDialog({ title: "Families", size: "wide", noCancel: true, body, actions: [{ label: "Close", kind: "primary", run: () => true }] });
}

function familyEditor(store: Store, family: FamilyInfo, reload: () => void, closeDialog: () => void): HTMLElement {
  const name = h("input", { type: "text", value: family.name, attrs: { maxlength: 60, autocomplete: "off" } });
  const rows = family.parameters.map((p) => {
    const value = h("input", { type: "number", value: p.default === null ? "" : String(p.default), attrs: { "aria-label": `${p.label} default (${p.unit})`, step: "any" }, disabled: !!p.expression });
    const formula = h("input", { type: "text", value: p.expression ?? "", placeholder: "none", attrs: { "aria-label": `${p.label} formula`, autocomplete: "off", maxlength: 200 } });
    const min = h("input", { type: "number", value: p.min === null ? "" : String(p.min), attrs: { "aria-label": `${p.label} minimum`, step: "any" } });
    const max = h("input", { type: "number", value: p.max === null ? "" : String(p.max), attrs: { "aria-label": `${p.label} maximum`, step: "any" } });
    return { p, value, formula, min, max, row: h("tr", {}, h("th", { attrs: { scope: "row" }, text: p.unit ? `${p.label} (${p.unit})` : p.label }), h("td", {}, value), h("td", {}, formula), h("td", {}, min), h("td", {}, max)) };
  });
  const apply = async (): Promise<void> => {
    const parameters: Record<string, Record<string, unknown>> = {};
    for (const r of rows) {
      const spec: Record<string, unknown> = {};
      if (r.formula.value.trim() !== (r.p.expression ?? "")) spec["expression"] = r.formula.value.trim();
      if (!r.formula.value.trim() && r.value.value !== "" && Number(r.value.value) !== r.p.default) spec["default"] = Number(r.value.value);
      if (r.min.value !== (r.p.min === null ? "" : String(r.p.min))) spec["min"] = r.min.value === "" ? null : Number(r.min.value);
      if (r.max.value !== (r.p.max === null ? "" : String(r.p.max))) spec["max"] = r.max.value === "" ? null : Number(r.max.value);
      if (Object.keys(spec).length) parameters[r.p.name] = spec;
    }
    const params: Record<string, unknown> = { id: family.id, ...(name.value.trim() !== family.name ? { name: name.value.trim() } : {}), ...(Object.keys(parameters).length ? { parameters } : {}) };
    if (Object.keys(params).length === 1) { toast("Nothing was changed.", { kind: "info" }); return; }
    if (await store.run("family.update", params)) { toast("The family was updated and every element made from it was rebuilt.", { kind: "ok", timeoutMs: 3000 }); reload(); }
  };
  const remove = async (): Promise<void> => {
    if (!(await confirmDialog({ title: "Delete family", message: `Delete “${family.name}”?`, confirmLabel: "Delete", danger: true, detail: family.instances ? `${family.instances} placed element(s) still use it, so the server will refuse until they are deleted.` : "Nothing uses it." }))) return;
    if (await store.run("family.delete", { id: family.id })) reload();
  };
  const place = (): void => { store.family = { id: family.id, name: family.name }; closeDialog(); store.setTool("family"); };
  return h("section", { attrs: { "aria-label": `Family ${family.name}` }, css: { display: "grid", gap: ".6rem" } },
    labelled("Name", name),
    h("table", { class: "schedule", attrs: { "aria-label": `Parameters of ${family.name}` } }, h("thead", {}, h("tr", {}, ["Parameter", "Default", "Formula", "Min", "Max"].map((c) => h("th", { text: c })))), h("tbody", {}, rows.map((r) => r.row))),
    h("p", { class: "muted", text: "Sizes are in millimetres. A formula (for example width / 2) replaces the default. Applying rebuilds every placed element, or refuses and says which one does not fit." }),
    h("div", { class: "prop-add" }, h("button", { class: "tool-inline", type: "button", text: "Apply changes", on: { click: () => { void apply(); } } }),
      h("button", { class: "tool-inline", type: "button", text: "Place on a floor plan", on: { click: place } }),
      h("a", { class: "tool-inline", attrs: { href: `/api/families/${family.id}/file`, download: "" }, text: "Download as SGEO" }),
      h("button", { class: "tool-inline", type: "button", text: "Delete", on: { click: () => { void remove(); } } })));
}

function defineForm(store: Store, d: FamiliesResponse, created: (id: string) => void): HTMLElement {
  const name = h("input", { type: "text", placeholder: "Family name", attrs: { "aria-label": "New family name", maxlength: 60, autocomplete: "off" } });
  const shapes = Object.keys(d.shapes);
  const shape = h("select", { attrs: { "aria-label": "Shape" } }, shapes.map((s) => h("option", { value: s, text: s })));
  const sizes = h("div", { class: "prop-add" });
  const inputs = new Map<string, HTMLInputElement>();
  const drawSizes = (): void => {
    inputs.clear();
    sizes.replaceChildren(...(d.shapes[shape.value] ?? []).map((n) => {
      const input = h("input", { type: "number", placeholder: `${n} (mm)`, attrs: { "aria-label": `${n} in millimetres`, step: "any", min: "1" } });
      inputs.set(n, input);
      return input;
    }));
  };
  shape.addEventListener("change", drawSizes);
  drawSizes();
  const define = async (): Promise<void> => {
    const parameters: Record<string, { default: number }> = {};
    for (const [n, input] of inputs) {
      const v = Number(input.value);
      if (input.value.trim() === "" || !Number.isFinite(v) || v <= 0) { toast(`Give ${n} as a size in millimetres greater than zero.`, { kind: "info" }); input.focus(); return; }
      parameters[n] = { default: v };
    }
    if (!name.value.trim()) { toast("Give the family a name.", { kind: "info" }); name.focus(); return; }
    const out = await store.run("family.define", { name: name.value.trim(), shape: shape.value, parameters });
    const id = (out?.result as { id?: string } | undefined)?.id;
    if (id) { name.value = ""; created(id); }
  };
  return h("section", { attrs: { "aria-label": "Define a family" }, css: { display: "grid", gap: ".5rem" } }, h("h3", { text: "Define a family" }),
    h("div", { class: "prop-add" }, name, shape), sizes,
    h("p", { class: "muted", text: "A family is a parametric box, cylinder or sphere. After defining it you can add formulas and bounds to its parameters." }),
    h("div", { class: "prop-add" }, h("button", { class: "tool-inline", type: "button", text: "Define family", on: { click: () => { void define(); } } })));
}

// ------------------------------------------------------------------------------------------------------------------------ reports
const REPORT_SECTIONS: Array<[string, string]> = [["summary", "Project and model summary"], ["levels", "Levels"], ["schedules", "Schedules"], ["materials", "Material quantities"], ["sheets", "Sheets and revisions"], ["checks", "Model check"]];

/** Make a project report (an SLDC document) from chosen sections, as a background operation, then read it. */
export function newReportDialog(store: Store): void {
  const name = h("input", { type: "text", value: `${store.project?.name ?? "Project"} report`, attrs: { maxlength: 80, autocomplete: "off", "aria-label": "Report name" } });
  const boxes = REPORT_SECTIONS.map(([id, label]) => { const box = h("input", { type: "checkbox", attrs: { value: id } }); box.checked = true; return { id, label, box }; });
  const handle = openDialog<string>({
    title: "New report", size: "narrow", focus: () => name,
    body: h("div", { css: { display: "grid", gap: ".8rem" } }, labelled("Name", name),
      h("fieldset", { class: "plain-fieldset" }, h("legend", { text: "Sections" }), ...boxes.map((b) => h("label", { class: "check-inline" }, b.box, b.label))),
      h("p", { class: "muted", text: "A report is a snapshot of the project as it is now, saved in the project as an SLDC document. Later changes are not in it." })),
    actions: [{ label: "Make report", kind: "primary", run: async () => {
      const sections = boxes.filter((b) => b.box.checked).map((b) => b.id);
      if (!sections.length) { toast("Choose at least one section.", { kind: "info" }); return undefined; }
      const job = await store.runJob("report", { name: name.value.trim(), sections }, { refresh: true });
      const id = (job?.result as { id?: string } | undefined)?.id;
      return job?.status === "done" && id ? id : undefined;
    } }],
  });
  void handle.result.then((id) => { if (id) void reportDialog(store, id); });
}

export async function reportDialog(store: Store, id: string): Promise<void> {
  const view = await store.guard(() => store.api.get<ReportView>(`/report/${id}`), "Reading the report");
  if (!view) return;
  const r = view.report;
  const handle = openDialog<"delete">({
    title: r.name, eyebrow: `Made from revision ${r.revision} · ${r.created.slice(0, 16).replace("T", " ")} · ${(r.bytes / 1024).toFixed(1)} KB`, size: "wide", noCancel: true,
    body: h("pre", { class: "report-text", attrs: { tabindex: "0", "aria-label": `Text of ${r.name}` }, text: view.markdown }),
    actions: [
      { label: "Delete", kind: "danger", run: () => "delete" },
      { label: "Download SLDC", run: async () => {
        const file = await store.guard(() => store.api.download(`/export/report/${id}`), "Downloading the report");
        if (file) saveBlob(file.blob, file.filename);
        return undefined;
      } },
      { label: "Close", kind: "primary", run: () => null },
    ],
  });
  if ((await handle.result) === "delete") await store.run("report.delete", { id });
}

// ---------------------------------------------------------------------------------------------------------------- issue a revision
/** Issue a revision of a sheet: its label, what changed, who and when. The server records the model revision and fingerprints what the sheet shows. */
export function issueDialog(store: Store, sheetId: string): void {
  const label = h("input", { type: "text", placeholder: "Automatic", attrs: { maxlength: 10, autocomplete: "off" } });
  const description = h("input", { type: "text", placeholder: "What changed", attrs: { maxlength: 200, autocomplete: "off" } });
  const by = h("input", { type: "text", attrs: { maxlength: 40, autocomplete: "off" } });
  const date = h("input", { type: "text", placeholder: "Today", attrs: { maxlength: 10, autocomplete: "off", inputmode: "numeric" } });
  openDialog<true>({
    title: "Issue a revision", size: "narrow", focus: () => description,
    body: h("div", { css: { display: "grid", gap: ".8rem" } }, labelled("Description", description, { hint: "Shown in the sheet's revision table." }), labelled("Revision label", label, { hint: "Leave empty for the next one." }),
      labelled("By", by), labelled("Date (YYYY-MM-DD)", date)),
    actions: [{ label: "Issue", kind: "primary", run: async () => {
      if (!description.value.trim()) { toast("Describe the revision.", { kind: "info" }); description.focus(); return undefined; }
      const out = await store.run("sheet.issue", { id: sheetId, description: description.value.trim(), ...(label.value.trim() ? { revision: label.value.trim() } : {}), ...(by.value.trim() ? { by: by.value.trim() } : {}), ...(date.value.trim() ? { date: date.value.trim() } : {}) });
      return out ? true : undefined;
    } }],
  });
}
