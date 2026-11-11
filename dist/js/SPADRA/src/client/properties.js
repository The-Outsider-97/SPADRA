/** The Properties panel. Everything shown comes from the server for the current selection; every edit is a command.
 *  Rows are editable fields, read-only figures, or links (a relationship the person can follow: to a host, a room, a view, a sheet, a tag, a dialog).
 *  Custom properties and sheet notes can be added and removed; a sheet's revision history opens the issue dialog. */
import { h } from "../../../tools/web/src/dom.js";
import { glyph } from "../../../tools/web/src/icons.js";
import { promptDialog, toast } from "../../../tools/web/src/ui.js";
const open = new Map(); // which property groups the person has collapsed, kept across refreshes
export function renderProperties(store) {
    const p = store.properties;
    usedIds.clear();
    if (!p)
        return projectInfo(store);
    const extra = store.selection && "ids" in store.selection && store.selection.ids.length > 1
        ? [h("p", { class: "note", text: `${store.selection.ids.length} items are selected. Showing the first.` })] : [];
    return [
        h("div", { class: "prop-title" }, glyph(iconFor(p)), h("span", { text: p.title })),
        ...extra,
        ...p.top.map((row) => rowEl(store, p, row)),
        ...p.groups.map((g) => group(store, p, g)),
    ];
}
export function iconFor(p) {
    if (p.kind === "view")
        return p.category === "section" ? "section" : p.category === "elevation" ? "elevation" : p.category === "schedule" ? "table" : "plan";
    if (p.kind === "sheet")
        return "page";
    if (p.kind === "level")
        return "grid";
    if (p.kind === "entity")
        return p.category === "tag" ? "tag" : p.category === "dimension" ? "dimension" : p.category === "text" ? "text" : p.category === "circle" ? "circle" : "line";
    const byCategory = { wall: "wall", curtainwall: "curtainwall", door: "door", window: "window", floor: "floor", roof: "roof", gutter: "gutter", downpipe: "downpipe", foundation: "foundation", structural: "beam", beam: "beam", column: "column", room: "room", stair: "stair", grid: "grid", generic: "family" };
    return byCategory[p.category] ?? "cube";
}
function projectInfo(store) {
    const pr = store.project;
    if (!pr)
        return [h("p", { class: "note", text: "No project is open." })];
    const row = (label, value) => h("div", { class: "prop-row" }, h("span", { class: "label", text: label }), h("div", { class: "value", text: value }));
    const stale = store.sync?.stale_sheets ?? [];
    return [
        h("div", { class: "prop-title" }, glyph("info"), h("span", { text: "Project" })),
        row("Name", pr.name), row("Unit", pr.unit), row("Revision", String(pr.revision)), row("Saved to", pr.path ?? "Not saved yet"),
        row("Levels", String(store.levels.length)),
        ...(store.sync ? [row("Sheets", store.sync.sheets.length ? `${store.sync.sheets.length}${stale.length ? `, ${stale.length} changed since issue (${stale.join(", ")})` : ""}` : "none yet")] : []),
        ...(store.sync?.detached_tags.length ? [row("Tags", `${store.sync.detached_tags.length} lost their element`)] : []),
        h("p", { class: "note", text: "Select something in the drawing, the model or the Project Browser to see its properties." }),
    ];
}
function group(store, p, g) {
    const key = `${p.kind}:${g.title.replace(/\s*\(\d+\)$/, "")}`;
    const isOpen = open.get(key) ?? !g.collapsed;
    const el = h("div", { class: "prop-group", data: { open: String(isOpen) } });
    el.appendChild(h("button", { class: "group-head", type: "button", attrs: { "aria-expanded": String(isOpen) },
        on: { click: (e) => { const now = !(open.get(key) ?? !g.collapsed); open.set(key, now); el.dataset["open"] = String(now); e.currentTarget.setAttribute("aria-expanded", String(now)); } } }, glyph("chevron-down"), g.title));
    for (const row of g.rows)
        el.appendChild(rowEl(store, p, row));
    if (g.add)
        el.appendChild(addForm(store, p, g.add));
    if (g.action) {
        const action = g.action;
        el.appendChild(h("div", { class: "prop-row" }, h("button", { class: "tool-inline", type: "button", text: action.label, on: { click: () => {
                    if (action.command)
                        void store.run(action.command, action.params ?? {}); // an action that is a command (flip a wall) runs at once
                    else if (action.dialog)
                        store.openDialog?.(action.dialog, p.ref["sheet"]);
                } } })));
    }
    return el;
}
function format(value) {
    return typeof value === "number" ? String(Math.round(value * 1e4) / 1e4) : value; // four decimals is finer than any drawing tolerance here
}
/** Row ids are `prop-<key>`, which tests and assistive technology can rely on; a key that appears twice in one panel gets a number. Reset on every render. */
const usedIds = new Set();
function rowId(row) {
    const base = `prop-${(row.key ?? row.label).replace(/\W+/g, "-").toLowerCase()}`;
    let id = base;
    for (let n = 2; usedIds.has(id); n++)
        id = `${base}-${n}`;
    usedIds.add(id);
    return id;
}
function rowEl(store, p, row) {
    const id = rowId(row);
    const label = h("label", { attrs: { for: id }, text: row.label });
    const remove = row.removable && row.key ? h("button", { class: "iconbtn remove", type: "button", attrs: { "aria-label": `Remove ${row.label}`, title: `Remove ${row.label}` }, on: { click: () => { void removeRow(store, p, row.key); } } }, glyph("close")) : null;
    if (row.type === "link") {
        const target = row.target;
        const body = target
            ? h("button", { id, class: "link-row", type: "button", attrs: { title: `Go to ${row.value}` }, on: { click: () => { void store.follow(target); } } }, h("span", { text: String(row.value) }), glyph("arrow-right"))
            : h("div", { class: "value", text: String(row.value) });
        return h("div", { class: "prop-row" }, label, body);
    }
    if (row.type === "readonly" || !row.key)
        return h("div", { class: "prop-row" }, h("span", { class: "label", text: row.label }), h("div", { class: "value", text: String(row.value) }));
    const key = row.key;
    if (row.type === "color") {
        const initial = String(row.value || row.text || "#BFBFC7");
        const valid = /^#[0-9A-F]{6}$/i;
        const initialHex = valid.test(initial) ? initial.toUpperCase() : "#BFBFC7";
        const asRgb = (value) => [1, 3, 5].map((i) => String(parseInt(value.slice(i, i + 2), 16))).join(", ");
        const picker = h("input", { id, type: "color", value: initialHex, attrs: { "aria-label": `Choose ${row.label}` } });
        const hex = h("input", { type: "text", value: String(row.value || ""), placeholder: "Default", attrs: { maxlength: 7, "aria-label": `${row.label} HEX color` } });
        const rgb = h("input", { type: "text", value: asRgb(initialHex), placeholder: "R, G, B", attrs: { maxlength: 15, "aria-label": `${row.label} RGB color`, title: "Enter red, green and blue values (0–255), separated by commas" } });
        const reflect = (value) => { picker.value = value; hex.value = value; rgb.value = asRgb(value); };
        picker.addEventListener("input", () => { hex.value = picker.value.toUpperCase(); rgb.value = asRgb(picker.value); });
        picker.addEventListener("change", () => { const value = picker.value.toUpperCase(); reflect(value); void commit(store, p, key, value, "text"); });
        hex.addEventListener("change", () => {
            const value = hex.value.trim().toUpperCase();
            if (value && !valid.test(value)) {
                toast("Use a six-digit HEX color, for example #4477AA.", { kind: "error" });
                return;
            }
            if (value)
                reflect(value);
            void commit(store, p, key, value, "text");
        });
        rgb.addEventListener("change", () => {
            const match = rgb.value.trim().match(/^(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})$/);
            const channels = match?.slice(1).map(Number);
            if (!channels || channels.some((v) => v > 255)) {
                toast("Enter RGB as three whole numbers from 0 to 255, for example 68, 119, 170.", { kind: "error" });
                return;
            }
            const value = `#${channels.map((channel) => channel.toString(16).padStart(2, "0")).join("")}`.toUpperCase();
            reflect(value);
            void commit(store, p, key, value, "text");
        });
        const reset = h("button", { type: "button", class: "iconbtn remove", attrs: { title: `Reset ${row.label}`, "aria-label": `Reset ${row.label}` }, on: { click: () => void commit(store, p, key, "", "text") } }, glyph("close"));
        return h("div", { class: "prop-row" }, label, h("div", { class: "color-controls" }, h("div", { class: "color-controls-main" }, picker, hex, reset), rgb));
    }
    if (row.type === "multiline") {
        const input = h("textarea", { id, attrs: { rows: 3, maxlength: 2000, "aria-label": row.label } });
        input.value = String(row.value ?? "");
        input.addEventListener("change", () => { void commit(store, p, key, input.value, "multiline"); });
        return h("div", { class: "prop-row" }, label, input);
    }
    if (row.type === "sfb")
        return sfbRow(store, p, row, id, label);
    if (row.type === "catalog")
        return catalogRow(store, p, row, id, label);
    if (row.type === "choice") {
        const select = h("select", { id, on: { change: () => {
                    if (p.kind === "sheet" && key === "paper" && select.value === "custom") {
                        void (async () => {
                            const width = await promptDialog({ title: "Custom paper", label: "Width (mm)", placeholder: "900", confirmLabel: "Next" });
                            if (width === null) {
                                select.value = String(row.value);
                                return;
                            }
                            const height = await promptDialog({ title: "Custom paper", label: "Height (mm)", placeholder: "600", confirmLabel: "Set paper" });
                            const w = Number(width), h = Number(height);
                            if (height === null) {
                                select.value = String(row.value);
                                return;
                            }
                            if (![w, h].every((v) => Number.isFinite(v) && v >= 210 && v <= 5000)) {
                                toast("Custom paper width and height must be 210–5000 mm.", { kind: "error" });
                                select.value = String(row.value);
                                return;
                            }
                            await commit(store, p, key, `Custom ${w}x${h} mm`, "choice");
                        })();
                    }
                    else
                        void commit(store, p, key, select.value, "choice");
                } } }, ...(row.choices ?? []).map((c) => h("option", { value: c.value, text: c.label, selected: c.value === row.value })));
        return h("div", { class: "prop-row" }, label, remove ? h("div", { class: "with-unit" }, select, remove) : select);
    }
    const input = h("input", { id, type: row.type === "number" ? "number" : "text", value: format(row.value), attrs: { step: "any", ...(row.type === "text" ? { maxlength: p.kind === "sheet" && key.startsWith("note:") ? 500 : 200 } : {}) },
        on: { change: () => { void commit(store, p, key, input.value, row.type); }, keydown: (e) => { if (e.key === "Enter")
                input.blur(); } } });
    const field = row.unit ? h("div", { class: "with-unit" }, input, h("span", { class: "unit", text: row.unit }), remove) : remove ? h("div", { class: "with-unit" }, input, remove) : input;
    return h("div", { class: "prop-row" }, label, field);
}
/** An NL-SfB code: type a number or a word (Dutch or English), pick from the list the server searches; the name of the code in force is shown under the field. */
function sfbRow(store, p, row, id, label) {
    const key = row.key, table = row.table ?? 1;
    const list = h("datalist", { id: `${id}-list` });
    const name = h("div", { class: "sfb-name", text: row.text ?? "", attrs: { "aria-live": "polite" } });
    const input = h("input", { id, type: "text", value: String(row.value), placeholder: "Number or word", attrs: { list: `${id}-list`, autocomplete: "off", maxlength: 80, "aria-describedby": `${id}-name` } });
    name.id = `${id}-name`;
    const fill = (entries) => {
        list.replaceChildren(...entries.map((e) => h("option", { value: e.code, text: `${e.nl}${e.en ? ` / ${e.en}` : ""}` })));
    };
    fill(row.suggest ?? []);
    let timer;
    input.addEventListener("input", () => {
        window.clearTimeout(timer);
        const q = input.value.trim();
        timer = window.setTimeout(async () => {
            if (!q) {
                fill(row.suggest ?? []);
                return;
            }
            try {
                fill((await store.api.get(`/sfb/search?table=${table}&q=${encodeURIComponent(q)}&limit=40`)).results);
            }
            catch { /* the list is a convenience; the server checks the code when it is set */ }
        }, 150);
    });
    input.addEventListener("change", () => { void commit(store, p, key, input.value, "text"); });
    input.addEventListener("keydown", (e) => { if (e.key === "Enter")
        input.blur(); });
    const clear = row.value !== "" ? h("button", { class: "iconbtn remove", type: "button", attrs: { "aria-label": `Clear ${row.label}`, title: "Clear the code" }, on: { click: () => { void commit(store, p, key, "", "text"); } } }, glyph("close")) : null;
    return h("div", { class: "prop-row sfb-row" }, label, h("div", { class: "sfb-field" }, h("div", { class: "with-unit" }, input, clear), name, list));
}
/** Searchable standard-backed picker. The server remains authoritative for values and crosswalk rules. */
function catalogRow(store, p, row, id, label) {
    const kind = row.catalog ?? "ifc";
    const table = row.table ?? 21;
    const key = row.key;
    const list = h("datalist", { id: id + "-list" });
    const help = h("div", { class: "sfb-name", text: row.text ?? "", attrs: { "aria-live": "polite" } });
    help.id = id + "-name";
    const input = h("input", { id, type: "text", value: String(row.value),
        placeholder: kind === "ifc" ? "IfcWallType" : kind === "assembly" ? "Assembly code" : "OmniClass code",
        attrs: { list: id + "-list", autocomplete: "off", maxlength: 100, "aria-describedby": help.id } });
    const fill = (rows) => {
        list.replaceChildren(...rows.map(e => h("option", { value: e.code, text: e.title })));
    };
    fill(row.catalog_suggest ?? []);
    let timer;
    input.addEventListener("input", () => {
        window.clearTimeout(timer);
        timer = window.setTimeout(async () => {
            const q = input.value.trim();
            if (!q) {
                fill(row.catalog_suggest ?? []);
                return;
            }
            try {
                const uri = "/classifications/search?kind=" + encodeURIComponent(kind) +
                    "&table=" + table + "&q=" + encodeURIComponent(q) + "&limit=40";
                const result = await store.api.get(uri);
                fill(result.results);
            }
            catch { /* Server enforces catalog integrity on commit. */ }
        }, 150);
    });
    input.addEventListener("change", () => { void commit(store, p, key, input.value, "text"); });
    input.addEventListener("keydown", e => { if (e.key === "Enter")
        input.blur(); });
    const clear = row.value !== "" ? h("button", { class: "iconbtn remove", type: "button",
        attrs: { "aria-label": "Clear " + row.label, title: "Clear the selected classification" },
        on: { click: () => { void commit(store, p, key, "", "text"); } } }, glyph("close")) : null;
    return h("div", { class: "prop-row sfb-row" }, label, h("div", { class: "sfb-field" }, h("div", { class: "with-unit" }, input, clear), help, list));
}
/** A name and a value for a new custom property, or the text of a new sheet note. */
function addForm(store, p, kind) {
    if (kind === "layer") { // the "+": one more layer, which the person then names and sizes in its rows
        const plus = h("button", { class: "tool-inline", type: "button", text: "+ Add layer", attrs: { title: "Add a layer to this element" }, on: { click: () => { void store.run("model.add_layer", { id: p.ref["model"] }); } } });
        return h("div", { class: "prop-add" }, plus);
    }
    if (kind === "panel") { // the "+": split the widest panel of the door
        const plus = h("button", { class: "tool-inline", type: "button", text: "+ Add panel", attrs: { title: "Split the widest panel of this door in two" }, on: { click: () => { void store.run("model.add_panel", { id: p.ref["model"] }); } } });
        return h("div", { class: "prop-add" }, plus);
    }
    const name = h("input", { type: "text", placeholder: "Name", attrs: { "aria-label": "New property name", maxlength: 40, autocomplete: "off" } });
    const value = h("input", { type: "text", placeholder: kind === "custom" ? "Value" : "Note text", attrs: { "aria-label": kind === "custom" ? "New property value" : "New note text", maxlength: kind === "custom" ? 200 : 500, autocomplete: "off" } });
    const add = async () => {
        if (kind === "custom") {
            if (!name.value.trim()) {
                toast("Give the property a name (a letter first; letters, digits and _).", { kind: "info" });
                name.focus();
                return;
            }
            await commit(store, p, `custom:${name.value.trim()}`, value.value, "text");
        }
        else {
            if (!value.value.trim()) {
                toast("Type the note first.", { kind: "info" });
                value.focus();
                return;
            }
            await store.run("sheet.add_note", { id: p.ref["sheet"], text: value.value.trim() });
        }
    };
    const button = h("button", { class: "tool-inline", type: "button", text: kind === "custom" ? "Add property" : "Add note", on: { click: () => { void add(); } } });
    value.addEventListener("keydown", (e) => { if (e.key === "Enter") {
        e.preventDefault();
        void add();
    } });
    return h("div", { class: "prop-add" }, ...(kind === "custom" ? [name] : []), value, button);
}
async function removeRow(store, p, key) {
    if (p.kind === "model" && key.startsWith("custom:"))
        await store.run("model.set_property", { id: p.ref["model"], key, value: null });
    else if (p.kind === "model" && key.startsWith("layer:"))
        await store.run("model.remove_layer", { id: p.ref["model"], layer: key.split(":")[1] });
    else if (p.kind === "model" && key.startsWith("panel:"))
        await store.run("model.remove_panel", { id: p.ref["model"], panel: key.split(":")[1] });
    else if (p.kind === "sheet" && key.startsWith("note:"))
        await store.run("sheet.remove_note", { id: p.ref["sheet"], note: key.slice(5) });
}
/** Which command carries an edit of a row, and its parameters; null when the row is not editable here. Exported so a test can pin every mapping. */
export function editCommand(p, key, value) {
    const ref = p.ref;
    if (p.kind === "model" && key.startsWith("modifier:")) {
        const [, modifier, ...parts] = key.split(":");
        if (!modifier || !parts.length)
            return null;
        return { op: "model.set_modifier", params: { id: ref["model"], modifier, key: parts.join(":"), value } };
    }
    if (p.kind === "model")
        return { op: "model.set_property", params: { id: ref["model"], key, value } };
    if (p.kind === "level" && ["name", "description", "elevation", "height"].includes(key))
        return { op: "level.set", params: { id: ref["level"], [key]: value } };
    if (p.kind === "sheet") {
        if (key.startsWith("vp:")) {
            const [, viewport, field] = key.split(":");
            return { op: "sheet.set_viewport", params: { id: ref["sheet"], viewport, key: field, value } };
        }
        if (key.startsWith("note:"))
            return { op: "sheet.set_note", params: { id: ref["sheet"], note: key.slice(5), key: "text", value } };
        return { op: "sheet.set", params: { id: ref["sheet"], key, value } };
    }
    if (p.kind === "view" && ["cut_reference", "cut_offset", "range_top", "range_bottom", "range_depth"].includes(key))
        return { op: "view.set_cut_plane", params: { id: ref["view"], key, value } };
    if (p.kind === "view" && key === "name")
        return { op: "view.rename", params: { id: ref["view"], name: value } };
    if (p.kind === "view" && (key === "depth" || key === "angle"))
        return { op: "view.set_section", params: { id: ref["view"], [key]: value } };
    if (p.kind === "view" && (key === "opening_direction" || key === "material_hatching"))
        return { op: "view.set_option", params: { id: ref["view"], key, value } };
    if (p.kind === "view" && key.startsWith("hatch:"))
        return { op: "hatch.set", params: { material: key.slice(6), pattern: value } };
    if (p.kind === "view" && key === "looking")
        return value === "flip" ? { op: "view.flip_section", params: { id: ref["view"] } } : null;
    if (p.kind === "entity" && key === "tag_field")
        return { op: "draw.tag_field", params: { drawing: ref["drawing"], id: ref["entity"], field: value } };
    if (p.kind === "entity" && key === "layer")
        return { op: "draw.set_layer", params: { drawing: ref["drawing"], ids: [ref["entity"]], layer: value } };
    return null;
}
async function commit(store, p, key, raw, type) {
    let value = raw;
    if (type === "number") {
        value = Number(raw);
        if (raw.trim() === "" || !Number.isFinite(value)) {
            toast("Enter a number.", { kind: "error" });
            await store.refresh();
            return;
        }
    }
    const edit = editCommand(p, key, value);
    if (!edit) {
        toast(`${key} cannot be edited here.`, { kind: "info" });
        await store.refresh();
        return;
    }
    const done = await store.run(edit.op, edit.params);
    if (!done)
        await store.refresh(); // a refused edit puts the field back to what the project really holds
}
