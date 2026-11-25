"""Dimensions, anchors, chains and EQ.

Three independent decisions (authoritative PDF p.4): *what is measured* (``type`` + ``refs``), *whether the value drives geometry* (``mode``), and *which
geometry is allowed to move* (an edit ``anchor`` or a chain's locked vertices). A dimension is a **view** of the geometry; only ``driving`` and
``formula`` dimensions contribute a solver constraint (``dim:<id>``), ``reference`` and ``temporary`` ones never do.

Locks, in the authoritative sense: the *end locks* choose which end stays put when the value is edited (``anchor`` = ``start`` | ``end``, plus ``center`` as the
revised PDF's separate symmetric option, plus ``none``); the *middle lock* is the value lock - a locked (driving) segment keeps its value when an adjacent
dimension is edited and so carries its far end along, an unlocked (reference) segment absorbs the move. None of these silently overrides a pinned
vertex or another driving dimension: such an edit is rejected with the conflicting constraints named.
"""
from __future__ import annotations

import math
from typing import Any, Sequence

from src.utils.spatial_formats.common.ids import new_id

from . import geom as G
from .errors import ProfilerConflict, ProfilerError, ProfilerNotFound

TYPES = ("aligned", "horizontal", "vertical", "length", "radius", "diameter", "angular", "arc_length", "chord", "spline_length", "ordinate",
         "center_distance", "min_distance", "perpendicular", "slope", "area", "perimeter", "bounding")
MODES = ("reference", "driving", "formula", "temporary")
ANCHORS = ("none", "start", "end", "center")
DRIVING_KIND = {"aligned": "distance", "length": "distance", "center_distance": "distance", "horizontal": "hdistance", "vertical": "vdistance",
                "radius": "radius", "diameter": "diameter", "angular": "angle", "perpendicular": "point_line_distance"}
LINEAR = ("aligned", "horizontal", "vertical", "length", "center_distance")


def eligible(dim_type: str) -> bool:
    return dim_type in DRIVING_KIND


# ----------------------------------------------------------------------------------------------------------------- measuring
def _normalise_refs(sk, dim_type: str, refs: Sequence[str]) -> tuple[list[str], dict]:
    """Bind a dimension to semantic geometry. Linear dimensions bind to two vertex ids even when given a single edge or two circles."""
    refs, extra = list(refs), {}
    V, E = sk.vertices, sk.edges
    if dim_type in ("aligned", "horizontal", "vertical", "length"):
        if len(refs) == 1 and refs[0] in E:
            ends = sk.edge_ends(refs[0])
            if ends is None or E[refs[0]]["type"] not in ("line",):
                raise ProfilerError("%s needs a straight line or two points" % dim_type)
            extra["edge"], refs = refs[0], list(ends)
        if len(refs) != 2 or any(r not in V for r in refs):
            raise ProfilerError("%s needs two vertices or one line" % dim_type)
    elif dim_type == "center_distance":
        if len(refs) != 2 or any(r not in E or E[r]["type"] not in ("circle", "arc") for r in refs):
            raise ProfilerError("center_distance needs two circles/arcs")
        extra["edges"], refs = list(refs), [E[r]["c"] for r in refs]
    elif dim_type in ("radius", "diameter", "arc_length", "chord", "spline_length"):
        need = {"radius": ("circle", "arc"), "diameter": ("circle", "arc"), "arc_length": ("arc",), "chord": ("arc",), "spline_length": ("spline", "ellipse")}[dim_type]
        if len(refs) != 1 or refs[0] not in E or E[refs[0]]["type"] not in need:
            raise ProfilerError("%s needs one %s" % (dim_type, " or ".join(need)))
    elif dim_type == "angular":
        if len(refs) == 2 and all(r in E and E[r]["type"] == "line" for r in refs):
            pass
        elif len(refs) == 3 and all(r in V for r in refs):
            extra["three_point"] = True                      # vertex, then the two arm ends: measured only (no driving constraint without lines)
        else:
            raise ProfilerError("angular needs two lines or three points (vertex, first arm, second arm)")
    elif dim_type == "ordinate":
        if len(refs) != 1 or refs[0] not in V:
            raise ProfilerError("ordinate needs one vertex")
    elif dim_type == "perpendicular":
        if len(refs) != 2 or refs[0] not in V or refs[1] not in E or E[refs[1]]["type"] != "line":
            raise ProfilerError("perpendicular distance needs a vertex and a line")
    elif dim_type == "min_distance":
        if len(refs) != 2 or any(r not in V and r not in E for r in refs):
            raise ProfilerError("min_distance needs two entities")
    elif dim_type == "slope":
        if len(refs) == 1 and refs[0] in E and E[refs[0]]["type"] == "line":
            refs = list(sk.edge_ends(refs[0]))
        if len(refs) != 2 or any(r not in V for r in refs):
            raise ProfilerError("slope needs a line or two points")
    elif dim_type in ("area", "perimeter"):
        if not refs or any(r not in E for r in refs):
            raise ProfilerError("%s needs the edges of a closed region" % dim_type)
    elif dim_type == "bounding":
        if not refs or any(r not in V and r not in E for r in refs):
            raise ProfilerError("bounding needs one or more entities")
    return refs, extra


