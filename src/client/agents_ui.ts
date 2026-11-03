/** Dialogs for the engineering agents: simulation, optimisation, the calculator and the agent list. Every number is the server's (and so the agents'); nothing is computed here. */
import { h, labelled, s } from "../../../tools/web/src/dom.js";
import { ApiError } from "../../../tools/web/src/api.js";
import { confirmDialog, openDialog, toast } from "../../../tools/web/src/ui.js";
import type { Store } from "./store.js";
import { sig } from "./quantities.js";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Obj = Record<string, any>;

const SUPPORTS: Array<[string, string]> = [["simply_supported", "Simply supported"], ["cantilever", "Cantilever"], ["fixed_fixed", "Fixed at both ends"]];
const LOADS: Array<[string, string]> = [["udl", "Uniform load (kN/m)"], ["point_center", "Point load at mid-span (kN)"], ["point_end", "Point load at the free end (kN)"]];
const CASES = new Set(["simply_supported:udl", "simply_supported:point_center", "cantilever:udl", "cantilever:point_end", "fixed_fixed:udl"]);

const input = (id: string, placeholder = "", maxlength = 16): HTMLInputElement => h("input", { id, type: "text", placeholder, attrs: { autocomplete: "off", maxlength, inputmode: "decimal" } });
const num = (el: HTMLInputElement): number | null => { const v = Number(el.value.trim().replace(",", ".")); return el.value.trim() !== "" && Number.isFinite(v) ? v : null; };
const table = (label: string, rows: Array<[string, string]>): HTMLElement => h("table", { class: "schedule", attrs: { "aria-label": label } }, h("tbody", {}, rows.map(([k, v]) => h("tr", {}, h("th", { attrs: { scope: "row" }, text: k }), h("td", { class: "num", text: v })))));
const fail = (error: unknown, fallback: string): string => (error instanceof ApiError ? error.friendly : fallback);

/** The support / load / modulus fields shared by the simulation and the optimisation. */
function loadCase(prefix: string): { fields: HTMLElement[]; support: HTMLSelectElement; load: HTMLSelectElement; magnitude: HTMLInputElement; modulus: HTMLInputElement; density: HTMLInputElement;
  body: () => Obj | string } {
  const support = h("select", { id: `${prefix}-support` }, ...SUPPORTS.map(([v, l]) => h("option", { value: v, text: l })));
  const load = h("select", { id: `${prefix}-loadtype` }, ...LOADS.map(([v, l]) => h("option", { value: v, text: l })));
  const magnitude = input(`${prefix}-load`, "e.g. 10");
  const modulus = input(`${prefix}-modulus`, "Engine value if it has one", 12);
  const density = input(`${prefix}-density`, "Engine value if it has one", 12);
  const sync = (): void => {
    for (const opt of Array.from(load.options)) opt.disabled = !CASES.has(`${support.value}:${opt.value}`);
    if (load.selectedOptions[0]?.disabled) load.value = Array.from(load.options).find((o) => !o.disabled)?.value ?? "udl";
  };
  support.addEventListener("change", sync); sync();
  const body = (): Obj | string => {
    const v = num(magnitude);
    if (v === null || v <= 0) return "Enter the load as a number greater than zero.";
    const out: Obj = { support: support.value, load_type: load.value, load: v };
    if (modulus.value.trim() !== "") { const e = num(modulus); if (e === null || e <= 0) return "The elastic modulus must be a number greater than zero (GPa)."; out["youngs_modulus_gpa"] = e; }
    if (density.value.trim() !== "") { const d = num(density); if (d === null || d < 1) return "The density must be a number of kg/m³, at least 1."; out["density_kg_m3"] = d; }
    return out;
  };
  return { fields: [labelled("Support", support), labelled("Load", load), labelled("Load magnitude", magnitude),
    labelled("Elastic modulus E (GPa)", modulus, { hint: "Concrete and timber need a value: SPADRA does not assume one." }),
    labelled("Density (kg/m³)", density, { hint: "Needed for the mass; steel has an engine value, other materials need yours." })], support, load, magnitude, modulus, density, body };
}

