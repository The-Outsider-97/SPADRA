"""Topology-changing edits: trim, extend, split, cut, offset, fillet, chamfer, join, weld/merge, connect, attach and region booleans.

The topology contract (authoritative PDF p.2): vertex and edge identities survive ordinary edits. Where an edit must create or replace topology, the old->new
mapping is deterministic and *returned*: the first piece of a split keeps the original edge id, a circle that is trimmed becomes an arc under the same id, a
merged vertex keeps the id of the first vertex. Constraints and dimensions that referenced an edge whose extent changed and that cannot be carried over
unambiguously are flagged ``unresolved`` with provenance (never silently dropped, never silently re-solved), and show up in Check Profile.
"""
from __future__ import annotations

import math
from typing import Any, Sequence

from src.utils.spatial_formats.common.ids import new_id

from . import geom as G
from . import ops
from .errors import ProfilerConflict, ProfilerError, ProfilerNotFound

KEEP_ON_EDGE_EDIT = {"horizontal", "vertical", "parallel", "perpendicular", "collinear", "point_on", "tangent", "tangent_at", "concentric", "radius", "diameter", "symmetric", "midpoint"}
EXTENT_DIMS = {"arc_length", "chord", "spline_length"}


# ------------------------------------------------------------------------------------------------------ geometry access
def _geom(sk, eid: str):
    e = sk.edge(eid)
    t = e["type"]
    if t == "line":
        return ("line", sk.pos(e["v"][0]), sk.pos(e["v"][1]))
    if t == "circle":
        c, r = sk.center_radius(eid)
        return ("circle", c, r)
    if t == "arc":
        c, r, a0, sw = sk.arc_params(eid)
        return ("arc", c, r, a0, sw)
    return ("poly", sk.edge_polyline(eid, 0.001))


def _arc_filter(pts, g):
    if g[0] != "arc":
        return pts
    return [p for p in pts if G.point_on_circle_arc(p, g[1], g[3], g[4])]


def pair_points(ga, gb) -> list[tuple[float, float]]:
    if ga[0] == "poly" or gb[0] == "poly":
        pa = ga[1] if ga[0] == "poly" else _flat(ga)
        pb = gb[1] if gb[0] == "poly" else _flat(gb)
        out = []
        for i in range(len(pa) - 1):
            for j in range(len(pb) - 1):
                x = G.line_line(pa[i], pa[i + 1], pb[j], pb[j + 1])
                if x is not None and not any(G.dist(x, q) < 1e-7 for q in out):
                    out.append(x)
        return out
    if ga[0] == "line" and gb[0] == "line":
        x = G.line_line(ga[1], ga[2], gb[1], gb[2])
        return [x] if x else []
    if ga[0] == "line" or gb[0] == "line":
        l, c = (ga, gb) if ga[0] == "line" else (gb, ga)
        return _arc_filter(G.line_circle(l[1], l[2], c[1], c[2]), c)
    return _arc_filter(_arc_filter(G.circle_circle(ga[1], ga[2], gb[1], gb[2]), ga), gb)


def _flat(g):
    if g[0] == "line":
        return [g[1], g[2]]
    if g[0] == "circle":
        return G.flatten_arc(g[1], g[2], 0.0, G.TAU, True, 0.001)
    return G.flatten_arc(g[1], g[2], g[3], g[4], True, 0.001)


def _param(sk, eid: str, p) -> float:
    g = _geom(sk, eid)
    if g[0] == "line":
        return G.seg_param(p, g[1], g[2])
    if g[0] == "circle":
        return G.norm_angle(G.angle_of(G.sub(p, g[1]))) / G.TAU
    if g[0] == "arc":
        return G.norm_angle(G.angle_of(G.sub(p, g[1])) - g[3]) / g[4]
    pts = g[1]
    total, acc = G.polyline_length(pts), 0.0
    best = (1e18, 0.0)
    for i in range(len(pts) - 1):
        q, d = G.nearest_on_segment(p, pts[i], pts[i + 1])
        if d < best[0]:
            best = (d, (acc + G.dist(pts[i], q)) / total)
        acc += G.dist(pts[i], pts[i + 1])
    return best[1]


def _point_at(sk, eid: str, t: float) -> tuple[float, float]:
    g = _geom(sk, eid)
    if g[0] == "line":
        return G.lerp(g[1], g[2], t)
    if g[0] == "circle":
        return G.polar(g[1], g[2], G.TAU * t)
    if g[0] == "arc":
        return G.polar(g[1], g[2], g[3] + g[4] * t)
    raise ProfilerError("not supported on this curve type")


def intersections(sk, eid: str, others: Sequence[str] | None = None) -> list[dict[str, Any]]:
    ga = _geom(sk, eid)
    ids = [o for o, e in sk.edges.items() if o != eid and not e.get("construction")] if others is None else [o for o in others if o != eid]
    out = []
    for o in ids:
        for p in pair_points(ga, _geom(sk, o)):
            out.append({"t": _param(sk, eid, p), "point": p, "other": o})
    out.sort(key=lambda d: d["t"])
    return out