def _closest_point_pair(sk, a: str, b: str) -> float:
    def as_geom(i):
        if i in sk.vertices:
            return ("p", sk.pos(i))
        e = sk.edge(i)
        if e["type"] == "line":
            return ("l", sk.pos(e["v"][0]), sk.pos(e["v"][1]))
        if e["type"] in ("circle", "arc"):
            return ("c",) + sk.center_radius(i)
        return ("poly", sk.edge_polyline(i, 0.001))
    ga, gb = as_geom(a), as_geom(b)
    if ga[0] != "p" and gb[0] == "p":
        ga, gb = gb, ga
    if ga[0] == "p":
        p = ga[1]
        if gb[0] == "p":
            return G.dist(p, gb[1])
        if gb[0] == "l":
            return G.nearest_on_segment(p, gb[1], gb[2])[1]
        if gb[0] == "c":
            return abs(G.dist(p, gb[1]) - gb[2])
        return min(G.nearest_on_segment(p, gb[1][i], gb[1][i + 1])[1] for i in range(len(gb[1]) - 1))
    pa = [ga[1], ga[2]] if ga[0] == "l" else (ga[1] if ga[0] == "poly" else None)
    pb = [gb[1], gb[2]] if gb[0] == "l" else (gb[1] if gb[0] == "poly" else None)
    if pa is not None and pb is not None:
        for i in range(len(pa) - 1):
            for j in range(len(pb) - 1):
                if G.line_line(pa[i], pa[i + 1], pb[j], pb[j + 1]) is not None:
                    return 0.0
        best = min(G.nearest_on_segment(p, pb[j], pb[j + 1])[1] for p in pa for j in range(len(pb) - 1))
        return min(best, min(G.nearest_on_segment(p, pa[i], pa[i + 1])[1] for p in pb for i in range(len(pa) - 1)))
    if ga[0] == "c" and gb[0] == "c":
        d = G.dist(ga[1], gb[1])
        return max(0.0, d - ga[2] - gb[2], abs(ga[2] - gb[2]) - d)
    circ, other = (ga, gb) if ga[0] == "c" else (gb, ga)
    pts = [other[1], other[2]] if other[0] == "l" else other[1]
    return min(abs(G.nearest_on_segment(circ[1], pts[i], pts[i + 1])[1] - circ[2]) for i in range(len(pts) - 1))


