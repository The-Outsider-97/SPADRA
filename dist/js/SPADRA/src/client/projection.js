const EPS = 1e-9;
export function inTriangle(p, t, tolerance = 0) {
    const [x0 = 0, y0 = 0, x1 = 0, y1 = 0, x2 = 0, y2 = 0] = t;
    const side = (ax, ay, bx, by) => (bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax);
    const d1 = side(x0, y0, x1, y1), d2 = side(x1, y1, x2, y2), d3 = side(x2, y2, x0, y0);
    const neg = d1 < -EPS || d2 < -EPS || d3 < -EPS, pos = d1 > EPS || d2 > EPS || d3 > EPS;
    if (!(neg && pos))
        return true;
    return tolerance > 0 && Math.min(segDist(p, x0, y0, x1, y1), segDist(p, x1, y1, x2, y2), segDist(p, x2, y2, x0, y0)) <= tolerance;
}
function segDist(p, ax, ay, bx, by) {
    const dx = bx - ax, dy = by - ay;
    const len2 = dx * dx + dy * dy;
    const t = len2 < EPS ? 0 : Math.max(0, Math.min(1, ((p[0] - ax) * dx + (p[1] - ay) * dy) / len2));
    return Math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy));
}
/** Even-odd containment in a set of loops: a point in a hole is outside. */
export function insideLoops(p, loops) {
    let inside = false;
    for (const loop of loops) {
        for (let i = 0, j = loop.length - 1; i < loop.length; j = i++) {
            const a = loop[i], b = loop[j];
            if (!a || !b)
                continue;
            if ((a[1] > p[1]) !== (b[1] > p[1]) && p[0] < ((b[0] - a[0]) * (p[1] - a[1])) / (b[1] - a[1]) + a[0])
                inside = !inside;
        }
    }
    return inside;
}
/** True when a point is on what an item shows: its cut, a surface facing the viewer, or (within ``tolerance``) one of its drawn lines. */
export function itemContains(item, p, tolerance = 0) {
    if (item.cut.length && insideLoops(p, item.cut))
        return true;
    for (const t of item.tris)
        if (inTriangle(p, t, 0))
            return true;
    if (tolerance > 0) {
        for (const e of item.edges)
            if (segDist(p, e[0] ?? 0, e[1] ?? 0, e[2] ?? 0, e[3] ?? 0) <= tolerance)
                return true;
        if (nearLoops(p, item.cut, tolerance))
            return true;
    }
    return false;
}
function nearLoops(p, loops, tolerance) {
    if (tolerance <= 0)
        return false;
    for (const loop of loops)
        for (let i = 0; i < loop.length; i++) {
            const a = loop[i], b = loop[(i + 1) % loop.length];
            if (a && b && segDist(p, a[0], a[1], b[0], b[1]) <= tolerance)
                return true;
        }
    return false;
}
/** The element under a point: the one painted last (nearest the viewer) wins, and a cut beats anything beyond it. */
export function projectionAt(projection, p, tolerance = 0) {
    for (let i = projection.items.length - 1; i >= 0; i--) {
        const item = projection.items[i];
        if (item && item.cut.length && (insideLoops(p, item.cut) || nearLoops(p, item.cut, tolerance)))
            return item.id;
    }
    for (let i = projection.items.length - 1; i >= 0; i--) {
        const item = projection.items[i];
        if (item && itemContains(item, p, tolerance))
            return item.id;
    }
    return null;
}
/** Points worth snapping to: the corners of every cut loop and the end points of every drawn line. */
export function snapPolygons(projection) {
    const out = [];
    for (const item of projection.items) {
        for (const loop of item.cut)
            out.push(loop);
        for (const e of item.edges)
            out.push([[e[0] ?? 0, e[1] ?? 0], [e[2] ?? 0, e[3] ?? 0]]);
    }
    return out;
}
// ---------------------------------------------------------------------------------------------------------------------- labels
/** "Level 02", 3200 (mm) → "Level 02  +3.200". */
export function levelLabel(name, z, mmPerUnit) {
    const m = (z * mmPerUnit) / 1000;
    return `${name}  ${m < 0 ? "-" : "+"}${Math.abs(m).toFixed(3)}`;
}
/** The letter in a section bubble: "Section A-A" → "A"; any other name → its first letter. */
export function markLabel(name) {
    const m = /^Section\s+(\S+?)-\1$/i.exec(name.trim());
    return (m?.[1] ?? name.trim().charAt(0) ?? "?").toUpperCase();
}
/** The corners of the arrowheads of a section mark, in drawing units. ``size`` is the arrow's length in the same units. */
export function markArrows(mark, size) {
    const [lx, ly] = mark.look;
    const out = [];
    for (const base of [mark.a, mark.b]) {
        const tip = [base[0] + lx * size, base[1] + ly * size];
        const back = [tip[0] - lx * size * 0.35, tip[1] - ly * size * 0.35];
        out.push({ from: base, tip, left: [back[0] - ly * size * 0.2, back[1] + lx * size * 0.2], right: [back[0] + ly * size * 0.2, back[1] - lx * size * 0.2] });
    }
    return out;
}
// ------------------------------------------------------------------------------------------------------------------- SVG paths
const r = (n) => String(Math.round(n * 1000) / 1000);
/** All of an item's surfaces facing the viewer as one path (filled non-zero, so there are no seams between triangles). */
export function surfacePath(item, map) {
    return item.tris.map((t) => {
        const a = map([t[0] ?? 0, t[1] ?? 0]), b = map([t[2] ?? 0, t[3] ?? 0]), c = map([t[4] ?? 0, t[5] ?? 0]);
        return `M${r(a[0])} ${r(a[1])}L${r(b[0])} ${r(b[1])}L${r(c[0])} ${r(c[1])}Z`;
    }).join("");
}
export function edgePath(item, map) {
    return item.edges.map((e) => {
        const a = map([e[0] ?? 0, e[1] ?? 0]), b = map([e[2] ?? 0, e[3] ?? 0]);
        return `M${r(a[0])} ${r(a[1])}L${r(b[0])} ${r(b[1])}`;
    }).join("");
}
/** An item's cut as one path, to be filled even-odd so openings are holes. */
export function cutPath(item, map) {
    return item.cut.map((loop) => loop.map((p, i) => { const q = map(p); return `${i ? "L" : "M"}${r(q[0])} ${r(q[1])}`; }).join("") + "Z").join("");
}
/** How a cut material is drawn in a section: fill opacity and the hatch laid over it (concrete diagonal, reinforced concrete heavier and crossed, insulation crossed, granular fill dotted). */
export function cutStyle(material) {
    const m = material.toLowerCase();
    if (/(eps|xps|pir|mineral wool|foam glass|insulation)/.test(m))
        return { alpha: 0.06, hatch: "cross" };
    if (/(sand|gravel|fill)/.test(m))
        return { alpha: 0.12, hatch: "dots" };
    if (/reinforced/.test(m))
        return { alpha: 0.6, hatch: "cross" }; // declared bars: a darker, crossed hatch than plain concrete
    if (/(concrete|masonry)/.test(m))
        return { alpha: 0.45, hatch: "diag" };
    return { alpha: 0.28, hatch: "none" };
}
/** How strongly an element's cut is filled: structure is solid, glazing and doors are lighter so they read as different from the wall. */
export function cutWeight(category) {
    return category === "window" ? 0.25 : category === "door" ? 0.55 : 1;
}
/** The levels worth drawing: those within about 300 mm of what the view shows, so a roof level far above a low building is not drawn in mid air. */
export function visibleLevels(projection, mmPerUnit) {
    const b = projection.bounds;
    if (!b)
        return [];
    const slack = 300 / mmPerUnit;
    return projection.levels.filter((l) => l.z >= b[0][1] - slack && l.z <= b[1][1] + slack);
}
/** Hatch lines (or dots) laid over one closed loop, as a path in the loop's own units, clipped to the loop (even-odd, so any simple outline works). */
export function hatchPath(loop, hatch, step) {
    if (loop.length < 3 || !(step > 0))
        return "";
    const xs = loop.map((p) => p[0]), ys = loop.map((p) => p[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    const edges = loop.map((a, i) => [a, loop[(i + 1) % loop.length]]);
    const out = [];
    if (hatch === "dots") {
        const inside = (x, y) => { let c = false; for (const [a, b] of edges)
            if ((a[1] > y) !== (b[1] > y) && x < ((b[0] - a[0]) * (y - a[1])) / (b[1] - a[1]) + a[0])
                c = !c; return c; };
        let row = 0;
        for (let y = y0 + step / 2; y < y1; y += step, row++)
            for (let x = x0 + step / 2 + (row % 2 ? step / 2 : 0); x < x1; x += step)
                if (inside(x, y))
                    out.push(`M${r(x)} ${r(y)}l${r(step * 0.02)} 0`);
        return out.join("");
    }
    const lines = (sign) => {
        const lo = Math.min(x0 - sign * y0, x0 - sign * y1, x1 - sign * y0, x1 - sign * y1), hi = Math.max(x0 - sign * y0, x0 - sign * y1, x1 - sign * y0, x1 - sign * y1);
        for (let c = Math.ceil(lo / step) * step; c < hi; c += step) {
            const hits = [];
            for (const [a, b] of edges) {
                const ua = a[0] - sign * a[1] - c, ub = b[0] - sign * b[1] - c;
                if ((ua < 0) !== (ub < 0) && ua !== ub) {
                    const t = ua / (ua - ub);
                    hits.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]);
                }
            }
            hits.sort((p, q) => p[0] - q[0] || p[1] - q[1]);
            for (let i = 0; i + 1 < hits.length; i += 2) {
                const p = hits[i], q = hits[i + 1];
                out.push(`M${r(p[0])} ${r(p[1])}L${r(q[0])} ${r(q[1])}`);
            }
        }
    };
    lines(1);
    if (hatch === "cross")
        lines(-1);
    return out.join("");
}
/** The opening direction of a door or casement window on a plan, as an SVG path: the open leaf and the arc it sweeps. Empty when the symbol
 *  carries none (a fixed window, or a view that hides opening direction). The arc runs counter-clockwise in drawing coordinates. */
