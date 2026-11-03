"""Gutters and rainwater drainage.

A **gutter** hangs on one eave of a roof (an eave is an outline edge the roof sheds water to; see ``elements.roof_eaves``). It is stored as parameters
only, like every other element, and follows its roof: move, turn or re-pitch the roof and the gutter is rebuilt on the same eave. A roof change that
would leave a gutter without an eave to hang on (a gable changed to a shed roof, say) is refused whole, and says which gutter is in the way.

Four types are offered as starting points: half-round, ogee (K-style), box and fascia. A type only chooses the shape of the section and the first
width and depth; every dimension, the thickness of the sheet, the fall and the material can be edited afterwards, and so can the type.
The section is a thin-walled open channel swept along the eave. The gutter falls evenly towards one end (``fall_to``); the high end sits just under
the roof's edge. Corner pieces, stop ends and brackets are not modelled: each eave has its own straight gutter with plain closed ends.

A **downpipe** hangs under a gutter at a distance along it and runs down to a discharge height. It is a tube of the given diameter and wall.

**Drainage** is the rain each roof sends to each gutter and downpipe: the plan area that drains to the gutter, times a design rainfall that is set on
the gutter (litres per second per square metre). The area is exact for the four roof types: a gable roof sends half its plan to each long eave, a
shed roof all of it to its low eave, a hip roof the plan area of each of its four slopes to the eave below, and a flat roof is shared equally
by the gutters on it. A gutter with several downpipes is taken to share its flow equally between them. Sizing a gutter or downpipe to a code
(NEN 3215, EN 12056-3) is not done: the design rainfall is an assumed value to be set to the one that applies, and no pass or fail is given.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from ..tools.errors import BadRequest, NotFound
from ..tools.validation import number, text
from . import elements as E
from . import geometry as G

GUTTER_TYPES: dict[str, dict[str, Any]] = {
    "half_round": {"name": "Half-round gutter", "width": 125.0, "depth": 62.5},
    "ogee": {"name": "Ogee (K-style) gutter", "width": 125.0, "depth": 100.0},
    "box": {"name": "Box gutter", "width": 100.0, "depth": 100.0},
    "fascia": {"name": "Fascia gutter", "width": 115.0, "depth": 90.0},
}
GUTTER_MATERIALS = ("Zinc", "Aluminium", "Steel", "Copper", "PVC")
THICKNESS_DEFAULT = 1.0                  # mm of sheet
FALL_DEFAULT = 3.0                       # mm per metre, towards the outlet
RIM_DROP = 0.02                          # the high end's rim hangs this far below the top of the roof's edge, m
RAINFALL_DEFAULT = 0.03                  # l/s per m² of plan: an assumed design rainfall, set it to the value that applies
PIPE_DEFAULT = 80.0                      # mm
PIPE_WALL = 2.0                          # mm
DISCHARGE_ABOVE_LOWEST_LEVEL = 0.3       # m: where a downpipe ends unless told otherwise
CIRCLE = 24                              # sides of a downpipe


# --------------------------------------------------------------------------------------------------------------------------- sections
def _smooth(x: float) -> float:
    return x * x * (3.0 - 2.0 * x)


def _outside(kind: str, w: float, d: float) -> list[tuple[float, float]]:
    """The outside of the channel from the top of its back to the top of its front, as (distance out from the back, height above the bottom rim)."""
    if kind == "half_round":
        n = 16
        pts = [(w / 2 - w / 2 * math.cos(math.pi * i / n), -d * math.sin(math.pi * i / n)) for i in range(n + 1)]
        pts[0], pts[-1] = (0.0, 0.0), (w, 0.0)
        return pts
    if kind == "box":
        return [(0.0, 0.0), (0.0, -d), (w, -d), (w, 0.0)]
    if kind == "ogee":                                                        # the front leans in at the bottom and rolls out to the lip: an S
        n = 8
        pts = [(0.0, 0.0), (0.0, -d)]
        for i in range(n + 1):
            x = i / n
            pts.append((w * (0.85 + 0.15 * _smooth(x)), -d + d * x))
        return pts
    if kind == "fascia":                                                      # a box whose front stands up above the rim, to cover the end of the rafters
        return [(0.0, 0.0), (0.0, -d), (w, -d), (w, 0.4 * d)]
    raise BadRequest(f"gutter type must be one of {', '.join(GUTTER_TYPES)}")


def section(kind: str, width: float, depth: float, thickness: float) -> list[tuple[float, float]]:
    """The cross-section of a gutter as one closed outline (s out from the roof's edge, z up from the back rim), metres. The sheet is
    ``thickness`` thick; the open top is left open."""
    if thickness * 3 >= width or thickness * 2 >= depth:
        raise BadRequest("The sheet is too thick for the gutter's width and depth")
    outer = _outside(kind, width, depth)
    inner = [(s + thickness, z) for s, z in _outside(kind, width - 2 * thickness, depth - thickness)]
    return outer + inner[::-1]


# ---------------------------------------------------------------------------------------------------------------------------- placement
def _roof(project: Any, meta: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    rid = E.m(meta, "roof")
    if not rid or not E.alive(project, rid) or E.category(project, rid) != "roof":
        raise BadRequest("This gutter has lost its roof")
    return rid, E.meta_of(project, rid)


def frame(project: Any, oid: str) -> dict[str, Any]:
    """Where a gutter lies, in metres: the eave edge it hangs on (a to b), the unit vectors along it and out from the roof, the height of its high rim."""
    meta = E.meta_of(project, oid)
    if E.m(meta, "category") != "gutter":
        raise BadRequest("That element is not a gutter")
    rid, rmeta = _roof(project, meta)
    edge = int(E.m(meta, "edge"))
    if edge not in E.roof_eave_edges(rmeta):
        raise BadRequest("The roof has no eave where this gutter hangs", hint="Delete the gutter, or give the roof back its eave")
    boundary = [tuple(q) for q in E.m(rmeta, "boundary")]
    a, b = boundary[edge], boundary[(edge + 1) % len(boundary)]
    length = math.dist(a, b)
    u = ((b[0] - a[0]) / length, (b[1] - a[1]) / length)
    n = (u[1], -u[0]) if G.signed_area(boundary) > 0 else (-u[1], u[0])         # outwards: to the right of a counter-clockwise outline
    eave = E.level_elevation(project, E.m(rmeta, "level")) + float(E.m(rmeta, "offset", 0.0))
    return {"roof": rid, "a": a, "b": b, "length": length, "u": u, "n": n, "high": eave - RIM_DROP, "meta": meta, "roof_meta": rmeta,
            "width": float(E.m(meta, "width")), "depth": float(E.m(meta, "depth")), "thickness": float(E.m(meta, "thickness")),
            "fall": float(E.m(meta, "fall")), "fall_to": E.m(meta, "fall_to", "end")}


def rim_at(f: Mapping[str, Any], t: float) -> float:
    """Height of the back rim ``t`` metres along the gutter: the high end is at ``high`` and it falls evenly towards ``fall_to``."""
    run = t if f["fall_to"] == "end" else f["length"] - t
    return f["high"] - f["fall"] * run


def regenerate(project: Any, oid: str) -> None:
    f = frame(project, oid)
    if f["length"] < 0.05:
        raise BadRequest("The eave is too short for a gutter")
    if not 0.0 <= f["fall"] <= 0.2:
        raise BadRequest("The fall must be between 0 and 200 mm per metre")
    try:
        verts, faces = G.prism(section(E.m(f["meta"], "gutter"), f["width"], f["depth"], f["thickness"]), 0.0, f["length"])      # (s, z, t)
    except G.GeometryError as exc:
        raise BadRequest(str(exc)) from exc
    (ax, ay), (ux, uy), (nx, ny) = f["a"], f["u"], f["n"]
    mapped = [[ax + ux * t + nx * s, ay + uy * t + ny * s, rim_at(f, t) + z] for s, z, t in verts]
    E._set_mesh(project, oid, E._positive_volume((mapped, faces)))
    for pid in downpipes_of(project, oid):
        regenerate_downpipe(project, pid)


def downpipes_of(project: Any, gutter_id: str) -> list[str]:
    return list(project.model.query_ids(metadata={f"{E.NS}:gutter": gutter_id}, limit=10_000))


def gutters_of(project: Any, roof_id: str) -> list[str]:
    return list(project.model.query_ids(metadata={f"{E.NS}:roof": roof_id}, limit=10_000))


def hosts_of(project: Any, oid: str) -> list[str]:
    """The elements a gutter or downpipe hangs on, nearest first (a gutter: its roof; a downpipe: its gutter, then that gutter's roof)."""
    meta = E.meta_of(project, oid)
    out: list[str] = []
    if E.m(meta, "category") == "downpipe" and E.m(meta, "gutter") and E.alive(project, E.m(meta, "gutter")):
        out.append(E.m(meta, "gutter"))
        meta = E.meta_of(project, out[0])
    if E.m(meta, "roof"):
        out.append(E.m(meta, "roof"))
    return out


def dependents(project: Any, oid: str) -> list[str]:
    """What is deleted along with an element: a roof takes its gutters, and a gutter its downpipes, with it."""
    cat = E.category(project, oid)
    if cat == "roof":
        out: list[str] = []
        for g in gutters_of(project, oid):
            out += [g, *downpipes_of(project, g)]
        return out
    if cat == "gutter":
        return downpipes_of(project, oid)
    return []


def follow(project: Any, roof_id: str) -> None:
    """Rebuild a roof's gutters (and their downpipes) after the roof changed. Refuses, naming the gutter, if an eave one hangs on is gone."""
    for gid in gutters_of(project, roof_id):
        try:
            regenerate(project, gid)
        except BadRequest as exc:
            raise BadRequest(f"The roof change leaves the gutter '{project.model.get(gid)['name']}' without its eave: {exc.message}",
                             hint="Delete that gutter first, or keep the eave") from exc


# ------------------------------------------------------------------------------------------------------------------------------ gutters
def _choice_type(value: Any) -> str:
    return E._choice(value, tuple(GUTTER_TYPES), "gutter type")


def add_gutters(project: Any, p: Mapping[str, Any], to_m: float) -> list[str]:
    """Gutters on the eaves of a roof. ``edges`` lists the outline edges to hang one on (default: every eave of the roof); ``downpipe`` adds a downpipe at
    the low end of each (default: yes). All or none are made."""
    rid = text(p.get("roof"), "roof", 80)
    if not E.alive(project, rid) or E.category(project, rid) != "roof":
        raise NotFound(f"Roof {rid} does not exist")
    rmeta = E.meta_of(project, rid)
    eaves = E.roof_eave_edges(rmeta)
    edges = p.get("edges")
    if edges is None:
        edges = eaves
    if not isinstance(edges, list) or not edges:
        raise BadRequest("edges must be a list of outline edges of the roof")
    wanted: list[int] = []
    for e in edges:
        if isinstance(e, bool) or not isinstance(e, int) or e not in eaves:
            raise BadRequest(f"Edge {e} of this roof is not an eave: the roof sheds water to edges {', '.join(map(str, eaves))}")
        if e not in wanted:
            wanted.append(e)
    taken = {int(E.m(project.model.get(g)["metadata"], "edge")) for g in gutters_of(project, rid)}
    if taken & set(wanted):
        raise BadRequest(f"Edge {sorted(taken & set(wanted))[0]} of this roof already has a gutter")
    kind = _choice_type(p.get("gutter", "half_round"))
    base = GUTTER_TYPES[kind]
    width = number(p.get("width", base["width"]), "width", 30, 1000) / 1000.0
    depth = number(p.get("depth", base["depth"]), "depth", 20, 1000) / 1000.0
    thickness = number(p.get("thickness", THICKNESS_DEFAULT), "thickness", 0.2, 20) / 1000.0
    fall = number(p.get("fall", FALL_DEFAULT), "fall", 0, 200) / 1000.0
    fall_to = E._choice(p.get("fall_to", "end"), ("start", "end"), "fall_to")
    material = E._choice(p.get("material", "Zinc"), GUTTER_MATERIALS, "material")
    rainfall = number(p.get("rainfall", RAINFALL_DEFAULT), "rainfall", 0.001, 1.0)
    section(kind, width, depth, thickness)                                      # refuse a sheet too thick for the section before anything is made
    made: list[str] = []
    try:
        for e in wanted:
            oid = project.model.add_box(width=1.0, depth=1.0, height=1.0, name=text(p.get("name") or base["name"], "name", 80))
            made.append(oid)
            E._attach(project, oid, {"category": "gutter", "type": base["name"], "gutter": kind, "level": E.m(rmeta, "level"), "roof": rid, "edge": e,
                                     "width": width, "depth": depth, "thickness": thickness, "fall": fall, "fall_to": fall_to, "material": material,
                                     "rainfall": rainfall})
            regenerate(project, oid)
        if p.get("downpipe", True):
            diameter = number(p.get("diameter", PIPE_DEFAULT), "diameter", 20, 500)
            for oid in list(made):
                at_end = E.m(project.model.get(oid)["metadata"], "fall_to") == "end"
                length = frame(project, oid)["length"]
                made.append(add_downpipe(project, {"gutter": oid, "offset": length - diameter / 2000.0 - 0.005 if at_end else diameter / 2000.0 + 0.005, "diameter": diameter}, to_m))
    except BadRequest:
        for oid in reversed(made):
            if E.alive(project, oid):
                project.model.remove(oid)
        raise
    return made


def set_gutter_property(project: Any, meta: Mapping[str, Any], key: str, value: Any) -> dict[str, Any]:
    if key == "gutter":
        kind = _choice_type(value)
        return {"gutter": kind, "type": GUTTER_TYPES[kind]["name"]}
    if key in ("width", "depth"):
        return {key: number(value, key, 20 if key == "depth" else 30, 1000) / 1000.0}
    if key == "thickness":
        return {"thickness": number(value, key, 0.2, 20) / 1000.0}
    if key == "fall":
        return {"fall": number(value, key, 0, 200) / 1000.0}
    if key == "fall_to":
        return {"fall_to": E._choice(value, ("start", "end"), "fall_to")}
    if key == "material":
        return {"material": E._choice(value, GUTTER_MATERIALS, "material")}
    if key == "rainfall":
        return {"rainfall": number(value, key, 0.001, 1.0)}
    raise BadRequest(f"'{key}' is not an editable gutter property")


# ---------------------------------------------------------------------------------------------------------------------------- downpipes
def pipe_frame(project: Any, oid: str) -> dict[str, Any]:
    meta = E.meta_of(project, oid)
    if E.m(meta, "category") != "downpipe":
        raise BadRequest("That element is not a downpipe")
    gid = E.m(meta, "gutter")
    if not gid or not E.alive(project, gid) or E.category(project, gid) != "gutter":
        raise BadRequest("This downpipe has lost its gutter")
    g = frame(project, gid)
    d, t = float(E.m(meta, "diameter")), float(E.m(meta, "offset"))
    top = rim_at(g, t) - g["depth"]
    return {"gutter": gid, "g": g, "meta": meta, "diameter": d, "offset": t, "bottom": float(E.m(meta, "bottom")), "top": top,
            "at": (g["a"][0] + g["u"][0] * t + g["n"][0] * d / 2, g["a"][1] + g["u"][1] * t + g["n"][1] * d / 2), "wall": float(E.m(meta, "wall"))}


def _tube(cx: float, cy: float, ro: float, ri: float, z0: float, z1: float) -> tuple[list[list[float]], list[list[int]]]:
    n = CIRCLE
    ring = [(math.cos(2 * math.pi * i / n), math.sin(2 * math.pi * i / n)) for i in range(n)]
    verts = ([[cx + ro * c, cy + ro * s, z1] for c, s in ring] + [[cx + ro * c, cy + ro * s, z0] for c, s in ring]
             + [[cx + ri * c, cy + ri * s, z1] for c, s in ring] + [[cx + ri * c, cy + ri * s, z0] for c, s in ring])
    faces: list[list[int]] = []
    for i in range(n):
        j = (i + 1) % n
        ot, ob, it, ib = i, n + i, 2 * n + i, 3 * n + i
        otj, obj, itj, ibj = j, n + j, 2 * n + j, 3 * n + j
        faces += [[ob, obj, otj], [ob, otj, ot],                        # outside
                  [ib, itj, ibj], [ib, it, itj],                        # inside
                  [ot, otj, itj], [ot, itj, it],                        # top ring
                  [ob, ibj, obj], [ob, ib, ibj]]                        # bottom ring
    return verts, faces


def regenerate_downpipe(project: Any, oid: str) -> None:
    f = pipe_frame(project, oid)
    g = f["g"]
    if not 0.02 <= f["diameter"] <= 0.5:
        raise BadRequest("A downpipe is between 20 and 500 mm across")
    if f["diameter"] > g["width"] + 1e-9:
        raise BadRequest(f"A {f['diameter'] * 1000:.0f} mm downpipe is wider than the {g['width'] * 1000:.0f} mm gutter above it")
    if not 0.0005 <= f["wall"] < f["diameter"] / 2:
        raise BadRequest("The pipe wall must be thinner than the pipe's radius")
    if f["offset"] < f["diameter"] / 2 - 1e-9 or f["offset"] > g["length"] - f["diameter"] / 2 + 1e-9:
        raise BadRequest(f"The downpipe must stand within the gutter, between {f['diameter'] * 500:.0f} mm and {(g['length'] - f['diameter'] / 2) * 1000:.0f} mm along it")
    if f["top"] - f["bottom"] < 0.1:
        raise BadRequest("A downpipe must run down at least 100 mm from the gutter: lower its discharge height")
    ro = f["diameter"] / 2
    E._set_mesh(project, oid, E._positive_volume(_tube(f["at"][0], f["at"][1], ro, ro - f["wall"], f["bottom"], f["top"])))


def add_downpipe(project: Any, p: Mapping[str, Any], to_m: float) -> str:
    gid = text(p.get("gutter"), "gutter", 80)
    if not E.alive(project, gid) or E.category(project, gid) != "gutter":
        raise NotFound(f"Gutter {gid} does not exist")
    g = frame(project, gid)
    diameter = number(p.get("diameter", PIPE_DEFAULT), "diameter", 20, 500) / 1000.0
    offset = number(p.get("offset", diameter / 2 + 0.005), "offset", 0, 100000)
    levels = [float(l["elevation"]) for l in project.manifest["levels"]]
    bottom = number(p["bottom"], "bottom", -1000, 1000) if p.get("bottom") is not None else min(levels) + DISCHARGE_ABOVE_LOWEST_LEVEL
    oid = project.model.add_box(width=1.0, depth=1.0, height=1.0, name=text(p.get("name") or "Downpipe", "name", 80))
    E._attach(project, oid, {"category": "downpipe", "type": "Round Downpipe", "level": E.m(g["meta"], "level"), "gutter": gid, "offset": offset,
                             "diameter": diameter, "bottom": bottom, "wall": PIPE_WALL / 1000.0, "material": E.m(g["meta"], "material", "Zinc")})
    try:
        regenerate_downpipe(project, oid)
    except BadRequest:
        project.model.remove(oid)
        raise
    return oid


def set_downpipe_property(project: Any, meta: Mapping[str, Any], key: str, value: Any) -> dict[str, Any]:
    if key == "offset":
        return {"offset": number(value, key, 0, 100000)}
    if key == "diameter":
        return {"diameter": number(value, key, 20, 500) / 1000.0}
    if key == "wall":
        return {"wall": number(value, key, 0.5, 50) / 1000.0}
    if key == "bottom":
        return {"bottom": number(value, key, -1000, 1000)}
    if key == "material":
        return {"material": E._choice(value, GUTTER_MATERIALS, "material")}
    raise BadRequest(f"'{key}' is not an editable downpipe property")


def slide_downpipe(project: Any, oid: str, dx: float, dy: float) -> None:
    """Moving a downpipe slides it along its gutter, as a door slides along its wall."""
    meta = E.meta_of(project, oid)
    g = frame(project, E.m(meta, "gutter"))
    E._attach(project, oid, {"offset": float(E.m(meta, "offset")) + dx * g["u"][0] + dy * g["u"][1]})


# ----------------------------------------------------------------------------------------------------------------------------- drainage
def roof_slope_areas(rmeta: Mapping[str, Any]) -> dict[int, float] | None:
    """Plan area (m²) that drains to each eave edge of a pitched roof; ``None`` for a flat roof, which has no slopes to divide by."""
    kind = E.m(rmeta, "roof")
    if kind == "flat":
        return None
    boundary = [tuple(q) for q in E.m(rmeta, "boundary")]
    centre, along, across, length, width = E._rectangle([list(q) for q in boundary])
    eaves = E.roof_eave_edges(rmeta)
    out: dict[int, float] = {}
    for e in eaves:
        a, b = boundary[e], boundary[(e + 1) % len(boundary)]
        long_edge = abs(G.dot(G.sub(b, a), along)) >= 0.5 * math.dist(a, b)
        if kind == "shed":
            out[e] = length * width
        elif kind == "gable":
            out[e] = length * width / 2
        else:                                                                          # hip: a trapezoid on a long edge, a triangle on a short one
            out[e] = (length + (length - width)) / 2 * (width / 2) if long_edge else width * width / 4
    return out


def catchment(project: Any, gutter_id: str) -> float:
    """Plan area (m²) of roof that drains to a gutter."""
    g = frame(project, gutter_id)
    areas = roof_slope_areas(g["roof_meta"])
    if areas is not None:
        return areas[int(E.m(g["meta"], "edge"))]
    boundary = [tuple(q) for q in E.m(g["roof_meta"], "boundary")]
    return abs(G.signed_area(boundary)) / max(1, len(gutters_of(project, g["roof"])))


def drainage(project: Any, roof: str | None = None) -> dict[str, Any]:
    """The rain each roof sends to its gutters and downpipes (see the module notes for what is and is not assumed)."""
    roofs = [roof] if roof else [i for i in E.element_ids(project) if E.category(project, i) == "roof"]
    rows = []
    for rid in roofs:
        if not E.alive(project, rid) or E.category(project, rid) != "roof":
            raise NotFound(f"Roof {rid} does not exist")
        rmeta = E.meta_of(project, rid)
        area = abs(G.signed_area([tuple(q) for q in E.m(rmeta, "boundary")]))
        gutters, drained = [], 0.0
        for gid in gutters_of(project, rid):
            obj = project.model.get(gid)
            c = catchment(project, gid)
            rain = float(E.m(obj["metadata"], "rainfall", RAINFALL_DEFAULT))
            pipes = downpipes_of(project, gid)
            drained += c
            gutters.append({"id": gid, "name": obj["name"], "length_m": round(frame(project, gid)["length"], 6), "catchment_m2": round(c, 6), "rainfall_l_s_m2": rain,
                            "flow_l_s": round(c * rain, 6),
                            "downpipes": [{"id": pid, "name": project.model.get(pid)["name"], "diameter_mm": round(float(E.m(project.model.get(pid)["metadata"], "diameter")) * 1000, 6),
                                           "flow_l_s": round(c * rain / len(pipes), 6)} for pid in pipes]})
        boundary = [tuple(q) for q in E.m(rmeta, "boundary")]
        hung = {int(E.m(project.model.get(g["id"])["metadata"], "edge")): g["id"] for g in gutters}
        eaves = [{"edge": e, "length_m": round(math.dist(boundary[e], boundary[(e + 1) % len(boundary)]), 6), "gutter": hung.get(e)} for e in E.roof_eave_edges(rmeta)]
        rows.append({"id": rid, "name": project.model.get(rid)["name"], "roof": E.m(rmeta, "roof"), "plan_area_m2": round(area, 6), "drained_m2": round(drained, 6),
                     "undrained_m2": round(max(0.0, area - drained), 6), "eaves": eaves, "gutters": gutters})
    return {"roofs": rows, "note": NOTE}


NOTE = ("Rain is taken as the roof's plan area times the design rainfall set on each gutter (an assumed value, litres per second per square metre). "
        "A gutter's flow is shared equally by its downpipes. Gutters and downpipes are not sized to a code and no pass or fail is given.")


# --------------------------------------------------------------------------------------------------------------------- plan, properties, checks
def plan_item(project: Any, oid: str, f: float) -> dict[str, Any]:
    obj = project.model.get(oid)
    g = frame(project, oid)
    (ax, ay), (bx, by), (nx, ny) = g["a"], g["b"], g["n"]
    w = g["width"]
    return {"id": oid, "category": "gutter", "name": obj["name"],
            "polygon": [[ax * f, ay * f], [bx * f, by * f], [(bx + nx * w) * f, (by + ny * w) * f], [(ax + nx * w) * f, (ay + ny * w) * f]]}


def pipe_plan_item(project: Any, oid: str, f: float) -> dict[str, Any]:
    obj = project.model.get(oid)
    pf = pipe_frame(project, oid)
    x, y = pf["at"]
    r = pf["diameter"] / 2
    return {"id": oid, "category": "downpipe", "name": obj["name"],
            "polygon": [[(x + r * math.cos(2 * math.pi * i / CIRCLE)) * f, (y + r * math.sin(2 * math.pi * i / CIRCLE)) * f] for i in range(CIRCLE)]}


def pipe_on_level(project: Any, oid: str, level_id: str) -> bool:
    """A downpipe is drawn on the plan of its own level and of every level whose plan cuts through it."""
    pf = pipe_frame(project, oid)
    z = E.level_elevation(project, level_id)
    return level_id == E.m(pf["meta"], "level") or (pf["bottom"] <= z + E.CUT_HEIGHT and pf["top"] >= z)


def properties(project: Any, oid: str, out: dict[str, Any], volume: dict[str, Any]) -> dict[str, Any]:
    meta = E.meta_of(project, oid)
    mm = lambda v: round(v * 1000, 6)                                                              # noqa: E731
    if E.m(meta, "category") == "gutter":
        g = frame(project, oid)
        out["top"] = [{"key": "gutter", "label": "Gutter type", "type": "choice", "value": E.m(meta, "gutter"), "choices": [{"value": k, "label": v["name"]} for k, v in GUTTER_TYPES.items()]},
                      {"key": "width", "label": "Width", "type": "number", "unit": "mm", "value": mm(g["width"])},
                      {"key": "depth", "label": "Depth", "type": "number", "unit": "mm", "value": mm(g["depth"])},
                      {"key": "thickness", "label": "Sheet thickness", "type": "number", "unit": "mm", "value": mm(g["thickness"])},
                      {"key": "material", "label": "Material", "type": "choice", "value": E.m(meta, "material"), "choices": E._choices(GUTTER_MATERIALS)}]
        c = catchment(project, oid)
        pipes = downpipes_of(project, oid)
        rain = float(E.m(meta, "rainfall", RAINFALL_DEFAULT))
        out["groups"] = [
            {"title": "Fall", "rows": [{"key": "fall", "label": "Fall", "type": "number", "unit": "mm/m", "value": mm(g["fall"])},
                                       {"key": "fall_to", "label": "Falls towards", "type": "choice", "value": g["fall_to"], "choices": [{"value": "start", "label": "Start of the eave"}, {"value": "end", "label": "End of the eave"}]}]},
            {"title": "Rainwater drainage", "rows": [{"label": "Roof area draining here", "type": "readonly", "value": f"{c:.2f} m² (plan)"},
                                                     {"key": "rainfall", "label": "Design rainfall", "type": "number", "unit": "l/s·m²", "value": rain},
                                                     {"label": "Design flow", "type": "readonly", "value": f"{c * rain:.3f} l/s"},
                                                     {"label": "Downpipes", "type": "readonly", "value": str(len(pipes)) if pipes else "none (this gutter has no outlet)"}]},
            {"title": "Geometry", "rows": [{"label": "Length", "type": "readonly", "value": f"{g['length']:.3f} m"}, volume]}]
        return out
    pf = pipe_frame(project, oid)
    c = catchment(project, pf["gutter"])
    share = len(downpipes_of(project, pf["gutter"])) or 1
    rain = float(E.m(pf["g"]["meta"], "rainfall", RAINFALL_DEFAULT))
    out["top"] = [{"key": "diameter", "label": "Diameter", "type": "number", "unit": "mm", "value": mm(pf["diameter"])},
                  {"key": "offset", "label": "Distance along gutter", "type": "number", "unit": "m", "value": round(pf["offset"], 6)},
                  {"key": "bottom", "label": "Discharge height", "type": "number", "unit": "m", "value": round(pf["bottom"], 6)},
                  {"key": "wall", "label": "Pipe wall", "type": "number", "unit": "mm", "value": mm(pf["wall"])},
                  {"key": "material", "label": "Material", "type": "choice", "value": E.m(pf["meta"], "material"), "choices": E._choices(GUTTER_MATERIALS)}]
    out["groups"] = [{"title": "Rainwater drainage", "rows": [{"label": "Roof area draining here", "type": "readonly", "value": f"{c / share:.2f} m² (plan)"},
                                                              {"label": "Design flow", "type": "readonly", "value": f"{c / share * rain:.3f} l/s"}]},
                     {"title": "Geometry", "rows": [{"label": "Length", "type": "readonly", "value": f"{pf['top'] - pf['bottom']:.3f} m"}, volume]}]
    return out


def issues(project: Any, oid: str, label: str, issue: Any) -> list[dict[str, str]]:
    """Model-check findings for a gutter or downpipe."""
    out: list[dict[str, str]] = []
    cat = E.category(project, oid)
    try:
        if cat == "gutter":
            frame(project, oid)
            if not downpipes_of(project, oid):
                out.append(issue("warning", oid, f"{label} has no downpipe: the rain it collects has nowhere to go", "drainage"))
        else:
            f = pipe_frame(project, oid)
            if f["diameter"] > f["g"]["width"] + 1e-9:
                out.append(issue("error", oid, f"{label} is wider than its gutter", "parameters"))
            if f["top"] - f["bottom"] < 0.1:
                out.append(issue("error", oid, f"{label} does not run down from its gutter", "parameters"))
    except BadRequest as exc:
        out.append(issue("error", oid, f"{label}: {exc.message}", "hosting"))
    return out