def measure(sk, dim: dict) -> dict[str, Any]:
    """Current measured value from exact geometry. ``value`` is in document units (radians for angles, plain number for slope in degrees)."""
    t, refs = dim["type"], dim["refs"]
    if t == "aligned" or t == "length" or t == "center_distance":
        return {"value": G.dist(sk.pos(refs[0]), sk.pos(refs[1])), "unit": "length"}
    if t == "horizontal":
        return {"value": abs(sk.pos(refs[1])[0] - sk.pos(refs[0])[0]), "signed": sk.pos(refs[1])[0] - sk.pos(refs[0])[0], "unit": "length"}
    if t == "vertical":
        return {"value": abs(sk.pos(refs[1])[1] - sk.pos(refs[0])[1]), "signed": sk.pos(refs[1])[1] - sk.pos(refs[0])[1], "unit": "length"}
    if t in ("radius", "diameter"):
        r = sk.center_radius(refs[0])[1]
        return {"value": r if t == "radius" else 2 * r, "unit": "length"}
    if t == "angular":
        if dim.get("three_point"):
            c = sk.pos(refs[0])
            d1, d2 = G.sub(sk.pos(refs[1]), c), G.sub(sk.pos(refs[2]), c)
        else:
            (a1, a2), (b1, b2) = [[sk.pos(v) for v in sk.edge(r)["v"]] for r in refs]
            d1, d2 = G.sub(a2, a1), G.sub(b2, b1)
        ang = math.atan2(G.cross(d1, d2), G.dot(d1, d2))
        return {"value": ang, "unit": "angle", "degrees": math.degrees(ang), "convention": "directed, counter-clockwise positive, from the first arm to the second"}
    if t == "arc_length":
        return {"value": sk.edge_length(refs[0]), "unit": "length"}
    if t == "chord":
        a, b = sk.edge_ends(refs[0])
        return {"value": G.dist(sk.pos(a), sk.pos(b)), "unit": "length"}
    if t == "spline_length":
        pts = sk.edge_polyline(refs[0], 1e-4)
        return {"value": G.curve_length(pts), "unit": "length", "method": "flattened polyline, sagitta <= 1e-4 %s; the length is within the sampling error of the true curve" % sk.unit}
    if t == "ordinate":
        o = sk.d["view"]["origin"]
        p = sk.pos(refs[0])
        return {"value": p[0] - o[0] if dim.get("axis", "x") == "x" else p[1] - o[1], "x": p[0] - o[0], "y": p[1] - o[1], "unit": "length"}
    if t == "min_distance":
        return {"value": _closest_point_pair(sk, refs[0], refs[1]), "unit": "length"}
    if t == "perpendicular":
        a, b = [sk.pos(v) for v in sk.edge(refs[1])["v"]]
        return {"value": abs(G.dist_point_line(sk.pos(refs[0]), a, b)), "signed": G.dist_point_line(sk.pos(refs[0]), a, b), "unit": "length"}
    if t == "slope":
        a, b = sk.pos(refs[0]), sk.pos(refs[1])
        dx, dy = b[0] - a[0], b[1] - a[1]
        ang = math.degrees(math.atan2(dy, dx))
        out = {"value": ang, "unit": "angle_deg", "degrees": ang, "sign": "counter-clockwise from +X"}
        out["percent"] = None if abs(dx) < G.EPS else 100.0 * dy / dx
        out["ratio"] = None if abs(dy) < G.EPS else "1:%.6g" % (dx / dy)
        return out
    if t in ("area", "perimeter"):
        from . import regions
        found = regions.region_containing_edges(sk, refs)
        if found is None:
            return {"value": None, "unresolved": "the edges do not bound a closed region"}
        return {"value": found["area"] if t == "area" else found["perimeter"], "unit": "area" if t == "area" else "length", "holes": len(found["holes"])}
    if t == "bounding":
        b = sk.bounds([r for r in refs])
        if b is None:
            return {"value": None}
        return {"value": b[2] - b[0], "width": b[2] - b[0], "height": b[3] - b[1], "min": [b[0], b[1]], "max": [b[2], b[3]], "unit": "length"}
    raise ProfilerError("unknown dimension type " + t)


def readout(sk, dim_type: str, refs: Sequence[str], **opts) -> dict[str, Any]:
    """A *temporary* dimension: measured, returned, never stored (authoritative mode 'Temporary')."""
    r, extra = _normalise_refs(sk, dim_type, refs)
    dim = {"type": dim_type, "refs": r, **extra, **opts}
    out = measure(sk, dim)
    out["mode"] = "temporary"
    return out


# ---------------------------------------------------------------------------------------------------------- constraint view
def driving_constraints(sk) -> list[dict]:
    out = []
    for did, dim in sk.d["dimensions"].items():
        if dim.get("state") == "unresolved" or dim["mode"] not in ("driving", "formula") or not eligible(dim["type"]) or dim.get("three_point"):
            continue
        kind = DRIVING_KIND[dim["type"]]
        value = dim.get("value")
        if value is None:
            continue
        c: dict[str, Any] = {"id": "dim:" + did, "kind": kind, "refs": list(dim["refs"]), "value": value, "enabled": True}
        if dim["type"] in ("horizontal", "vertical"):
            c["value"] = value * dim.get("sign", 1.0)
        if dim["type"] == "perpendicular":
            c["side"] = dim.get("sign", 1.0)
        out.append(c)
    return out


