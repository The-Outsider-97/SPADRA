"""The render model: everything a viewport needs to draw a sketch, as plain JSON (no drawing here).

Curves are flattened here (sagitta ``tol``), colours are resolved (edge -> layer -> document default), dimensions carry their label text and placement
geometry, and hidden layers are omitted. Selection colour, hover and snap markers are client state and never part of this model.
"""
from __future__ import annotations

import math
from typing import Any, Sequence

from . import annotate as A
from . import dims as D
from . import geom as G


def fmt_value(sk, dim: dict, m: dict, style: dict) -> str:
    p = int(style.get("precision", sk.d.get("precision", 3)))
    v = m.get("value")
    if v is None:
        return "?"
    if dim["type"] == "angular":
        txt = "%.*f°" % (p, m.get("degrees", math.degrees(v)))
    elif dim["type"] == "slope":
        txt = "%.*f°" % (p, v)
    elif dim["type"] == "area":
        txt = "%.*f %s²" % (p, v, sk.unit)
    elif dim["type"] == "radius":
        txt = "R%.*f" % (p, v)
    elif dim["type"] == "diameter":
        txt = "Ø%.*f" % (p, v)
    else:
        txt = "%.*f" % (p, v)
        if style.get("unit_label", True):
            txt += " " + sk.unit
    if style.get("decimal", ".") == ",":
        txt = txt.replace(".", ",")
    tol = dim.get("tolerance") or style.get("tolerance")
    if tol:
        txt += " ±%g" % tol if isinstance(tol, (int, float)) else " " + str(tol)
    return txt