export function swingPath(sym) {
    if (!(sym?.hinge && sym.leaf_end && sym.radius !== undefined && sym.start_angle !== undefined && sym.end_angle !== undefined))
        return "";
    const box = (q) => (q && q.length >= 3 ? `M${q.map((c) => `${c[0]} ${c[1]}`).join("L")}Z` : "");
    const one = (c, leaf, r, a0, a1, poly) => {
        const sx = c[0] + r * Math.cos(a0), sy = c[1] + r * Math.sin(a0), ex = c[0] + r * Math.cos(a1), ey = c[1] + r * Math.sin(a1);
        return `${poly ? box(poly) : `M${c[0]} ${c[1]}L${leaf[0]} ${leaf[1]}`}M${sx} ${sy}A${r} ${r} 0 ${a1 - a0 > Math.PI ? 1 : 0} 1 ${ex} ${ey}`;
    };
    return one(sym.hinge, sym.leaf_end, sym.radius, sym.start_angle, sym.end_angle, sym.poly) + (sym.more ?? []).map((l) => one(l.hinge, l.leaf_end, l.radius, l.start_angle, l.end_angle, l.poly)).join(""); // a door of several panels: one per swinging panel
}
/** A door's leaves as SVG paths: each shape with its own dash, so a sheet draws the closed leaves solid, the open sliding leaf and the pocket dashed, the
 *  track dash-dot and the travel arrow with its head. */
