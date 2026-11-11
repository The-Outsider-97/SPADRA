/** Pure 2D geometry for the drawing editor: hit testing, snapping, grid steps. No DOM, so it is unit-tested directly. */
import type { Entity, Pt } from "./types.js";

export const dist = (a: Pt, b: Pt): number => Math.hypot(a[0] - b[0], a[1] - b[1]);
export const mid = (a: Pt, b: Pt): Pt => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];

const isPt = (v: unknown): v is Pt => Array.isArray(v) && v.length === 2 && typeof v[0] === "number" && typeof v[1] === "number";
const isPts = (v: unknown): v is Pt[] => Array.isArray(v) && v.every(isPt);

export function distToSegment(p: Pt, a: Pt, b: Pt): number {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const len2 = dx * dx + dy * dy;
  if (len2 === 0) return dist(p, a);
  const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len2));
  return Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy));
}

/** The straight pieces an entity is drawn from (lines, polyline edges, dimension lines). Circles and text have none. */
export function segments(e: Entity): Array<[Pt, Pt]> {
  const g = e.geometry;
  if (e.kind === "line" && isPt(g["start"]) && isPt(g["end"])) return [[g["start"], g["end"]]];
  if (e.kind === "polyline" && isPts(g["points"])) {
    const pts = g["points"];
    const out: Array<[Pt, Pt]> = [];
    for (let i = 0; i + 1 < pts.length; i++) { const a = pts[i], b = pts[i + 1]; if (a && b) out.push([a, b]); }
    const first = pts[0], last = pts[pts.length - 1];
    if (g["closed"] === true && first && last && pts.length > 2) out.push([last, first]);
    return out;
  }
  if (e.kind === "dimension" && e.primitives) {
    const out: Array<[Pt, Pt]> = [];
    for (const p of e.primitives) if (p.kind === "line" && isPt(p.start) && isPt(p.end)) out.push([p.start, p.end]);
    return out;
  }
  return [];
}

/** Points along an arc (counter-clockwise from its start angle to its end angle), including both ends. */
export function arcPoints(g: Record<string, unknown>, steps = 48): Pt[] {
  const c = g["center"], r = g["radius"], a0 = g["start_angle"], a1 = g["end_angle"];
  if (!isPt(c) || typeof r !== "number" || typeof a0 !== "number" || typeof a1 !== "number") return [];
  let end = a1;
  while (end < a0) end += Math.PI * 2;
  const n = Math.max(2, Math.ceil((steps * (end - a0)) / (Math.PI * 2)));
  const out: Pt[] = [];
  for (let i = 0; i <= n; i++) { const a = a0 + ((end - a0) * i) / n; out.push([c[0] + r * Math.cos(a), c[1] + r * Math.sin(a)]); }
  return out;
}

/** The closed outlines of a hatch as polygons (circles sampled), for hit testing and bounds. Path outlines are not read. */
export function hatchLoops(e: Entity): Pt[][] {
  const boundary = e.geometry["boundary"];
  if (e.kind !== "hatch" || !Array.isArray(boundary)) return [];
  const loops: Pt[][] = [];
  for (const raw of boundary as Array<Record<string, unknown>>) {
    if (raw["kind"] === "polygon" && isPts(raw["points"])) loops.push(raw["points"]);
    else if (raw["kind"] === "circle" && isPt(raw["center"]) && typeof raw["radius"] === "number") {
      const [cx, cy] = raw["center"], r = raw["radius"];
      loops.push(Array.from({ length: 64 }, (_, i) => [cx + r * Math.cos((i / 64) * Math.PI * 2), cy + r * Math.sin((i / 64) * Math.PI * 2)] as Pt));
    }
  }
  return loops;
}

/** The corners of a polyline that is closed: flagged closed, or drawn back to its own first point (then the repeated last point is not a corner). Null when it is open. */
export function closedPolylinePoints(e: Entity): Pt[] | null {
  const g = e.geometry, points = g["points"];
  if (e.kind !== "polyline" || !isPts(points)) return null;
  const same = points.length >= 4 && Math.hypot((points[0] as Pt)[0] - (points[points.length - 1] as Pt)[0], (points[0] as Pt)[1] - (points[points.length - 1] as Pt)[1]) < 1e-9;
  if (g["closed"] === true && points.length >= 3) return points.map((q) => [q[0], q[1]] as Pt);
  return same ? points.slice(0, -1).map((q) => [q[0], q[1]] as Pt) : null;
}

