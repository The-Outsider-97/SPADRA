"""Planar geometry for SPADRA's 2D editing commands: offset, trim, extend, fillet, mirror and hatch fill.

SDRW stores entities but does not edit them this way, so the maths lives here as small pure functions on plain points. Nothing here
touches a document; ``project.py`` reads an entity's geometry, calls one of these, and writes the result back as one command.

A *chain* is a list of points plus a ``closed`` flag. A line is a chain of two points, a rectangle a closed chain of four. Positions
along a chain are a single float ``s``: the integer part is the segment index and the fraction is the distance along that segment.
Every function raises ``GeometryError`` with a sentence a person can act on.
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

Point = tuple[float, float]
EPS = 1e-9


class GeometryError(ValueError):
    """The requested edit has no sensible result (parallel lines, an offset that collapses, nothing to trim to ...)."""


# ------------------------------------------------------------------------------------------------------------------- vectors
def sub(a: Point, b: Point) -> Point:
    return (a[0] - b[0], a[1] - b[1])


def add(a: Point, b: Point) -> Point:
    return (a[0] + b[0], a[1] + b[1])


def mul(a: Point, k: float) -> Point:
    return (a[0] * k, a[1] * k)


def dot(a: Point, b: Point) -> float:
    return a[0] * b[0] + a[1] * b[1]


def cross(a: Point, b: Point) -> float:
    return a[0] * b[1] - a[1] * b[0]


def norm(a: Point) -> float:
    return math.hypot(a[0], a[1])


def unit(a: Point) -> Point:
    n = norm(a)
    if n < EPS:
        raise GeometryError("A zero-length segment has no direction")
    return (a[0] / n, a[1] / n)


def left(u: Point) -> Point:
    return (-u[1], u[0])


# ------------------------------------------------------------------------------------------------------------------- chains
def segment_count(points: Sequence[Point], closed: bool) -> int:
    return len(points) if closed and len(points) > 2 else len(points) - 1


def segments(points: Sequence[Point], closed: bool) -> list[tuple[Point, Point]]:
    n = segment_count(points, closed)
    return [(points[i], points[(i + 1) % len(points)]) for i in range(n)]


def chain_length(points: Sequence[Point], closed: bool) -> float:
    return sum(math.dist(a, b) for a, b in segments(points, closed))


def nearest_on_segment(p: Point, a: Point, b: Point) -> tuple[float, float]:
    """(t, distance) of the point on segment ab closest to p; t in [0, 1]."""
    d = sub(b, a)
    l2 = dot(d, d)
    t = 0.0 if l2 < EPS else max(0.0, min(1.0, dot(sub(p, a), d) / l2))
    return t, math.dist(p, add(a, mul(d, t)))


def nearest_param(points: Sequence[Point], closed: bool, p: Point) -> float:
    """Position ``s`` on the chain closest to ``p``."""
    best, best_s = math.inf, 0.0
    for i, (a, b) in enumerate(segments(points, closed)):
        t, dist = nearest_on_segment(p, a, b)
        if dist < best - 1e-12:
            best, best_s = dist, i + t
    return best_s


def point_at(points: Sequence[Point], closed: bool, s: float) -> Point:
    n = segment_count(points, closed)
    i = int(math.floor(s + 1e-12))
    t = s - i
    if not closed and i >= n:
        return tuple(points[-1])                                    # type: ignore[return-value]
    a, b = points[i % len(points)], points[(i + 1) % len(points)]
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def extract(points: Sequence[Point], closed: bool, a: float, b: float) -> list[Point]:
    """The part of a chain from position a forward to b. For a closed chain b may be smaller than a: the walk wraps round."""
    n = segment_count(points, closed)
    if closed and b < a:
        b += n
    out: list[Point] = [point_at(points, closed, a)]
    k = int(math.floor(a + 1e-9)) + 1
    while k < b - 1e-9:
        out.append(tuple(points[k % len(points)]))                  # type: ignore[arg-type]
        k += 1
    out.append(point_at(points, closed, b))
    cleaned = [out[0]]
    for q in out[1:]:
        if math.dist(q, cleaned[-1]) > 1e-7:
            cleaned.append(q)
    return cleaned


# ---------------------------------------------------------------------------------------------------------------- intersections
def intersect_lines(p: Point, r: Point, q: Point, s: Point) -> tuple[float, float] | None:
    """Parameters (t, u) with p + t r = q + u s for direction vectors r and s, or None when parallel."""
    denom = cross(r, s)
    if abs(denom) < EPS * max(1.0, norm(r) * norm(s)):
        return None
    qp = sub(q, p)
    return cross(qp, s) / denom, cross(qp, r) / denom


def segment_cutter_hits(a: Point, b: Point, cutter: tuple) -> list[tuple[float, Point]]:
    """Where the segment ab meets a cutter: ``("seg", c, d)`` or ``("circle", centre, radius)``. Returns (t along ab, point)."""
    r = sub(b, a)
    hits: list[tuple[float, Point]] = []
    if cutter[0] == "seg":
        _, c, d = cutter
        tu = intersect_lines(a, r, c, sub(d, c))
        if tu and -1e-9 <= tu[0] <= 1 + 1e-9 and -1e-9 <= tu[1] <= 1 + 1e-9:
            t = max(0.0, min(1.0, tu[0]))
            hits.append((t, add(a, mul(r, t))))
    else:
        _, centre, radius = cutter
        f = sub(a, centre)
        qa, qb, qc = dot(r, r), 2 * dot(f, r), dot(f, f) - radius * radius
        if qa < EPS:
            return hits
        disc = qb * qb - 4 * qa * qc
        if disc >= 0:
            root = math.sqrt(disc)
            for t in sorted({(-qb - root) / (2 * qa), (-qb + root) / (2 * qa)}):
                if -1e-9 <= t <= 1 + 1e-9:
                    t = max(0.0, min(1.0, t))
                    hits.append((t, add(a, mul(r, t))))
    return hits


def ray_cutter_hit(origin: Point, direction: Point, cutter: tuple) -> list[tuple[float, Point]]:
    """Where a ray from ``origin`` meets a cutter (the cutter itself stays bounded). Returns (distance, point), distance > 0."""
    hits: list[tuple[float, Point]] = []
    d = unit(direction)
    if cutter[0] == "seg":
        _, c, e = cutter
        tu = intersect_lines(origin, d, c, sub(e, c))
        if tu and tu[0] > 1e-7 and -1e-9 <= tu[1] <= 1 + 1e-9:
            hits.append((tu[0], add(origin, mul(d, tu[0]))))
    else:
        _, centre, radius = cutter
        f = sub(origin, centre)
        qb, qc = 2 * dot(f, d), dot(f, f) - radius * radius
        disc = qb * qb - 4 * qc
        if disc >= 0:
            root = math.sqrt(disc)
            for t in ((-qb - root) / 2, (-qb + root) / 2):
                if t > 1e-7:
                    hits.append((t, add(origin, mul(d, t))))
    return hits


def cutters_for(kind: str, geometry: dict) -> list[tuple]:
    """The cutting/boundary pieces an entity offers to trim and extend. Entities that are not curves offer none."""
    if kind == "line":
        return [("seg", tuple(geometry["start"]), tuple(geometry["end"]))]
    if kind == "polyline":
        pts = [tuple(p) for p in geometry["points"]]
        return [("seg", a, b) for a, b in segments(pts, bool(geometry.get("closed")))]
    if kind == "circle":
        return [("circle", tuple(geometry["center"]), float(geometry["radius"]))]
    return []


# ------------------------------------------------------------------------------------------------------------------------ offset
def offset_chain(points: Sequence[Point], closed: bool, distance: float, miter_limit: float = 4.0) -> list[Point]:
    """Parallel copy of a chain, ``distance`` to the left of its direction (negative = right). Corners are mitred; a corner sharper than
    ``miter_limit`` x distance is bevelled. If the offset is so large that a segment would fold back, that is an error, not a guess."""
    pts = [tuple(p) for p in points]
    if closed and len(pts) > 2 and math.dist(pts[0], pts[-1]) < 1e-9:
        pts.pop()
    segs = segments(pts, closed)
    if not segs:
        raise GeometryError("There is nothing to offset")
    dirs = [unit(sub(b, a)) for a, b in segs]
    shifted = [(add(a, mul(left(u), distance)), add(b, mul(left(u), distance))) for (a, b), u in zip(segs, dirs)]
    count = len(segs)

    def corner(i: int, j: int, vertex: Point) -> tuple[Point, Point]:
        """The offset end of segment i and offset start of segment j that meet at ``vertex``."""
        tu = intersect_lines(shifted[i][0], sub(shifted[i][1], shifted[i][0]), shifted[j][0], sub(shifted[j][1], shifted[j][0]))
        if tu is None:                                                          # collinear: the shifted ends already coincide
            return shifted[i][1], shifted[j][0]
        m = add(shifted[i][0], mul(sub(shifted[i][1], shifted[i][0]), tu[0]))
        if math.dist(m, vertex) > miter_limit * abs(distance):
            return shifted[i][1], shifted[j][0]                                  # bevel
        return m, m

    starts = [s[0] for s in shifted]
    ends = [s[1] for s in shifted]
    interior = range(1, count) if not closed else range(count)
    for j in interior:
        i = (j - 1) % count
        ends[i], starts[j] = corner(i, j, segs[j][0])
    for k in range(count):
        if dot(sub(ends[k], starts[k]), dirs[k]) <= 1e-9:
            raise GeometryError("That offset distance is larger than the shape can hold: a side would collapse")
    out: list[Point] = []
    for k in range(count):
        if not closed or k == 0 or math.dist(starts[k], out[-1]) > 1e-9:
            if not out or math.dist(starts[k], out[-1]) > 1e-9:
                out.append(starts[k])
        if math.dist(ends[k], out[-1]) > 1e-9 and not (closed and k == count - 1 and math.dist(ends[k], out[0]) < 1e-9):
            out.append(ends[k])
    return out


def side_of(points: Sequence[Point], closed: bool, pick: Point) -> float:
    """+1 if ``pick`` is to the left of the chain's nearest segment, -1 if to the right."""
    s = nearest_param(points, closed, pick)
    a, b = segments(points, closed)[min(int(s), segment_count(points, closed) - 1)]
    c = cross(sub(b, a), sub(pick, a))
    if abs(c) < 1e-12:
        raise GeometryError("Click on one side of the shape to say which way to offset")
    return 1.0 if c > 0 else -1.0


