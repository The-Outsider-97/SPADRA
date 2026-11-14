/** 2D-to-BIM reconstruction: import a dimensioned plan (raster, PDF) or take a drawing of this project, detect, check the result over the source, correct, preview, then generate.
 *  Everything shown comes from ``/api/reconstruct/run`` (read-only); nothing enters the project until the person presses Generate, which runs the ordinary commands
 *  ``reconstruct.build`` / ``reconstruct.draw`` (one undo step each). Corrections are re-sent to the server, which re-runs the whole pipeline deterministically: this file never
 *  edits geometry itself. */
import { h, labelled, s } from "../../../tools/web/src/dom.js";
import { openDialog, toast } from "../../../tools/web/src/ui.js";
import { openable } from "./store.js";
const pct = (v) => `${Math.round(v * 100)} %`;
const toBase64 = (buffer) => { let bin = ""; const bytes = new Uint8Array(buffer); for (let i = 0; i < bytes.length; i += 0x8000)
    bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000)); return btoa(bin); };
export async function reconstructDialog(store) {
    if (!store.project) {
        toast("Open a project first.", { kind: "info" });
        return;
    }
    const caps = await store.api.get("/reconstruct/capabilities");
    const drawings = openable(store.tree).filter((n) => n.open?.kind === "drawing");
    let data = "", kind = "raster", imageUrl = "", model = null, busy = false;
    const corrections = { dimensions: {}, remove: [], openings: {}, walls: {} };
    const file = h("input", { type: "file", attrs: { accept: ".png,.jpg,.jpeg,.tif,.tiff,.pdf,image/png,image/jpeg,image/tiff,application/pdf", "aria-label": "Plan image or PDF" } });
    const drawing = h("select", { attrs: { "aria-label": "Drawing of this project" } }, h("option", { value: "", text: "—" }), ...drawings.map((n) => h("option", { value: n.open?.id ?? "", text: n.label })));
    const unit = h("select", { attrs: { "aria-label": "Unit of the printed dimensions" } }, ...[["auto", "Detect"], ["mm", "Millimetres"], ["cm", "Centimetres"], ["m", "Metres"]].map(([v, t]) => h("option", { value: v, text: t })));
    const minConf = h("input", { type: "number", value: "0", attrs: { min: "0", max: "1", step: "0.05", "aria-label": "Minimum confidence" } });
    const height = h("input", { type: "number", value: "3", attrs: { min: "0.1", step: "0.1", "aria-label": "Wall height (m)" } });
    const target = h("select", { attrs: { "aria-label": "Generate" } }, h("option", { value: "bim", text: "BIM walls, doors, windows and rooms" }), h("option", { value: "drawing", text: "Drawing geometry (SDRW layers)" }), h("option", { value: "both", text: "Both" }));
    const scaleBox = h("input", { type: "number", attrs: { min: "0", step: "any", placeholder: "from dimensions", "aria-label": "Scale override (mm per pixel)" } });
    const status = h("p", { class: "muted", attrs: { role: "status" }, text: caps.sources["raster"]?.available === false ? `Raster plans cannot be read here: ${caps.sources["raster"].reason ?? ""}` : "Choose a plan, then press Detect." });
    const stage = s("svg", { attrs: { viewBox: "0 0 400 300", role: "img", "aria-label": "Detected geometry over the source plan" }, css: { width: "100%", maxHeight: "420px", border: "1px solid var(--hairline)", background: "var(--surface)", borderRadius: "10px" } });
    const layers = { source: true, lines: true, dimensions: true, walls: true, openings: true, rooms: true };
    const toggles = h("div", { css: { display: "flex", gap: ".8rem", flexWrap: "wrap" } }, ...Object.keys(layers).map((k) => {
        const box = h("input", { type: "checkbox", attrs: { "aria-label": `Show ${k}` } });
        box.checked = layers[k];
        box.addEventListener("change", () => { layers[k] = box.checked; draw(); });
        return h("label", { class: "muted" }, box, ` ${k}`);
    }));
    const diagnostics = h("div", {}), dimTable = h("div", {}), wallTable = h("div", {}), summary = h("div", {});
    const run = async () => {
        if (busy)
            return;
        if (!data && !drawing.value) {
            toast("Choose a file or a drawing first.", { kind: "info" });
            return;
        }
        busy = true;
        status.textContent = "Reading the plan (read-only)…";
        try {
            const body = { kind, options: { unit: unit.value }, corrections: { ...corrections, ...(scaleBox.value ? { scale_mm_per_px: Number(scaleBox.value) } : {}) } };
            if (kind === "sdrw")
                body["drawing"] = drawing.value;
            else
                body["data"] = data;
            model = await store.api.post("/reconstruct/run", body);
            show();
        }
        catch (error) {
            status.textContent = `Detection failed: ${error instanceof Error ? error.message : String(error)}`;
        }
        finally {
            busy = false;
        }
    };
    const px = (m, p) => m.frame ? [((p[0] ?? 0) + m.frame.ox) / m.frame.scale, (m.frame.oy - (p[1] ?? 0)) / m.frame.scale] : [0, 0];
    const draw = () => {
        const m = model;
        if (!m) {
            stage.replaceChildren();
            return;
        }
        const pts = [...m.overlay.segments.flatMap((q) => [q.a, q.b])];
        const W = m.source.width ?? (Math.max(...pts.map((p) => p[0] ?? 0), 1) + 50), H = m.source.height ?? (Math.max(...pts.map((p) => p[1] ?? 0), 1) + 50);
        stage.setAttribute("viewBox", `0 0 ${W} ${H}`);
        const stroke = Math.max(W, H) / 500;
        const nodes = [];
        if (layers.source && imageUrl)
            nodes.push(s("image", { attrs: { href: imageUrl, x: 0, y: 0, width: W, height: H, opacity: 0.55 } }));
        if (layers.lines)
            for (const q of m.overlay.segments)
                if (q.role === "line")
                    nodes.push(s("line", { attrs: { x1: q.a[0], y1: q.a[1], x2: q.b[0], y2: q.b[1], stroke: "#8796a5", "stroke-width": stroke * 0.6, "stroke-opacity": 0.5 } }));
        if (layers.rooms)
            for (const r of m.spaces)
                nodes.push(s("polygon", { attrs: { points: r.polygon.map((p) => px(m, p).join(",")).join(" "), fill: "#2677c9", "fill-opacity": 0.14, stroke: "#2677c9", "stroke-width": stroke * 0.8 } }));
        if (layers.walls)
            for (const w of m.walls) {
                const a = px(m, w.start), b = px(m, w.end);
                nodes.push(s("line", { attrs: { x1: a[0], y1: a[1], x2: b[0], y2: b[1], stroke: corrections.remove.includes(w.id) ? "#b0b0b0" : "#c2610f", "stroke-width": Math.max(stroke, w.thickness_mm / (m.frame?.scale ?? 1)), "stroke-opacity": 0.45, "stroke-linecap": "butt" } }));
                nodes.push(s("text", { attrs: { x: (a[0] + b[0]) / 2, y: (a[1] + b[1]) / 2, "font-size": stroke * 9, fill: "#7a3a05" }, text: w.id }));
            }
        if (layers.openings)
            for (const w of m.walls) {
                const a = px(m, w.start), b = px(m, w.end), L = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1, ux = (b[0] - a[0]) / L, uy = (b[1] - a[1]) / L;
                for (const o of w.openings) {
                    const c = o.offset_mm / (m.frame?.scale ?? 1), half = o.width_mm / (m.frame?.scale ?? 1) / 2;
                    nodes.push(s("line", { attrs: { x1: a[0] + ux * (c - half), y1: a[1] + uy * (c - half), x2: a[0] + ux * (c + half), y2: a[1] + uy * (c + half), stroke: o.kind === "door" ? "#1e8f4e" : "#14a3b8", "stroke-width": stroke * 4 } }));
                }
            }
        if (layers.dimensions)
            for (const d of m.dimensions) {
                const used = d.status === "used" || d.status === "thickness";
                nodes.push(s("line", { attrs: { x1: d.p[0], y1: d.p[1], x2: d.q[0], y2: d.q[1], stroke: used ? "#2677c9" : "#c0392b", "stroke-width": stroke * 1.4, "stroke-dasharray": used ? "" : `${stroke * 4},${stroke * 3}` } }));
                nodes.push(s("text", { attrs: { x: ((d.p[0] ?? 0) + (d.q[0] ?? 0)) / 2, y: ((d.p[1] ?? 0) + (d.q[1] ?? 0)) / 2 - stroke * 3, "font-size": stroke * 10, fill: used ? "#1c5a99" : "#962d22", "text-anchor": "middle" }, text: `${d.id} ${d.text}` }));
            }
        stage.replaceChildren(...nodes);
    };
    const show = () => {
        const m = model;
        if (!m)
            return;
        draw();
        const sm = m.summary;
        summary.replaceChildren(h("p", { text: `${sm["walls"]} walls · ${sm["openings"]} openings · ${sm["spaces"]} rooms · ${sm["dimensions_used"]}/${sm["dimensions_found"]} dimensions used · scale ${m.scale.mm_per_unit ? `${m.scale.mm_per_unit.toFixed(4)} mm/unit (${m.scale.method})` : "unknown"}` }), h("p", { class: "muted", text: `Alignment: ${Object.entries(m.constraints.axes ?? {}).map(([a, i]) => `${a} ${i.status}`).join(", ") || "no dimensions were attached to walls"}. Confidence is an evidence score, not a probability.` }));
        diagnostics.replaceChildren(...(m.diagnostics.length ? [h("ul", { attrs: { "aria-label": "Diagnostics" } }, m.diagnostics.map((d) => h("li", {}, h("span", { class: d.severity === "error" ? "badge risk" : "badge", text: d.severity }), h("span", { text: ` ${d.message}${d.hint ? ` ${d.hint}` : ""}` }))))] : []));
        dimTable.replaceChildren(h("table", { class: "schedule", attrs: { "aria-label": "Dimensions" } }, h("thead", {}, h("tr", {}, ["Id", "Printed", "Value (mm)", "Status", "Conf.", "Ignore"].map((t) => h("th", { text: t })))), h("tbody", {}, m.dimensions.map((d) => {
            const value = h("input", { type: "number", value: String(d.value_mm), attrs: { step: "any", "aria-label": `Value of ${d.id} in mm` }, css: { width: "6.5rem" } });
            value.addEventListener("change", () => { corrections.dimensions[d.id] = { ...corrections.dimensions[d.id], value_mm: Number(value.value) }; status.textContent = "Dimension changed: press Re-run to apply."; });
            const ignore = h("input", { type: "checkbox", attrs: { "aria-label": `Ignore ${d.id}` } });
            ignore.checked = d.status === "ignored";
            ignore.addEventListener("change", () => { corrections.dimensions[d.id] = { ...corrections.dimensions[d.id], ignore: ignore.checked }; status.textContent = "Dimension changed: press Re-run to apply."; });
            return h("tr", {}, h("td", { text: d.id }), h("td", { class: "num", text: d.text }), h("td", {}, value), h("td", { text: d.dropped_reason ? `${d.status} — ${d.dropped_reason}` : d.status }), h("td", { class: "num", text: pct(d.confidence) }), h("td", {}, ignore));
        }))));
        wallTable.replaceChildren(h("table", { class: "schedule", attrs: { "aria-label": "Walls and openings" } }, h("thead", {}, h("tr", {}, ["Element", "Size (mm)", "Conf.", "Evidence", "Remove / kind"].map((t) => h("th", { text: t })))), h("tbody", {}, m.walls.flatMap((w) => {
            const rm = (id) => {
                const box = h("input", { type: "checkbox", attrs: { "aria-label": `Remove ${id}` } });
                box.checked = corrections.remove.includes(id);
                box.addEventListener("change", () => { corrections.remove = box.checked ? [...corrections.remove, id] : corrections.remove.filter((x) => x !== id); status.textContent = "Removal marked: press Re-run to apply."; draw(); });
                return box;
            };
            const rows = [h("tr", {}, h("th", { attrs: { scope: "row" }, text: `${w.id} wall` }), h("td", { class: "num", text: `${Math.round(Math.hypot(w.end[0] - w.start[0], w.end[1] - w.start[1]))} × ${w.thickness_mm}` }), h("td", { class: "num", text: pct(w.confidence) }), h("td", { text: "two parallel faces" }), h("td", {}, rm(w.id)))];
            for (const o of w.openings) {
                const sel = h("select", { attrs: { "aria-label": `Kind of ${o.id}` } }, h("option", { value: "door", text: "door" }), h("option", { value: "window", text: "window" }));
                sel.value = o.kind;
                sel.addEventListener("change", () => { corrections.openings[o.id] = { kind: sel.value }; status.textContent = "Opening changed: press Re-run to apply."; });
                rows.push(h("tr", {}, h("th", { attrs: { scope: "row" }, text: `${o.id} ${o.kind}` }), h("td", { class: "num", text: `${Math.round(o.width_mm)} wide at ${Math.round(o.offset_mm)}` }), h("td", { class: "num", text: pct(o.confidence) }), h("td", { text: Object.keys(o.evidence).filter((k) => k !== "jambs").join(", ") }), h("td", {}, sel, rm(o.id))));
            }
            return rows;
        }))));
        status.textContent = m.ok ? "Detected. Check the overlay, correct what is wrong and Re-run, then Generate." : "Detected with errors: read the diagnostics. Generate is available only for what was found.";
    };
    file.addEventListener("change", async () => {
        const f = file.files?.[0];
        if (!f)
            return;
        kind = /\.pdf$/i.test(f.name) || f.type === "application/pdf" ? "pdf" : "raster";
        const buffer = await f.arrayBuffer();
        data = toBase64(buffer);
        imageUrl = kind === "raster" ? URL.createObjectURL(new Blob([buffer], { type: f.type || "image/png" })) : "";
        drawing.value = "";
        model = null;
        corrections.dimensions = {};
        corrections.remove = [];
        corrections.openings = {};
        corrections.walls = {};
        status.textContent = `${f.name} loaded (${Math.round(f.size / 1024)} KB). Press Detect.`;
    });
    drawing.addEventListener("change", () => { if (drawing.value) {
        kind = "sdrw";
        data = "";
        imageUrl = "";
        file.value = "";
        model = null;
        status.textContent = "A drawing of this project will be read. Press Detect.";
    } });
    const generate = async () => {
        const m = model;
        if (!m || !m.walls.length) {
            toast("Nothing was detected to generate.", { kind: "info" });
            return undefined;
        }
        const kept = { ...m, walls: m.walls.filter((w) => !corrections.remove.includes(w.id)).map((w) => ({ ...w, openings: w.openings.filter((o) => !corrections.remove.includes(o.id)) })) };
        const minimum = Number(minConf.value) || 0;
        if (target.value !== "drawing") {
            const out = await store.run("reconstruct.build", { model: kept, height: Number(height.value) || 3, min_confidence: minimum });
            if (!out)
                return undefined;
            const r = out.result;
            toast(`Built ${Object.keys(r.walls).length} walls, ${Object.keys(r.openings).length} openings, ${Object.keys(r.spaces).length} rooms${r.skipped.length ? `; ${r.skipped.length} skipped for low confidence` : ""}.`, { kind: "ok", action: { label: "Undo", run: () => { void store.undo(); } } });
        }
        if (target.value !== "bim") {
            const view = drawing.value || drawings[0]?.open?.id;
            if (!view) {
                toast("There is no drawing to write into.", { kind: "error" });
                return undefined;
            }
            const out = await store.run("reconstruct.draw", { drawing: view, model: kept, origin_mm: [0, 0] });
            if (!out)
                return undefined;
            toast("The plan was written to the drawing on layers A-WALL, A-OPEN and A-ROOM.", { kind: "ok", action: { label: "Undo", run: () => { void store.undo(); } } });
        }
        return true;
    };
    const body = h("div", { css: { display: "grid", gap: ".7rem" } }, h("p", { class: "muted", text: "Dimensions printed on the plan set the scale and are imposed on the walls through the constraint solver. Nothing is created until you press Generate." }), h("div", { css: { display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(14rem,1fr))", gap: ".6rem" } }, labelled("Plan image or PDF", file), labelled("…or a drawing of this project", drawing), labelled("Unit of printed dimensions", unit), labelled("Scale override (mm per pixel)", scaleBox)), h("div", { css: { display: "flex", gap: ".5rem" } }, h("button", { class: "btn primary", type: "button", text: "Detect", on: { click: () => { void run(); } } }), h("button", { class: "btn", type: "button", text: "Re-run with corrections", on: { click: () => { void run(); } } })), status, toggles, stage, summary, diagnostics, h("h3", { text: "Dimensions" }), dimTable, h("h3", { text: "Walls and openings" }), wallTable, h("details", {}, h("summary", { text: "What this does not read" }), h("ul", {}, caps.limits.map((l) => h("li", { text: l })))), h("div", { css: { display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(14rem,1fr))", gap: ".6rem" } }, labelled("Generate", target), labelled("Wall height (m)", height), labelled("Minimum confidence (0–1)", minConf)));
    const handle = openDialog({ title: "Reconstruct from a 2D plan", size: "wide", body, actions: [{ label: "Generate", kind: "primary", run: generate }] });
    await handle.result;
    if (imageUrl)
        URL.revokeObjectURL(imageUrl);
}
