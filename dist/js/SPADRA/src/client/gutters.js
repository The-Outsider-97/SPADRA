/** Gutters and rainwater drainage: put gutters (and downpipes) on the eaves of the selected roof, add a downpipe to a gutter, and see the rain each
 *  gutter and downpipe takes. The server does all the work; these dialogs only gather the choices. */
import { clear, h, labelled, s } from "../../../tools/web/src/dom.js";
import { openDialog, toast } from "../../../tools/web/src/ui.js";
/** The nominal sizes a type starts from (mm); the server holds the same table and checks every value, and each can be changed afterwards. */
const TYPES = [
    { key: "half_round", label: "Half-round", width: 125, depth: 62.5 },
    { key: "ogee", label: "Ogee (K-style)", width: 125, depth: 100 },
    { key: "box", label: "Box", width: 100, depth: 100 },
    { key: "fascia", label: "Fascia", width: 115, depth: 90 },
];
const MATERIALS = ["Zinc", "Aluminium", "Steel", "Copper", "PVC"];
function selectedOf(store, category) {
    const sel = store.selection;
    if (sel?.kind !== "model" || sel.ids.length !== 1)
        return null;
    return store.properties?.category === category ? sel.ids[0] : null;
}
async function drainage(store, roof) {
    try {
        return await store.api.post("/drainage", roof ? { roof } : {});
    }
    catch (e) {
        toast(e instanceof Error ? e.message : "Could not read the drainage.", { kind: "error" });
        return null;
    }
}
const FALLS = [["", "Automatic (a loop: every corner; a chain: its end)"], ["end", "End of the run"], ["start", "Start of the run"], ["middle", "Middle of the run"], ["corners", "Every corner"]];
const SVG_PAD = 400;
/** A small plan of the roof's outline with the gutters the dialog would make: chosen eaves are clickable, new pieces are filled (their mitres are the real cuts),
 *  low points are marked and downpipes drawn where they would stand. The server draws nothing; it returns the outlines and this paints them. */
