"""Object snapping and numeric point entry for the Profiler viewport.

``snap`` is a pure query: given a cursor position (document units), a pick radius and the enabled modes it returns the best candidate, and always the full
candidate list so the viewer can show which mode won. Modes: endpoint, midpoint, center, intersection, tangent, perpendicular, nearest, quadrant, grid.
"""
from __future__ import annotations

import math
import re
from typing import Any, Iterable, Sequence

from . import geom as G
from . import topo
from .errors import ProfilerError

MODES = ("endpoint", "midpoint", "center", "intersection", "tangent", "perpendicular", "nearest", "quadrant", "grid")
PRIORITY = {m: i for i, m in enumerate(("endpoint", "intersection", "midpoint", "center", "quadrant", "tangent", "perpendicular", "nearest", "grid"))}
MAX_EDGES_FOR_INTERSECTIONS = 400


def _visible_edges(sk, include_construction: bool = True) -> list[str]:
    layers = {l["name"]: l for l in sk.d["layers"]}
    out = []
    for eid, e in sk.edges.items():
        if not layers.get(e["layer"], {}).get("visible", True):
            continue
        if e.get("construction") and not include_construction:
            continue
        out.append(eid)
    return out


def _near(eid: str, sk, p, radius: float) -> bool:
    b = sk.bounds([eid])
    return b is not None and b[0] - radius <= p[0] <= b[2] + radius and b[1] - radius <= p[1] <= b[3] + radius


def snap(sk, cursor: Sequence[float], radius: float, modes: Iterable[str] | None = None, *, from_point: Sequence[float] | None = None,
         exclude: Iterable[str] = (), grid: float | None = None) -> dict[str, Any]:
    p = (float(cursor[0]), float(cursor[1]))
    if radius <= 0:
        raise ProfilerError("the snap radius must be positive")
    want = list(modes) if modes is not None else list(MODES)
    bad = [m for m in want if m not in MODES]
    if bad:
        raise ProfilerError("unknown snap mode %s (use %s)" % (", ".join(bad), ", ".join(MODES)))
    skip = set(exclude)
    ids = [e for e in _visible_edges(sk) if e not in skip and _near(e, sk, p, radius * 1.5 + 1e-9)]
    cands: list[dict[str, Any]] = []

    def add(mode, pt, ref=None, extra=None):
        d = G.dist(p, pt)
        if d <= radius:
            c = {"mode": mode, "point": [pt[0], pt[1]], "distance": d, "ref": ref}
            if extra:
                c.update(extra)
            cands.append(c)

    fp = (float(from_point[0]), float(from_point[1])) if from_point is not None else None
    for eid in ids:
        e = sk.edge(eid)
        t = e["type"]
        if "endpoint" in want and t in ("line", "arc", "spline"):
            for v in sk.edge_ends(eid) or ():
                add("endpoint", sk.pos(v), v, {"edge": eid})
        if "midpoint" in want and t in ("line", "arc", "spline"):
            if t == "line":
                a, b = [sk.pos(v) for v in e["v"]]
                add("midpoint", G.mid(a, b), eid)
            elif t == "arc":
                c, r, a0, sw = sk.arc_params(eid)
                add("midpoint", G.polar(c, r, a0 + sw / 2), eid)
            else:
                pl = sk.edge_polyline(eid, 0.01)
                total, acc = G.polyline_length(pl), 0.0
                for i in range(len(pl) - 1):
                    seg = G.dist(pl[i], pl[i + 1])
                    if acc + seg >= total / 2:
                        add("midpoint", G.lerp(pl[i], pl[i + 1], (total / 2 - acc) / (seg or 1)), eid)
                        break
                    acc += seg
        if "center" in want and t in ("circle", "arc", "ellipse"):
            add("center", sk.pos(e["c"]), e["c"], {"edge": eid})
        if "quadrant" in want and t in ("circle", "arc", "ellipse"):
            if t == "ellipse":
                c = sk.pos(e["c"])
                rx, ry, rot = sk.d["scalars"][e["rx"]], sk.d["scalars"][e["ry"]], math.radians(e.get("rot", 0.0))
                for k, (ex, ey) in enumerate(((rx, 0), (0, ry), (-rx, 0), (0, -ry))):
                    add("quadrant", (c[0] + ex * math.cos(rot) - ey * math.sin(rot), c[1] + ex * math.sin(rot) + ey * math.cos(rot)), eid, {"quadrant": k})
            else:
                c, r = sk.center_radius(eid)
                for k in range(4):
                    q = G.polar(c, r, k * math.pi / 2)
                    if t == "circle" or G.point_on_circle_arc(q, c, sk.arc_params(eid)[2], sk.arc_params(eid)[3]):
                        add("quadrant", q, eid, {"quadrant": k})
        if "nearest" in want:
            q = _nearest_on_edge(sk, eid, p)
            if q is not None:
                add("nearest", q, eid)
        if fp is not None and "perpendicular" in want:
            for q in _perpendicular_feet(sk, eid, fp):
                add("perpendicular", q, eid)
        if fp is not None and "tangent" in want and t in ("circle", "arc"):
            for q in _tangent_points(sk, eid, fp):
                add("tangent", q, eid)
    if "intersection" in want and 2 <= len(ids) <= MAX_EDGES_FOR_INTERSECTIONS:
        geos = {i: topo._geom(sk, i) for i in ids if sk.edge(i)["type"] in ("line", "arc", "circle", "spline")}
        keys = list(geos)
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                for q in topo.pair_points(geos[keys[i]], geos[keys[j]]):
                    add("intersection", q, None, {"edges": [keys[i], keys[j]]})
    if "grid" in want and sk.d["view"].get("snap_grid", True):
        g = float(grid) if grid else float(sk.d["view"]["grid"])          # ``grid``: the spacing the screen is showing (automatic grids follow the zoom)
        if g > 0:
            origin = sk.d["view"]["origin"]
            q = (origin[0] + round((p[0] - origin[0]) / g) * g, origin[1] + round((p[1] - origin[1]) / g) * g)
            add("grid", q, None)
    for c in cands:
        c["score"] = c["distance"] + PRIORITY[c["mode"]] * 0.02 * radius
    cands.sort(key=lambda c: c["score"])
    best = cands[0] if cands else None
    return {"snap": best, "candidates": cands[:12], "cursor": [p[0], p[1]]}


