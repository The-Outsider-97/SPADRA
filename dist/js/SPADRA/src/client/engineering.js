/** Beam analysis dialog. The numbers come from the SLAI STEM Agent through the server; nothing is calculated here except unit display. */
import { h, labelled } from "../../../tools/web/src/dom.js";
import { ApiError } from "../../../tools/web/src/api.js";
import { openDialog } from "../../../tools/web/src/ui.js";
const SUPPORTS = [["simply_supported", "Simply supported"], ["cantilever", "Cantilever"], ["fixed_fixed", "Fixed at both ends"]];
const LOADS = [["udl", "Uniform load (kN/m)", "kN/m"], ["point_center", "Point load at mid-span (kN)", "kN"], ["point_end", "Point load at the free end (kN)", "kN"]];
/** Cases the engine supports; the server rejects the others, this only keeps the menus from offering them. */
const CASES = new Set(["simply_supported:udl", "simply_supported:point_center", "cantilever:udl", "cantilever:point_end", "fixed_fixed:udl"]);
const sig = (n, digits = 4) => (n === 0 ? "0" : String(Number(n.toPrecision(digits))));
/** The agent's SI results in the units engineers read them in. */
export function resultRows(r) {
    const o = r.outputs, c = r.details.base_engineering_check;
    const rows = [];
    if (o["max_moment_nm"])
        rows.push(["Maximum bending moment", `${sig(o["max_moment_nm"].value / 1000)} kN·m`]);
    if (o["max_shear_n"])
        rows.push(["Maximum shear force", `${sig(o["max_shear_n"].value / 1000)} kN`]);
    if (o["max_deflection_m"])
        rows.push(["Maximum deflection", `${sig(o["max_deflection_m"].value * 1000)} mm`]);
    if (o["deflection_limit_m"])
        rows.push(["Engine deflection limit", `${sig(o["deflection_limit_m"].value * 1000)} mm`]);
    if (c)
        rows.push(["Engine deflection check", `${sig(c.utilization * 100, 3)} % of the limit: ${c.passed ? "within" : "exceeds"}`]);
    return rows;
}
export function beamAnalysisDialog(store, beamId, beamName) {
    const support = h("select", { id: "beam-support" }, ...SUPPORTS.map(([v, l]) => h("option", { value: v, text: l })));
    const load = h("select", { id: "beam-loadtype" }, ...LOADS.map(([v, l]) => h("option", { value: v, text: l })));
    const magnitude = h("input", { id: "beam-load", type: "text", placeholder: "e.g. 10", attrs: { autocomplete: "off", maxlength: 16, inputmode: "decimal" } });
    const modulus = h("input", { id: "beam-modulus", type: "text", placeholder: "Engine value if it has one", attrs: { autocomplete: "off", maxlength: 12, inputmode: "decimal" } });
    const message = h("p", { class: "muted", attrs: { role: "alert" } });
    const results = h("div", { class: "beam-results", attrs: { "aria-live": "polite" } });
    const sync = () => {
        for (const opt of Array.from(load.options))
            opt.disabled = !CASES.has(`${support.value}:${opt.value}`);
        if (load.selectedOptions[0]?.disabled)
            load.value = Array.from(load.options).find((o) => !o.disabled)?.value ?? "udl";
    };
    support.addEventListener("change", sync);
    sync();
    const run = async () => {
        results.replaceChildren();
        message.textContent = "";
        const value = Number(magnitude.value.replace(",", "."));
        if (magnitude.value.trim() === "" || !Number.isFinite(value) || value <= 0) {
            message.textContent = "Enter the load as a number greater than zero.";
            return undefined;
        }
        const body = { id: beamId, support: support.value, load_type: load.value, load: value };
        if (modulus.value.trim() !== "") {
            const e = Number(modulus.value.replace(",", "."));
            if (!Number.isFinite(e) || e <= 0) {
                message.textContent = "The elastic modulus must be a number greater than zero (GPa).";
                return undefined;
            }
            body["youngs_modulus_gpa"] = e;
        }
        try {
            const r = await store.api.post("/engineering/beam", body);
            const i = r.inputs;
            results.append(h("h3", { text: "Result" }), h("table", { class: "schedule", attrs: { "aria-label": "Beam analysis result" } }, h("tbody", {}, resultRows(r).map(([k, v]) => h("tr", {}, h("th", { attrs: { scope: "row" }, text: k }), h("td", { class: "num", text: v }))))), h("h3", { text: "Used" }), h("p", { class: "muted", text: `Span ${sig(i.span_m)} m · ${i.profile} section ${sig(i.width_m * 1000)} × ${sig(i.depth_m * 1000)} mm (I = ${sig(i.second_moment_m4, 4)} m⁴, from ${i.section_source}) · ${i.material} · E = ${sig(i.youngs_modulus_gpa)} GPa, ${i.youngs_modulus_source}` }), h("p", { class: "muted", text: `Assumptions: ${r.assumptions.join("; ")}.` }), h("p", { class: "note", text: r.note }), h("p", { class: "muted", text: `Computed by the ${r.agent} (${r.method}).` }));
        }
        catch (error) {
            message.textContent = error instanceof ApiError ? error.friendly : "The analysis could not be run.";
        }
        return undefined;
    };
    openDialog({
        title: "Beam analysis", eyebrow: beamName, size: "wide", noCancel: true, cancelLabel: "Close", focus: () => magnitude,
        body: h("div", { css: { display: "grid", gap: ".8rem" } }, h("p", { class: "note", text: "Peak moment, shear and deflection of this beam under a load you enter, computed by the SLAI STEM Agent. An analytical baseline, not a code check." }), labelled("Support", support), labelled("Load", load), labelled("Load magnitude", magnitude), labelled("Elastic modulus E (GPa)", modulus, { hint: "Leave empty to use the engine's value for steel. Concrete and timber need a value: SPADRA does not assume one." }), message, results),
        actions: [{ label: "Close", run: () => true }, { label: "Analyse", kind: "primary", run: run }],
    });
}
