/** The SLAI Assistant panel (master prompt 15). Every answer, proposal and result comes from the server; this only lays them out and carries the person's decisions. */
import { h, replace } from "../../../tools/web/src/dom.js";
import { ApiError } from "../../../tools/web/src/api.js";
import { glyph } from "../../../tools/web/src/icons.js";
import { toast } from "../../../tools/web/src/ui.js";
export const KIND_LABEL = {
    success: "Successful operation", proposal: "Pending proposal", explanation: "Read-only explanation", analysis: "Preliminary analysis",
    calculation: "Calculation from the model", unsupported: "Unsupported", failed: "Failed", cancelled: "Cancelled",
};
export const STATUS_LABEL = { pending: "Waiting for your decision", executed: "Approved and applied", rejected: "Rejected", cancelled: "Cancelled", stale: "Out of date", failed: "Failed, nothing changed" };
/** A cell of a before / after table: numbers are shown with sensible precision. */
export function cell(v, unit = "") {
    if (v === null || v === undefined)
        return "–";
    const text = typeof v === "number" ? String(Number(v.toPrecision(6))) : String(v);
    return unit ? `${text} ${unit}` : text;
}
export class AssistantPanel {
    store;
    el = h("aside", { class: "assistant-panel", hidden: true, attrs: { role: "complementary", "aria-label": "SLAI Assistant" } });
    list = h("div", { class: "ap-list", attrs: { "aria-live": "polite" } });
    input = h("input", { type: "text", id: "assistant-input", class: "ap-input", placeholder: "Ask about the model or describe a change", attrs: { maxlength: 600, autocomplete: "off", "aria-label": "Request for the SLAI Assistant" } });
    chips = h("div", { class: "ap-chips" });
    messages = [];
    capabilities = [];
    busy = false;
    drawnRevision = -1;
    constructor(store) {
        this.store = store;
        store.changed.on(() => { const r = store.project?.revision ?? -1; if (this.isOpen && r !== this.drawnRevision)
            this.draw(); }); // an Undo button disappears once the project has moved on
        const send = h("button", { type: "button", class: "ap-send", attrs: { "aria-label": "Send" }, on: { click: () => { void this.ask(); } } }, glyph("arrow-right"));
        this.input.addEventListener("keydown", (e) => { if (e.key === "Enter") {
            e.preventDefault();
            void this.ask();
        } });
        this.el.append(h("div", { class: "ap-head" }, h("div", { class: "title" }, glyph("check-circle"), "SLAI Assistant"), h("button", { class: "iconbtn", type: "button", attrs: { "aria-label": "Close the assistant" }, on: { click: () => this.close() } }, glyph("close"))), h("p", { class: "ap-note", text: "A pattern interpreter, not a language model: it acts on a fixed set of request forms and says so when it cannot. Changes wait for your approval." }), this.list, this.chips, h("div", { class: "ap-form" }, this.input, send));
    }
    get isOpen() { return !this.el.hidden; }
    async toggle() { if (this.isOpen)
        this.close();
    else
        await this.open(); }
    close() { this.el.hidden = true; }
    async open() { this.el.hidden = false; await this.load(); this.input.focus(); }
    /** Only the context the request needs: what is selected, which view and level are active. */
    context() {
        const sel = this.store.selection;
        const view = this.store.drawing?.view;
        return { selection: sel?.kind === "model" ? sel.ids : [], view: this.store.tab?.ref.id ?? null, level: view?.["level_id"] ?? null };
    }
    async load() {
        if (!this.store.project) {
            this.messages = [];
            this.draw();
            return;
        }
        try {
            const h0 = await this.store.api.get("/assistant");
            this.messages = h0["messages"];
            this.capabilities = h0["capabilities"];
        }
        catch (error) {
            toast(error instanceof ApiError ? error.friendly : "The assistant could not be reached.", { kind: "error" });
        }
        this.draw();
    }
    async ask(text) {
        const request = (text ?? this.input.value).trim();
        if (!request || this.busy || !this.store.project)
            return;
        this.busy = true;
        this.input.value = "";
        try {
            await this.store.api.post("/assistant/ask", { request, context: this.context() });
            await this.load();
        }
        catch (error) {
            toast(error instanceof ApiError ? error.friendly : "The assistant could not answer.", { kind: "error" });
        }
        finally {
            this.busy = false;
        }
    }
    async decide(path, p, confirm = false) {
        try {
            await this.store.api.post(`/assistant/${path}`, { proposal: p["id"], expected_revision: p["revision"], confirm_high_risk: confirm });
            if (path === "approve") {
                const gone = new Set(p["steps"].filter((st) => st["op"] === "model.delete").map((st) => String(st["params"]["id"])));
                const sel = this.store.selection;
                if (sel?.kind === "model" && sel.ids.some((id) => gone.has(id))) {
                    this.store.selection = null;
                    this.store.properties = null;
                } // a deleted element is no longer there to describe
                await this.store.refresh();
            }
        }
        catch (error) {
            if (!(error instanceof ApiError && error.status === 409))
                toast(error instanceof ApiError ? error.friendly : "That could not be done.", { kind: "error" }); // a stale or already-decided proposal is explained on its card
            if (path === "approve")
                await this.store.refresh();
        }
        await this.load();
    }
    proposalCard(m) {
        const p = m["proposal"];
        const live = p["status"] === "pending";
        const warn = h("p", { class: "ap-stale", attrs: { role: "alert" } });
        const confirm = h("input", { type: "checkbox", id: `confirm-${p["id"]}`, attrs: { "aria-label": "I understand this is a high-impact change" } });
        const rows = p["changes"] ?? [];
        const issues = p["validation"]?.["new_issues"] ?? [];
        return h("div", { class: `ap-proposal risk-${p["risk"]}`, data: { status: p["status"] } }, h("div", { class: "ap-ptitle", text: p["title"] }), h("div", { class: "ap-status" }, h("span", { class: "badge", text: STATUS_LABEL[p["status"]] ?? p["status"] }), h("span", { class: "badge risk", text: `${p["risk"]} risk` }), h("span", { class: "muted", text: `made for revision ${p["revision"]}` })), h("p", { class: "ap-expl", text: p["explanation"] }), rows.length ? h("table", { class: "schedule ap-changes", attrs: { "aria-label": "Proposed changes" } }, h("thead", {}, h("tr", {}, ["Element", "Parameter", "Now", "After"].map((t) => h("th", { text: t })))), h("tbody", {}, rows.slice(0, 40).map((r) => h("tr", {}, h("td", { text: r["name"] ?? "" }), h("td", { text: r["label"] }), h("td", { class: "num", text: cell(r["before"], r["unit"]) }), h("td", { class: "num", text: cell(r["after"], r["unit"]) }))))) : null, ...(p["assumptions"] ?? []).map((a) => h("p", { class: "ap-assume", text: `Assumed: ${a}` })), ...(p["skipped"] ?? []).map((a) => h("p", { class: "ap-assume", text: a })), h("p", { class: "ap-valid", text: issues.length ? `Would introduce ${issues.length} new issue(s): ${issues.map((i) => i["message"]).join("; ")}` : `Checked by running ${p["validation"]?.["steps"] ?? 0} step(s) on a scratch copy of the project: no new issues. ${p["validation"]?.["checks"] ?? ""}` }), p["status_note"] ? h("p", { class: "ap-stale", text: p["status_note"] }) : null, p["evidence_kind"] === "optimization" ? h("p", { class: "ap-valid", text: "The agent's evidence is in Analyze → Optimization; this proposal only applies its result." }) : null, warn, live && p["requires_confirmation"] ? h("label", { class: "ap-confirm", attrs: { for: `confirm-${p["id"]}` } }, confirm, " I understand this is a high-impact change") : null, h("div", { class: "ap-actions" }, live ? h("button", { type: "button", class: "review", text: "Approve", on: { click: () => { if (p["requires_confirmation"] && !confirm.checked) {
                    warn.textContent = "Tick the confirmation first.";
                    return;
                } void this.decide("approve", p, confirm.checked); } } }) : null, live ? h("button", { type: "button", class: "plain", text: "Reject", on: { click: () => { void this.decide("reject", p); } } }) : null, p["status"] === "stale" && m["request"] ? h("button", { type: "button", class: "plain", text: "Ask again", on: { click: () => { void this.ask(String(m["request"])); } } }) : null, p["status"] === "executed" && this.store.project?.revision === p["result"]?.["revision"] ? h("button", { type: "button", class: "plain", text: `Undo (${p["undo_steps"]})`, on: { click: () => { void this.undo(p); } } }) : null));
    }
    async undo(p) {
        for (let i = 0; i < Number(p["undo_steps"]); i += 1)
            await this.store.undo();
        await this.load();
    }
    draw() {
        this.drawnRevision = this.store.project?.revision ?? -1;
        const items = this.messages.map((m) => {
            const kind = m["kind"];
            const detail = m["proposal"] && kind !== "proposal" ? this.proposalSummary(m["proposal"]) : null;
            return h("div", { class: `ap-msg kind-${kind}`, data: { kind } }, m["request"] ? h("div", { class: "ap-you", text: m["request"] }) : null, h("div", { class: "ap-reply" }, h("span", { class: "badge", text: KIND_LABEL[kind] ?? kind }), kind === "proposal" ? null : h("p", { class: "ap-text", text: m["text"] }), kind === "proposal" ? this.proposalCard(m) : detail, (m["agents"] ?? []).length ? h("p", { class: "ap-agents", text: "Agent calls: " + m["agents"].map((c) => `${c["agent"]} · ${c["operation"]}`).join("; ") }) : null, h("p", { class: "ap-meta", text: `Revision ${m["revision"]}${(m["context"]?.["selection"] ?? []).length ? ` · ${m["context"]["selection"].length} selected` : ""}` })));
        });
        // a proposal that has been decided is shown once, in the message that asked for it; its result message only summarises
        replace(this.list, items.length ? items : h("p", { class: "muted", text: this.store.project ? "Ask a question or describe a change." : "Open a project first." }));
        replace(this.chips, this.capabilities.slice(0, 5).map((c) => h("button", { type: "button", class: "ap-chip", text: c["example"], attrs: { title: `${c["kind"]} · needs ${c["needs"]}` }, on: { click: () => { this.input.value = c["example"]; this.input.focus(); } } })));
        this.list.scrollTop = this.list.scrollHeight;
    }
    proposalSummary(p) {
        const checks = p["result"]?.["verification"] ?? [];
        if (!checks.length)
            return null;
        return h("ul", { class: "ap-verify" }, checks.map((c) => h("li", { class: c["ok"] ? "ok" : "bad", text: `${c["label"]}: wanted ${cell(c["wanted"])}, found ${cell(c["found"])} ${c["ok"] ? "✓" : "✗"}` })));
    }
}
