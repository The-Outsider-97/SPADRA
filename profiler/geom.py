"""Pure 2D geometry for Profiler: no sketch, no document, no solver. Points are ``(x, y)`` tuples in the caller's drawing unit.

Everything here is exact analytic geometry (lines, circles, circular arcs) except splines, which are evaluated numerically and
whose length is therefore a *documented-tolerance* approximation (see :func:`curve_length`). Angles are radians, counter-clockwise from +X.
"""
from __future__ import annotations

import math
from typing import Sequence

Pt = tuple[float, float]
EPS = 1e-9
TAU = 2.0 * math.pi


# ------------------------------------------------------------------------------------------------------------------ vectors
def sub(a: Pt, b: Pt) -> Pt: return (a[0] - b[0], a[1] - b[1])
def add(a: Pt, b: Pt) -> Pt: return (a[0] + b[0], a[1] + b[1])
def mul(a: Pt, k: float) -> Pt: return (a[0] * k, a[1] * k)
def dot(a: Pt, b: Pt) -> float: return a[0] * b[0] + a[1] * b[1]
def cross(a: Pt, b: Pt) -> float: return a[0] * b[1] - a[1] * b[0]
def norm(a: Pt) -> float: return math.hypot(a[0], a[1])
def dist(a: Pt, b: Pt) -> float: return math.hypot(a[0] - b[0], a[1] - b[1])
def mid(a: Pt, b: Pt) -> Pt: return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
def lerp(a: Pt, b: Pt, t: float) -> Pt: return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
def unit(a: Pt) -> Pt:
    n = norm(a)
    if n < EPS:
        raise ValueError("zero-length direction")
    return (a[0] / n, a[1] / n)
def perp(a: Pt) -> Pt: return (-a[1], a[0])          # rotate +90 degrees
def angle_of(a: Pt) -> float: return math.atan2(a[1], a[0])
def polar(c: Pt, r: float, ang: float) -> Pt: return (c[0] + r * math.cos(ang), c[1] + r * math.sin(ang))
def norm_angle(a: float) -> float:
    """Angle in [0, 2*pi)."""
    a = math.fmod(a, TAU)
    return a + TAU if a < 0 else a


def rotate_pt(p: Pt, about: Pt, ang: float) -> Pt:
    c, s = math.cos(ang), math.sin(ang)
    d = sub(p, about)
    return (about[0] + d[0] * c - d[1] * s, about[1] + d[0] * s + d[1] * c)


def mirror_pt(p: Pt, a: Pt, b: Pt) -> Pt:
    """Reflect ``p`` in the infinite line through ``a`` and ``b``."""
    d = unit(sub(b, a))
    v = sub(p, a)
    along = dot(v, d)
    foot = (a[0] + d[0] * along, a[1] + d[1] * along)
    return (2 * foot[0] - p[0], 2 * foot[1] - p[1])


# ------------------------------------------------------------------------------------------------------------ nearest / on
def seg_param(p: Pt, a: Pt, b: Pt) -> float:
    d = sub(b, a)
    n2 = dot(d, d)
    return 0.0 if n2 < EPS * EPS else dot(sub(p, a), d) / n2


def nearest_on_segment(p: Pt, a: Pt, b: Pt) -> tuple[Pt, float]:
    t = min(1.0, max(0.0, seg_param(p, a, b)))
    q = lerp(a, b, t)
    return q, dist(p, q)


def dist_point_line(p: Pt, a: Pt, b: Pt) -> float:
    """Signed distance from ``p`` to the infinite line a->b (positive on the left)."""
    d = sub(b, a)
    n = norm(d)
    return cross(d, sub(p, a)) / n if n > EPS else dist(p, a)


