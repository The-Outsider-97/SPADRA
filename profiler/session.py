"""A Profiler editing session: every edit is a transaction, undoable, and either completes or leaves the sketch exactly as it was.

``Session.run(op, args)`` is the single mutation entry point. It clones the sketch, runs the operation, regenerates linked patterns, re-checks structure and
only then commits (push undo, bump revision, mark dirty). Any exception - a solver conflict, a bad argument, an internal error - restores the clone, so the
last valid geometry always survives (authoritative PDF p.5/p.6). Read-only operations (queries, previews) never touch undo or the dirty flag.
"""
from __future__ import annotations

import re

import json
import math
import threading
import time
from collections import deque
from typing import Any, Callable, Sequence

from src.utils.spatial_formats.common.ids import new_id

from . import annotate as AN
from . import dims as D
from . import geom as G
from . import ops, regions, render as RN, select as SEL, snap as SN, topo, validate as V
from . import params as PM
from .errors import ProfilerError, ProfilerNotFound
from .model import Sketch

MAX_UNDO = 200
MAX_SESSIONS = 16


# ------------------------------------------------------------------------------------------------------------ argument helpers
def _pt(a: dict, k: str) -> tuple[float, float]:
    v = a.get(k)
    try:
        x, y = float(v[0]), float(v[1])
    except (TypeError, ValueError, IndexError, KeyError):
        raise ProfilerError("'%s' must be a point [x, y]" % k)
    if not (math.isfinite(x) and math.isfinite(y)):
        raise ProfilerError("'%s' must be finite" % k)
    return (x, y)


def _num(a: dict, k: str, default: Any = ..., *, positive: bool = False, nonzero: bool = False, integer: bool = False, minimum: float | None = None) -> float:
    if k not in a or a[k] is None:
        if default is ...:
            raise ProfilerError("'%s' is required" % k)
        return default
    try:
        v = float(a[k])
    except (TypeError, ValueError):
        raise ProfilerError("'%s' must be a number" % k)
    if not math.isfinite(v):
        raise ProfilerError("'%s' must be finite" % k)
    if positive and v <= 0:
        raise ProfilerError("'%s' must be greater than zero" % k)
    if nonzero and v == 0:
        raise ProfilerError("'%s' cannot be zero" % k)
    if minimum is not None and v < minimum:
        raise ProfilerError("'%s' must be at least %g" % (k, minimum))
    if integer:
        if abs(v - round(v)) > 1e-9:
            raise ProfilerError("'%s' must be a whole number" % k)
        return int(round(v))
    return v


def _ids(a: dict, k: str = "ids", minimum: int = 1) -> list[str]:
    v = a.get(k)
    if not isinstance(v, (list, tuple)) or not all(isinstance(x, str) and x for x in v):
        raise ProfilerError("'%s' must be a list of ids" % k)
    if len(v) < minimum:
        raise ProfilerError("select at least %d item%s" % (minimum, "" if minimum == 1 else "s"))
    return list(v)


def _str(a: dict, k: str, default: Any = ...) -> str:
    v = a.get(k, default)
    if v is ...:
        raise ProfilerError("'%s' is required" % k)
    if not isinstance(v, str) or (not v and default is ...):
        raise ProfilerError("'%s' must be text" % k)
    return v


def _style(sk, a: dict) -> dict:
    out: dict[str, Any] = {}
    layer = a.get("layer", "0")
    if sk.layer(layer)["locked"]:
        raise ProfilerError("layer %r is locked: unlock it or draw on another layer" % layer)
    out["layer"] = layer
    if a.get("construction"):
        out["construction"] = True
    if a.get("color"):
        out["color"] = AN.norm_color(a["color"])
    return out


def _slim(r) -> dict:
    return {"status": r.status, "dof": r.dof, "conflicting": list(r.conflicting), "redundant": list(r.redundant)}


def _solved(sk) -> dict:
    return _slim(sk.settle())


def _default(o: Any):
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    try:
        import numpy as np
        if isinstance(o, np.generic):
            return o.item()
        if isinstance(o, np.ndarray):
            return o.tolist()
    except ImportError:
        pass
    if isinstance(o, tuple):
        return list(o)
    raise TypeError("not JSON serialisable: %r" % type(o))


def jsonable(x: Any) -> Any:
    return json.loads(json.dumps(x, default=_default))


# ------------------------------------------------------------------------------------------------------------- operations
Handler = Callable[[Sketch, dict, "Session"], Any]
OPERATIONS: dict[str, tuple[Handler, bool, str]] = {}      # name -> (handler, mutates, label)


