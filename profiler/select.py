"""Selection queries: pick-at-point, window / crossing rectangles, and filters. Pure reads - the client keeps the selection."""
from __future__ import annotations

from typing import Any, Iterable, Sequence

from . import annotate as A
from . import geom as G
from .errors import ProfilerError


_KINDS = ("edges", "vertices", "dimensions", "comments", "texts")


def _norm_filters(filters: Any) -> dict:
    """Accept both shapes: a dict of options, or the list of switched-on object kinds the browser sends (``["edges", "texts"]``)."""
    if not filters:
        return {}
    if isinstance(filters, dict):
        return filters
    if isinstance(filters, (list, tuple, set, frozenset)):
        on = {str(k) for k in filters}
        return {k: (k in on) for k in _KINDS}
    raise ProfilerError("selection filters must be an object or a list of names")


def _passes(sk, eid: str, f: dict) -> bool:
    e = sk.edge(eid)
    layer = sk.layer(e["layer"])
    if f.get("types") and e["type"] not in f["types"]:
        return False
    if f.get("layers") and e["layer"] not in f["layers"]:
        return False
    if not layer["visible"]:
        return False
    if e.get("construction") and not f.get("construction", True):
        return False
    if (e.get("locked") or layer["locked"]) and not f.get("locked", True):
        return False
    return True


def _seg_hits_rect(p, q, r) -> bool:
    x0, y0, x1, y1 = r
    if max(p[0], q[0]) < x0 or min(p[0], q[0]) > x1 or max(p[1], q[1]) < y0 or min(p[1], q[1]) > y1:
        return False
    if x0 <= p[0] <= x1 and y0 <= p[1] <= y1 or x0 <= q[0] <= x1 and y0 <= q[1] <= y1:
        return True
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return any(G.line_line(p, q, corners[i], corners[(i + 1) % 4]) is not None for i in range(4))


def window(sk, rect: Sequence[float], mode: str = "window", filters: dict | None = None) -> dict[str, Any]:
    """``window``: only what lies completely inside the rectangle. ``crossing``: anything inside *or touching* it. The rectangle is normalised, so dragging
    right-to-left or bottom-to-top works. Filters: ``types``, ``layers``, ``construction`` (default included), ``locked`` (default included), ``vertices``,
    ``dimensions``, ``comments``, ``texts`` (all default on)."""
    if mode not in ("window", "crossing"):
        raise ProfilerError("selection mode is window or crossing")
    x0, x1 = sorted((float(rect[0]), float(rect[2])))
    y0, y1 = sorted((float(rect[1]), float(rect[3])))
    r = (x0, y0, x1, y1)
    f = _norm_filters(filters)
    inside = lambda p: x0 <= p[0] <= x1 and y0 <= p[1] <= y1
    edges = []
    for eid in (sk.edges if f.get("edges", True) else ()):
        if not _passes(sk, eid, f):
            continue
        pl = sk.edge_polyline(eid, 0.01)
        if mode == "window":
            ok = all(inside(p) for p in pl)
        else:
            ok = any(inside(p) for p in pl) or any(_seg_hits_rect(pl[i], pl[i + 1], r) for i in range(len(pl) - 1))
        if ok:
            edges.append(eid)
    from . import ops
    used = ops.used_vertices(sk)
    verts = [v for v in sk.vertices if f.get("vertices", True) and v in used and inside(sk.pos(v))]
    out = {"edges": edges, "vertices": verts, "mode": mode, "rect": list(r)}
    if f.get("dimensions", True):
        from . import render as RN
        out["dimensions"] = []
        for did, dim in sk.d["dimensions"].items():
            try:
                g = RN.dimension_geometry(sk, dim, float(dim.get("offset", 8.0)))
                if inside(g["label"]):
                    out["dimensions"].append(did)
            except Exception:
                continue
    if f.get("comments", True):
        out["comments"] = [cid for cid, c in sk.d["comments"].items() if inside(A.comment_position(sk, c))]
    if f.get("texts", True):
        out["texts"] = [tid for tid, t in sk.d["texts"].items() if inside(t["xy"])]
    return out


def pick(sk, point: Sequence[float], radius: float, filters: dict | None = None) -> dict[str, Any]:
    """Objects within ``radius`` of ``point``, nearest first; vertices win ties with edges (so corners can be grabbed)."""
    from . import ops, snap
    f = _norm_filters(filters)
    p = (float(point[0]), float(point[1]))
    hits: list[dict] = []
    used = ops.used_vertices(sk)
    if f.get("vertices", True):
        for v in used:
            d = G.dist(p, sk.pos(v))
            if d <= radius:
                hits.append({"kind": "vertex", "id": v, "distance": d, "score": d * 0.5})
    for eid in (sk.edges if f.get("edges", True) else ()):
        if not _passes(sk, eid, f):
            continue
        q = snap._nearest_on_edge(sk, eid, p)
        if q is not None and G.dist(p, q) <= radius:
            d = G.dist(p, q)
            # construction copies (such as the sharp-corner legs a fillet keeps) lie on top of real edges: the real edge wins a tie
            hits.append({"kind": "edge", "id": eid, "distance": d, "score": d + (radius * 0.01 if sk.edge(eid).get("construction") else 0.0)})
    for cid, c in sk.d["comments"].items():
        d = G.dist(p, A.comment_position(sk, c))
        if d <= radius:
            hits.append({"kind": "comment", "id": cid, "distance": d})
    for tid, t in sk.d["texts"].items():
        d = G.dist(p, t["xy"])
        if d <= max(radius, t["height"]):
            hits.append({"kind": "text", "id": tid, "distance": d})
    from . import render as RN
    for did, dim in sk.d["dimensions"].items():
        try:
            g = RN.dimension_geometry(sk, dim, float(dim.get("offset", 8.0)))
            d = G.dist(p, g["label"])
            if d <= radius * 2:
                hits.append({"kind": "dimension", "id": did, "distance": d})
        except Exception:
            continue
    hits.sort(key=lambda h: h.get("score", h["distance"]))
    return {"hits": hits, "first": hits[0] if hits else None}


def connected(sk, seed: Iterable[str]) -> list[str]:
    """Select chain: every edge connected to the seed edges through shared vertices."""
    from . import ops
    seed = [s for s in seed if s in sk.edges]
    seen = set(seed)
    stack = list(seed)
    by_v: dict[str, list[str]] = {}
    for e in sk.edges:
        for v in ops.edge_vertices(sk, e):
            by_v.setdefault(v, []).append(e)
    while stack:
        e = stack.pop()
        for v in ops.edge_vertices(sk, e):
            for o in by_v.get(v, ()):
                if o not in seen:
                    seen.add(o)
                    stack.append(o)
    return sorted(seen)