def _new_dim(sk, dim_type, refs, mode, layer="annotation") -> tuple[str, dict]:
    if dim_type not in TYPES:
        raise ProfilerError("unknown dimension type %r (use one of %s)" % (dim_type, ", ".join(TYPES)))
    if mode not in MODES:
        raise ProfilerError("dimension mode must be one of %s" % ", ".join(MODES))
    r, extra = _normalise_refs(sk, dim_type, refs)
    did = new_id()
    dim = {"id": did, "type": dim_type, "refs": r, "mode": mode, "value": None, "param": None, "expr": None, "anchor": "none", "style": "default",
           "offset": 8.0, "layer": layer, "tolerance": None, "name": None, **extra}
    return did, dim


def add_dimension(sk, dim_type: str, refs: Sequence[str], *, mode: str = "reference", value: float | None = None, anchor: str = "none", **opts) -> str:
    """Create a persistent dimension. ``reference`` (default) only reports; ``driving`` constrains to ``value`` (default: the current measure) and is refused if
    the resulting system is not solvable; ``temporary`` is not stored (use :func:`readout`)."""
    if mode == "temporary":
        raise ProfilerError("temporary dimensions are readouts: use readout(); commit one by adding it as 'reference' or 'driving'")
    did, dim = _new_dim(sk, dim_type, refs, mode)
    if anchor not in ANCHORS:
        raise ProfilerError("anchor must be one of %s" % ", ".join(ANCHORS))
    dim["anchor"] = anchor
    dim.update({k: v for k, v in opts.items() if k in ("offset", "style", "layer", "tolerance", "name", "axis")})
    m = measure(sk, dim)
    if mode in ("driving", "formula"):
        if not eligible(dim_type) or dim.get("three_point"):
            raise ProfilerError("a %s dimension cannot drive geometry (it is a measurement only)" % dim_type)
        sg = m.get("signed")
        dim["sign"] = -1.0 if (sg is not None and sg < 0) else 1.0
        dim["value"] = m["value"] if value is None else float(value)
    sk.d["dimensions"][did] = dim
    if mode in ("driving", "formula"):
        try:
            sk.settle()
        except ProfilerConflict as exc:
            del sk.d["dimensions"][did]
            raise ProfilerConflict("this dimension conflicts with existing constraints and was not added: " + exc.message, details=exc.details)
    return did


def auto_type(sk, refs: Sequence[str]) -> tuple[str, list[str]]:
    """The dimension that one or two picked objects ask for: a line is measured along its length, an arc or circle by its radius; two points by their
    horizontal, vertical or straight distance; a point and a line, or two parallel lines, by the distance across; two other lines by the angle between them;
    two circles or arcs centre to centre."""
    V, E = sk.vertices, sk.edges
    refs = list(dict.fromkeys(refs))
    for r in refs:
        if r not in V and r not in E:
            raise ProfilerNotFound("nothing with id %s to measure" % r)
    tol = max(sk.tol * 10, 1e-9)
    kind = lambda r: "point" if r in V else E[r]["type"]
    if len(refs) == 1:
        r = refs[0]
        k = kind(r)
        if k == "line":
            return "length", [r]
        if k in ("arc", "circle"):
            return "radius", [r]
        if k in ("spline", "ellipse"):
            return "spline_length", [r]
        raise ProfilerError("pick a second point or line to measure from this point")
    if len(refs) != 2:
        raise ProfilerError("pick one or two objects to dimension")
    a, b = refs
    ka, kb = kind(a), kind(b)
    if ka != "point" and kb == "point":
        a, b, ka, kb = b, a, kb, ka
    rounds = ("arc", "circle")
    if ka == "point" and kb == "point":
        pa, pb = sk.pos(a), sk.pos(b)
        if abs(pa[1] - pb[1]) <= tol:
            return "horizontal", [a, b]
        if abs(pa[0] - pb[0]) <= tol:
            return "vertical", [a, b]
        return "aligned", [a, b]
    if ka == "point" and kb == "line":
        if a in E[b]["v"]:
            return "length", [b]
        return "perpendicular", [a, b]
    if ka == "point" and kb in rounds:
        return "aligned", [a, E[b]["c"]]
    if ka == "line" and kb == "line":
        a0, a1, b0, b1 = (sk.pos(v) for v in (*E[a]["v"], *E[b]["v"]))
        da, db = G.sub(a1, a0), G.sub(b1, b0)
        la, lb = G.norm(da), G.norm(db)
        if la > 0 and lb > 0 and abs(G.cross(da, db)) <= 1e-6 * la * lb:
            return "perpendicular", [E[a]["v"][0], b]
        return "angular", [a, b]
    if ka in rounds and kb in rounds:
        return "center_distance", [a, b]
    if ka in rounds and kb == "line":
        return "perpendicular", [E[a]["c"], b]
    if ka == "line" and kb in rounds:
        return "perpendicular", [E[b]["c"], a]
    return "min_distance", [a, b]


