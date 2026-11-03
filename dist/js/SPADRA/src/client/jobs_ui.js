/** Background operations and the diagnostics log, as the person sees them: a chip in the status bar while something runs, a list with progress and cancel,
 *  and the recent failures the server recorded. The server owns every state; nothing here is invented. */
import { h } from "../../../tools/web/src/dom.js";
import { copyText, openDialog, toast } from "../../../tools/web/src/ui.js";
const STATUS_TEXT = { queued: "Waiting", running: "Running", done: "Finished", failed: "Failed", cancelled: "Cancelled" };
export function progressText(job) {
    return job.status === "running" || job.status === "queued" ? `${job.label}: ${job.message} (${Math.round(job.progress * 100)}%)` : `${job.label}: ${STATUS_TEXT[job.status].toLowerCase()}`;
}
/** The status-bar chip for running operations (null when none run). Clicking it opens the list. */
export function jobChip(store) {
    const active = store.activeJobs;
    const first = active[0];
    if (!first)
        return null;
    const text = active.length > 1 ? `${active.length} operations running` : progressText(first);
    return h("button", { class: "job-chip", type: "button", attrs: { title: "Show running operations", "aria-label": text }, on: { click: () => jobsDialog(store) } }, h("progress", { attrs: { max: "1", value: String(first.progress), "aria-label": first.label } }), h("span", { text }));
}
function jobRow(store, job) {
    const active = job.status === "queued" || job.status === "running";
    const meta = job.finished && job.started ? `${((job.finished - job.started)).toFixed(1)} s` : active ? `${Math.round(job.progress * 100)}%` : "";
    return h("li", { data: { status: job.status }, attrs: { "aria-label": `${job.label}: ${STATUS_TEXT[job.status]}` } }, h("div", { css: { display: "flex", justifyContent: "space-between", gap: ".5rem" } }, h("strong", { text: job.label }), h("span", { class: "muted", text: `${STATUS_TEXT[job.status]}${meta ? ` · ${meta}` : ""}` })), active ? h("progress", { attrs: { max: "1", value: String(job.progress), "aria-label": `Progress of ${job.label}` } }) : null, h("div", { class: "job-message", attrs: { role: job.status === "failed" ? "alert" : "status" }, text: job.error ? `${job.error.category}: ${job.error.message}` : job.message }), job.error ? h("div", { class: "muted", text: job.error.recovery }) : null, h("div", { class: "prop-add" }, active ? h("button", { class: "tool-inline", type: "button", disabled: !job.cancellable, text: job.cancellable ? "Cancel" : "Being applied", attrs: { title: job.cancellable ? "Stop it; nothing is changed" : "The change is being applied and can no longer be cancelled. Use Undo to take it back." },
        on: { click: () => { void store.cancelJob(job.id); } } }) : h("button", { class: "tool-inline", type: "button", text: "Dismiss", on: { click: () => { void store.dismissJob(job.id); } } })));
}
export function jobsDialog(store) {
    const list = h("ul", { class: "job-list", attrs: { "aria-label": "Operations" } });
    const draw = () => {
        list.replaceChildren(...(store.jobs.length ? store.jobs.map((j) => jobRow(store, j)) : [h("li", { class: "muted", text: "Nothing has run yet. Imports, exports, reports, checks and simulations appear here while they run." })]));
    };
    draw();
    void store.loadJobs(); // what the server holds now, which may be more than this page started
    const handle = openDialog({ title: "Operations", size: "wide", noCancel: true, body: list, actions: [{ label: "Close", kind: "primary", run: () => true }] });
    const off = store.jobsChanged.on(draw);
    void handle.result.then(off);
}
/** What the server recorded when something failed: when, where, what kind of problem, and the message. Never the content of a project or a file. */
export async function diagnosticsDialog(store) {
    const data = await store.guard(() => store.api.get("/diagnostics", { limit: 100 }), "Reading the diagnostics");
    if (!data)
        return;
    const entries = data.entries;
    const text = entries.map((e) => `${e.at}  ${e.source}  ${e.category}  ${e.code}  ${e.message}${e.project ? `  [${e.project} r${e.revision ?? "?"}]` : ""}`).join("\n");
    openDialog({
        title: "Diagnostics", eyebrow: "Recent failures", size: "wide", noCancel: true,
        body: entries.length
            ? h("table", { class: "schedule", attrs: { "aria-label": "Recent failures" } }, h("thead", {}, h("tr", {}, ["When", "Where", "Kind", "What happened"].map((c) => h("th", { text: c })))), h("tbody", {}, entries.map((e) => h("tr", {}, h("td", { text: e.at.replace("T", " ").slice(0, 19) }), h("td", { text: e.source }), h("td", { text: e.category }), h("td", { text: e.message })))))
            : h("p", { text: "No failures have been recorded." }),
        actions: [
            { label: "Copy", run: async () => { toast((await copyText(text)) ? "Copied." : "The browser would not copy. Select the text instead.", { kind: "info", timeoutMs: 2500 }); return undefined; } },
            { label: "Close", kind: "primary", run: () => true }
        ],
    });
}
