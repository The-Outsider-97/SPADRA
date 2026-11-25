/** The Measure tool of the 3D view: pick things on the model (with snapping to a corner, an edge or a face), read the figure and the way it was worked out,
 *  and keep it with the project if it should stay. The figures come from the server (``measurements.py``), computed exactly from the meshes; this only picks,
 *  shows and keeps. A measurement here never changes the model and never drives anything.
 *
 *  Picks: *Point* snaps to the nearest corner (and then follows that corner when the model changes) or takes the exact spot on the surface; *Edge*, *Face* and
 *  *Element* take what is under the pointer. Enter finishes a measurement that takes any number of points (a length, a circle), Backspace takes the last pick back,
 *  Esc starts again. */
import { h } from "../../../tools/web/src/dom.js";
import { toast } from "../../../tools/web/src/ui.js";
const SPECS = {
    distance: { label: "Distance", mode: "point", count: 2, modes: ["point", "face", "element"], hints: ["Pick the first point", "Pick the second point"] },
    horizontal: { label: "Horizontal distance", mode: "point", count: 2, modes: ["point"], hints: ["Pick the first point", "Pick the second point"] },
    vertical: { label: "Vertical distance", mode: "point", count: 2, modes: ["point"], hints: ["Pick the first point", "Pick the second point"] },
    length: { label: "Length (edge or path)", mode: "point", count: "many", modes: ["point", "edge"], hints: ["Pick the first point", "Pick the next point; Enter finishes"] },
    angle: { label: "Angle", mode: "edge", count: 2, modes: ["edge", "face", "point"], hints: ["Pick the first edge", "Pick the second edge"] },
    radius: { label: "Radius", mode: "point", count: "many", modes: ["point"], hints: ["Pick three or more points on the circle", "Pick another point; Enter finishes"] },
    diameter: { label: "Diameter", mode: "point", count: "many", modes: ["point"], hints: ["Pick three or more points on the circle", "Pick another point; Enter finishes"] },
    area: { label: "Area", mode: "face", count: 1, modes: ["face", "element", "point"], hints: ["Pick a face (or an element for its whole surface)"] },
    perimeter: { label: "Perimeter", mode: "face", count: 1, modes: ["face", "point"], hints: ["Pick a face"] },
    volume: { label: "Volume", mode: "element", count: 1, modes: ["element"], hints: ["Pick an element"] },
    thickness: { label: "Thickness", mode: "element", count: 1, modes: ["element", "face"], hints: ["Pick a layered element, or a face of a solid"] },
    clearance: { label: "Clearance", mode: "element", count: 2, modes: ["element", "face"], hints: ["Pick the first element", "Pick the second element"] },
    coordinates: { label: "Coordinates", mode: "point", count: 1, modes: ["point", "element"], hints: ["Pick a point"] },
    slope: { label: "Slope", mode: "edge", count: 1, modes: ["edge", "face", "point"], hints: ["Pick an edge or a face"] },
};
/** What a plan can measure: only what is picked as points on the drawing. Areas and perimeters are closed outlines picked corner by corner. */
const FLAT = {
    distance: { label: "Distance", mode: "point", count: 2, modes: ["point"], hints: ["Pick the first point", "Pick the second point"] },
    length: { label: "Length (path)", mode: "point", count: "many", modes: ["point"], hints: ["Pick the first point", "Pick the next point; Enter finishes"] },
    angle: { label: "Angle", mode: "point", count: 3, modes: ["point"], hints: ["Pick a point on the first line", "Pick the corner (the angle is measured here)", "Pick a point on the second line"] },
    area: { label: "Area (closed outline)", mode: "point", count: "many", modes: ["point"], hints: ["Pick the first corner", "Pick the next corner; Enter closes the outline"] },
    perimeter: { label: "Perimeter (closed outline)", mode: "point", count: "many", modes: ["point"], hints: ["Pick the first corner", "Pick the next corner; Enter closes the outline"] },
    radius: { label: "Radius", mode: "point", count: "many", modes: ["point"], hints: ["Pick three or more points on the circle", "Pick another point; Enter finishes"] },
    coordinates: { label: "Coordinates", mode: "point", count: 1, modes: ["point"], hints: ["Pick a point"] },
};
const UNITS = ["mm", "cm", "m", "in", "ft"];
export class MeasureTool {
    host;
    panel;
    specs;
    kindSelect;
    modeSelect;
    unitSelect;
    precision;
    hint = h("p", { class: "muted", attrs: { "aria-live": "polite" } });
    result = h("div", {});
    keptList = h("div", {});
    active = false;
    refs = [];
    shown = null;
    hover = null;
    kept = [];
    keptRevision = -1;
    busy = false;
    pending = null;
    constructor(host) {
        this.host = host;
        this.specs = host.flat ? FLAT : SPECS;
        this.kindSelect = h("select", { attrs: { "aria-label": "Measure" } }, ...Object.entries(this.specs).map(([k, s]) => h("option", { value: k, text: s.label })));
        this.modeSelect = h("select", { attrs: { "aria-label": "Pick" } });
        this.unitSelect = h("select", { attrs: { "aria-label": "Unit" } }, h("option", { value: "", text: "Project unit" }), ...UNITS.map((u) => h("option", { value: u, text: u })));
        this.precision = h("input", { type: "number", min: "0", max: "8", step: "1", value: "", attrs: { "aria-label": "Decimals", placeholder: "auto" } });
        this.kindSelect.addEventListener("change", () => { this.reset(); this.fillModes(); this.recompute(); });
        this.modeSelect.addEventListener("change", () => { this.refs = []; this.updateHint(); this.host.redraw(); });
        this.unitSelect.addEventListener("change", () => { void this.recompute(); });
        this.precision.addEventListener("change", () => { void this.recompute(); });
        this.panel = h("div", { class: host.flat ? "measure-panel flat" : "measure-panel", hidden: true, attrs: { role: "region", "aria-label": "Measure" } }, h("h3", { text: "Measure" }), h("div", { class: "row" }, h("label", { class: "field" }, h("span", { text: "Measure" }), this.kindSelect), h("label", { class: "field" }, h("span", { text: "Pick" }), this.modeSelect)), h("div", { class: "row" }, h("label", { class: "field" }, h("span", { text: "Unit" }), this.unitSelect), h("label", { class: "field" }, h("span", { text: "Decimals" }), this.precision)), this.hint, this.result, h("div", { class: "row" }, h("button", { class: "btn", type: "button", text: "Keep", on: { click: () => { void this.keep(); } } }), h("button", { class: "btn", type: "button", text: "Clear", on: { click: () => { this.reset(); this.host.redraw(); } } })), h("h3", { text: "Kept in the project" }), this.keptList);
        host.el.appendChild(this.panel);
        this.fillModes();
    }
    get isActive() { return this.active; }
    setActive(on) {
        if (on === this.active)
            return;
        this.active = on;
        this.panel.hidden = !on;
        if (on) {
            void this.loadKept();
            this.updateHint();
        }
        else {
            this.reset();
            this.hover = null;
        }
    }
    spec() { return this.specs[this.kindSelect.value]; }
    mode() { return (this.modeSelect.value || this.spec().mode); }
    fillModes() {
        const s = this.spec();
        this.modeSelect.replaceChildren(...s.modes.map((m) => h("option", { value: m, text: { point: "Point", edge: "Edge", face: "Face", element: "Element" }[m] })));
        this.modeSelect.value = s.mode;
        this.updateHint();
    }
    updateHint() {
        const s = this.spec();
        const need = s.count === "many" ? "many" : s.count;
        const text = this.refs.length >= (need === "many" ? 1 : need) && this.shown ? "Done. Keep it, or pick again to measure something else." : s.hints[Math.min(this.refs.length, s.hints.length - 1)] ?? "";
        const done = !!this.shown && this.refs.length >= (need === "many" ? 1 : need);
        const snap = this.mode() === "point" && !done ? (this.host.flat ? " Points snap to corners, ends and grid." : " Points snap to corners.") : "";
        this.hint.textContent = `${text.replace(/[.;]$/, "")}.${snap} Backspace undoes a pick, Esc starts again.`;
    }
    reset() { this.refs = []; this.shown = null; this.result.replaceChildren(); this.updateHint(); }
    // ----------------------------------------------------------------------------------------------------------------------- pointer
    /** The pointer moved over the view: find what a click here would pick. */
    move(x, y) {
        if (!this.active)
            return;
        this.pending = { x, y };
        if (this.busy)
            return;
        void this.look();
    }
    /** Plan views: the pointer is over this point (metres), already snapped by the view. */
    flatHover(at) { this.hover = at ? { snap: null, at, id: "" } : null; this.host.redraw(); }
    /** Plan views: a click at this point (metres). */
    async flatPoint(at) {
        if (!this.active)
            return;
        const s = this.spec();
        if (this.shown && s.count !== "many") {
            this.refs = [];
            this.shown = null;
        }
        if (this.shown && s.count === "many") {
            this.refs = [];
            this.shown = null;
            this.result.replaceChildren();
        }
        this.refs.push({ type: "point", p: at });
        this.updateHint();
        if (s.count !== "many" && this.refs.length >= s.count)
            await this.recompute();
        else
            this.host.redraw();
    }
    async look() {
        while (this.pending) {
            const { x, y } = this.pending;
            this.pending = null;
            this.busy = true;
            const hit = this.host.hit(x, y);
            if (!hit) {
                this.hover = null;
                this.busy = false;
                this.host.redraw();
                continue;
            }
            try {
                const snap = await this.host.store.api.post("/measure/snap", { id: hit.id, point: hit.point, radius: 0.08 });
                this.hover = { snap: this.withId(snap, hit.id), at: hit.point, id: hit.id };
            }
            catch {
                this.hover = { snap: null, at: hit.point, id: hit.id };
            }
            this.busy = false;
            this.host.redraw();
        }
    }
    withId(snap, id) {
        return { ...snap, vertex: snap.vertex ? { ...snap.vertex, id } : null };
    }
    /** A click: add what is under the pointer to the measurement, and work it out once there is enough. Returns true when the click was used. */
    async click() {
        if (!this.active)
            return false;
        const h0 = this.hover;
        if (!h0) {
            toast("Click on the model to pick.", { kind: "info", timeoutMs: 1800 });
            return true;
        }
        const ref = this.refFor(h0);
        if (!ref)
            return true;
        if (this.shown && this.spec().count !== "many") {
            this.refs = [];
            this.shown = null;
        }
        this.refs.push(ref);
        const s = this.spec();
        this.updateHint();
        if (s.count !== "many" && this.refs.length >= s.count)
            await this.recompute();
        else
            this.host.redraw();
        return true;
    }
    refFor(h0) {
        const mode = this.mode();
        if (mode === "element")
            return { type: "element", id: h0.id };
        if (mode === "face") {
            if (!h0.snap?.face) {
                toast("Click on a face of the model.", { kind: "info", timeoutMs: 1800 });
                return null;
            }
            return { type: "face", id: h0.snap.face.id, face: h0.snap.face.face };
        }
        if (mode === "edge") {
            if (!h0.snap?.edge) {
                toast("Click closer to an edge.", { kind: "info", timeoutMs: 1800 });
                return null;
            }
            return { type: "edge", id: h0.snap.edge.id, a: h0.snap.edge.a, b: h0.snap.edge.b };
        }
        if (h0.snap?.vertex)
            return { type: "vertex", id: h0.id, index: h0.snap.vertex.index };
        return { type: "point", p: h0.at };
    }
    /** A key in the view while measuring. Returns true when the key was used. */
    key(e) {
        if (!this.active)
            return false;
        if (e.key === "Escape") {
            if (!this.refs.length && !this.shown)
                return false; // nothing to cancel: let the view use Esc as usual
            this.reset();
            this.host.redraw();
            return true;
        }
        if (e.key === "Backspace") {
            this.refs.pop();
            this.shown = null;
            this.result.replaceChildren();
            this.updateHint();
            this.host.redraw();
            return true;
        }
        if (e.key === "Enter" && this.spec().count === "many") {
            void this.recompute();
            return true;
        }
        return false;
    }
    // ----------------------------------------------------------------------------------------------------------------------- computing
    body() {
        const decimals = this.precision.value === "" ? undefined : Number(this.precision.value);
        const kind = this.kindSelect.value;
        const refs = this.host.flat && (kind === "area" || kind === "perimeter") ? [{ type: "path", points: this.refs.map((r) => r["p"]), closed: true }] : this.refs;
        return { kind: kind === "radius" ? "radius" : kind, refs, ...(this.unitSelect.value ? { unit: this.unitSelect.value } : {}), ...(decimals !== undefined && Number.isInteger(decimals) ? { precision: decimals } : {}) };
    }
    async recompute() {
        if (!this.refs.length) {
            this.shown = null;
            this.result.replaceChildren();
            this.host.redraw();
            return;
        }
        const s = this.spec();
        if (s.count !== "many" && this.refs.length < s.count)
            return;
        try {
            this.shown = await this.host.store.api.post("/measure", this.body());
            this.render(this.result, this.shown);
        }
        catch (error) {
            this.shown = null;
            this.result.replaceChildren(h("p", { class: "field-error", text: error instanceof Error ? error.message : "That could not be measured." }));
        }
        this.updateHint();
        this.host.redraw();
    }
    render(into, r) {
        into.replaceChildren(h("table", {}, h("tbody", {}, ...r.values.map((v, i) => h("tr", {}, h("td", { text: v.label }), h("td", { text: v.display, css: i === 0 ? { "font-weight": "700" } : {} }))))), h("p", { class: "muted", text: r.basis }), ...r.notes.map((n) => h("p", { class: "muted", text: `Note: ${n}` })));
    }
    // ----------------------------------------------------------------------------------------------------------------------- kept
    async keep() {
        if (!this.shown) {
            toast("Measure something first.", { kind: "info", timeoutMs: 2000 });
            return;
        }
        const out = await this.host.store.run("measure.add", { ...this.body(), label: "" });
        if (out) {
            await this.loadKept(true);
            toast("Kept with the project. It follows the model when it changes.", { kind: "ok", timeoutMs: 2500 });
        }
    }
    async loadKept(force = false) {
        const revision = this.host.store.project?.revision ?? -1;
        if (!force && revision === this.keptRevision)
            return;
        this.keptRevision = revision;
        try {
            this.kept = (await this.host.store.api.get("/measure/list")).measurements;
        }
        catch {
            this.kept = [];
        }
        this.keptList.replaceChildren(...(this.kept.length ? this.kept.map((k) => h("div", { class: "row" }, h("span", { class: k.status === "lost" ? "lost" : "", text: k.status === "ok" && k.result ? `${(this.specs[k.kind] ?? SPECS[k.kind])?.label ?? k.kind}: ${k.result.primary.display}` : `${(this.specs[k.kind] ?? SPECS[k.kind])?.label ?? k.kind}: ${k.reason ?? "its element is gone"}` }), h("button", { class: "iconbtn tiny", type: "button", text: "×", attrs: { "aria-label": "Remove this measurement" }, on: { click: () => { void this.host.store.run("measure.remove", { id: k.id }).then(() => this.loadKept(true)); } } })))
            : [h("p", { class: "muted", text: "Nothing kept yet." })]));
        this.host.redraw();
    }
    // ----------------------------------------------------------------------------------------------------------------------- drawing
    paint(ctx, font, select, paper, ink) {
        if (this.active && this.kept.length === 0 && this.keptRevision < 0)
            void this.loadKept();
        const label = (text, at, strong) => {
            ctx.font = `${strong ? "600 " : ""}12px ${font}`;
            const w = ctx.measureText(text).width + 10;
            ctx.fillStyle = paper;
            ctx.fillRect(at[0] - w / 2, at[1] - 10, w, 20);
            ctx.strokeStyle = select;
            ctx.lineWidth = 1;
            ctx.strokeRect(at[0] - w / 2 + 0.5, at[1] - 9.5, w, 20);
            ctx.fillStyle = ink;
            ctx.textBaseline = "middle";
            ctx.textAlign = "center";
            ctx.fillText(text, at[0], at[1]);
            ctx.textAlign = "start";
        };
        const draw = (r, strong) => {
            ctx.strokeStyle = select;
            ctx.lineWidth = strong ? 2 : 1.25;
            ctx.setLineDash(strong ? [] : [4, 3]);
            for (const line of r.graphics) {
                ctx.beginPath();
                let started = false;
                for (const p of line) {
                    const s = this.host.project([p[0] ?? 0, p[1] ?? 0, p[2] ?? 0]);
                    if (!s)
                        continue;
                    if (started)
                        ctx.lineTo(s[0], s[1]);
                    else {
                        ctx.moveTo(s[0], s[1]);
                        started = true;
                    }
                }
                ctx.stroke();
            }
            ctx.setLineDash([]);
            if (r.label_at) {
                const s = this.host.project([r.label_at[0] ?? 0, r.label_at[1] ?? 0, r.label_at[2] ?? 0]);
                if (s)
                    label(r.primary.display, [s[0], s[1] - 14], strong);
            }
        };
        for (const k of this.kept)
            if (k.status === "ok" && k.result)
                draw(k.result, false);
        if (!this.active)
            return;
        if (this.shown)
            draw(this.shown, true);
        ctx.fillStyle = select;
        for (const ref of this.refs) {
            const p = ref["type"] === "point" ? ref["p"] : null;
            const s = p ? this.host.project(p) : null;
            if (s) {
                ctx.beginPath();
                ctx.arc(s[0], s[1], 4, 0, Math.PI * 2);
                ctx.fill();
            }
        }
        const hv = this.hover;
        if (!hv)
            return;
        const mode = this.mode();
        ctx.strokeStyle = select;
        ctx.lineWidth = 2;
        if (mode === "edge" && hv.snap?.edge) {
            const a = this.host.project(hv.snap.edge.a_point), b = this.host.project(hv.snap.edge.b_point);
            if (a && b) {
                ctx.beginPath();
                ctx.moveTo(a[0], a[1]);
                ctx.lineTo(b[0], b[1]);
                ctx.stroke();
            }
        }
        else if (mode === "point") {
            const at = hv.snap?.vertex ? hv.snap.vertex.point : hv.at;
            const s = this.host.project(at);
            if (s) {
                if (hv.snap?.vertex)
                    ctx.strokeRect(s[0] - 5, s[1] - 5, 10, 10);
                else {
                    ctx.beginPath();
                    ctx.arc(s[0], s[1], 4, 0, Math.PI * 2);
                    ctx.stroke();
                }
            }
        }
        else {
            const s = this.host.project(hv.at);
            if (s) {
                ctx.beginPath();
                ctx.arc(s[0], s[1], 6, 0, Math.PI * 2);
                ctx.stroke();
            }
        }
    }
}
