/** Project properties and recovery from a backup (section 19). The facts come from the server; nothing here is estimated. */
import { ApiError } from "../../../tools/web/src/api.js";
import { h, labelled } from "../../../tools/web/src/dom.js";
import { openDialog, toast } from "../../../tools/web/src/ui.js";
import { formatBytes as kitBytes } from "../../../tools/web/src/util.js";
import { ensureClean } from "./dialogs.js";
/** 1536 → "1.5 KB" (the kit's formatter), with a dash where the size is not known. */
export function formatBytes(n) {
    return n === null || !Number.isFinite(n) || n < 0 ? "–" : kitBytes(n);
}
/** "2026-11-02T13:55:01Z" → "2026-11-02 13:55 UTC". Anything that is not an ISO time is shown as it is. */
export function formatStamp(iso) {
    if (!iso)
        return "–";
    const m = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/.exec(iso);
    return m ? `${m[1]} ${m[2]} UTC` : iso;
}
/** The elements counted by category, largest first: "2 walls"-style wording is avoided because categories are the model's own names. */
export function elementSummary(counts) {
    const rows = Object.entries(counts).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
    return rows.length ? rows.map(([k, n]) => `${k}: ${n}`).join(", ") : "none";
}
export function backupLabel(b) {
    return `${formatStamp(b.created)} · revision ${b.revision} · ${formatBytes(b.bytes)}`;
}
const friendly = (e, fallback) => (e instanceof ApiError ? e.friendly : fallback);
export async function propertiesDialog(store, onRecover) {
    let props;
    try {
        props = await store.api.get("/project/properties");
    }
    catch (e) {
        toast(friendly(e, "The project's properties could not be read."), { kind: "error" });
        return;
    }
    const name = h("input", { type: "text", id: "project-name", value: props.name, attrs: { maxlength: 80, autocomplete: "off" } });
    const description = h("textarea", { id: "project-description", attrs: { rows: 4, maxlength: 2000 } });
    description.value = props.description;
    const message = h("p", { class: "muted", attrs: { role: "alert" } });
    const row = (k, v) => h("tr", {}, h("th", { text: k, attrs: { scope: "row" } }), h("td", { text: v }));
    const facts = h("table", { class: "schedule", attrs: { "aria-label": "Project facts" } }, h("tbody", {}, row("Saved at", props.path ?? "Not saved yet"), row("Format", props.format), row("Drawing unit", `${props.unit} (the 3D model is always in ${props.model_unit})`), row("Created", formatStamp(props.created)), row("Last saved", formatStamp(props.modified)), row("Revision", String(props.revision)), row("Levels / views / sheets", `${props.counts.levels} / ${props.counts.views} / ${props.counts.sheets}`), row("Model elements", elementSummary(props.counts.elements)), row("Unsaved changes", props.dirty ? "Yes" : "No")));
    const files = Object.entries(props.files);
    const fileTable = files.length
        ? h("table", { class: "schedule", attrs: { "aria-label": "Project files" } }, h("thead", {}, h("tr", {}, ["File", "Size", "SHA-256 (as saved)"].map((c) => h("th", { text: c })))), h("tbody", {}, files.map(([rel, i]) => h("tr", {}, h("td", { text: rel }), h("td", { class: "num", text: formatBytes(i.bytes) }), h("td", { class: "hash", text: i.sha256 ? `${i.sha256.slice(0, 16)}…` : "–", attrs: { title: i.sha256 ?? "" } })))))
        : h("p", { class: "muted", text: "No files yet: the project is written when it is first saved." });
    const backups = props.backups.length
        ? h("p", { class: "muted", text: `${props.backups.length} backup${props.backups.length === 1 ? "" : "s"} (the newest ${props.backups_kept} are kept). The latest is from ${formatStamp(props.backups[0].created)}.` })
        : h("p", { class: "muted", text: props.path ? "No backups yet: one is made each time a saved project is saved again." : "Backups start once the project has been saved." });
    const recover = h("button", { type: "button", id: "project-open-recover", class: "tool-inline", text: "Recover from a backup…", disabled: props.backups.length === 0,
        on: { click: () => { handle.close(false); onRecover(); } } });
    const handle = openDialog({
        title: "Project properties", eyebrow: props.name, size: "wide", focus: () => name,
        body: h("div", { css: { display: "grid", gap: "1rem" } }, labelled("Project name", name), labelled("Description", description), message, facts, h("h3", { text: "Files" }), fileTable, h("h3", { text: "Backups" }), backups, recover),
        actions: [{
                label: "Apply", kind: "primary",
                run: async () => {
                    const next = name.value.trim();
                    if (!next) {
                        message.textContent = "A project needs a name.";
                        return undefined;
                    }
                    if (next === props.name && description.value === props.description)
                        return true; // nothing changed: no edit, no undo step
                    const params = {};
                    if (next !== props.name)
                        params["name"] = next;
                    if (description.value !== props.description)
                        params["description"] = description.value;
                    return (await store.run("project.set", params)) ? true : undefined;
                },
            }],
    });
    await handle.result;
}
export async function recoverDialog(store) {
    let props;
    try {
        props = await store.api.get("/project/properties");
    }
    catch (e) {
        toast(friendly(e, "The backups could not be listed."), { kind: "error" });
        return;
    }
    if (!props.path) {
        toast("This project has not been saved, so it has no backups.", { kind: "info" });
        return;
    }
    if (!props.backups.length) {
        toast("There are no backups yet. One is made each time a saved project is saved again.", { kind: "info" });
        return;
    }
    const state = await ensureClean(store);
    if (!state)
        return;
    let handle = null;
    const pick = (b) => { void store.recover(b.id, state === "discard").then((ok) => { if (ok) {
        handle?.close(true);
        toast("Opened the backup as a new, unsaved copy. Use Save As to keep it.", { kind: "ok", timeoutMs: 5000 });
    } }); };
    const list = h("ul", { class: "recent-list", attrs: { "aria-label": "Backups" } }, props.backups.map((b) => h("li", {}, h("button", { type: "button", data: { backup: b.id }, on: { click: () => pick(b) } }, h("strong", { text: backupLabel(b) }), h("span", { class: "path", text: b.name })))));
    handle = openDialog({
        title: "Recover from a backup", size: "wide", noCancel: false,
        body: h("div", { css: { display: "grid", gap: ".8rem" } }, h("p", { text: `Backups of “${props.name}” are copies of the previous version, made each time the project was saved again. Choosing one opens it as a new, unsaved copy; the saved project and the backup are not changed.` }), list),
        actions: [],
    });
    await handle.result;
}