# ------------------------------------------------------------------------------------------------------ reassignment
def _reassign(sk, old: str, new_ids: Sequence[str], *, extent_changed: bool) -> list[str]:
    flagged = []
    for c in sk.d["constraints"]:
        if old in c["refs"] and c.get("state") != "unresolved":
            if c["kind"] in KEEP_ON_EDGE_EDIT or not extent_changed:
                if c["kind"] in ("horizontal", "vertical", "parallel", "perpendicular", "collinear") and new_ids:
                    for n in new_ids:                                     # every piece keeps the orientation relationship
                        nc = {k: (list(v) if isinstance(v, list) else v) for k, v in c.items()}
                        nc["id"], nc["refs"], nc["origin"] = new_id(), [n if r == old else r for r in c["refs"]], "auto"
                        sk.d["constraints"].append(nc)
                continue
            c["state"] = "unresolved"
            c["provenance"] = {"was": old, "now": list(new_ids), "reason": "the edge's extent changed"}
            flagged.append(c["id"])
    for did, d in sk.d["dimensions"].items():
        if old in d["refs"] or old in d.get("edges", []) or d.get("edge") == old:
            if d["type"] in EXTENT_DIMS and extent_changed:
                d["state"] = "unresolved"
                d["provenance"] = {"was": old, "now": list(new_ids)}
                flagged.append(did)
    return flagged


def _drop_orphans(sk) -> None:
    used = ops.used_vertices(sk)
    protected = {c["target"] for c in sk.d["comments"].values() if "target" in c}
    for v in list(sk.vertices):
        if v not in used and v not in protected:
            del sk.d["vertices"][v]
    from . import dims
    dims.refresh_unresolved(sk)
    live = set(sk.vertices) | set(sk.edges) | set(sk.d["dimensions"])
    for c in sk.d["constraints"]:
        if c.get("state") != "unresolved" and any(r not in live for r in c["refs"]):
            c["state"] = "unresolved"
            c["provenance"] = {"missing": [r for r in c["refs"] if r not in live]}


def _new_edge_like(sk, eid: str, **fields) -> str:
    e = sk.edge(eid)
    n = {"type": e["type"], "construction": e.get("construction", False), "layer": e["layer"], "color": e.get("color"), "locked": False}
    n.update(fields)
    nid = new_id()
    sk.d["edges"][nid] = n
    return nid


def _vertex_on(sk, p, other_edge: str | None) -> str:
    v = sk.add_vertex(p, reuse=False)
    if other_edge:
        sk.add_constraint("point_on", [v, other_edge], origin="auto")
    return v


# ------------------------------------------------------------------------------------------------------------- split
def split(sk, eid: str, point: Sequence[float], point2: Sequence[float] | None = None) -> dict[str, Any]:
    """Split a line or arc at ``point`` (circle: at two points) into two edges. The first piece keeps the original id; the pieces stay tied by a collinear
    constraint (lines) or a shared centre (arcs), so the split is invisible geometrically and the original constraints/dimensions stay valid."""
    e = sk.edge(eid)
    t = e["type"]
    p = (float(point[0]), float(point[1]))
    if t == "line":
        a, b = [sk.pos(v) for v in e["v"]]
        q, d = G.nearest_on_segment(p, a, b)
        if d > max(sk.tol * 100, 1e-6) and d > 1e-3 * max(1.0, G.dist(a, b)):
            pass
        s = G.seg_param(q, a, b)
        if not 1e-9 < s < 1 - 1e-9:
            raise ProfilerError("the split point is at an end of the edge: nothing to split")
        m = sk.add_vertex(q, reuse=False)
        old_end = e["v"][1]
        e["v"] = [e["v"][0], m]
        e2 = _new_edge_like(sk, eid, v=[m, old_end])
        sk.add_constraint("collinear", [eid, e2], origin="auto")
        flagged = _reassign(sk, eid, [e2], extent_changed=False)
        return {"mapping": {eid: [eid, e2]}, "vertex": m, "unresolved": flagged}
    if t == "arc":
        c, r, a0, sw = sk.arc_params(eid)
        ang = G.norm_angle(G.angle_of(G.sub(p, c)) - a0)
        if not 1e-9 < ang < sw - 1e-9:
            raise ProfilerError("the split point is not on the arc between its ends")
        m = sk.add_vertex(G.polar(c, r, a0 + ang), reuse=False)
        old_end = e["v"][1]
        e["v"] = [e["v"][0], m]
        e2 = _new_edge_like(sk, eid, v=[m, old_end], c=e["c"])
        return {"mapping": {eid: [eid, e2]}, "vertex": m, "unresolved": _reassign(sk, eid, [e2], extent_changed=True)}
    if t == "circle":
        if point2 is None:
            raise ProfilerError("a circle is split at two points (it becomes two arcs)")
        c, r = sk.center_radius(eid)
        p2 = (float(point2[0]), float(point2[1]))
        a1 = G.angle_of(G.sub(p, c)); a2 = G.angle_of(G.sub(p2, c))
        if abs(G.norm_angle(a1 - a2)) < 1e-9:
            raise ProfilerError("the two split points coincide")
        v1, v2 = sk.add_vertex(G.polar(c, r, a1), reuse=False), sk.add_vertex(G.polar(c, r, a2), reuse=False)
        cen = e["c"]
        scalar = e.pop("r")
        e["type"], e["v"] = "arc", [v1, v2]
        e2 = _new_edge_like(sk, eid, v=[v2, v1], c=cen)
        sk.d["scalars"].pop(scalar, None)
        return {"mapping": {eid: [eid, e2]}, "vertices": [v1, v2], "unresolved": _reassign(sk, eid, [e2], extent_changed=True)}
    raise ProfilerError("splitting a %s is not supported (split lines, arcs and circles)" % t)