def add_auto(sk, refs: Sequence[str], *, prefer: str = "driving") -> dict[str, Any]:
    """Dimension what was picked, choosing the type (``auto_type``) and the mode: driving when that adds information, reference (measure only) when the geometry
    is already fixed by other constraints or dimensions, or the measurement cannot drive. Never leaves the profile over-constrained."""
    dim_type, r = auto_type(sk, refs)
    note = None
    if prefer == "driving" and eligible(dim_type) and not (dim_type == "angular" and len(r) != 2):
        before = set(sk.solve().redundant)
        try:
            did = add_dimension(sk, dim_type, r, mode="driving")
        except ProfilerConflict:
            did = None
            note = "conflicts with what is already fixed: added as a reference"
        if did is not None:
            after = sk.solve()
            if set(after.redundant) - before:
                del sk.d["dimensions"][did]
                did = None
                note = "the size is already fixed by other dimensions or constraints: added as a reference"
        if did is not None:
            return {"dimension": did, "type": dim_type, "mode": "driving", "measure": measure(sk, get(sk, did)), "note": None}
    elif prefer == "driving" and not eligible(dim_type):
        note = "a %s dimension only measures" % dim_type.replace("_", " ")
    did = add_dimension(sk, dim_type, r, mode="reference")
    return {"dimension": did, "type": dim_type, "mode": "reference", "measure": measure(sk, get(sk, did)), "note": note}


def get(sk, did: str) -> dict:
    try:
        return sk.d["dimensions"][did]
    except KeyError:
        raise ProfilerNotFound("no dimension %s" % did)


def _end_vertices(dim) -> tuple[str, str] | None:
    return (dim["refs"][0], dim["refs"][1]) if dim["type"] in LINEAR else None


def _anchor_constraint(sk, dim, anchor: str, extra_locked: Sequence[str] = ()) -> list[dict]:
    cons = []
    ends = _end_vertices(dim)
    if anchor in ("start", "end", "center") and ends is None:
        raise ProfilerError("only linear dimensions have start/end/centre anchors")
    if anchor == "start":
        cons.append({"id": "anchor:start", "kind": "fixed", "refs": [ends[0]], "value": list(sk.pos(ends[0]))})
    elif anchor == "end":
        cons.append({"id": "anchor:end", "kind": "fixed", "refs": [ends[1]], "value": list(sk.pos(ends[1]))})
    elif anchor == "center":
        cons.append({"id": "anchor:center", "kind": "midpoint_at", "refs": [ends[0], ends[1]], "value": list(G.mid(sk.pos(ends[0]), sk.pos(ends[1])))})
    for v in extra_locked:
        cons.append({"id": "anchor:" + v[:8], "kind": "fixed", "refs": [v], "value": list(sk.pos(v))})
    return cons


def _explain(sk, exc: ProfilerConflict, what: str) -> ProfilerConflict:
    names = exc.details.get("conflicting", [])
    labels = ", ".join(n["label"] for n in names) or "the existing constraints"
    return ProfilerConflict("%s is not possible: it conflicts with %s. Release or change one of them. Nothing was moved." % (what, labels), details=exc.details)


def _set_formula(sk, dim, did: str, expr: str, anchor: str) -> dict:
    """Make the dimension follow a formula over the profile's parameters (``wsb``, ``w - 40``). Nothing changes if the formula cannot be evaluated or the geometry
    cannot satisfy it."""
    from . import params as PM
    before = {k: dim.get(k) for k in ("mode", "value", "sign", "param", "expr", "anchor")}
    if dim["mode"] in ("reference", "temporary"):
        sg = measure(sk, dim).get("signed")
        dim["sign"] = -1.0 if (sg is not None and sg < 0) else 1.0
        dim["value"] = measure(sk, dim)["value"]
    dim["mode"], dim["expr"], dim["param"], dim["anchor"] = "formula", expr, None, anchor
    try:
        res = PM.apply(sk)
    except Exception:
        dim.update(before)
        raise
    return {"dimension": did, "value": dim["value"], "anchor": anchor, "solve": res["solve"]}