def op(name: str, label: str | None = None, *, mutates: bool = True):
    def deco(fn: Handler) -> Handler:
        OPERATIONS[name] = (fn, mutates, label or name.replace("_", " ").capitalize())
        return fn
    return deco


# ---- draw
@op("line", "Line")
def _line(sk, a, S):
    return {"edge": sk.add_line(_pt(a, "a"), _pt(a, "b"), **_style(sk, a))}


@op("construction_line", "Construction line")
def _cline(sk, a, S):
    return {"edge": sk.add_line(_pt(a, "a"), _pt(a, "b"), **{**_style(sk, a), "construction": True})}


@op("polyline", "Polyline")
def _poly(sk, a, S):
    pts = a.get("points")
    if not isinstance(pts, list) or len(pts) < 2:
        raise ProfilerError("a polyline needs at least two points")
    return {"edges": sk.add_polyline([_pt({"p": p}, "p") for p in pts], closed=bool(a.get("closed")), constrain=bool(a.get("constrain")), **_style(sk, a))}


@op("rectangle", "Rectangle")
def _rect(sk, a, S):
    return {"edges": sk.add_rectangle(_pt(a, "p1"), _pt(a, "p2"), constrain=a.get("constrain", True), **_style(sk, a))}


@op("circle", "Circle")
def _circle(sk, a, S):
    st = _style(sk, a)
    if "p3" in a:
        try:
            c, r = G.circle_through(_pt(a, "p1"), _pt(a, "p2"), _pt(a, "p3"))
        except ValueError as exc:
            raise ProfilerError(str(exc))
        return {"edge": sk.add_circle(c, r, **st)}
    if "p1" in a and "p2" in a:
        p1, p2 = _pt(a, "p1"), _pt(a, "p2")
        return {"edge": sk.add_circle(G.mid(p1, p2), G.dist(p1, p2) / 2, **st)}
    c = _pt(a, "center")
    r = G.dist(c, _pt(a, "through")) if "through" in a else _num(a, "radius", positive=True)
    return {"edge": sk.add_circle(c, r, **st)}


@op("ellipse", "Ellipse")
def _ellipse(sk, a, S):
    return {"edge": sk.add_ellipse(_pt(a, "center"), _num(a, "rx", positive=True), _num(a, "ry", positive=True), _num(a, "rotation", 0.0), **_style(sk, a))}


@op("polygon", "Polygon")
def _polygon(sk, a, S):
    return {"edges": sk.add_polygon(_pt(a, "center"), _num(a, "radius", positive=True), int(_num(a, "sides", integer=True, minimum=3)), rotation=_num(a, "rotation", 0.0),
                                    circumscribed=bool(a.get("circumscribed")), **_style(sk, a))}


@op("slot", "Slot")
def _slot(sk, a, S):
    return {"edges": sk.add_slot(_pt(a, "p1"), _pt(a, "p2"), _num(a, "width", positive=True), rounded=a.get("rounded", True), **_style(sk, a))}


@op("arc_start_end_radius", "Arc (start, end, radius)")
def _arc1(sk, a, S):
    return {"edge": sk.add_arc_start_end_radius(_pt(a, "start"), _pt(a, "end"), _num(a, "radius", positive=True), large=bool(a.get("large")), ccw=a.get("ccw", True), **_style(sk, a))}


@op("arc_center_ends", "Arc (centre, start, end)")
def _arc2(sk, a, S):
    return {"edge": sk.add_arc_center_ends(_pt(a, "center"), _pt(a, "start"), _pt(a, "end"), ccw=a.get("ccw", True), **_style(sk, a))}


@op("arc_three_point", "Arc (3 points)")
def _arc3(sk, a, S):
    s, m, e = _pt(a, "start"), _pt(a, "mid"), _pt(a, "end")
    try:
        c, r = G.circle_through(s, m, e)
    except ValueError as exc:
        raise ProfilerError(str(exc))
    ccw = G.cross(G.sub(m, s), G.sub(e, m)) > 0
    return {"edge": sk.add_arc_center_ends(c, s, e, ccw=ccw, **_style(sk, a))}


@op("spline", "Spline")
def _spline(sk, a, S):
    pts = [_pt({"p": p}, "p") for p in (a.get("points") or [])]
    return {"edge": sk.add_spline(_str(a, "mode"), pts, degree=int(_num(a, "degree", 3, integer=True, minimum=1)), handles=a.get("handles"), **_style(sk, a))}