# ---------------------------------------------------------------------------------------------------------------- arcs
def arc_sweep(c: Pt, s: Pt, e: Pt, ccw: bool = True) -> float:
    """Sweep angle in (0, 2*pi] of the arc c-centred from ``s`` to ``e`` (coincident ends = full circle)."""
    a0, a1 = angle_of(sub(s, c)), angle_of(sub(e, c))
    sw = norm_angle(a1 - a0) if ccw else norm_angle(a0 - a1)
    return TAU if sw < 1e-12 else sw


def angle_in_arc(ang: float, a0: float, sweep: float, ccw: bool = True) -> bool:
    d = norm_angle(ang - a0) if ccw else norm_angle(a0 - ang)
    return d <= sweep + 1e-9


def arc_point(c: Pt, r: float, a0: float, sweep: float, t: float, ccw: bool = True) -> Pt:
    return polar(c, r, a0 + (sweep * t if ccw else -sweep * t))


def arc_from_start_end_radius(s: Pt, e: Pt, r: float, *, large: bool = False, ccw: bool = True) -> Pt:
    """Centre of the radius-``r`` arc from ``s`` to ``e`` going counter-clockwise (``ccw``) or clockwise.

    Two solutions exist for a given direction (minor and major arc); ``large`` picks the major one. ``r`` smaller than half the chord has no
    solution and raises ``ValueError`` (a zero or negative radius is refused the same way).
    """
    if r <= EPS:
        raise ValueError("arc radius must be positive")
    chord = dist(s, e)
    if chord < EPS:
        raise ValueError("arc ends coincide")
    if r < chord / 2.0 - 1e-12:
        raise ValueError("radius %.6g is smaller than half the chord %.6g: no arc" % (r, chord / 2.0))
    h = math.sqrt(max(0.0, r * r - (chord / 2.0) ** 2))
    n = unit(perp(sub(e, s)))                      # left of s->e
    m = mid(s, e)
    # For a ccw minor arc the centre lies to the LEFT of s->e; the major arc has it on the right; clockwise mirrors both.
    side = 1.0 if (ccw != large) else -1.0
    return (m[0] + n[0] * h * side, m[1] + n[1] * h * side)


def circle_through(a: Pt, b: Pt, c: Pt) -> tuple[Pt, float]:
    d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    if abs(d) < EPS:
        raise ValueError("the three points are collinear")
    ux = ((a[0] ** 2 + a[1] ** 2) * (b[1] - c[1]) + (b[0] ** 2 + b[1] ** 2) * (c[1] - a[1]) + (c[0] ** 2 + c[1] ** 2) * (a[1] - b[1])) / d
    uy = ((a[0] ** 2 + a[1] ** 2) * (c[0] - b[0]) + (b[0] ** 2 + b[1] ** 2) * (a[0] - c[0]) + (c[0] ** 2 + c[1] ** 2) * (b[0] - a[0])) / d
    return (ux, uy), dist((ux, uy), a)


def flatten_arc(c: Pt, r: float, a0: float, sweep: float, ccw: bool = True, tol: float = 1e-3) -> list[Pt]:
    """Polyline within ``tol`` (maximum sagitta) of the arc; always includes both ends exactly."""
    step = 2.0 * math.acos(max(-1.0, min(1.0, 1.0 - tol / max(r, tol)))) if r > tol else math.pi / 2
    n = max(2, int(math.ceil(sweep / max(step, 1e-3))))
    return [arc_point(c, r, a0, sweep, i / n, ccw) for i in range(n + 1)]


# ------------------------------------------------------------------------------------------------------- intersections
def line_line(a: Pt, b: Pt, c: Pt, d: Pt, *, segments: bool = True) -> Pt | None:
    """Intersection of a-b with c-d (None if parallel, or outside either segment when ``segments``)."""
    r, s = sub(b, a), sub(d, c)
    den = cross(r, s)
    if abs(den) < EPS * max(1.0, norm(r) * norm(s)):
        return None
    t = cross(sub(c, a), s) / den
    u = cross(sub(c, a), r) / den
    if segments and (t < -1e-9 or t > 1 + 1e-9 or u < -1e-9 or u > 1 + 1e-9):
        return None
    return (a[0] + r[0] * t, a[1] + r[1] * t)


