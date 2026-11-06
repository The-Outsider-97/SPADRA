/** The sheet view: a paper-sized SVG with a title block and the drawings placed on it at their scales. Everything is read from the project. */
import { h, replace, s } from "../../../tools/web/src/dom.js";
import { openMenu } from "./menus.js";
import { entityBounds, formatDimension, segments, textBox } from "./geom2d.js";
import { cutPath, cutStyle, cutWeight, edgePath, hatchPath, levelLabel, markArrows, markLabel, hatchFill, patternPath, surfacePath, swingPath, visibleLevels } from "./projection.js";
import { formatCell } from "./schedule.js";
import { iconFor } from "./browser.js";
import { flatten, openable } from "./store.js";
const MARGIN = 10; // mm between the paper edge and the drawing frame
export class SheetView {
    store;
    el;
    host;
    shown = "";
    data = null;
    box = { x: 0, y: 0, w: 841, h: 594 };
    svg = null;
    drag = null;
    kept = null; // the person's zoom and pan, while the whole sheet is set up for printing
    pageRule = new CSSStyleSheet();
    source = null; // set by the shell, which owns the 3D view
    constructor(store) {
        this.store = store;
        this.host = h("div", { class: "sheet-wrap", attrs: { tabindex: "0", "aria-label": "Sheet. Arrow keys move it, plus and minus zoom, 0 shows the whole sheet." } });
        this.el = h("div", { class: "canvas-wrap", hidden: true }, this.host);
        this.host.addEventListener("wheel", (e) => { e.preventDefault(); this.zoom(e); }, { passive: false });
        this.host.addEventListener("pointerdown", (e) => { this.host.setPointerCapture(e.pointerId); this.drag = { x: e.clientX, y: e.clientY }; });
        this.host.addEventListener("pointermove", (e) => this.pan(e));
        this.host.addEventListener("pointerup", () => { this.drag = null; });
        this.host.addEventListener("keydown", (e) => this.key(e));
        window.addEventListener("beforeprint", () => this.prepareToPrint()); // also for Ctrl+P and the browser's own menu
        window.addEventListener("afterprint", () => this.restoreAfterPrint());
    }
    /** The sheet is printed whole and at the size of its paper, whatever the zoom on screen: the view box is the paper and the page is the paper's size. */
    prepareToPrint() {
        const d = this.data;
        if (!d || !this.svg || this.el.hidden || this.kept)
            return;
        this.kept = { ...this.box };
        this.svg.setAttribute("viewBox", `0 0 ${d.width} ${d.height}`);
        this.pageRule.replaceSync(`@page { size: ${d.width}mm ${d.height}mm; margin: 0; }`);
        document.adoptedStyleSheets = [...document.adoptedStyleSheets, this.pageRule];
    }
    restoreAfterPrint() {
        if (!this.kept)
            return;
        this.box = this.kept;
        this.kept = null;
        this.applyBox();
        document.adoptedStyleSheets = document.adoptedStyleSheets.filter((x) => x !== this.pageRule);
    }
    /** Print the sheet (or save it as PDF from the browser's print dialog). */
    print() { this.prepareToPrint(); try {
        window.print();
    }
    finally {
        this.restoreAfterPrint();
    } }
    key(e) {
        const d = this.data;
        if (!d || e.ctrlKey || e.metaKey || e.altKey)
            return;
        const step = (e.shiftKey ? 0.25 : 0.06);
        const move = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
        const m = move[e.key];
        if (m) {
            e.preventDefault();
            this.box = { ...this.box, x: this.box.x + m[0] * step * this.box.w, y: this.box.y + m[1] * step * this.box.h };
            this.applyBox();
            return;
        }
        const factor = e.key === "+" || e.key === "=" ? 1 / 1.25 : e.key === "-" ? 1.25 : 0;
        if (factor) {
            e.preventDefault();
            const cx = this.box.x + this.box.w / 2, cy = this.box.y + this.box.h / 2, w = this.box.w * factor, hh = this.box.h * factor;
            this.box = { x: cx - w / 2, y: cy - hh / 2, w, h: hh };
            this.applyBox();
        }
        else if (e.key === "0") {
            e.preventDefault();
            this.box = { x: -20, y: -20, w: d.width + 40, h: d.height + 40 };
            this.applyBox();
        }
    }
    /** Load and draw a sheet, unless what is on screen is already this sheet at this revision. */
    async show(id) {
        const key = `${id}@${this.store.project?.revision ?? 0}`;
        if (key === this.shown)
            return;
        this.shown = key;
        const data = await this.store.guard(() => this.store.api.get(`/sheet/${id}`), "Reading the sheet");
        if (!data || this.shown !== key)
            return;
        const fresh = this.data?.id !== data.id;
        this.data = data;
        if (fresh)
            this.box = { x: -20, y: -20, w: data.width + 40, h: data.height + 40 };
        this.draw(data);
    }
    placeViewMenu(anchor) {
        const sheet = this.data;
        if (!sheet)
            return;
        const nodes = flatten(this.store.tree);
        const views = openable(this.store.tree).filter((n) => n.open?.kind === "drawing" || n.open?.kind === "schedule");
        const model = openable(this.store.tree).find((n) => n.open?.kind === "3d");
        const reports = nodes.filter((n) => n.report);
        const entries = views.map((n) => ({ label: n.label, icon: iconFor(n), run: () => { void this.store.run("sheet.place_view", { id: sheet.id, view_id: n.open?.id }); } }));
        if (model) {
            entries.push("separator", { label: `${model.label}, as vector shapes (follows the model)`, icon: "cube", run: () => { void this.store.run("sheet.place_view", { id: sheet.id, view_id: model.open?.id, ...(this.source?.camera() ?? {}) }); } }, { label: "3D snapshot: use Snapshot in the 3D view's ribbon", icon: "cube", disabled: true, run: () => undefined });
        }
        if (reports.length)
            entries.push("separator", ...reports.map((n) => ({ label: `Report: ${n.label}`, icon: "report", run: () => { void this.store.run("sheet.place_report", { id: sheet.id, report: n.report }); } })));
        openMenu(anchor, entries.length ? entries : [{ label: "There is nothing to place", disabled: true, run: () => undefined }], { label: "Place view" });
    }
    zoom(e) {
        const svg = this.svg;
        if (!svg)
            return;
        const r = svg.getBoundingClientRect();
        const k = Math.exp(e.deltaY * 0.0015);
        const fx = (e.clientX - r.left) / r.width, fy = (e.clientY - r.top) / r.height;
        const w = this.box.w * k, hh = this.box.h * k;
        this.box = { x: this.box.x + (this.box.w - w) * fx, y: this.box.y + (this.box.h - hh) * fy, w, h: hh };
        this.applyBox();
    }
    pan(e) {
        if (!this.drag || !this.svg)
            return;
        const r = this.svg.getBoundingClientRect();
        const scale = Math.max(this.box.w / r.width, this.box.h / r.height);
        this.box = { ...this.box, x: this.box.x - (e.clientX - this.drag.x) * scale, y: this.box.y - (e.clientY - this.drag.y) * scale };
        this.drag = { x: e.clientX, y: e.clientY };
        this.applyBox();
    }
    applyBox() { this.svg?.setAttribute("viewBox", `${this.box.x} ${this.box.y} ${this.box.w} ${this.box.h}`); }
    draw(d) {
        const svg = s("svg", { attrs: { viewBox: `${this.box.x} ${this.box.y} ${this.box.w} ${this.box.h}`, role: "img", "aria-label": `Sheet ${d.number} ${d.name}`, preserveAspectRatio: "xMidYMid meet" },
            css: { width: "100%", height: "100%", display: "block", cursor: "grab", "touch-action": "none" } });
        svg.appendChild(s("rect", { attrs: { x: 0, y: 0, width: d.width, height: d.height, fill: "var(--paper)", stroke: "var(--border)", "stroke-width": 0.6 } }));
        svg.appendChild(s("rect", { attrs: { x: MARGIN, y: MARGIN, width: d.width - 2 * MARGIN, height: d.height - 2 * MARGIN, fill: "none", stroke: "var(--ink)", "stroke-width": 0.7 } }));
        for (const vp of d.viewports)
            svg.appendChild(this.viewport(d, vp));
        svg.appendChild(this.titleBlock(d));
        this.svg = svg;
        replace(this.host, svg);
    }
    viewport(d, vp) {
        if (vp.schedule)
            return this.scheduleTable(d, vp);
        if (vp.kind === "snapshot")
            return this.snapshotImage(d, vp);
        if (vp.kind === "report" && vp.report)
            return this.reportBox(d, vp, vp.report);
        const den = Number(vp.scale.split(":")[1] ?? "100") / Number(vp.scale.split(":")[0] ?? "1");
        const mmPerUnit = vp.mm_per_unit ?? 1;
        const k = mmPerUnit / den;
        const g = s("g", { attrs: { transform: `translate(${vp.x} ${d.height - vp.y}) scale(${k} ${-k}) translate(${-vp.anchor[0]} ${-vp.anchor[1]})` }, data: { viewport: vp.id } });
        const hidden = new Set((vp.layers ?? []).filter((l) => !l.visible).map((l) => l.name));
        if (vp.projection)
            this.projection(g, vp.projection, 1 / k, mmPerUnit);
        if (vp.solid)
            this.solidFaces(g, vp.solid);
        const outline = (w) => `M${w.polygon.map((p) => `${p[0]} ${p[1]}`).join("L")}Z`;
        const stroke = { stroke: "var(--ink)", "stroke-width": 1, "vector-effect": "non-scaling-stroke", "stroke-linejoin": "round" };
        for (const cat of ["foundation", "floor", "room", "generic", "stair", "wall", "structural", "window", "door", "column", "gutter", "downpipe", "grid"]) {
            for (const w of (vp.model ?? []).filter((m) => m.category === cat)) {
                if (cat === "room" || cat === "generic" || cat === "column" || cat === "stair" || cat === "grid" || cat === "gutter" || cat === "downpipe")
                    this.otherItem(g, w, 1 / k);
                else if (cat === "foundation")
                    g.appendChild(s("path", { attrs: { d: outline(w), fill: "var(--ink)", "fill-opacity": 0.06, "stroke-dasharray": "5 2 1 2", ...stroke, "stroke-width": 0.5 } }));
                else if (cat === "wall") {
                    g.appendChild(s("path", { attrs: { d: outline(w), fill: "var(--ink)", "fill-opacity": 0.22, ...stroke } }));
                    const d = w.hatch && !w.bands?.length ? patternPath([w.polygon], w.hatch, w.hatch.spacing_mm / k) : "";
                    if (d)
                        g.appendChild(s("path", { attrs: { d, fill: "none", "stroke-width": 0.3, stroke: "var(--ink)", "vector-effect": "non-scaling-stroke" } }));
                    for (const band of w.bands ?? [])
                        for (const poly of band.polygons) { // a layered wall: each layer's band, outlined and hatched by its material
                            g.appendChild(s("path", { attrs: { d: outline({ polygon: poly }), fill: "none", ...stroke, "stroke-width": 0.5 } }));
                            const bd = band.hatch ? patternPath([poly], band.hatch, band.hatch.spacing_mm / k) : "";
                            if (bd)
                                g.appendChild(s("path", { attrs: { d: bd, fill: "none", "stroke-width": 0.3, stroke: "var(--ink)", "vector-effect": "non-scaling-stroke" } }));
                        }
                }
                else if (cat === "floor")
                    g.appendChild(s("path", { attrs: { d: outline(w), fill: "var(--ink)", "fill-opacity": 0.05, stroke: "var(--muted)", "stroke-width": 0.75, "vector-effect": "non-scaling-stroke" } }));
                else if (cat === "structural")
                    g.appendChild(s("path", { attrs: { d: outline(w), fill: "none", "stroke-dasharray": "6 4", ...stroke } }));
                else if (cat === "window" && w.symbol?.a && w.symbol.b) {
                    g.appendChild(s("path", { attrs: { d: outline(w), fill: "var(--paper)", ...stroke } }));
                    g.appendChild(s("path", { attrs: { d: `M${w.symbol.a[0]} ${w.symbol.a[1]}L${w.symbol.b[0]} ${w.symbol.b[1]}`, fill: "none", ...stroke } }));
                    const d = swingPath(w.symbol);
                    if (d)
                        g.appendChild(s("path", { attrs: { d, fill: "none", ...stroke } }));
                }
                else if (cat === "door") {
                    const p = w.polygon, sym = w.symbol;
                    g.appendChild(s("path", { attrs: { d: outline(w), fill: "var(--paper)", stroke: "none" } }));
                    if (p[0] && p[1] && p[2] && p[3])
                        g.appendChild(s("path", { attrs: { d: `M${p[3][0]} ${p[3][1]}L${p[0][0]} ${p[0][1]}M${p[1][0]} ${p[1][1]}L${p[2][0]} ${p[2][1]}`, fill: "none", ...stroke } }));
                    const d = swingPath(sym);
                    if (d)
                        g.appendChild(s("path", { attrs: { d, fill: "none", ...stroke } }));
                }
            }
        }
        for (const e of vp.entities ?? [])
            if (!hidden.has(e.layer))
                this.entity(g, e);
        for (const m of vp.section_marks ?? [])
            this.sectionMark(g, m, 1 / k);
        const wrap = s("g", {});
        wrap.appendChild(g);
        const bounds = [...(vp.entities ?? []).map(entityBounds).filter((b) => !!b), ...(vp.projection?.bounds ? [vp.projection.bounds] : []), ...(vp.solid?.bounds ? [vp.solid.bounds] : []), ...(vp.model ?? []).map((w) => [[Math.min(...w.polygon.map((p) => p[0])), Math.min(...w.polygon.map((p) => p[1]))], [Math.max(...w.polygon.map((p) => p[0])), Math.max(...w.polygon.map((p) => p[1]))]])];
        if (bounds.length) {
            const maxY = Math.max(...bounds.map((b) => b[1][1]));
            const left = Math.min(...bounds.map((b) => b[0][0]));
            const tx = vp.x + (left - vp.anchor[0]) * k, ty = d.height - vp.y - (maxY - vp.anchor[1]) * k - 7;
            wrap.appendChild(s("text", { attrs: { x: tx, y: ty, "font-size": 5, "font-weight": 700, fill: "var(--ink)" }, text: `${(vp.title || vp.view.name).toUpperCase()}${vp.kind === "model" && !/3d/i.test(vp.title || vp.view.name) ? " (3D)" : ""}  ${vp.scale}` }));
        }
        return wrap;
    }
    /** Rooms, family elements, columns, stairs and grids on a plan viewport, in drawing units (``unitsPerMm`` is how many drawing units one millimetre of paper covers). */
    otherItem(g, w, unitsPerMm) {
        const outline = `M${w.polygon.map((p) => `${p[0]} ${p[1]}`).join("L")}${w.polygon.length > 2 ? "Z" : ""}`;
        const stroke = { stroke: "var(--ink)", "stroke-width": 0.7, "vector-effect": "non-scaling-stroke", "stroke-linejoin": "round" };
        const label = (at, size, str, weight = 400) => s("text", { attrs: { transform: `translate(${at[0]} ${at[1] - size * 0.35}) scale(1 -1)`, "font-size": size, "text-anchor": "middle", "font-weight": weight, fill: "var(--ink)", stroke: "none" }, text: str });
        if (w.category === "room") {
            g.appendChild(s("path", { attrs: { d: outline, fill: "var(--ink)", "fill-opacity": 0.04, "stroke-dasharray": "2 3", ...stroke, "stroke-width": 0.4 } }));
            const l = w.label;
            if (l && "number" in l) {
                g.appendChild(label([l.at[0], l.at[1] + 1.8 * unitsPerMm], 3 * unitsPerMm, l.name, 700));
                g.appendChild(label([l.at[0], l.at[1] - 2.2 * unitsPerMm], 2.4 * unitsPerMm, `${l.number} · ${l.area_m2.toLocaleString(undefined, { maximumFractionDigits: 2 })} m²`));
                if (l.volume_m3 !== undefined)
                    g.appendChild(label([l.at[0], l.at[1] - 5.6 * unitsPerMm], 2.4 * unitsPerMm, `${l.volume_m3.toLocaleString(undefined, { maximumFractionDigits: 2 })} m³`));
            }
        }
        else if (w.category === "generic") {
            g.appendChild(s("path", { attrs: { d: outline, fill: "var(--ink)", "fill-opacity": 0.18, ...stroke } }));
        }
        else if (w.category === "column") {
            g.appendChild(s("path", { attrs: { d: outline, fill: "var(--ink)", "fill-opacity": 0.4, ...stroke } }));
        }
        else if (w.category === "gutter") {
            g.appendChild(s("path", { attrs: { d: outline, fill: "var(--ink)", "fill-opacity": 0.2, ...stroke } }));
        }
        else if (w.category === "downpipe") {
            g.appendChild(s("path", { attrs: { d: outline, fill: "var(--paper)", ...stroke } }));
        }
        else if (w.category === "stair") {
            g.appendChild(s("path", { attrs: { d: outline, fill: "none", ...stroke } }));
            g.appendChild(s("path", { attrs: { d: (w.lines ?? []).map(([a, b]) => `M${a[0]} ${a[1]}L${b[0]} ${b[1]}`).join(""), fill: "none", ...stroke, "stroke-width": 0.4 } }));
            const a = w.arrow;
            if (a) {
                const ang = Math.atan2(a.to[1] - a.from[1], a.to[0] - a.from[0]), head = 2.4 * unitsPerMm;
                g.appendChild(s("path", { attrs: { d: `M${a.from[0]} ${a.from[1]}L${a.to[0]} ${a.to[1]}M${a.to[0] - head * Math.cos(ang - 0.45)} ${a.to[1] - head * Math.sin(ang - 0.45)}L${a.to[0]} ${a.to[1]}L${a.to[0] - head * Math.cos(ang + 0.45)} ${a.to[1] - head * Math.sin(ang + 0.45)}`, fill: "none", ...stroke } }));
                g.appendChild(label([(a.from[0] + a.to[0]) / 2, (a.from[1] + a.to[1]) / 2 + 1.5 * unitsPerMm], 2.2 * unitsPerMm, a.label, 700));
            }
        }
        else if (w.category === "grid") {
            const [a, b] = w.polygon;
            const l = w.label;
            if (!a || !b)
                return;
            g.appendChild(s("path", { attrs: { d: `M${a[0]} ${a[1]}L${b[0]} ${b[1]}`, fill: "none", ...stroke, "stroke-dasharray": "10 2 1 2", "stroke-width": 0.4 } }));
            if (l && "radius" in l) {
                const r = 3 * unitsPerMm;
                for (const end of l.bubbles ?? ["start"]) {
                    const [from, to] = end === "start" ? [a, b] : [b, a];
                    const dx = from[0] - to[0], dy = from[1] - to[1], len = Math.hypot(dx, dy) || 1, cx = from[0] + (dx / len) * r, cy = from[1] + (dy / len) * r;
                    g.appendChild(s("circle", { attrs: { cx, cy, r, fill: "var(--paper)", ...stroke } }));
                    g.appendChild(label([cx, cy - r * 0.0], r * 1.1, l.name, 700));
                }
            }
        }
    }
    /** A section or elevation in drawing units, inside the viewport's group. ``unitsPerMm`` is how many drawing units one millimetre of paper covers. */
    projection(g, pr, unitsPerMm, mmPerUnit) {
        const ink = { stroke: "var(--ink)", "vector-effect": "non-scaling-stroke", "stroke-linejoin": "round", "stroke-linecap": "round" };
        const same = (p) => p;
        if (pr.bounds) {
            const pad = (pr.bounds[1][0] - pr.bounds[0][0]) * 0.04 + 6 * unitsPerMm;
            for (const l of visibleLevels(pr, mmPerUnit)) {
                g.appendChild(s("path", { attrs: { d: `M${pr.bounds[0][0] - pad} ${l.z}L${pr.bounds[1][0] + pad} ${l.z}`, fill: "none", stroke: "var(--muted)", "stroke-width": 0.5, "stroke-dasharray": "8 4", "vector-effect": "non-scaling-stroke" } }));
                const size = 2.5 * unitsPerMm;
                g.appendChild(s("text", { attrs: { transform: `translate(${pr.bounds[0][0] - pad} ${l.z + size * 0.4}) scale(1 -1)`, "font-size": size, "text-anchor": "end", fill: "var(--muted)" }, text: levelLabel(l.name, l.z, mmPerUnit) }));
            }
        }
        if (pr.bounds)
            for (const gm of pr.grid_marks ?? []) { // the grids this picture meets, with their bubbles above
                const pad = (pr.bounds[1][1] - pr.bounds[0][1]) * 0.08 + 4 * unitsPerMm, r = 3 * unitsPerMm, top = pr.bounds[1][1] + pad + r * 2;
                g.appendChild(s("path", { attrs: { d: `M${gm.x} ${pr.bounds[0][1] - pad}L${gm.x} ${top - r * 2}`, fill: "none", stroke: "var(--muted)", "stroke-width": 0.4, "stroke-dasharray": "10 2 1 2", "vector-effect": "non-scaling-stroke" } }));
                g.appendChild(s("circle", { attrs: { cx: gm.x, cy: top - r, r, fill: "var(--paper)", ...ink, "stroke-width": 0.5 } }));
                g.appendChild(s("text", { attrs: { transform: `translate(${gm.x} ${top - r - r * 0.4}) scale(1 -1)`, "font-size": r * 1.1, "text-anchor": "middle", "font-weight": 700, fill: "var(--ink)", stroke: "none" }, text: gm.name }));
            }
        for (const it of pr.items) { // far to near, so nearer elements cover what they hide
            g.appendChild(s("path", { attrs: { d: surfacePath(it, same), fill: "var(--paper)", "fill-rule": "nonzero", stroke: "none" }, data: { element: it.id } }));
            g.appendChild(s("path", { attrs: { d: edgePath(it, same), fill: "none", "stroke-width": 0.5, ...ink } }));
            if (it.opening) { // opening direction: solid toward the viewer, dashed away from it, per panel
                for (const part of [it.opening, ...(it.opening.more ?? [])]) {
                    const d = part.lines.map((l) => `M${l[0]} ${l[1]}L${l[2]} ${l[3]}`).join("");
                    g.appendChild(s("path", { attrs: { d, fill: "none", "stroke-width": 0.35, ...ink, ...(part.toward ? {} : { "stroke-dasharray": "3 2" }) }, data: { opening: it.id } }));
                }
            }
        }
        for (const it of pr.items) {
            if (!it.cut.length)
                continue;
            if (it.cut_material) { // a foundation: one region per layer, hatched by its material
                it.cut.forEach((loop, n) => {
                    const style = cutStyle(it.cut_material?.[n] ?? "Concrete");
                    const d = `M${loop.map((p) => `${p[0]} ${p[1]}`).join("L")}Z`;
                    g.appendChild(s("path", { attrs: { d, fill: "var(--ink)", "fill-opacity": style.alpha, stroke: "none" }, data: { cut: it.id } }));
                    if (style.hatch !== "none")
                        g.appendChild(s("path", { attrs: { d: hatchPath(loop, style.hatch, 2.2 * unitsPerMm), fill: "none", "stroke-width": 0.35, ...ink } }));
                    g.appendChild(s("path", { attrs: { d, fill: "none", "stroke-width": 0.9, ...ink } }));
                });
                continue;
            }
            if (it.cut_hatches) { // layers: each loop is one layer, filled and hatched by its own material
                it.cut.forEach((loop, n) => {
                    const hatch = it.cut_hatches?.[n] ?? null;
                    g.appendChild(s("path", { attrs: { d: cutPath({ ...it, cut: [loop] }, same), fill: "var(--ink)", "fill-opacity": cutWeight(it.category) * hatchFill(hatch), "stroke-width": 0.7, ...ink }, data: { cut: it.id, layer: it.cut_layers?.[n]?.layer ?? "" } }));
                    const d = hatch ? patternPath([loop], hatch, hatch.spacing_mm * unitsPerMm) : "";
                    if (d)
                        g.appendChild(s("path", { attrs: { d, fill: "none", "stroke-width": 0.3, ...ink } }));
                });
                continue;
            }
            g.appendChild(s("path", { attrs: { d: cutPath(it, same), fill: "var(--ink)", "fill-opacity": cutWeight(it.category) * hatchFill(it.cut_hatch), "fill-rule": "evenodd", "stroke-width": 0.9, ...ink }, data: { cut: it.id } }));
            if (it.cut_hatch) { // the material's hatch, spaced on paper
                const d = patternPath(it.cut, it.cut_hatch, it.cut_hatch.spacing_mm * unitsPerMm);
                if (d)
                    g.appendChild(s("path", { attrs: { d, fill: "none", "stroke-width": 0.3, ...ink }, data: { hatch: it.id } }));
            }
        }
    }
    /** A section's cut line, arrowheads and bubbles on a plan. */
    sectionMark(g, m, unitsPerMm) {
        const stroke = { stroke: "var(--ink)", "vector-effect": "non-scaling-stroke", "stroke-linejoin": "round" };
        g.appendChild(s("path", { attrs: { d: `M${m.a[0]} ${m.a[1]}L${m.b[0]} ${m.b[1]}`, fill: "none", "stroke-width": 0.6, "stroke-dasharray": "12 3 2 3", ...stroke } }));
        for (const a of markArrows(m, 6 * unitsPerMm))
            g.appendChild(s("path", { attrs: { d: `M${a.from[0]} ${a.from[1]}L${a.tip[0]} ${a.tip[1]}M${a.left[0]} ${a.left[1]}L${a.tip[0]} ${a.tip[1]}L${a.right[0]} ${a.right[1]}`, fill: "none", "stroke-width": 0.7, ...stroke } }));
        const r = 3 * unitsPerMm, label = markLabel(m.name);
        for (const base of [m.a, m.b]) {
            const cx = base[0] - m.look[0] * r * 1.1, cy = base[1] - m.look[1] * r * 1.1;
            g.appendChild(s("circle", { attrs: { cx, cy, r, fill: "var(--paper)", "stroke-width": 0.6, ...stroke } }));
            g.appendChild(s("text", { attrs: { transform: `translate(${cx} ${cy - r * 0.35}) scale(1 -1)`, "font-size": r * 1.2, "text-anchor": "middle", "font-weight": 700, fill: "var(--ink)" }, text: label }));
        }
    }
    /** The 3D model as shaded faces, far to near, in the viewport's group (drawing units, y up). Faces are filled with a thin stroke of their own colour so that
     *  neighbouring faces leave no hairline gaps; the sides marked as boundaries or creases get the ink line. */
    solidFaces(g, solid) {
        const grey = (shade) => { const v = Math.round(255 * Math.min(1, Math.max(0, shade))); return `rgb(${v},${v},${v})`; };
        for (const f of solid.faces) {
            const [x1, y1, x2, y2, x3, y3, shade, edges] = f;
            const fill = grey(shade);
            g.appendChild(s("path", { attrs: { d: `M${x1} ${y1}L${x2} ${y2}L${x3} ${y3}Z`, fill, stroke: fill, "stroke-width": 0.6, "vector-effect": "non-scaling-stroke", "stroke-linejoin": "round" } }));
            const pts = [[x1, y1], [x2, y2], [x3, y3]];
            let d = "";
            for (let i = 0; i < 3; i++)
                if (edges & (1 << i)) {
                    const a = pts[i], b = pts[(i + 1) % 3];
                    d += `M${a[0]} ${a[1]}L${b[0]} ${b[1]}`;
                }
            if (d)
                g.appendChild(s("path", { attrs: { d, fill: "none", stroke: "var(--ink)", "stroke-width": 0.8, "vector-effect": "non-scaling-stroke", "stroke-linecap": "round" } }));
        }
    }
    /** A picture of the 3D view, its lower left corner at the viewport's place on the paper. */
    snapshotImage(d, vp) {
        const w = vp.paper_width ?? 100, hh = vp.paper_height ?? 60;
        const g = s("g", { attrs: { transform: `translate(${vp.x} ${d.height - vp.y - hh})` }, data: { viewport: vp.id } });
        if (vp.image)
            g.appendChild(s("image", { attrs: { href: vp.image.href, x: 0, y: 0, width: w, height: hh, preserveAspectRatio: "none" } }));
        g.appendChild(s("rect", { attrs: { x: 0, y: 0, width: w, height: hh, fill: "none", stroke: "var(--ink)", "stroke-width": 0.3 } }));
        g.appendChild(s("text", { attrs: { x: 0, y: hh + 4.5, "font-size": 3.2, "font-weight": 700, fill: "var(--ink)", stroke: "none" }, text: (vp.title || "3D snapshot").toUpperCase() }));
        return g;
    }
    /** A report's text and tables in a box, top left at the viewport's place: exactly the lines the server laid out for this width and text height. */
    reportBox(d, vp, box) {
        const g = s("g", { attrs: { transform: `translate(${vp.x} ${d.height - vp.y})` }, data: { viewport: vp.id } });
        const size = box.line_height / 1.35, cw = box.char_width;
        const weights = { h1: 700, h2: 700, h3: 600, head: 700 };
        const scale = { h1: 1.35, h2: 1.1, h3: 1 };
        let y = size;
        for (const line of box.lines) {
            const z = scale[line.style] ?? 1;
            if (line.style === "gap") {
                y += box.line_height * 0.5;
                continue;
            }
            const common = { "font-size": size * z, "font-weight": weights[line.style] ?? 400, fill: "var(--ink)", stroke: "none" };
            if (line.cells) {
                for (const [col, str] of line.cells)
                    g.appendChild(s("text", { attrs: { x: col * cw, y, ...common }, text: str }));
                g.appendChild(s("line", { attrs: { x1: 0, y1: y + size * 0.3, x2: (line.span ?? 0) * cw, y2: y + size * 0.3, stroke: "var(--ink)", "stroke-width": line.style === "head" ? 0.35 : 0.1 } }));
            }
            else
                g.appendChild(s("text", { attrs: { x: 0, y, ...common }, text: line.text ?? "" }));
            y += box.line_height * z;
        }
        return g;
    }
    /** A schedule placed on the sheet: a real table in paper millimetres, the same rows as the Schedule view. */
    scheduleTable(d, vp) {
        const sd = vp.schedule;
        const g = s("g", { attrs: { transform: `translate(${vp.x} ${d.height - vp.y})` }, data: { viewport: vp.id } });
        if (!sd)
            return g;
        const size = 2.6, rowH = 5, pad = 1.6, cw = size * 0.56;
        const text = (x, y, str, o = {}) => s("text", { attrs: { x, y, "font-size": size, "font-weight": o.weight ?? 400, "text-anchor": o.anchor ?? "start", fill: "var(--ink)", stroke: "none" }, text: str });
        const widths = sd.columns.map((c) => Math.max(...[`${c.label}${c.unit ? ` (${c.unit})` : ""}`, ...sd.rows.map((r) => formatCell(r[c.key])), ...(c.total ? [formatCell(sd.totals[c.key])] : [])].map((t) => t.length)) * cw + 2 * pad);
        const total = widths.reduce((a, b) => a + b, 0);
        const lines = sd.rows.length + 1 + (sd.rows.length && Object.keys(sd.totals).length ? 1 : 0);
        g.appendChild(text(0, -3, sd.title.toUpperCase(), { weight: 700 }));
        g.appendChild(s("rect", { attrs: { x: 0, y: 0, width: total, height: lines * rowH, fill: "none", stroke: "var(--ink)", "stroke-width": 0.4 } }));
        g.appendChild(s("line", { attrs: { x1: 0, y1: rowH, x2: total, y2: rowH, stroke: "var(--ink)", "stroke-width": 0.4 } }));
        let x = 0;
        sd.columns.forEach((c, i) => {
            const w = widths[i] ?? 0;
            const tx = c.align === "right" ? x + w - pad : x + pad;
            const anchor = c.align === "right" ? "end" : "start";
            g.appendChild(text(tx, rowH * 0.7, `${c.label}${c.unit ? ` (${c.unit})` : ""}`, { weight: 700, anchor }));
            sd.rows.forEach((r, n) => g.appendChild(text(tx, rowH * (n + 1.7), formatCell(r[c.key]), { anchor })));
            if (sd.rows.length && c.total && Object.keys(sd.totals).length)
                g.appendChild(text(tx, rowH * (sd.rows.length + 1.7), formatCell(sd.totals[c.key]), { weight: 700, anchor }));
            x += w;
        });
        if (sd.rows.length && Object.keys(sd.totals).length) {
            g.appendChild(s("line", { attrs: { x1: 0, y1: rowH * (sd.rows.length + 1), x2: total, y2: rowH * (sd.rows.length + 1), stroke: "var(--ink)", "stroke-width": 0.4 } }));
            g.appendChild(text(pad, rowH * (sd.rows.length + 1.7), "Total", { weight: 700 }));
        }
        return g;
    }
    /** One entity in drawing units, inside a group that already maps units to paper millimetres (y up). */
    entity(g, e) {
        const stroke = { fill: "none", stroke: "var(--ink)", "stroke-width": 1, "vector-effect": "non-scaling-stroke", "stroke-linecap": "round", "stroke-linejoin": "round" };
        const gm = e.geometry;
        if (e.kind === "circle" && Array.isArray(gm["center"]) && typeof gm["radius"] === "number") {
            const c = gm["center"];
            g.appendChild(s("circle", { attrs: { cx: c[0], cy: c[1], r: gm["radius"], ...stroke } }));
            return;
        }
        const box = textBox(e);
        if (box) {
            g.appendChild(s("text", { attrs: { transform: `translate(${box.origin[0]} ${box.origin[1]}) scale(1 -1)`, "font-size": box.height, fill: "var(--ink)" }, text: String(gm["text"] ?? "") }));
            return;
        }
        const d = segments(e).map(([a, b]) => `M${a[0]} ${a[1]}L${b[0]} ${b[1]}`).join("");
        if (d)
            g.appendChild(s("path", { attrs: { d, ...stroke } }));
        if (e.kind === "dimension" && e.text && Array.isArray(gm["p1"]) && Array.isArray(gm["p2"]) && Array.isArray(gm["dimline_point"])) {
            const a = gm["p1"], b = gm["p2"], at = gm["dimline_point"];
            const dx = b[0] - a[0], dy = b[1] - a[1], len = Math.hypot(dx, dy) || 1, nx = -dy / len, ny = dx / len;
            const off = (at[0] - a[0]) * nx + (at[1] - a[1]) * ny;
            const mx = (a[0] + b[0]) / 2 + nx * off, my = (a[1] + b[1]) / 2 + ny * off;
            let angle = (Math.atan2(dy, dx) * 180) / Math.PI;
            if (angle > 90 || angle < -90)
                angle += 180;
            const size = (2.5 * this.denominatorFor(g)) / 1; // 2.5 mm of text on paper, whatever the scale
            g.appendChild(s("text", { attrs: { transform: `translate(${mx} ${my}) rotate(${angle}) scale(1 -1)`, "font-size": size, "text-anchor": "middle", dy: -size * 0.3, fill: "var(--ink)" }, text: formatDimension(e.text) }));
        }
    }
    denominatorFor(g) {
        const t = g.getAttribute("transform") ?? "";
        const m = /scale\(([-\d.e]+) /.exec(t);
        const k = m ? Number(m[1]) : 1;
        return k > 0 ? 1 / k : 1;
    }
    /** The title block from the sheet's own fields (project, client, numbers, drawn / checked by, date, status, scale, paper, revision), the revision table above it
     *  (newest first) and the sheet's notes. Whatever the server holds is printed; an empty field is left empty rather than invented. */
    titleBlock(d) {
        const tb = d.title_block ?? {};
        const field = (key) => { const v = tb[key]; return typeof v === "string" ? v : ""; };
        const w = 180, hh = 66, x = d.width - MARGIN - w, y = d.height - MARGIN - hh;
        const g = s("g", { attrs: { fill: "none", stroke: "var(--ink)", "stroke-width": 0.5 }, data: { part: "title-block" } });
        const text = (tx, ty, str, size, weight = 400) => s("text", { attrs: { x: tx, y: ty, "font-size": size, "font-weight": weight, fill: "var(--ink)", stroke: "none" }, text: str });
        const rule = (x1, y1, x2, y2) => s("line", { attrs: { x1, y1, x2, y2 } });
        g.append(s("rect", { attrs: { x, y, width: w, height: hh } }), rule(x, y + 24, x + w, y + 24), rule(x + 95, y + 24, x + 95, y + hh), ...[34, 44, 54].map((dy) => rule(x, y + dy, x + w, y + dy)), text(x + 4, y + 9, (field("project") || d.project).toUpperCase(), 7, 700), text(x + 4, y + 15, field("client"), 3.2), text(x + 4, y + 21.5, (field("sheet_name") || d.name).toUpperCase() || "UNTITLED SHEET", 4.5, 600));
        const left = [["PROJECT NO.", field("project_number")], ["DRAWING NO.", field("sheet_number") || d.number], ["SCALE", field("scale") || d.scale], ["PAPER", field("paper") || d.paper]];
        const right = [["STATUS", field("status")], ["DRAWN BY", field("drawn_by")], ["CHECKED BY", field("checked_by")], ["DATE / REV", `${field("date")}${field("date") ? "  " : ""}${field("revision") || d.revision}`]];
        left.forEach(([label, value], i) => { const ty = y + 31 + i * 10; g.append(text(x + 4, ty - 4, label, 2.2, 600), text(x + 4, ty, value, 3.4)); });
        right.forEach(([label, value], i) => { const ty = y + 31 + i * 10; g.append(text(x + 99, ty - 4, label, 2.2, 600), text(x + 99, ty, value, 3.4)); });
        const out = s("g", {}, g);
        out.append(...this.revisionTable(d, x, y, w), ...this.notes(d));
        return out;
    }
    /** The revision table, directly above the title block: revision, date, description, by (newest first, at most six rows, as the server sends them). */
    revisionTable(d, x, y, w) {
        const rows = d.title_block?.["revisions"] ?? [];
        if (!rows.length)
            return [];
        const rowH = 5, top = y - rowH * (rows.length + 1);
        const cols = [["REV", 12, (r) => r.rev], ["DATE", 24, (r) => r.date], ["DESCRIPTION", w - 12 - 24 - 28, (r) => r.description], ["BY", 28, (r) => r.by]];
        const g = s("g", { attrs: { fill: "none", stroke: "var(--ink)", "stroke-width": 0.4 }, data: { part: "revision-table" } });
        g.appendChild(s("rect", { attrs: { x, y: top, width: w, height: rowH * (rows.length + 1) } }));
        let cx = x;
        for (const [label, cw, get] of cols) {
            if (cx > x)
                g.appendChild(s("line", { attrs: { x1: cx, y1: top, x2: cx, y2: y } }));
            g.appendChild(s("text", { attrs: { x: cx + 1.5, y: top + rowH * 0.72, "font-size": 2.4, "font-weight": 700, fill: "var(--ink)", stroke: "none" }, text: label }));
            rows.forEach((r, i) => g.appendChild(s("text", { attrs: { x: cx + 1.5, y: top + rowH * (i + 1.72), "font-size": 2.8, fill: "var(--ink)", stroke: "none" }, text: get(r) })));
            cx += cw;
        }
        for (let i = 1; i <= rows.length; i++)
            g.appendChild(s("line", { attrs: { x1: x, y1: top + rowH * i, x2: x + w, y2: top + rowH * i } }));
        return [g];
    }
    /** The sheet's notes (free text on the paper), and, on screen only, a marker when what the sheet shows has changed since its last issue. */
    notes(d) {
        const out = [];
        for (const n of d.notes ?? []) {
            const t = s("text", { attrs: { "font-size": n.size, fill: "var(--ink)", stroke: "none" }, data: { note: n.id } });
            n.text.split("\n").forEach((line, i) => t.appendChild(s("tspan", { attrs: { x: n.x, y: d.height - n.y + i * n.size * 1.3 }, text: line })));
            out.push(t);
        }
        if (d.status?.state === "changed") {
            out.push(s("text", { attrs: { x: MARGIN + 3, y: MARGIN + 6, "font-size": 4, "font-weight": 700, fill: "var(--danger, #b3261e)", stroke: "none", class: "screen-only" },
                text: `Changed since revision ${d.status.revision}: ${d.status.changed.join(", ")}` }));
        }
        return out;
    }
}