def set_value(sk, did: str, value: float | None = None, *, anchor: str | None = None, expr: str | None = None, locked_vertices: Sequence[str] = ()) -> dict:
    """Edit a dimension's value. A reference dimension is first converted to driving (allowed only if the new system is solvable). The anchor decides which
    side stays stationary; with ``none`` the solver's least-change solution distributes the movement (both ends of a single dimension move half each)."""
    dim = get(sk, did)
    if not eligible(dim["type"]) or dim.get("three_point"):
        raise ProfilerError("a %s dimension is a measurement and cannot be edited" % dim["type"])
    if dim["mode"] == "formula" and expr is None and value is not None:
        raise ProfilerError("this dimension is formula-driven: edit its formula or its parameter, not its value")
    anchor = dim.get("anchor", "none") if anchor is None else anchor
    if anchor not in ANCHORS:
        raise ProfilerError("anchor must be one of %s" % ", ".join(ANCHORS))
    if expr is not None and str(expr).strip():
        return _set_formula(sk, dim, did, str(expr).strip(), anchor)
    if value is None or value <= 0 and dim["type"] not in ("angular",):
        raise ProfilerError("a dimension value must be a positive number")
    before = {"mode": dim["mode"], "value": dim["value"], "sign": dim.get("sign", 1.0)}
    if dim["mode"] in ("reference", "temporary"):
        sg = measure(sk, dim).get("signed")
        dim["sign"] = -1.0 if (sg is not None and sg < 0) else 1.0
    dim["mode"] = "driving" if dim["mode"] != "formula" else "formula"
    dim["value"] = float(value)
    extra = _anchor_constraint(sk, dim, anchor, locked_vertices)
    result = sk.solve(extra)
    if not result.ok:
        dim.update(before)
        raise _explain(sk, ProfilerConflict(result.message, details={"conflicting": sk._names(result.conflicting), "residuals": result.residuals}),
                       "changing %s to %.6g" % (dim["type"], value))
    sk.apply_solution(result)
    return {"dimension": did, "value": dim["value"], "anchor": anchor, "solve": result.to_dict()}


def set_mode(sk, did: str, mode: str) -> None:
    """Convert a dimension. driving -> reference releases its constraint; reference -> driving is allowed only if the system stays solvable."""
    dim = get(sk, did)
    if mode not in ("reference", "driving"):
        raise ProfilerError("use reference or driving (formula mode is set by giving the dimension a parameter formula)")
    if mode == dim["mode"]:
        return
    old = (dim["mode"], dim["value"], dim.get("sign", 1.0), dim.get("param"), dim.get("expr"))
    if mode == "driving":
        if not eligible(dim["type"]) or dim.get("three_point"):
            raise ProfilerError("a %s dimension cannot drive geometry" % dim["type"])
        m = measure(sk, dim)
        dim["sign"] = -1.0 if (m.get("signed") is not None and m["signed"] < 0) else 1.0
        dim["value"] = m["value"]
    dim["mode"] = mode
    if mode == "reference":
        dim["value"], dim["param"], dim["expr"] = None, None, None
    r = sk.solve()
    if not r.ok:
        dim["mode"], dim["value"], dim["sign"], dim["param"], dim["expr"] = old
        raise _explain(sk, ProfilerConflict(r.message, details={"conflicting": sk._names(r.conflicting)}), "making this dimension driving")
    sk.apply_solution(r)


def remove(sk, did: str) -> None:
    get(sk, did)
    del sk.d["dimensions"][did]
    for ch in sk.d["chains"].values():
        ch["segments"] = [s for s in ch["segments"] if s != did]
        if ch.get("total") == did:
            ch["total"] = None


def refresh_unresolved(sk) -> list[str]:
    """Flag dimensions whose references no longer exist (after a topology edit). Never deletes them; they stay for manual repair."""
    bad = []
    for did, dim in sk.d["dimensions"].items():
        ok = all(r in sk.vertices or r in sk.edges for r in dim["refs"])
        if not ok:
            dim["state"] = "unresolved"
            bad.append(did)
        elif dim.get("state") == "unresolved":
            dim.pop("state", None)
    return bad