def line_circle(a: Pt, b: Pt, c: Pt, r: float, *, segment: bool = True) -> list[Pt]:
    d = sub(b, a)
    f = sub(a, c)
    qa = dot(d, d)
    if qa < EPS * EPS:
        return []
    qb = 2 * dot(f, d)
    qc = dot(f, f) - r * r
    disc = qb * qb - 4 * qa * qc
    if disc < -1e-12 * max(1.0, qb * qb):
        return []
    disc = math.sqrt(max(0.0, disc))
    out = []
    for t in sorted({(-qb - disc) / (2 * qa), (-qb + disc) / (2 * qa)}):
        if segment and (t < -1e-9 or t > 1 + 1e-9):
            continue
        out.append((a[0] + d[0] * t, a[1] + d[1] * t))
    if len(out) == 2 and dist(out[0], out[1]) < 1e-9:
        out = out[:1]
    return out


def circle_circle(c1: Pt, r1: float, c2: Pt, r2: float) -> list[Pt]:
    d = dist(c1, c2)
    if d < EPS or d > r1 + r2 + 1e-9 or d < abs(r1 - r2) - 1e-9:
        return []
    a = (r1 * r1 - r2 * r2 + d * d) / (2 * d)
    h = math.sqrt(max(0.0, r1 * r1 - a * a))
    u = unit(sub(c2, c1))
    p = (c1[0] + u[0] * a, c1[1] + u[1] * a)
    if h < 1e-9:
        return [p]
    n = perp(u)
    return [(p[0] - n[0] * h, p[1] - n[1] * h), (p[0] + n[0] * h, p[1] + n[1] * h)]


def point_on_circle_arc(p: Pt, c: Pt, a0: float, sweep: float, ccw: bool = True) -> bool:
    return angle_in_arc(angle_of(sub(p, c)), a0, sweep, ccw)


# ------------------------------------------------------------------------------------------------- fillet / chamfer / offset
def fillet_corner(corner: Pt, p1: Pt, p2: Pt, r: float) -> dict:
    """Tangent fillet of radius ``r`` between the rays corner->p1 and corner->p2.

    Returns ``{"t1", "t2", "center", "setback"}``: the tangent points on each ray, the arc centre and the distance from the corner to each tangent
    point. Raises ``ValueError`` with the reason when the corner is straight or the fillet does not fit inside the shorter leg.
    """
    u1, u2 = unit(sub(p1, corner)), unit(sub(p2, corner))
    cosang = max(-1.0, min(1.0, dot(u1, u2)))
    ang = math.acos(cosang)
    if ang < 1e-6 or abs(ang - math.pi) < 1e-6:
        raise ValueError("the two edges are collinear: nothing to fillet")
    if r <= EPS:
        raise ValueError("fillet radius must be positive")
    setback = r / math.tan(ang / 2.0)
    short = min(dist(corner, p1), dist(corner, p2))
    if setback > short + 1e-9:
        raise ValueError("fillet radius %.6g needs %.6g of each edge but the shorter edge is %.6g" % (r, setback, short))
    t1, t2 = add(corner, mul(u1, setback)), add(corner, mul(u2, setback))
    bis = unit(add(u1, u2))
    center = add(corner, mul(bis, r / math.sin(ang / 2.0)))
    return {"t1": t1, "t2": t2, "center": center, "setback": setback}