def dimension_geometry(sk, dim: dict, off: float) -> dict[str, Any]:
    t, refs = dim["type"], dim["refs"]
    try:
        if t in ("aligned", "length", "center_distance", "horizontal", "vertical"):
            a, b = sk.pos(refs[0]), sk.pos(refs[1])
            if t == "horizontal":
                a2, b2 = (a[0], max(a[1], b[1]) + off), (b[0], max(a[1], b[1]) + off)
            elif t == "vertical":
                a2, b2 = (max(a[0], b[0]) + off, a[1]), (max(a[0], b[0]) + off, b[1])
            else:
                u = G.unit(G.sub(b, a))
                n = G.perp(u)
                a2, b2 = G.add(a, G.mul(n, off)), G.add(b, G.mul(n, off))
            return {"kind": "linear", "ext": [[list(a), list(a2)], [list(b), list(b2)]], "line": [list(a2), list(b2)], "label": list(G.mid(a2, b2))}
        if t in ("radius", "diameter"):
            c, r = sk.center_radius(refs[0])
            ang = math.radians(45)
            q = G.polar(c, r, ang)
            return {"kind": "radial", "line": [list(c) if t == "radius" else list(G.polar(c, r, ang + math.pi)), list(q)], "label": list(G.polar(c, r * 1.15 + off * 0.2, ang))}
        if t == "angular":
            if dim.get("three_point"):
                c, p1, p2 = sk.pos(refs[0]), sk.pos(refs[1]), sk.pos(refs[2])
            else:
                (a1, a2), (b1, b2) = [[sk.pos(v) for v in sk.edge(r)["v"]] for r in refs]
                c = G.line_line(a1, a2, b1, b2, segments=False) or a1
                p1, p2 = a2, b2
            r = max(G.dist(c, p1), G.dist(c, p2)) * 0.5 or off
            s0, s1 = G.angle_of(G.sub(p1, c)), G.angle_of(G.sub(p2, c))
            sw = G.norm_angle(s1 - s0)
            if sw > math.pi:
                sw -= G.TAU
            pts = [list(G.polar(c, r, s0 + sw * i / 16)) for i in range(17)]
            return {"kind": "angular", "arc": pts, "label": list(G.polar(c, r * 1.1, s0 + sw / 2))}
        if t in ("arc_length", "chord", "spline_length"):
            pl = sk.edge_polyline(refs[0], 0.02)
            return {"kind": "curve", "line": [list(p) for p in pl], "label": list(pl[len(pl) // 2])}
        pts = [sk.pos(r) for r in refs if r in sk.vertices]
        if pts:
            return {"kind": "note", "label": [sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts) + off]}
    except Exception:
        pass
    return {"kind": "none", "label": [0.0, 0.0]}


def render(sk, *, tol: float | None = None, include_hidden: bool = False, with_dimension_values: bool = True) -> dict[str, Any]:
    d = sk.d
    layers = {l["name"]: l for l in d["layers"]}
    vis = lambda name: include_hidden or layers.get(name, {}).get("visible", True)
    tol = tol or max(sk.tol * 100, 0.01)
    edges = []
    for eid, e in d["edges"].items():
        if not vis(e["layer"]):
            continue
        pl = sk.edge_polyline(eid, tol)
        rec: dict[str, Any] = {"id": eid, "type": e["type"], "layer": e["layer"], "color": A.effective_color(sk, e), "construction": bool(e.get("construction")),
                               "locked": bool(e.get("locked") or layers.get(e["layer"], {}).get("locked")), "points": [[p[0], p[1]] for p in pl]}
        if e["type"] in ("line", "arc", "spline"):
            rec["ends"] = list(sk.edge_ends(eid) or ())
        if e["type"] in ("circle", "arc", "ellipse"):
            rec["center"] = list(sk.pos(e["c"]))
        if e["type"] in ("circle", "arc"):
            rec["radius"] = sk.center_radius(eid)[1]
        if e["type"] == "arc":
            c, r, a0, sw = sk.arc_params(eid)
            rec["start_angle"], rec["sweep"] = a0, sw
        edges.append(rec)
    from . import ops
    used = ops.used_vertices(sk)
    verts = [{"id": v, "x": p["x"], "y": p["y"]} for v, p in d["vertices"].items() if v in used]
    dim_out = []
    for did, dim in d["dimensions"].items():
        if not vis(dim.get("layer", "annotation")) and dim.get("layer") in layers:
            continue
        style = {**d["styles"]["dimension"].get("default", {}), **d["styles"]["dimension"].get(dim.get("style", "default"), {})}
        rec = {"id": did, "type": dim["type"], "mode": dim["mode"], "state": dim.get("state", "ok"), "refs": dim["refs"], "param": dim.get("param"),
               "expr": dim.get("expr"), "anchor": dim.get("anchor", "none"), "name": dim.get("name"), "style": style}
        if dim.get("state") == "unresolved":
            rec.update(text="?", geometry={"kind": "none", "label": [0, 0]}, unresolved=True, provenance=dim.get("provenance"))
        elif with_dimension_values:
            try:
                m = D.measure(sk, dim)
                rec["value"] = m.get("value")
                rec["text"] = fmt_value(sk, dim, m, style)
                rec["geometry"] = dimension_geometry(sk, dim, float(dim.get("offset", 8.0)))
            except Exception as exc:                       # a broken reference is shown, not hidden
                rec.update(text="?", geometry={"kind": "none", "label": [0, 0]}, error=str(exc))
        dim_out.append(rec)
    cons = []
    for c in d["constraints"]:
        glyph = None
        pts = [sk.pos(r) for r in c["refs"] if r in d["vertices"]]
        for r in c["refs"]:
            if r in d["edges"]:
                try:
                    pl = sk.edge_polyline(r, 0.05)
                    pts.append(pl[len(pl) // 2])
                except Exception:
                    pass
        if pts:
            glyph = [sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)]
        cons.append({"id": c["id"], "kind": c["kind"], "refs": c["refs"], "state": c.get("state", "ok"), "enabled": c.get("enabled", True), "origin": c.get("origin"),
                     "value": c.get("value"), "at": glyph})
    comments = [{"id": cid, "at": list(A.comment_position(sk, c)), "text": c["text"], "author": c["author"], "created": c["created"], "resolved": c["resolved"],
                 "replies": c["replies"], "anchor": c["anchor"]} for cid, c in d["comments"].items()]
    texts = [dict(t) for t in d["texts"].values() if vis(t["layer"])]
    hatches = []
    for hid, h in d.get("hatches", {}).items():
        if vis(h["layer"]):
            hl = A.hatch_lines(sk, h)
            hatches.append({"id": hid, "pattern": h["pattern"], "color": h["color"] or d["default_color"], "rings": hl["rings"], "segments": hl["segments"],
                            "broken": hl["broken"], "edges": h["edges"]})
    b = sk.bounds()
    return {"revision": d["revision"], "name": d["name"], "unit": d["unit"], "precision": d["precision"], "default_color": d["default_color"],
            "bounds": list(b) if b else None, "view": d["view"], "layers": d["layers"], "edges": edges, "vertices": verts, "dimensions": dim_out,
            "constraints": cons, "comments": comments, "texts": texts, "hatches": hatches,
            "groups": [{"id": g, **v} for g, v in d["groups"].items()], "params": d["params"], "chains": d["chains"],
            "features": d.get("features", [])}