// ------------------------------------------------------------------------------------------------------------- simulation
/** A displacement-time chart: the simulated trajectory and, dotted, the closed-form solution of the same equation. */
export function responseChart(series: { time_s: number[]; displacement_m: number[]; reference_m: number[] }): SVGElement {
  const W = 560, H = 220, L = 52, R = 10, T = 10, B = 28;
  const t = series.time_s, y = series.displacement_m.map((v) => v * 1000), r = series.reference_m.map((v) => v * 1000);
  const lo = Math.min(...y, ...r, 0), hi = Math.max(...y, ...r, 0), span = hi - lo || 1, t1 = t[t.length - 1] || 1;
  const X = (v: number): number => L + (v / t1) * (W - L - R), Y = (v: number): number => T + (1 - (v - lo) / span) * (H - T - B);
  const path = (vals: number[]): string => vals.map((v, i) => `${i ? "L" : "M"}${X(t[i] ?? 0).toFixed(1)},${Y(v).toFixed(1)}`).join(" ");
  return s("svg", { class: "response-chart", attrs: { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Displacement against time" } },
    s("line", { attrs: { x1: L, x2: W - R, y1: Y(0), y2: Y(0), stroke: "currentColor", "stroke-opacity": "0.35" } }),
    s("path", { attrs: { d: path(r), fill: "none", stroke: "currentColor", "stroke-opacity": "0.6", "stroke-dasharray": "3 3", "stroke-width": "1.5" } }),
    s("path", { attrs: { d: path(y), fill: "none", stroke: "var(--accent)", "stroke-width": "1.6" } }),
    s("text", { attrs: { x: L - 6, y: T + 8, "text-anchor": "end", "font-size": "10", fill: "currentColor" } }, `${sig(hi, 3)}`),
    s("text", { attrs: { x: L - 6, y: H - B, "text-anchor": "end", "font-size": "10", fill: "currentColor" } }, `${sig(lo, 3)}`),
    s("text", { attrs: { x: L, y: H - 8, "font-size": "10", fill: "currentColor" } }, "0 s"),
    s("text", { attrs: { x: W - R, y: H - 8, "text-anchor": "end", "font-size": "10", fill: "currentColor" } }, `${sig(t1, 3)} s`),
    s("text", { attrs: { x: 4, y: 12, "font-size": "10", fill: "currentColor" } }, "mm"));
}

export function simulationDialog(store: Store, beamId: string, beamName: string): void {
  const c = loadCase("sim");
  const scenario = h("select", { id: "sim-scenario" }, h("option", { value: "release", text: "Release from the static deflection" }), h("option", { value: "step", text: "Load applied suddenly (step)" }));
  const damping = input("sim-damping", "0", 6), duration = input("sim-duration", "2", 6);
  const message = h("p", { class: "muted", attrs: { role: "alert" } });
  const results = h("div", { class: "beam-results", attrs: { "aria-live": "polite" } });
  const run = async (): Promise<undefined> => {
    results.replaceChildren(); message.textContent = "";
    const body = c.body();
    if (typeof body === "string") { message.textContent = body; return undefined; }
    body["id"] = beamId; body["scenario"] = scenario.value;
    if (damping.value.trim() !== "") { const z = num(damping); if (z === null || z < 0 || z >= 100) { message.textContent = "Damping is a percentage of critical damping from 0 to less than 100."; return undefined; } body["damping_ratio"] = z / 100; }
    if (duration.value.trim() !== "") { const d = num(duration); if (d === null || d <= 0 || d > 600) { message.textContent = "The duration is a number of seconds from 0 to 600."; return undefined; } body["duration_s"] = d; }
    message.textContent = "Running…";
    try {
      const r = await store.api.post<Obj>("/simulation/beam", body);
      const d = r["derived"], p = r["response"], v = r["verification"], pv = r["provenance"];
      message.textContent = "";
      results.append(
        h("h3", { text: "Response" }), responseChart(r["series"]),
        h("p", { class: "muted", text: "Solid: simulated by the SLAI Simulation Agent. Dotted: the closed-form solution of the same equation." }),
        table("Simulation results", [["Natural frequency", `${sig(d["natural_frequency_hz"])} Hz (period ${sig(d["period_s"])} s)`], ["Static deflection", `${sig(d["static_deflection_m"] * 1000)} mm`],
          ["Peak displacement", `${sig(p["peak_displacement_m"] * 1000)} mm at ${sig(p["peak_time_s"])} s`], ["Dynamic amplification", sig(p["amplification"])],
          ["Displacement at the end", `${sig(p["final_displacement_m"] * 1000)} mm`], ["Stiffness / vibrating mass", `${sig(d["stiffness_n_per_m"] / 1e6)} MN/m / ${sig(d["effective_mass_kg"])} kg`]]),
        h("h3", { text: "Checked" }),
        h("p", { class: v["passed"] ? "muted" : "note", text: `${v["passed"] ? "Agrees with" : "DOES NOT agree with"} the ${v["reference"]}: largest difference ${sig(v["max_abs_error_m"] * 1000, 3)} mm (${sig(v["relative_to_peak"] * 100, 3)} % of the peak).` }),
        h("p", { class: "muted", text: `Run ${pv["solver"]["run_id"]} · ${pv["inputs"]["steps"]} steps of ${sig(pv["inputs"]["time_step_s"], 3)} s · ${pv["solver"]["model"]} · ${pv["solver"]["integrator"]} · inputs fingerprint ${String(pv["inputs_sha256"]).slice(0, 12)}…` }),
        h("p", { class: "muted", text: `Assumptions: ${(pv["assumptions"] as string[]).join("; ")}.` }),
        h("p", { class: "note", text: r["note"] }));
    } catch (error) { message.textContent = fail(error, "The simulation could not be run."); }
    return undefined;
  };
  openDialog({ title: "Beam simulation", eyebrow: beamName, size: "wide", noCancel: true, focus: () => c.magnitude,
    body: h("div", { css: { display: "grid", gap: ".8rem" } },
      h("p", { class: "note", text: "The dynamic response of this beam as a single-degree-of-freedom system, run by the SLAI Simulation Agent with the SLAI STEM Agent as integrator. A preliminary analysis, not a design check." }),
      ...c.fields, labelled("Scenario", scenario), labelled("Damping (% of critical)", damping, { hint: "Empty means undamped." }), labelled("Duration (s)", duration, { hint: "Default 2 s." }), message, results),
    actions: [{ label: "Close", run: () => true }, { label: "Simulate", kind: "primary", run }] });
}

// ----------------------------------------------------------------------------------------------------------- optimisation
export function optimizationDialog(store: Store, beamId: string, beamName: string): void {
  const c = loadCase("opt");
  const variable = h("select", { id: "opt-variable" }, h("option", { value: "depth", text: "Depth" }), h("option", { value: "width", text: "Width" }));
  const lower = input("opt-lower", "e.g. 150", 8), upper = input("opt-upper", "e.g. 600", 8), ratio = input("opt-ratio", "360", 6);
  const message = h("p", { class: "muted", attrs: { role: "alert" } });
  const results = h("div", { class: "beam-results", attrs: { "aria-live": "polite" } });
  let dialog: { close(): void } | null = null;
  const run = async (): Promise<undefined> => {
    results.replaceChildren(); message.textContent = "";
    const body = c.body();
    if (typeof body === "string") { message.textContent = body; return undefined; }
    const lo = num(lower), hi = num(upper);
    if (lo === null || hi === null || lo <= 0 || hi <= lo) { message.textContent = "Enter the lower and upper bound in mm, the lower smaller than the upper. SPADRA does not choose them for you."; return undefined; }
    Object.assign(body, { id: beamId, variable: variable.value, lower_mm: lo, upper_mm: hi });
    if (ratio.value.trim() !== "") { const q = num(ratio); if (q === null || q < 10 || q > 10000) { message.textContent = "The deflection limit ratio is span ÷ 10 to span ÷ 10000."; return undefined; } body["deflection_ratio"] = q; }
    message.textContent = "Optimising…";
    try {
      const r = await store.api.post<Obj>("/optimization/beam", body);
      message.textContent = "";
      const a = r["agent"], pr = r["problem"];
      results.append(
        h("h3", { text: "Problem" }),
        table("Problem", [["Minimise", pr["minimise"]], ["By choosing", pr["variable"]], ["Subject to", pr["constraint"]], ["Load case", `${pr["support"]}, ${pr["load_type"]}, ${sig(pr["load"])} ${pr["load_unit"]}, span ${sig(pr["span_m"])} m`]]),
        h("h3", { text: "Answer" }));
      if (!r["found"]) { results.append(h("p", { class: "note", text: r["message"] ?? "No feasible value was found." })); }
      else {
        const b = r["best"], cur = r["current"], ch = r["change"];
        results.append(
          table("Optimisation result", [["Now", `${sig(cur["value_mm"])} mm · volume ${sig(cur["volume_m3"])} m³ · deflection ${sig(cur["max_deflection_m"] * 1000)} mm`],
            ["Smallest that meets the limit", `${sig(b["value_mm"])} mm · volume ${sig(b["volume_m3"])} m³ · deflection ${sig(b["max_deflection_m"] * 1000)} mm of ${sig(b["limit_m"] * 1000)} mm allowed`],
            ["Independent check", `${b["check"]["meets_limit"] ? "meets" : "FAILS"} the limit, re-evaluated through the STEM Agent`]]),
          h("button", { type: "button", class: "review", id: "opt-apply", text: `Apply ${ch["key"]} = ${ch["value"]} mm to the beam…`, on: { click: () => { void apply(r); } } }),
          h("p", { class: "muted", text: "Applying is a separate step: it asks you to confirm, then edits the beam through the project's normal command (one undo step)." }));
      }
      results.append(
        h("h3", { text: "Evidence from the Optimization Agent" }),
        table("Agent evidence", [["Solver", String(a["solver"] ?? "–")], ["Status", `${a["status"] ?? "–"} · ${a["solver_status"] ?? "–"}`], ["Termination", String(a["termination"] ?? "–")],
          ["Feasibility", String(a["feasibility"] ?? "–")], ["Optimality", String(a["optimality"] ?? "–")], ["Evaluations", String(a["evaluations"] ?? "–")], ["Run", String(a["run_id"] ?? "–")]]),
        ...((a["warnings"] as string[]) ?? []).map((w) => h("p", { class: "muted", text: `Agent warning: ${w}` })),
        h("p", { class: "note", text: r["note"] }));
    } catch (error) { message.textContent = fail(error, "The optimisation could not be run."); }
    return undefined;
  };
  const apply = async (r: Obj): Promise<void> => {
    if (store.project && store.project.revision !== r["revision"]) { message.textContent = "The project has changed since this was computed. Run it again."; return; }
    const ch = r["change"];
    const ok = await confirmDialog({ title: "Apply the optimised size?", message: `Set the ${ch["key"]} of ${r["name"]} to ${ch["value"]} mm. Only the deflection limit was considered.`, confirmLabel: "Apply" });
    if (!ok) return;
    const done = await store.run("model.set_property", { id: ch["id"], key: ch["key"], value: ch["value"] });
    if (done) { toast(`${ch["key"]} set to ${ch["value"]} mm. Undo reverts it.`, { kind: "ok", timeoutMs: 3000 }); dialog?.close(); }
  };
  dialog = openDialog({ title: "Beam optimisation", eyebrow: beamName, size: "wide", noCancel: true, focus: () => c.magnitude,
    body: h("div", { css: { display: "grid", gap: ".8rem" } },
      h("p", { class: "note", text: "Finds the smallest depth or width, within your bounds, that keeps this beam's deflection under span ÷ the ratio you set - the one constraint considered. The SLAI Optimization Agent searches; the SLAI STEM Agent evaluates every trial." }),
      ...c.fields, labelled("Dimension to optimise", variable), labelled("Lower bound (mm)", lower), labelled("Upper bound (mm)", upper), labelled("Deflection limit: span ÷", ratio, { hint: "Default 360." }), message, results),
    actions: [{ label: "Close", run: () => true }, { label: "Optimise", kind: "primary", run }] });
}

// ------------------------------------------------------------------------------------------------------------- calculator
/** ``name = value unit`` lines to the parameters the server wants. */
export function parseParameters(textValue: string): { parameters: Obj; error: string | null } {
  const parameters: Obj = {};
  for (const [n, line] of textValue.split("\n").map((l) => l.trim()).entries()) {
    if (!line) continue;
    const m = /^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(-?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?)\s*([A-Za-z]+)?$/.exec(line);
    if (!m) return { parameters, error: `Parameter line ${n + 1}: write it as name = 3 m (a number and, optionally, a unit).` };
    parameters[m[1] as string] = m[3] ? { value: Number((m[2] as string).replace(",", ".")), unit: m[3] } : Number((m[2] as string).replace(",", "."));
  }
  return { parameters, error: null };
}

/** ``a <= 4 m`` lines. A number with a unit on the right becomes a quantity; anything else is passed through as an expression over the parameters. */
export function parseConstraints(textValue: string): { constraints: Array<string | Obj>; error: string | null } {
  const out: Array<string | Obj> = [];
  for (const [n, line] of textValue.split("\n").map((l) => l.trim()).entries()) {
    if (!line) continue;
    const m = /^(.+?)\s*(<=|>=|==|<|>)\s*(.+)$/.exec(line);
    if (!m) return { constraints: out, error: `Constraint line ${n + 1}: write it as, for example, height <= 4 m.` };
    const q = /^(-?\d+(?:[.,]\d+)?)\s*([A-Za-z]+)?$/.exec((m[3] as string).trim());
    out.push(q ? { left: (m[1] as string).trim(), op: m[2], right: q[2] ? { value: Number((q[1] as string).replace(",", ".")), unit: q[2] } : Number((q[1] as string).replace(",", ".")) } : line);
  }
  return { constraints: out, error: null };
}

export function parseFormulas(textValue: string): { formulas: Record<string, string>; error: string | null } {
  const formulas: Record<string, string> = {};
  for (const [n, line] of textValue.split("\n").map((l) => l.trim()).entries()) {
    if (!line) continue;
    const m = /^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)$/.exec(line);
    if (!m) return { formulas, error: `Formula line ${n + 1}: write it as name = expression.` };
    formulas[m[1] as string] = m[2] as string;
  }
  return { formulas, error: null };
}

export function calculatorDialog(store: Store): void {
  const picked = store.selection?.kind === "model" ? store.selection.ids : [];
  const value = input("calc-value", "e.g. 2.5", 18), from = input("calc-from", "m", 12), to = input("calc-to", "mm", 12);
  const params = h("textarea", { id: "calc-params", attrs: { rows: 4, spellcheck: "false", "aria-label": "Parameters", placeholder: "width = 3 m\nthickness = 200 mm" }, css: { width: "100%", fontFamily: "monospace" } });
  const formulas = h("textarea", { id: "calc-formulas", attrs: { rows: 3, spellcheck: "false", "aria-label": "Formulas", placeholder: "volume = width * thickness * 2.5" }, css: { width: "100%", fontFamily: "monospace" } });
  const constraints = h("textarea", { id: "calc-constraints", attrs: { rows: 3, spellcheck: "false", "aria-label": "Constraints", placeholder: "thickness >= 150 mm" }, css: { width: "100%", fontFamily: "monospace" } });
  const message = h("p", { class: "muted", attrs: { role: "alert" } });
  const out = h("div", { class: "beam-results", attrs: { "aria-live": "polite" } });
  const guarded = async (work: () => Promise<void>): Promise<undefined> => { message.textContent = ""; out.replaceChildren(); try { await work(); } catch (error) { message.textContent = fail(error, "That could not be calculated."); } return undefined; };

  const convert = (): Promise<undefined> => guarded(async () => {
    const v = num(value);
    if (v === null || !from.value.trim() || !to.value.trim()) { message.textContent = "Enter a value and the units to convert from and to."; return; }
    const r = await store.api.post<Obj>("/calculator/convert", { value: v, from: from.value.trim(), to: to.value.trim() });
    out.append(table("Conversion", [[`${sig(v, 8)} ${from.value.trim()}`, `${sig(r["result"]["value"], 10)} ${r["result"]["unit"]}`]]), h("p", { class: "muted", text: `Converted by the ${r["agent"]}.` }));
  });
  const useSelection = (): Promise<undefined> => guarded(async () => {
    if (!picked.length) { message.textContent = "Select one or more elements first."; return; }
    const r = await store.api.post<Obj>("/calculator/parameters", { ids: picked.slice(0, 12) });
    params.value = (r["rows"] as Obj[]).map((x) => `${x["name"]} = ${x["value"]} ${x["unit"]}`).join("\n");
    out.append(table("Named parameters", (r["rows"] as Obj[]).map((x) => [x["name"], `${x["label"]}`] as [string, string])), h("p", { class: "muted", text: "Use these names in formulas and constraints. They are the elements' own values." }));
  });
  const evaluate = (): Promise<undefined> => guarded(async () => {
    const p = parseParameters(params.value), f = parseFormulas(formulas.value);
    if (p.error || f.error) { message.textContent = p.error ?? f.error ?? ""; return; }
    const r = await store.api.post<Obj>("/calculator/evaluate", { formulas: f.formulas, parameters: p.parameters });
    out.append(table("Formula results", Object.entries(r["results"] as Obj).map(([k, q]) => [k, `${sig(q["value"], 8)} ${q["unit"]}  (= ${sig(q["si_value"], 8)} in SI base units)`] as [string, string])), h("p", { class: "muted", text: r["note"] }));
  });
  const check = (): Promise<undefined> => guarded(async () => {
    const p = parseParameters(params.value), c = parseConstraints(constraints.value);
    if (p.error || c.error) { message.textContent = p.error ?? c.error ?? ""; return; }
    if (!c.constraints.length) { message.textContent = "Enter at least one constraint."; return; }
    const r = await store.api.post<Obj>("/calculator/constraints", { constraints: c.constraints, parameters: p.parameters });
    out.append(h("p", { class: r["feasible"] ? "muted" : "note", text: r["feasible"] ? "All constraints are satisfied." : "At least one constraint is violated." }),
      table("Constraint results", (r["results"] as Obj[]).map((x) => [x["constraint"], x["passed"] ? "satisfied" : "violated"] as [string, string])), h("p", { class: "muted", text: "Dimensions are checked: comparing a length with a mass is refused." }));
  });
  const measure = (): Promise<undefined> => guarded(async () => {
    if (!picked.length) { message.textContent = "Select one element (its figures) or two (the distance between them)."; return; }
    const r = await store.api.post<Obj>("/calculator/measure", { ids: picked });
    out.append(table("Measurements", (r["figures"] as Obj[]).map((x) => [x["label"], `${typeof x["value"] === "number" ? sig(x["value"], 6) : Array.isArray(x["value"]) ? x["value"].map((n: number) => sig(n, 5)).join(", ") : String(x["value"])} ${x["unit"]}`.trim()] as [string, string])),
      h("p", { class: "muted", text: (r["figures"] as Obj[]).map((x) => `${x["label"]}: ${x["basis"]}`).join(" · ") }));
  });
  const btn = (label: string, id: string, run: () => Promise<undefined>): HTMLButtonElement => h("button", { type: "button", class: "plain", id, text: label, on: { click: () => { void run(); } } });

  openDialog({ title: "Engineering calculator", eyebrow: picked.length ? `${picked.length} selected` : "No selection", size: "wide", noCancel: true,
    body: h("div", { css: { display: "grid", gap: ".8rem" } },
      h("p", { class: "note", text: "Unit conversion, formulas with units and constraint checks are computed by the SLAI STEM Agent from exactly what you enter; measurements come from the model and the SLAI Spatial Agent." }),
      h("h3", { text: "Convert" }), h("div", { class: "row" }, labelled("Value", value), labelled("From", from), labelled("To", to), btn("Convert", "calc-convert", convert)),
      h("h3", { text: "Selected elements" }), h("div", { class: "row" }, btn("Measure", "calc-measure", measure), btn("Use their parameters", "calc-use", useSelection)),
      h("h3", { text: "Formulas and constraints" }), labelled("Parameters (name = value unit)", params), labelled("Formulas (name = expression)", formulas), labelled("Constraints (for example height <= 4 m)", constraints),
      h("div", { class: "row" }, btn("Evaluate formulas", "calc-eval", evaluate), btn("Check constraints", "calc-check", check)), message, out),
    actions: [{ label: "Close", run: () => true }] });
}

// ----------------------------------------------------------------------------------------------------------------- agents
export async function agentsDialog(store: Store): Promise<void> {
  let data: Obj;
  try { data = await store.api.get<Obj>("/agents"); } catch (error) { toast(fail(error, "The agent list could not be read."), { kind: "error" }); return; }
  const rows: Obj[] = data["agents"];
  const state = (a: Obj): string => (a["integrated"] ? (a["available"] ? "Integrated · available" : `Integrated · not available here${a["reason"] ? `: ${a["reason"]}` : ""}`) : `Not integrated: ${a["why_not"] ?? ""}`);
  openDialog({ title: "SLAI agents", eyebrow: `${rows.filter((a) => a["integrated"] && a["available"]).length} of ${rows.length} in use`, size: "wide", noCancel: true,
    body: h("div", { css: { display: "grid", gap: ".8rem" } },
      h("p", { class: "note", text: "Only agents that SPADRA actually calls are marked integrated. The others are listed with the reason, so nothing here suggests more than it does." }),
      h("table", { class: "schedule", attrs: { "aria-label": "SLAI agents" } },
        h("thead", {}, h("tr", {}, ["Agent", "Spec", "Status", "Used for"].map((t) => h("th", { text: t })))),
        h("tbody", {}, rows.map((a) => h("tr", { data: { agent: a["key"] } }, h("th", { attrs: { scope: "row" }, text: a["label"] }), h("td", { text: a["section"] }), h("td", { text: state(a) }), h("td", { text: a["purpose"] }))))),
      h("h3", { text: "Recent agent calls" }),
      (data["calls"] as Obj[]).length ? h("ul", {}, (data["calls"] as Obj[]).slice(-12).reverse().map((c) => h("li", { text: `${c["agent"]} · ${c["operation"]}${c["request_id"] ? ` · ${c["request_id"]}` : ""}` })))
        : h("p", { class: "muted", text: "No agent has been called in this session yet." })),
    actions: [{ label: "Close", run: () => true }] });
}