def split_at_intersections(sk, eids: Sequence[str] | None = None) -> dict[str, Any]:
    """Turn every crossing and T-junction among the given (default: all) edges into a shared vertex by splitting. Never moves geometry."""
    from . import regions
    done = {"splits": 0, "mapping": {}}
    for _ in range(64):                                  # each pass removes at least one crossing
        hits = regions.crossings(sk)
        if eids is not None:
            hits = [h for h in hits if set(h["edges"]) & set(eids)]
        if not hits:
            break
        h = hits[0]
        target = h["edges"][1] if h["kind"] == "t_junction" else h["edges"][0]
        for tid in ([target] if h["kind"] == "t_junction" else list(h["edges"])):
            if tid in sk.edges and sk.edge(tid)["type"] in ("line", "arc"):
                r = split(sk, tid, h["point"])
                done["splits"] += 1
                done["mapping"].update(r["mapping"])
                if h["kind"] == "t_junction":
                    sk.add_constraint("coincident", [h["vertex"], r["vertex"]], origin="auto")
                else:
                    pass
        if h["kind"] == "crossing":
            # the two new vertices are the same point: make them one vertex
            pass
    else:
        raise ProfilerError("too many crossings to resolve")
    weld(sk, tol=1e-7)
    return done


def cut(sk, ids: Sequence[str] | None, a: Sequence[float], b: Sequence[float]) -> dict[str, Any]:
    """Cut/slice: split every selected (default: every) line and arc crossed by the cutter segment a-b at the crossing points. Nothing is removed."""
    A, B = (float(a[0]), float(a[1])), (float(b[0]), float(b[1]))
    if G.dist(A, B) < G.EPS:
        raise ProfilerError("the cutter needs two different points")
    cutter = ("line", A, B)
    targets = [i for i in (ids if ids is not None else list(sk.edges)) if i in sk.edges and sk.edge(i)["type"] in ("line", "arc") and not sk.edge(i).get("construction")]
    pts = []
    for eid in targets:
        for p in pair_points(_geom(sk, eid), cutter):
            pts.append((eid, p))
    mapping, n = {}, 0
    for eid, p in pts:
        current = eid
        for piece in mapping.get(eid, [eid]):
            try:
                g = _geom(sk, piece)
            except ProfilerNotFound:
                continue
            if _on_edge(sk, piece, p):
                r = split(sk, piece, p)
                mapping[eid] = [x for x in mapping.get(eid, [eid]) if x != piece] + r["mapping"][piece]
                n += 1
                break
    if not n:
        raise ProfilerError("the cutter does not cross any selected edge")
    return {"splits": n, "mapping": mapping}


def _on_edge(sk, eid, p, tol=1e-6) -> bool:
    pl = sk.edge_polyline(eid, 0.0005)
    return min(G.nearest_on_segment(p, pl[i], pl[i + 1])[1] for i in range(len(pl) - 1)) < max(tol, 5e-4)


# -------------------------------------------------------------------------------------------------------------- trim
def trim(sk, eid: str, pick: Sequence[float], boundaries: Sequence[str] | None = None) -> dict[str, Any]:
    """Remove the part of a line/arc/circle between the nearest crossings around ``pick``. Cut ends are attached to the boundary they were cut at (point-on-edge)."""
    e = sk.edge(eid)
    if e["type"] not in ("line", "arc", "circle"):
        raise ProfilerError("trim works on lines, arcs and circles")
    ops._check_unlocked(sk, [eid])
    hits = intersections(sk, eid, boundaries)
    closed = e["type"] == "circle"
    ts = []
    for h in hits:
        if closed or 1e-9 < h["t"] < 1 - 1e-9:
            if not any(abs(h["t"] - x["t"]) < 1e-9 for x in ts):
                ts.append(h)
    if closed and len(ts) < 2:
        raise ProfilerError("a circle needs at least two crossing edges to be trimmed")
    if not ts:
        raise ProfilerError("nothing crosses this edge: there is no boundary to trim to")
    tp = _param(sk, eid, pick)
    ends_original = list(sk.edge_ends(eid) or ())
    mapping: dict[str, list[str]] = {}
    if closed:
        ts.sort(key=lambda h: h["t"])
        lo = max((h for h in ts if h["t"] <= tp), key=lambda h: h["t"], default=ts[-1])
        hi = min((h for h in ts if h["t"] > tp), key=lambda h: h["t"], default=ts[0])
        c, r = sk.center_radius(eid)
        s_pt, e_pt = hi["point"], lo["point"]                     # keep the arc from hi round to lo (counter-clockwise)
        vs, ve = _vertex_on(sk, s_pt, hi["other"]), _vertex_on(sk, e_pt, lo["other"])
        scalar = e.pop("r")
        e["type"], e["v"] = "arc", [vs, ve]
        sk.d["scalars"].pop(scalar, None)
        flagged = _reassign(sk, eid, [], extent_changed=True)
        mapping[eid] = [eid]
    else:
        lo_t = max([h for h in ts if h["t"] < tp], key=lambda h: h["t"], default=None)
        hi_t = min([h for h in ts if h["t"] > tp], key=lambda h: h["t"], default=None)
        a_v, b_v = ends_original
        pieces = []
        if lo_t is not None:
            pieces.append((a_v, _vertex_on(sk, lo_t["point"], lo_t["other"])))
        if hi_t is not None:
            pieces.append((_vertex_on(sk, hi_t["point"], hi_t["other"]), b_v))
        if not pieces:
            raise ProfilerError("trimming here would remove the whole edge: delete it instead")
        first = pieces[0]
        new_ids = []
        _set_ends(e, first)
        mapping[eid] = [eid]
        for pc in pieces[1:]:
            n = _new_edge_like(sk, eid, v=list(pc), **({"c": e["c"]} if "c" in e else {}))
            new_ids.append(n)
            mapping[eid].append(n)
        flagged = _reassign(sk, eid, new_ids, extent_changed=True)
    _drop_orphans(sk)
    sk.settle()
    return {"mapping": mapping, "unresolved": flagged}