def chamfer_corner(corner: Pt, p1: Pt, p2: Pt, d1: float, d2: float | None = None) -> dict:
    d2 = d1 if d2 is None else d2
    if d1 <= EPS or d2 <= EPS:
        raise ValueError("chamfer setback must be positive")
    l1, l2 = dist(corner, p1), dist(corner, p2)
    if d1 > l1 + 1e-9 or d2 > l2 + 1e-9:
        raise ValueError("chamfer setbacks %.6g / %.6g exceed the edge lengths %.6g / %.6g" % (d1, d2, l1, l2))
    u1, u2 = unit(sub(p1, corner)), unit(sub(p2, corner))
    if abs(cross(u1, u2)) < 1e-9:
        raise ValueError("the two edges are collinear: nothing to chamfer")
    return {"t1": add(corner, mul(u1, d1)), "t2": add(corner, mul(u2, d2))}


# ----------------------------------------------------------------------------------------- corners between lines and arcs
def branch(corner: Pt, far: Pt, centre: Pt | None = None, *, corner_is_start: bool = True, sweep: float = 0.0) -> dict:
    """One of the two curves that meet at a corner, seen from the corner: a straight line to ``far``, or an arc (stored counter-clockwise from its start to its
    end) whose start or end is the corner. ``sweep`` is the arc's whole sweep."""
    if centre is None:
        return {"type": "line", "k": corner, "far": far, "len": dist(corner, far)}
    return {"type": "arc", "k": corner, "far": far, "c": centre, "R": dist(centre, corner), "sign": 1.0 if corner_is_start else -1.0, "sweep": sweep}


def branch_tangent(b: dict) -> Pt:
    """Unit direction in which the curve leaves its corner."""
    if b["type"] == "line":
        return unit(sub(b["far"], b["k"]))
    return mul(perp(unit(sub(b["k"], b["c"]))), b["sign"])


def _travelled(b: dict, t: Pt) -> float:
    """How far along the curve (from the corner) the point ``t`` lies: length for a line, angle for an arc; None when it is not on the curve's body."""
    if b["type"] == "line":
        u = unit(sub(b["far"], b["k"]))
        along = dot(sub(t, b["k"]), u)
        return along if -1e-9 <= along <= b["len"] + 1e-9 else float("nan")
    ang = norm_angle(b["sign"] * (angle_of(sub(t, b["c"])) - angle_of(sub(b["k"], b["c"]))))
    return ang if ang <= b["sweep"] + 1e-9 else float("nan")


def _locus(b: dict, r: float) -> list[tuple]:
    """Where the centre of a circle of radius ``r`` tangent to the curve can be: two parallel lines, or two concentric circles."""
    if b["type"] == "line":
        u = unit(sub(b["far"], b["k"]))
        n = perp(u)
        return [("line", add(b["k"], mul(n, r * sg)), u) for sg in (1.0, -1.0)]
    out = [("circle", b["c"], b["R"] + r, "out")]
    if abs(b["R"] - r) > 1e-9:
        out.append(("circle", b["c"], abs(b["R"] - r), "in" if r < b["R"] else "around"))
    return out


def _meet(l1: tuple, l2: tuple) -> list[Pt]:
    if l1[0] == "line" and l2[0] == "line":
        q = line_line(l1[1], add(l1[1], l1[2]), l2[1], add(l2[1], l2[2]), segments=False)
        return [q] if q else []
    if l1[0] == "circle" and l2[0] == "circle":
        return circle_circle(l1[1], l1[2], l2[1], l2[2])
    ln, ci = (l1, l2) if l1[0] == "line" else (l2, l1)
    return line_circle(ln[1], add(ln[1], ln[2]), ci[1], ci[2], segment=False)


def _touch(b: dict, centre: Pt, r: float) -> Pt:
    """The point where the circle (centre, r) touches the curve."""
    if b["type"] == "line":
        u = unit(sub(b["far"], b["k"]))
        return add(b["k"], mul(u, dot(sub(centre, b["k"]), u)))
    d = sub(centre, b["c"])
    if norm(d) < 1e-12:
        raise ValueError("the fillet circle would be concentric with the arc")
    around = r > b["R"] and abs(norm(d) - (r - b["R"])) < 1e-6 * max(1.0, r)       # fillet circle encloses the arc's circle
    return add(b["c"], mul(unit(d), -b["R"] if around else b["R"]))