# -------------------------------------------------------------------------------------------------------------------------- trim
def trim_chain(points: Sequence[Point], closed: bool, pick: Point, cutters: Iterable[tuple]) -> list[list[Point]]:
    """Remove the part of a chain under ``pick`` that lies between the nearest cuts either side. Returns the pieces that remain
    (0, 1 or 2 chains). Raises if the chain is not crossed by any cutter."""
    pts = [tuple(p) for p in points]
    n = segment_count(pts, closed)
    cuts: list[float] = []
    for i, (a, b) in enumerate(segments(pts, closed)):
        for cutter in cutters:
            for t, _ in segment_cutter_hits(a, b, cutter):
                cuts.append(i + t)
    cuts = sorted(set(round(c, 9) for c in cuts))
    if closed:
        cuts = sorted({c % n if c < n - 1e-9 else 0.0 for c in cuts})
    if not cuts:
        raise GeometryError("Nothing crosses that line, so there is nothing to trim to")
    s = nearest_param(pts, closed, pick)
    if closed:
        if len(cuts) < 2:
            raise GeometryError("A closed shape needs to be crossed in two places before part of it can be trimmed")
        before = [c for c in cuts if c <= s + 1e-12]
        after = [c for c in cuts if c > s + 1e-12]
        prev = before[-1] if before else cuts[-1]
        nxt = after[0] if after else cuts[0]
        piece = extract(pts, True, nxt, prev)
        return [piece] if len(piece) >= 2 else []
    before = [c for c in cuts if c <= s + 1e-12]
    after = [c for c in cuts if c > s + 1e-12]
    pieces: list[list[Point]] = []
    if before:
        first = extract(pts, False, 0.0, before[-1])
        if len(first) >= 2 and chain_length(first, False) > 1e-7:
            pieces.append(first)
    if after:
        last = extract(pts, False, after[0], float(n))
        if len(last) >= 2 and chain_length(last, False) > 1e-7:
            pieces.append(last)
    return pieces


