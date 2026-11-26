/** Regulations and History: the two windows behind Analyze → Regulations and Analyze → History.
 *
 *  Nothing is decided here. The rules, the statuses, the corrections and the history all come from the server (``regulations/`` and ``provenance_bridge.py``); this
 *  only shows them, lets a person pick the settings, select an element in the 2D or 3D view, preview a correction and press Apply. Apply is a separate, explicit press:
 *  asking for a correction changes nothing. */
import { ApiError } from "../../../tools/web/src/api.js";
import { h } from "../../../tools/web/src/dom.js";
import { openDialog, saveBlob, toast } from "../../../tools/web/src/ui.js";
/** Worst first: the order findings are listed in and the order the summary badges appear in. */
export const STATUS_ORDER = ["non_compliant", "insufficient_data", "approaching_limit", "unsupported", "not_applicable", "compliant"];
export const STATUS_TEXT = {
    non_compliant: "Does not comply", insufficient_data: "Not enough data", approaching_limit: "Close to the limit", unsupported: "Not covered",
    not_applicable: "Does not apply", compliant: "Complies",
};
const REPORT_STATE_TEXT = {
    disabled: "Regulation assistance is off for this project.", unconfigured: "Choose a country or region to start.", unsupported: "This country or region cannot be checked yet.",
    dataset_problem: "The rule data on this computer failed its own integrity check, so nothing is checked.",
};
export const mm = (v, unit) => v === null || v === undefined ? "–" : unit === "m" ? `${(v * 1000).toFixed(0)} mm` : `${Number(v.toPrecision(5))} ${unit}`.trim();
export const percent = (fraction) => `${Number((fraction * 100).toFixed(2))}%`;
/** The findings the filter lets through, worst status first and then by element name. Pure, so it can be tested without a screen. */
export function visibleFindings(findings, filter) {
    const rank = (s) => { const i = STATUS_ORDER.indexOf(s); return i < 0 ? STATUS_ORDER.length : i; };
    return findings.filter((f) => filter === "all" || (filter === "attention" ? ["non_compliant", "insufficient_data", "approaching_limit"].includes(f.status) : f.status === filter))
        .sort((a, b) => rank(a.status) - rank(b.status) || a.element_name.localeCompare(b.element_name) || a.id.localeCompare(b.id));
}
/** The settings patch the form sends; the margin is typed as a percentage and stored as a fraction. */
export function settingsPatch(form, expected) {
    const margin = Number(form.marginPercent);
    if (!Number.isFinite(margin) || margin < 0 || margin > 50)
        throw new Error("The warning margin is a percentage between 0 and 50.");
    return { expected_revision: expected, enabled: form.enabled, jurisdiction: form.jurisdiction || null, project_status: form.status || null, building_function: form.fn || null,
        reference_date: form.date || null, warning_margin: margin / 100, accept_unverified: form.acceptUnverified };
}
/** What a history record means in words; a kind this table does not know is shown as it is rather than hidden. */
export const EVENT_TEXT = {
    "spadra.command": "Change", "spadra.command.rejected": "Change refused", "spadra.undo": "Undo", "spadra.redo": "Redo",
    "spadra.proposal.made": "Correction proposed", "spadra.proposal.approved": "Correction approved", "spadra.proposal.executed": "Correction applied",
    "spadra.proposal.rejected": "Correction rejected", "spadra.proposal.failed": "Correction failed and was undone", "spadra.proposal.cancelled": "Correction cancelled",
    "spadra.project.new": "Project created", "spadra.project.open": "Project opened", "spadra.project.save": "Project saved", "spadra.project.copy": "Project saved as a copy",
    "regulation.checked": "Regulation check",
};
export const eventText = (kind) => EVENT_TEXT[kind] ?? kind;
/** The one line of detail a record carries: what the change was, or what the correction was for. */
export function eventDetail(e) {
    const p = e["payload"] ?? {};
    const text = p["label"] ?? p["title"] ?? p["op"] ?? "";
    return typeof text === "string" ? text.slice(0, 160) : "";
}
const say = (error, fallback) => error instanceof ApiError ? error.friendly : error instanceof Error ? error.message : fallback;
const badge = (status) => h("span", { class: `badge reg-${status}`, text: STATUS_TEXT[status] ?? status });
function field(label, control) { return h("label", { class: "reg-field" }, h("span", { text: label }), control); }
function select(options, value) {
    const el = h("select", {}, ...options.map((o) => h("option", { value: o.value, text: o.label, ...(o.disabled ? { disabled: true } : {}) })));
    el.value = value;
    return el;
}
/** Analyze → Regulations. */
export async function regulationDialog(store) {
    if (!store.project)
        return;
    let state;
    try {
        state = await store.api.get("/regulation");
    }
    catch (error) {
        toast(say(error, "The regulation check could not be read."), { kind: "error" });
        return;
    }
    let filter = "attention";
    let openFinding = null;
    let proposal = null; // the correction being previewed: nothing is changed until Apply
    let busy = false;
    const body = h("div", { class: "reg" });
    const live = h("p", { class: "muted", attrs: { role: "status", "aria-live": "polite" } });
    const dialog = openDialog({ title: "Regulations", eyebrow: "Screening against the building rules, with the source of every limit", size: "wide", cancelLabel: "Close", body: [body, live] });
    void dialog;
    const adopt = (next) => { state = next; };
    const guarded = async (what, run) => {
        if (busy)
            return;
        busy = true;
        live.textContent = `${what}…`;
        try {
            await run();
            live.textContent = "";
        }
        catch (error) {
            live.textContent = "";
            toast(say(error, `${what} did not work.`), { kind: "error" });
            if (error instanceof ApiError && error.status === 409) {
                try {
                    adopt(await store.api.get("/regulation"));
                    proposal = null;
                }
                catch { /* the toast already explains */ }
            }
        }
        finally {
            busy = false;
            draw();
        }
    };
    const settings = () => {
        const c = state.config, o = state.options;
        const enabled = h("input", { type: "checkbox", checked: c.enabled, attrs: { "aria-label": "Check this project against the building rules" } });
        const jur = select([{ value: "", label: "Choose…" }, ...o.jurisdictions.map((j) => ({ value: j.id, label: `${j.label}${j.available ? "" : " (not available yet)"}` }))], c.jurisdiction ?? "");
        const st = select([{ value: "", label: "Not stated" }, ...o.project_statuses.map((s) => ({ value: s.id, label: s.label }))], c.project_status ?? "");
        const fn = select([{ value: "", label: o.functions.length ? "Not stated" : "Choose a country and save, then pick one" }, ...o.functions.map((f) => ({ value: f.id, label: f.label }))], c.building_function ?? "");
        const date = h("input", { type: "date", value: c.reference_date ?? "" });
        const margin = h("input", { type: "number", value: String(Number((c.warning_margin * 100).toFixed(2))), attrs: { min: "0", max: "50", step: "0.5" } });
        const unverified = h("input", { type: "checkbox", checked: c.accept_unverified });
        const j = o.jurisdictions.find((x) => x.id === c.jurisdiction);
        const ds = state.report.dataset;
        return h("section", {}, h("h3", { text: "Settings" }), h("label", { class: "reg-field reg-check" }, enabled, h("span", { text: " Check this project against the building rules" })), h("div", { class: "reg-grid" }, field("Country or region", jur), field("Project status", st), field("Building function", fn), field("Rules in force on", date), field("Warning margin (%)", margin)), j && !j.available && j.blocked_reason ? h("p", { class: "ap-stale", text: j.blocked_reason }) : null, ds ? h("p", { class: "muted", text: `Rule data: ${String(ds["title"] ?? ds["id"])} (${String(ds["version"])}). ${ds["verification"] === "verified_official" ? "Checked against the official text." : "Not yet checked against the official text: treat every result as a screening hint."}` }) : null, h("label", { class: "reg-field reg-check" }, unverified, h("span", { text: " Offer corrections even when the rule data is not yet checked against the official text" })), h("p", { class: "muted", text: "The warning margin only marks values that are close to a limit. The limit itself always comes from the rule data." }), h("div", { class: "ap-actions" }, h("button", { type: "button", class: "review", text: "Save settings", on: { click: () => {
                    void guarded("Saving the settings", async () => {
                        const patch = settingsPatch({ enabled: enabled.checked, jurisdiction: jur.value, status: st.value, fn: fn.value, date: date.value, marginPercent: margin.value, acceptUnverified: unverified.checked }, state.revision);
                        const next = await store.api.post("/regulation/configure", patch);
                        adopt(next);
                        proposal = null;
                        openFinding = null;
                        void store.refresh(); // the window updates now; the views catch up behind it
                    });
                } } })));
    };
    const summary = () => {
        const r = state.report;
        if (r.state !== "ok")
            return h("section", {}, h("h3", { text: "Result" }), h("p", { class: "muted", text: `${REPORT_STATE_TEXT[r.state] ?? r.state}${r.reason && r.state !== "disabled" ? ` ${r.reason}` : ""}` }));
        const total = r.findings.length;
        const choose = select([{ value: "attention", label: "Needs attention" }, { value: "all", label: `Everything (${total})` }, ...STATUS_ORDER.map((s) => ({ value: s, label: `${STATUS_TEXT[s]} (${r.summary[s] ?? 0})` }))], filter);
        choose.addEventListener("change", () => { filter = choose.value; draw(); });
        const rows = visibleFindings(r.findings, filter);
        return h("section", {}, h("h3", { text: "Result" }), h("div", { class: "ap-status" }, ...STATUS_ORDER.filter((s) => (r.summary[s] ?? 0) > 0).map((s) => h("span", { class: `badge reg-${s}`, text: `${STATUS_TEXT[s]}: ${r.summary[s]}` })), h("span", { class: "muted", text: `${total} check${total === 1 ? "" : "s"} at revision ${r.revision}, computed in ${r.computed_ms} ms` })), h("div", { class: "ap-actions" }, field("Show", choose), h("button", { type: "button", class: "plain", text: "Check again", on: { click: () => { void guarded("Checking", async () => { adopt(await store.api.post("/regulation/check", {})); }); } } }), h("button", { type: "button", class: "plain", text: "Export CSV", on: { click: () => { void exportReport("csv"); } } }), h("button", { type: "button", class: "plain", text: "Export JSON", on: { click: () => { void exportReport("json"); } } })), rows.length ? h("table", { class: "schedule", attrs: { "aria-label": "Findings" } }, h("thead", {}, h("tr", {}, ["Status", "Element", "Level", "Now", "Limit", "Rule", ""].map((t) => h("th", { text: t })))), h("tbody", {}, rows.slice(0, 300).map((f) => h("tr", { class: f.id === openFinding ? "current" : "", data: { finding: f.id } }, h("td", {}, badge(f.status)), h("td", { text: f.element_name }), h("td", { text: f.level_name ?? "" }), h("td", { class: "num", text: mm(f.value, f.unit) }), h("td", { class: "num", text: mm(f.limit, f.unit) }), h("td", { text: f.citation }), h("td", {}, h("button", { type: "button", class: "plain", text: "Show", on: { click: () => { void show(f); } } })))))) : h("p", { class: "muted", text: total ? "Nothing in this group." : "Nothing to check in this project yet." }), rows.length > 300 ? h("p", { class: "muted", text: `Showing the first 300 of ${rows.length}. Export the report to see all of them.` }) : null);
    };
    const exportReport = async (format) => {
        try {
            const file = await store.api.download("/regulation/export", { format });
            saveBlob(file.blob, file.filename);
        }
        catch (error) {
            toast(say(error, "The report could not be exported."), { kind: "error" });
        }
    };
    /** Select the element in the 2D and 3D views (the same selection every other panel uses) and open its explanation. */
    const show = async (f) => {
        openFinding = f.id;
        proposal = null;
        await store.select({ kind: "model", ids: [f.element] });
        draw();
    };
    const detail = () => {
        const f = state.report.findings.find((x) => x.id === openFinding);
        if (!f)
            return null;
        const correctable = f.correctable && f.status !== "compliant";
        return h("section", { class: "reg-detail" }, h("h3", { text: `${f.element_name}: ${f.title}` }), h("div", { class: "ap-status" }, badge(f.status), h("span", { class: "badge", text: f.evidence_grade === "official" ? "checked against the official text" : "rule data not yet checked against the official text" })), h("p", { text: f.explanation }), h("p", { class: "muted", text: `${f.citation}. Rule data ${f.dataset.id} ${f.dataset.version}.` }), f.needs.length ? h("p", { class: "ap-stale", text: `Needed to decide this: ${f.needs.join(", ")}.` }) : null, ...f.assumptions.map((a) => h("p", { class: "ap-assume", text: `Assumed: ${a}` })), f.correction_note ? h("p", { class: "muted", text: f.correction_note }) : null, proposal ? proposalCard() : h("div", { class: "ap-actions" }, h("button", { type: "button", class: "plain", text: "Select in the model", on: { click: () => { void store.select({ kind: "model", ids: [f.element] }); } } }), correctable ? h("button", { type: "button", class: "review", text: "Preview a correction", on: { click: () => {
                    void guarded("Preparing the correction", async () => {
                        const out = await store.api.post("/regulation/correct", { finding: f.id, expected_revision: state.revision });
                        proposal = out["proposal"] ?? null;
                    });
                } } }) : null));
    };
    const proposalCard = () => {
        const p = proposal;
        const rows = p["changes"] ?? [];
        const issues = p["validation"]?.["new_issues"] ?? [];
        const confirm = h("input", { type: "checkbox", id: "reg-confirm" });
        const warn = h("p", { class: "ap-stale", attrs: { role: "alert" } });
        return h("div", { class: `ap-proposal risk-${p["risk"]}` }, h("div", { class: "ap-ptitle", text: p["title"] }), h("p", { class: "ap-expl", text: p["explanation"] }), rows.length ? h("table", { class: "schedule ap-changes", attrs: { "aria-label": "Proposed changes" } }, h("thead", {}, h("tr", {}, ["Element", "Parameter", "Now", "After"].map((t) => h("th", { text: t })))), h("tbody", {}, rows.slice(0, 20).map((r) => h("tr", {}, h("td", { text: r["name"] ?? "" }), h("td", { text: r["label"] }), h("td", { class: "num", text: r["before"] === null || r["before"] === undefined ? "–" : `${r["before"]} ${r["unit"] ?? ""}`.trim() }), h("td", { class: "num", text: r["after"] === null || r["after"] === undefined ? "–" : `${r["after"]} ${r["unit"] ?? ""}`.trim() }))))) : null, h("p", { class: "ap-valid", text: issues.length ? `Would introduce ${issues.length} new issue(s): ${issues.map((i) => i["message"]).join("; ")}` : `Tried on a copy of the project first: no new issues. Nothing has been changed yet.` }), p["requires_confirmation"] ? h("label", { class: "ap-confirm", attrs: { for: "reg-confirm" } }, confirm, " I understand this is a high-impact change") : null, warn, h("div", { class: "ap-actions" }, h("button", { type: "button", class: "review", text: "Apply", on: { click: () => {
                    if (p["requires_confirmation"] && !confirm.checked) {
                        warn.textContent = "Tick the confirmation first.";
                        return;
                    }
                    void guarded("Applying the correction", async () => {
                        const out = await store.api.post("/regulation/apply", { proposal: p["id"], expected_revision: p["revision"], confirm_high_risk: confirm.checked });
                        adopt(out);
                        proposal = null;
                        void store.refresh();
                        toast(out.message ?? "The correction was applied. Undo is in the Edit controls.", { kind: "ok" });
                    });
                } } }), h("button", { type: "button", class: "plain", text: "Cancel", on: { click: () => {
                    const id = p["id"];
                    proposal = null;
                    draw();
                    void store.api.post("/assistant/reject", { proposal: id, expected_revision: p["revision"] }).catch(() => undefined); // the proposal was never run; rejecting only tidies the assistant's list
                } } })));
    };
    function draw() { body.replaceChildren(settings(), summary(), ...[detail()].filter((x) => !!x)); }
    draw();
}
/** Analyze → History: what happened to the project, who or what did it, and whether the record still verifies. */
export async function historyDialog(store, element) {
    if (!store.project)
        return;
    let status, events;
    const load = async () => {
        status = await store.api.get("/provenance/status");
        events = status["state"] === "unavailable" || status["state"] === "idle" ? { events: [] } : await store.api.get("/provenance/events", { element, limit: 200 });
    };
    try {
        await load();
    }
    catch (error) {
        toast(say(error, "The history could not be read."), { kind: "error" });
        return;
    }
    const body = h("div", { class: "reg" });
    let verdict = null;
    let kindFilter = "";
    const actor = (a) => a ? `${a["type"] === "human" ? "You" : a["type"] === "ai" ? `AI assistant${a["id"] ? ` (${a["id"]})` : ""}` : "SPADRA"}` : "";
    const when = (ts) => { const d = new Date(typeof ts === "number" ? ts * 1000 : String(ts)); return Number.isNaN(d.getTime()) ? String(ts ?? "") : d.toLocaleString(); };
    const draw = () => {
        const state = String(status["state"]);
        const rows = (events["events"] ?? []).filter((e) => !kindFilter || String(e["kind"]).startsWith(kindFilter)).slice().reverse();
        const kinds = [...new Set((events["events"] ?? []).map((e) => String(e["kind"]).split(".")[0] ?? ""))].sort();
        const filter = select([{ value: "", label: "All kinds of events" }, ...kinds.map((k) => ({ value: k, label: k === "spadra" ? "Changes and corrections" : k === "regulation" ? "Regulation checks" : k }))], kindFilter);
        filter.addEventListener("change", () => { kindFilter = filter.value; draw(); });
        body.replaceChildren(h("section", {}, h("h3", { text: element ? "History of the selected element" : "History of this project" }), h("div", { class: "ap-status" }, h("span", { class: `badge ${state === "ok" ? "" : "risk"}`, text: state === "ok" ? "History recorded" : state === "degraded" ? "History is catching up" : state === "unavailable" ? "History is not available" : state }), status["records"] !== undefined ? h("span", { class: "muted", text: `${String(status["records"])} records` }) : null, status["pending"] ? h("span", { class: "badge risk", text: `${String(status["pending"])} waiting to be written` }) : null), state === "degraded" ? h("p", { class: "ap-stale", text: `Some records are waiting to be written and will be retried. ${status["last_error"] ? String(status["last_error"]) : ""} Changes that need the history (applying a correction) are refused until it has caught up.` }) : null, state === "unavailable" ? h("p", { class: "ap-stale", text: `${String(status["reason"] ?? "")} ${String(status["hint"] ?? "")}` }) : null, status["note"] ? h("p", { class: "muted", text: String(status["note"]) }) : null, h("div", { class: "ap-actions" }, field("Show", filter), h("button", { type: "button", class: "plain", text: "Verify the history", on: { click: () => {
                    void (async () => {
                        try {
                            verdict = await store.api.post("/provenance/verify", {});
                        }
                        catch (error) {
                            toast(say(error, "Verification did not run."), { kind: "error" });
                        }
                        draw();
                    })();
                } } }), h("button", { type: "button", class: "plain", text: "Export history (JSON)", on: { click: () => {
                    void (async () => {
                        try {
                            const out = await store.api.post("/provenance/export", { redact: [] });
                            saveBlob(new Blob([JSON.stringify(out, null, 2)], { type: "application/json" }), `history-${String(store.project?.name ?? "project")}.json`);
                        }
                        catch (error) {
                            toast(say(error, "The history could not be exported."), { kind: "error" });
                        }
                    })();
                } } }), h("button", { type: "button", class: "plain", text: "Refresh", on: { click: () => { void (async () => { try {
                    await load();
                }
                catch (error) {
                    toast(say(error, "Could not refresh."), { kind: "error" });
                } draw(); })(); } } })), verdict ? h("p", { class: verdict["ok"] ? "ap-valid" : "ap-stale", attrs: { role: "status" }, text: verdict["ok"]
                ? `The records are consistent and unchanged since they were written (${String(verdict["count"] ?? verdict["records"] ?? "all")} checked). This does not show who wrote them.`
                : `The history did not verify: ${JSON.stringify(verdict["problems"] ?? verdict["detail"] ?? verdict).slice(0, 300)}` }) : null), h("section", {}, rows.length ? h("table", { class: "schedule", attrs: { "aria-label": "History" } }, h("thead", {}, h("tr", {}, ["When", "What", "Detail", "By", "Revision"].map((t) => h("th", { text: t })))), h("tbody", {}, rows.slice(0, 200).map((e) => h("tr", {}, h("td", { text: when(e["ts"]) }), h("td", { text: eventText(String(e["kind"])) }), h("td", { text: eventDetail(e) }), h("td", { text: actor(e["actor"]) }), h("td", { class: "num", text: String(e["revision"]?.["at"] ?? e["revision"]?.["after"] ?? "") }))))) : h("p", { class: "muted", text: "No events recorded yet." })));
    };
    openDialog({ title: "History", eyebrow: "What changed, who or what changed it, and whether the record still checks out", size: "wide", cancelLabel: "Close", body });
    draw();
}