def _corner_edges(sk, a) -> list[str]:
    """The two edges of a corner: given directly, or found from the corner point (the one real line or arc on each side of it)."""
    if a.get("edges"):
        return _ids(a, "edges", 2)
    v = a.get("vertex")
    if not v or v not in sk.d["vertices"]:
        raise ProfilerError("pick a corner (a point where two lines or arcs meet), or the two lines")
    near = [eid for eid in sk.edges if v in sk.edge(eid)["v"] and sk.edge(eid)["type"] in ("line", "arc") and not sk.edge(eid).get("construction")]
    if len(near) != 2:
        if not near:
            kept = any(v in sk.edge(eid)["v"] for eid in sk.edges)
            raise ProfilerError("that corner is already rounded or cut: pick the new line or arc next to it" if kept else "no line or arc ends at that point")
        raise ProfilerError("that point joins %d line or arc, not two: pick the two lines instead" % len(near) if len(near) == 1 else "that point joins %d lines or arcs, not two: pick the two lines instead" % len(near))
    return near


@op("fillet", "Fillet")
def _fillet(sk, a, S):
    e = _corner_edges(sk, a)
    return topo.fillet(sk, e[0], e[1], _num(a, "radius", positive=True), associative=a.get("associative", True), param=a.get("param"))


@op("chamfer", "Chamfer")
def _chamfer(sk, a, S):
    e = _corner_edges(sk, a)
    return topo.chamfer(sk, e[0], e[1], _num(a, "d1", positive=True), _num(a, "d2", None, positive=True), associative=a.get("associative", True))


@op("merge_vertices", "Merge vertices")
def _merge(sk, a, S):
    return topo.merge_vertices(sk, _ids(a, "ids", 2), at=a.get("at", "first"))


# ---- modify
@op("move", "Move")
def _move(sk, a, S):
    if "from" in a and "to" in a:
        f, t = _pt(a, "from"), _pt(a, "to")
        return ops.translate(sk, _ids(a), t[0] - f[0], t[1] - f[1])
    return ops.translate(sk, _ids(a), _num(a, "dx"), _num(a, "dy"))


@op("drag_vertex", "Move point")
def _drag(sk, a, S):
    return ops.drag_vertex(sk, _str(a, "vertex"), _pt(a, "to"))


@op("rotate", "Rotate")
def _rotate(sk, a, S):
    return ops.rotate(sk, _ids(a), _num(a, "angle"), _pt(a, "about") if "about" in a else (0.0, 0.0), degrees=a.get("degrees", True), release=bool(a.get("release")))


@op("scale", "Resize")
def _scale(sk, a, S):
    return ops.scale(sk, _ids(a), _num(a, "factor", positive=True), _pt(a, "about") if "about" in a else (0.0, 0.0))


@op("mirror", "Mirror")
def _mirror(sk, a, S):
    return ops.mirror(sk, _ids(a), _pt(a, "a"), _pt(a, "b"), copy=a.get("copy", True), constrain=a.get("constrain", True))


@op("copy", "Copy")
def _copy(sk, a, S):
    return ops.copy(sk, _ids(a), _num(a, "dx", 0.0), _num(a, "dy", 0.0))


@op("align", "Align")
def _align(sk, a, S):
    t = a.get("target")
    return ops.align(sk, _ids(a), _str(a, "how"), t if isinstance(t, (list, tuple)) else _num(a, "target"))


@op("stretch", "Stretch")
def _stretch(sk, a, S):
    w = a.get("window")
    if not isinstance(w, (list, tuple)) or len(w) != 4:
        raise ProfilerError("'window' must be [x0, y0, x1, y1]")
    return ops.stretch(sk, [float(x) for x in w], _num(a, "dx"), _num(a, "dy"))


@op("array", "Array")
def _array(sk, a, S):
    spec = dict(a.get("spec") or {k: v for k, v in a.items() if k not in ("ids", "kind", "linked", "spec")})
    return ops.array(sk, _ids(a), _str(a, "kind"), linked=bool(a.get("linked")), **spec)


@op("delete", "Delete")
def _delete(sk, a, S):
    return ops.delete(sk, _ids(a))


@op("trim", "Trim")
def _trim(sk, a, S):
    return topo.trim(sk, _str(a, "edge"), _pt(a, "pick"), a.get("boundaries"))


@op("extend", "Extend")
def _extend(sk, a, S):
    return topo.extend(sk, _str(a, "edge"), _pt(a, "near"), a.get("boundaries"))