def _nearest_on_edge(sk, eid: str, p) -> tuple[float, float] | None:
    e = sk.edge(eid)
    t = e["type"]
    if t == "circle":
        c, r = sk.center_radius(eid)
        d = G.sub(p, c)
        n = G.norm(d)
        return G.polar(c, r, G.angle_of(d)) if n > G.EPS else None
    if t == "arc":
        c, r, a0, sw = sk.arc_params(eid)
        d = G.sub(p, c)
        if G.norm(d) > G.EPS:
            q = G.polar(c, r, G.angle_of(d))
            if G.point_on_circle_arc(q, c, a0, sw):
                return q
        s, e2 = [sk.pos(v) for v in sk.edge_ends(eid)]
        return s if G.dist(p, s) < G.dist(p, e2) else e2
    pl = sk.edge_polyline(eid, 0.005)
    best = min((G.nearest_on_segment(p, pl[i], pl[i + 1]) for i in range(len(pl) - 1)), key=lambda t_: t_[1])
    return best[0]


def _perpendicular_feet(sk, eid: str, fp) -> list[tuple[float, float]]:
    e = sk.edge(eid)
    t = e["type"]
    if t == "line":
        a, b = [sk.pos(v) for v in e["v"]]
        s = G.seg_param(fp, a, b)
        return [G.lerp(a, b, s)] if 0.0 <= s <= 1.0 else []
    if t in ("circle", "arc"):
        c, r = sk.center_radius(eid)
        d = G.sub(fp, c)
        if G.norm(d) < G.EPS:
            return []
        out = []
        for ang in (G.angle_of(d), G.angle_of(d) + math.pi):          # the radial line through ``fp`` meets the curve at two points, both perpendicular
            q = G.polar(c, r, ang)
            if t == "circle" or G.point_on_circle_arc(q, c, sk.arc_params(eid)[2], sk.arc_params(eid)[3]):
                out.append(q)
        return out
    return []


def _tangent_points(sk, eid: str, fp) -> list[tuple[float, float]]:
    c, r = sk.center_radius(eid)
    d = G.dist(fp, c)
    if d <= r + 1e-9:
        return []
    base = G.angle_of(G.sub(fp, c))
    off = math.acos(r / d)
    out = []
    for ang in (base + off, base - off):
        q = G.polar(c, r, ang)
        if sk.edge(eid)["type"] == "circle" or G.point_on_circle_arc(q, c, sk.arc_params(eid)[2], sk.arc_params(eid)[3]):
            out.append(q)
    return out


# ------------------------------------------------------------------------------------------------------ numeric entry
_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_ABS = re.compile(r"^\s*(%s)\s*[,;\s]\s*(%s)\s*$" % (_NUM, _NUM))
_POL = re.compile(r"^\s*(%s)\s*<\s*(%s)\s*$" % (_NUM, _NUM))


def parse_point(text: str, last: Sequence[float] | None = None) -> dict[str, Any]:
    """Numeric entry: ``50,30`` absolute; ``@20,10`` relative to ``last``; ``50<30`` polar from the origin; ``@50<30`` polar from ``last`` (degrees, CCW from +X).
    A bare distance (``40``) is a *distance along the current direction* handled by the tool, so it is reported as such rather than guessed here."""
    if not isinstance(text, str) or not text.strip():
        raise ProfilerError("type a coordinate such as 50,30 or @20,10 or @50<30")
    s = text.strip()
    rel = s.startswith("@")
    if rel:
        s = s[1:]
        if last is None:
            raise ProfilerError("a relative entry (@) needs a previous point")
    base = (float(last[0]), float(last[1])) if rel else (0.0, 0.0)
    m = _ABS.match(s)
    if m:
        return {"point": [base[0] + float(m.group(1)), base[1] + float(m.group(2))], "kind": "relative" if rel else "absolute"}
    m = _POL.match(s)
    if m:
        d, a = float(m.group(1)), math.radians(float(m.group(2)))
        return {"point": [base[0] + d * math.cos(a), base[1] + d * math.sin(a)], "kind": "polar-relative" if rel else "polar"}
    if re.match(r"^\s*%s\s*$" % _NUM, s) and not rel:
        return {"distance": float(s), "kind": "distance"}
    raise ProfilerError("cannot read %r as a point: use x,y or @dx,dy or [@]distance<angle" % text)