# ------------------------------------------------------------------------------------------------------------------------ extend
def extend_chain(points: Sequence[Point], pick: Point, boundaries: Iterable[tuple]) -> list[Point]:
    """Lengthen the end of an open chain that is nearer ``pick`` along its last segment until it reaches the nearest boundary."""
    pts = [tuple(p) for p in points]
    if len(pts) < 2:
        raise GeometryError("There is nothing to extend")
    at_end = math.dist(pick, pts[-1]) <= math.dist(pick, pts[0])
    if at_end:
        origin, direction = pts[-1], sub(pts[-1], pts[-2])
    else:
        origin, direction = pts[0], sub(pts[0], pts[1])
    best: tuple[float, Point] | None = None
    for cutter in boundaries:
        for hit in ray_cutter_hit(origin, direction, cutter):
            if best is None or hit[0] < best[0]:
                best = hit
    if best is None:
        raise GeometryError("That end does not reach any boundary when extended")
    out = list(pts)
    if at_end:
        out[-1] = best[1]
    else:
        out[0] = best[1]
    return out


# ------------------------------------------------------------------------------------------------------------------------ fillet
def fillet_lines(l1: tuple[Point, Point], l2: tuple[Point, Point], pick1: Point, pick2: Point, radius: float):
    """Round (or, with radius 0, square off) the corner between two lines. ``pick`` says which part of each line to keep.
    Returns (new l1, new l2, arc) with arc = (centre, radius, start_angle, end_angle) counter-clockwise, or None for radius 0."""
    d1, d2 = sub(l1[1], l1[0]), sub(l2[1], l2[0])
    tu = intersect_lines(l1[0], d1, l2[0], d2)
    if tu is None:
        raise GeometryError("These lines are parallel, so they have no corner to fillet")
    corner = add(l1[0], mul(d1, tu[0]))

    def keep(line: tuple[Point, Point], pick: Point) -> tuple[Point, Point]:
        """(unit direction away from the corner towards the kept side, far end of the kept part)."""
        axis = unit(sub(line[1], line[0]))
        side = 1.0 if dot(sub(pick, corner), axis) >= 0 else -1.0
        direction = mul(axis, side)
        far = max(line, key=lambda e: dot(sub(e, corner), direction))
        if dot(sub(far, corner), direction) < 1e-7:
            raise GeometryError("There is no length of that line on the side you clicked")
        return direction, far

    u1, far1 = keep(l1, pick1)
    u2, far2 = keep(l2, pick2)
    if radius <= 0:
        return (corner, far1), (corner, far2), None
    theta = math.acos(max(-1.0, min(1.0, dot(u1, u2))))
    if theta < 1e-6 or theta > math.pi - 1e-6:
        raise GeometryError("These lines are too close to straight to fillet")
    tangent = radius / math.tan(theta / 2)
    if tangent > math.dist(corner, far1) + 1e-9 or tangent > math.dist(corner, far2) + 1e-9:
        raise GeometryError("That radius is too large for these lines")
    t1, t2 = add(corner, mul(u1, tangent)), add(corner, mul(u2, tangent))
    bis = unit(add(u1, u2))
    centre = add(corner, mul(bis, radius / math.sin(theta / 2)))
    a1, a2 = math.atan2(t1[1] - centre[1], t1[0] - centre[0]), math.atan2(t2[1] - centre[1], t2[0] - centre[0])
    if cross(sub(t1, centre), sub(t2, centre)) < 0:
        a1, a2 = a2, a1                                                       # run counter-clockwise from the first to the second
    while a2 <= a1:
        a2 += 2 * math.pi
    return (t1, far1), (t2, far2), (centre, radius, a1, a2)