/** The outline of a closed drawing entity as one polygon (a closed polyline, a circle sampled, a hatch with a single outline), or null: what Extrude and Sweep can take as a shape. */
export function closedLoop(e: Entity): Pt[] | null {
  const g = e.geometry;
  const polygon = closedPolylinePoints(e);
  if (polygon) return polygon;
  if (e.kind === "circle" && isPt(g["center"]) && typeof g["radius"] === "number" && g["radius"] > 0) {
    const [cx, cy] = g["center"], r = g["radius"];
    return Array.from({ length: 48 }, (_, i) => [cx + r * Math.cos((i / 48) * Math.PI * 2), cy + r * Math.sin((i / 48) * Math.PI * 2)] as Pt);
  }
  const loops = hatchLoops(e);
  return loops.length === 1 ? (loops[0] as Pt[]) : null;
}

/** True when the point is inside a hatch's outline by the even-odd rule (holes stay open). */
export function hatchContains(e: Entity, p: Pt): boolean {
  let inside = false;
  for (const loop of hatchLoops(e)) if (pointInPolygon(p, loop)) inside = !inside;
  return inside;
}

export function textBox(e: Entity): { origin: Pt; width: number; height: number } | null {
  const g = e.geometry;
  if (e.kind !== "text" || !isPt(g["position"])) return null;
  const height = typeof g["height"] === "number" ? g["height"] : 250;
  const text = typeof g["text"] === "string" ? g["text"] : "";
  return { origin: g["position"], width: Math.max(1, text.length) * height * 0.6, height };
}

/** Distance from a point to the drawn entity (0 inside a text box). */
export function entityDistance(e: Entity, p: Pt): number {
  const g = e.geometry;
  if (e.kind === "circle" && isPt(g["center"]) && typeof g["radius"] === "number") return Math.abs(dist(p, g["center"]) - g["radius"]);
  if (e.kind === "arc") {
    const pts = arcPoints(g);
    let best = Infinity;
    for (let i = 0; i + 1 < pts.length; i++) best = Math.min(best, distToSegment(p, pts[i] as Pt, pts[i + 1] as Pt));
    return best;
  }
  if (e.kind === "hatch") return hatchContains(e, p) ? 0 : Infinity;
  const box = textBox(e);
  if (box) {
    const dx = Math.max(box.origin[0] - p[0], 0, p[0] - (box.origin[0] + box.width));
    const dy = Math.max(box.origin[1] - p[1], 0, p[1] - (box.origin[1] + box.height));
    return Math.hypot(dx, dy);
  }
  let best = Infinity;
  for (const [a, b] of segments(e)) best = Math.min(best, distToSegment(p, a, b));
  return best;
}

/** The entity's bounding box, or null when it has no extent we can read. */
export function entityBounds(e: Entity): [Pt, Pt] | null {
  const g = e.geometry;
  if (e.kind === "circle" && isPt(g["center"]) && typeof g["radius"] === "number") {
    const [cx, cy] = g["center"], r = g["radius"];
    return [[cx - r, cy - r], [cx + r, cy + r]];
  }
  const sampled = e.kind === "arc" ? arcPoints(g) : e.kind === "hatch" ? hatchLoops(e).flat() : null;
  if (sampled) {
    if (!sampled.length) return null;
    const xs = sampled.map((q) => q[0]), ys = sampled.map((q) => q[1]);
    return [[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)]];
  }
  const box = textBox(e);
  if (box) return [box.origin, [box.origin[0] + box.width, box.origin[1] + box.height]];
  const segs = segments(e);
  if (!segs.length) return null;
  const xs = segs.flatMap(([a, b]) => [a[0], b[0]]), ys = segs.flatMap(([a, b]) => [a[1], b[1]]);
  return [[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)]];
}

/** A window selection takes entities fully inside the box. */
export function insideBox(e: Entity, lo: Pt, hi: Pt): boolean {
  const b = entityBounds(e);
  return !!b && b[0][0] >= lo[0] && b[0][1] >= lo[1] && b[1][0] <= hi[0] && b[1][1] <= hi[1];
}

/** 1, 2 or 5 times a power of ten, at least `minimum`: the grid spacing that keeps lines a readable distance apart. */
export function niceStep(minimum: number): number {
  if (!(minimum > 0) || !Number.isFinite(minimum)) return 1;
  const exp = Math.floor(Math.log10(minimum));
  const base = 10 ** exp;
  for (const m of [1, 2, 5, 10]) if (base * m >= minimum - 1e-12 * minimum) return base * m;
  return base * 10;
}

/** Strip the last binary-float digits: 3 * 0.1 is 0.30000000000000004, and a grid coordinate should be 0.3. Twelve significant digits is far below any drawing's precision. */
export function cleanNumber(v: number): number {
  if (!Number.isFinite(v) || v === 0) return 0;
  return Number(v.toPrecision(12));
}