@op("split", "Split")
def _split(sk, a, S):
    return topo.split(sk, _str(a, "edge"), _pt(a, "point"), _pt(a, "point2") if "point2" in a else None)


@op("split_at_intersections", "Split at intersections")
def _splitx(sk, a, S):
    return topo.split_at_intersections(sk, a.get("edges"))


@op("cut", "Cut")
def _cut(sk, a, S):
    return topo.cut(sk, a.get("ids"), _pt(a, "a"), _pt(a, "b"))


@op("offset", "Offset")
def _offset(sk, a, S):
    return topo.offset(sk, _ids(a, "edges"), _num(a, "distance", positive=True), side=a.get("side", "left"), parametric=a.get("parametric", True), param=a.get("param"))


@op("join", "Join")
def _join(sk, a, S):
    e = _ids(a, "edges", 2)
    return topo.join(sk, e[0], e[1])


@op("weld", "Weld")
def _weld(sk, a, S):
    return topo.weld(sk, _num(a, "tol", None, positive=True))


@op("connect", "Connect")
def _connect(sk, a, S):
    v = _ids(a, "vertices", 2)
    return {"edge": topo.connect(sk, v[0], v[1])}


@op("attach", "Attach")
def _attach(sk, a, S):
    return topo.attach(sk, _str(a, "vertex"), _str(a, "target"))


@op("region_boolean", "Region boolean")
def _boolean(sk, a, S):
    return topo.region_boolean(sk, _str(a, "operation"), _ids(a, "a"), _ids(a, "b"), replace=a.get("replace", True))


@op("group", "Group")
def _group(sk, a, S):
    return {"group": ops.make_group(sk, _ids(a), a.get("kind", "rigid"), a.get("name"))}


@op("ungroup", "Ungroup")
def _ungroup(sk, a, S):
    ops.ungroup(sk, _str(a, "group"))


@op("make_component", "Make component")
def _component(sk, a, S):
    return {"component": ops.make_component(sk, _ids(a), _str(a, "name"), origin=_pt(a, "origin") if "origin" in a else None, params=a.get("params") or ())}


@op("instantiate_component", "Insert component")
def _inst(sk, a, S):
    return ops.instantiate_component(sk, _str(a, "component"), _pt(a, "at"))


# ---- constraints
@op("constrain", "Add constraint")
def _constrain(sk, a, S):
    extra = {k: v for k, v in a.items() if k not in ("kind", "refs", "value")}
    cid = sk.add_constraint(_str(a, "kind"), _ids(a, "refs"), a.get("value"), **extra)
    return {"constraint": cid, "solve": _solved(sk)}


@op("pin", "Fix point")
def _pin(sk, a, S):
    cid = sk.pin(_str(a, "vertex"))
    return {"constraint": cid}


@op("remove_constraint", "Remove constraint")
def _rmcon(sk, a, S):
    sk.remove_constraint(_str(a, "constraint"))
    return {"solve": _solved(sk)}


@op("set_constraint_enabled", "Toggle constraint")
def _concon(sk, a, S):
    c = sk.constraint(_str(a, "constraint"))
    c["enabled"] = bool(a.get("enabled", True))
    return {"solve": _solved(sk)}


# ---- dimensions
@op("dimension", "Dimension")
def _dim(sk, a, S):
    opts = {k: a[k] for k in ("offset", "style", "layer", "tolerance", "name", "axis") if k in a}
    did = D.add_dimension(sk, _str(a, "type"), _ids(a, "refs"), mode=a.get("mode", "reference"), value=a.get("value"), anchor=a.get("anchor", "none"), **opts)
    return {"dimension": did, "measure": D.measure(sk, D.get(sk, did))}


@op("dimension_auto", "Dimension")
def _dimauto(sk, a, S):
    """Dimension one or two picked points, lines, arcs or circles; the type and the mode are chosen from what was picked."""
    out = D.add_auto(sk, _ids(a, "refs", 1), prefer=a.get("prefer", "driving"))
    out["solve"] = _solved(sk)
    return out


@op("dimension_value", "Edit dimension")
def _dimval(sk, a, S):
    return D.set_value(sk, _str(a, "dimension"), a.get("value"), anchor=a.get("anchor"), expr=a.get("expr"), locked_vertices=a.get("locked_vertices") or ())


@op("dimension_mode", "Dimension mode")
def _dimmode(sk, a, S):
    D.set_mode(sk, _str(a, "dimension"), _str(a, "mode"))


