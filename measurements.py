"""Measuring the model: distances, lengths, angles, radii, areas, perimeters, volumes, thickness, clearance, coordinates and slope.

A measurement *reads* the model; it never changes it, and it is never a dimension that drives anything. What it measures is named by *references*:

    ``{"type": "point", "p": [x, y, z]}``                       a point in world space (metres)
    ``{"type": "vertex", "id": element, "index": n}``           a vertex of the element's world mesh (it follows the element when the geometry moves)
    ``{"type": "edge", "id": element, "a": n, "b": m}``         the segment between two vertices of one element
    ``{"type": "face", "id": element, "face": triangle}``       the planar face (all connected coplanar triangles) that contains that triangle
    ``{"type": "element", "id": element}``                      the whole element
    ``{"type": "path", "points": [[x, y, z], ...], "closed": bool}``   a polyline or polygon

Everything is computed exactly from the element meshes the viewports draw (modifiers included) and the element's own layer stack: no sampling, no
estimation. What cannot be measured honestly (an open surface has no volume, points not on a circle have no radius, a vertical line has no percentage
slope) is *refused with the reason*, never answered with a number.

Persistent measurements are kept in ``project.manifest["measurements"]`` and re-evaluated against the model whenever they are asked for, so a vertex,
face or element reference follows the geometry (and reports ``lost`` when what it pointed at is gone); a point reference stays where it was put.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, NamedTuple, Sequence

from ..tools.errors import BadRequest, NotFound
from ..tools.validation import new_id, text
from . import elements as E

Pt = tuple[float, float, float]
KINDS = ("coordinates", "distance", "horizontal", "vertical", "length", "angle", "radius", "diameter", "area", "perimeter", "volume", "thickness",
         "clearance", "slope")
LENGTH_UNITS = {"mm": 0.001, "cm": 0.01, "m": 1.0, "in": 0.0254, "ft": 0.3048}
DEFAULT_PRECISION = {"mm": 1, "cm": 2, "m": 3, "in": 2, "ft": 3}
EPS = 1e-9
PLANE_TOL = 1e-7                                                  # m: how far from a plane still counts as on it
CIRCLE_TOL = 1e-6                                                 # relative: how far from a circle still counts as on it
MAX_PAIRS = 4_000_000                                             # triangle pairs a clearance may compare before it asks for a face instead


# ------------------------------------------------------------------------------------------------------------------------- vector helpers
def _sub(a: Sequence[float], b: Sequence[float]) -> Pt:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a: Sequence[float], b: Sequence[float]) -> Pt:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _mul(a: Sequence[float], k: float) -> Pt:
    return (a[0] * k, a[1] * k, a[2] * k)


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Sequence[float], b: Sequence[float]) -> Pt:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _len(a: Sequence[float]) -> float:
    return math.sqrt(_dot(a, a))


def _unit(a: Sequence[float]) -> Pt:
    n = _len(a)
    if n < EPS:
        raise BadRequest("That measurement is degenerate: two of its points coincide")
    return (a[0] / n, a[1] / n, a[2] / n)


def _pt(value: Any, label: str = "point") -> Pt:
    if (not isinstance(value, (list, tuple)) or len(value) != 3
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in value)):
        raise BadRequest(f"{label} must be a point [x, y, z] in metres")
    return (float(value[0]), float(value[1]), float(value[2]))


def _angle_between(u: Sequence[float], v: Sequence[float]) -> float:
    c = max(-1.0, min(1.0, _dot(_unit(u), _unit(v))))
    return math.degrees(math.acos(c))


# ------------------------------------------------------------------------------------------------------------------------- references
class Prim(NamedTuple):
    kind: str                                  # point | segment | path | face | element
    name: str
    points: tuple                              # point: (p,) ; segment: (a, b) ; path: the polyline ; face: the boundary loop
    closed: bool = False
    normal: Pt | None = None                   # face
    mesh: Any = None                           # (vertices, faces, triangle indices) for face / element
    element: str | None = None
    area: float | None = None


def _mesh(project: Any, oid: str) -> tuple[list[Pt], list[tuple[int, int, int]]]:
    from . import documentation as D
    if not E.alive(project, oid):
        raise NotFound(f"Element {oid} does not exist")
    try:
        verts, faces = D.world_mesh(project, oid)
    except Exception:
        raise BadRequest(f"{_name(project, oid)} has no geometry to measure") from None
    if not verts or not faces:
        raise BadRequest(f"{_name(project, oid)} has no geometry to measure")
    return verts, faces


def _name(project: Any, oid: str) -> str:
    try:
        return project.model.get(oid)["name"] or E.category(project, oid).title()
    except Exception:
        return oid


def _tri_normal(a: Pt, b: Pt, c: Pt) -> Pt:
    return _cross(_sub(b, a), _sub(c, a))


def _face_region(verts: Sequence[Pt], faces: Sequence[Sequence[int]], seed: int) -> list[int]:
    """The triangles connected to ``seed`` through shared edges that lie in the same plane."""
    n0 = _tri_normal(*(verts[i] for i in faces[seed]))
    if _len(n0) < EPS:
        raise BadRequest("That triangle has no area: pick another point on the face")
    n0 = _unit(n0)
    p0 = verts[faces[seed][0]]
    by_edge: dict[tuple[int, int], list[int]] = {}
    for t, f in enumerate(faces):
        for i in range(3):
            a, b = f[i], f[(i + 1) % 3]
            by_edge.setdefault((min(a, b), max(a, b)), []).append(t)
    seen, stack = {seed}, [seed]
    while stack:
        t = stack.pop()
        f = faces[t]
        for i in range(3):
            a, b = f[i], f[(i + 1) % 3]
            for other in by_edge[(min(a, b), max(a, b))]:
                if other in seen:
                    continue
                g = faces[other]
                nn = _tri_normal(*(verts[j] for j in g))
                if _len(nn) < EPS:
                    continue
                if _dot(_unit(nn), n0) < 1 - 1e-9 or any(abs(_dot(_sub(verts[j], p0), n0)) > PLANE_TOL for j in g):
                    continue
                seen.add(other)
                stack.append(other)
    return sorted(seen)


def _boundary_loop(verts: Sequence[Pt], faces: Sequence[Sequence[int]], tris: Sequence[int]) -> list[Pt]:
    """The outer boundary of a set of triangles as an ordered loop (the longest closed chain of edges used once)."""
    use: dict[tuple[int, int], tuple[int, int]] = {}
    count: dict[tuple[int, int], int] = {}
    for t in tris:
        f = faces[t]
        for i in range(3):
            a, b = f[i], f[(i + 1) % 3]
            key = (min(a, b), max(a, b))
            count[key] = count.get(key, 0) + 1
            use[key] = (a, b)
    nxt: dict[int, list[int]] = {}
    for key, c in count.items():
        if c == 1:
            a, b = use[key]
            nxt.setdefault(a, []).append(b)
    loops: list[list[int]] = []
    visited: set[int] = set()
    for start in list(nxt):
        if start in visited:
            continue
        loop, cur = [start], start
        visited.add(start)
        while True:
            options = [v for v in nxt.get(cur, ()) if v not in visited or v == start]
            if not options:
                break
            cur = options[0]
            if cur == start:
                break
            loop.append(cur)
            visited.add(cur)
        loops.append(loop)
    if not loops:
        return []
    def span(loop: list[int]) -> float:
        return sum(_len(_sub(verts[loop[i]], verts[loop[(i + 1) % len(loop)]])) for i in range(len(loop)))
    return [verts[i] for i in max(loops, key=span)]


def resolve(project: Any, ref: Mapping[str, Any]) -> Prim:
    if not isinstance(ref, Mapping):
        raise BadRequest("Each reference must be an object with a type")
    kind = ref.get("type")
    if kind == "point":
        return Prim("point", "point", (_pt(ref.get("p")),))
    if kind == "path":
        pts = ref.get("points")
        if not isinstance(pts, (list, tuple)) or len(pts) < 2 or len(pts) > 10_000:
            raise BadRequest("A path needs between 2 and 10000 points")
        return Prim("path", "path", tuple(_pt(p) for p in pts), bool(ref.get("closed")))
    oid = text(ref.get("id"), "id", 80) if kind in ("vertex", "edge", "face", "element") else None
    if kind == "element":
        verts, faces = _mesh(project, oid)
        return Prim("element", _name(project, oid), (), mesh=(verts, faces, list(range(len(faces)))), element=oid)
    if kind in ("vertex", "edge"):
        verts, _ = _mesh(project, oid)
        idx = [ref.get("index")] if kind == "vertex" else [ref.get("a"), ref.get("b")]
        for i in idx:
            if not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < len(verts):
                raise BadRequest(f"{_name(project, oid)} has no vertex {i}: the geometry changed since it was picked")
        pts = tuple(verts[i] for i in idx)
        return Prim("point" if kind == "vertex" else "segment", _name(project, oid), pts, element=oid)
    if kind == "face":
        verts, faces = _mesh(project, oid)
        tri = ref.get("face")
        if not isinstance(tri, int) or isinstance(tri, bool) or not 0 <= tri < len(faces):
            raise BadRequest(f"{_name(project, oid)} has no face {tri}: the geometry changed since it was picked")
        region = _face_region(verts, faces, tri)
        normal = _unit(_tri_normal(*(verts[i] for i in faces[tri])))
        area = sum(_len(_tri_normal(*(verts[i] for i in faces[t]))) / 2 for t in region)
        return Prim("face", _name(project, oid), tuple(_boundary_loop(verts, faces, region)), True, normal, (verts, faces, region), oid, area)
    raise BadRequest(f"Unknown reference type '{kind}'", hint="point, vertex, edge, face, element or path")


# ------------------------------------------------------------------------------------------------------------------------- distances
def _point_segment(p: Pt, a: Pt, b: Pt) -> float:
    ab = _sub(b, a)
    d = _dot(ab, ab)
    t = 0.0 if d < EPS else max(0.0, min(1.0, _dot(_sub(p, a), ab) / d))
    return _len(_sub(p, _add(a, _mul(ab, t))))


def _point_triangle(p: Pt, a: Pt, b: Pt, c: Pt) -> float:
    n = _cross(_sub(b, a), _sub(c, a))
    nn = _dot(n, n)
    if nn > EPS * EPS:
        d = _dot(_sub(p, a), n) / math.sqrt(nn)
        q = _sub(p, _mul(n, _dot(_sub(p, a), n) / nn))
        if (_dot(_cross(_sub(b, a), _sub(q, a)), n) >= 0 and _dot(_cross(_sub(c, b), _sub(q, b)), n) >= 0 and _dot(_cross(_sub(a, c), _sub(q, c)), n) >= 0):
            return abs(d)
    return min(_point_segment(p, a, b), _point_segment(p, b, c), _point_segment(p, c, a))


def _segment_segment(p1: Pt, q1: Pt, p2: Pt, q2: Pt) -> float:
    d1, d2, r = _sub(q1, p1), _sub(q2, p2), _sub(p1, p2)
    a, e, f = _dot(d1, d1), _dot(d2, d2), _dot(d2, r)
    if a <= EPS and e <= EPS:
        return _len(r)
    if a <= EPS:
        s, t = 0.0, max(0.0, min(1.0, f / e))
    else:
        c = _dot(d1, r)
        if e <= EPS:
            t, s = 0.0, max(0.0, min(1.0, -c / a))
        else:
            b = _dot(d1, d2)
            den = a * e - b * b
            s = max(0.0, min(1.0, (b * f - c * e) / den)) if den > EPS else 0.0
            t = (b * s + f) / e
            if t < 0:
                t, s = 0.0, max(0.0, min(1.0, -c / a))
            elif t > 1:
                t, s = 1.0, max(0.0, min(1.0, (b - c) / a))
    return _len(_sub(_add(p1, _mul(d1, s)), _add(p2, _mul(d2, t))))


def _segment_hits_triangle(p: Pt, q: Pt, a: Pt, b: Pt, c: Pt) -> bool:
    d = _sub(q, p)
    e1, e2 = _sub(b, a), _sub(c, a)
    h = _cross(d, e2)
    det = _dot(e1, h)
    if abs(det) < 1e-14:
        return False
    f = 1.0 / det
    s = _sub(p, a)
    u = f * _dot(s, h)
    if u < -1e-12 or u > 1 + 1e-12:
        return False
    qv = _cross(s, e1)
    v = f * _dot(d, qv)
    if v < -1e-12 or u + v > 1 + 1e-12:
        return False
    t = f * _dot(e2, qv)
    return -1e-12 <= t <= 1 + 1e-12


def _tri_tri(t1: Sequence[Pt], t2: Sequence[Pt]) -> float:
    for i in range(3):
        if _segment_hits_triangle(t1[i], t1[(i + 1) % 3], *t2) or _segment_hits_triangle(t2[i], t2[(i + 1) % 3], *t1):
            return 0.0
    best = min(min(_point_triangle(p, *t2) for p in t1), min(_point_triangle(p, *t1) for p in t2))
    for i in range(3):
        for j in range(3):
            best = min(best, _segment_segment(t1[i], t1[(i + 1) % 3], t2[j], t2[(j + 1) % 3]))
    return best


def _triangles(prim: Prim) -> list[tuple[Pt, Pt, Pt]]:
    verts, faces, region = prim.mesh
    return [tuple(verts[i] for i in faces[t]) for t in region]                          # type: ignore[misc]


def _box(tri: Sequence[Pt]) -> tuple[Pt, Pt]:
    return (tuple(min(p[i] for p in tri) for i in range(3)), tuple(max(p[i] for p in tri) for i in range(3)))      # type: ignore[return-value]


def _box_gap(a: tuple[Pt, Pt], b: tuple[Pt, Pt]) -> float:
    return math.sqrt(sum(max(0.0, a[0][i] - b[1][i], b[0][i] - a[1][i]) ** 2 for i in range(3)))


def min_distance(a: Prim, b: Prim) -> tuple[float, bool]:
    """Exact minimum distance between two primitives (points, faces, elements) and whether they intersect or touch."""
    if a.kind == "point" and b.kind == "point":
        d = _len(_sub(a.points[0], b.points[0]))
        return d, d < EPS
    if a.kind == "point" or b.kind == "point":
        p, other = (a.points[0], b) if a.kind == "point" else (b.points[0], a)
        if other.mesh is None:
            raise BadRequest("A point can be measured to a point, a face or an element")
        d = min(_point_triangle(p, *t) for t in _triangles(other))
        return d, d < EPS
    if a.mesh is None or b.mesh is None:
        raise BadRequest("Only points, faces and elements have a distance to each other")
    ta, tb = _triangles(a), _triangles(b)
    if len(ta) * len(tb) > MAX_PAIRS:
        raise BadRequest(f"Too many triangles to compare ({len(ta)} × {len(tb)}): measure from a face or a vertex instead")
    boxes_b = [_box(t) for t in tb]
    best = math.inf
    for t in ta:
        bx = _box(t)
        for u, ub in zip(tb, boxes_b):
            if _box_gap(bx, ub) >= best:
                continue
            d = _tri_tri(t, u)
            if d < best:
                best = d
                if best < EPS:
                    return 0.0, True
    return best, best < EPS


# ------------------------------------------------------------------------------------------------------------------------- shapes
def _polygon_area(points: Sequence[Pt]) -> tuple[float, Pt]:
    """Area and unit normal of a closed planar polygon (Newell's method); refuses a polygon that is not flat."""
    if len(points) < 3:
        raise BadRequest("A closed profile needs at least three points")
    n = (0.0, 0.0, 0.0)
    for i, p in enumerate(points):
        q = points[(i + 1) % len(points)]
        n = _add(n, _cross(p, q))
    area = _len(n) / 2
    if area < EPS:
        raise BadRequest("Those points enclose no area (they are in a line)")
    u = _unit(n)
    worst = max(abs(_dot(_sub(p, points[0]), u)) for p in points)
    if worst > 1e-6:
        raise BadRequest(f"Those points are not in one plane (they are {worst * 1000:.2f} mm off it): the area would be ambiguous")
    return area, u


def _perimeter(points: Sequence[Pt], closed: bool) -> float:
    n = len(points)
    return sum(_len(_sub(points[i], points[(i + 1) % n])) for i in range(n if closed else n - 1))


def _fit_circle(points: Sequence[Pt]) -> tuple[Pt, float, Pt, float]:
    """The circle through the points: (centre, radius, normal, worst deviation). Three points define it; more must lie on it."""
    if len(points) < 3:
        raise BadRequest("A radius needs three points on the circle")
    a, b, c = points[0], points[len(points) // 3], points[2 * len(points) // 3]
    ab, ac = _sub(b, a), _sub(c, a)
    n = _cross(ab, ac)
    nn = _dot(n, n)
    if nn < EPS:
        raise BadRequest("Those points are in a line: they do not define a circle")
    centre = _add(a, _mul(_add(_mul(_cross(n, ab), _dot(ac, ac)), _mul(_cross(ac, n), _dot(ab, ab))), 1 / (2 * nn)))
    r = _len(_sub(a, centre))
    nu = _unit(n)
    dev = max(max(abs(_len(_sub(p, centre)) - r), abs(_dot(_sub(p, centre), nu))) for p in points)
    return centre, r, nu, dev


# ------------------------------------------------------------------------------------------------------------------------- results
def _convert(value: float, unit: str, power: int = 1) -> float:
    return value / (LENGTH_UNITS[unit] ** power)


def _row(label: str, value: float, kind: str, unit: str, precision: int, symbol: str | None = None) -> dict[str, Any]:
    if kind == "length":
        v, u = _convert(value, unit), unit
    elif kind == "area":
        v, u = _convert(value, unit, 2), f"{unit}²"
    elif kind == "volume":
        v, u = _convert(value, unit, 3), f"{unit}³"
    elif kind == "angle":
        v, u = value, "°"
    else:
        v, u = value, symbol or ""
    p = 2 if kind == "angle" else precision
    return {"label": label, "value": v, "si": value, "unit": u, "display": f"{v:,.{p}f} {u}".strip().replace(",", " ")}


class _Out:
    def __init__(self, project: Any, kind: str, unit: str, precision: int) -> None:
        self.project, self.kind, self.unit, self.precision = project, kind, unit, precision
        self.rows: list[dict[str, Any]] = []
        self.notes: list[str] = []
        self.basis = ""
        self.graphics: list[list[Pt]] = []
        self.at: Pt | None = None

    def add(self, label: str, value: float, kind: str = "length", symbol: str | None = None) -> None:
        self.rows.append(_row(label, value, kind, self.unit, self.precision, symbol))

    def done(self, inputs: list[str]) -> dict[str, Any]:
        return {"kind": self.kind, "revision": self.project.revision, "primary": self.rows[0], "values": self.rows, "inputs": inputs, "basis": self.basis,
                "notes": self.notes, "graphics": self.graphics, "label_at": list(self.at) if self.at else None, "unit": self.unit,
                "precision": self.precision}


def _need(prims: Sequence[Prim], low: int, high: int, what: str) -> None:
    if not low <= len(prims) <= high:
        raise BadRequest(what)


def _anchor_of(prim: Prim) -> Pt:
    if prim.kind == "point":
        return prim.points[0]
    pts = prim.points if prim.points else tuple(v for v in prim.mesh[0])
    return tuple(sum(p[i] for p in pts) / len(pts) for i in range(3))                    # type: ignore[return-value]


def _centre_of(project: Any, prim: Prim) -> Pt:
    if prim.kind == "element":
        verts = prim.mesh[0]
        return tuple((min(v[i] for v in verts) + max(v[i] for v in verts)) / 2 for i in range(3))      # type: ignore[return-value]
    return _anchor_of(prim)


def _direction(prim: Prim, what: str) -> Pt:
    if prim.kind == "segment" or (prim.kind == "path" and len(prim.points) == 2):
        return _sub(prim.points[1], prim.points[0])
    raise BadRequest(f"{what}: pick an edge or two points")


def measure(project: Any, kind: str, refs: Sequence[Mapping[str, Any]], *, unit: str | None = None, precision: int | None = None,
            origin: Any = None) -> dict[str, Any]:
    """One measurement. Raises ``BadRequest`` (with the reason) when the references cannot be measured honestly."""
    if kind not in KINDS:
        raise BadRequest(f"Unknown measurement '{kind}'", hint=", ".join(KINDS))
    if not isinstance(refs, (list, tuple)) or not refs or len(refs) > 100:
        raise BadRequest("Give the references to measure (at least one)")
    unit = unit or {"mm": "mm", "cm": "cm", "m": "m"}.get(project.manifest.get("unit", "mm"), "mm")
    if unit not in LENGTH_UNITS:
        raise BadRequest(f"unit must be one of {', '.join(LENGTH_UNITS)}")
    if precision is None:
        precision = DEFAULT_PRECISION[unit]
    if not isinstance(precision, int) or isinstance(precision, bool) or not 0 <= precision <= 8:
        raise BadRequest("precision must be a whole number from 0 to 8")
    prims = [resolve(project, r) for r in refs]
    out = _Out(project, kind, unit, precision)
    names = [p.name for p in prims]
    fn = _DISPATCH[kind]
    fn(project, out, prims, origin)
    return out.done(names)


def _coordinates(project: Any, out: _Out, prims: Sequence[Prim], origin: Any) -> None:
    _need(prims, 1, 1, "Pick one point (or one element)")
    p = prims[0]
    pt = _centre_of(project, p) if p.kind != "point" else p.points[0]
    out.at = pt
    out.add("X", pt[0])
    out.add("Y", pt[1])
    out.add("Z", pt[2])
    out.basis = "World coordinates" + (" of the centre of the element's bounding box" if p.kind != "point" else "")
    if origin is not None:
        o = _pt(origin, "origin")
        out.add("X from origin", pt[0] - o[0])
        out.add("Y from origin", pt[1] - o[1])
        out.add("Z from origin", pt[2] - o[2])
        out.basis += f"; local values are measured from the chosen origin ({o[0]:.3f}, {o[1]:.3f}, {o[2]:.3f}) m, axes parallel to the world axes"
    levels = sorted(project.manifest["levels"], key=lambda l: l["elevation"])
    below = [l for l in levels if l["elevation"] <= pt[2] + 1e-9]
    if below:
        out.add(f"Height above {below[-1]['name']}", pt[2] - below[-1]["elevation"])


def _distance(project: Any, out: _Out, prims: Sequence[Prim], origin: Any) -> None:
    _need(prims, 2, 2, "Pick two things to measure between")
    a, b = prims
    if a.kind == "point" and b.kind == "point":
        d = _sub(b.points[0], a.points[0])
        out.add("Distance", _len(d))
        out.add("Horizontal", math.hypot(d[0], d[1]))
        out.add("Vertical", d[2])
        out.add("ΔX", d[0])
        out.add("ΔY", d[1])
        out.graphics.append([a.points[0], b.points[0]])
        out.at = _mul(_add(a.points[0], b.points[0]), 0.5)
        out.basis = "Straight line between the two points"
        return
    ca, cb = _centre_of(project, a), _centre_of(project, b)
    out.add("Distance between centres", _len(_sub(cb, ca)))
    out.graphics.append([ca, cb])
    out.at = _mul(_add(ca, cb), 0.5)
    if a.mesh is not None and (b.mesh is not None or b.kind == "point") or (a.kind == "point" and b.mesh is not None):
        d, touching = min_distance(a, b)
        out.add("Minimum gap", d)
        if touching:
            out.notes.append("The two touch or overlap, so the gap is zero")
    out.basis = "Between the centres of the bounding boxes (points and faces: their own centre); the minimum gap is exact on the meshes"


def _horizontal(project: Any, out: _Out, prims: Sequence[Prim], origin: Any) -> None:
    _need(prims, 2, 2, "Pick two points")
    a, b = (_centre_of(project, p) for p in prims)
    d = _sub(b, a)
    out.add("Horizontal distance", math.hypot(d[0], d[1]))
    out.add("ΔX", d[0])
    out.add("ΔY", d[1])
    out.graphics.append([(a[0], a[1], a[2]), (b[0], b[1], a[2])])
    out.at = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, a[2])
    out.basis = "Projected on the horizontal plane (the plan distance)"


def _vertical(project: Any, out: _Out, prims: Sequence[Prim], origin: Any) -> None:
    _need(prims, 2, 2, "Pick two points")
    a, b = (_centre_of(project, p) for p in prims)
    out.add("Vertical distance", abs(b[2] - a[2]))
    out.add("Elevation difference (second − first)", b[2] - a[2])
    out.add("First elevation", a[2])
    out.add("Second elevation", b[2])
    out.graphics.append([(b[0], b[1], a[2]), b[0:3]])                                    # type: ignore[list-item]
    out.at = (b[0], b[1], (a[2] + b[2]) / 2)
    out.basis = "Difference in elevation (Z)"


def _length(project: Any, out: _Out, prims: Sequence[Prim], origin: Any) -> None:
    pts: list[Pt] = []
    closed = False
    if len(prims) == 1 and prims[0].kind in ("segment", "path", "face"):
        pts, closed = list(prims[0].points), prims[0].closed
        if prims[0].kind == "face":
            out.notes.append("The length of a face is its outline")
    elif all(p.kind == "point" for p in prims) and len(prims) >= 2:
        pts = [p.points[0] for p in prims]
    elif all(p.kind in ("segment", "point") for p in prims):
        for p in prims:
            pts.extend(p.points)
    else:
        raise BadRequest("Pick an edge, a path or at least two points")
    if len(pts) < 2:
        raise BadRequest("A length needs at least two points")
    out.add("Length", _perimeter(pts, closed))
    out.graphics.append(pts + ([pts[0]] if closed else []))
    out.at = pts[len(pts) // 2]
    out.basis = f"Sum of {len(pts) - (0 if closed else 1)} straight segment{'s' if len(pts) > 2 else ''}" + (" (closed)" if closed else "")


def _angle(project: Any, out: _Out, prims: Sequence[Prim], origin: Any) -> None:
    if len(prims) == 3 and all(p.kind == "point" for p in prims):
        a, b, c = (p.points[0] for p in prims)
        deg = _angle_between(_sub(a, b), _sub(c, b))
        out.add("Angle", deg, "angle")
        out.add("Complement to 180°", 180 - deg, "angle")
        out.graphics += [[b, a], [b, c]]
        out.at = b
        out.basis = "Angle at the middle point between the lines to the other two"
        return
    _need(prims, 2, 2, "Pick two edges, two faces, an edge and a face, or three points (the angle is at the middle one)")
    a, b = prims
    if a.kind == "face" and b.kind == "face":
        deg = _angle_between(a.normal, b.normal)                                          # type: ignore[arg-type]
        out.add("Angle between planes", min(deg, 180 - deg), "angle")
        out.add("Between the face normals", deg, "angle")
        out.add("Dihedral (inside) angle", 180 - deg, "angle")
        if deg < 1e-6 or abs(deg - 180) < 1e-6:
            out.notes.append("The faces are parallel")
        out.at = _anchor_of(a)
        out.basis = "Between the planes of the two faces; the first value is the acute one, the dihedral angle assumes outward-facing normals"
        return
    if {a.kind, b.kind} == {"face", "segment"}:
        face, seg = (a, b) if a.kind == "face" else (b, a)
        d = _sub(seg.points[1], seg.points[0])
        deg = 90 - min(_angle_between(d, face.normal), 180 - _angle_between(d, face.normal))   # type: ignore[arg-type]
        out.add("Angle between line and plane", deg, "angle")
        out.graphics.append(list(seg.points))
        out.at = _mul(_add(seg.points[0], seg.points[1]), 0.5)
        out.basis = "Between the edge and the plane of the face (0° = lies in the plane, 90° = perpendicular)"
        return
    da, db = _direction(a, "An angle between lines"), _direction(b, "An angle between lines")
    deg = _angle_between(da, db)
    out.add("Angle", deg, "angle")
    out.add("Acute angle", min(deg, 180 - deg), "angle")
    out.add("Supplementary angle", 180 - deg, "angle")
    out.graphics += [list(a.points), list(b.points)]
    out.at = _mul(_add(a.points[0], b.points[0]), 0.5)
    out.basis = "Between the directions of the two edges, as picked (first point to second point)"


def _circle_points(prims: Sequence[Prim]) -> list[Pt]:
    if len(prims) == 1 and prims[0].kind in ("path", "face"):
        return list(prims[0].points)
    if len(prims) >= 3 and all(p.kind == "point" for p in prims):
        return [p.points[0] for p in prims]
    raise BadRequest("Pick three points on the circle, or a closed circular outline (a face or a path)")


def _radius(project: Any, out: _Out, prims: Sequence[Prim], origin: Any) -> None:
    pts = _circle_points(prims)
    centre, r, normal, dev = _fit_circle(pts)
    if dev > max(CIRCLE_TOL, r * 1e-6):
        raise BadRequest(f"Those points are not on a circle (the worst is {dev * 1000:.3f} mm off the best circle through them): there is no radius to report")
    out.add("Radius" if out.kind == "radius" else "Diameter", r if out.kind == "radius" else 2 * r)
    out.add("Diameter" if out.kind == "radius" else "Radius", 2 * r if out.kind == "radius" else r)
    out.add("Circumference", 2 * math.pi * r)
    out.add("Centre X", centre[0])
    out.add("Centre Y", centre[1])
    out.add("Centre Z", centre[2])
    out.graphics.append([centre, pts[0]])
    out.at = centre
    out.basis = f"The circle through the points ({len(pts)} checked, worst deviation {dev * 1000:.4f} mm)"


def _area(project: Any, out: _Out, prims: Sequence[Prim], origin: Any) -> None:
    _need(prims, 1, 1, "Pick one face, one closed profile or one element")
    p = prims[0]
    if p.kind == "face":
        out.add("Area", p.area or 0.0, "area")
        out.add("Perimeter", _perimeter(p.points, True))
        out.graphics.append(list(p.points) + [p.points[0]])
        out.at = _anchor_of(p)
        out.basis = "The planar face (all connected coplanar triangles)" + ("; a hole in it is not subtracted from the outline's perimeter" if False else "")
        return
    if p.kind == "path":
        pts = list(p.points)
        if not p.closed:
            raise BadRequest("An open path encloses no area: close the profile")
        area, _ = _polygon_area(pts)
        out.add("Area", area, "area")
        out.add("Perimeter", _perimeter(pts, True))
        out.graphics.append(pts + [pts[0]])
        out.at = _anchor_of(Prim("path", "path", tuple(pts)))
        out.basis = "Closed planar profile (Newell's method)"
        return
    if p.kind == "element":
        verts, faces, _ = p.mesh
        area = sum(_len(_tri_normal(*(verts[i] for i in f))) / 2 for f in faces)
        out.add("Surface area", area, "area")
        out.at = _centre_of(project, p)
        out.basis = "Every surface of the element's mesh, openings and modifiers included"
        return
    raise BadRequest("An area needs a face, a closed profile or an element")


def _perimeter_kind(project: Any, out: _Out, prims: Sequence[Prim], origin: Any) -> None:
    _need(prims, 1, 1, "Pick one face or one closed profile")
    p = prims[0]
    if p.kind == "face":
        out.add("Perimeter", _perimeter(p.points, True))
        out.graphics.append(list(p.points) + [p.points[0]])
        out.at = _anchor_of(p)
        out.basis = "The outer boundary of the face"
    elif p.kind == "path" and p.closed:
        _polygon_area(list(p.points))
        out.add("Perimeter", _perimeter(p.points, True))
        out.graphics.append(list(p.points) + [p.points[0]])
        out.at = _anchor_of(p)
        out.basis = "The closed profile"
    else:
        raise BadRequest("A perimeter needs a face or a closed profile")


def _volume(project: Any, out: _Out, prims: Sequence[Prim], origin: Any) -> None:
    _need(prims, 1, 1, "Pick one element")
    p = prims[0]
    if p.kind != "element":
        raise BadRequest("A volume belongs to a whole element: pick the element, not a face or a point")
    st = E.solid_status(project, p.element)                                                # type: ignore[arg-type]
    if st.state != "solid":
        raise BadRequest(f"{p.name} has no volume: {st.message or st.state}")
    out.add("Volume", st.volume, "volume")                                                 # type: ignore[arg-type]
    out.at = _centre_of(project, p)
    out.basis = "SGEO: signed volume of the closed, oriented mesh (openings and modifiers taken out)"


def _thickness(project: Any, out: _Out, prims: Sequence[Prim], origin: Any) -> None:
    from . import layers as LY
    _need(prims, 1, 1, "Pick one element, or one face of it")
    p = prims[0]
    if p.kind == "element" and E.category(project, p.element) in LY.LAYERED:               # type: ignore[arg-type]
        stack = LY.stack(project, p.element)                                               # type: ignore[arg-type]
        out.add("Thickness", LY.total(stack))
        if len(stack) > 1:
            for r in stack:
                out.add(f"Layer {r.get('name') or r.get('material')}", float(r["thickness"]))
        out.at = _centre_of(project, p)
        out.basis = "The element's own layer stack"
        return
    if p.kind == "face":
        verts, faces, region = p.mesh
        n = p.normal                                                                       # type: ignore[assignment]
        c = _anchor_of(p)
        probe = _sub(c, _mul(n, 1e-6))                                                     # a hair inside, so the face itself is not the hit
        best = math.inf
        for t in range(len(faces)):
            if t in region:
                continue
            a, b, cc = (verts[i] for i in faces[t])
            far = _sub(probe, _mul(n, 1000.0))
            if _segment_hits_triangle(probe, far, a, b, cc):
                nn = _tri_normal(a, b, cc)
                den = _dot(_sub(far, probe), nn)
                if abs(den) > 1e-15:
                    s = _dot(_sub(a, probe), nn) / den
                    best = min(best, s * 1000.0 + 1e-6)
        if not math.isfinite(best):
            raise BadRequest("There is no surface behind that face: the element is open there, so no thickness can be given")
        out.add("Thickness at the face centre", best)
        out.graphics.append([c, _sub(c, _mul(n, best))])
        out.at = c
        out.basis = "From the face, perpendicular to it, to the first surface behind it"
        return
    raise BadRequest("Thickness is measured on a layered element (wall, floor, roof) or from a face of a solid")


def _clearance(project: Any, out: _Out, prims: Sequence[Prim], origin: Any) -> None:
    _need(prims, 2, 2, "Pick two elements (or faces) to find the clearance between")
    a, b = prims
    if a.mesh is None or b.mesh is None:
        raise BadRequest("Clearance is measured between elements or faces")
    d, touching = min_distance(a, b)
    out.add("Clearance", d)
    if touching:
        out.notes.append("The two touch or overlap: there is no clearance")
    out.graphics.append([_centre_of(project, a), _centre_of(project, b)])
    out.at = _mul(_add(_centre_of(project, a), _centre_of(project, b)), 0.5)
    out.basis = "Minimum distance between the two meshes (exact: closest triangle features), not between their bounding boxes"


def _slope(project: Any, out: _Out, prims: Sequence[Prim], origin: Any) -> None:
    _need(prims, 1, 2, "Pick an edge, two points, or a face")
    if len(prims) == 2 and all(p.kind == "point" for p in prims):
        d = _sub(prims[1].points[0], prims[0].points[0])
        out.graphics.append([prims[0].points[0], prims[1].points[0]])
        out.at = _mul(_add(prims[0].points[0], prims[1].points[0]), 0.5)
    elif len(prims) == 1 and prims[0].kind == "segment":
        d = _sub(prims[0].points[1], prims[0].points[0])
        out.graphics.append(list(prims[0].points))
        out.at = _mul(_add(prims[0].points[0], prims[0].points[1]), 0.5)
    elif len(prims) == 1 and prims[0].kind == "face":
        n = prims[0].normal                                                                 # type: ignore[assignment]
        inclination = math.degrees(math.acos(max(0.0, min(1.0, abs(n[2])))))
        out.add("Inclination from horizontal", inclination, "angle")
        if inclination > 89.999:
            out.notes.append("The face is vertical: its percentage and pitch are infinite and are not reported")
        else:
            t = math.tan(math.radians(inclination))
            out.add("Slope", t * 100, "number", "%")
            out.add("Pitch (rise per 1 run)", t, "number", ":1")
        if inclination < 1e-6:
            out.notes.append("The face is horizontal")
        out.at = _anchor_of(prims[0])
        out.basis = "The inclination of the face's plane from the horizontal"
        return
    else:
        raise BadRequest("Slope is measured on an edge, between two points, or on a face")
    run = math.hypot(d[0], d[1])
    inclination = math.degrees(math.atan2(abs(d[2]), run))
    out.add("Inclination from horizontal", inclination, "angle")
    if run < EPS:
        if abs(d[2]) < EPS:
            raise BadRequest("Those points coincide")
        out.notes.append("The line is vertical: its percentage and pitch are infinite and are not reported")
    else:
        out.add("Slope", abs(d[2]) / run * 100, "number", "%")
        out.add("Pitch (rise per 1 run)", abs(d[2]) / run, "number", ":1")
        out.add("Rise", d[2])
        out.add("Run", run)
    out.basis = "Rise over the horizontal run of the line"


_DISPATCH = {"coordinates": _coordinates, "distance": _distance, "horizontal": _horizontal, "vertical": _vertical, "length": _length, "angle": _angle,
             "radius": _radius, "diameter": _radius, "area": _area, "perimeter": _perimeter_kind, "volume": _volume, "thickness": _thickness,
             "clearance": _clearance, "slope": _slope}


# ------------------------------------------------------------------------------------------------------------------------- picking
def nearest_features(project: Any, oid: str, point: Sequence[float], radius: float = 0.05) -> dict[str, Any]:
    """What a pointer at ``point`` (metres) can snap to on an element: its nearest vertex and the edge and face it is on. For the measure tools."""
    verts, faces = _mesh(project, oid)
    p = _pt(point)
    vi = min(range(len(verts)), key=lambda i: _len(_sub(verts[i], p)))
    vertex = {"type": "vertex", "id": oid, "index": vi, "point": list(verts[vi]), "distance": _len(_sub(verts[vi], p))}
    best_edge, best_d = None, math.inf
    best_face, best_fd = None, math.inf
    for t, f in enumerate(faces):
        for i in range(3):
            a, b = f[i], f[(i + 1) % 3]
            d = _point_segment(p, verts[a], verts[b])
            if d < best_d:
                best_d, best_edge = d, (a, b)
        fd = _point_triangle(p, *(verts[i] for i in f))
        if fd < best_fd:
            best_fd, best_face = fd, t
    return {"vertex": vertex if vertex["distance"] <= radius else None,
            "edge": {"type": "edge", "id": oid, "a": best_edge[0], "b": best_edge[1], "a_point": list(verts[best_edge[0]]), "b_point": list(verts[best_edge[1]]),
                     "distance": best_d} if best_edge and best_d <= radius else None,
            "face": {"type": "face", "id": oid, "face": best_face, "distance": best_fd} if best_face is not None else None}


def figures(project: Any, oid: str, unit: str | None = None) -> dict[str, Any]:
    """Every figure that applies to one element, each by the same rules (a figure that does not apply is listed with its reason, not left out)."""
    out: dict[str, Any] = {"element": oid, "name": _name(project, oid), "category": E.category(project, oid), "figures": [], "unavailable": []}
    for kind in ("volume", "area", "thickness", "coordinates"):
        try:
            r = measure(project, kind, [{"type": "element", "id": oid}], unit=unit)
            out["figures"].append({"kind": kind, **{k: r[k] for k in ("values", "basis", "notes")}})
        except BadRequest as exc:
            out["unavailable"].append({"kind": kind, "reason": str(exc)})
    return out


# ------------------------------------------------------------------------------------------------------------------------- persistent measurements
def _store(project: Any, create: bool = False) -> list[dict[str, Any]]:
    if create:
        return project.manifest.setdefault("measurements", [])
    return project.manifest.get("measurements") or []


def add(project: Any, kind: str, refs: Sequence[Mapping[str, Any]], *, label: str | None = None, unit: str | None = None, precision: int | None = None,
        origin: Any = None) -> dict[str, Any]:
    """Keep a measurement with the project. It is checked first (an unmeasurable one is not stored) and does not change the model."""
    result = measure(project, kind, refs, unit=unit, precision=precision, origin=origin)
    item = {"id": new_id(), "kind": kind, "refs": [dict(r) for r in refs], "label": text(label, "label", 80) if label else "",
            "unit": result["unit"], "precision": result["precision"]}
    if origin is not None:
        item["origin"] = list(_pt(origin, "origin"))
    _store(project, True).append(item)
    return {"id": item["id"], "result": result}


def remove(project: Any, mid: str) -> dict[str, Any]:
    items = _store(project, True)
    keep = [m for m in items if m["id"] != mid]
    if len(keep) == len(items):
        raise NotFound(f"Measurement {mid} does not exist")
    if keep:
        project.manifest["measurements"] = keep
    else:
        project.manifest.pop("measurements", None)
    return {"removed": mid}


def edit(project: Any, mid: str, *, label: str | None = None, unit: str | None = None, precision: int | None = None) -> dict[str, Any]:
    for item in _store(project, True):
        if item["id"] == mid:
            if label is not None:
                item["label"] = text(label, "label", 80, empty=True)
            if unit is not None:
                if unit not in LENGTH_UNITS:
                    raise BadRequest(f"unit must be one of {', '.join(LENGTH_UNITS)}")
                item["unit"] = unit
            if precision is not None:
                if not isinstance(precision, int) or isinstance(precision, bool) or not 0 <= precision <= 8:
                    raise BadRequest("precision must be a whole number from 0 to 8")
                item["precision"] = precision
            return {"id": mid}
    raise NotFound(f"Measurement {mid} does not exist")


def evaluate_all(project: Any) -> list[dict[str, Any]]:
    """Every kept measurement against the model as it is now. One that points at what is gone says ``lost`` and why; it never shows a stale figure."""
    out = []
    for item in _store(project):
        entry = {"id": item["id"], "kind": item["kind"], "label": item.get("label", ""), "refs": item["refs"], "unit": item.get("unit"), "precision": item.get("precision")}
        try:
            entry["result"] = measure(project, item["kind"], item["refs"], unit=item.get("unit"), precision=item.get("precision"), origin=item.get("origin"))
            entry["status"] = "ok"
        except (BadRequest, NotFound) as exc:
            entry["status"] = "lost"
            entry["reason"] = str(exc)
        out.append(entry)
    return out
