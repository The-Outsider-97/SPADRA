/** The parametric modelling interface: family types, a formula editor with a live check and a dependency inspector, Python scripts (with the isolation the machine
 *  really provides), parametric optimization and stair design. Every screen shows what the server holds and sends commands or read-only previews; nothing here
 *  computes a model result, and nothing changes the project except through a command the server validates. */
import { h, labelled, replace } from "../../../tools/web/src/dom.js";
import { confirmDialog, openDialog, toast } from "../../../tools/web/src/ui.js";
const STATUS_WORDS = { converged: "A feasible point was found and the search settled on it", budget_exhausted: "A feasible point was found; the search ran out of budget before settling",
    infeasible: "No feasible point was found (this is not a proof that none exists)", unbounded: "An objective is not bounded as posed", failed: "Every evaluation failed" };
function note(text, kind = "muted") {
    return h("p", { class: kind === "muted" ? "muted" : `status-${kind}`, text, attrs: { role: kind === "muted" ? "note" : "status" } });
}
const shown = (v) => (v === null || v === undefined ? "" : typeof v === "number" ? String(Math.round(v * 1e6) / 1e6) : String(v));
// ------------------------------------------------------------------------------------------------------------------ family types
/** The parameters a type can fix: not formulas, not read-only. */
const typed = (family) => family.parameters.filter((p) => !p.expression && !p.read_only);
/** A table with one row per named type and one column per parameter. An empty cell means "follow the family default"; saving a row sends only what changed. */
export function familyTypesPanel(store, family, reload) {
    const types = family.types ?? [];
    const cols = typed(family);
    const newName = h("input", { type: "text", placeholder: "New type name (for example D-900)", attrs: { "aria-label": "New type name", maxlength: 80, autocomplete: "off" } });
    const rows = types.map((t) => {
        const cells = cols.map((p) => {
            const input = h("input", { type: "text", value: shown(t.values[p.name]), placeholder: shown(p.default), attrs: { "aria-label": `${t.name}: ${p.label}${p.unit ? ` (${p.unit})` : ""}`, inputmode: "decimal", autocomplete: "off" } });
            return { p, input };
        });
        const save = async () => {
            const values = {};
            for (const c of cells) {
                const was = shown(t.values[c.p.name]);
                if (c.input.value.trim() === was)
                    continue;
                const raw = c.input.value.trim();
                values[c.p.name] = raw === "" ? null : (c.p.type === "boolean" || c.p.type === "enumeration" ? raw : Number(raw));
                if (raw !== "" && typeof values[c.p.name] === "number" && !Number.isFinite(values[c.p.name])) {
                    toast(`${c.p.label}: enter a number.`, { kind: "error" });
                    c.input.focus();
                    return;
                }
            }
            if (!Object.keys(values).length) {
                toast("Nothing was changed.", { kind: "info" });
                return;
            }
            if (await store.run("family.type_update", { id: family.id, name: t.name, values })) {
                toast(`Type ${t.name} was updated; ${t.instances} placed element(s) were rebuilt.`, { kind: "ok", timeoutMs: 3000 });
                reload();
            }
        };
        const rename = async () => {
            const name = window.prompt(`Rename type ${t.name} to`, t.name);
            if (name && name.trim() && name.trim() !== t.name && (await store.run("family.type_rename", { id: family.id, name: t.name, new_name: name.trim() })))
                reload();
        };
        const remove = async () => {
            if (t.instances) {
                toast(`${t.instances} placed element(s) use the type ${t.name}. Give them another type first, then delete it.`, { kind: "info", timeoutMs: 6000 });
                return;
            }
            if (!(await confirmDialog({ title: "Delete type", message: `Delete type “${t.name}”?`, confirmLabel: "Delete", danger: true, detail: "No placed element uses it." })))
                return;
            if (await store.run("family.type_delete", { id: family.id, name: t.name }))
                reload();
        };
        return h("tr", {}, h("th", { attrs: { scope: "row" }, text: t.name }), ...cells.map((c) => h("td", {}, c.input)), h("td", { text: String(t.instances) }), h("td", {}, h("button", { class: "tool-inline", type: "button", text: "Save", on: { click: () => { void save(); } } }), h("button", { class: "tool-inline", type: "button", text: "Duplicate", on: { click: () => { void store.run("family.type_duplicate", { id: family.id, name: t.name }).then((ok) => { if (ok)
                    reload(); }); } } }), h("button", { class: "tool-inline", type: "button", text: "Rename", on: { click: () => { void rename(); } } }), h("button", { class: "tool-inline", type: "button", text: "Delete", on: { click: () => { void remove(); } } })));
    });
    const add = async () => {
        if (!newName.value.trim()) {
            toast("Give the type a name.", { kind: "info" });
            newName.focus();
            return;
        }
        if (await store.run("family.type_define", { id: family.id, name: newName.value.trim(), values: {} })) {
            newName.value = "";
            reload();
        }
    };
    return h("section", { attrs: { "aria-label": `Types of ${family.name}` }, css: { display: "grid", gap: ".5rem" } }, h("h3", { text: "Types" }), types.length ? h("div", { css: { overflowX: "auto" } }, h("table", { class: "schedule", attrs: { "aria-label": `Types of ${family.name}` } }, h("thead", {}, h("tr", {}, h("th", { text: "Type" }), ...cols.map((p) => h("th", { text: p.unit ? `${p.label} (${p.unit})` : p.label })), h("th", { text: "Placed" }), h("th", { text: "" }))), h("tbody", {}, rows)))
        : note("This family has no named types yet. A type fixes some parameters (for example width) under a name; an element made from it follows the type until you override a value on the element."), note("An empty cell follows the family default. Precedence: family default or formula, then the named type, then a value set on the element. Parameters marked as type parameters cannot be overridden on an element."), h("div", { class: "prop-add" }, newName, h("button", { class: "tool-inline", type: "button", text: "Add type", on: { click: () => { void add(); } } })));
}
/** A formula editor for one parameter: the person types, the server checks (syntax, names, units, cycles) and previews the value; the inspector lists what the
 *  parameter depends on and what depends on it. Nothing is saved from here. */