@op("dimension_remove", "Delete dimension")
def _dimrm(sk, a, S):
    D.remove(sk, _str(a, "dimension"))
    return {"solve": _solved(sk)}


@op("dimension_move", "Move dimension")
def _dimmv(sk, a, S):
    D.get(sk, _str(a, "dimension"))["offset"] = _num(a, "offset")


@op("dimension_style", "Dimension style")
def _dimstyle(sk, a, S):
    name = _str(a, "name")
    base = sk.d["styles"]["dimension"].get(name) or dict(sk.d["styles"]["dimension"]["default"])
    allowed = set(base)
    bad = [k for k in (a.get("style") or {}) if k not in allowed]
    if bad:
        raise ProfilerError("unknown dimension style field(s): %s" % ", ".join(bad))
    base.update(a.get("style") or {})
    if "color" in base:
        base["color"] = AN.norm_color(base["color"]) or "#F6F6F6"
    sk.d["styles"]["dimension"][name] = base
    return {"style": base}


@op("chain", "Dimension chain")
def _chain(sk, a, S):
    cid = D.add_chain(sk, _ids(a, "vertices", 2), style=a.get("style", "chain"), with_total=a.get("with_total", True), along=a.get("along", "aligned"))
    return {"chain": cid, "segments": D.segment_values(sk, cid)}


@op("chain_lock_segment", "Lock segment")
def _chlock(sk, a, S):
    D.lock_segment(sk, _str(a, "chain"), int(_num(a, "index", integer=True, minimum=0)), bool(a.get("locked", True)))


@op("chain_end_locks", "Chain end locks")
def _chends(sk, a, S):
    D.set_end_locks(sk, _str(a, "chain"), a.get("vertices") or [])


@op("chain_total", "Chain total")
def _chtotal(sk, a, S):
    return D.set_chain_total(sk, _str(a, "chain"), _num(a, "total", positive=True), policy=a.get("policy", "min_change"))


@op("chain_eq", "Equal segments")
def _cheq(sk, a, S):
    return D.set_eq(sk, _str(a, "chain"), bool(a.get("on", True)), release=bool(a.get("release")))


# ---- parameters
@op("param_add", "Add parameter")
def _padd(sk, a, S):
    kw = {k: a[k] for k in ("scope", "min", "max", "description", "unit") if k in a}
    PM.add_param(sk, _str(a, "name"), a.get("type", "length"), a.get("value"), a.get("expression"), **kw)
    return PM.apply(sk)


@op("param_remove", "Remove parameter")
def _prm(sk, a, S):
    PM.remove_param(sk, _str(a, "name"))
    return PM.apply(sk)


@op("param_set", "Set parameter")
def _pset(sk, a, S):
    return PM.set_param(sk, _str(a, "name"), value=a.get("value"), expression=a.get("expression", False))


@op("param_link", "Link dimension")
def _plink(sk, a, S):
    PM.link_dimension(sk, _str(a, "dimension"), _str(a, "name"))
    return PM.apply(sk)


@op("param_from_dimension", "Dimension to parameter")
def _pfrom(sk, a, S):
    name = PM.dimension_to_parameter(sk, _str(a, "dimension"), _str(a, "name"), expression=a.get("expression"))
    return {"parameter": name, **PM.apply(sk)}


@op("variant_save", "Save variant")
def _vsave(sk, a, S):
    PM.save_variant(sk, _str(a, "name"))


@op("variant_apply", "Apply variant")
def _vapply(sk, a, S):
    return PM.apply_variant(sk, _str(a, "name"))


# ---- annotate / appearance
@op("color", "Colour")
def _color(sk, a, S):
    return AN.set_color(sk, _ids(a), a.get("color"))


@op("default_color", "Default colour")
def _dcolor(sk, a, S):
    AN.set_default_color(sk, _str(a, "color"))


@op("layer_add", "Add layer")
def _ladd(sk, a, S):
    return AN.add_layer(sk, _str(a, "name"), color=a.get("color"))


@op("layer_rename", "Rename layer")
def _lren(sk, a, S):
    AN.rename_layer(sk, _str(a, "name"), _str(a, "new_name"))


@op("layer_set", "Layer")
def _lset(sk, a, S):
    kw = {k: a[k] for k in ("visible", "locked", "color") if k in a}
    return AN.set_layer(sk, _str(a, "name"), **kw)


@op("layer_delete", "Delete layer")
def _ldel(sk, a, S):
    return AN.delete_layer(sk, _str(a, "name"), move_to=a.get("move_to", "0"))


