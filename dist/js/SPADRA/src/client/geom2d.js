export const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
export const mid = (a, b) => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
const isPt = (v) => Array.isArray(v) && v.length === 2 && typeof v[0] === "number" && typeof v[1] === "number";
const isPts = (v) => Array.isArray(v) && v.every(isPt);
export function distToSegment(p, a, b) {
    const dx = b[0] - a[0], dy = b[1] - a[1];
    const len2 = dx * dx + dy * dy;
    if (len2 === 0)
        return dist(p, a);
    const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len2));
    return Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy));
}
/** The straight pieces an entity is drawn from (lines, polyline edges, dimension lines). Circles and text have none. */
export function segments(e) {
    const g = e.geometry;
    if (e.kind === "line" && isPt(g["start"]) && isPt(g["end"]))
        return [[g["start"], g["end"]]];
    if (e.kind === "polyline" && isPts(g["points"])) {
        const pts = g["points"];
        const out = [];
        for (let i = 0; i + 1 < pts.length; i++) {
            const a = pts[i], b = pts[i + 1];
            if (a && b)
                out.push([a, b]);
        }
        const first = pts[0], last = pts[pts.length - 1];
        if (g["closed"] === true && first && last && pts.length > 2)
            out.push([last, first]);
        return out;
    }
    if (e.kind === "dimension" && e.primitives) {
        const out = [];
        for (const p of e.primitives)
            if (p.kind === "line" && isPt(p.start) && isPt(p.end))
                out.push([p.start, p.end]);
        return out;
    }
    return [];
}
/** Points along an arc (counter-clockwise from its start angle to its end angle), including both ends. */
export function arcPoints(g, steps = 48) {
    const c = g["center"], r = g["radius"], a0 = g["start_angle"], a1 = g["end_angle"];
    if (!isPt(c) || typeof r !== "number" || typeof a0 !== "number" || typeof a1 !== "number")
        return [];
    let end = a1;
    while (end < a0)
        end += Math.PI * 2;
    const n = Math.max(2, Math.ceil((steps * (end - a0)) / (Math.PI * 2)));
    const out = [];
    for (let i = 0; i <= n; i++) {
        const a = a0 + ((end - a0) * i) / n;
        out.push([c[0] + r * Math.cos(a), c[1] + r * Math.sin(a)]);
    }
    return out;
}
/** The closed outlines of a hatch as polygons (circles sampled), for hit testing and bounds. Path outlines are not read. */
export function hatchLoops(e) {
    const boundary = e.geometry["boundary"];
    if (e.kind !== "hatch" || !Array.isArray(boundary))
        return [];
    const loops = [];
    for (const raw of boundary) {
        if (raw["kind"] === "polygon" && isPts(raw["points"]))
            loops.push(raw["points"]);
        else if (raw["kind"] === "circle" && isPt(raw["center"]) && typeof raw["radius"] === "number") {
            const [cx, cy] = raw["center"], r = raw["radius"];
            loops.push(Array.from({ length: 64 }, (_, i) => [cx + r * Math.cos((i / 64) * Math.PI * 2), cy + r * Math.sin((i / 64) * Math.PI * 2)]));
        }
    }
    return loops;
}
/** True when the point is inside a hatch's outline by the even-odd rule (holes stay open). */
export function hatchContains(e, p) {
    let inside = false;
    for (const loop of hatchLoops(e))
        if (pointInPolygon(p, loop))
            inside = !inside;
    return inside;
}
export function textBox(e) {
    const g = e.geometry;
    if (e.kind !== "text" || !isPt(g["position"]))
        return null;
    const height = typeof g["height"] === "number" ? g["height"] : 250;
    const text = typeof g["text"] === "string" ? g["text"] : "";
    return { origin: g["position"], width: Math.max(1, text.length) * height * 0.6, height };
}
/** Distance from a point to the drawn entity (0 inside a text box). */
export function entityDistance(e, p) {
    const g = e.geometry;
    if (e.kind === "circle" && isPt(g["center"]) && typeof g["radius"] === "number")
        return Math.abs(dist(p, g["center"]) - g["radius"]);
    if (e.kind === "arc") {
        const pts = arcPoints(g);
        let best = Infinity;
        for (let i = 0; i + 1 < pts.length; i++)
            best = Math.min(best, distToSegment(p, pts[i], pts[i + 1]));
        return best;
    }
    if (e.kind === "hatch")
        return hatchContains(e, p) ? 0 : Infinity;
    const box = textBox(e);
    if (box) {
        const dx = Math.max(box.origin[0] - p[0], 0, p[0] - (box.origin[0] + box.width));
        const dy = Math.max(box.origin[1] - p[1], 0, p[1] - (box.origin[1] + box.height));
        return Math.hypot(dx, dy);
    }
    let best = Infinity;
    for (const [a, b] of segments(e))
        best = Math.min(best, distToSegment(p, a, b));
    return best;
}
/** The entity's bounding box, or null when it has no extent we can read. */
export function entityBounds(e) {
    const g = e.geometry;
    if (e.kind === "circle" && isPt(g["center"]) && typeof g["radius"] === "number") {
        const [cx, cy] = g["center"], r = g["radius"];
        return [[cx - r, cy - r], [cx + r, cy + r]];
    }
    const sampled = e.kind === "arc" ? arcPoints(g) : e.kind === "hatch" ? hatchLoops(e).flat() : null;
    if (sampled) {
        if (!sampled.length)
            return null;
        const xs = sampled.map((q) => q[0]), ys = sampled.map((q) => q[1]);
        return [[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)]];
    }
    const box = textBox(e);
    if (box)
        return [box.origin, [box.origin[0] + box.width, box.origin[1] + box.height]];
    const segs = segments(e);
    if (!segs.length)
        return null;
    const xs = segs.flatMap(([a, b]) => [a[0], b[0]]), ys = segs.flatMap(([a, b]) => [a[1], b[1]]);
    return [[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)]];
}
/** A window selection takes entities fully inside the box. */
export function insideBox(e, lo, hi) {
    const b = entityBounds(e);
    return !!b && b[0][0] >= lo[0] && b[0][1] >= lo[1] && b[1][0] <= hi[0] && b[1][1] <= hi[1];
}
/** 1, 2 or 5 times a power of ten, at least `minimum`: the grid spacing that keeps lines a readable distance apart. */
export function niceStep(minimum) {
    if (!(minimum > 0) || !Number.isFinite(minimum))
        return 1;
    const exp = Math.floor(Math.log10(minimum));
    const base = 10 ** exp;
    for (const m of [1, 2, 5, 10])
        if (base * m >= minimum - 1e-12 * minimum)
            return base * m;
    return base * 10;
}
/** Keep the dominant axis of a drag (the Shift constraint while drawing). */
export function orthoConstrain(from, to) {
    return Math.abs(to[0] - from[0]) >= Math.abs(to[1] - from[1]) ? [to[0], from[1]] : [from[0], to[1]];
}
export function normaliseRect(a, b) {
    return { corner: [Math.min(a[0], b[0]), Math.min(a[1], b[1])], width: Math.abs(b[0] - a[0]), height: Math.abs(b[1] - a[1]) };
}
export function segmentIntersection(a, b) {
    const [p, p2] = a, [q, q2] = b;
    const rx = p2[0] - p[0], ry = p2[1] - p[1], sx = q2[0] - q[0], sy = q2[1] - q[1];
    const den = rx * sy - ry * sx;
    if (Math.abs(den) < 1e-12)
        return null;
    const t = ((q[0] - p[0]) * sy - (q[1] - p[1]) * sx) / den;
    const u = ((q[0] - p[0]) * ry - (q[1] - p[1]) * rx) / den;
    if (t < 0 || t > 1 || u < 0 || u > 1)
        return null;
    return [p[0] + t * rx, p[1] + t * ry];
}
/** The best snap for a cursor position. Geometry snaps beat the grid; among geometry snaps the nearest wins, with endpoints first on ties. */
export function snapPoint(p, entities, tolerance, gridStep, polygons = []) {
    const rank = { end: 0, intersection: 1, center: 2, mid: 3 };
    let best = null;
    let bestScore = Infinity;
    const consider = (point, kind) => {
        const d = dist(p, point);
        if (d > tolerance)
            return;
        const score = d + (rank[kind] ?? 4) * 1e-9 * tolerance;
        if (score < bestScore) {
            best = { point, kind };
            bestScore = score;
        }
    };
    const near = [];
    for (const e of entities) {
        const g = e.geometry;
        if (e.kind === "circle" && isPt(g["center"]))
            consider(g["center"], "center");
        if (e.kind === "text" && isPt(g["position"]))
            consider(g["position"], "end");
        if (e.kind === "arc") {
            if (isPt(g["center"]))
                consider(g["center"], "center");
            const pts = arcPoints(g);
            const first = pts[0], last = pts[pts.length - 1];
            if (first)
                consider(first, "end");
            if (last)
                consider(last, "end");
            continue;
        }
        if (e.kind === "dimension") {
            for (const key of ["p1", "p2"]) {
                const v = g[key];
                if (isPt(v))
                    consider(v, "end");
            }
            continue;
        }
        for (const [a, b] of segments(e)) {
            consider(a, "end");
            consider(b, "end");
            consider(mid(a, b), "mid");
            if (distToSegment(p, a, b) <= tolerance)
                near.push([a, b]);
        }
    }
    for (const poly of polygons) { // model walls: their corners and the middle of each edge
        for (let i = 0; i < poly.length; i++) {
            const a = poly[i], b = poly[(i + 1) % poly.length];
            if (!a || !b)
                continue;
            consider(a, "end");
            consider(mid(a, b), "mid");
            if (poly.length === 2 && i === 0 && distToSegment(p, a, b) <= tolerance)
                near.push([a, b]); // a grid line or a wall's centre line can be crossed by another
        }
    }
    for (let i = 0; i < near.length; i++)
        for (let j = i + 1; j < near.length; j++) {
            const a = near[i], b = near[j];
            const hit = a && b ? segmentIntersection(a, b) : null;
            if (hit)
                consider(hit, "intersection");
        }
    if (best)
        return best;
    if (gridStep && gridStep > 0)
        return { point: [Math.round(p[0] / gridStep) * gridStep, Math.round(p[1] / gridStep) * gridStep], kind: "grid" };
    return { point: p, kind: "none" };
}
/** Fit a world box into a viewport: scale (pixels per unit) and the world point that sits at the viewport centre. */
export function fitBox(lo, hi, width, height, margin = 48) {
    const w = Math.max(hi[0] - lo[0], 1e-9), h = Math.max(hi[1] - lo[1], 1e-9);
    const scale = Math.max(1e-9, Math.min((width - 2 * margin) / w, (height - 2 * margin) / h));
    return { scale, centre: [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2] };
}
/** "12000.00" -> "12,000", "1234.50" -> "1,234.5". Text that is not a plain number is shown as it is. */
export function formatDimension(text) {
    if (!/^-?\d+(\.\d+)?$/.test(text))
        return text;
    const decimals = (text.split(".")[1] ?? "").replace(/0+$/, "").length;
    return Number(text).toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}