/** What the grid shows and what it snaps to at a zoom. All values are in drawing units; the origin of the grid is the world origin (0, 0), always.
 *  `display` is the interval between drawn lines and `snap` the increment the pointer snaps to. With automatic spacing they are the same. With a fixed `user` spacing the
 *  lines are drawn every `user` times 1, 2, 5, 10... (every drawn line is a multiple of the spacing) so they stay a readable distance apart, while the pointer keeps
 *  snapping to `user` itself, until `user` is so small on screen (under `minSnapPx`) that it cannot be aimed at, when `collapsed` is true and snapping follows the display. */
export interface GridSpec { display: number; snap: number; major: number; auto: boolean; collapsed: boolean }

export function gridSpec(scale: number, user: number | null = null, minPx = 24, minSnapPx = 4): GridSpec {
  const s = scale > 0 && Number.isFinite(scale) ? scale : 1;
  if (!user || !(user > 0) || !Number.isFinite(user)) {
    const step = niceStep(minPx / s);
    return { display: step, snap: step, major: cleanNumber(step * 5), auto: true, collapsed: false };
  }
  const need = minPx / (s * user);
  const display = cleanNumber(user * (need > 1 ? niceStep(need) : 1));
  const collapsed = user * s < minSnapPx;
  return { display, snap: collapsed ? display : user, major: cleanNumber(display * 5), auto: false, collapsed };
}

export interface GridLine { value: number; kind: "axis" | "major" | "minor" }

/** The grid lines of one direction from `from` to `to`: index times step, never a running sum, so no error accumulates however far the view is panned. The line at zero is
 *  the axis, every fifth is major. A range that would need more than `maxLines` lines gives none (the grid is finer than the screen can show). */
export function gridLines(from: number, to: number, step: number, maxLines = 2000): GridLine[] {
  if (!(step > 0) || !Number.isFinite(from) || !Number.isFinite(to) || to < from) return [];
  const first = Math.floor(from / step), last = Math.ceil(to / step);
  if (last - first + 1 > maxLines) return [];
  const out: GridLine[] = [];
  for (let i = first; i <= last; i++) out.push({ value: cleanNumber(i * step), kind: i === 0 ? "axis" : i % 5 === 0 ? "major" : "minor" });
  return out;
}

/** The nearest grid intersection, computed as an integer multiple of the step so that 3 steps is exactly 0.3 and not 0.30000000000000004. */
export function snapToGrid(p: Pt, step: number): Pt {
  if (!(step > 0) || !Number.isFinite(step)) return p;
  return [cleanNumber(Math.round(p[0] / step) * step), cleanNumber(Math.round(p[1] / step) * step)];
}

/** Millimetres in one drawing unit. */
export const UNIT_MM: Readonly<Record<string, number>> = { mm: 1, cm: 10, m: 1000 };

/** "10.0 mm": a grid size as the status bar shows it, with at least one decimal and as many more (up to four) as the number needs. */
export function formatGridSize(step: number, unit: string): string {
  const v = cleanNumber(step);
  const decimals = Math.min(4, Math.max(1, (String(v).split(".")[1] ?? "").length));
  return `${v.toFixed(decimals)} ${unit}`;
}

/** Keep the dominant axis of a drag (the Shift constraint while drawing). */
export function orthoConstrain(from: Pt, to: Pt): Pt {
  return Math.abs(to[0] - from[0]) >= Math.abs(to[1] - from[1]) ? [to[0], from[1]] : [from[0], to[1]];
}

export function normaliseRect(a: Pt, b: Pt): { corner: Pt; width: number; height: number } {
  return { corner: [Math.min(a[0], b[0]), Math.min(a[1], b[1])], width: Math.abs(b[0] - a[0]), height: Math.abs(b[1] - a[1]) };
}

export function segmentIntersection(a: [Pt, Pt], b: [Pt, Pt]): Pt | null {
  const [p, p2] = a, [q, q2] = b;
  const rx = p2[0] - p[0], ry = p2[1] - p[1], sx = q2[0] - q[0], sy = q2[1] - q[1];
  const den = rx * sy - ry * sx;
  if (Math.abs(den) < 1e-12) return null;
  const t = ((q[0] - p[0]) * sy - (q[1] - p[1]) * sx) / den;
  const u = ((q[0] - p[0]) * ry - (q[1] - p[1]) * rx) / den;
  if (t < 0 || t > 1 || u < 0 || u > 1) return null;
  return [p[0] + t * rx, p[1] + t * ry];
}