def _set_ends(e, pair):
    e["v"] = [pair[0], pair[1]]


# ------------------------------------------------------------------------------------------------------------ extend
def extend(sk, eid: str, near: Sequence[float], boundaries: Sequence[str] | None = None) -> dict[str, Any]:
    """Extend the end of a line or arc nearest ``near`` to the first boundary it meets. The end must be free (not shared with another edge)."""
    e = sk.edge(eid)
    if e["type"] not in ("line", "arc"):
        raise ProfilerError("extend works on lines and arcs")
    ops._check_unlocked(sk, [eid])
    p = (float(near[0]), float(near[1]))
    ends = e["v"]
    which = 0 if G.dist(p, sk.pos(ends[0])) <= G.dist(p, sk.pos(ends[1])) else 1
    v = ends[which]
    shared = [o for o in sk.edges if o != eid and v in ops.edge_vertices(sk, o)]
    if shared:
        raise ProfilerError("that end is connected to other geometry: extending it would move that geometry")
    others = [o for o, ed in sk.edges.items() if o != eid and not ed.get("construction")] if boundaries is None else list(boundaries)
    best = None
    if e["type"] == "line":
        a, b = sk.pos(ends[0]), sk.pos(ends[1])
        d = G.unit(G.sub(b, a)) if which == 1 else G.unit(G.sub(a, b))
        far = sk.pos(v)
        ray_end = G.add(far, G.mul(d, 1e6))
        for o in others:
            for q in pair_points(("line", far, ray_end), _geom(sk, o)):
                s = G.dot(G.sub(q, far), d)
                if s > 1e-9 and (best is None or s < best[0]):
                    best = (s, q, o)
    else:
        c, r, a0, sw = sk.arc_params(eid)
        ccw_out = which == 1                                       # extending the end counter-clockwise, or the start clockwise
        for o in others:
            for q in pair_points(("circle", c, r), _geom(sk, o)):
                ang = G.norm_angle(G.angle_of(G.sub(q, c)) - (a0 + sw if which == 1 else a0))
                s = ang if ccw_out else G.norm_angle(-ang)
                if s > 1e-9 and (best is None or s < best[0]):
                    best = (s, q, o)
    if best is None:
        raise ProfilerError("nothing in that direction to extend to")
    sk.vertices[v]["x"], sk.vertices[v]["y"] = best[1]
    sk.add_constraint("point_on", [v, best[2]], origin="auto")
    flagged = _reassign(sk, eid, [], extent_changed=True)
    sk.settle()
    return {"to": best[2], "unresolved": flagged}


# ------------------------------------------------------------------------------------------------------------ offset
def order_chain(sk, eids: Sequence[str]) -> tuple[list[tuple[str, bool]], bool]:
    """Order connected edges into a path: ``[(edge, forward), ...]`` and whether it closes. Raises when the edges branch or are not connected."""
    rest = list(eids)
    if not rest:
        raise ProfilerError("select the edges to offset")
    for r in rest:
        if sk.edge(r)["type"] not in ("line", "arc"):
            raise ProfilerError("only lines and arcs can form a connected path")
    deg: dict[str, int] = {}
    for r in rest:
        for v in sk.edge_ends(r):
            deg[v] = deg.get(v, 0) + 1
    if any(d > 2 for d in deg.values()):
        raise ProfilerError("the selected edges branch: select one path")
    starts = [v for v, d in deg.items() if d == 1]
    first_v = starts[0] if starts else sk.edge_ends(rest[0])[0]
    path, cur, remaining = [], first_v, list(rest)
    while remaining:
        nxt = next((r for r in remaining if cur in sk.edge_ends(r)), None)
        if nxt is None:
            raise ProfilerError("the selected edges are not one connected path")
        a, b = sk.edge_ends(nxt)
        fwd = a == cur
        path.append((nxt, fwd))
        cur = b if fwd else a
        remaining.remove(nxt)
    return path, (not starts and cur == first_v)