export function pointInPolygon(p, poly) {
    let inside = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
        const a = poly[i], b = poly[j];
        if (!a || !b)
            continue;
        if (a[1] > p[1] !== b[1] > p[1] && p[0] < ((b[0] - a[0]) * (p[1] - a[1])) / (b[1] - a[1]) + a[0])
            inside = !inside;
    }
    return inside;
}
/** 0 inside the polygon, otherwise the distance to its nearest edge. */
export function polygonDistance(p, poly) {
    if (pointInPolygon(p, poly))
        return 0;
    let best = Infinity;
    for (let i = 0; i < poly.length; i++) {
        const a = poly[i], b = poly[(i + 1) % poly.length];
        if (a && b)
            best = Math.min(best, distToSegment(p, a, b));
    }
    return best;
}
export function grips(e) {
    const g = e.geometry;
    if (e.kind === "line" && isPt(g["start"]) && isPt(g["end"])) {
        const s = g["start"], t = g["end"];
        return [{ at: s, geometry: (p) => ({ start: p, end: t }) }, { at: t, geometry: (p) => ({ start: s, end: p }) }];
    }
    if (e.kind === "polyline" && isPts(g["points"])) {
        const pts = g["points"];
        return pts.map((at, i) => ({ at, geometry: (p) => ({ points: pts.map((q, j) => (j === i ? p : q)) }) }));
    }
    if (e.kind === "circle" && isPt(g["center"]) && typeof g["radius"] === "number") {
        const c = g["center"], r = g["radius"];
        return [{ at: c, geometry: (p) => ({ center: p, radius: r }) },
            { at: [c[0] + r, c[1]], geometry: (p) => ({ center: c, radius: Math.max(dist(c, p), 1e-9) }) }];
    }
    if (e.kind === "text" && isPt(g["position"]))
        return [{ at: g["position"], geometry: (p) => ({ position: p }) }];
    return [];
}
/** The nearest grip within `tolerance` of a point, or -1. */
export function gripAt(list, p, tolerance) {
    let best = -1, bestD = tolerance;
    list.forEach((gr, i) => { const d = dist(gr.at, p); if (d <= bestD) {
        best = i;
        bestD = d;
    } });
    return best;
}
// ------------------------------------------------------------------------------------------------------------ turning and stretching
/** A point turned about a centre by an angle (radians, counter-clockwise, y up). */
export function rotateAbout(p, centre, angle) {
    const c = Math.cos(angle), s = Math.sin(angle), dx = p[0] - centre[0], dy = p[1] - centre[1];
    return [centre[0] + dx * c - dy * s, centre[1] + dx * s + dy * c];
}
/** The signed angle (radians, in (-π, π]) that turns the direction centre→from onto the direction centre→to. */
export function angleBetween(centre, from, to) {
    const a = Math.atan2(to[1] - centre[1], to[0] - centre[0]) - Math.atan2(from[1] - centre[1], from[0] - centre[0]);
    return Math.atan2(Math.sin(a), Math.cos(a));
}
/** An angle (radians) rounded to the nearest multiple of ``stepDegrees``. */
export function roundAngle(angle, stepDegrees) {
    const step = (stepDegrees * Math.PI) / 180;
    return Math.round(angle / step) * step;
}
/** The box that holds all the polygons, or null when there are none. */
export function polygonsBounds(polygons) {
    let lo = null, hi = null;
    for (const poly of polygons)
        for (const p of poly) {
            lo = lo ? [Math.min(lo[0], p[0]), Math.min(lo[1], p[1])] : [p[0], p[1]];
            hi = hi ? [Math.max(hi[0], p[0]), Math.max(hi[1], p[1])] : [p[0], p[1]];
        }
    return lo && hi ? [lo, hi] : null;
}
/** The footprint of a straight, square-ended wall of the given thickness along a→b (used to show a wall that is being drawn or stretched). */
export function wallBand(a, b, thickness) {
    const len = dist(a, b) || 1, nx = (-(b[1] - a[1]) / len) * (thickness / 2), ny = ((b[0] - a[0]) / len) * (thickness / 2);
    return [[a[0] + nx, a[1] + ny], [b[0] + nx, b[1] + ny], [b[0] - nx, b[1] - ny], [a[0] - nx, a[1] - ny]];
}
/** The point on the line a→b that is ``length`` from a (for a wall whose length is typed in). */
export function alongFrom(a, b, length) {
    const len = dist(a, b) || 1;
    return [a[0] + ((b[0] - a[0]) / len) * length, a[1] + ((b[1] - a[1]) / len) * length];
}