export type SnapKind = "end" | "mid" | "center" | "intersection" | "grid" | "none";
export interface Snap { point: Pt; kind: SnapKind }

/** The best snap for a cursor position. Geometry snaps beat the grid; among geometry snaps the nearest wins, with endpoints first on ties. */
export function snapPoint(p: Pt, entities: readonly Entity[], tolerance: number, gridStep: number | null, polygons: readonly (readonly Pt[])[] = []): Snap {
  const rank: Record<string, number> = { end: 0, intersection: 1, center: 2, mid: 3 };
  let best: Snap | null = null;
  let bestScore = Infinity;
  const consider = (point: Pt, kind: SnapKind): void => {
    const d = dist(p, point);
    if (d > tolerance) return;
    const score = d + (rank[kind] ?? 4) * 1e-9 * tolerance;
    if (score < bestScore) { best = { point, kind }; bestScore = score; }
  };
  const near: Array<[Pt, Pt]> = [];
  for (const e of entities) {
    const g = e.geometry;
    if (e.kind === "circle" && isPt(g["center"])) consider(g["center"], "center");
    if (e.kind === "text" && isPt(g["position"])) consider(g["position"], "end");
    if (e.kind === "arc") {
      if (isPt(g["center"])) consider(g["center"], "center");
      const pts = arcPoints(g);
      const first = pts[0], last = pts[pts.length - 1];
      if (first) consider(first, "end");
      if (last) consider(last, "end");
      continue;
    }
    if (e.kind === "dimension") {
      for (const key of ["p1", "p2"]) { const v = g[key]; if (isPt(v)) consider(v, "end"); }
      continue;
    }
    for (const [a, b] of segments(e)) {
      consider(a, "end"); consider(b, "end"); consider(mid(a, b), "mid");
      if (distToSegment(p, a, b) <= tolerance) near.push([a, b]);
    }
  }
  for (const poly of polygons) {                       // model walls: their corners and the middle of each edge
    for (let i = 0; i < poly.length; i++) {
      const a = poly[i], b = poly[(i + 1) % poly.length];
      if (!a || !b) continue;
      consider(a, "end"); consider(mid(a, b), "mid");
      if (poly.length === 2 && i === 0 && distToSegment(p, a, b) <= tolerance) near.push([a, b]);       // a grid line or a wall's centre line can be crossed by another
    }
  }
  for (let i = 0; i < near.length; i++) for (let j = i + 1; j < near.length; j++) {
    const a = near[i], b = near[j];
    const hit = a && b ? segmentIntersection(a, b) : null;
    if (hit) consider(hit, "intersection");
  }
  if (best) return best;
  if (gridStep && gridStep > 0) return { point: snapToGrid(p, gridStep), kind: "grid" };
  return { point: p, kind: "none" };
}

/** Fit a world box into a viewport: scale (pixels per unit) and the world point that sits at the viewport centre. */
export function fitBox(lo: Pt, hi: Pt, width: number, height: number, margin = 48): { scale: number; centre: Pt } {
  const w = Math.max(hi[0] - lo[0], 1e-9), h = Math.max(hi[1] - lo[1], 1e-9);
  const scale = Math.max(1e-9, Math.min((width - 2 * margin) / w, (height - 2 * margin) / h));
  return { scale, centre: [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2] };
}

/** "12000.00" -> "12,000", "1234.50" -> "1,234.5". Text that is not a plain number is shown as it is. */
export function formatDimension(text: string): string {
  if (!/^-?\d+(\.\d+)?$/.test(text)) return text;
  const decimals = (text.split(".")[1] ?? "").replace(/0+$/, "").length;
  return Number(text).toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

export function pointInPolygon(p: Pt, poly: readonly Pt[]): boolean {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const a = poly[i], b = poly[j];
    if (!a || !b) continue;
    if (a[1] > p[1] !== b[1] > p[1] && p[0] < ((b[0] - a[0]) * (p[1] - a[1])) / (b[1] - a[1]) + a[0]) inside = !inside;
  }
  return inside;
}

/** 0 inside the polygon, otherwise the distance to its nearest edge. */
export function polygonDistance(p: Pt, poly: readonly Pt[]): number {
  if (pointInPolygon(p, poly)) return 0;
  let best = Infinity;
  for (let i = 0; i < poly.length; i++) { const a = poly[i], b = poly[(i + 1) % poly.length]; if (a && b) best = Math.min(best, distToSegment(p, a, b)); }
  return best;
}

/** A handle on a selected entity. Dragging it to `p` gives the geometry to send to the server (only the fields that change). */
export interface Grip { at: Pt; geometry(p: Pt): Record<string, unknown> }