def offset(sk, eids: Sequence[str], distance: float, *, side: str = "left", parametric: bool = True, param: str | None = None) -> dict[str, Any]:
    """Offset a circle, an arc, a line, or a connected path of lines and arcs by ``distance`` to the left (or right) of the traversal direction. Adjacent
    offsets are joined at their intersection (miter join). Parametric (lines only): each offset line is parallel to its source at a driving perpendicular distance
    tied to ONE length parameter, so the secondary contour stays at the formula-defined distance."""
    if distance <= 0 or side not in ("left", "right"):
        raise ProfilerError("distance must be positive; side is left or right")
    sd = distance if side == "left" else -distance
    eids = list(eids)
    if len(eids) == 1 and sk.edge(eids[0])["type"] == "circle":
        c, r = sk.center_radius(eids[0])
        rr = r - sd
        if rr <= G.EPS:
            raise ProfilerError("the offset circle would have zero or negative radius")
        n = sk._edge("circle", c=sk.add_vertex(c, reuse=False), r=sk.new_scalar(rr), layer=sk.edge(eids[0])["layer"])
        sk.add_constraint("concentric", [eids[0], n], origin="offset")
        return {"edges": [n], "parametric": False, "reason": "circle/arc offsets are static (a radius is not an offset distance)", "self_intersections": 0}
    path, closed = order_chain(sk, eids)
    prims = []
    for eid, fwd in path:
        e = sk.edge(eid)
        if e["type"] == "line":
            a, b = sk.pos(e["v"][0]), sk.pos(e["v"][1])
            if not fwd:
                a, b = b, a
            a2, b2 = G.offset_line(a, b, sd)
            prims.append(("line", a2, b2))
        else:
            c, r, a0, sw = sk.arc_params(eid)
            ccw_travel = fwd
            rr = r - sd if ccw_travel else r + sd
            if rr <= G.EPS:
                raise ProfilerError("an offset arc would have zero or negative radius")
            prims.append(("circle", c, rr, fwd))
    n = len(prims)
    corner_pts: list[tuple[float, float] | None] = []
    for i in range(n if closed else n - 1):
        j = (i + 1) % n
        shared = sk.pos(_shared_vertex(sk, path[i][0], path[j][0]))
        pa, pb = prims[i], prims[j]
        pts = _infinite_intersections(pa, pb)
        if not pts:
            raise ProfilerError("adjacent edges %d and %d are parallel/concentric: the offset has no join" % (i + 1, j + 1))
        corner_pts.append(min(pts, key=lambda q: G.dist(q, shared)))
    def end_point(i, at_start: bool):
        eid, fwd = path[i]
        v = sk.edge_ends(eid)[0 if (fwd == at_start) else 1]
        pos = sk.pos(v)
        pr = prims[i]
        if pr[0] == "line":
            q, _ = G.nearest_on_segment(pos, pr[1], pr[2]) if False else (None, None)
            d = G.unit(G.sub(pr[2], pr[1]))
            base = G.sub(pos, pr[1])
            s = G.dot(base, d)
            return G.add(pr[1], G.mul(d, s))
        c = pr[1]
        return G.polar(c, pr[2], G.angle_of(G.sub(pos, c)))
    pts_start, pts_end = [], []
    for i in range(n):
        pts_start.append(corner_pts[(i - 1) % n] if (closed or i > 0) else end_point(i, True))
        pts_end.append(corner_pts[i] if (closed or i < n - 1) else end_point(i, False))
    vmap: dict[tuple[int, str], str] = {}
    # share a vertex per corner
    corner_v: list[str] = [sk.add_vertex(p, reuse=False) for p in corner_pts]
    first_v = None if closed else sk.add_vertex(pts_start[0], reuse=False)
    last_v = None if closed else sk.add_vertex(pts_end[-1], reuse=False)
    new_edges = []
    off_dims: list[str] = []
    pname = None
    if parametric and all(sk.edge(e)["type"] == "line" for e, _ in path):
        from . import params as PM
        from . import dims
        pname = param or "Offset"
        k = 1
        base = pname
        while pname in sk.d["params"]:
            k += 1
            pname = "%s%d" % (base, k)
        PM.add_param(sk, pname, "length", value=distance)
    for i, (eid, fwd) in enumerate(path):
        sv = corner_v[(i - 1) % n] if (closed or i > 0) else first_v
        ev = corner_v[i] if (closed or i < n - 1) else last_v
        pr = prims[i]
        src = sk.edge(eid)
        if pr[0] == "line":
            ne = sk._edge("line", v=[sv, ev], layer=src["layer"], construction=src.get("construction", False))
            if pname:
                from . import dims
                sk.add_constraint("parallel", [eid, ne], origin="offset")
                src_a, _ = [sk.pos(x) for x in src["v"]]
                did = dims.add_dimension(sk, "perpendicular", [sv, eid], mode="driving", value=distance)
                from . import params as PM
                PM.link_dimension(sk, did, pname)
                sk.d["dimensions"][did]["name"] = "offset"
                off_dims.append(did)
        else:
            cen = sk.add_vertex(pr[1], reuse=False)
            s_pt, e_pt = sk.pos(sv), sk.pos(ev)
            pair = [sv, ev] if pr[3] else [ev, sv]                    # arcs are stored counter-clockwise
            ne = sk._edge("arc", v=pair, c=cen, layer=src["layer"], construction=src.get("construction", False))
            sk.add_constraint("concentric", [eid, ne], origin="offset")
        new_edges.append(ne)
    # report self-intersections of the result
    pts = []
    for ne in new_edges:
        pts.extend(sk.edge_polyline(ne, 0.01) if not pts else sk.edge_polyline(ne, 0.01)[1:])
    selfx = len(G.polyline_self_intersections(pts, closed)) if len(pts) > 3 else 0
    if selfx:
        raise ProfilerError("this offset distance makes the contour cross itself (%d crossing%s): use a smaller distance" % (selfx, "s" if selfx > 1 else ""),
                            details={"self_intersections": selfx})
    if off_dims:
        sk.d.setdefault("features", []).append({"type": "offset", "id": new_id(), "sources": [e for e, _ in path], "edges": new_edges, "dims": off_dims,
                                               "holds": sorted({v for e, _ in path for v in sk.edge_ends(e)}), "parameter": pname})
    sk.settle()
    return {"edges": new_edges, "parametric": bool(pname), "parameter": pname, "self_intersections": 0, "closed": closed}