export function shapePaths(sym) {
    const out = [];
    for (const sh of sym?.shapes ?? []) {
        const pts = sh.p.map((c) => `${c[0]} ${c[1]}`);
        let d = sh.t === "poly" ? `M${pts.join("L")}Z` : `M${pts.join("L")}`;
        if (sh.t === "arrow" && sh.p.length === 2) {
            const [a, b] = sh.p;
            const ang = Math.atan2(b[1] - a[1], b[0] - a[0]), len = Math.hypot(b[0] - a[0], b[1] - a[1]) * 0.18;
            for (const k of [-0.45, 0.45])
                d += `M${b[0]} ${b[1]}L${b[0] - len * Math.cos(ang + k)} ${b[1] - len * Math.sin(ang + k)}`;
        }
        out.push({ d, dash: sh.dash ? (sh.role === "track" ? "10 3 2 3" : "5 4") : "", role: sh.role });
    }
    return out;
}
/** How a cut is filled given its hatch: a plain tint when there is no hatch (or the view hides it), solid ink for a solid pattern, and nothing
 *  under the lines for the others. Returns the fill opacity multiplier (1 = the element's own cut weight). */
export function hatchFill(hatch) {
    if (!hatch)
        return 1;
    return hatch.kind === "solid" ? 1 : 0;
}
/** The lines (or dots) of a hatch laid over a cut made of several loops, filled even-odd so openings stay clear, as an SVG/canvas path in
 *  the loops' own units. ``step`` is the spacing in those units; ``flip`` mirrors the angle for a screen whose y points down. Empty for none/solid. */