export function grips(e: Entity): Grip[] {
  const g = e.geometry;
  if (e.kind === "line" && isPt(g["start"]) && isPt(g["end"])) {
    const s = g["start"], t = g["end"];
    return [{ at: s, geometry: (p) => ({ start: p, end: t }) }, { at: t, geometry: (p) => ({ start: s, end: p }) }];
  }
  if (e.kind === "polyline" && isPts(g["points"])) {
    const pts = g["points"];
    return pts.map((at, i) => ({ at, geometry: (p: Pt) => ({ points: pts.map((q, j) => (j === i ? p : q)) }) }));
  }
  if (e.kind === "circle" && isPt(g["center"]) && typeof g["radius"] === "number") {
    const c = g["center"], r = g["radius"];
    return [{ at: c, geometry: (p) => ({ center: p, radius: r }) },
      { at: [c[0] + r, c[1]], geometry: (p) => ({ center: c, radius: Math.max(dist(c, p), 1e-9) }) }];
  }
  if (e.kind === "text" && isPt(g["position"])) return [{ at: g["position"], geometry: (p) => ({ position: p }) }];
  return [];
}

/** The nearest grip within `tolerance` of a point, or -1. */
export function gripAt(list: readonly Grip[], p: Pt, tolerance: number): number {
  let best = -1, bestD = tolerance;
  list.forEach((gr, i) => { const d = dist(gr.at, p); if (d <= bestD) { best = i; bestD = d; } });
  return best;
}

// ------------------------------------------------------------------------------------------------------------ turning and stretching
/** A point turned about a centre by an angle (radians, counter-clockwise, y up). */
export function rotateAbout(p: Pt, centre: Pt, angle: number): Pt {
  const c = Math.cos(angle), s = Math.sin(angle), dx = p[0] - centre[0], dy = p[1] - centre[1];
  return [centre[0] + dx * c - dy * s, centre[1] + dx * s + dy * c];
}

/** The signed angle (radians, in (-π, π]) that turns the direction centre→from onto the direction centre→to. */
export function angleBetween(centre: Pt, from: Pt, to: Pt): number {
  const a = Math.atan2(to[1] - centre[1], to[0] - centre[0]) - Math.atan2(from[1] - centre[1], from[0] - centre[0]);
  return Math.atan2(Math.sin(a), Math.cos(a));
}

/** An angle (radians) rounded to the nearest multiple of ``stepDegrees``. */
export function roundAngle(angle: number, stepDegrees: number): number {
  const step = (stepDegrees * Math.PI) / 180;
  return Math.round(angle / step) * step;
}

/** The box that holds all the polygons, or null when there are none. */
export function polygonsBounds(polygons: readonly (readonly Pt[])[]): [Pt, Pt] | null {
  let lo: Pt | null = null, hi: Pt | null = null;
  for (const poly of polygons) for (const p of poly) {
    lo = lo ? [Math.min(lo[0], p[0]), Math.min(lo[1], p[1])] : [p[0], p[1]];
    hi = hi ? [Math.max(hi[0], p[0]), Math.max(hi[1], p[1])] : [p[0], p[1]];
  }
  return lo && hi ? [lo, hi] : null;
}

/** The footprint of a straight, square-ended wall of the given thickness along a→b (used to show a wall that is being drawn or stretched). */
export function wallBand(a: Pt, b: Pt, thickness: number, shift = 0): Pt[] {
  const len = dist(a, b) || 1, ux = -(b[1] - a[1]) / len, uy = (b[0] - a[0]) / len, nx = ux * (thickness / 2), ny = uy * (thickness / 2), sx = ux * shift, sy = uy * shift;
  return [[a[0] + sx + nx, a[1] + sy + ny], [b[0] + sx + nx, b[1] + sy + ny], [b[0] + sx - nx, b[1] + sy - ny], [a[0] + sx - nx, a[1] + sy - ny]];
}

/** How far (same unit as ``thickness``) a wall's centre line lies from the line that was drawn: the offset, plus half the thickness when the line is a face.
 *  The near face is the right-hand one looking along the path, so its centre is to the left (+). */
export function wallCentreShift(reference: "near" | "centre" | "far", offset: number, thickness: number): number {
  return offset + (reference === "near" ? thickness / 2 : reference === "far" ? -thickness / 2 : 0);
}

/** The point on the line a→b that is ``length`` from a (for a wall whose length is typed in). */
export function alongFrom(a: Pt, b: Pt, length: number): Pt {
  const len = dist(a, b) || 1;
  return [a[0] + ((b[0] - a[0]) / len) * length, a[1] + ((b[1] - a[1]) / len) * length];
}