def fillet_branches(b1: dict, b2: dict, r: float) -> dict:
    """Tangent fillet of radius ``r`` between two curves (line or arc) that meet at a corner. Of every circle of that radius touching both curves on their
    bodies, inside the corner, the one nearest the corner is taken. Returns ``{"t1", "t2", "center"}`` or raises ``ValueError`` with the reason."""
    if r <= EPS:
        raise ValueError("fillet radius must be positive")
    k = b1["k"]
    u1, u2 = branch_tangent(b1), branch_tangent(b2)
    wedge = cross(u1, u2)
    if abs(wedge) < 1e-6:
        raise ValueError("the two edges leave the corner in the same (or the opposite) direction: nothing to fillet")
    best = None
    for l1 in _locus(b1, r):
        for l2 in _locus(b2, r):
            for p in _meet(l1, l2):
                w = sub(p, k)
                if cross(u1, w) * wedge <= 0 or cross(u2, w) * -wedge <= 0:          # not inside the corner
                    continue
                try:
                    t1, t2 = _touch(b1, p, r), _touch(b2, p, r)
                except ValueError:
                    continue
                if abs(dist(p, t1) - r) > 1e-6 * max(1.0, r) or abs(dist(p, t2) - r) > 1e-6 * max(1.0, r):
                    continue
                s1, s2 = _travelled(b1, t1), _travelled(b2, t2)
                if s1 != s1 or s2 != s2 or dist(t1, k) < 1e-9 or dist(t2, k) < 1e-9:    # not on the body of one of the curves
                    continue
                if best is None or norm(w) < best[0]:
                    best = (norm(w), p, t1, t2)
    if best is None:
        raise ValueError("no fillet of radius %.6g fits this corner: it needs more room on both edges, or a smaller radius" % r)
    return {"t1": best[2], "t2": best[3], "center": best[1]}


def _at_chord(b: dict, d: float) -> Pt:
    """The point of the curve whose straight-line distance from the corner is ``d``."""
    if b["type"] == "line":
        if d > b["len"] + 1e-9:
            raise ValueError("the setback %.6g is longer than the edge (%.6g)" % (d, b["len"]))
        return add(b["k"], mul(unit(sub(b["far"], b["k"])), d))
    for q in circle_circle(b["k"], d, b["c"], b["R"]):
        s = _travelled(b, q)
        if s == s and dist(q, b["k"]) > 1e-9:
            return q
    raise ValueError("the setback %.6g does not reach the arc's body" % d)


def chamfer_branches(b1: dict, b2: dict, d1: float, d2: float | None = None) -> dict:
    """Chamfer between two curves: the chamfer line runs between the points at straight-line distance ``d1`` / ``d2`` from the corner."""
    d2 = d1 if d2 is None else d2
    if d1 <= EPS or d2 <= EPS:
        raise ValueError("chamfer setback must be positive")
    if abs(cross(branch_tangent(b1), branch_tangent(b2))) < 1e-6:
        raise ValueError("the two edges leave the corner in the same (or the opposite) direction: nothing to chamfer")
    return {"t1": _at_chord(b1, d1), "t2": _at_chord(b2, d2)}


def offset_line(a: Pt, b: Pt, d: float) -> tuple[Pt, Pt]:
    """Parallel copy ``d`` to the LEFT of a->b (negative d: right)."""
    n = unit(perp(sub(b, a)))
    return (a[0] + n[0] * d, a[1] + n[1] * d), (b[0] + n[0] * d, b[1] + n[1] * d)


# ------------------------------------------------------------------------------------------------------------- splines
def bezier_point(p0: Pt, p1: Pt, p2: Pt, p3: Pt, t: float) -> Pt:
    u = 1 - t
    return (u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0],
            u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1])


