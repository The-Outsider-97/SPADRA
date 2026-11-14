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
const CONNECTIONS = [
    ["A1", "A1 Square cut, near-face bearing"], ["A2", "A2 Square cut, centreline bearing"], ["A3", "A3 Square cut, far-face bearing"],
    ["B1", "B1 Plumb cut, near face"], ["B2", "B2 Plumb cut, centreline"], ["B3", "B3 Plumb cut, far face"],
    ["C1", "C1 Horizontal cut, far-face edge"], ["C2", "C2 Horizontal cut, projecting"], ["C3", "C3 Horizontal cut, centreline-referenced"], ["C4", "C4 Horizontal cut, flush with the far face"], ["C5", "C5 Horizontal cut, near-face edge"],
    ["D1", "D1 Raised heel"], ["D2", "D2 Plumb cut, near-face butt abutment"], ["D3", "D3 Embedded, plumb cut"], ["custom", "Custom"],
];
/** How the selected gable or shed roof ends at an eave: the 14 connection details A1 to D3 as presets of a cut across the eave (cut orientation, which wall line the cut
 *  stands over, offset, heel, layers). The server computes the section and the findings for every change; Apply makes exactly what the preview last showed. */
export async function roofEndDialog(store) {
    if (!store.project)
        return;
    const roof = selectedOf(store, "roof");
    if (!roof) {
        toast("Select a gable or shed roof first, then choose End connections.", { kind: "info" });
        return;
    }
    const props = await store.api.post("/properties", { model: roof });
    const edges = [...new Set((props.groups.find((g) => g.title === "End connections")?.rows ?? []).map((r) => /^end:(\d+):/.exec(r.key ?? "")?.[1]).filter((x) => !!x).map(Number))];
    if (!edges.length) {
        toast("End connections are defined for gable and shed roofs.", { kind: "info" });
        return;
    }
    const num = (value, step) => h("input", { type: "number", value, step, attrs: { autocomplete: "off" } });
    const pick = (options) => h("select", {}, ...options.map(([v, l]) => h("option", { value: v, text: l })));
    const edge = pick(edges.map((e) => [String(e), `Eave ${e + 1}`]));
    const type = pick(CONNECTIONS);
    const cut = pick([["square", "Square to the slope"], ["plumb", "Plumb (vertical)"], ["horizontal", "Horizontal seat"]]);
    const ref = pick([["near", "Near face (towards the ridge)"], ["centre", "Centre line"], ["far", "Far face (outer)"]]);
    const anchor = pick([["underside", "Lower corner of the cut"], ["mid", "Middle of the cut (reference axis)"], ["top", "Upper corner of the cut"]]);
    const layers = pick([["through", "One cut through all layers"], ["own", "Each layer cut on its own"]]);
    const offset = num("0", "5"), heel = num("0", "5");
    const seat = h("input", { type: "checkbox", checked: true, attrs: { "aria-label": "Move the roof so the corner bears as designed" } });
    const status = h("div", { class: "muted", attrs: { role: "status", "aria-live": "polite" } });
    const picture = h("div", {});
    let latest = null, seq = 0, loading = true;
    const request = (mode) => mode === "current" ? { id: roof, edge: Number(edge.value) } : mode === "full"
        ? { id: roof, edge: Number(edge.value), type: type.value, cut: cut.value, ref: ref.value, anchor: anchor.value, offset: Number(offset.value) || 0, heel: Number(heel.value) || 0, layers: layers.value }
        : { id: roof, edge: Number(edge.value), type: type.value };
    const draw = (pv) => {
        clear(picture);
        const pts = pv.slab.map((q) => [q[0], q[1]]);
        seat.disabled = !pv.wall; // there is nothing to seat the roof on when no wall carries this eave
        const xs = [...pts.map((q) => q[0]), pv.wall?.near ?? 0, pv.wall?.far ?? 0], zs = [...pts.map((q) => q[1]), -0.9, 0.6];
        const x0 = Math.min(...xs) - 0.1, x1 = Math.max(...xs) + 0.1, z0 = Math.min(...zs), z1 = Math.max(...zs);
        const svg = s("svg", { attrs: { viewBox: `${x0} ${-z1} ${x1 - x0} ${z1 - z0}`, role: "img", "aria-label": "Section across the eave: the roof end on its wall", preserveAspectRatio: "xMidYMid meet" }, css: { width: "100%", maxHeight: "22rem" } });
        const k = (x1 - x0) / 300;
        const g = s("g", { attrs: { transform: "scale(1,-1)" } });
        if (pv.wall)
            g.append(s("rect", { attrs: { x: pv.wall.near, y: pv.wall.base, width: pv.wall.far - pv.wall.near, height: pv.wall.top - pv.wall.base, fill: "currentColor", "fill-opacity": 0.18, stroke: "currentColor", "stroke-width": k, "stroke-opacity": 0.5 } }));
        g.append(s("polygon", { attrs: { points: pts.map((q) => q.join(",")).join(" "), fill: "var(--accent, #2f6fde)", "fill-opacity": 0.28, stroke: "currentColor", "stroke-width": k } }));
        const a = pts[1], b = pts[2];
        g.append(s("line", { attrs: { x1: a[0], y1: a[1], x2: b[0], y2: b[1], stroke: "#d9962b", "stroke-width": k * 3 } })); // the cut face
        g.append(s("line", { attrs: { x1: pv.q_ref, y1: z0, x2: pv.q_ref, y2: z1, stroke: "#d33", "stroke-width": k, "stroke-dasharray": `${k * 4} ${k * 4}` } }));
        g.append(s("circle", { attrs: { cx: pv.anchor[0], cy: pv.anchor[1], r: k * 4, fill: "#d33" } }));
        svg.append(g);
        picture.append(svg);
    };
    const refresh = async (mode) => {
        const mine = ++seq;
        try {
            const pv = await store.api.post("/roof/end/preview", request(mode));
            if (mine !== seq)
                return;
            latest = pv;
            if (mode !== "full") { // a preset (or the eave) was chosen: show its parameters
                loading = true;
                type.value = pv.end.type;
                cut.value = pv.end.cut;
                ref.value = pv.end.ref;
                anchor.value = pv.end.anchor;
                layers.value = pv.end.layers;
                offset.value = String(Math.round(pv.end.offset * 1000));
                heel.value = String(Math.round(pv.end.heel * 1000));
                loading = false;
            }
            draw(pv);
            clear(status);
            status.append(h("div", { text: `Reference: ${pv.reference}. ${pv.wall ? `Carried by ${pv.wall.name}.` : "No wall under this eave."} Cut face ${pv.perpendicular_thickness_mm.toFixed(0)} mm square to the slope.` }));
            if (pv.gap_mm !== null)
                status.append(h("div", { text: `Roof corner ${pv.gap_mm.toFixed(0)} mm above the wall top; seating moves the roof ${pv.seat_mm?.toFixed(0)} mm. Projection beyond the far face ${pv.projection_mm.toFixed(0)} mm.` }));
            for (const n of pv.notes)
                status.append(h("div", { class: n.severity === "warning" ? "error" : "", text: `${n.severity === "warning" ? "⚠ " : ""}${n.message}` }));
        }
        catch (e) {
            if (mine === seq) {
                latest = null;
                clear(status);
                status.append(h("div", { class: "error", text: e instanceof Error ? e.message : "Could not preview." }));
            }
        }
    };
    let timer;
    const later = (mode) => { if (loading)
        return; window.clearTimeout(timer); timer = window.setTimeout(() => { void refresh(mode); }, 100); };
    const hasEnd = (e) => { const row = props.groups.find((g) => g.title === "End connections")?.rows.find((r) => r.key === `end:${e}:type`); return !!row?.value && row.value !== "default"; };
    edge.addEventListener("change", () => later(hasEnd(Number(edge.value)) ? "current" : "preset"));
    type.addEventListener("change", () => { loading = false; later("preset"); });
    for (const el of [cut, ref, anchor, layers, offset, heel])
        el.addEventListener("change", () => later("full"));
    const two = (a, b) => h("div", { css: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.75rem" } }, a, b);
    const handle = openDialog({
        title: "Roof end connections", size: "wide", focus: () => type,
        body: h("div", { css: { display: "grid", gridTemplateColumns: "minmax(16rem, 1fr) minmax(18rem, 1.2fr)", gap: "1.25rem", alignItems: "start" } }, h("div", { css: { display: "grid", gap: "0.8rem" } }, labelled("Eave", edge), labelled("Connection", type, { hint: "A1 to D3 are presets of the parameters below; changing a parameter keeps the rest." }), two(labelled("Cut", cut), labelled("Wall alignment", ref)), labelled("Cut point over the wall line", anchor), two(labelled("Cut position offset (mm, + outward)", offset), labelled("Heel above wall top (mm, − embedded)", heel)), labelled("Layers", layers), h("label", { css: { display: "flex", gap: "0.5rem", alignItems: "center" } }, seat, "Move the whole roof so the corner bears as designed (undoable)")), h("div", { css: { display: "grid", gap: "0.5rem", position: "sticky", top: "0" } }, picture, status, h("p", { class: "muted", text: "Section across the eave. Near is the wall face towards the ridge. Red: the wall line the cut stands over; amber: the cut face." }))),
        actions: [{ label: "Apply", kind: "primary", run: async () => {
                    window.clearTimeout(timer);
                    await refresh("full");
                    if (!latest) {
                        toast("This connection cannot be made.", { kind: "error" });
                        return undefined;
                    }
                    const out = await store.run("model.set_roof_end", { ...request("full"), seat: seat.checked && !!latest.wall });
                    return out ? true : undefined;
                } }],
    });
    const existing = hasEnd(edges[0]);
    type.value = "B1";
    loading = false;
    void refresh(existing ? "current" : "preset");
    await handle.result;
    window.clearTimeout(timer);
}
/** The downpipe component kinds the server builds (the same table as ``gutters.COMPONENT_KINDS``). The hopper head goes on top and the shoe at the
 *  bottom, wherever they are added; the server checks every change against the assembly as a whole and refuses one that cannot be built. */
export const COMPONENT_KINDS = [
    ["pipe", "Downpipe section"], ["offset_bend", "Offset bend"], ["elbow", "Elbow"], ["shoe", "Shoe"], ["y_branch", "Y-branch"], ["hopper", "Hopper head"],
];
/** Read a downpipe's components in order (from its Properties, which the server builds from the stored assembly). */
async function assemblyOf(store, id) {
    try {
        const p = await store.api.post("/properties", { model: id });
        return { name: p.title, items: p.assembly ?? [] };
    }
    catch (e) {
        toast(e instanceof Error ? e.message : "Could not read the downpipe.", { kind: "error" });
        return null;
    }
}
/** Edit a downpipe's assembly: add, replace, reorder, split or remove its components, or open one in Properties. Every button is one undoable
 *  ``model.edit_downpipe`` command; connectors between sections are made by the server and are not listed as separate steps. */
export async function downpipeComponentsDialog(store, id) {
    const first = await assemblyOf(store, id);
    if (!first)
        return;
    const list = h("ol", { css: { display: "grid", gap: "0.35rem", margin: "0", paddingLeft: "1.5rem" } });
    const status = h("p", { class: "muted", attrs: { role: "status", "aria-live": "polite" } });
    const kindSelect = (value = "pipe") => h("select", { attrs: { "aria-label": "Component kind" } }, ...COMPONENT_KINDS.map(([v, l]) => h("option", { value: v, text: l, ...(v === value ? { selected: true } : {}) })));
    const addKind = kindSelect("offset_bend");
    const edit = async (params) => {
        const out = await store.run("model.edit_downpipe", { id, ...params });
        if (out)
            await paint();
    };
    let handle = null;
    const paint = async () => {
        const now = await assemblyOf(store, id);
        if (!now)
            return;
        clear(list);
        now.items.forEach((c, i) => {
            const btn = (text, title, run, disabled = false) => h("button", { class: "tool-inline", type: "button", text, disabled, attrs: { title, "aria-label": `${title}: ${c.label} ${c.id}` }, on: { click: run } });
            const swap = kindSelect(c.kind);
            swap.addEventListener("change", () => { void edit({ action: "replace", component: c.id, kind: swap.value }); });
            list.append(h("li", { css: { display: "flex", gap: "0.4rem", alignItems: "center", flexWrap: "wrap" } }, h("span", { text: `${c.label} (${c.id})`, css: { minWidth: "11rem" } }), swap, btn("Edit", "Open this component in Properties", () => { handle?.close(true); void store.select({ kind: "model", ids: [id], part: `comp:${c.id}` }); }), btn("↑", "Move up", () => { void edit({ action: "move", component: c.id, index: i - 1 }); }, i === 0), btn("↓", "Move down", () => { void edit({ action: "move", component: c.id, index: i + 1 }); }, i === now.items.length - 1), btn("Split", "Split this downpipe section in two", () => { void edit({ action: "split", component: c.id }); }, c.kind !== "pipe"), btn("Remove", "Remove this component", () => { void edit({ action: "remove", component: c.id }); }, now.items.length === 1)));
        });
        status.textContent = `${now.items.length} component${now.items.length === 1 ? "" : "s"}, top to bottom. Connectors are added automatically wherever two downpipe sections meet.`;
    };
    const opened = openDialog({
        title: `Downpipe components — ${first.name}`, size: "wide", cancelLabel: "Close",
        body: h("div", { css: { display: "grid", gap: "0.8rem" } }, list, status, h("div", { css: { display: "flex", gap: "0.5rem", alignItems: "end" } }, labelled("Add a component", addKind), h("button", { class: "btn", type: "button", text: "Add", on: { click: () => { void edit({ action: "add", kind: addKind.value }); } } })), h("p", { class: "muted", text: "Bends take the 72°, 45° or 40° fitting angles (or any 5–90°) and their direction in Properties. A component inherits the downpipe's NL-SfB and IFC classification unless you give it its own." })),
    });
    handle = opened;
    await paint();
    await opened.result;
}
/** Place a ground drain: a point drain (gully), a linear channel drain or a downpipe ground inlet. It stands in the floor under it and cuts that floor's
 *  opening (the server finds the floor, builds the body, cover and outlet, and keeps the cut in step when the drain moves or the floor changes). */
export async function drainDialog(store, at = {}) {
    if (!store.project)
        return;
    const unit = store.unit;
    const num = (value, step = "any", min) => h("input", { type: "number", value, step, ...(min ? { min } : {}), attrs: { autocomplete: "off" } });
    const kinds = at.downpipe ? [["inlet", "Downpipe ground inlet"]] : [["gully", "Point drain (yard gully)"], ["channel", "Linear channel drain"], ["inlet", "Downpipe ground inlet"]];
    const kind = h("select", {}, ...kinds.map(([v, l]) => h("option", { value: v, text: l, ...(v === (at.kind ?? kinds[0]?.[0]) ? { selected: true } : {}) })));
    const level = h("select", {}, ...store.levels.map((l) => h("option", { value: l.id, text: l.name, ...(l.id === at.level ? { selected: true } : {}) })));
    const p = at.point ?? [0, 0];
    const px = num(String(p[0])), py = num(String(p[1]));
    const per = unit === "mm" ? 1 : unit === "cm" ? 0.1 : 0.001;
    const ex = num(String(p[0] + 2000 * per)), ey = num(String(p[1]));
    const width = num("", "5", "50"), depth = num("", "5", "50");
    width.placeholder = "default";
    depth.placeholder = "default";
    const cover = h("select", {}, ...["", "grating", "solid cover", "open"].map((v) => h("option", { value: v, text: v || "Default for the kind" })));
    const endRow = h("div", { css: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.75rem" } }, labelled(`End x (${unit})`, ex), labelled(`End y (${unit})`, ey));
    const sync = () => { endRow.hidden = kind.value !== "channel"; };
    kind.addEventListener("change", sync);
    sync();
    const where = at.downpipe ? h("p", { class: "muted", text: "It stands under the downpipe's bottom end and follows it." })
        : h("div", { css: { display: "grid", gap: "0.6rem" } }, labelled("Level", level), h("div", { css: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.75rem" } }, labelled(`x (${unit})`, px, { hint: "The centre; for a channel, its start" }), labelled(`y (${unit})`, py)), endRow);
    const handle = openDialog({
        title: "Add a ground drain", size: "narrow", focus: () => kind,
        body: h("div", { css: { display: "grid", gap: "0.9rem" } }, labelled("Type", kind), where, h("div", { css: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.75rem" } }, labelled("Width or diameter (mm)", width), labelled("Depth (mm)", depth)), labelled("Cover", cover), h("p", { class: "muted", text: "It is set into the floor under it, which gets the matching opening automatically. Sizes, cover, outlet and material can be changed in Properties." })),
        actions: [{ label: "Add drain", kind: "primary", run: async () => {
                    const params = { kind: kind.value };
                    if (at.downpipe)
                        params["downpipe"] = at.downpipe;
                    else {
                        params["level"] = level.value;
                        if (kind.value === "channel") {
                            params["start"] = [Number(px.value), Number(py.value)];
                            params["end"] = [Number(ex.value), Number(ey.value)];
                        }
                        else
                            params["point"] = [Number(px.value), Number(py.value)];
                    }
                    if (width.value)
                        params[kind.value === "inlet" ? "diameter" : "width"] = Number(width.value);
                    if (depth.value)
                        params["depth"] = Number(depth.value);
                    if (cover.value)
                        params["cover"] = cover.value;
                    const out = await store.run("model.add_drain", params);
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
