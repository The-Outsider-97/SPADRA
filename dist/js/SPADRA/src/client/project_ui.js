/** Dialogs for what the BIM and documentation phases added: levels, families, project reports and issuing a sheet revision.
 *  Each shows what the server holds and sends commands; none keeps state of its own. */
import { h, labelled, replace } from "../../../tools/web/src/dom.js";
import { glyph } from "../../../tools/web/src/icons.js";
import { confirmDialog, openDialog, saveBlob, toast } from "../../../tools/web/src/ui.js";
import { paginatedReport } from "./report_layout.js";
import { familyTypesPanel, formulaPanel } from "./parametric_ui.js";
/** Keep a dialog's body in step with the project while it is open: re-draw on every change, stop when it closes. */
function live(store, handle, redraw) {
    const off = store.changed.on(redraw);
    void handle.result.then(off);
}
// -------------------------------------------------------------------------------------------------------------------------- levels
export function levelsDialog(store) {
    const body = h("div", { css: { display: "grid", gap: "1rem" } });
    const name = h("input", { type: "text", placeholder: "Level name", attrs: { "aria-label": "New level name", maxlength: 40, autocomplete: "off" } });
    const elevation = h("input", { type: "number", placeholder: "Elevation (m)", attrs: { "aria-label": "New level elevation in metres", step: "any" } });
    const plan = h("input", { type: "checkbox", attrs: { "aria-label": "Make a floor plan for the new level" } });
    plan.checked = true;
    const draw = () => {
        const rows = store.levels.map((l) => {
            const nameIn = h("input", { type: "text", value: l.name, attrs: { "aria-label": `Name of ${l.name}`, maxlength: 40, autocomplete: "off" },
                on: { change: () => { void store.run("level.set", { id: l.id, name: nameIn.value }).then((ok) => { if (!ok)
                        draw(); }); } } });
            const elevIn = h("input", { type: "number", value: String(l.elevation), attrs: { "aria-label": `Elevation of ${l.name} in metres`, step: "any" },
                on: { change: () => { const v = Number(elevIn.value); if (!Number.isFinite(v)) {
                        toast("Enter the elevation as a number of metres.", { kind: "error" });
                        draw();
                        return;
                    } void store.run("level.set", { id: l.id, elevation: v }).then((ok) => { if (!ok)
                        draw(); }); } } });
            return h("tr", {}, h("td", {}, nameIn), h("td", {}, h("div", { class: "with-unit" }, elevIn, h("span", { class: "unit", text: "m" }))), h("td", {}, h("button", { class: "iconbtn", type: "button", attrs: { "aria-label": `Delete ${l.name}`, title: store.levels.length <= 1 ? "A project needs at least one level" : `Delete ${l.name}` }, disabled: store.levels.length <= 1,
                on: { click: () => { void store.run("level.delete", { id: l.id }); } } }, glyph("trash"))));
        });
        replace(body, h("table", { class: "schedule", attrs: { "aria-label": "Levels" } }, h("thead", {}, h("tr", {}, ["Name", "Elevation", ""].map((c) => h("th", { text: c })))), h("tbody", {}, rows)), h("p", { class: "muted", text: "Changing an elevation rebuilds everything that stands on the level, or refuses the change and says why. A level that elements stand on cannot be deleted." }), h("div", { class: "prop-add" }, name, elevation, h("label", { class: "check-inline" }, plan, "With a floor plan"), h("button", { class: "tool-inline", type: "button", text: "Add level", on: { click: () => { void add(); } } })));
    };
    const add = async () => {
        const e = Number(elevation.value);
        if (!name.value.trim() || elevation.value.trim() === "" || !Number.isFinite(e)) {
            toast("Give the level a name and an elevation in metres.", { kind: "info" });
            return;
        }
        if (await store.run("level.add", { name: name.value.trim(), elevation: e, plan: plan.checked })) {
            name.value = "";
            elevation.value = "";
        }
    };
    draw();
    const handle = openDialog({ title: "Levels", size: "wide", noCancel: true, body, focus: () => name, actions: [{ label: "Close", kind: "primary", run: () => true }] });
    live(store, handle, draw);
}
// ------------------------------------------------------------------------------------------------------------------------ families
export async function familiesDialog(store, focusId) {
    const body = h("div", { css: { display: "grid", gap: "1rem" } });
    let selected = focusId ?? "";
    let data = null;
    const reload = async () => { data = await store.guard(() => store.api.get("/families"), "Reading the families"); draw(); };
    const draw = () => {
        if (!data) {
            body.replaceChildren(h("p", { text: "The families could not be read." }));
            return;
        }
        const d = data;
        if (!d.families.some((f) => f.id === selected))
            selected = d.families[0]?.id ?? "";
        const family = d.families.find((f) => f.id === selected);
        const list = h("ul", { class: "recent-list", attrs: { "aria-label": "Families" } }, d.families.map((f) => h("li", {}, h("button", { type: "button", attrs: { "aria-current": String(f.id === selected) }, on: { click: () => { selected = f.id; draw(); } } }, h("strong", { text: f.name }), h("span", { class: "path", text: `${f.shape} · ${f.instances} placed` })))));
        replace(body, d.families.length ? list : h("p", { class: "muted", text: "The project has no families yet. Define one below." }), family ? familyEditor(store, family, () => reload(), () => handle?.close()) : null, defineForm(store, d, (id) => { selected = id; void reload(); }));
    };
    let handle = null;
    await reload();
    handle = openDialog({ title: "Families", size: "wide", noCancel: true, body, actions: [{ label: "Close", kind: "primary", run: () => true }] });
}
function familyEditor(store, family, reload, closeDialog) {
    const name = h("input", { type: "text", value: family.name, attrs: { maxlength: 60, autocomplete: "off" } });
    const rows = family.parameters.map((p) => {
        const value = h("input", { type: "number", value: p.default === null ? "" : String(p.default), attrs: { "aria-label": `${p.label} default (${p.unit})`, step: "any" }, disabled: !!p.expression });
        const formula = h("input", { type: "text", value: p.expression ?? "", placeholder: "none", attrs: { "aria-label": `${p.label} formula`, autocomplete: "off", maxlength: 200 } });
        const min = h("input", { type: "number", value: p.min === null ? "" : String(p.min), attrs: { "aria-label": `${p.label} minimum`, step: "any" } });
        const max = h("input", { type: "number", value: p.max === null ? "" : String(p.max), attrs: { "aria-label": `${p.label} maximum`, step: "any" } });
        return { p, value, formula, min, max, row: h("tr", {}, h("th", { attrs: { scope: "row" }, text: p.unit ? `${p.label} (${p.unit})` : p.label }), h("td", {}, value), h("td", {}, formula), h("td", {}, min), h("td", {}, max)) };
    });
    const apply = async () => {
        const parameters = {};
        for (const r of rows) {
            const spec = {};
            if (r.formula.value.trim() !== (r.p.expression ?? ""))
                spec["expression"] = r.formula.value.trim();
            if (!r.formula.value.trim() && r.value.value !== "" && Number(r.value.value) !== r.p.default)
                spec["default"] = Number(r.value.value);
            if (r.min.value !== (r.p.min === null ? "" : String(r.p.min)))
                spec["min"] = r.min.value === "" ? null : Number(r.min.value);
            if (r.max.value !== (r.p.max === null ? "" : String(r.p.max)))
                spec["max"] = r.max.value === "" ? null : Number(r.max.value);
            if (Object.keys(spec).length)
                parameters[r.p.name] = spec;
        }
        const params = { id: family.id, ...(name.value.trim() !== family.name ? { name: name.value.trim() } : {}), ...(Object.keys(parameters).length ? { parameters } : {}) };
        if (Object.keys(params).length === 1) {
            toast("Nothing was changed.", { kind: "info" });
            return;
        }
        if (await store.run("family.update", params)) {
            toast("The family was updated and every element made from it was rebuilt.", { kind: "ok", timeoutMs: 3000 });
            reload();
        }
    };
    const remove = async () => {
        if (!(await confirmDialog({ title: "Delete family", message: `Delete “${family.name}”?`, confirmLabel: "Delete", danger: true, detail: family.instances ? `${family.instances} placed element(s) still use it, so the server will refuse until they are deleted.` : "Nothing uses it." })))
            return;
        if (await store.run("family.delete", { id: family.id }))
            reload();
    };
    const place = () => { store.family = { id: family.id, name: family.name }; closeDialog(); store.setTool("family"); };
    return h("section", { attrs: { "aria-label": `Family ${family.name}` }, css: { display: "grid", gap: ".6rem" } }, labelled("Name", name), familyTypesPanel(store, family, reload), h("table", { class: "schedule", attrs: { "aria-label": `Parameters of ${family.name}` } }, h("thead", {}, h("tr", {}, ["Parameter", "Default", "Formula", "Min", "Max"].map((c) => h("th", { text: c })))), h("tbody", {}, rows.map((r) => r.row))), h("p", { class: "muted", text: "Sizes are in millimetres. A formula (for example width / 2) replaces the default. Applying rebuilds every placed element, or refuses and says which one does not fit." }), formulaPanel(store, family), h("div", { class: "prop-add" }, h("button", { class: "tool-inline", type: "button", text: "Apply changes", on: { click: () => { void apply(); } } }), h("button", { class: "tool-inline", type: "button", text: "Place on a floor plan", on: { click: place } }), h("a", { class: "tool-inline", attrs: { href: `/api/families/${family.id}/file`, download: "" }, text: "Download as SGEO" }), h("button", { class: "tool-inline", type: "button", text: "Delete", on: { click: () => { void remove(); } } })));
}
function defineForm(store, d, created) {
    const name = h("input", { type: "text", placeholder: "Family name", attrs: { "aria-label": "New family name", maxlength: 60, autocomplete: "off" } });
    const shapes = Object.keys(d.shapes);
    const shape = h("select", { attrs: { "aria-label": "Shape" } }, shapes.map((s) => h("option", { value: s, text: s })));
    const sizes = h("div", { class: "prop-add" });
    const inputs = new Map();
    const drawSizes = () => {
        inputs.clear();
        sizes.replaceChildren(...(d.shapes[shape.value] ?? []).map((n) => {
            const input = h("input", { type: "number", placeholder: `${n} (mm)`, attrs: { "aria-label": `${n} in millimetres`, step: "any", min: "1" } });
            inputs.set(n, input);
            return input;
        }));
    };
    shape.addEventListener("change", drawSizes);
    drawSizes();
    const define = async () => {
        const parameters = {};
        for (const [n, input] of inputs) {
            const v = Number(input.value);
            if (input.value.trim() === "" || !Number.isFinite(v) || v <= 0) {
                toast(`Give ${n} as a size in millimetres greater than zero.`, { kind: "info" });
                input.focus();
                return;
            }
            parameters[n] = { default: v };
        }
        if (!name.value.trim()) {
            toast("Give the family a name.", { kind: "info" });
            name.focus();
            return;
        }
        const out = await store.run("family.define", { name: name.value.trim(), shape: shape.value, parameters });
        const id = out?.result?.id;
        if (id) {
            name.value = "";
            created(id);
        }
    };
    return h("section", { attrs: { "aria-label": "Define a family" }, css: { display: "grid", gap: ".5rem" } }, h("h3", { text: "Define a family" }), h("div", { class: "prop-add" }, name, shape), sizes, h("p", { class: "muted", text: "A family is a parametric box, cylinder or sphere. After defining it you can add formulas and bounds to its parameters." }), h("div", { class: "prop-add" }, h("button", { class: "tool-inline", type: "button", text: "Define family", on: { click: () => { void define(); } } })));
}
// ------------------------------------------------------------------------------------------------------------------------ reports
const REPORT_SECTIONS = [["summary", "Project and model summary"], ["levels", "Levels"], ["schedules", "Schedules"], ["materials", "Material quantities"], ["sheets", "Sheets and revisions"], ["checks", "Model check"], ["requirements", "Information requirements (ILS and IDS)"]];
/** Make a project report (an SLDC document) from chosen sections, as a background operation, then read it. */
export function newReportDialog(store) {
    const name = h("input", { type: "text", value: `${store.project?.name ?? "Project"} report`, attrs: { maxlength: 80, autocomplete: "off", "aria-label": "Report name" } });
    const author = h("input", { type: "text", placeholder: "Report author", attrs: { maxlength: 200, autocomplete: "name", "aria-label": "Report author" } });
    const paper = h("select", { attrs: { "aria-label": "Paper size" } }, ...["A5", "A4", "A3", "B5", "Letter", "Legal", "Tabloid"].map(value => h("option", { value, text: value })));
    paper.value = "A4";
    const orientation = h("select", { attrs: { "aria-label": "Paper orientation" } }, h("option", { value: "portrait", text: "Portrait" }), h("option", { value: "landscape", text: "Landscape" }));
    const boxes = REPORT_SECTIONS.map(([id, label]) => { const box = h("input", { type: "checkbox", attrs: { value: id } }); box.checked = true; return { id, label, box }; });
    const handle = openDialog({
        title: "New report", size: "narrow", focus: () => name,
        body: h("div", { css: { display: "grid", gap: ".8rem" } }, labelled("Name", name), labelled("Author", author), labelled("Paper size", paper), labelled("Orientation", orientation), h("fieldset", { class: "plain-fieldset" }, h("legend", { text: "Sections" }), ...boxes.map((b) => h("label", { class: "check-inline" }, b.box, b.label))), h("p", { class: "muted", text: "A report is a snapshot of the project as it is now, saved in the project as an SLDC document. Later changes are not in it." })),
        actions: [{ label: "Make report", kind: "primary", run: async () => {
                    const sections = boxes.filter((b) => b.box.checked).map((b) => b.id);
                    if (!sections.length) {
                        toast("Choose at least one section.", { kind: "info" });
                        return undefined;
                    }
                    const job = await store.runJob("report", { name: name.value.trim(), author: author.value.trim(),
                        paper: paper.value, orientation: orientation.value, sections }, { refresh: true });
                    const id = job?.result?.id;
                    return job?.status === "done" && id ? id : undefined;
                } }],
    });
    void handle.result.then((id) => { if (id)
        void reportDialog(store, id); });
}
export async function reportDialog(store, id) {
    const view = await store.guard(() => store.api.get(`/report/${id}`), "Reading the report");
    if (!view)
        return;
    const r = view.report;
    let pageView = null;
    try {
        pageView = paginatedReport(view);
    }
    catch (error) {
        toast(error instanceof Error ? error.message : "Report pagination failed", { kind: "error" });
    }
    const handle = openDialog({
        title: r.name, eyebrow: `Made from revision ${r.revision} · ${r.created.slice(0, 16).replace("T", " ")} · ${(r.bytes / 1024).toFixed(1)} KB`, size: "wide", noCancel: true,
        body: h("div", { class: "report-dialog-body", attrs: { "aria-label": `Paginated preview of ${r.name}`, tabindex: "0" } }, pageView ? pageView.preview : h("pre", { class: "report-text", text: view.markdown }), pageView ? h("p", { class: "muted", text: `${pageView.pageCount} numbered content pages · ${pageView.tocCount} table-of-contents page(s). The preview and PDF printout share the same layout.` }) : null),
        actions: [
            { label: "Delete", kind: "danger", run: () => "delete" },
            { label: "Print / PDF", run: () => { if (pageView)
                    pageView.print(); return undefined; } },
            { label: "Download SLDC", run: async () => {
                    const file = await store.guard(() => store.api.download(`/export/report/${id}`), "Downloading the report");
                    if (file)
                        saveBlob(file.blob, file.filename);
                    return undefined;
                } },
            { label: "Close", kind: "primary", run: () => null },
        ],
    });
    if ((await handle.result) === "delete")
        await store.run("report.delete", { id });
}
// ---------------------------------------------------------------------------------------------------------------- issue a revision
/** Issue a revision of a sheet: its label, what changed, who and when. The server records the model revision and fingerprints what the sheet shows. */
export function issueDialog(store, sheetId) {
    const label = h("input", { type: "text", placeholder: "Automatic", attrs: { maxlength: 10, autocomplete: "off" } });
    const description = h("input", { type: "text", placeholder: "What changed", attrs: { maxlength: 200, autocomplete: "off" } });
    const by = h("input", { type: "text", attrs: { maxlength: 40, autocomplete: "off" } });
    const date = h("input", { type: "text", placeholder: "Today", attrs: { maxlength: 10, autocomplete: "off", inputmode: "numeric" } });
    openDialog({
        title: "Issue a revision", size: "narrow", focus: () => description,
        body: h("div", { css: { display: "grid", gap: ".8rem" } }, labelled("Description", description, { hint: "Shown in the sheet's revision table." }), labelled("Revision label", label, { hint: "Leave empty for the next one." }), labelled("By", by), labelled("Date (YYYY-MM-DD)", date)),
        actions: [{ label: "Issue", kind: "primary", run: async () => {
                    if (!description.value.trim()) {
                        toast("Describe the revision.", { kind: "info" });
                        description.focus();
                        return undefined;
                    }
                    const out = await store.run("sheet.issue", { id: sheetId, description: description.value.trim(), ...(label.value.trim() ? { revision: label.value.trim() } : {}), ...(by.value.trim() ? { by: by.value.trim() } : {}), ...(date.value.trim() ? { date: date.value.trim() } : {}) });
                    return out ? true : undefined;
                } }],
    });
}