# --------------------------------------------------------------------------------------------------------------------- chains
def add_chain(sk, vertices: Sequence[str], *, style: str = "chain", with_total: bool = True, along: str = "aligned") -> str:
    """Chain (successive segments) or baseline (every distance from the first vertex) dimensions over ordered vertices. Segments start as *reference*
    dimensions (unlocked); a total is added when asked. ``along`` is ``aligned`` | ``horizontal`` | ``vertical``."""
    if style not in ("chain", "baseline") or along not in ("aligned", "horizontal", "vertical"):
        raise ProfilerError("style is chain|baseline and along is aligned|horizontal|vertical")
    vs = list(vertices)
    if len(vs) < 3 or any(v not in sk.vertices for v in vs) or len(set(vs)) != len(vs):
        raise ProfilerError("a chain needs three or more distinct vertices")
    cid = new_id()
    pairs = [(vs[i], vs[i + 1]) for i in range(len(vs) - 1)] if style == "chain" else [(vs[0], vs[i]) for i in range(1, len(vs))]
    segs = [add_dimension(sk, along, list(p), mode="reference") for p in pairs]
    total = add_dimension(sk, along, [vs[0], vs[-1]], mode="reference", offset=16.0) if with_total and style == "chain" else None
    sk.d["chains"][cid] = {"id": cid, "style": style, "along": along, "vertices": vs, "segments": segs, "total": total, "eq": False, "eq_constraints": [],
                           "locked_vertices": [vs[0]]}
    return cid


def _chain(sk, cid):
    try:
        return sk.d["chains"][cid]
    except KeyError:
        raise ProfilerNotFound("no chain %s" % cid)


def lock_segment(sk, cid: str, index: int, locked: bool) -> None:
    """The middle lock of one segment: locked = its value is held (driving), unlocked = it absorbs adjacent edits (reference)."""
    ch = _chain(sk, cid)
    if not 0 <= index < len(ch["segments"]):
        raise ProfilerError("segment %d does not exist" % index)
    set_mode(sk, ch["segments"][index], "driving" if locked else "reference")


def set_end_locks(sk, cid: str, locked_vertices: Sequence[str]) -> None:
    ch = _chain(sk, cid)
    if any(v not in ch["vertices"] for v in locked_vertices):
        raise ProfilerError("only the chain's own vertices can be locked")
    ch["locked_vertices"] = list(locked_vertices)


def segment_values(sk, cid: str) -> list[float]:
    ch = _chain(sk, cid)
    return [measure(sk, get(sk, s))["value"] for s in ch["segments"]]