def bezier_chain(points: Sequence[Pt], handles: Sequence[tuple[Pt, Pt]], n: int = 24) -> list[Pt]:
    """Piecewise cubic Bezier through ``points`` (3ds Max style): ``handles[i]`` is (out handle of point i, in handle of point i+1)."""
    out: list[Pt] = []
    for i in range(len(points) - 1):
        h_out, h_in = handles[i]
        for k in range(n + (1 if i == len(points) - 2 else 0)):
            out.append(bezier_point(points[i], h_out, h_in, points[i + 1], k / n))
    return out


def _bspline_basis(i: int, k: int, t: float, knots: Sequence[float]) -> float:
    if k == 0:
        last = knots[i + 1] == knots[-1] and t == knots[-1]
        return 1.0 if (knots[i] <= t < knots[i + 1]) or (last and knots[i] < knots[i + 1]) else 0.0
    a = b = 0.0
    d1 = knots[i + k] - knots[i]
    d2 = knots[i + k + 1] - knots[i + 1]
    if d1 > 0:
        a = (t - knots[i]) / d1 * _bspline_basis(i, k - 1, t, knots)
    if d2 > 0:
        b = (knots[i + k + 1] - t) / d2 * _bspline_basis(i + 1, k - 1, t, knots)
    return a + b


def clamped_knots(n_ctrl: int, degree: int) -> list[float]:
    return [0.0] * (degree + 1) + [float(i) for i in range(1, n_ctrl - degree)] + [float(max(1, n_ctrl - degree))] * (degree + 1)


def bspline_curve(ctrl: Sequence[Pt], degree: int = 3, n: int = 80) -> list[Pt]:
    """Clamped, non-rational B-spline (the 'math' workflow): passes through the first and last control point only."""
    m = len(ctrl)
    degree = min(degree, m - 1)
    knots = clamped_knots(m, degree)
    t0, t1 = knots[degree], knots[-degree - 1]
    pts = []
    for s in range(n + 1):
        t = t0 + (t1 - t0) * s / n
        x = y = 0.0
        for i in range(m):
            w = _bspline_basis(i, degree, t, knots)
            x += w * ctrl[i][0]
            y += w * ctrl[i][1]
        pts.append((x, y))
    pts[0], pts[-1] = tuple(ctrl[0]), tuple(ctrl[-1])
    return pts


def hermite_through(points: Sequence[Pt], start_tangent: Pt | None = None, end_tangent: Pt | None = None, n: int = 24) -> list[Pt]:
    """Interpolating cubic Hermite (Catmull-Rom tangents) through fit points: the 'Revit' workflow, where the curve passes through every point."""
    m = len(points)
    if m == 2:
        return [lerp(points[0], points[1], i / n) for i in range(n + 1)]
    tans = []
    for i in range(m):
        if i == 0:
            tans.append(start_tangent if start_tangent else mul(sub(points[1], points[0]), 1.0))
        elif i == m - 1:
            tans.append(end_tangent if end_tangent else mul(sub(points[-1], points[-2]), 1.0))
        else:
            tans.append(mul(sub(points[i + 1], points[i - 1]), 0.5))
    out: list[Pt] = []
    for i in range(m - 1):
        p0, p1, m0, m1 = points[i], points[i + 1], tans[i], tans[i + 1]
        for k in range(n + (1 if i == m - 2 else 0)):
            t = k / n
            h00, h10, h01, h11 = 2*t**3 - 3*t**2 + 1, t**3 - 2*t**2 + t, -2*t**3 + 3*t**2, t**3 - t**2
            out.append((h00*p0[0] + h10*m0[0] + h01*p1[0] + h11*m1[0], h00*p0[1] + h10*m0[1] + h01*p1[1] + h11*m1[1]))
    return out