# ------------------------------------------------------------------------------------------------------------------------ mirror
def reflect(p: Point, a: Point, b: Point) -> Point:
    u = unit(sub(b, a))
    v = sub(p, a)
    along = mul(u, dot(v, u))
    return add(a, sub(mul(along, 2), v))


def axis_angle(a: Point, b: Point) -> float:
    return math.atan2(b[1] - a[1], b[0] - a[0])


# ------------------------------------------------------------------------------------------------------------------------- hatch
def polygon_of(kind: str, geometry: dict, arc_steps: int = 64) -> list[Point]:
    """A closed outline as a polygon: a closed polyline's points, or a circle sampled finely. Anything else cannot bound a hatch."""
    if kind == "polyline" and geometry.get("closed") and len(geometry["points"]) >= 3:
        return [tuple(p) for p in geometry["points"]]            # type: ignore[misc]
    if kind == "circle":
        cx, cy = geometry["center"]
        r = float(geometry["radius"])
        return [(cx + r * math.cos(2 * math.pi * k / arc_steps), cy + r * math.sin(2 * math.pi * k / arc_steps)) for k in range(arc_steps)]
    raise GeometryError("A hatch needs a closed shape: a closed polyline, a rectangle or a circle")


# ================================================================================================ solids for building elements
Mesh = tuple[list[list[float]], list[list[int]]]