export function formulaPanel(store, family) {
    const names = family.parameters.map((p) => p.name);
    const param = h("select", { attrs: { "aria-label": "Parameter" } }, names.map((n) => h("option", { value: n, text: n })));
    const expr = h("input", { type: "text", placeholder: "for example width - 2 * frame", attrs: { "aria-label": "Formula", maxlength: 200, autocomplete: "off", list: `fn-${family.id}` } });
    const catalog = h("datalist", { attrs: { id: `fn-${family.id}` } });
    void store.guard(() => store.api.get("/formula/functions"), "Reading the formula functions").then((r) => {
        if (r)
            catalog.replaceChildren(...names.map((n) => h("option", { value: n })), ...r.functions.map((f) => h("option", { value: f.name, text: f.signature })));
    });
    const out = h("div", { attrs: { "aria-live": "polite" }, css: { display: "grid", gap: ".3rem" } });
    const inspector = h("div", { css: { display: "grid", gap: ".3rem" } });
    const draw = () => {
        const spec = family.parameters.find((p) => p.name === param.value);
        const dep = spec;
        expr.value = spec?.expression ?? "";
        replace(inspector, h("p", {}, h("strong", { text: "Depends on: " }), (dep.depends_on ?? []).join(", ") || "nothing"), h("p", {}, h("strong", { text: "Used by: " }), (dep.dependents ?? []).join(", ") || "nothing"), h("p", { class: "muted", text: `Scope: ${dep.scope ?? "instance"}` }));
        out.replaceChildren();
    };
    param.addEventListener("change", draw);
    let timer;
    const check = async () => {
        if (!expr.value.trim()) {
            out.replaceChildren(note("Type a formula to check it."));
            return;
        }
        const r = await store.guard(() => store.api.post("/families/formula", { family: family.id, parameter: param.value, expression: expr.value.trim() }), "Checking the formula");
        if (!r)
            return;
        if (r.ok) {
            replace(out, note(`Valid. Value now: ${shown(r.value)} ${r.unit ?? ""}  (${r.dimension ?? "number"})`, "ok"), h("p", {}, h("strong", { text: "Would depend on: " }), r.dependencies.join(", ") || "nothing"), h("p", {}, h("strong", { text: "Would change: " }), r.dependents.join(", ") || "nothing"));
        }
        else {
            const e = r.error;
            replace(out, note(`${e?.message ?? "The formula is not valid."}${e?.cycle ? ` Cycle: ${e.cycle.join(" → ")}` : ""}`, "warn"));
        }
    };
    expr.addEventListener("input", () => { window.clearTimeout(timer); timer = window.setTimeout(() => { void check(); }, 350); });
    draw();
    return h("section", { attrs: { "aria-label": "Formula editor" }, css: { display: "grid", gap: ".5rem" } }, h("h3", { text: "Formula editor and dependency inspector" }), h("div", { class: "prop-add" }, labelled("Parameter", param), labelled("Formula", expr), catalog), out, inspector, note("The check never changes the family. To keep a formula, put it in the parameter table above and apply."));
}
// ---------------------------------------------------------------------------------------------------------------------- scripts
export async function scriptsDialog(store) {
    const body = h("div", { css: { display: "grid", gap: "1rem" } });
    let data = null;
    let selected = "";
    let src = null;
    let lastRun = null;
    const reload = async () => {
        data = await store.guard(() => store.api.get("/scripts"), "Reading the scripts");
        if (data && !data.scripts.some((s) => s.id === selected))
            selected = data.scripts[0]?.id ?? "";
        src = selected ? await store.guard(() => store.api.get(`/scripts/${selected}`), "Reading the script") : null;
        draw();
    };
    const draw = () => {
        if (!data) {
            body.replaceChildren(note("The scripts could not be read."));
            return;
        }
        const d = data;
        const iso = h("div", { class: d.isolation.untrusted_execution ? "status-ok" : "status-warn", attrs: { role: "status" } }, h("strong", { text: `Isolation: ${d.isolation.level}. ` }), d.isolation.untrusted_execution ? "A script may run without approval; the operating system itself refuses files, network and new processes."
            : "No operating-system sandbox is available here. A script runs only after you approve its exact source (its hash), as a limited separate process, which is not a sandbox.", h("span", { class: "muted", text: ` ${d.isolation.mechanisms.join(", ")}.${d.isolation.notes.length ? ` ${d.isolation.notes.join(" ")}` : ""}` }));
        const list = h("ul", { class: "recent-list", attrs: { "aria-label": "Scripts" } }, d.scripts.map((s) => h("li", {}, h("button", { type: "button", attrs: { "aria-current": String(s.id === selected) },
            on: { click: () => { selected = s.id; lastRun = null; void reload(); } } }, h("strong", { text: s.name }), h("span", { class: "path", text: `version ${s.current} · ${s.versions.length} saved` })))));
        replace(body, iso, d.scripts.length ? list : note("The project has no scripts yet."), src ? editor(src, d) : null, newScript());
    };
    const newScript = () => {
        const name = h("input", { type: "text", placeholder: "Script name", attrs: { "aria-label": "New script name", maxlength: 80, autocomplete: "off" } });
        const make = async () => {
            if (!name.value.trim()) {
                toast("Give the script a name.", { kind: "info" });
                return;
            }
            const source = "def run(model, params):\n    doors = model.elements(category=\"door\")\n    return {\"count\": len(doors)}\n";
            const out = await store.run("script.add", { name: name.value.trim(), source, inputs: {}, outputs: { count: "integer" } });
            const id = out?.result?.id;
            if (id) {
                selected = id;
                name.value = "";
                void reload();
            }
        };
        return h("section", { attrs: { "aria-label": "New script" }, css: { display: "grid", gap: ".4rem" } }, h("h3", { text: "New script" }), h("div", { class: "prop-add" }, name, h("button", { class: "tool-inline", type: "button", text: "Create", on: { click: () => { void make(); } } })));
    };
    const editor = (s, d) => {
        const code = h("textarea", { value: s.source, attrs: { "aria-label": "Script source", spellcheck: "false", rows: 14, wrap: "off" }, css: { fontFamily: "monospace", width: "100%" } });
        const problems = h("div", { attrs: { "aria-live": "polite" } });
        const seed = h("input", { type: "number", value: "0", attrs: { "aria-label": "Seed", step: "1", min: "0" } });
        const inputs = h("textarea", { value: JSON.stringify(Object.fromEntries(Object.entries(s.inputs).filter(([, v]) => v.default === undefined).map(([k]) => [k, null])), null, 1), attrs: { "aria-label": "Inputs as JSON", rows: 3 }, css: { width: "100%", fontFamily: "monospace" } });
        const runOut = h("div", { attrs: { "aria-live": "polite" }, css: { display: "grid", gap: ".3rem" } });
        const check = async () => {
            const r = await store.guard(() => store.api.post("/scripts/check", { source: code.value }), "Checking the script");
            if (!r)
                return false;
            replace(problems, r.ok ? note("The source passes the syntax and safety scan.", "ok") : h("ul", {}, r.problems.map((p) => h("li", { class: "status-warn", text: `${p.line ? `line ${p.line}: ` : ""}${p.message}` }))));
            return r.ok;
        };
        const save = async () => { if (await check() && await store.run("script.update", { id: s.id, source: code.value }))
            void reload(); };
        const approve = async () => {
            if (!(await confirmDialog({ title: "Approve this version", message: `Approve version ${s.version} of “${s.name}”?`, confirmLabel: "Approve",
                detail: `You are approving exactly this source (hash ${s.sha256.slice(0, 12)}…). Any edit makes a new version that needs a new approval.` })))
                return;
            if (await store.run("script.approve", { id: s.id, version: s.version, sha256: s.sha256 }))
                void reload();
        };
        const run = async () => {
            let parsed = {};
            try {
                parsed = inputs.value.trim() ? JSON.parse(inputs.value) : {};
            }
            catch {
                toast("The inputs are not valid JSON.", { kind: "error" });
                return;
            }
            const r = await store.guard(() => store.api.post("/scripts/run", { script: s.id, version: s.version, inputs: parsed, seed: Number(seed.value) || 0 }), "Running the script");
            if (!r)
                return;
            lastRun = r;
            replace(runOut, r.status === "ok" ? note(`Preview ready (${r.isolation}, ${r.duration_s ?? 0} s, seed ${r.seed}). The project has not changed.`, "ok") : note(`${r.status}: ${r.error ?? ""}`, "warn"), r.outputs ? h("pre", { class: "report-text", text: JSON.stringify(r.outputs, null, 1) }) : null, r.log ? h("pre", { class: "report-text", text: r.log }) : null);
        };
        const propose = async () => {
            if (!lastRun || lastRun.status !== "ok") {
                toast("Run the script first.", { kind: "info" });
                return;
            }
            const family = window.prompt("Family id to receive the outputs");
            const typeName = family ? window.prompt("Family type to change") : null;
            const output = Object.keys(lastRun.outputs ?? {})[0];
            const parameter = output ? window.prompt(`Parameter that receives “${output}”`, output) : null;
            if (!family || !typeName || !output || !parameter)
                return;
            const r = await store.guard(() => store.api.post("/scripts/propose", { run: lastRun?.id, targets: [{ output, family, type: typeName, parameter }] }), "Making the proposal");
            if (r)
                toast("A proposal was made. Review and approve it in the assistant; nothing has changed yet.", { kind: "ok", timeoutMs: 5000 });
        };
        const v = d.scripts.find((x) => x.id === s.id)?.versions.find((x) => x.version === s.version);
        return h("section", { attrs: { "aria-label": `Script ${s.name}` }, css: { display: "grid", gap: ".5rem" } }, h("h3", { text: `${s.name} · version ${s.version}${v?.approved ? " · approved" : ""}` }), code, problems, h("div", { class: "prop-add" }, h("button", { class: "tool-inline", type: "button", text: "Check", on: { click: () => { void check(); } } }), h("button", { class: "tool-inline", type: "button", text: "Save as new version", on: { click: () => { void save(); } } }), h("button", { class: "tool-inline", type: "button", text: "Approve this version", disabled: !!v?.approved, on: { click: () => { void approve(); } } }), h("button", { class: "tool-inline", type: "button", text: "Delete script", on: { click: () => { void store.run("script.delete", { id: s.id }).then((ok) => { if (ok) {
                    selected = "";
                    void reload();
                } }); } } })), h("details", {}, h("summary", { text: `Declared inputs and outputs` }), h("pre", { class: "report-text", text: `inputs: ${JSON.stringify(s.inputs)}\noutputs: ${JSON.stringify(s.outputs)}` })), labelled("Inputs (JSON; lengths in metres)", inputs), labelled("Seed", seed), h("div", { class: "prop-add" }, h("button", { class: "tool-inline", type: "button", text: "Run (preview)", on: { click: () => { void run(); } } }), h("button", { class: "tool-inline", type: "button", text: "Make a proposal from the last run", on: { click: () => { void propose(); } } })), runOut, note("A script reads a copy of the model and returns values. A run never changes the project; a proposal is reviewed and approved in the assistant, and applies as one undoable change."));
    };
    await reload();
    const handle = openDialog({ title: "Python scripts", size: "wide", noCancel: true, body, actions: [{ label: "Close", kind: "primary", run: () => true }] });
    void handle.result;
}
// ------------------------------------------------------------------------------------------------------------- stair design
export function stairDesignDialog(store) {
    const height = h("input", { type: "number", value: store.levels.length > 1 ? String(Math.round(((store.levels[1]?.elevation ?? 3.2) - (store.levels[0]?.elevation ?? 0)) * 1000)) : "3200", attrs: { "aria-label": "Total rise in millimetres", step: "any", min: "100" } });
    const avail = h("input", { type: "number", placeholder: "Available length (mm), optional", attrs: { "aria-label": "Available length in millimetres", step: "any" } });
    const out = h("div", { attrs: { "aria-live": "polite" }, css: { display: "grid", gap: ".3rem" } });
    const preview = async () => {
        const r = await store.guard(() => store.api.post("/stairs/design", { height: Number(height.value), ...(avail.value ? { available: Number(avail.value) } : {}) }), "Designing the stair");
        if (!r)
            return;
        const c = r.chosen;
        if (!c) {
            replace(out, note(`No stair fits. ${(r.reasons ?? []).join(" ")}`, "warn"));
            return;
        }
        replace(out, note(`${c.risers} risers of ${(c.riser * 1000).toFixed(1)} mm, tread ${(c.tread * 1000).toFixed(0)} mm, 2R+T = ${(c.comfort * 1000).toFixed(0)} mm; ${c.flights.length} flight(s) (${c.flights.join(" + ")}), ${c.landings} landing(s).`, "ok"), h("ul", {}, c.checks.map((k) => h("li", { class: k.ok ? "" : "status-warn", text: `${k.name}: ${shown(k.value)} (limit ${Array.isArray(k.limit) ? k.limit.map(shown).join("–") : shown(k.limit)}) ${k.ok ? "✓" : "✗"}` }))), note(`Riser interval for this rise: ${r.riser_interval[0]} to ${r.riser_interval[1]} risers. The limits are design defaults you can change, not a building code.`));
    };
    openDialog({ title: "Stair design", size: "narrow", focus: () => height, noCancel: true,
        body: h("div", { css: { display: "grid", gap: ".6rem" } }, labelled("Total rise (mm)", height), labelled("Available length (mm)", avail), h("button", { class: "tool-inline", type: "button", text: "Preview design", on: { click: () => { void preview(); } } }), out, note("To create the stair, use the model.design_stair command from the assistant or draw one with the Stair tool; this preview creates nothing.")),
        actions: [{ label: "Close", kind: "primary", run: () => true }] });
}
// ------------------------------------------------------------------------------------------------------------------- optimization
export async function optimizeDialog(store) {
    const fams = await store.guard(() => store.api.get("/families"), "Reading the families");
    if (!fams || !fams.families.length) {
        toast("Define a family first.", { kind: "info" });
        return;
    }
    const family = h("select", { attrs: { "aria-label": "Family" } }, fams.families.map((f) => h("option", { value: f.id, text: f.name })));
    const typeSel = h("select", { attrs: { "aria-label": "Type to change" } });
    const vars = h("textarea", { attrs: { "aria-label": "Variables as JSON", rows: 4 }, css: { width: "100%", fontFamily: "monospace" }, value: "{\n \"width\": {\"min\": 700, \"max\": 1400}\n}" });
    const objective = h("input", { type: "text", value: "", placeholder: "Objective, for example width * height", attrs: { "aria-label": "Objective", autocomplete: "off" } });
    const sense = h("select", { attrs: { "aria-label": "Minimise or maximise" } }, h("option", { value: "min", text: "Minimise" }), h("option", { value: "max", text: "Maximise" }));
    const cons = h("textarea", { attrs: { "aria-label": "Constraints, one per line", rows: 3 }, css: { width: "100%", fontFamily: "monospace" }, placeholder: "clear_width >= min_clear" });
    const consts = h("textarea", { attrs: { "aria-label": "Constants as JSON", rows: 2 }, css: { width: "100%", fontFamily: "monospace" }, value: "{}" });
    const seed = h("input", { type: "number", value: "0", attrs: { "aria-label": "Seed", step: "1", min: "0" } });
    const count = h("input", { type: "number", value: "4", attrs: { "aria-label": "Number of alternatives", step: "1", min: "1", max: "10" } });
    const out = h("div", { attrs: { "aria-live": "polite" }, css: { display: "grid", gap: ".3rem" } });
    const refreshTypes = () => {
        const f = fams.families.find((x) => x.id === family.value);
        const names = (f?.types ?? []).map((t) => t.name);
        typeSel.replaceChildren(...names.map((n) => h("option", { value: n, text: n })));
    };
    family.addEventListener("change", refreshTypes);
    refreshTypes();
    const body = () => {
        try {
            return { family: family.value, type: typeSel.value || undefined, variables: JSON.parse(vars.value), constants: JSON.parse(consts.value || "{}"), seed: Number(seed.value) || 0,
                objectives: [{ expression: objective.value.trim(), sense: sense.value }], constraints: cons.value.split("\n").map((l) => l.trim()).filter(Boolean) };
        }
        catch {
            toast("Variables and constants must be valid JSON.", { kind: "error" });
            return null;
        }
    };
    const show = (r) => {
        const p = r.proposal;
        replace(out, note(`${STATUS_WORDS[r.status] ?? r.status}. ${r.evaluations} evaluations, ${r.feasible_found} feasible, seed ${r.seed}. ${r.note}`, r.status === "converged" || r.status === "budget_exhausted" ? "ok" : "warn"), p ? h("pre", { class: "report-text", text: `${p.feasible ? "Proposed" : "Least-violating (not feasible)"}: ${JSON.stringify(p.point)}\nobjective: ${p.objectives.map(shown).join(", ")}${p.buildable ? "" : `\nnot buildable: ${p.why_not_buildable ?? ""}`}` }) : null, r.pareto.length ? note(`${r.pareto.length} non-dominated point(s) on the Pareto front.`) : null);
    };
    const run = async () => { const b = body(); if (!b)
        return; const r = await store.guard(() => store.api.post("/optimize/family", b), "Searching"); if (r)
        show(r); };
    const propose = async () => {
        const b = body();
        if (!b)
            return;
        const r = await store.guard(() => store.api.post("/optimize/family/propose", b), "Making the proposal");
        if (!r)
            return;
        show(r.result);
        toast(r.message ? "A proposal was made. Review and approve it in the assistant; nothing has changed yet." : "No feasible, buildable point, so no proposal was made.", { kind: r.message ? "ok" : "info", timeoutMs: 5000 });
    };
    const alternatives = async () => {
        const b = body();
        if (!b)
            return;
        const r = await store.guard(() => store.api.post("/optimize/family/alternatives", { ...b, count: Number(count.value) || 4, propose: true }), "Looking for alternatives");
        if (!r)
            return;
        const a = r.result;
        replace(out, note(a.status === "ok" || a.status === "fewer_than_requested" ? `${a.alternatives.length} of ${a.requested} requested alternative(s) from ${a.evaluations} candidates, seed ${a.seed}. ${a.note}` : `${a.status}: no alternative satisfies the constraints.`, a.alternatives.length ? "ok" : "warn"), h("ul", {}, a.alternatives.map((x) => h("li", { class: x.buildable ? "" : "status-warn",
            text: `${x.type_name ?? "not buildable"}: ${JSON.stringify(x.point)} → ${x.objectives.map(shown).join(", ")} (rank ${x.rank} of the feasible candidates${x.distance === null ? "" : `, distance ${shown(x.distance)}`})${x.buildable ? "" : ` — ${x.why_not_buildable ?? ""}`}` }))));
        if (r.message)
            toast("A proposal was made. Review and approve it in the assistant; nothing has changed yet.", { kind: "ok", timeoutMs: 5000 });
    };
    openDialog({ title: "Parametric optimization", size: "wide", noCancel: true, focus: () => objective,
        body: h("div", { css: { display: "grid", gap: ".6rem" } }, h("div", { class: "prop-add" }, labelled("Family", family), labelled("Type to change", typeSel)), labelled("Variables (display units)", vars), h("div", { class: "prop-add" }, labelled("Objective", objective), labelled("Sense", sense)), labelled("Constraints (formulas compared with <=, >=, <, >)", cons), labelled("Constants ({name: {type, value}})", consts), labelled("Seed", seed), labelled("Alternatives to suggest", count), h("div", { class: "prop-add" }, h("button", { class: "tool-inline", type: "button", text: "Search", on: { click: () => { void run(); } } }), h("button", { class: "tool-inline", type: "button", text: "Search and make a proposal", on: { click: () => { void propose(); } } }), h("button", { class: "tool-inline", type: "button", text: "Suggest alternatives", on: { click: () => { void alternatives(); } } })), out, note("The search evaluates the family's own formulas and bounds. It is deterministic for a given seed, proves nothing about optimality, and never changes the project; applying a result is a proposal you approve.")),
        actions: [{ label: "Close", kind: "primary", run: () => true }] });
}