def _shared_vertex(sk, e1: str, e2: str) -> str:
    s = set(sk.edge_ends(e1)) & set(sk.edge_ends(e2))
    if not s:
        raise ProfilerError("edges are not adjacent")
    return next(iter(s))


def _infinite_intersections(pa, pb):
    if pa[0] == "line" and pb[0] == "line":
        x = G.line_line(pa[1], pa[2], pb[1], pb[2], segments=False)
        return [x] if x else []
    if pa[0] == "line" or pb[0] == "line":
        l, c = (pa, pb) if pa[0] == "line" else (pb, pa)
        far = 1e6
        d = G.unit(G.sub(l[2], l[1]))
        return G.line_circle(G.sub(l[1], G.mul(d, far)), G.add(l[2], G.mul(d, far)), c[1], c[2], segment=False)
    return G.circle_circle(pa[1], pa[2], pb[1], pb[2])


# -------------------------------------------------------------------------------------------------- fillet / chamfer
def _corner(sk, e1: str, e2: str):
    """The corner vertex shared by two edges (lines or circular arcs) and, for each edge, the vertex at its far end."""
    if e1 == e2:
        raise ProfilerError("select two different edges")
    for x in (e1, e2):
        if sk.edge(x)["type"] not in ("line", "arc"):
            raise ProfilerError("fillet and chamfer work on the corner between two lines or circular arcs, not on a %s" % sk.edge(x)["type"])
        if sk.edge(x).get("construction"):
            raise ProfilerError("that is a construction line (for example the sharp corner a fillet remembers): pick the real line next to it")
    shared = set(sk.edge(e1)["v"]) & set(sk.edge(e2)["v"])
    if len(shared) != 1:
        raise ProfilerError("the two edges must share exactly one corner vertex")
    k = next(iter(shared))
    a = next(v for v in sk.edge(e1)["v"] if v != k)
    b = next(v for v in sk.edge(e2)["v"] if v != k)
    return k, a, b


def _branch(sk, eid: str, k: str, far: str) -> dict:
    """The edge seen from the corner (see ``geom.branch``)."""
    e = sk.edge(eid)
    if e["type"] == "line":
        return G.branch(sk.pos(k), sk.pos(far))
    c, _, _, sweep = sk.arc_params(eid)
    return G.branch(sk.pos(k), sk.pos(far), c, corner_is_start=(e["v"][0] == k), sweep=sweep)


def _trim(sk, eid: str, k: str, far: str, t: str, keep_leg: bool) -> str | None:
    """Move the end of edge ``eid`` from the corner K back to the tangent/chamfer vertex ``t``. With ``keep_leg`` a construction copy of the original edge is left
    behind (a line A-K, or an arc on the same centre from the far end to K), so the sharp corner stays defined and changing the radius or setback re-solves around it
    instead of drifting."""
    e = sk.edge(eid)
    starts_at_k = e["v"][0] == k
    leg = None
    if e["type"] == "line":
        if keep_leg:
            leg = sk._edge("line", v=[far, k], construction=True, layer=e["layer"])
            # a line filleted at both ends keeps one collinear tie: it already forces the first leg's end points onto the line, which is all the second leg needs
            if not any(c["kind"] == "collinear" and c.get("origin") == "fillet" and eid in c["refs"] for c in sk.d["constraints"]):
                sk.add_constraint("collinear", [eid, leg], origin="fillet")
        e["v"] = [t, far] if starts_at_k else [far, t]
    else:
        if keep_leg:
            leg = sk._edge("arc", v=[k, far] if starts_at_k else [far, k], c=e["c"], construction=True, layer=e["layer"])
        e["v"] = [t, far] if starts_at_k else [far, t]
    return leg


def _legs(sk, e1, e2, k, a, b, t1: str, t2: str) -> list[str]:
    return [x for x in (_trim(sk, e1, k, a, t1, True), _trim(sk, e2, k, b, t2, True)) if x]


def _held(sk, k, a, b, e1, e2) -> list[str]:
    out = [k, a, b]
    for e in (e1, e2):
        if sk.edge(e)["type"] == "arc":
            out.append(sk.edge(e)["c"])
    return list(dict.fromkeys(out))