def signed_area(poly: Sequence[Point]) -> float:
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(poly, list(poly[1:]) + [poly[0]])) / 2.0


def is_simple_polygon(poly: Sequence[Point]) -> bool:
    """True when no two non-adjacent edges of the polygon cross or touch (and no vertex repeats)."""
    n = len(poly)
    if n < 3 or len({(round(p[0], 9), round(p[1], 9)) for p in poly}) != n:
        return False
    edges = [(poly[i], poly[(i + 1) % n]) for i in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                continue
            a, b = edges[i]
            c, d = edges[j]
            tu = intersect_lines(a, sub(b, a), c, sub(d, c))
            if tu is None:
                # parallel: only a problem if they overlap along a shared line
                if abs(cross(sub(b, a), sub(c, a))) < 1e-9 and (nearest_on_segment(c, a, b)[1] < 1e-9 or nearest_on_segment(d, a, b)[1] < 1e-9):
                    return False
                continue
            if -1e-9 <= tu[0] <= 1 + 1e-9 and -1e-9 <= tu[1] <= 1 + 1e-9:
                return False
    return True


def triangulate(poly: Sequence[Point]) -> list[tuple[int, int, int]]:
    """Ear-clipping triangulation of a simple polygon given in any winding; triangles index the input and are counter-clockwise."""
    n = len(poly)
    if not is_simple_polygon(poly):
        raise GeometryError("The outline crosses itself or repeats a point")
    order = list(range(n)) if signed_area(poly) > 0 else list(range(n - 1, -1, -1))
    tris: list[tuple[int, int, int]] = []

    def inside(p: Point, a: Point, b: Point, c: Point) -> bool:
        d1, d2, d3 = cross(sub(b, a), sub(p, a)), cross(sub(c, b), sub(p, b)), cross(sub(a, c), sub(p, c))
        return d1 >= -1e-12 and d2 >= -1e-12 and d3 >= -1e-12

    guard = 0
    while len(order) > 3:
        guard += 1
        if guard > n * n:
            raise GeometryError("That outline could not be triangulated")
        for k in range(len(order)):
            i0, i1, i2 = order[k - 1], order[k], order[(k + 1) % len(order)]
            a, b, c = poly[i0], poly[i1], poly[i2]
            if cross(sub(b, a), sub(c, b)) <= 1e-12:
                continue                                              # reflex or flat corner: not an ear
            if any(inside(poly[j], a, b, c) for j in order if j not in (i0, i1, i2)):
                continue
            tris.append((i0, i1, i2))
            order.pop(k)
            break
        else:
            # Only collinear leftovers can remain: drop a flat vertex and carry on.
            flat = next((k for k in range(len(order)) if abs(cross(sub(poly[order[k]], poly[order[k - 1]]), sub(poly[order[(k + 1) % len(order)]], poly[order[k]]))) <= 1e-12), None)
            if flat is None:
                raise GeometryError("That outline could not be triangulated")
            order.pop(flat)
    if len(order) == 3 and cross(sub(poly[order[1]], poly[order[0]]), sub(poly[order[2]], poly[order[1]])) > 1e-12:
        tris.append((order[0], order[1], order[2]))
    return tris


def prism(poly: Sequence[Point], z0: float, z1: float) -> Mesh:
    """A closed, outward-facing prism: ``poly`` (a simple polygon in the XY plane) extruded from z0 up to z1."""
    if z1 <= z0:
        raise GeometryError("A solid needs a positive thickness")
    pts = [tuple(p) for p in poly]
    if signed_area(pts) < 0:
        pts.reverse()
    n = len(pts)
    verts = [[p[0], p[1], z0] for p in pts] + [[p[0], p[1], z1] for p in pts]
    faces: list[list[int]] = []
    for a, b, c in triangulate(pts):
        faces.append([n + a, n + b, n + c])                           # top, facing up
        faces.append([a, c, b])                                       # bottom, facing down
    for i in range(n):
        j = (i + 1) % n
        faces.append([i, j, n + j])
        faces.append([i, n + j, n + i])
    return verts, faces


WallEnds = tuple[tuple[float, float], tuple[float, float]]
SQUARE_ENDS: WallEnds = ((0.0, 0.0), (0.0, 0.0))
MITER_LIMIT = 4.0                         # a joint may move a wall face by at most this many wall thicknesses, else the end stays square
MIN_BUTT_ANGLE = math.radians(5.0)        # a wall that meets another within 5 degrees is running along it, not joining it


def _face_cut(d: Point, t: float, side: int, other: tuple[Point, float], other_side: int) -> float:
    """How far along ``d`` (from the joint, into the wall) one face line of a wall meets one face line of its neighbour at that joint.
    Positive: the face starts that far in (trimmed); negative: it reaches that far past the joint (extended). Parallel faces, or a
    meeting point beyond ``MITER_LIMIT`` thicknesses, leave the end square (0)."""
    od, ot = other
    hit = intersect_lines(mul(left(d), side * t / 2), d, mul(left(od), other_side * ot / 2), od)
    if hit is None or abs(hit[0]) > MITER_LIMIT * max(t, ot):
        return 0.0
    return hit[0]


def miter_node(members: Sequence[tuple[str, Point, float]]) -> dict[str, tuple[float, float]]:
    """Where the faces of the walls that end at one point meet. Each member is ``(key, d, thickness)`` with ``d`` the unit direction from
    the joint into that wall. The result gives, per member, how far its (left, right) face, looking along ``d``, extends past the joint
    (negative: is cut back from it). Two walls meet in an exact mitre at any angle; three or more meet face to face with the next wall
    round the joint, so a star of walls leaves no gap and no overlap."""
    if len(members) < 2:
        return {k: (0.0, 0.0) for k, _, _ in members}
    ring = sorted(members, key=lambda mb: math.atan2(mb[1][1], mb[1][0]))
    out: dict[str, tuple[float, float]] = {}
    for i, (key, d, t) in enumerate(ring):
        ccw, cw = ring[(i + 1) % len(ring)], ring[i - 1]
        out[key] = (-_face_cut(d, t, +1, (ccw[1], ccw[2]), -1),        # my left face meets the right face of the next wall anticlockwise
                    -_face_cut(d, t, -1, (cw[1], cw[2]), +1))          # my right face meets the left face of the previous one
    return out


def butt_ends(d: Point, t: float, point: Point, axis_dir: Point, axis_point: Point, axis_thickness: float) -> tuple[float, float] | None:
    """The faces of a wall that ends against the side of another (a T): the (left, right) extensions that bring its end square-on or
    slanted flush to the near face of the wall it meets. ``point`` is the wall's end, ``d`` points into it; None if the walls run too
    nearly parallel, or the face is further away than a mitre allows."""
    if abs(cross(axis_dir, d)) < math.sin(MIN_BUTT_ANGLE):
        return None
    side = 1 if cross(axis_dir, d) > 0 else -1
    face = add(axis_point, mul(left(axis_dir), side * axis_thickness / 2))
    out = []
    for face_side in (+1, -1):
        hit = intersect_lines(add(point, mul(left(d), face_side * t / 2)), d, face, axis_dir)
        if hit is None or abs(hit[0]) > MITER_LIMIT * max(t, axis_thickness):
            return None
        out.append(-hit[0])
    return out[0], out[1]


def _top_at(top: Sequence[tuple[float, float]], x: float) -> float:
    """The height of a wall's top edge at ``x``: the polyline through ``top`` (x, z), held level beyond its first and last point."""
    if x <= top[0][0]:
        return top[0][1]
    for (xa, za), (xb, zb) in zip(top, top[1:]):
        if x <= xb:
            return za if xb - xa < 1e-12 else za + (zb - za) * (x - xa) / (xb - xa)
    return top[-1][1]


def wall_mesh(length: float, thickness: float, height: float, openings: Sequence[tuple[float, float, float, float]],
              ends: WallEnds = SQUARE_ENDS, top: Sequence[tuple[float, float]] | None = None) -> Mesh:
    """A wall as a closed mesh, centred on the origin (x along the wall, y through it, z up), with rectangular openings cut through.
    Each opening is (x0, x1, z0, z1) in the same local frame. The result is the exact boundary of the box minus the openings.
    ``ends`` slants the two end faces for a joint: ((start +y face, start -y face), (end +y face, end -y face)), each the distance that
    face reaches past the end of the wall (negative: is cut back). A planar end face stays planar; an opening may not run into one.
    ``top`` is the wall's top edge as a polyline of (x, z) in the same local frame (x along the wall, z up, both from the centre): the top face
    then follows it, in planar pieces, instead of lying level at ``height``. No opening may reach above it."""
    hx, hy, hz = length / 2, thickness / 2, height / 2
    (sl, sr), (el, er) = ends
    start_inner, end_inner = -hx - min(sl, sr), hx + min(el, er)               # the most cut-back point of each end face
    if end_inner - start_inner < 1e-6:
        raise GeometryError("The joints at the two ends of this wall cut it away entirely")
    margin_start, margin_end = (1e-6 if start_inner > -hx else -1e-9), (1e-6 if end_inner < hx else -1e-9)       # an opening may touch a square end
    if any(o[0] < start_inner + margin_start or o[1] > end_inner - margin_end for o in openings):
        raise GeometryError("An opening runs into the angled corner at the end of the wall")
    kinks = [min(hx, max(-hx, q[0])) for q in top] if top else []
    xs = sorted({-hx, hx, *(min(hx, max(-hx, v)) for o in openings for v in (o[0], o[1])), *kinks})
    zs = sorted({-hz, hz, *(min(hz, max(-hz, v)) for o in openings for v in (o[2], o[3]))})
    if top:
        floor_ = min(_top_at(top, x) for x in xs)
        if floor_ < -hz + 1e-6:
            raise GeometryError("The top of the wall comes down to or below its base")
        for o in openings:
            lowest = min(_top_at(top, x) for x in [o[0], o[1], *(q[0] for q in top if o[0] < q[0] < o[1])])
            if o[3] > lowest - 1e-6:
                raise GeometryError("An opening reaches the sloping top of the wall: lower it or raise the roof it follows")
        if len(zs) > 2 and floor_ < zs[-2] + 1e-6:
            raise GeometryError("Where the sloping top of the wall is lowest it comes down to the height of an opening elsewhere in the wall")
    occupied = {(i, k) for i in range(len(xs) - 1) for k in range(len(zs) - 1)
                if not any(o[0] < (xs[i] + xs[i + 1]) / 2 < o[1] and o[2] < (zs[k] + zs[k + 1]) / 2 < o[3] for o in openings)}
    if not occupied:
        raise GeometryError("The openings leave nothing of the wall")
    verts: list[list[float]] = []
    index: dict[tuple[float, float, float], int] = {}

    def v(x: float, y: float, z: float) -> int:
        key = (x, y, z)
        if key not in index:
            index[key] = len(verts)
            verts.append([x, y, z])
        return index[key]

    faces: list[list[int]] = []

    def quad(a: int, b: int, c: int, d: int, flip: bool) -> None:
        q = [a, b, c, d][::-1] if flip else [a, b, c, d]
        faces.extend(([q[0], q[1], q[2]], [q[0], q[2], q[3]]))

    for i, k in sorted(occupied):
        x0, x1, z0, z1 = xs[i], xs[i + 1], zs[k], zs[k + 1]
        quad(v(x0, -hy, z0), v(x1, -hy, z0), v(x1, -hy, z1), v(x0, -hy, z1), False)           # face towards -y
        quad(v(x0, hy, z0), v(x1, hy, z0), v(x1, hy, z1), v(x0, hy, z1), True)                # face towards +y
        if (i - 1, k) not in occupied:
            quad(v(x0, -hy, z0), v(x0, hy, z0), v(x0, hy, z1), v(x0, -hy, z1), True)          # face towards -x
        if (i + 1, k) not in occupied:
            quad(v(x1, -hy, z0), v(x1, hy, z0), v(x1, hy, z1), v(x1, -hy, z1), False)         # face towards +x
        if (i, k - 1) not in occupied:
            quad(v(x0, -hy, z0), v(x1, -hy, z0), v(x1, hy, z0), v(x0, hy, z0), True)          # face towards -z
        if (i, k + 1) not in occupied:
            quad(v(x0, -hy, z1), v(x1, -hy, z1), v(x1, hy, z1), v(x0, hy, z1), False)         # face towards +z
    if top:
        for p in verts:
            if p[2] == zs[-1]:                                                 # the top face follows the top edge; nothing else sits above the lowest point of it
                p[2] = _top_at(top, p[0])
    if ends != SQUARE_ENDS:
        for p in verts:                                                        # shear the end faces: x moves with y only, so they stay planar
            if p[0] == xs[0]:
                p[0] = -hx - (sl if p[1] > 0 else sr)
            elif p[0] == xs[-1]:
                p[0] = hx + (el if p[1] > 0 else er)
    return verts, faces
