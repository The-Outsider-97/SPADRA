/** Profiler's interactive tools. A tool is a list of steps (click a point, pick an object, ask for a number) and a function that turns what was collected into one or
 *  more server operations. The workspace runs the steps; the server does the geometry. Every tool listed here maps to an operation the server really has. */
import { h } from "../../../tools/web/src/dom.js";
import { openDialog } from "../../../tools/web/src/ui.js";
const p = (v, k) => v[k];
const num = (v, k) => Number(v[k]);
const layerArg = (layer) => (layer && layer !== "0" ? { layer } : {});
const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
/** A corner picked as a point (``vertex``) or as two lines (``edges``). */
const cornerArgs = (v) => (v["r1"] ? { edges: [v["r0"], v["r1"]] } : { vertex: v["r0"] });
/** A selection that already names a corner (one point, or two lines) skips straight to the size. */
const cornerSeed = (s) => {
    if (s.ids.length === 1 && s.vertices.length === 1)
        return { values: { r0: s.vertices[0] }, step: 2 };
    if (s.ids.length === 2 && s.edges.length === 2)
        return { values: { r0: s.edges[0], r1: s.edges[1] }, step: 2 };
    return null;
};
export const DRAW_TOOLS = [
    { id: "line", label: "Line", icon: "line", hint: "Click the start, then the end. Esc finishes.", repeat: true, steps: [
            { key: "a", kind: "point", prompt: "Start point" }, { key: "b", kind: "point", prompt: "End point", band: "line" }
        ],
        build: (v, _s, l) => [{ op: "line", args: { a: p(v, "a"), b: p(v, "b"), ...layerArg(l) } }] },
    { id: "polyline", label: "Polyline", icon: "polyline", hint: "Click each corner; Enter or double-click finishes (type C then Enter to close).", repeat: true, steps: [
            { key: "points", kind: "points", prompt: "Next corner", need: 2, band: "line" }
        ],
        build: (v, _s, l) => [{ op: "polyline", args: { points: v["points"], closed: v["closed"] === true, constrain: true, ...layerArg(l) } }] },
    { id: "rectangle", label: "Rectangle", icon: "rectangle", hint: "Click two opposite corners.", repeat: true, steps: [
            { key: "p1", kind: "point", prompt: "First corner" }, { key: "p2", kind: "point", prompt: "Opposite corner", band: "rect" }
        ],
        build: (v, _s, l) => [{ op: "rectangle", args: { p1: p(v, "p1"), p2: p(v, "p2"), ...layerArg(l) } }] },
    { id: "circle", label: "Circle", icon: "circle", hint: "Click the centre, then a point on the circle.", repeat: true, steps: [
            { key: "center", kind: "point", prompt: "Centre" }, { key: "through", kind: "point", prompt: "Point on the circle", band: "circle" }
        ],
        build: (v, _s, l) => [{ op: "circle", args: { center: p(v, "center"), through: p(v, "through"), ...layerArg(l) } }] },
    { id: "circle3", label: "Circle (3 points)", icon: "circle", hint: "Click three points on the circle.", repeat: true, steps: [
            { key: "p1", kind: "point", prompt: "First point" }, { key: "p2", kind: "point", prompt: "Second point", band: "line" }, { key: "p3", kind: "point", prompt: "Third point" }
        ],
        build: (v, _s, l) => [{ op: "circle", args: { p1: p(v, "p1"), p2: p(v, "p2"), p3: p(v, "p3"), ...layerArg(l) } }] },
    { id: "ellipse", label: "Ellipse", icon: "circle", hint: "Click the centre, then a corner of the ellipse's bounding box.", repeat: true, steps: [
            { key: "center", kind: "point", prompt: "Centre" }, { key: "corner", kind: "point", prompt: "Corner of its box", band: "rect" }
        ],
        build: (v, _s, l) => { const c = p(v, "center"), q = p(v, "corner"); return [{ op: "ellipse", args: { center: c, rx: Math.abs(q[0] - c[0]) || 1e-6, ry: Math.abs(q[1] - c[1]) || 1e-6, rotation: 0, ...layerArg(l) } }]; } },
    { id: "polygon", label: "Polygon", icon: "subdivision", hint: "Click the centre and a corner; you are asked for the number of sides.", repeat: true, steps: [
            { key: "center", kind: "point", prompt: "Centre" }, { key: "corner", kind: "point", prompt: "A corner", band: "circle" },
            { key: "sides", kind: "number", prompt: "Number of sides", label: "Sides", value: 6, min: 3 }
        ],
        build: (v, _s, l) => { const c = p(v, "center"), q = p(v, "corner"); return [{ op: "polygon", args: { center: c, radius: dist(c, q), sides: num(v, "sides"), rotation: Math.atan2(q[1] - c[1], q[0] - c[0]) * 180 / Math.PI, ...layerArg(l) } }]; } },
    { id: "slot", label: "Slot", icon: "split", hint: "Click the two end centres, then give the width.", repeat: true, steps: [
            { key: "p1", kind: "point", prompt: "First end centre" }, { key: "p2", kind: "point", prompt: "Second end centre", band: "line" },
            { key: "width", kind: "number", prompt: "Slot width", label: "Width", value: 10, min: 1e-6 }
        ],
        build: (v, _s, l) => [{ op: "slot", args: { p1: p(v, "p1"), p2: p(v, "p2"), width: num(v, "width"), ...layerArg(l) } }] },
    { id: "construction_line", label: "Construction line", icon: "line", hint: "Click two points; it guides but is not part of the profile.", repeat: true, steps: [
            { key: "a", kind: "point", prompt: "First point" }, { key: "b", kind: "point", prompt: "Second point", band: "line" }
        ],
        build: (v, _s, l) => [{ op: "construction_line", args: { a: p(v, "a"), b: p(v, "b"), ...layerArg(l) } }] },
    { id: "arc_ser", label: "Arc: start, end, radius", icon: "fillet", hint: "Click the start and end, then give the radius.", repeat: true, steps: [
            { key: "start", kind: "point", prompt: "Start point" }, { key: "end", kind: "point", prompt: "End point", band: "line" },
            { key: "radius", kind: "number", prompt: "Arc radius", label: "Radius", value: 50, min: 1e-6 }
        ],
        build: (v, _s, l) => [{ op: "arc_start_end_radius", args: { start: p(v, "start"), end: p(v, "end"), radius: num(v, "radius"), ...layerArg(l) } }] },
    { id: "arc_ce", label: "Arc: centre, ends", icon: "fillet", hint: "Click the centre, the start, then the end (counter-clockwise).", repeat: true, steps: [
            { key: "center", kind: "point", prompt: "Centre" }, { key: "start", kind: "point", prompt: "Start point", band: "line" }, { key: "end", kind: "point", prompt: "End point", band: "line" }
        ],
        build: (v, _s, l) => [{ op: "arc_center_ends", args: { center: p(v, "center"), start: p(v, "start"), end: p(v, "end"), ccw: true, ...layerArg(l) } }] },
    { id: "arc3", label: "Arc: 3 points", icon: "fillet", hint: "Click the start, a point on the arc, then the end.", repeat: true, steps: [
            { key: "start", kind: "point", prompt: "Start point" }, { key: "mid", kind: "point", prompt: "A point on the arc", band: "line" }, { key: "end", kind: "point", prompt: "End point" }
        ],
        build: (v, _s, l) => [{ op: "arc_three_point", args: { start: p(v, "start"), mid: p(v, "mid"), end: p(v, "end"), ...layerArg(l) } }] },
    { id: "spline_math", label: "Spline: control points", icon: "bend", hint: "Mathematical B-spline over a control polygon; Enter finishes (4 points or more).", repeat: true, steps: [
            { key: "points", kind: "points", prompt: "Next control point", need: 4, band: "line" }
        ],
        build: (v, _s, l) => [{ op: "spline", args: { mode: "math", degree: 3, points: v["points"], ...layerArg(l) } }] },
    { id: "spline_max", label: "Spline: Bézier", icon: "bend", hint: "3ds Max style piecewise Bézier through the points; Enter finishes (2 points or more).", repeat: true, steps: [
            { key: "points", kind: "points", prompt: "Next knot", need: 2, band: "line" }
        ],
        build: (v, _s, l) => [{ op: "spline", args: { mode: "max", points: v["points"], ...layerArg(l) } }] },
    { id: "spline_revit", label: "Spline: through points", icon: "bend", hint: "Revit style curve through every point; Enter finishes (2 points or more).", repeat: true, steps: [
            { key: "points", kind: "points", prompt: "Next point", need: 2, band: "line" }
        ],
        build: (v, _s, l) => [{ op: "spline", args: { mode: "revit", points: v["points"], ...layerArg(l) } }] },
    // Fillet and chamfer: click the corner (or the two lines), then type the size. The corner point is enough, so it is two steps.
    { id: "fillet", label: "Fillet", icon: "fillet", hint: "Click a corner, then type the radius. (Or click the two lines.)", repeat: true,
        steps: [{ key: "r0", kind: "ref", prompt: "Click the corner to round (or the first line)" }, { key: "r1", kind: "ref", prompt: "Click the second line", skip: (v, h) => h.isVertex(String(v["r0"])) },
            { key: "radius", kind: "number", prompt: "Fillet radius", label: "Radius", value: 10, min: 1e-6 }],
        seed: (s) => cornerSeed(s), build: (v) => [{ op: "fillet", args: { ...cornerArgs(v), radius: num(v, "radius"), associative: true } }] },
    { id: "chamfer", label: "Chamfer", icon: "chamfer", hint: "Click a corner, then type the size. (Or click the two lines.)", repeat: true,
        steps: [{ key: "r0", kind: "ref", prompt: "Click the corner to cut (or the first line)" }, { key: "r1", kind: "ref", prompt: "Click the second line", skip: (v, h) => h.isVertex(String(v["r0"])) },
            { key: "d1", kind: "number", prompt: "Chamfer size", label: "Size", value: 10, min: 1e-6 }],
        seed: (s) => cornerSeed(s), build: (v) => [{ op: "chamfer", args: { ...cornerArgs(v), d1: num(v, "d1"), associative: true } }] },
    { id: "chamfer2", label: "Chamfer with two sizes", icon: "chamfer", hint: "Click a corner, then the two set-backs.", repeat: true,
        steps: [{ key: "r0", kind: "ref", prompt: "Click the corner to cut (or the first line)" }, { key: "r1", kind: "ref", prompt: "Click the second line", skip: (v, h) => h.isVertex(String(v["r0"])) },
            { key: "d1", kind: "number", prompt: "First set-back", label: "Distance 1", value: 10, min: 1e-6 }, { key: "d2", kind: "number", prompt: "Second set-back", label: "Distance 2", value: 10, min: 1e-6 }],
        seed: (s) => cornerSeed(s), build: (v) => [{ op: "chamfer", args: { ...cornerArgs(v), d1: num(v, "d1"), d2: num(v, "d2"), associative: true } }] },
];
export const MODIFY_TOOLS = [
    { id: "move", label: "Move", icon: "select", needs: "objects", hint: "Click a base point, then the new position.", steps: [
            { key: "from", kind: "point", prompt: "Base point" }, { key: "to", kind: "point", prompt: "Move to", band: "line" }
        ],
        build: (v, s) => [{ op: "move", args: { ids: s.ids, from: p(v, "from"), to: p(v, "to") } }] },
    { id: "copy", label: "Copy", icon: "copy", needs: "objects", hint: "Click a base point, then where the copy goes.", repeat: false, steps: [
            { key: "from", kind: "point", prompt: "Base point" }, { key: "to", kind: "point", prompt: "Copy to", band: "line" }
        ],
        build: (v, s) => { const a = p(v, "from"), b = p(v, "to"); return [{ op: "copy", args: { ids: s.ids, dx: b[0] - a[0], dy: b[1] - a[1] } }]; } },
    { id: "rotate", label: "Rotate", icon: "bend", needs: "objects", hint: "Click the centre of rotation, then give the angle in degrees.", steps: [
            { key: "about", kind: "point", prompt: "Centre of rotation" }, { key: "angle", kind: "number", prompt: "Angle (degrees, counter-clockwise)", label: "Angle", value: 90 }
        ],
        build: (v, s) => [{ op: "rotate", args: { ids: s.ids, about: p(v, "about"), angle: num(v, "angle"), degrees: true, release: true } }] },
    { id: "scale", label: "Resize", icon: "extend", needs: "objects", hint: "Click the fixed point, then give the scale factor.", steps: [
            { key: "about", kind: "point", prompt: "Fixed point" }, { key: "factor", kind: "number", prompt: "Scale factor", label: "Factor", value: 2, min: 1e-6 }
        ],
        build: (v, s) => [{ op: "scale", args: { ids: s.ids, about: p(v, "about"), factor: num(v, "factor") } }] },
    { id: "mirror", label: "Mirror", icon: "mirror", needs: "objects", hint: "Click two points of the mirror line; the original is kept.", steps: [
            { key: "a", kind: "point", prompt: "First point of the mirror line" }, { key: "b", kind: "point", prompt: "Second point", band: "line" }
        ],
        build: (v, s) => [{ op: "mirror", args: { ids: s.ids, a: p(v, "a"), b: p(v, "b"), copy: true } }] },
    { id: "mirror_move", label: "Mirror (replace)", icon: "mirror", needs: "objects", hint: "Click two points of the mirror line; the original is replaced.", steps: [
            { key: "a", kind: "point", prompt: "First point of the mirror line" }, { key: "b", kind: "point", prompt: "Second point", band: "line" }
        ],
        build: (v, s) => [{ op: "mirror", args: { ids: s.ids, a: p(v, "a"), b: p(v, "b"), copy: false } }] },
    { id: "stretch", label: "Stretch", icon: "bend", hint: "Click two corners around the points to move, then a base point and the new position.", steps: [
            { key: "w1", kind: "point", prompt: "First corner of the stretch window" }, { key: "w2", kind: "point", prompt: "Opposite corner", band: "rect" },
            { key: "from", kind: "point", prompt: "Base point" }, { key: "to", kind: "point", prompt: "Stretch to", band: "line" }
        ],
        build: (v) => {
            const a = p(v, "w1"), b = p(v, "w2"), f = p(v, "from"), t = p(v, "to");
            return [{ op: "stretch", args: { window: [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])], dx: t[0] - f[0], dy: t[1] - f[1] } }];
        } },
    { id: "trim", label: "Trim", icon: "trim", hint: "Click the part of a line you want to remove.", repeat: true, steps: [{ key: "pick", kind: "pair", prompt: "Click the part to trim" }],
        build: (v) => { const [edge, pt] = v["pick"]; return [{ op: "trim", args: { edge, pick: pt } }]; } },
    { id: "extend", label: "Extend", icon: "extend", hint: "Click near the end of a line to extend it to the next object.", repeat: true, steps: [{ key: "pick", kind: "pair", prompt: "Click near the end to extend" }],
        build: (v) => { const [edge, pt] = v["pick"]; return [{ op: "extend", args: { edge, near: pt } }]; } },
    { id: "split", label: "Split", icon: "split", hint: "Click a point on a line to split it in two.", repeat: true, steps: [{ key: "pick", kind: "pair", prompt: "Click where to split" }],
        build: (v) => { const [edge, pt] = v["pick"]; return [{ op: "split", args: { edge, point: pt } }]; } },
    { id: "cut", label: "Cut", icon: "trim", hint: "Click two points for a cutting line through the selection (or everything if nothing is selected).", steps: [
            { key: "a", kind: "point", prompt: "Start of the cutting line" }, { key: "b", kind: "point", prompt: "End of the cutting line", band: "line" }
        ],
        build: (v, s) => [{ op: "cut", args: { a: p(v, "a"), b: p(v, "b"), ...(s.edges.length ? { ids: s.edges } : {}) } }] },
    { id: "offset", label: "Offset", icon: "offset", needs: "edges2", hint: "Give the distance and the side (left or right of the line's direction, start to end).", steps: [
            { key: "distance", kind: "number", prompt: "Offset distance", label: "Distance", value: 10, min: 1e-6 },
            { key: "side", kind: "choice", prompt: "Side", label: "Side of the line's direction", value: "left", options: [["left", "Left of the direction (start to end)"], ["right", "Right of the direction"]] }
        ],
        build: (v, s) => [{ op: "offset", args: { edges: s.edges, distance: num(v, "distance"), side: String(v["side"]), parametric: true } }] },
    { id: "join", label: "Join", icon: "weld", needs: "edges2", hint: "Joins two selected lines or arcs that share an end into one.", steps: [], build: (_v, s) => [{ op: "join", args: { edges: s.edges } }] },
    { id: "weld", label: "Weld", icon: "weld", hint: "Merges points that are closer than the tolerance.", steps: [{ key: "tol", kind: "number", prompt: "Weld tolerance", label: "Tolerance", value: 0.01, min: 1e-9 }],
        build: (v) => [{ op: "weld", args: { tol: num(v, "tol") } }] },
    { id: "connect", label: "Connect", icon: "weld", needs: "vertices", hint: "Draws a line between two selected points.", steps: [], build: (_v, s) => [{ op: "connect", args: { vertices: s.vertices } }] },
    { id: "attach", label: "Attach", icon: "attach-roof", hint: "Click a point, then the line it should lie on.", repeat: true, steps: [
            { key: "vertex", kind: "vertex", prompt: "Point to attach" }, { key: "target", kind: "edge", prompt: "Line to attach it to" }
        ],
        build: (v) => [{ op: "attach", args: { vertex: v["vertex"], target: v["target"] } }] },
    { id: "group", label: "Group", icon: "boolean", needs: "objects", hint: "Groups the selection so it moves as one.", steps: [], build: (_v, s) => [{ op: "group", args: { ids: s.ids, kind: "rigid" } }] },
    { id: "group_sel", label: "Selection group", icon: "boolean", needs: "objects", hint: "A temporary named selection.", steps: [], build: (_v, s) => [{ op: "group", args: { ids: s.ids, kind: "selection" } }] },
    { id: "color", label: "Colour", icon: "hatch", needs: "objects", hint: "Sets the colour of the selected lines.", steps: [{ key: "color", kind: "text", prompt: "Colour (#RRGGBB, or empty for the default)", label: "Colour", value: "#F6F6F6" }],
        build: (v, s) => [{ op: "color", args: { ids: s.ids, color: String(v["color"] || "") || null } }] },
];
/** One running tool: collects the steps' answers, previews the rubber band, and builds the operations. */
export class ToolRun {
    def;
    host;
    extraSteps;
    cursor = "crosshair";
    i = 0;
    values = {};
    remembered = {};
    pts = [];
    last = null;
    busy = false;
    snapshot;
    constructor(def, host, extraSteps = []) {
        this.def = def;
        this.host = host;
        this.extraSteps = extraSteps;
        this.snapshot = host.selection();
    }
    get steps() { return this.extraSteps.length ? this.extraSteps : this.def.steps; }
    get step() { return this.steps[this.i]; }
    get prompt() { const s = this.step; return s ? (s.kind === "points" ? `${s.prompt} (${this.pts.length} so far; Enter to finish)` : s.prompt) : this.def.hint; }
    from() { return this.last; }
    reset() { this.i = 0; this.values = {}; this.pts = []; this.last = null; this.snapshot = this.host.selection(); }
    async start() {
        const need = this.def.needs, sel = this.host.selection();
        if (need === "objects" && sel.ids.length === 0) {
            this.host.say(`${this.def.label}: select what to ${this.def.label.toLowerCase()} first (click, or drag a window), then choose the tool again.`);
            this.host.ended();
            return;
        }
        if (need === "edges2" && sel.edges.length < (this.def.id === "join" ? 2 : 1)) {
            this.host.say(`${this.def.label}: select ${this.def.id === "join" ? "two lines or arcs" : "a line or arc"} first.`);
            this.host.ended();
            return;
        }
        if (need === "vertices" && sel.vertices.length < 2) {
            this.host.say("Connect: select two points first.");
            this.host.ended();
            return;
        }
        this.snapshot = sel;
        const direct = this.def.quick?.(sel) ?? null;
        if (direct) {
            await this.host.perform(direct, this.def.label);
            this.host.ended();
            return;
        }
        const seeded = this.def.seed?.(sel) ?? null;
        if (seeded) {
            Object.assign(this.values, seeded.values);
            this.i = seeded.step;
        }
        await this.advance();
    }
    async advance() {
        for (;;) {
            const s = this.step;
            if (!s) {
                await this.finish();
                return;
            }
            if (s.skip?.(this.values, this.host)) {
                this.i++;
                continue;
            }
            if (s.kind === "number") {
                const v = await this.host.askNumber(this.def.label, s.label ?? s.prompt, Number(this.remembered[s.key] ?? s.value ?? 1), s.min);
                if (v === null) {
                    this.host.ended();
                    return;
                }
                this.values[s.key] = v;
                this.remembered[s.key] = v;
                this.i++;
                continue;
            } // a repeating tool (Fillet) offers the last radius again, not its default
            if (s.kind === "text") {
                const v = await this.host.askText(this.def.label, s.label ?? s.prompt, String(s.value ?? ""));
                if (v === null) {
                    this.host.ended();
                    return;
                }
                this.values[s.key] = v;
                this.i++;
                continue;
            }
            if (s.kind === "choice") {
                const v = await this.host.askChoice(this.def.label, s.label ?? s.prompt, s.options ?? [], String(s.value ?? ""));
                if (v === null) {
                    this.host.ended();
                    return;
                }
                this.values[s.key] = v;
                this.i++;
                continue;
            }
            this.host.say(this.prompt);
            return;
        }
    }
    async click(pt) {
        if (this.busy)
            return;
        const s = this.step;
        if (!s)
            return;
        this.busy = true;
        try {
            if (s.kind === "point") {
                this.values[s.key] = pt;
                this.last = pt;
                this.i++;
            }
            else if (s.kind === "points") {
                this.pts.push(pt);
                this.last = pt;
                this.values[s.key] = [...this.pts];
                return this.host.say(this.prompt);
            }
            else if (s.kind === "edge" || s.kind === "vertex" || s.kind === "ref") {
                const id = this.host.pickAt(pt, s.kind);
                if (!id) {
                    this.host.say(`${s.prompt}: nothing there to pick.`);
                    return;
                }
                this.values[s.key] = id;
                this.i++;
            }
            else if (s.kind === "pair") {
                const id = this.host.pickAt(pt, "edge");
                if (!id) {
                    this.host.say("Click on a line.");
                    return;
                }
                this.values[s.key] = [id, pt];
                this.i++;
            }
            else if (s.kind === "region") {
                const r = this.host.regionAt(pt);
                if (!r) {
                    this.host.say("Click inside a closed region.");
                    return;
                }
                this.values[s.key] = r;
                this.i++;
            }
            await this.advance();
        }
        finally {
            this.busy = false;
        }
    }
    dblclick() { void this.enter(); }
    /** Enter finishes a multi-point step; typing "c" closes a polyline. */
    async enter(close = false) {
        const s = this.step;
        if (!s)
            return;
        if (s.optional && s.kind === "ref") {
            this.i++;
            await this.advance();
            return;
        }
        if (s.kind !== "points")
            return;
        if (this.pts.length < (s.need ?? 2)) {
            this.host.say(`Needs at least ${s.need ?? 2} points.`);
            return;
        }
        if (close)
            this.values["closed"] = true;
        this.i++;
        await this.advance();
    }
    /** A typed or entered point (command line). */
    async point(pt) { await this.click(pt); }
    async finish() {
        const builds = await this.def.build(this.values, this.snapshot, this.host.layer());
        const ok = builds.length ? await this.host.perform(builds, this.def.label) : true;
        if (this.def.repeat && ok) {
            this.reset();
            await this.advance();
        }
        else
            this.host.ended();
    }
    cancel() { this.host.ended(); }
    preview(api, cursor) {
        const s = this.step;
        if (!s || !cursor)
            return;
        const c = api.ctx;
        c.save();
        c.strokeStyle = "#ffd75e";
        c.fillStyle = "rgba(255,215,94,.08)";
        c.lineWidth = 1;
        c.setLineDash([5, 4]);
        const pts = s.kind === "points" ? this.pts : this.last ? [this.last] : [];
        if (s.kind === "points" && pts.length) {
            c.beginPath();
            c.moveTo(api.sx(pts[0][0]), api.sy(pts[0][1]));
            for (const q of pts)
                c.lineTo(api.sx(q[0]), api.sy(q[1]));
            c.lineTo(api.sx(cursor[0]), api.sy(cursor[1]));
            c.stroke();
        }
        else if (s.band && this.last) {
            const a = this.last;
            if (s.band === "line") {
                c.beginPath();
                c.moveTo(api.sx(a[0]), api.sy(a[1]));
                c.lineTo(api.sx(cursor[0]), api.sy(cursor[1]));
                c.stroke();
            }
            else if (s.band === "rect") {
                c.beginPath();
                c.rect(api.sx(a[0]), api.sy(a[1]), api.sx(cursor[0]) - api.sx(a[0]), api.sy(cursor[1]) - api.sy(a[1]));
                c.fill();
                c.stroke();
            }
            else {
                const r = Math.hypot(api.sx(cursor[0]) - api.sx(a[0]), api.sy(cursor[1]) - api.sy(a[1]));
                c.beginPath();
                c.arc(api.sx(a[0]), api.sy(a[1]), r, 0, Math.PI * 2);
                c.stroke();
            }
        }
        c.restore();
    }
}
export function formDialog(title, fields, confirm, intro) {
    const inputs = new Map();
    const rows = new Map();
    const read = () => {
        const out = {};
        for (const f of fields) {
            const el = inputs.get(f.key);
            out[f.key] = f.kind === "check" ? el.checked : f.kind === "number" ? (el.value === "" ? null : Number(el.value)) : el.value;
        }
        return out;
    };
    const refresh = () => { const v = read(); for (const f of fields)
        rows.get(f.key)?.toggleAttribute("hidden", f.show ? !f.show(v) : false); };
    const body = h("div", { class: "pf-form" }, intro ? h("p", { class: "note", text: intro }) : null, ...fields.map((f) => {
        let el;
        if (f.kind === "select")
            el = h("select", {}, ...(f.options ?? []).map(([v, t]) => h("option", { value: v, text: t, selected: v === String(f.value ?? "") })));
        else if (f.kind === "check")
            el = h("input", { type: "checkbox", checked: f.value === true });
        else
            el = h("input", { type: f.kind === "number" ? "number" : "text", value: f.value === undefined ? "" : String(f.value), attrs: { ...(f.kind === "number" ? { step: f.step ?? "any", ...(f.min !== undefined ? { min: f.min } : {}) } : {}), autocomplete: "off" } });
        el.addEventListener("input", refresh);
        el.addEventListener("change", refresh);
        inputs.set(f.key, el);
        const row = h("label", { class: `pf-field${f.kind === "check" ? " check" : ""}` }, h("span", { text: f.label }), el, f.note ? h("small", { class: "note", text: f.note }) : null);
        rows.set(f.key, row);
        return row;
    }));
    refresh();
    return new Promise((resolve) => {
        const dlg = openDialog({ title, size: "narrow", body, focus: () => [...inputs.values()][0] ?? null,
            actions: [{ label: confirm, kind: "primary", run: () => read() }] });
        void dlg.result.then((r) => resolve(r));
    });
}
