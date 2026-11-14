/** The BIM connection check: rainwater, roof edges and drip edges, curtain-wall corners and hosts. The server analyses the model (its own geometry, at
 *  the current revision) and attaches a correction to findings that have one; corrections are only ever *proposed* here, as one assistant proposal that
 *  is dry-run first and applied only when approved. This dialog draws what the server returns and invents nothing. */
import { clear, h } from "../../../tools/web/src/dom.js";
import { openDialog, toast } from "../../../tools/web/src/ui.js";
export const DOMAIN_LABEL = { rainwater: "Rainwater", roof: "Roof edges", curtainwall: "Curtain walls" };
/** Group findings for display: errors first, then warnings, then notes, each in the server's order. */
export function bySeverity(findings) {
    const order = [["error", "Errors"], ["warning", "Warnings"], ["info", "Notes"]];
    return order.map(([key, label]) => [label, findings.filter((f) => f.severity === key)]).filter(([, list]) => list.length > 0);
}
export async function connectionsDialog(store) {
    if (!store.project)
        return;
    const picked = store.selection?.kind === "model" ? store.selection.ids : [];
    const scope = h("select", { attrs: { "aria-label": "What to check" } }, ...(picked.length ? [h("option", { value: "selection", text: `The selection (${picked.length})` })] : []), h("option", { value: "model", text: "The whole model" }));
    let sel = picked;
    const body = h("div", { css: { display: "grid", gap: "0.8rem" } });
    const status = h("p", { class: "muted", attrs: { role: "status", "aria-live": "polite" } });
    const chosen = new Set();
    let result = null;
    const load = async () => {
        sel = scope.value === "selection" ? picked : [];
        chosen.clear();
        try {
            result = await store.api.post("/connections", sel.length ? { ids: sel } : {});
        }
        catch (e) {
            status.textContent = e instanceof Error ? e.message : "Could not check the connections.";
            return;
        }
        paint();
    };
    const paint = () => {
        clear(body);
        const r = result;
        if (!r)
            return;
        const checked = Object.entries(r.checked).map(([d, n]) => `${n} ${DOMAIN_LABEL[d]?.toLowerCase() ?? d}`).join(", ");
        status.textContent = `${r.findings.length ? `${r.findings.length} finding${r.findings.length === 1 ? "" : "s"}` : "No connection problems"} in ${sel.length ? "the selection" : "the model"} at revision ${r.revision} (${checked} checked). `
            + `Proximity candidates: ${r.candidates.source}${r.candidates.agent_calls ? ` (${r.candidates.agent_calls} call${r.candidates.agent_calls === 1 ? "" : "s"})` : ""}.`;
        for (const [label, list] of bySeverity(r.findings)) {
            const rows = h("ul", { css: { display: "grid", gap: "0.4rem", margin: "0", paddingLeft: "0", listStyle: "none" } });
            for (const f of list) {
                const box = h("input", { type: "checkbox", disabled: !f.fix, attrs: { "aria-label": f.fix ? `Include: ${f.fix.title}` : "No correction to propose" } });
                box.checked = chosen.has(f.id);
                box.addEventListener("change", () => { if (box.checked)
                    chosen.add(f.id);
                else
                    chosen.delete(f.id); });
                const show = h("button", { class: "tool-inline", type: "button", text: "Select", on: { click: () => { void store.select(f.part ? { kind: "model", ids: [f.ref], part: f.part } : { kind: "model", ids: [f.ref] }); } } });
                rows.append(h("li", { css: { display: "grid", gridTemplateColumns: "auto 1fr auto", gap: "0.5rem", alignItems: "start" } }, box, h("div", {}, h("div", { text: f.message }), h("div", { class: "muted", text: `${DOMAIN_LABEL[f.domain] ?? f.domain}${f.fix ? ` · correction: ${f.fix.title}` : ""}` })), show));
            }
            body.append(h("section", {}, h("h3", { text: label, css: { margin: "0 0 0.3rem" } }), rows));
        }
        const health = h("details", {}, h("summary", { text: "Agent calls and latency" }));
        health.addEventListener("toggle", () => { if (health.open && health.childElementCount === 1)
            void agentHealth(store, health); }, { once: false });
        body.append(h("p", { class: "muted", text: r.basis }), health);
    };
    const handle = openDialog({
        title: "Connection check", size: "wide", cancelLabel: "Close",
        body: h("div", { css: { display: "grid", gap: "0.6rem" } }, scope, status, body),
        actions: [{ label: "Propose selected corrections", kind: "primary", run: async () => {
                    if (!result || !chosen.size) {
                        toast("Tick the findings whose corrections you want proposed.", { kind: "info" });
                        return undefined;
                    }
                    try {
                        await store.api.post("/connections/propose", { findings: [...chosen], revision: result.revision, ...(sel.length ? { ids: sel } : {}) });
                    }
                    catch (e) {
                        toast(e instanceof Error ? e.message : "Could not propose the corrections.", { kind: "error" });
                        return undefined;
                    }
                    store.openAssistant?.();
                    toast("The corrections are proposed in the assistant. Nothing has changed: review and approve them there.", { kind: "ok", timeoutMs: 8000 });
                    return true;
                } }],
    });
    scope.addEventListener("change", () => { void load(); });
    await load();
    await handle.result;
}
async function agentHealth(store, into) {
    try {
        const hl = await store.api.get("/agents/health");
        const list = h("ul", {}, ...hl.operations.map((o) => h("li", { text: `${o.agent} · ${o.operation}: ${o.calls} call${o.calls === 1 ? "" : "s"}${o.failures ? `, ${o.failures} failed` : ""}${o.mean_ms !== undefined ? `, mean ${o.mean_ms.toFixed(1)} ms, p95 ${o.p95_ms?.toFixed(1)} ms` : ""}` })));
        into.append(list, h("p", { class: "muted", text: hl.observability.used ? `Latency also summarised by the ${hl.observability.agent}.` : `Observability Agent not used: ${hl.observability.reason ?? "unavailable"}.` }));
    }
    catch (e) {
        into.append(h("p", { class: "muted", text: e instanceof Error ? e.message : "Could not read the agent health." }));
    }
}
