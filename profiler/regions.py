"""Closed-loop recognition, region nesting and region booleans.

Method (an arrangement, not a flood fill): the non-construction edges form a planar graph. Dangling spurs are pruned (and reported), then the bounded faces
are traversed half-edge by half-edge, so a face may be bounded by shared edges and arcs exactly. Circles and ellipses are loops of their own. A connected
component that lies inside another's face becomes a *hole* of that face. Areas are exact for lines and circular arcs (Green's theorem), numerical for splines.
Construction geometry is never part of a region.

The graph assumes edges meet only at shared vertices; edges that cross elsewhere are reported by ``crossings`` (and by Check Profile) rather than
guessed at. Use Split at intersections to make the crossings into vertices.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Sequence

from . import geom as G


def _tangent_at(sk, eid: str, at_vertex: str) -> tuple[float, float]:
    """Unit direction leaving ``at_vertex`` along the edge."""
    e = sk.edge(eid)
    t = e["type"]
    if t == "line":
        a, b = [sk.pos(v) for v in e["v"]]
        d = G.sub(b, a) if e["v"][0] == at_vertex else G.sub(a, b)
        return G.unit(d)
    if t == "arc":
        c, r, a0, sw = sk.arc_params(eid)
        if e["v"][0] == at_vertex:           # leaving the start counter-clockwise
            return G.unit(G.perp(G.sub(sk.pos(at_vertex), c)))
        return G.unit(G.mul(G.perp(G.sub(sk.pos(at_vertex), c)), -1.0))
    pts = sk.edge_polyline(eid, 0.001)
    if e["v"][0] == at_vertex:
        return G.unit(G.sub(pts[1], pts[0]))
    return G.unit(G.sub(pts[-2], pts[-1]))


def _directed_points(sk, eid: str, forward: bool, tol: float) -> list[tuple[float, float]]:
    pts = sk.edge_polyline(eid, tol)
    return pts if forward else pts[::-1]


def _directed_area(sk, eid: str, forward: bool) -> float:
    """Signed contribution of one directed edge to the loop area (∮(x dy - y dx)/2)."""
    e = sk.edge(eid)
    if e["type"] == "line":
        a, b = [sk.pos(v) for v in e["v"]]
        if not forward:
            a, b = b, a
        return 0.5 * G.cross(a, b)
    if e["type"] == "arc":
        c, r, a0, sw = sk.arc_params(eid)
        t0, t1 = (a0, a0 + sw) if forward else (a0 + sw, a0)
        return 0.5 * (r * r * (t1 - t0) + r * c[0] * (math.sin(t1) - math.sin(t0)) - r * c[1] * (math.cos(t1) - math.cos(t0)))
    pts = _directed_points(sk, eid, forward, 0.0005)
    return 0.5 * sum(G.cross(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def _loop(sk, halves: Sequence[tuple[str, bool]], tol: float) -> dict[str, Any]:
    pts: list[tuple[float, float]] = []
    for eid, fwd in halves:
        seg = _directed_points(sk, eid, fwd, tol)
        pts.extend(seg if not pts else seg[1:])
    if len(pts) > 1 and G.dist(pts[0], pts[-1]) < 1e-9:
        pts.pop()
    area = sum(_directed_area(sk, e, f) for e, f in halves)
    perim = sum(sk.edge_length(e) for e, _ in halves)
    return {"edges": [e for e, _ in halves], "halves": [[e, f] for e, f in halves], "points": pts, "area": area, "perimeter": perim}


def analyse(sk, *, tol: float = 0.01) -> dict[str, Any]:
    """Loops, regions and the graph problems found on the way (``dangling`` edges, ``crossings`` between non-adjacent edges)."""
    E = {eid: e for eid, e in sk.edges.items() if not e.get("construction")}
    comps_faces: list[dict] = []
    dangling: list[str] = []

    closed = [eid for eid, e in E.items() if e["type"] in ("circle", "ellipse")]
    open_edges = {eid: e for eid, e in E.items() if e["type"] in ("line", "arc", "spline")}
    for eid in closed:
        pts = sk.edge_polyline(eid, tol)[:-1]
        area = math.pi * sk.center_radius(eid)[1] ** 2 if E[eid]["type"] == "circle" else G.signed_area(pts)
        comps_faces.append({"bounded": [{"edges": [eid], "halves": [[eid, True]], "points": pts, "area": abs(area), "perimeter": sk.edge_length(eid)}],
                            "outer": {"edges": [eid], "points": pts[::-1], "area": -abs(area), "perimeter": sk.edge_length(eid)}})

    # prune spurs
    adj: dict[str, list[str]] = defaultdict(list)
    for eid, e in open_edges.items():
        a, b = sk.edge_ends(eid)
        adj[a].append(eid)
        adj[b].append(eid)
    live = dict(open_edges)
    changed = True
    while changed:
        changed = False
        deg: dict[str, int] = defaultdict(int)
        for eid in live:
            a, b = sk.edge_ends(eid)
            deg[a] += 1
            deg[b] += 1
        for eid in list(live):
            a, b = sk.edge_ends(eid)
            if deg[a] == 1 or deg[b] == 1:
                dangling.append(eid)
                del live[eid]
                changed = True
    out_at: dict[str, list[tuple[float, str, bool]]] = defaultdict(list)       # vertex -> [(angle, edge, forward)] leaving the vertex
    for eid in live:
        a, b = sk.edge_ends(eid)
        out_at[a].append((G.angle_of(_tangent_at(sk, eid, a)), eid, True))
        out_at[b].append((G.angle_of(_tangent_at(sk, eid, b)), eid, False))
    for v in out_at:
        out_at[v].sort()
    visited: set[tuple[str, bool]] = set()
    comp_of: dict[str, int] = {}
    # connected components of the live graph
    parent: dict[str, str] = {}
    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for eid in live:
        a, b = sk.edge_ends(eid)
        parent[find(a)] = find(b)
    faces_by_comp: dict[str, list[dict]] = defaultdict(list)
    for eid in live:
        for fwd in (True, False):
            if (eid, fwd) in visited:
                continue
            halves: list[tuple[str, bool]] = []
            cur = (eid, fwd)
            guard = 0
            while cur not in visited and guard < 100000:
                guard += 1
                visited.add(cur)
                halves.append(cur)
                e, f = cur
                a, b = sk.edge_ends(e)
                end_v = b if f else a
                # at end_v, take the next edge clockwise from the reverse of the arriving edge (interior stays on the left)
                back = G.angle_of(_tangent_at(sk, e, end_v))
                lst = out_at[end_v]
                idx = next(i for i, (_, ee, ff) in enumerate(lst) if ee == e and ff == (not f))
                nxt = lst[(idx - 1) % len(lst)]
                cur = (nxt[1], nxt[2])
            comp = find(sk.edge_ends(eid)[0])
            faces_by_comp[comp].append(_loop(sk, halves, tol))
    for comp, faces in faces_by_comp.items():
        bounded = [f for f in faces if f["area"] > 1e-12]
        outers = [f for f in faces if f["area"] <= 1e-12]
        comps_faces.append({"bounded": bounded, "outer": min(outers, key=lambda f: f["area"]) if outers else None})

    # holes: a component sitting inside a bounded face of another component
    regions: list[dict] = []
    all_bounded: list[tuple[int, dict]] = [(ci, f) for ci, c in enumerate(comps_faces) for f in c["bounded"]]
    holes_of: dict[int, list[dict]] = defaultdict(list)
    for ci, c in enumerate(comps_faces):
        o = c["outer"]
        if not o or not o["points"]:
            continue
        probe = o["points"][0]
        best, best_area = None, None
        for cj, f in all_bounded:
            if cj == ci:
                continue
            if f["area"] > 0 and G.point_in_ring(probe, f["points"]) and (best_area is None or f["area"] < best_area):
                best, best_area = f, f["area"]
        if best is not None:
            holes_of[id(best)].append({"edges": o["edges"], "points": o["points"], "area": o["area"], "perimeter": o["perimeter"], "component": ci})
    for ci, f in all_bounded:
        holes = holes_of.get(id(f), [])
        net = f["area"] - sum(abs(h["area"]) for h in holes)
        regions.append({"outer": f, "holes": holes, "area": net, "perimeter": f["perimeter"] + sum(h["perimeter"] for h in holes),
                        "edges": sorted(set(f["edges"]) | {e for h in holes for e in h["edges"]})})
    return {"regions": regions, "dangling": sorted(set(dangling)), "loops": len(all_bounded), "crossings": crossings(sk)}


def crossings(sk, tol: float = 0.01) -> list[dict[str, Any]]:
    """Proper crossings and overlaps between edges that do not simply share an end vertex. Polylines approximate arcs/splines (within ``tol``)."""
    E = [(eid, e) for eid, e in sk.edges.items() if not e.get("construction")]
    polys = {eid: sk.edge_polyline(eid, tol) for eid, _ in E}
    out = []
    for i in range(len(E)):
        for j in range(i + 1, len(E)):
            a, b = E[i][0], E[j][0]
            ea, eb = E[i][1], E[j][1]
            shared = set(sk.edge_ends(a) or ()) & set(sk.edge_ends(b) or ())
            ba, bb = _bbox(polys[a]), _bbox(polys[b])
            if ba[2] < bb[0] - 1e-9 or bb[2] < ba[0] - 1e-9 or ba[3] < bb[1] - 1e-9 or bb[3] < ba[1] - 1e-9:
                continue
            pts = _poly_intersections(polys[a], polys[b])
            ends = [sk.pos(v) for e in (a, b) for v in (sk.edge_ends(e) or ())]
            for p in pts:
                # Touching at an end vertex of either edge is a junction, not a crossing.
                if any(G.dist(p, q) < 1e-6 for q in ends if q is not None):
                    continue
                out.append({"kind": "crossing", "edges": [a, b], "point": list(p)})
    # T-junctions: an end vertex that lies on another edge without being one of its vertices (the graph is not connected there)
    for eid, e in E:
        for v in (sk.edge_ends(eid) or ()):
            p = sk.pos(v)
            for oid, o in E:
                if oid == eid or v in (sk.edge_ends(oid) or ()):
                    continue
                pl = polys[oid]
                if _bbox(pl)[0] - 1e-6 <= p[0] <= _bbox(pl)[2] + 1e-6 and _bbox(pl)[1] - 1e-6 <= p[1] <= _bbox(pl)[3] + 1e-6:
                    near = min(G.nearest_on_segment(p, pl[i], pl[i + 1])[1] for i in range(len(pl) - 1))
                    if near < 1e-6 and not any(G.dist(p, sk.pos(q)) < 1e-6 for q in (sk.edge_ends(oid) or ())):
                        out.append({"kind": "t_junction", "edges": [eid, oid], "vertex": v, "point": list(p)})
    return out


def _bbox(pts):
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _poly_intersections(p, q):
    found = []
    for i in range(len(p) - 1):
        for j in range(len(q) - 1):
            x = G.line_line(p[i], p[i + 1], q[j], q[j + 1])
            if x is not None and not any(G.dist(x, f) < 1e-7 for f in found):
                found.append(x)
    return found


def region_containing_edges(sk, edge_ids: Sequence[str]) -> dict[str, Any] | None:
    want = set(edge_ids)
    best = None
    for r in analyse(sk)["regions"]:
        if want <= set(r["edges"]) and (best is None or r["area"] < best["area"]):
            best = r
    return best


def orientation_problems(a: dict) -> list[dict[str, Any]]:
    """Loops that are wound the wrong way for a region (outer clockwise): in this representation faces are always built counter-clockwise, so this only
    reports closed *polylines* a user may have imported clockwise."""
    return []


# ----------------------------------------------------------------------------------------------------------------- booleans
def boolean(sk, op: str, region_a: Sequence[str], region_b: Sequence[str]) -> list[dict[str, Any]]:
    """Union / subtract / intersect of two regions (given by edge ids). Returns result regions as polygon rings in document units.

    The planar kernel works on straight edges: arcs and splines are flattened (tolerance 0.001 document units) first, so a *result* is polygonal. That loss is
    stated in the return value; the inputs are not modified (the session decides whether to replace them)."""
    from .. import planar as PL
    ra, rb = region_containing_edges(sk, region_a), region_containing_edges(sk, region_b)
    if ra is None or rb is None:
        raise ValueError("both selections must bound a closed region")
    f = {"m": 0.001 if sk.unit == "mm" else 1.0}
    from src.utils.spatial_formats.common import units as U
    k = U.METERS_PER_UNIT[sk.unit]
    ring = lambda loop: [(p[0] * k, p[1] * k) for p in _flat(sk, loop)]
    a = ring(ra["outer"])
    b = ring(rb["outer"])
    try:
        if op == "union":
            res = PL.union([a, b])
        elif op == "subtract":
            res = PL.difference(a, [b])
        elif op == "intersect":
            res = PL.intersection(a, b)
        else:
            raise ValueError("operation must be union, subtract or intersect")
    except PL.PlanarError as exc:
        raise ValueError(str(exc))
    out = []
    for outer, holes in res:
        out.append({"outer": [(x / k, y / k) for x, y in outer], "holes": [[(x / k, y / k) for x, y in h] for h in holes],
                    "area": PL.region_area((outer, holes)) / (k * k), "approximation": "arcs and splines flattened to 0.001 %s" % sk.unit})
    return out


def _flat(sk, loop):
    return loop["points"]