def fillet(sk, e1: str, e2: str, radius: float, *, associative: bool = True, param: str | None = None) -> dict[str, Any]:
    from . import dims
    k, a, b = _corner(sk, e1, e2)
    try:
        f = G.fillet_branches(_branch(sk, e1, k, a), _branch(sk, e2, k, b), float(radius))
    except ValueError as exc:
        raise ProfilerError(str(exc))
    t1, t2, c = sk.add_vertex(f["t1"], reuse=False), sk.add_vertex(f["t2"], reuse=False), sk.add_vertex(f["center"], reuse=False)
    ccw = G.cross(G.sub(f["t1"], f["center"]), G.sub(f["t2"], f["center"])) > 0
    arc = sk._edge("arc", v=[t1, t2] if ccw else [t2, t1], c=c, layer=sk.edge(e1)["layer"])
    if associative:
        legs = _legs(sk, e1, e2, k, a, b, t1, t2)
    else:
        legs = []
        _trim(sk, e1, k, a, t1, False)
        _trim(sk, e2, k, b, t2, False)
    sk.add_constraint("tangent_at", [e1, arc, t1], origin="fillet")
    sk.add_constraint("tangent_at", [e2, arc, t2], origin="fillet")
    did = dims.add_dimension(sk, "radius", [arc], mode="driving", value=radius) if associative else None
    if param and did:
        from . import params as PM
        PM.link_dimension(sk, did, param)
    sk.d.setdefault("features", []).append({"type": "fillet", "id": new_id(), "lines": [e1, e2], "arc": arc, "corner": k, "legs": legs, "radius_dimension": did, "associative": associative,
                                           "dims": [did] if did else [], "holds": _held(sk, k, a, b, e1, e2)})
    _drop_orphans(sk)
    sk.settle()
    return {"arc": arc, "radius_dimension": did, "corner_vertex": k, "associative": associative}


def chamfer(sk, e1: str, e2: str, d1: float, d2: float | None = None, *, associative: bool = True) -> dict[str, Any]:
    """Chamfer the corner between two lines or arcs. The set-backs are straight-line distances from the sharp corner to the chamfer's end points."""
    from . import dims
    k, a, b = _corner(sk, e1, e2)
    try:
        f = G.chamfer_branches(_branch(sk, e1, k, a), _branch(sk, e2, k, b), float(d1), None if d2 is None else float(d2))
    except ValueError as exc:
        raise ProfilerError(str(exc))
    t1, t2 = sk.add_vertex(f["t1"], reuse=False), sk.add_vertex(f["t2"], reuse=False)
    if associative:
        legs = _legs(sk, e1, e2, k, a, b, t1, t2)
    else:
        legs = []
        _trim(sk, e1, k, a, t1, False)
        _trim(sk, e2, k, b, t2, False)
    line = sk._edge("line", v=[t1, t2], layer=sk.edge(e1)["layer"])
    ds = []
    if associative:
        ds = [dims.add_dimension(sk, "aligned", [k, t1], mode="driving", value=d1), dims.add_dimension(sk, "aligned", [k, t2], mode="driving", value=d1 if d2 is None else d2)]
    sk.d.setdefault("features", []).append({"type": "chamfer", "id": new_id(), "lines": [e1, e2], "edge": line, "corner": k, "legs": legs, "setback_dimensions": ds, "associative": associative,
                                           "dims": ds, "holds": _held(sk, k, a, b, e1, e2)})
    _drop_orphans(sk)
    sk.settle()
    return {"edge": line, "setback_dimensions": ds, "corner_vertex": k}


# --------------------------------------------------------------------------------------------- join / weld / merge / connect
def _replace_vertex(sk, old: str, new: str) -> None:
    for e in sk.edges.values():
        e["v"] = [new if v == old else v for v in e.get("v", [])]
        if e.get("c") == old:
            e["c"] = new
        if "handles" in e:
            e["handles"] = [[new if x == old else x for x in h] for h in e["handles"]]
    for c in sk.d["constraints"]:
        c["refs"] = [new if r == old else r for r in c["refs"]]
    for d in sk.d["dimensions"].values():
        d["refs"] = [new if r == old else r for r in d["refs"]]
    for c in sk.d["comments"].values():
        if c.get("target") == old:
            c["target"] = new
    for g in sk.d["groups"].values():
        g["members"] = [new if m == old else m for m in g["members"]]
    for comp in sk.d["components"].values():
        if isinstance(comp, dict) and "members" in comp:
            comp["members"] = [new if m == old else m for m in comp["members"]]
            if comp.get("origin") == old:
                comp["origin"] = new
    sk.d["vertices"].pop(old, None)