# ------------------------------------------------------------------------------------- exact curves for export (Bezier form, guaranteed flattening)
def _insert_knot(ctrl: list, knots: list, u: float, p: int) -> None:
    """Boehm knot insertion (in place) into a B-spline of degree ``p``."""
    k = max(i for i in range(len(knots) - 1) if knots[i] <= u < knots[i + 1])
    q = list(ctrl[: k - p + 1])
    for i in range(k - p + 1, k + 1):
        a = (u - knots[i]) / (knots[i + p] - knots[i])
        q.append(((1 - a) * ctrl[i - 1][0] + a * ctrl[i][0], (1 - a) * ctrl[i - 1][1] + a * ctrl[i][1]))
    q.extend(ctrl[k:])
    ctrl[:] = q
    knots.insert(k + 1, u)


def _elevate_to_cubic(seg: list) -> tuple:
    if len(seg) == 4:
        return tuple(seg)
    if len(seg) == 2:
        a, b = seg
        return (a, lerp(a, b, 1 / 3), lerp(a, b, 2 / 3), b)
    a, q, b = seg
    return (a, lerp(a, q, 2 / 3), lerp(b, q, 2 / 3), b)


def spline_beziers(mode: str, pts: Sequence[Pt], *, degree: int = 3, handles: Sequence[tuple[Pt, Pt]] | None = None) -> list[tuple[Pt, Pt, Pt, Pt]] | None:
    """The spline as a chain of cubic Beziers that reproduces it *exactly* (up to rounding), or ``None`` when that is not possible (B-spline degree above 3).
    Same definitions as ``bspline_curve`` / ``bezier_chain`` / ``hermite_through``."""
    pts = [tuple(p) for p in pts]
    m = len(pts)
    if m < 2:
        return None
    if mode == "max":
        return [(pts[i], tuple(handles[i][0]), tuple(handles[i][1]), pts[i + 1]) for i in range(m - 1)]
    if mode == "revit":
        if m == 2:
            return [_elevate_to_cubic([pts[0], pts[1]])]
        tans = [sub(pts[1], pts[0])] + [mul(sub(pts[i + 1], pts[i - 1]), 0.5) for i in range(1, m - 1)] + [sub(pts[-1], pts[-2])]
        return [(pts[i], add(pts[i], mul(tans[i], 1 / 3)), sub(pts[i + 1], mul(tans[i + 1], 1 / 3)), pts[i + 1]) for i in range(m - 1)]
    if mode == "math":
        p = min(int(degree), m - 1)
        if p > 3 or p < 1:
            return None
        ctrl, knots = list(pts), clamped_knots(m, p)
        for u in sorted({k for k in knots if knots[p] < k < knots[-p - 1]}):
            for _ in range(p - 1):
                _insert_knot(ctrl, knots, u, p)
        n_seg = (len(ctrl) - 1) // p
        return [_elevate_to_cubic(ctrl[j * p: j * p + p + 1]) for j in range(n_seg)]
    return None


def cubic_point(seg: tuple, t: float) -> Pt:
    return bezier_point(seg[0], seg[1], seg[2], seg[3], t)


def flatten_cubic(seg: tuple, tol: float, _depth: int = 0) -> list[Pt]:
    """Polyline for one cubic Bezier, start to end, whose distance from the curve never exceeds ``tol`` (the control-polygon bound: a cubic stays within 3/4 of the
    larger control-point distance from its chord)."""
    p0, c1, c2, p3 = seg
    chord = dist(p0, p3)
    if chord > 1e-12:
        d = max(abs(cross(sub(p3, p0), sub(c1, p0))), abs(cross(sub(p3, p0), sub(c2, p0)))) / chord
        along_ok = all(-1e-9 <= dot(sub(c, p0), unit(sub(p3, p0))) <= chord + 1e-9 for c in (c1, c2))     # control points between the ends: no overshoot
    else:
        d = max(dist(p0, c1), dist(p0, c2))
        along_ok = False
    if (0.75 * d <= tol and along_ok) or _depth >= 18 or (chord <= 1e-12 and d <= tol):
        return [p0, p3]
    a = lerp(p0, c1, .5); b = lerp(c1, c2, .5); c = lerp(c2, p3, .5)
    ab = lerp(a, b, .5); bc = lerp(b, c, .5); m = lerp(ab, bc, .5)
    return flatten_cubic((p0, a, ab, m), tol, _depth + 1)[:-1] + flatten_cubic((m, bc, c, p3), tol, _depth + 1)