@op("move_to_layer", "Move to layer")
def _lmove(sk, a, S):
    return AN.move_to_layer(sk, _ids(a), _str(a, "layer"))


@op("construction", "Construction")
def _constr(sk, a, S):
    return AN.set_construction(sk, _ids(a), bool(a.get("on", True)))


@op("lock", "Lock")
def _lock(sk, a, S):
    return AN.set_locked(sk, _ids(a), bool(a.get("on", True)))


@op("comment_add", "Add comment")
def _cadd(sk, a, S):
    return AN.add_comment(sk, _str(a, "text"), xy=_pt(a, "xy") if "xy" in a else None, vertex=a.get("vertex"), edge=a.get("edge"), t=_num(a, "t", 0.5), author=a.get("author", S.user))


@op("comment_edit", "Edit comment")
def _cedit(sk, a, S):
    return AN.edit_comment(sk, _str(a, "comment"), _str(a, "text"))


@op("comment_reply", "Reply to comment")
def _creply(sk, a, S):
    return AN.reply_comment(sk, _str(a, "comment"), _str(a, "text"), a.get("author", S.user))


@op("comment_resolve", "Resolve comment")
def _cres(sk, a, S):
    return AN.resolve_comment(sk, _str(a, "comment"), bool(a.get("resolved", True)))


@op("comment_move", "Move comment")
def _cmove(sk, a, S):
    return AN.move_comment(sk, _str(a, "comment"), _pt(a, "xy"))


@op("comment_delete", "Delete comment")
def _cdel(sk, a, S):
    AN.delete_comment(sk, _str(a, "comment"))


@op("text_add", "Add text")
def _tadd(sk, a, S):
    return {"text": AN.add_text(sk, _pt(a, "xy"), _str(a, "text"), height=a.get("height"), rotation=_num(a, "rotation", 0.0), layer=a.get("layer", "annotation"), color=a.get("color"))}


@op("text_edit", "Edit text")
def _tedit(sk, a, S):
    return AN.edit_text(sk, _str(a, "text_id"), **{k: v for k, v in a.items() if k not in ("text_id",)})


@op("text_delete", "Delete text")
def _tdel(sk, a, S):
    AN.delete_text(sk, _str(a, "text_id"))


@op("hatch_add", "Hatch")
def _hadd(sk, a, S):
    return {"hatch": AN.add_hatch(sk, _ids(a, "edges"), pattern=a.get("pattern", "lines"), angle=_num(a, "angle", 45.0), spacing=_num(a, "spacing", None, positive=True), color=a.get("color"), layer=a.get("layer", "0"))}


@op("hatch_remove", "Remove hatch")
def _hrm(sk, a, S):
    AN.remove_hatch(sk, _str(a, "hatch"))


@op("block_define", "Define block")
def _bdef(sk, a, S):
    return AN.define_block(sk, _str(a, "name"), _ids(a), _pt(a, "base") if "base" in a else (0.0, 0.0))


@op("block_insert", "Insert block")
def _bins(sk, a, S):
    return AN.insert_block(sk, _str(a, "name"), _pt(a, "at"), rotation=_num(a, "rotation", 0.0), scale=_num(a, "scale", 1.0, positive=True), mirror=bool(a.get("mirror")))


@op("set_unit", "Change unit")
def _unit(sk, a, S):
    return AN.set_unit(sk, _str(a, "unit"), convert=a.get("convert", True))


@op("rename", "Rename profile")
def _rename(sk, a, S):
    n = _str(a, "name").strip()
    if not n or len(n) > 120:
        raise ProfilerError("a profile name is 1-120 characters")
    sk.d["name"] = n


@op("view", "View settings")
def _view(sk, a, S):
    v = sk.d["view"]
    for k in ("grid", "snap_grid", "rulers", "axes", "origin"):
        if k in a:
            if k == "grid":
                v[k] = _num(a, k, positive=True)
            elif k == "origin":
                v[k] = list(_pt(a, k))
            else:
                v[k] = bool(a[k])
    if "precision" in a:
        sk.d["precision"] = int(_num(a, "precision", integer=True, minimum=0))


# ---- clipboard / session-level edits
@op("paste", "Paste")
def _paste(sk, a, S):
    if S.clipboard is None:
        raise ProfilerError("the clipboard is empty")
    at = _pt(a, "at") if "at" in a else (S.clipboard["origin"][0] + S.clipboard["step"], S.clipboard["origin"][1] + S.clipboard["step"])
    r = AN.place_block(sk, S.clipboard["block"], "clip", at, group=False, record=False)
    S.clipboard["step"] += max(sk.d["view"]["grid"], 1.0)
    return {"edges": r["edges"], "vertices": r["vertices"]}