def set_chain_total(sk, cid: str, total: float, *, policy: str = "min_change") -> dict:
    """Change the overall length of a chain.

    ``min_change``      locked (driving) segments keep their value; the movement goes to what the locks and the solver allow (an unlocked segment next to
                        the locked end absorbs it) - the authoritative lock behaviour (40 locked: 80+40 -> 90+40; 80 locked: 80+50).
    ``equal_growth``    every unlocked segment grows by the same amount (85 + 45).
    ``proportional``    every unlocked segment is scaled by the same factor (86.667 + 43.333 for 2:1).
    A persistent EQ constraint, if enabled, simply stays in force. Incompatible fixed segments are reported as a conflict; nothing is moved then.
    """
    ch = _chain(sk, cid)
    if ch["style"] != "chain" or ch["total"] is None:
        raise ProfilerError("this chain has no total dimension")
    if total <= 0:
        raise ProfilerError("the total must be positive")
    snapshot = sk.clone()
    td = get(sk, ch["total"])
    locked_segments = [s for s in ch["segments"] if get(sk, s)["mode"] == "driving"]
    free = [s for s in ch["segments"] if s not in locked_segments]
    cur = {s: measure(sk, get(sk, s))["value"] for s in ch["segments"]}
    old_total = sum(cur.values())
    temp: list[str] = []
    try:
        if policy in ("equal_growth", "proportional"):
            if ch["eq"]:
                raise ProfilerError("EQ is on: equality already decides how the segments share the change; disable EQ for %s" % policy)
            free_sum = sum(cur[s] for s in free)
            need = total - (old_total - free_sum)                       # what the free segments must add up to
            if not free:
                raise ProfilerConflict("all segments are locked, so the total cannot change", details={"conflicting": [{"id": s, "label": "locked segment"} for s in locked_segments]})
            if policy == "equal_growth":
                newv = {s: cur[s] + (need - free_sum) / len(free) for s in free}
            else:
                newv = {s: cur[s] * need / free_sum for s in free}
            if any(v <= 0 for v in newv.values()):
                raise ProfilerError("this change would make a segment zero or negative")
            for s in free:                                              # temporarily drive the free segments to their target
                get(sk, s)["mode"], get(sk, s)["value"], get(sk, s)["sign"] = "driving", newv[s], 1.0 if (measure(sk, get(sk, s)).get("signed") or 1) >= 0 else -1.0
                temp.append(s)
        elif policy != "min_change":
            raise ProfilerError("policy must be min_change, equal_growth or proportional")
        td["mode"], td["value"] = "driving", float(total)
        td["sign"] = 1.0 if (measure(snapshot, get(snapshot, ch["total"])).get("signed") or 1) >= 0 else -1.0
        res = sk.solve([], fixed_points=ch["locked_vertices"])
        if not res.ok:
            raise _explain(sk, ProfilerConflict(res.message, details={"conflicting": sk._names(res.conflicting), "residuals": res.residuals}),
                           "setting the total to %.6g" % total)
        sk.apply_solution(res)
    except Exception:
        sk.restore(snapshot)
        raise
    for s in temp:                                                      # the temporary drivers were only a means: release them again
        get(sk, s)["mode"], get(sk, s)["value"] = "reference", None
    return {"segments": segment_values(sk, cid), "total": measure(sk, get(sk, ch["total"]))["value"]}


def set_eq(sk, cid: str, on: bool, *, release: bool = False) -> dict:
    """One-click EQ: a *persistent* equality of all segment lengths. Turning it off removes only the equality - the geometry is not pulled back to the old
    ratio. If driving dimensions make it impossible, the blocking ones are named and nothing changes - unless ``release`` converts them to reference."""
    ch = _chain(sk, cid)
    if not on:
        sk.d["constraints"] = [c for c in sk.d["constraints"] if c["id"] not in ch["eq_constraints"]]
        ch["eq"], ch["eq_constraints"] = False, []
        return {"eq": False}
    if ch["eq"]:
        return {"eq": True}
    snapshot = sk.clone()
    vs = ch["vertices"] if ch["style"] == "chain" else None
    if vs is None:
        raise ProfilerError("EQ applies to chain dimensions (successive segments)")
    ids = []
    for i in range(len(vs) - 2):
        ids.append(sk.add_constraint("equal_distance", [vs[i], vs[i + 1], vs[i + 1], vs[i + 2]], origin="eq", chain=cid))
    ch["eq"], ch["eq_constraints"] = True, ids
    released = []
    # Turning EQ on keeps the overall length (a preference): both chain ends are held for the first solve; if that is impossible the plain solve is used.
    r = sk.solve([], fixed_points=list({vs[0], vs[-1], *ch["locked_vertices"]}))
    if not r.ok:
        r = sk.solve([], fixed_points=ch["locked_vertices"])
    if not r.ok:
        blockers = [s for s in ch["segments"] + ([ch["total"]] if ch["total"] else []) if get(sk, s)["mode"] == "driving"]
        if release and blockers:
            # release the segment dimensions first, keep the total if possible
            for s in [x for x in ch["segments"] if get(sk, x)["mode"] == "driving"]:
                get(sk, s)["mode"], get(sk, s)["value"] = "reference", None
                released.append(s)
            r = sk.solve([], fixed_points=ch["locked_vertices"])
        if not r.ok:
            sk.restore(snapshot)
            names = [{"id": b, "label": "driving %s dimension (%.6g)" % (get(sk, b)["type"], get(sk, b)["value"])} for b in blockers]
            raise ProfilerConflict("EQ cannot be enabled while these driving dimensions fix the segments to different lengths: make them reference "
                                   "dimensions or change them. Nothing was changed.", details={"must_release": names, "conflicting": sk._names(r.conflicting)})
    sk.apply_solution(r)
    return {"eq": True, "released": released, "segments": segment_values(sk, cid)}