def flatten_beziers(segs: Sequence[tuple], tol: float) -> list[Pt]:
    out: list[Pt] = []
    for sg in segs:
        pts = flatten_cubic(sg, tol)
        out.extend(pts if not out else pts[1:])
    return out


def ellipse_point(c: Pt, rx: float, ry: float, rot: float, t: float) -> Pt:
    x, y = rx * math.cos(t), ry * math.sin(t)
    return (c[0] + x * math.cos(rot) - y * math.sin(rot), c[1] + x * math.sin(rot) + y * math.cos(rot))


def flatten_ellipse(c: Pt, rx: float, ry: float, rot: float, tol: float) -> list[Pt]:
    """Closed ellipse (first point not repeated) whose chords stay within ``tol`` of it. Between parameter steps of ``d`` the sagitta is at most
    ``rmax^3 d^2 / (8 rmin^2)`` (radius of curvature at most rmax^2/rmin, chord at most rmax d)."""
    rmax, rmin = max(rx, ry), min(rx, ry)
    d = math.sqrt(8.0 * max(tol, 1e-12) * rmin * rmin / (rmax ** 3))
    n = min(max(int(math.ceil(TAU / d)), 16), 20000)
    return [ellipse_point(c, rx, ry, rot, TAU * i / n) for i in range(n)]


def polyline_length(pts: Sequence[Pt]) -> float:
    return sum(dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def curve_length(pts: Sequence[Pt]) -> float:
    """Length of a sampled curve. For splines the sampling error is bounded by the sagitta of the flattening, which is stated with the result."""
    return polyline_length(pts)


# ------------------------------------------------------------------------------------------------------------- polygons
def signed_area(ring: Sequence[Pt]) -> float:
    n = len(ring)
    return 0.5 * sum(ring[i][0] * ring[(i + 1) % n][1] - ring[(i + 1) % n][0] * ring[i][1] for i in range(n))


def point_in_ring(p: Pt, ring: Sequence[Pt]) -> bool:
    inside, n = False, len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > p[1]) != (yj > p[1]) and p[0] < (xj - xi) * (p[1] - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def ring_perimeter(ring: Sequence[Pt]) -> float:
    return sum(dist(ring[i], ring[(i + 1) % len(ring)]) for i in range(len(ring)))


def ring_centroid(ring: Sequence[Pt]) -> Pt:
    a = signed_area(ring)
    if abs(a) < EPS:
        return (sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring))
    cx = cy = 0.0
    n = len(ring)
    for i in range(n):
        x0, y0, x1, y1 = ring[i][0], ring[i][1], ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        k = x0 * y1 - x1 * y0
        cx += (x0 + x1) * k
        cy += (y0 + y1) * k
    return (cx / (6 * a), cy / (6 * a))


def polyline_self_intersections(pts: Sequence[Pt], closed: bool = False) -> list[Pt]:
    """Proper crossings between non-adjacent segments of one polyline (shared end points are not crossings)."""
    n = len(pts)
    segs = [(pts[i], pts[(i + 1) % n]) for i in range(n if closed else n - 1)]
    found = []
    for i in range(len(segs)):
        for j in range(i + 2, len(segs)):
            if closed and i == 0 and j == len(segs) - 1:
                continue
            x = line_line(segs[i][0], segs[i][1], segs[j][0], segs[j][1])
            if x is not None and not any(dist(x, q) < 1e-9 for q in (segs[i][0], segs[i][1], segs[j][0], segs[j][1])):
                found.append(x)
    return found