@op("cut_to_clipboard", "Cut")
def _cutclip(sk, a, S):
    _clip(sk, a, S)
    return ops.delete(sk, _ids(a))


def _clip(sk, a, S):
    ids = _ids(a)
    vs, es = ops.resolve(sk, ids)
    if not es:
        raise ProfilerError("select edges to copy")
    b = sk.bounds(list(es))
    S.clipboard = {"block": AN.snapshot_geometry(sk, ids, (b[0], b[1])), "origin": [b[0], b[1]], "step": max(sk.d["view"]["grid"], 1.0)}
    return {"edges": len(es)}


# ---- read-only queries
@op("copy_to_clipboard", "Copy", mutates=False)
def _copyclip(sk, a, S):
    return _clip(sk.clone(), a, S)


@op("render", mutates=False)
def _render(sk, a, S):
    return RN.render(sk, tol=a.get("tolerance"), include_hidden=bool(a.get("include_hidden")))


@op("check", mutates=False)
def _check(sk, a, S):
    return V.check(sk, a.get("purpose", "drafting"), axis=a.get("axis"), gap_tol=a.get("gap_tolerance"))


@op("repair_preview", mutates=False)
def _repprev(sk, a, S):
    return V.repair(sk, _str(a, "action"), purpose=a.get("purpose", "drafting"), apply=False, gap_tol=a.get("gap_tolerance"), axis=a.get("axis"))


@op("repair", "Repair")
def _repair(sk, a, S):
    return V.repair(sk, _str(a, "action"), purpose=a.get("purpose", "drafting"), apply=True, gap_tol=a.get("gap_tolerance"), axis=a.get("axis"))


@op("diagnostics", mutates=False)
def _diag(sk, a, S):
    return sk.diagnostics()


@op("regions", mutates=False)
def _regions(sk, a, S):
    r = regions.analyse(sk)
    return {"regions": [{"edges": x["edges"], "area": x["area"], "perimeter": x["perimeter"], "holes": len(x["holes"])} for x in r["regions"]], "dangling": r["dangling"],
            "crossings": r["crossings"]}


@op("readout", mutates=False)
def _readout(sk, a, S):
    return D.readout(sk, _str(a, "type"), _ids(a, "refs"))


