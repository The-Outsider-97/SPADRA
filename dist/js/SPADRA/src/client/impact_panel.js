/** The geometric-impact panel of a modifier preview: one renderer for the one server contract (``spadra.preview.v1``), used for 3D model modifiers and 2D drawing modifiers alike.
 *  It only shows what the server measured or predicted: nothing is computed here, and a prediction is always labelled as one. */
import { h } from "../../../tools/web/src/dom.js";
const num = (v, digits = 4) => typeof v === "number" && Number.isFinite(v) ? String(Number(v.toFixed(digits))) : "–";
const vec = (v) => Array.isArray(v) ? `(${v.map((x) => num(x, 3)).join(", ")})` : "–";
const at = (o, ...path) => path.reduce((acc, k) => (acc && typeof acc === "object" ? acc[k] : undefined), o);
const signed = (v, digits = 4) => typeof v === "number" && Number.isFinite(v) ? `${v > 0 ? "+" : ""}${num(v, digits)}` : "–";
function row(label, before, after, change = "") {
    return h("tr", {}, h("th", { attrs: { scope: "row" }, text: label }), h("td", { class: "num", text: before }), h("td", { class: "num", text: after }), h("td", { class: "num", text: change }));
}
/** Added / removed / modified, before and after, what is expected against what the kernel did, who depends on it, what it touches, and the findings, in that order. */
export function renderImpact(into, analysis, opts = {}) {
    if (!analysis) {
        into.replaceChildren(h("p", { class: "muted", text: opts.refused ?? "No impact analysis yet." }));
        return;
    }
    const parts = [];
    const diags = analysis.diagnostics ?? [];
    const errors = diags.filter((d) => d.severity === "error").length, warnings = diags.filter((d) => d.severity === "warning").length;
    parts.push(h("div", { class: "ap-status", attrs: { role: "status" } }, h("span", { class: "badge", text: analysis.valid ? "valid" : "not valid" }), errors ? h("span", { class: "badge risk", text: `${errors} error${errors > 1 ? "s" : ""}` }) : null, warnings ? h("span", { class: "badge", text: `${warnings} warning${warnings > 1 ? "s" : ""}` }) : null, h("span", { class: "muted", text: `${analysis.kind === "model" ? "model" : "drawing"} modifier · ${analysis.operation}` })));
    const imp = analysis.impact ?? {};
    const count = (k) => Array.isArray(imp[k]) ? imp[k].length : 0;
    parts.push(h("p", { text: `Added ${count("added")} · removed ${count("removed")} · modified ${count("modified")}${count("modifiers_added") ? ` · modifier stack +${count("modifiers_added")}` : ""}` }));
    const b = analysis.before, a = analysis.after, d = analysis.diff;
    if (b && a) {
        const rows = [];
        if (analysis.kind === "model") {
            rows.push(row("Size (m)", vec(at(b, "bbox", "size")), vec(at(a, "bbox", "size")), vec(at(d, "size"))));
            rows.push(row("Volume (m³)", num(b["volume"]), num(a["volume"]), signed(at(d, "volume"))));
            rows.push(row("Area (m²)", num(b["area"]), num(a["area"]), signed(at(d, "area"))));
            rows.push(row("Faces", num(at(b, "topology", "faces"), 0), num(at(a, "topology", "faces"), 0), signed(at(d, "topology", "faces"), 0)));
            rows.push(row("Shells", num(at(b, "topology", "shells"), 0), num(at(a, "topology", "shells"), 0), signed(at(d, "topology", "shells"), 0)));
            rows.push(row("Closed solid", String(b["closed"] ?? "–"), String(a["closed"] ?? "–")));
        }
        else {
            rows.push(row("Vertices", num(b["vertices"], 0), num(a["vertices"], 0), signed(at(d, "vertices"), 0)));
            rows.push(row("Length", num(b["length"]), num(a["length"]), signed(at(d, "length"))));
            rows.push(row("Area", num(b["area"]), num(a["area"]), signed(at(d, "area"))));
        }
        parts.push(h("table", { class: "schedule", attrs: { "aria-label": "Before and after" } }, h("thead", {}, h("tr", {}, ["", "Before", "After", "Change"].map((t) => h("th", { text: t })))), h("tbody", {}, rows)));
    }
    const audit = analysis.audit ?? [];
    const pred = analysis.predicted;
    if (audit.length) {
        parts.push(h("p", { class: "muted", text: `Expected before running (${String(pred?.["method"] ?? "deterministic")}) against what the kernel produced:` }));
        parts.push(h("table", { class: "schedule", attrs: { "aria-label": "Predicted and actual" } }, h("thead", {}, h("tr", {}, ["Measure", "Predicted", "Actual", "Within"].map((t) => h("th", { text: t })))), h("tbody", {}, audit.map((r) => h("tr", {}, h("th", { attrs: { scope: "row" }, text: `${r["metric"]} (${r["kind"]})` }), h("td", { class: "num", text: Array.isArray(r["predicted"]) ? vec(r["predicted"]) : Array.isArray(r["predicted"]?.["bounds"]) ? "bounds" : JSON.stringify(r["predicted"]) }), h("td", { class: "num", text: Array.isArray(r["actual"]) ? vec(r["actual"]) : num(r["actual"]) }), h("td", { text: r["within"] ? "yes" : "no" }))))));
    }
    else if (pred && pred["available"] === false) {
        parts.push(h("p", { class: "muted", text: `No prediction for this operation: ${String(pred["reason"] ?? "")}` }));
    }
    const learned = analysis.learned;
    if (learned)
        parts.push(h("p", { class: "muted", text: learned["available"] ? "A learned prediction is available and is advisory only." : "Learned prediction: not available. The numbers above are deterministic." }));
    const deps = analysis.dependents ?? [];
    if (deps.length) {
        parts.push(h("p", { text: `${deps.length} dependent element${deps.length > 1 ? "s" : ""} will be re-evaluated:` }));
        parts.push(h("ul", {}, deps.slice(0, 12).map((x) => h("li", { text: `${String(x["name"] ?? x["id"])} (${String(x["via"] ?? x["relation"] ?? "depends")})` }))));
    }
    const col = analysis.collisions;
    const overlaps = Array.isArray(col?.["introduced"]) ? col?.["introduced"] : [];
    if (col)
        parts.push(h("p", { class: overlaps.length ? "" : "muted", text: overlaps.length
                ? `${overlaps.length} new bounding-box overlap${overlaps.length > 1 ? "s" : ""} (candidates, not proven interference).`
                : `No new bounding-box overlaps among ${String(col["checked"] ?? 0)} neighbouring element(s).` }));
    if (diags.length)
        parts.push(h("ul", { class: "ap-diags", attrs: { "aria-label": "Diagnostics" } }, diags.map((x) => h("li", {}, h("span", { class: x.severity === "error" ? "badge risk" : "badge", text: x.severity }), h("span", { text: ` ${x.message}${x.hint ? ` ${x.hint}` : ""}` })))));
    into.replaceChildren(...parts);
}
/** The analysis a refused preview carries (the server answers 400 with ``details.analysis``); null for any other error. */
export function analysisOfError(error) {
    const d = error?.details;
    return d?.analysis && d.analysis.contract === "spadra.preview.v1" ? d.analysis : null;
}
