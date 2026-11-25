"""Annotation and appearance: layers, colours, comments, text, hatching, blocks, units.

Colours are kept strictly apart (authoritative PDF p.3): ``color`` on an edge / layer is the *line* colour, with ``default_color`` (#F6F6F6) when nothing is
set. Selection colour is a viewer state, region fill/material belongs to the hand-off - none of them is ever written into an edge's ``color``.
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Iterable, Sequence

from src.utils.spatial_formats.common import units as U
from src.utils.spatial_formats.common.ids import new_id

from . import geom as G
from . import ops
from .errors import ProfilerError, ProfilerNotFound

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
PATTERNS = ("solid", "lines", "cross", "dots")
MAX_TEXT = 4000


def norm_color(c: str | None) -> str | None:
    if c is None or c == "":
        return None
    if not isinstance(c, str) or not HEX.match(c):
        raise ProfilerError("a colour is #RRGGBB (for example #F6F6F6)")
    return c.upper()


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _text(s: Any, what: str = "text", allow_empty: bool = False) -> str:
    if not isinstance(s, str):
        raise ProfilerError("%s must be text" % what)
    s = s.strip("\r\n")
    if not s.strip() and not allow_empty:
        raise ProfilerError("%s cannot be empty" % what)
    if len(s) > MAX_TEXT:
        raise ProfilerError("%s is longer than %d characters" % (what, MAX_TEXT))
    return s


# ------------------------------------------------------------------------------------------------------------ colour
def effective_color(sk, edge: dict) -> str:
    if edge.get("color"):
        return edge["color"]
    lc = sk.layer(edge["layer"]).get("color")
    return lc or sk.d["default_color"]


def set_color(sk, ids: Iterable[str], color: str | None) -> dict:
    """Set (or with ``None`` clear -> by layer / default) the line colour of the selected edges. Never touches selection colour or region fill."""
    col = norm_color(color)
    _, es = ops.resolve(sk, ids)
    if not es:
        raise ProfilerError("select some edges to colour")
    ops._check_unlocked(sk, es)
    for e in es:
        sk.edge(e)["color"] = col
    return {"edges": sorted(es), "color": col}


def set_default_color(sk, color: str) -> None:
    c = norm_color(color)
    if c is None:
        raise ProfilerError("the default colour cannot be empty")
    sk.d["default_color"] = c


# ------------------------------------------------------------------------------------------------------------ layers
def add_layer(sk, name: str, *, color: str | None = None) -> dict:
    name = _text(name, "layer name")
    if len(name) > 64:
        raise ProfilerError("layer names are at most 64 characters")
    if name in sk.layer_names():
        raise ProfilerError("layer %r already exists" % name)
    layer = {"name": name, "visible": True, "locked": False, "color": norm_color(color)}
    sk.d["layers"].append(layer)
    return layer


def rename_layer(sk, old: str, new: str) -> None:
    sk.layer(old)
    if old == "0":
        raise ProfilerError("layer 0 is the base layer and cannot be renamed")
    new = _text(new, "layer name")
    if new in sk.layer_names():
        raise ProfilerError("layer %r already exists" % new)
    sk.layer(old)["name"] = new
    for e in sk.edges.values():
        if e["layer"] == old:
            e["layer"] = new
    for coll in ("dimensions", "texts", "hatches"):
        for it in sk.d.get(coll, {}).values():
            if it.get("layer") == old:
                it["layer"] = new


def set_layer(sk, name: str, *, visible: bool | None = None, locked: bool | None = None, color: Any = ...) -> dict:
    layer = sk.layer(name)
    if visible is not None:
        layer["visible"] = bool(visible)
    if locked is not None:
        layer["locked"] = bool(locked)
    if color is not ...:
        layer["color"] = norm_color(color)
    return layer


def delete_layer(sk, name: str, *, move_to: str = "0") -> dict:
    if name == "0":
        raise ProfilerError("layer 0 is the base layer and cannot be deleted")
    sk.layer(name)
    sk.layer(move_to)
    if move_to == name:
        raise ProfilerError("choose a different layer to move the content to")
    moved = 0
    for e in sk.edges.values():
        if e["layer"] == name:
            e["layer"] = move_to
            moved += 1
    for coll in ("dimensions", "texts", "hatches"):
        for it in sk.d.get(coll, {}).values():
            if it.get("layer") == name:
                it["layer"] = move_to
    sk.d["layers"] = [l for l in sk.d["layers"] if l["name"] != name]
    return {"moved": moved, "to": move_to}


def move_to_layer(sk, ids: Iterable[str], layer: str) -> dict:
    sk.layer(layer)
    _, es = ops.resolve(sk, ids)
    if not es:
        raise ProfilerError("select some edges to move")
    ops._check_unlocked(sk, es)
    if sk.layer(layer)["locked"]:
        raise ProfilerError("layer %r is locked" % layer)
    for e in es:
        sk.edge(e)["layer"] = layer
    return {"edges": sorted(es), "layer": layer}


def set_construction(sk, ids: Iterable[str], on: bool) -> dict:
    _, es = ops.resolve(sk, ids)
    if not es:
        raise ProfilerError("select some edges")
    ops._check_unlocked(sk, es)
    for e in es:
        sk.edge(e)["construction"] = bool(on)
    return {"edges": sorted(es), "construction": bool(on)}


def set_locked(sk, ids: Iterable[str], on: bool) -> dict:
    _, es = ops.resolve(sk, ids)
    for e in es:
        sk.edge(e)["locked"] = bool(on)
    return {"edges": sorted(es), "locked": bool(on)}


# ----------------------------------------------------------------------------------------------------------- comments
def comment_position(sk, c: dict) -> tuple[float, float]:
    a = c["anchor"]
    try:
        if a["kind"] == "vertex" and a["id"] in sk.vertices:
            return sk.pos(a["id"])
        if a["kind"] == "edge" and a["id"] in sk.edges:
            pl = sk.edge_polyline(a["id"], 0.01)
            total = G.polyline_length(pl) or 1.0
            want, acc = total * float(a.get("t", 0.5)), 0.0
            for i in range(len(pl) - 1):
                seg = G.dist(pl[i], pl[i + 1])
                if acc + seg >= want - 1e-12:
                    return G.lerp(pl[i], pl[i + 1], 0 if seg == 0 else (want - acc) / seg)
                acc += seg
            return pl[-1]
    except ProfilerNotFound:
        pass
    return tuple(c.get("xy", (0.0, 0.0)))  # type: ignore[return-value]


def add_comment(sk, text: str, *, xy: Sequence[float] | None = None, vertex: str | None = None, edge: str | None = None, t: float = 0.5,
                author: str = "user") -> dict:
    """A comment attaches to a point (free), a vertex or a position along an edge, and follows that geometry when it moves. The marker shows a bubble on
    double-click (UI); this stores the text, the author and the resolved state."""
    text = _text(text, "comment")
    if vertex is not None:
        sk.pos(vertex)
        anchor = {"kind": "vertex", "id": vertex}
    elif edge is not None:
        sk.edge(edge)
        anchor = {"kind": "edge", "id": edge, "t": min(1.0, max(0.0, float(t)))}
    elif xy is not None:
        anchor = {"kind": "point"}
    else:
        raise ProfilerError("say where the comment goes: a point, a vertex or an edge")
    cid = new_id()
    c: dict[str, Any] = {"id": cid, "text": text, "anchor": anchor, "xy": [0.0, 0.0], "author": str(author)[:80] or "user", "created": _now(), "edited": None,
                         "resolved": False, "replies": [], "layer": "annotation"}
    if xy is not None:
        c["xy"] = [float(xy[0]), float(xy[1])]
    if vertex is not None:
        c["target"] = vertex                                    # lets vertex merges re-point the comment instead of orphaning it
    sk.d["comments"][cid] = c
    c["xy"] = list(comment_position(sk, c))
    return c


def _comment(sk, cid: str) -> dict:
    c = sk.d["comments"].get(cid)
    if c is None:
        raise ProfilerNotFound("no comment %s" % cid)
    return c


def edit_comment(sk, cid: str, text: str) -> dict:
    c = _comment(sk, cid)
    c["text"], c["edited"] = _text(text, "comment"), _now()
    return c


def reply_comment(sk, cid: str, text: str, author: str = "user") -> dict:
    c = _comment(sk, cid)
    c["replies"].append({"id": new_id(), "text": _text(text, "reply"), "author": str(author)[:80] or "user", "created": _now()})
    return c


def resolve_comment(sk, cid: str, resolved: bool = True) -> dict:
    c = _comment(sk, cid)
    c["resolved"] = bool(resolved)
    return c


def move_comment(sk, cid: str, xy: Sequence[float]) -> dict:
    c = _comment(sk, cid)
    c["anchor"], c["xy"] = {"kind": "point"}, [float(xy[0]), float(xy[1])]
    c.pop("target", None)
    return c


def delete_comment(sk, cid: str) -> None:
    _comment(sk, cid)
    del sk.d["comments"][cid]


# ---------------------------------------------------------------------------------------------------------------- text
def add_text(sk, xy: Sequence[float], text: str, *, height: float | None = None, rotation: float = 0.0, layer: str = "annotation", color: str | None = None) -> str:
    if layer not in sk.layer_names():
        add_layer(sk, layer)
    h = float(height if height is not None else sk.d["styles"]["dimension"]["default"]["text_height"])
    if h <= 0:
        raise ProfilerError("text height must be positive")
    tid = new_id()
    sk.d["texts"][tid] = {"id": tid, "xy": [float(xy[0]), float(xy[1])], "text": _text(text), "height": h, "rotation": float(rotation), "layer": layer,
                          "color": norm_color(color)}
    return tid


def edit_text(sk, tid: str, **fields) -> dict:
    t = sk.d["texts"].get(tid)
    if t is None:
        raise ProfilerNotFound("no text %s" % tid)
    for k, v in fields.items():
        if k == "text":
            t["text"] = _text(v)
        elif k in ("height", "rotation"):
            t[k] = float(v)
        elif k == "xy":
            t["xy"] = [float(v[0]), float(v[1])]
        elif k == "color":
            t["color"] = norm_color(v)
        elif k == "layer":
            sk.layer(v)
            t["layer"] = v
        else:
            raise ProfilerError("a text has no field %r" % k)
    return t


def delete_text(sk, tid: str) -> None:
    if tid not in sk.d["texts"]:
        raise ProfilerNotFound("no text %s" % tid)
    del sk.d["texts"][tid]


# -------------------------------------------------------------------------------------------------------------- hatches
def add_hatch(sk, edges: Sequence[str], *, pattern: str = "lines", angle: float = 45.0, spacing: float | None = None, color: str | None = None,
              layer: str = "0") -> str:
    """Hatch the closed region bounded by ``edges`` (holes are excluded). The hatch is tied to the *edges*, so it follows edits; if they stop bounding a region
    it is reported by Check Profile rather than silently dropped."""
    from . import regions
    if pattern not in PATTERNS:
        raise ProfilerError("pattern must be one of %s" % ", ".join(PATTERNS))
    found = regions.region_containing_edges(sk, list(edges))
    if found is None:
        raise ProfilerError("these edges do not bound a closed region: nothing to hatch")
    sp = float(spacing) if spacing else max(1.0, (found["area"] ** 0.5) / 12.0)
    if sp <= 0:
        raise ProfilerError("hatch spacing must be positive")
    hid = new_id()
    sk.d.setdefault("hatches", {})[hid] = {"id": hid, "edges": list(edges), "pattern": pattern, "angle": float(angle), "spacing": sp, "color": norm_color(color), "layer": layer}
    return hid


def remove_hatch(sk, hid: str) -> None:
    if hid not in sk.d.get("hatches", {}):
        raise ProfilerNotFound("no hatch %s" % hid)
    del sk.d["hatches"][hid]


def hatch_lines(sk, h: dict) -> dict[str, Any]:
    """Geometry of a hatch for display / export: the boundary rings and clipped hatch segments (crossing-number clip against outer minus holes)."""
    from . import regions
    found = regions.region_containing_edges(sk, h["edges"])
    if found is None:
        return {"rings": [], "segments": [], "broken": True}
    rings = [list(found["outer"]["points"])] + [list(h["points"]) for h in found["holes"]]
    segs: list[list[tuple[float, float]]] = []
    if h["pattern"] == "solid":
        return {"rings": rings, "segments": [], "broken": False}
    angs = [h["angle"]] + ([h["angle"] + 90.0] if h["pattern"] == "cross" else [])
    xs = [p[0] for r in rings for p in r]
    ys = [p[1] for r in rings for p in r]
    c = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
    R = G.dist((min(xs), min(ys)), (max(xs), max(ys))) / 2 + h["spacing"]
    import math
    for a in angs:
        d = (math.cos(math.radians(a)), math.sin(math.radians(a)))
        n = (-d[1], d[0])
        k = int(R // h["spacing"])
        for i in range(-k, k + 1):
            o = (c[0] + n[0] * i * h["spacing"], c[1] + n[1] * i * h["spacing"])
            hits = []
            for ring in rings:
                for j in range(len(ring)):
                    p, q = ring[j], ring[(j + 1) % len(ring)]
                    x = G.line_line(G.sub(o, G.mul(d, 2 * R)), G.add(o, G.mul(d, 2 * R)), p, q)
                    if x is not None:
                        hits.append(G.dot(G.sub(x, o), d))
            hits.sort()
            for j in range(0, len(hits) - 1, 2):
                if h["pattern"] == "dots":
                    segs.append([G.add(o, G.mul(d, (hits[j] + hits[j + 1]) / 2))] * 2)
                else:
                    segs.append([G.add(o, G.mul(d, hits[j])), G.add(o, G.mul(d, hits[j + 1]))])
    return {"rings": rings, "segments": segs, "broken": False}


# --------------------------------------------------------------------------------------------------------------- blocks
def define_block(sk, name: str, ids: Iterable[str], base: Sequence[float] = (0.0, 0.0)) -> dict:
    """Store the selected geometry (relative to ``base``) as a reusable block. The original stays where it is."""
    name = _text(name, "block name")
    if name in sk.d["blocks"]:
        raise ProfilerError("block %r already exists" % name)
    vs, es = ops.resolve(sk, ids)
    if not es:
        raise ProfilerError("select edges to make a block")
    for e in es:
        vs |= set(ops.edge_vertices(sk, e))
    b = (float(base[0]), float(base[1]))
    ids_v = {v: {"x": sk.pos(v)[0] - b[0], "y": sk.pos(v)[1] - b[1]} for v in vs}
    edges = {}
    scalars = {}
    for e in es:
        ed = {k: (list(v) if isinstance(v, list) else v) for k, v in sk.edge(e).items()}
        for key in ("r", "rx", "ry"):
            if key in ed:
                scalars[ed[key]] = sk.d["scalars"][ed[key]]
        edges[e] = ed
    cons = [{k: (list(v) if isinstance(v, list) else v) for k, v in c.items()} for c in sk.d["constraints"]
            if c["refs"] and all(r in vs or r in es for r in c["refs"]) and c["kind"] not in ("fixed",)]
    sk.d["blocks"][name] = {"name": name, "vertices": ids_v, "edges": edges, "scalars": scalars, "constraints": cons, "instances": []}
    return {"block": name, "edges": len(edges)}


def insert_block(sk, name: str, at: Sequence[float], *, rotation: float = 0.0, scale: float = 1.0, mirror: bool = False) -> dict:
    """Insert an independent copy of a block as a *rigid group* (so it moves as one) and record the instance. Edits to the block definition do not rewrite
    existing instances - they are real geometry that can be edited or exploded (ungrouped)."""
    blk = sk.d["blocks"].get(name)
    if blk is None:
        raise ProfilerNotFound("no block %r" % name)
    return place_block(sk, blk, name, at, rotation=rotation, scale=scale, mirror=mirror)


def snapshot_geometry(sk, ids: Iterable[str], base: Sequence[float]) -> dict:
    """The block-shaped snapshot used by the clipboard (same format as a block definition, but not stored in the document)."""
    name = "_clip_" + new_id()[:8]
    define_block(sk, name, ids, base)
    return sk.d["blocks"].pop(name)


def place_block(sk, blk: dict, name: str, at: Sequence[float], *, rotation: float = 0.0, scale: float = 1.0, mirror: bool = False, group: bool = True,
                record: bool = True) -> dict:
    import math
    if scale <= 0:
        raise ProfilerError("block scale must be positive")
    c, s = math.cos(math.radians(rotation)), math.sin(math.radians(rotation))
    vmap, emap, smap = {}, {}, {}
    for vid, v in blk["vertices"].items():
        x, y = v["x"] * scale * (-1 if mirror else 1), v["y"] * scale
        vmap[vid] = sk.add_vertex((at[0] + x * c - y * s, at[1] + x * s + y * c), reuse=False)
    for sid, val in blk["scalars"].items():
        smap[sid] = sk.new_scalar(val * scale)
    for eid, ed in blk["edges"].items():
        ne = {k: (list(v) if isinstance(v, list) else v) for k, v in ed.items()}
        ne["v"] = [vmap[x] for x in ed.get("v", [])]
        if "c" in ne:
            ne["c"] = vmap[ne["c"]]
        if "handles" in ne:
            ne["handles"] = [[vmap[x] for x in h] for h in ed["handles"]]
        for key in ("r", "rx", "ry"):
            if key in ne:
                ne[key] = smap[ne[key]]
        if "rot" in ne:
            ne["rot"] = ne["rot"] + rotation
        if mirror and ne["type"] == "arc":
            ne["v"] = ne["v"][::-1]
        ne["locked"] = False
        nid = new_id()
        sk.d["edges"][nid] = ne
        emap[eid] = nid
    quarter = round(rotation / 90.0)
    on_axis = abs(rotation - quarter * 90.0) < 1e-9
    dropped = 0
    for cn in blk["constraints"]:
        nc = {k: (list(v) if isinstance(v, list) else v) for k, v in cn.items()}
        axis_bound = nc["kind"] in ("horizontal", "vertical", "hdistance", "vdistance")
        if axis_bound and nc["kind"] in ("hdistance", "vdistance") and (not on_axis or quarter % 4 != 0 or mirror):
            dropped += 1                                     # signed axis distances do not survive rotation / mirroring; the rigid group holds the shape
            continue
        if axis_bound and nc["kind"] in ("horizontal", "vertical"):
            if not on_axis:
                dropped += 1
                continue
            if quarter % 2:                                  # a quarter turn swaps horizontal and vertical
                nc["kind"] = "vertical" if nc["kind"] == "horizontal" else "horizontal"
        nc["id"] = new_id()
        nc["refs"] = [vmap.get(r) or emap.get(r) for r in cn["refs"]]
        if None in nc["refs"]:
            continue
        if nc["kind"] in ("distance", "hdistance", "vdistance", "radius", "diameter", "point_line_distance", "ellipse_rx", "ellipse_ry") and nc.get("value") is not None and not isinstance(nc["value"], list):
            nc["value"] = nc["value"] * scale
        sk.d["constraints"].append(nc)
    gid, inst = None, {"id": None}
    if group:
        gid = ops.make_group(sk, list(emap.values()), "rigid", name="%s #%d" % (name, len(blk["instances"]) + 1))
    if record:
        inst = {"id": new_id(), "at": [float(at[0]), float(at[1])], "rotation": float(rotation), "scale": float(scale), "group": gid}
        blk["instances"].append(inst)
    sk.settle()
    return {"group": gid, "edges": list(emap.values()), "vertices": list(vmap.values()), "instance": inst["id"], "dropped_axis_constraints": dropped}


# ---------------------------------------------------------------------------------------------------------------- units
def set_unit(sk, new_unit: str, *, convert: bool = True) -> dict:
    """Change the document unit. ``convert`` rescales all geometry, literal parameter values and driving dimension values so the *physical* size is kept
    (formulas are unit-aware and re-evaluate); ``convert=False`` only relabels (the numbers stay and the physical size changes)."""
    from . import params as PM
    if new_unit not in U.METERS_PER_UNIT or new_unit not in ("mm", "cm", "m", "in", "ft"):
        raise ProfilerError("unsupported unit %r" % new_unit)
    old = sk.unit
    if new_unit == old:
        return {"unit": old, "factor": 1.0}
    f = U.METERS_PER_UNIT[old] / U.METERS_PER_UNIT[new_unit]
    if convert:
        for v in sk.vertices.values():
            v["x"] *= f
            v["y"] *= f
        for sid in sk.d["scalars"]:
            sk.d["scalars"][sid] *= f
        for p in sk.d["params"].values():
            if p["type"] == "length":
                for k in ("value", "min", "max"):
                    if p.get(k) is not None:
                        p[k] *= f
        for d in sk.d["dimensions"].values():
            if d.get("value") is not None and d["type"] not in ("angular", "slope"):
                d["value"] *= f
            d["offset"] = d.get("offset", 8.0) * f
        for c in sk.d["constraints"]:
            if c["kind"] in ("distance", "hdistance", "vdistance", "radius", "diameter", "point_line_distance", "ellipse_rx", "ellipse_ry") and isinstance(c.get("value"), (int, float)):
                c["value"] *= f
            if c["kind"] == "midpoint_at" and isinstance(c.get("value"), list):
                c["value"] = [x * f for x in c["value"]]
        for t in sk.d["texts"].values():
            t["xy"] = [t["xy"][0] * f, t["xy"][1] * f]
            t["height"] *= f
        for c in sk.d["comments"].values():
            c["xy"] = [c["xy"][0] * f, c["xy"][1] * f]
        sk.d["view"]["grid"] *= f
        sk.d["view"]["origin"] = [x * f for x in sk.d["view"]["origin"]]
        for st in sk.d["styles"]["dimension"].values():
            for k in ("text_height", "arrow_size", "ext_gap", "ext_over"):
                st[k] *= f
        for h in sk.d.get("hatches", {}).values():
            h["spacing"] *= f
        sk.d["tolerance"] *= f
    sk.d["unit"] = new_unit
    PM.apply(sk) if sk.d["params"] else None
    return {"unit": new_unit, "factor": f, "converted": convert}