@op("part_color", "Part colour")
def _part_color(sk, a, S):
    """The colour the part gets in SPADRA (a profile parameter named ``colour``). Empty removes it: the part then keeps its material colour."""
    c = a.get("color")
    if c in (None, ""):
        sk.d["appearance"] = {"color": None}
        return {"color": None}
    if not isinstance(c, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", c):
        raise ProfilerError("the part colour is a six-digit hex value such as #E8590C")
    sk.d["appearance"] = {"color": c.upper()}
    return {"color": c.upper()}


@op("params", mutates=False)
def _params(sk, a, S):
    return {"colour": (sk.d.get("appearance") or {}).get("color"), "parameters": PM.listing(sk), "graph": PM.graph(sk), "variants": sorted(sk.d.get("variants", {})), "blocks": sorted(sk.d["blocks"]), "components": sorted(k for k in sk.d["components"] if not k.startswith("_")),
            "styles": sorted(sk.d["styles"]["dimension"])}


@op("snap", mutates=False)
def _snap(sk, a, S):
    return SN.snap(sk, _pt(a, "cursor"), _num(a, "radius", positive=True), a.get("modes"), from_point=_pt(a, "from") if "from" in a else None, exclude=a.get("exclude") or (),
                   grid=_num(a, "grid", positive=True) if a.get("grid") else None)


@op("parse_point", mutates=False)
def _parse(sk, a, S):
    return SN.parse_point(_str(a, "text"), a.get("last"))


@op("select_window", mutates=False)
def _selwin(sk, a, S):
    r = a.get("rect")
    if not isinstance(r, (list, tuple)) or len(r) != 4:
        raise ProfilerError("'rect' must be [x0, y0, x1, y1]")
    return SEL.window(sk, r, a.get("mode", "window"), a.get("filters"))


@op("pick", mutates=False)
def _pick(sk, a, S):
    return SEL.pick(sk, _pt(a, "point"), _num(a, "radius", positive=True), a.get("filters"))


@op("select_connected", mutates=False)
def _selconn(sk, a, S):
    return {"edges": SEL.connected(sk, _ids(a))}


# --------------------------------------------------------------------------------------------------------------- session
class Session:
    def __init__(self, sketch: Sketch, *, path: str | None = None, user: str = "user") -> None:
        self.id = new_id()
        self.sk = sketch
        self.path = path
        self.user = user
        self.created = time.time()
        self.lock = threading.RLock()
        self.undo: deque[tuple[str, dict]] = deque(maxlen=MAX_UNDO)
        self.redo: list[tuple[str, dict]] = []
        self.last: tuple[str, dict] | None = None
        self.clipboard: dict | None = None
        self.dirty = False
        self.saved_digest: str | None = sketch.digest()
        self.autosave_digest: str | None = None
        self.meta: dict[str, Any] = {}            # file bookkeeping owned by sdrw_io / transfer

    # -- mutation
    def run(self, name: str, args: dict | None = None) -> dict[str, Any]:
        args = dict(args or {})
        spec = OPERATIONS.get(name)
        if spec is None:
            raise ProfilerError("unknown operation %r" % name, details={"known": sorted(OPERATIONS)})
        fn, mutates, label = spec
        with self.lock:
            if not mutates:
                return {"result": jsonable(fn(self.sk, args, self))}
            before = self.sk.clone()
            before_digest = before.digest()
            try:
                result = fn(self.sk, args, self)
                if self.sk.d.get("features"):
                    ops.regenerate_patterns(self.sk)
                D.refresh_unresolved(self.sk)
                self.sk.validate_structure()
                result = jsonable(result)
            except BaseException:
                self.sk.restore(before)
                raise
            changed = self.sk.digest() != before_digest
            if changed:
                self.sk.d["revision"] += 1
                self.undo.append((label, before.d))
                self.redo.clear()
                self.dirty = self.sk.digest() != self.saved_digest
                self.last = (name, args)
            return {"result": result, "changed": changed, "revision": self.sk.d["revision"], "status": self.status()}

    def undo_last(self) -> dict[str, Any]:
        with self.lock:
            if not self.undo:
                raise ProfilerError("nothing to undo")
            label, snap = self.undo.pop()
            self.redo.append((label, self.sk.to_dict()))
            self.sk.d = snap
            self.sk.d["revision"] += 1
            self.dirty = self.sk.digest() != self.saved_digest
            return {"undone": label, "status": self.status()}

    def redo_last(self) -> dict[str, Any]:
        with self.lock:
            if not self.redo:
                raise ProfilerError("nothing to redo")
            label, snap = self.redo.pop()
            self.undo.append((label, self.sk.to_dict()))
            self.sk.d = snap
            self.sk.d["revision"] += 1
            self.dirty = self.sk.digest() != self.saved_digest
            return {"redone": label, "status": self.status()}

    def repeat(self, overrides: dict | None = None) -> dict[str, Any]:
        with self.lock:
            if self.last is None:
                raise ProfilerError("there is no previous command to repeat")
            name, args = self.last
            return self.run(name, {**args, **(overrides or {})})

    def mark_saved(self) -> None:
        self.saved_digest = self.sk.digest()
        self.dirty = False

    def status(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.sk.d["name"], "revision": self.sk.d["revision"], "dirty": self.dirty, "path": self.path, "can_undo": bool(self.undo),
                "can_redo": bool(self.redo), "undo_label": self.undo[-1][0] if self.undo else None, "redo_label": self.redo[-1][0] if self.redo else None,
                "unit": self.sk.d["unit"], "entities": {"vertices": len(self.sk.d["vertices"]), "edges": len(self.sk.d["edges"])},
                "can_repeat": self.last is not None, "clipboard": self.clipboard is not None}


class SessionStore:
    def __init__(self) -> None:
        self._s: dict[str, Session] = {}
        self._lock = threading.RLock()

    def add(self, s: Session) -> Session:
        with self._lock:
            if len(self._s) >= MAX_SESSIONS:
                raise ProfilerError("too many open profiles (%d): close one first" % MAX_SESSIONS)
            self._s[s.id] = s
            return s

    def get(self, sid: str) -> Session:
        with self._lock:
            s = self._s.get(sid)
        if s is None:
            raise ProfilerNotFound("no open profile %s" % sid)
        return s

    def close(self, sid: str) -> None:
        with self._lock:
            self._s.pop(sid, None)

    def all(self) -> list[Session]:
        with self._lock:
            return list(self._s.values())


STORE = SessionStore()