function previewPlan(info, preview, outline, chosen, free, toggle) {
    const pts = outline ?? [];
    const all = [...pts, ...(preview?.runs.flatMap((r) => r.segments.flatMap((sg) => sg.polygon)) ?? [])];
    const xs = all.map((p) => p[0]), ys = all.map((p) => p[1]);
    const x0 = Math.min(...xs, 0) - SVG_PAD, x1 = Math.max(...xs, 1) + SVG_PAD, y0 = Math.min(...ys, 0) - SVG_PAD, y1 = Math.max(...ys, 1) + SVG_PAD;
    const svg = s("svg", { attrs: { viewBox: `${x0} ${-y1} ${x1 - x0} ${y1 - y0}`, role: "img", "aria-label": "Plan of the roof with the gutters that would be made", preserveAspectRatio: "xMidYMid meet" }, css: { width: "100%", height: "16rem", "max-height": "40vh" } });
    const g = s("g", { attrs: { transform: "scale(1,-1)" } }); // model y runs up, SVG y runs down
    const stroke = Math.max(1, (x1 - x0) / 400);
    g.append(s("polygon", { attrs: { points: pts.map((p) => p.join(",")).join(" "), fill: "none", stroke: "currentColor", "stroke-width": stroke, "stroke-opacity": 0.55 } }));
    for (const r of preview?.runs ?? []) {
        for (const sg of r.segments)
            g.append(s("polygon", { attrs: { points: sg.polygon.map((p) => p.join(",")).join(" "), fill: sg.new ? "var(--accent, #2f6fde)" : "currentColor", "fill-opacity": sg.new ? 0.45 : 0.2, stroke: "var(--accent, #2f6fde)", "stroke-width": stroke } }));
        for (const o of r.outlets)
            g.append(s("circle", { attrs: { cx: o.at[0], cy: o.at[1], r: Math.max(stroke * 3, 40), fill: "var(--accent, #2f6fde)", stroke: "currentColor", "stroke-width": stroke } }));
    }
    pts.forEach((a, i) => {
        const e = info.eaves.find((x) => x.edge === i);
        if (!e || !free.has(i))
            return;
        const b = pts[(i + 1) % pts.length];
        const line = s("line", { attrs: { x1: a[0], y1: a[1], x2: b[0], y2: b[1], stroke: chosen.has(i) ? "var(--accent, #2f6fde)" : "currentColor", "stroke-opacity": chosen.has(i) ? 0.9 : 0.35, "stroke-width": stroke * 5, "stroke-linecap": "round" }, css: { cursor: "pointer" } });
        line.addEventListener("click", () => toggle(i));
        g.append(line);
    });
    svg.append(g);
    return svg;
}
/** Gutters on the eaves of the selected roof. */
export async function gutterDialog(store) {
    if (!store.project)
        return;
    const roof = selectedOf(store, "roof");
    if (!roof) {
        toast("Select a roof first (click it in the 3D view or in the browser), then choose Gutter.", { kind: "info" });
        return;
    }
    const info = (await drainage(store, roof))?.roofs[0];
    if (!info)
        return;
    const free = info.eaves.filter((e) => !e.gutter);
    if (!free.length) {
        toast("Every eave of this roof already has a gutter. Select a gutter to change it in Properties.", { kind: "info" });
        return;
    }
    const number = (value, step = "any", min) => h("input", { type: "number", value, step, ...(min ? { min } : {}), attrs: { autocomplete: "off" } });
    const kind = h("select", {}, ...TYPES.map((t) => h("option", { value: t.key, text: t.label })));
    const width = number("125", "1", "30"), depth = number("62.5", "0.5", "20");
    const fall = number("3", "0.5", "0");
    const fallTo = h("select", {}, ...FALLS.map(([v, l]) => h("option", { value: v, text: l })));
    const material = h("select", {}, ...MATERIALS.map((m) => h("option", { value: m, text: m })));
    const gap = number("0", "5", "0"), drop = number("20", "5", "0");
    const extStart = number("0", "10"), extEnd = number("0", "10");
    const endKinds = () => h("select", {}, h("option", { value: "cap", text: "Closed with a stop end" }), h("option", { value: "open", text: "Open" }));
    const endStart = endKinds(), endEnd = endKinds();
    const connect = h("input", { type: "checkbox", checked: true, attrs: { "aria-label": "Join gutters on neighbouring eaves with mitred corners" } });
    const pipe = h("input", { type: "checkbox", checked: true, attrs: { "aria-label": "Add a downpipe at each low point" } });
    const diameter = number("80", "5", "20");
    const chosen = new Set(free.map((e) => e.edge));
    const freeSet = new Set(free.map((e) => e.edge));
    const boxes = free.map((e) => ({ edge: e.edge, box: h("input", { type: "checkbox", checked: true, attrs: { "aria-label": `Eave ${e.edge + 1}` } }) }));
    const status = h("p", { class: "muted", attrs: { role: "status", "aria-live": "polite" } });
    const plan = h("div", {});
    let latest = null;
    let outline = null;
    let seq = 0;
    const request = () => {
        const out = { roof, edges: [...chosen].sort((a, b) => a - b), gutter: kind.value, width: Number(width.value), depth: Number(depth.value), fall: Number(fall.value),
            material: material.value, gap: Number(gap.value), drop: Number(drop.value), ext_start: Number(extStart.value), ext_end: Number(extEnd.value), end_start: endStart.value, end_end: endEnd.value,
            connect: connect.checked, diameter: Number(diameter.value), downpipe: pipe.checked };
        if (fallTo.value)
            out.fall_to = fallTo.value;
        return out;
    };
    const paint = () => {
        clear(plan);
        // With downpipes off, low points remain defined but no outlet is drawn.
        const shown = !pipe.checked && latest ? { ...latest,
            runs: latest.runs.map((r) => ({ ...r, outlets: [] })) } : latest;
        plan.append(previewPlan(info, shown, outline, chosen, freeSet, (edge) => { const b = boxes.find((x) => x.edge === edge); if (b) {
            b.box.checked = !b.box.checked;
            sync();
        } }));
    };
    const refresh = async () => {
        const mine = ++seq;
        if (!chosen.size) {
            latest = null;
            status.textContent = "Tick at least one eave.";
            paint();
            return;
        }
        try {
            const out = await store.api.post("/gutters/preview", request());
            if (mine !== seq)
                return; // a newer request is on its way
            latest = out.problems.length ? null : out;
            outline = out.outline ?? outline;
            const runs = out.runs;
            status.textContent = out.problems.length ? out.problems.join(" ") : `${runs.length} run${runs.length === 1 ? "" : "s"}: ${runs.map((r) => `${r.segments.length} piece${r.segments.length === 1 ? "" : "s"}${r.closed ? " in a closed loop" : ""}, ${r.length_m.toFixed(2)} m`).join("; ")}. ${pipe.checked ? runs.reduce((n, r) => n + r.outlets.length, 0) : 0} downpipe${pipe.checked && runs.reduce((n, r) => n + r.outlets.length, 0) === 1 ? "" : "s"} at the low points.`;
            status.classList.toggle("error", out.problems.length > 0);
        }
        catch (e) {
            if (mine === seq) {
                latest = null;
                status.textContent = e instanceof Error ? e.message : "Could not preview.";
            }
        }
        paint();
    };
    let timer;
    const sync = () => {
        chosen.clear();
        for (const b of boxes)
            if (b.box.checked)
                chosen.add(b.edge);
        window.clearTimeout(timer);
        timer = window.setTimeout(() => { void refresh(); }, 120);
        paint();
    };
    kind.addEventListener("change", () => { const t = TYPES.find((x) => x.key === kind.value); if (t) {
        width.value = String(t.width);
        depth.value = String(t.depth);
    } sync(); });
    pipe.addEventListener("change", () => { diameter.disabled = !pipe.checked; sync(); });
    for (const el of [width, depth, fall, fallTo, material, gap, drop, extStart, extEnd, endStart, endEnd, connect, diameter, ...boxes.map((b) => b.box)])
        el.addEventListener("change", sync);
    const two = (a, b) => h("div", { css: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.75rem" } }, a, b);
    const handle = openDialog({
        title: `Gutters on ${info.name}`, size: "wide", focus: () => kind,
        body: h("div", { css: { display: "grid", gridTemplateColumns: "minmax(16rem, 1fr) minmax(16rem, 1fr)", gap: "1.25rem", alignItems: "start" } }, h("div", { css: { display: "grid", gap: "0.9rem" } }, labelled("Gutter type", kind, { hint: "Only the starting shape and size: everything can be changed afterwards in Properties, including the type." }), two(labelled("Width (mm)", width), labelled("Depth (mm)", depth)), two(labelled("Fall (mm per m)", fall), labelled("Material", material)), labelled("Falls towards", fallTo, { hint: "Where the run is lowest. Downpipes are placed there." }), two(labelled("Out from the eave line (mm)", gap), labelled("Below the eave (mm)", drop)), h("fieldset", { css: { display: "grid", gap: "0.35rem", border: "1px solid var(--line, #8884)", padding: "0.5rem 0.75rem" } }, h("legend", { text: "Eaves (tick here or click them in the plan)" }), ...boxes.map(({ edge, box }) => h("label", { css: { display: "flex", gap: "0.5rem", alignItems: "center" } }, box, `Eave ${edge + 1} · ${(info.eaves.find((e) => e.edge === edge)?.length_m ?? 0).toFixed(2)} m`)), h("label", { css: { display: "flex", gap: "0.5rem", alignItems: "center" } }, connect, "Join neighbouring eaves into one run, mitred at the corners")), h("fieldset", { css: { display: "grid", gap: "0.5rem", border: "1px solid var(--line, #8884)", padding: "0.5rem 0.75rem" } }, h("legend", { text: "Free ends of a run" }), two(labelled("First end", endStart), labelled("Extension (mm)", extStart)), two(labelled("Last end", endEnd), labelled("Extension (mm)", extEnd))), h("label", { css: { display: "flex", gap: "0.5rem", alignItems: "center" } }, pipe, "Add a downpipe at each low point"), labelled("Downpipe diameter (mm)", diameter)), h("div", { css: { display: "grid", gap: "0.5rem", position: "sticky", top: "0" } }, plan, status, h("p", { class: "muted", text: "Preview of what will be made. Brackets and preformed corner units are not modelled: a corner is two mitred pieces." }))),
        actions: [{ label: "Add gutters", kind: "primary", run: async () => {
                    if (!chosen.size) {
                        toast("Tick at least one eave.", { kind: "info" });
                        return undefined;
                    }
                    window.clearTimeout(timer);
                    await refresh(); // what is built is exactly what the preview last showed
                    if (!latest) {
                        toast(status.textContent || "These gutters cannot be made.", { kind: "error" });
                        return undefined;
                    }
                    const out = await store.run("model.add_gutters", { ...request(), downpipe: pipe.checked });
                    if (!out)
                        return undefined;
                    const ids = out.result?.ids;
                    if (ids?.length)
                        await store.select({ kind: "model", ids });
                    return true;
                } }],
    });
    paint();
    void refresh();
    await handle.result;
    window.clearTimeout(timer);
}
/** One more downpipe under the selected gutter. */
export async function downpipeDialog(store) {
    if (!store.project)
        return;
    const gutter = selectedOf(store, "gutter");
    if (!gutter) {
        toast("Select a gutter first, then choose Downpipe.", { kind: "info" });
        return;
    }
    const number = (value, step = "any", min) => h("input", { type: "number", value, step, ...(min ? { min } : {}), attrs: { autocomplete: "off" } });
    const offset = number("0.5", "0.05", "0"), diameter = number("80", "5", "20");
    const handle = openDialog({
        title: "Add a downpipe", size: "narrow", focus: () => offset,
        body: h("div", { css: { display: "grid", gap: "0.9rem" } }, labelled("Distance along the gutter (m)", offset, { hint: "From the start of the eave. Drag the downpipe in the plan, or edit this in Properties, to move it later." }), labelled("Diameter (mm)", diameter), h("p", { class: "muted", text: "It runs down to 300 mm above the lowest level; change the discharge height in Properties." })),
        actions: [{ label: "Add downpipe", kind: "primary", run: async () => {
                    const out = await store.run("model.add_downpipe", { gutter, offset: Number(offset.value), diameter: Number(diameter.value) });
                    if (!out)
                        return undefined;
                    const id = out.result?.id;
                    if (id)
                        await store.select({ kind: "model", ids: [id] });
                    return true;
                } }],
    });
    await handle.result;
}
/** What each roof drains: area, flow and downpipes per gutter. */
export async function drainageDialog(store) {
    if (!store.project)
        return;
    const data = await drainage(store);
    if (!data)
        return;
    if (!data.roofs.length) {
        toast("There is no roof yet. Draw a roof, then add gutters to its eaves.", { kind: "info" });
        return;
    }
    const num = (v, d = 2) => v.toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d });
    const rows = [];
    for (const r of data.roofs) {
        rows.push(h("tr", {}, h("th", { attrs: { scope: "row", colspan: "5" }, text: `${r.name} · ${num(r.plan_area_m2)} m² in plan · ${num(r.drained_m2)} m² drained${r.undrained_m2 > 1e-6 ? ` · ${num(r.undrained_m2)} m² with no gutter` : ""}` })));
        for (const g of r.gutters) {
            const pipes = g.downpipes.length ? g.downpipes.map((p) => `${p.name} Ø${num(p.diameter_mm, 0)} · ${num(p.flow_l_s, 3)} l/s`).join("; ") : "no downpipe";
            rows.push(h("tr", {}, h("td", { text: g.name }), h("td", { class: "num", text: `${num(g.length_m)} m` }), h("td", { class: "num", text: `${num(g.catchment_m2)} m²` }), h("td", { class: "num", text: `${num(g.flow_l_s, 3)} l/s` }), h("td", { text: pipes })));
        }
    }
    openDialog({ title: "Rainwater drainage", size: "wide", noCancel: true, cancelLabel: "Close",
        body: h("div", { css: { display: "grid", gap: "0.75rem" } }, h("table", { class: "schedule", attrs: { "aria-label": "Rainwater drainage" } }, h("thead", {}, h("tr", {}, ...["Gutter", "Length", "Roof area", "Design flow", "Downpipes"].map((t) => h("th", { text: t })))), h("tbody", {}, rows)), ...data.roofs.flatMap((r) => r.runs.map((run) => h("section", { css: { display: "grid", gap: "0.25rem" } }, h("h3", { text: `${r.name}: run of ${run.gutters.length} gutter${run.gutters.length === 1 ? "" : "s"}${run.closed ? " (closed loop)" : ""} · ${num(run.length_m)} m · ${num(run.flow_l_s, 3)} l/s` }), h("ul", {}, ...run.lows.map((l) => h("li", { text: `Low point ${num(l.at_m)} m along the run: ${num(l.catchment_m2)} m² · ${num(l.flow_l_s, 3)} l/s · ${l.served ? `${l.downpipes.length} downpipe${l.downpipes.length === 1 ? "" : "s"}` : "no downpipe, the water would stand"}` }))), ...run.problems.map((t) => h("p", { class: "error", text: t }))))), h("p", { class: "muted", text: data.note })),
        actions: [{ label: "Done", kind: "primary", run: () => true }] });
}