export function patternPath(loops, hatch, step, flip = false) {
    if ((hatch.kind !== "lines" && hatch.kind !== "cross" && hatch.kind !== "dots") || !(step > 0))
        return "";
    const rings = loops.filter((l) => l.length >= 3);
    if (!rings.length)
        return "";
    const out = [];
    const angle = ((flip ? -hatch.angle : hatch.angle) * Math.PI) / 180;
    const scan = (theta) => {
        const c = Math.cos(theta), s = Math.sin(theta);
        const to = (p) => [p[0] * c + p[1] * s, -p[0] * s + p[1] * c];
        const back = (x, y) => [x * c - y * s, x * s + y * c];
        const turned = rings.map((l) => l.map(to));
        const ys = turned.flat().map((p) => p[1]);
        const lo = Math.min(...ys), hi = Math.max(...ys);
        for (let y = Math.ceil(lo / step) * step; y < hi; y += step) {
            const hits = [];
            for (const l of turned)
                for (let i = 0; i < l.length; i++) {
                    const a = l[i], b = l[(i + 1) % l.length];
                    if ((a[1] < y) !== (b[1] < y))
                        hits.push(a[0] + ((y - a[1]) / (b[1] - a[1])) * (b[0] - a[0]));
                }
            hits.sort((p, q) => p - q);
            for (let i = 0; i + 1 < hits.length; i += 2) {
                const p = back(hits[i], y), q = back(hits[i + 1], y);
                out.push(`M${r(p[0])} ${r(p[1])}L${r(q[0])} ${r(q[1])}`);
            }
        }
    };
    if (hatch.kind === "dots") {
        const inside = (x, y) => { let c = false; for (const l of rings)
            for (let i = 0; i < l.length; i++) {
                const a = l[i], b = l[(i + 1) % l.length];
                if ((a[1] > y) !== (b[1] > y) && x < ((b[0] - a[0]) * (y - a[1])) / (b[1] - a[1]) + a[0])
                    c = !c;
            } return c; };
        const xs = rings.flat().map((p) => p[0]), ys = rings.flat().map((p) => p[1]);
        let row = 0;
        for (let y = Math.min(...ys) + step / 2; y < Math.max(...ys); y += step, row++)
            for (let x = Math.min(...xs) + step / 2 + (row % 2 ? step / 2 : 0); x < Math.max(...xs); x += step)
                if (inside(x, y))
                    out.push(`M${r(x)} ${r(y)}l${r(step * 0.02)} 0`);
        return out.join("");
    }
    scan(angle);
    if (hatch.kind === "cross")
        scan(angle + Math.PI / 2);
    return out.join("");
}