def merge_vertices(sk, ids: Sequence[str], *, at: str = "first") -> dict[str, Any]:
    """Merge two or more vertices into one (the first keeps its id). ``at`` = ``first`` (its position) or ``centroid``. Edges that would collapse are refused."""
    ids = list(dict.fromkeys(ids))
    if len(ids) < 2 or any(i not in sk.vertices for i in ids):
        raise ProfilerError("select two or more vertices to merge")
    keep, rest = ids[0], ids[1:]
    pts = [sk.pos(i) for i in ids]
    for e in sk.edges.values():
        vs = [v for v in e.get("v", [])]
        if e["type"] in ("line", "arc") and set(vs) <= set(ids):
            raise ProfilerError("merging these vertices would collapse an edge to a point")
    target = pts[0] if at == "first" else (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
    sk.vertices[keep]["x"], sk.vertices[keep]["y"] = target
    for r in rest:
        _replace_vertex(sk, r, keep)
    sk.d["constraints"] = [c for c in sk.d["constraints"] if not (c["kind"] == "coincident" and len(set(c["refs"])) == 1)]
    sk.settle(fixed_points=[keep])
    return {"kept": keep, "merged": rest}


def weld(sk, tol: float | None = None) -> dict[str, Any]:
    """Merge every group of coincident vertices (within ``tol``, default the document tolerance) - used to heal tiny gaps and duplicate points."""
    tol = sk.tol if tol is None else tol
    ids = sorted(sk.vertices)
    groups, seen = [], set()
    for i, a in enumerate(ids):
        if a in seen:
            continue
        grp = [a]
        for b in ids[i + 1:]:
            if b not in seen and G.dist(sk.pos(a), sk.pos(b)) <= tol:
                grp.append(b)
        if len(grp) > 1:
            seen.update(grp)
            groups.append(grp)
    merged = 0
    for g in groups:
        try:
            merge_vertices(sk, g)
            merged += len(g) - 1
        except ProfilerError:
            continue
    return {"merged": merged, "groups": len(groups)}


def connect(sk, v1: str, v2: str) -> str:
    """Connect: bridge two (free) vertices with a new line. Nothing is moved or merged."""
    if v1 == v2 or v1 not in sk.vertices or v2 not in sk.vertices:
        raise ProfilerError("connect needs two different vertices")
    for eid, e in sk.edges.items():
        if e["type"] == "line" and set(e["v"]) == {v1, v2}:
            raise ProfilerError("these vertices are already connected by a line")
    return sk._edge("line", v=[v1, v2])


def attach(sk, vertex: str, target: str) -> dict[str, Any]:
    """Attach: keep both identities but bind them with a persistent relationship - to another vertex (coincident) or to an edge (point-on-edge, the vertex
    is projected onto it first). Different from Weld/Merge (one vertex left) and Join (two edges become one)."""
    if vertex not in sk.vertices:
        raise ProfilerNotFound("no vertex %s" % vertex)
    if target in sk.vertices:
        cid = sk.add_constraint("coincident", [vertex, target], origin="attach")
    elif target in sk.edges:
        pl = sk.edge_polyline(target, 0.0005)
        best = min((G.nearest_on_segment(sk.pos(vertex), pl[i], pl[i + 1]) for i in range(len(pl) - 1)), key=lambda t: t[1])
        sk.vertices[vertex]["x"], sk.vertices[vertex]["y"] = best[0]
        cid = sk.add_constraint("point_on", [vertex, target], origin="attach")
    else:
        raise ProfilerNotFound("nothing with id %s" % target)
    sk.settle(fixed_points=[vertex] if target in sk.edges else [])
    return {"constraint": cid}


def join(sk, e1: str, e2: str) -> dict[str, Any]:
    """Join two edges into one: collinear lines that share a vertex, or arcs of one circle that share an end. The first edge keeps its id."""
    a, b = sk.edge(e1), sk.edge(e2)
    if a["type"] != b["type"] or a["type"] not in ("line", "arc"):
        raise ProfilerError("join works on two lines or two arcs")
    shared = set(a["v"]) & set(b["v"])
    if len(shared) != 1:
        raise ProfilerError("the edges must share exactly one end vertex")
    m = next(iter(shared))
    if any(m in ops.edge_vertices(sk, o) for o in sk.edges if o not in (e1, e2)):
        raise ProfilerError("another edge meets at that vertex: join would break the junction")
    if a["type"] == "line":
        p, q, r = [sk.pos(next(v for v in a["v"] if v != m)), sk.pos(m), sk.pos(next(v for v in b["v"] if v != m))]
        if abs(G.dist_point_line(q, p, r)) > max(sk.tol, 1e-6):
            raise ProfilerError("the lines are not collinear: they cannot be joined into one line")
        ends = [next(v for v in a["v"] if v != m), next(v for v in b["v"] if v != m)]
        a["v"] = ends
    else:
        if a["c"] != b["c"] and G.dist(sk.pos(a["c"]), sk.pos(b["c"])) > 1e-6:
            raise ProfilerError("the arcs are not concentric")
        if abs(sk.arc_params(e1)[1] - sk.arc_params(e2)[1]) > 1e-6:
            raise ProfilerError("the arcs have different radii")
        if a["v"][1] == m and b["v"][0] == m:
            a["v"] = [a["v"][0], b["v"][1]]
        elif b["v"][1] == m and a["v"][0] == m:
            a["v"] = [b["v"][0], a["v"][1]]
        else:
            raise ProfilerError("the arcs do not run end to start")
    del sk.d["edges"][e2]
    _replace_edge_refs(sk, e2, e1)
    flagged = _reassign(sk, e1, [], extent_changed=True)
    _drop_orphans(sk)
    sk.settle()
    return {"mapping": {e2: [e1]}, "unresolved": flagged}


def _replace_edge_refs(sk, old: str, new: str) -> None:
    for c in sk.d["constraints"]:
        c["refs"] = [new if r == old else r for r in c["refs"]]
    for d in sk.d["dimensions"].values():
        d["refs"] = [new if r == old else r for r in d["refs"]]


# --------------------------------------------------------------------------------------------------- region booleans
def region_boolean(sk, op: str, edges_a: Sequence[str], edges_b: Sequence[str], *, replace: bool = True) -> dict[str, Any]:
    from . import regions
    try:
        res = regions.boolean(sk, op, edges_a, edges_b)
    except ValueError as exc:
        raise ProfilerError(str(exc))
    if replace:
        ops.delete(sk, list(edges_a) + list(edges_b), _purge=True)
    out = []
    for r in res:
        loop = [sk.add_polyline(r["outer"], closed=True)]
        for h in r["holes"]:
            loop.append(sk.add_polyline(h, closed=True))
        out.append({"edges": [e for part in loop for e in part], "area": r["area"], "approximation": r["approximation"]})
    return {"regions": out}
