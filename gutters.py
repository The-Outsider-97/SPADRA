"""Gutters and rainwater drainage.

A **gutter** hangs on one eave of a roof (an eave is an outline edge the roof sheds water to; see ``elements.roof_eaves``). It is stored as parameters
only, like every other element, and follows its roof: move, turn or re-pitch the roof and the gutter is rebuilt on the same eave. A roof change that
would leave a gutter without an eave to hang on (a gable changed to a shed roof, say), or a corner that can no longer be made, is refused whole, and
says which gutter is in the way.

Four types are offered as starting points: half-round, ogee (K-style), box and fascia. A type only chooses the shape of the section and the first
width and depth; every dimension, the thickness of the sheet, the fall and the material can be edited afterwards, and so can the type.
The section is a thin-walled open channel swept along the eave.

**Runs.** Gutters on eaves that follow one another round the outline are one *run* (they share a ``run`` key; each piece stays its own element, so each can
be selected, measured and scheduled). Where two pieces meet, both are cut on the bisector of the corner (a mitre), so the channel is continuous; every edge of
the outline makes a closed loop. An end that meets nothing is a free end: it is closed with a stop-end plate (or left open), and can be extended or
shortened. The type, size, material, fall, rainfall, offset from the eave line and the drop below the eave are the run's, and editing one on any piece edits
them all. The piece-by-piece layout is a pure function (``layout``), which is what the preview, the build and the plan all use.

**Fall.** A run falls evenly towards its *low points*, which are chosen by ``fall_to``: the end of the run, its start, its middle, or every corner. The
highest point(s) sit just under the roof's edge and the rim is continuous across every corner. Brackets and preformed corner units are not modelled: a corner
is two mitred pieces.

A **downpipe** hangs under a gutter at a distance along it and runs down to a discharge height. It is a tube of the given diameter and wall. A new run gets one
at each low point; a downpipe belongs to the low point it stands within ``OUTLET_REACH`` of.

**Drainage** is the rain each roof sends to each gutter and downpipe: the plan area that drains to the gutter, times a design rainfall that is set on
the gutter (litres per second per square metre). The area is exact for the four roof types: a gable roof sends half its plan to each long eave, a
shed roof all of it to its low eave, a hip roof the plan area of each of its four slopes to the eave below, and a flat roof is shared equally
by the gutters on it. Within a run the rain of the stretch that drains to a low point leaves by the downpipes there (``run_state``, ``trace``); a low point
without a downpipe is reported by the model check. Sizing a gutter or downpipe to a code (NEN 3215, EN 12056-3) is not done: the design rainfall is an
assumed value to be set to the one that applies, and no pass or fail is given.
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


# ----------------------------------------------------------------------------------------------------------------------------- runs, layout
# A run is the gutters of one roof that share a ``run`` key. Its *chains* are the stretches of consecutive roof edges that all carry a gutter of the run
# (a closed loop when every edge of the outline does). Everything geometric is computed from the chain by the pure functions below, so that what the
# preview shows, what is built and what the plan draws can never disagree.
RUN_KEYS = ("gutter", "type", "width", "depth", "thickness", "material", "fall", "fall_to", "rainfall", "gap", "drop")      # one value for the whole run
SEGMENT_DEFAULTS = {"ext_start": 0.0, "ext_end": 0.0, "end_start": "cap", "end_end": "cap"}                                    # per segment, free ends only
FALL_TO = ("end", "start", "middle", "corners")
END_KINDS = ("cap", "open")
MAX_TURN = math.radians(170.0)           # a corner sharper than this cannot be mitred
OUTLET_REACH = 0.25                      # m: a downpipe serves a low point when its centre is this close (plus its radius)
HOLE_BAND = 0.002                        # m: the stations either side of an outlet stand this far clear of it


def _outward(boundary: Sequence[Sequence[float]], u: tuple[float, float]) -> tuple[float, float]:
    return (u[1], -u[0]) if G.signed_area([tuple(q) for q in boundary]) > 0 else (-u[1], u[0])


def order_edges(count: int, edges: Sequence[int]) -> list[tuple[list[int], bool]]:
    """Split outline edges into chains of consecutive edges (round the outline, wrapping at the end). ``True`` marks the closed loop that every
    edge of the outline makes."""
    have = set(edges)
    if len(have) == count and count >= 3:
        return [(sorted(have), True)]
    out = []
    for start in sorted(e for e in have if (e - 1) % count not in have):
        chain = [start]
        while (chain[-1] + 1) % count in have:
            chain.append((chain[-1] + 1) % count)
        out.append((chain, False))
    return out


def _lows(fall_to: str, starts: list[float], total: float, closed: bool) -> list[float]:
    """Where along the chain (metres from its start) the gutter is lowest, i.e. where the rain must leave it."""
    if closed and fall_to in ("start", "end"):
        raise BadRequest(f"A closed loop of gutters has no start or end: let it fall to 'middle' (one outlet) or 'corners' (one at every corner), not '{fall_to}'")
    if fall_to == "end":
        return [total]
    if fall_to == "start":
        return [0.0]
    if fall_to == "middle":
        return [total / 2]
    if fall_to == "corners":
        lows = list(starts) if closed else list(starts[1:])
        if not lows:
            raise BadRequest("A single straight gutter has no corner to fall to: choose 'start', 'end' or 'middle'")
        return lows
    raise BadRequest(f"fall_to must be one of {', '.join(FALL_TO)}")


def _distance(p: float, lows: Sequence[float], total: float, closed: bool) -> float:
    if closed:
        q = p % total
        return min(min(abs(q - l), total - abs(q - l)) for l in lows)
    return min(abs(p - l) for l in lows)


def _farthest(lows: Sequence[float], total: float, closed: bool) -> float:
    """The greatest distance from any point of the chain to its nearest low: the high point(s) sit this far uphill of the lows."""
    ls = sorted(lows)
    gaps = [(ls[i + 1] - ls[i]) / 2 for i in range(len(ls) - 1)]
    if closed:
        return max([*gaps, (ls[0] + total - ls[-1]) / 2])
    return max([ls[0], total - ls[-1], *gaps])


def layout(boundary: Sequence[Sequence[float]], order: Sequence[int], closed: bool, *, width: float, gap: float, fall_to: str,
           segments: Mapping[int, Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """The geometry of one chain of gutters on an outline (metres): each segment's edge, direction, outward normal, where it starts along the chain,
    the mitre at each corner it shares with its neighbour, its free ends and extensions, and the chain's low points. Pure: nothing is read from or
    written to a model. Raises ``BadRequest`` for anything that cannot be built (a corner too sharp, an eave too short, a fall that does not suit the chain)."""
    pts = [tuple(q) for q in boundary]
    count = len(pts)
    sigma = 1.0 if G.signed_area(pts) > 0 else -1.0
    segs: list[dict[str, Any]] = []
    p = 0.0
    for e in order:
        a, b = pts[e], pts[(e + 1) % count]
        length = math.dist(a, b)
        if length < 0.05:
            raise BadRequest("The eave is too short for a gutter")
        u = ((b[0] - a[0]) / length, (b[1] - a[1]) / length)
        own = dict(SEGMENT_DEFAULTS)
        own.update(dict(segments or {}).get(e, {}))
        segs.append({"edge": e, "a": a, "b": b, "length": length, "u": u, "n": _outward(pts, u), "p0": p, "k0": None, "k1": None,
                     "ext0": float(own["ext_start"]), "ext1": float(own["ext_end"]), "end0": own["end_start"], "end1": own["end_end"]})
        p += length
    total = p
    for i, sg in enumerate(segs):
        j = i + 1
        if j == len(segs):
            if not closed:
                continue
            j = 0
        nxt = segs[j]
        cross = sg["u"][0] * nxt["u"][1] - sg["u"][1] * nxt["u"][0]
        phi = math.atan2(cross, sg["u"][0] * nxt["u"][0] + sg["u"][1] * nxt["u"][1])
        if abs(phi) > MAX_TURN:
            raise BadRequest(f"The corner between eaves {sg['edge'] + 1} and {nxt['edge'] + 1} is too sharp to mitre ({abs(math.degrees(phi)):.0f}° turn)")
        k = sigma * math.tan(phi / 2)                                           # a mitre moves the cut by k per metre out from the eave line
        sg["k1"], nxt["k0"] = k, k
    for sg in segs:
        for side, ext, kind, k in ((0, sg["ext0"], sg["end0"], sg["k0"]), (1, sg["ext1"], sg["end1"], sg["k1"])):
            label = "start" if side == 0 else "end"
            if k is not None and abs(ext) > 1e-12:
                raise BadRequest(f"The {label} of eave {sg['edge'] + 1} is mitred to its neighbour and cannot be extended")
            if k is None and kind not in END_KINDS:
                raise BadRequest(f"The {label} of a gutter is either a 'cap' or 'open'")
            if k is None and ext < -sg["length"] / 2:
                raise BadRequest("An end cannot be shortened by more than half the eave")
        if any(_t_cut(sg, 1, s) - _t_cut(sg, 0, s) < 0.05 for s in (gap, gap + width)):
            raise BadRequest(f"Eave {sg['edge'] + 1} is too short for the mitres at its corners")
    starts = [sg["p0"] for sg in segs]
    lows = _lows(fall_to, starts, total, closed)
    return {"segments": segs, "total": total, "closed": closed, "fall_to": fall_to, "lows": lows, "farthest": _farthest(lows, total, closed), "sigma": sigma,
            "width": width, "gap": gap}


def rim(chain: Mapping[str, Any], p: float, high: float, fall: float) -> float:
    """Height of the back rim ``p`` metres along the chain: the farthest-uphill point sits at ``high`` and the gutter falls evenly towards the nearest low."""
    return high - fall * (chain["farthest"] - _distance(p, chain["lows"], chain["total"], chain["closed"]))


def _t_cut(sg: Mapping[str, Any], side: int, s: float) -> float:
    """Where along the eave the end of a segment is cut, for the part of the section ``s`` metres out from the eave line. A mitre slants the cut."""
    if side == 0:
        return -sg["ext0"] - (sg["k0"] or 0.0) * s
    return sg["length"] + sg["ext1"] + (sg["k1"] or 0.0) * s


def plan_outline(chain: Mapping[str, Any], sg: Mapping[str, Any]) -> list[tuple[float, float]]:
    """A segment's footprint in plan (metres), with its mitred or square ends."""
    s0, s1 = chain["gap"], chain["gap"] + chain["width"]
    (ax, ay), (ux, uy), (nx, ny) = sg["a"], sg["u"], sg["n"]
    return [(ax + ux * t + nx * s, ay + uy * t + ny * s) for t, s in ((_t_cut(sg, 0, s0), s0), (_t_cut(sg, 1, s0), s0), (_t_cut(sg, 1, s1), s1), (_t_cut(sg, 0, s1), s1))]


def outlet_span(chain: Mapping[str, Any], sg: Mapping[str, Any], radius: float) -> tuple[float, float]:
    """The distances along a segment's eave between which a downpipe of this radius, centred on the gutter's section, stays wholly inside that
    segment: clear of a free end, and clear of a mitre by the circle's true distance from the slanted cut (not just along the eave)."""
    s_c = chain["gap"] + chain["width"] / 2
    k0, k1 = sg["k0"] or 0.0, sg["k1"] or 0.0
    lo = -sg["ext0"] - k0 * s_c + radius * math.sqrt(1 + k0 * k0)
    hi = sg["length"] + sg["ext1"] + k1 * s_c - radius * math.sqrt(1 + k1 * k1)
    return lo, hi


def default_outlets(chain: Mapping[str, Any], diameter: float) -> list[tuple[int, float]]:
    """Where a downpipe goes at each low point of a chain: (edge, distance along that eave), kept clear of the ends and mitres by the pipe's radius."""
    margin = diameter / 2 + 0.005
    out = []
    for low in chain["lows"]:
        placed = None
        for sg in chain["segments"]:
            if sg["p0"] - 1e-9 <= low <= sg["p0"] + sg["length"] + 1e-9:
                lo, hi = outlet_span(chain, sg, margin)
                t = min(max(low - sg["p0"], lo), hi)
                if placed is None or abs(low - sg["p0"] - t) < abs(low - placed[2] - placed[1]):
                    placed = (sg["edge"], t, sg["p0"])
                if low - sg["p0"] > 1e-9:                                         # a low inside this eave, or at its end: it stays here
                    break
        if placed:
            out.append((placed[0], placed[1]))
    return out


# ---------------------------------------------------------------------------------------------------------------------------- placement
def _roof(project: Any, meta: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    rid = E.m(meta, "roof")
    if not rid or not E.alive(project, rid) or E.category(project, rid) != "roof":
        raise BadRequest("This gutter has lost its roof")
    return rid, E.meta_of(project, rid)


def run_key(project: Any, oid: str, meta: Mapping[str, Any] | None = None) -> str:
    """The key the gutters of one run share. A gutter made before runs existed is a run of its own."""
    return E.m(meta if meta is not None else E.meta_of(project, oid), "run") or f"run:{oid}"


def run_members(project: Any, roof_id: str, key: str) -> list[str]:
    return [g for g in gutters_of(project, roof_id) if run_key(project, g) == key]


def _segment_params(project: Any, ids: Sequence[str]) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for g in ids:
        meta = E.meta_of(project, g)
        out[int(E.m(meta, "edge"))] = {"ext_start": float(E.m(meta, "ext_start", 0.0)), "ext_end": float(E.m(meta, "ext_end", 0.0)),
                                       "end_start": E.m(meta, "end_start", "cap"), "end_end": E.m(meta, "end_end", "cap")}
    return out


def _roof_edge_clearance(project: Any, roof_id: str, roof: Mapping[str, Any], run_edges: Sequence[int], requested: float) -> float:
    """Place a gutter run outside its roof-edge members, regardless of creation order.

    The explicit gutter gap remains the user's minimum offset. The effective
    gap is derived from the actual 3D edge profiles (including an adjacent
    gable-end mitre), so adding, resizing or deleting a trim can never leave
    a gutter inside it. Every segment of a connected gutter run shares the
    same gap; its existing mitred corner and downpipe relationships survive.
    """
    trims = [E.meta_of(project, oid) for oid in E.element_ids(project)
             if E.m(E.meta_of(project, oid), "roof_host") == roof_id
             and E.m(E.meta_of(project, oid), "accessory_kind") == "roof_edge"]
    if not trims:
        return requested
    boundary = [tuple(q) for q in E.m(roof, "boundary")]
    bounds = []
    for e in run_edges:
        a, b = boundary[e], boundary[(e + 1) % len(boundary)]
        length = math.dist(a, b)
        u = ((b[0] - a[0]) / length, (b[1] - a[1]) / length)
        bounds.append((a, u, _outward(boundary, u), length))
    clearance = requested
    for trim in trims:
        vertices, _ = E._roof_edge_mesh(project, roof, int(E.m(trim, "edge")), float(E.m(trim, "width")),
                                        float(E.m(trim, "depth")), float(E.m(trim, "vertical_offset", 0.0)),
                                        float(E.m(trim, "lateral_offset", 0.0)))
        for a, u, n, length in bounds:
            for x, y, _ in vertices:
                along = (x - a[0]) * u[0] + (y - a[1]) * u[1]
                if -0.25 <= along <= length + 0.25:
                    clearance = max(clearance, (x - a[0]) * n[0] + (y - a[1]) * n[1] + 0.005)
    return clearance


def frame(project: Any, oid: str) -> dict[str, Any]:
    """Where a gutter lies, in metres: the eave edge it hangs on (a to b), the unit vectors along it and out from the roof, the height of its high rim,
    and the chain of connected gutters it belongs to (``chain``, with this gutter's own entry as ``seg`` and the ids of the whole chain as ``ids``)."""
    meta = E.meta_of(project, oid)
    if E.m(meta, "category") != "gutter":
        raise BadRequest("That element is not a gutter")
    rid, rmeta = _roof(project, meta)
    edge = int(E.m(meta, "edge"))
    if edge not in E.roof_eave_edges(rmeta):
        raise BadRequest("The roof has no eave where this gutter hangs", hint="Delete the gutter, or give the roof back its eave")
    boundary = [tuple(q) for q in E.m(rmeta, "boundary")]
    members = {int(E.m(E.meta_of(project, g), "edge")): g for g in run_members(project, rid, run_key(project, oid, meta))}
    order, closed = next((o, c) for o, c in order_edges(len(boundary), list(members)) if edge in o)
    width, requested_gap = float(E.m(meta, "width")), float(E.m(meta, "gap", 0.0))
    gap = _roof_edge_clearance(project, rid, rmeta, order, requested_gap)
    chain = layout(boundary, order, closed, width=width, gap=gap, fall_to=E.m(meta, "fall_to", "end"), segments=_segment_params(project, [members[e] for e in order]))
    seg = next(s for s in chain["segments"] if s["edge"] == edge)
    eave = E.level_elevation(project, E.m(rmeta, "level")) + float(E.m(rmeta, "offset", 0.0))
    return {"roof": rid, "a": seg["a"], "b": seg["b"], "length": seg["length"], "u": seg["u"], "n": seg["n"], "high": eave - float(E.m(meta, "drop", RIM_DROP)),
            "meta": meta, "roof_meta": rmeta, "width": width, "depth": float(E.m(meta, "depth")), "thickness": float(E.m(meta, "thickness")), "gap": gap,
            "fall": float(E.m(meta, "fall")), "fall_to": E.m(meta, "fall_to", "end"), "chain": chain, "seg": seg, "ids": [members[e] for e in order],
            "key": run_key(project, oid, meta)}


def rim_at(f: Mapping[str, Any], t: float) -> float:
    """Height of the back rim ``t`` metres along the gutter."""
    return rim(f["chain"], f["seg"]["p0"] + t, f["high"], f["fall"])


def _sweep(poly: Sequence[tuple[float, float]], stations: Sequence[tuple[Any, float]], f: Mapping[str, Any]) -> tuple[list[list[float]], list[list[int]]]:
    """``poly`` (s out from the eave line, z up from the rim) swept along the eave through several stations. A station is ``(t_of, p)``: where it lies
    along the eave for a part ``s`` out (a mitre slants the end stations), and the position along the chain its rim height is taken at. The rim is
    straight between stations, so there is one wherever the fall changes direction."""
    pts = [tuple(q) for q in poly]
    if G.signed_area(pts) < 0:
        pts.reverse()
    n = len(pts)
    (ax, ay), (ux, uy), (nx, ny) = f["a"], f["u"], f["n"]
    verts: list[list[float]] = []
    for t_of, p in stations:
        h = rim(f["chain"], f["seg"]["p0"] + p, f["high"], f["fall"])
        for s, z in pts:
            t = t_of(s)
            verts.append([ax + ux * t + nx * s, ay + uy * t + ny * s, h + z])
    faces: list[list[int]] = []
    last = (len(stations) - 1) * n
    for a, b, c in G.triangulate(pts):
        faces.append([last + a, last + b, last + c])
        faces.append([a, c, b])
    for k in range(len(stations) - 1):
        for i in range(n):
            j = (i + 1) % n
            faces.append([k * n + i, k * n + j, (k + 1) * n + j])
            faces.append([k * n + i, (k + 1) * n + j, (k + 1) * n + i])
    return verts, faces


def _join(parts: Sequence[tuple[list[list[float]], list[list[int]]]]) -> tuple[list[list[float]], list[list[int]]]:
    verts: list[list[float]] = []
    faces: list[list[int]] = []
    for v, fc in parts:
        base = len(verts)
        verts += v
        faces += [[a + base, b + base, c + base] for a, b, c in fc]
    return verts, faces


def kinks(chain: Mapping[str, Any]) -> list[float]:
    """Positions along the chain where the rim stops falling one way and starts falling the other: every low point, and the high point midway between
    two lows."""
    lows, total = sorted(chain["lows"]), chain["total"]
    out = list(lows)
    out += [(lows[i] + lows[i + 1]) / 2 for i in range(len(lows) - 1)]
    if chain["closed"]:
        out.append(((lows[-1] + lows[0] + total) / 2) % total)
    return out


def segment_mesh(f: Mapping[str, Any], kind: str, holes: Sequence[tuple[float, float, float]] = ()) -> tuple[list[list[float]], list[list[int]]]:
    """The solid of one gutter segment: the channel swept along its eave with mitred ends where it joins its neighbours, a station wherever the fall
    changes direction, and a stop-end plate at each free end that is capped. ``holes`` are round outlets ``(t along the eave, s out from the eave line,
    radius)`` cut straight down through the sheet (see ``_sweep_with_holes``)."""
    sg, th, gap, chain = f["seg"], f["thickness"], f["gap"], f["chain"]
    chan = [(s + gap, z) for s, z in section(kind, f["width"], f["depth"], th)]
    cap0 = sg["k0"] is None and sg["end0"] == "cap"
    cap1 = sg["k1"] is None and sg["end1"] == "cap"
    length = sg["length"]
    lo = -sg["ext0"] + (th if cap0 else 0.0)
    hi = length + sg["ext1"] - (th if cap1 else 0.0)
    if hi - lo < 0.01:
        raise BadRequest("The eave is too short for a gutter")
    first = ((lambda s: lo), lo) if sg["k0"] is None else ((lambda s: _t_cut(sg, 0, s)), 0.0)
    final = ((lambda s: hi), hi) if sg["k1"] is None else ((lambda s: _t_cut(sg, 1, s)), length)
    inside = sorted({round(k - sg["p0"] + sh, 9) for k in kinks(chain) for sh in ((-chain["total"], 0.0, chain["total"]) if chain["closed"] else (0.0,))
                     if lo + 1e-6 < k - sg["p0"] + sh < hi - 1e-6})
    if holes:                                                                  # a station either side of each outlet and through its middle
        inside = sorted(set(inside) | {round(tc + d, 9) for tc, _, r in holes for d in (-r - HOLE_BAND, 0.0, r + HOLE_BAND) if lo + 1e-6 < tc + d < hi - 1e-6})
    stations = [first, *[((lambda s, t=t: t), t) for t in inside], final]
    parts = [_sweep_with_holes(chan, stations, f, holes) if holes else _sweep(chan, stations, f)]
    plate = [(s + gap, z) for s, z in _outside(kind, f["width"], f["depth"])]
    for yes, a, b in ((cap0, -sg["ext0"], -sg["ext0"] + th), (cap1, length + sg["ext1"] - th, length + sg["ext1"])):
        if yes:
            parts.append(_sweep(plate, [((lambda s, a=a: a), a), ((lambda s, b=b: b), b)], f))
    return _join(parts)


def _build(project: Any, oid: str) -> None:
    f = frame(project, oid)
    if not 0.0 <= f["fall"] <= 0.2:
        raise BadRequest("The fall must be between 0 and 200 mm per metre")
    if not 0.0 <= f["gap"] <= 0.5:
        raise BadRequest("A gutter hangs between 0 and 500 mm out from the eave line")
    holes = outlet_holes(project, oid, f)                                           # every downpipe's outlet is a real hole through the sheet
    try:
        mesh = segment_mesh(f, E.m(f["meta"], "gutter"), [h for _, h in holes])
    except G.GeometryError as exc:
        names = ", ".join(project.model.get(pid)["name"] or "a downpipe" for pid, _ in holes)
        raise BadRequest(f"{exc}" + (f" (outlets of {names})" if holes else ""), hint="Move the downpipe along the gutter, or use a smaller one" if holes else None) from exc
    E._set_mesh(project, oid, E._positive_volume(mesh))


def _sweep_with_holes(poly: Sequence[tuple[float, float]], stations: Sequence[tuple[Any, float]], f: Mapping[str, Any],
                      holes: Sequence[tuple[float, float, float]]) -> tuple[list[list[float]], list[list[int]]]:
    """The channel swept as ``_sweep`` does, with round outlets cut straight down through its sheet, exactly.

    Between two stations the outside and the inside of the sheet are each made of flat strips: the rim height is linear along the eave and the section
    is straight between its points, so on each strip the height is ``rim(t) + z(s)``, a plane over its rectangle in plan (t along the eave, s out from
    it). A strip that an outlet reaches is replaced by that rectangle minus the outlet's polygon (``planar.difference``, the exact boolean the voids
    use), triangulated in plan and lifted onto the strip's plane. Stations run through each outlet's middle and both sides, so no rectangle holds a
    whole outlet. The two rims round the hole are joined by its wall. Every vertex is shared by position, so the result is one closed shell."""
    from . import planar as P
    pts = [tuple(q) for q in poly]
    if G.signed_area(pts) < 0:
        pts.reverse()
    n = len(pts)
    (ax, ay), (ux, uy), (nx, ny) = f["a"], f["u"], f["n"]
    verts: list[list[float]] = []
    faces: list[list[int]] = []
    index: dict[tuple[float, float, float], int] = {}

    def vid(t: float, s: float, z: float) -> int:
        key = (round(t, 9), round(s, 9), round(z, 9))
        if key not in index:
            index[key] = len(verts)
            verts.append([ax + ux * t + nx * s, ay + uy * t + ny * s, z])
        return index[key]

    heights = [rim(f["chain"], f["seg"]["p0"] + p, f["high"], f["fall"]) for _, p in stations]
    rings = [[(t_of(s), s, h + z) for s, z in pts] for (t_of, _), h in zip(stations, heights)]
    last = len(stations) - 1
    for a, b, c in G.triangulate(pts):                                          # the two end faces
        faces.append([vid(*rings[last][a]), vid(*rings[last][b]), vid(*rings[last][c])])
        faces.append([vid(*rings[0][a]), vid(*rings[0][c]), vid(*rings[0][b])])
    polys = [[(tc + r * math.cos(2 * math.pi * i / CIRCLE), sc + r * math.sin(2 * math.pi * i / CIRCLE)) for i in range(CIRCLE)] for tc, sc, r in holes]
    rims: list[dict[int, dict[str, list[tuple[float, tuple[float, float, float]]]]]] = [{k: {"up": [], "down": []} for k in range(CIRCLE)} for _ in holes]
    removed = [{"up": 0.0, "down": 0.0} for _ in holes]
    def reaches(cell: Sequence[Sequence[float]]) -> int | None:
        """Which outlet (if any) reaches into a cell; two in one cell are refused."""
        lo_t, hi_t = min(q[0] for q in cell), max(q[0] for q in cell)
        lo_s, hi_s = min(q[1] for q in cell), max(q[1] for q in cell)
        if hi_s - lo_s < 1e-9:
            return None                                                       # an upright face of the section: an outlet never reaches it
        found = None
        for h, (tc, sc, r) in enumerate(holes):
            if lo_t < tc + r - 1e-9 and hi_t > tc - r + 1e-9 and lo_s < sc + r - 1e-9 and hi_s > sc - r - 1e-9:
                if found is not None:
                    raise G.GeometryError("Two outlets are too close together to cut in the same part of the gutter")
                found = h
        return found

    def cut_cell(cell: list[tuple[float, float, float]], h: int) -> None:
        """A flat cell (in t, s, z, wound the way it faces) minus the outlet's polygon: kept part triangulated, its rim points recorded."""
        a3, b3, c3 = cell[0], cell[1], cell[2]
        normal = G.cross3([b3[q] - a3[q] for q in range(3)], [c3[q] - a3[q] for q in range(3)])
        if abs(normal[2]) < 1e-15:
            raise G.GeometryError("The outlet reaches an upright part of the gutter")
        which = "up" if normal[2] > 0 else "down"

        def lift(t: float, s: float) -> tuple[float, float, float]:             # the cell's own plane
            return (t, s, a3[2] - (normal[0] * (t - a3[0]) + normal[1] * (s - a3[1])) / normal[2])
        plan = [(q[0], q[1]) for q in cell]
        edges = [(plan[e], plan[(e + 1) % len(plan)]) for e in range(len(plan))]
        try:
            regions = P.difference(plan, [polys[h]])
        except P.PlanarError as exc:
            raise G.GeometryError(f"The outlet could not be cut through the gutter: {exc}") from exc
        kept = 0.0
        for outer, inner in regions:
            if inner:
                raise G.GeometryError("The outlet could not be cut through the gutter")
            exact = []                                                         # the boolean snaps to 0.1 µm: put points that lie on the cell's own
            for q in outer:                                                    # edges back on them exactly, so the cells beside it share its vertices
                for p0, p1 in edges:
                    if math.dist(q, p0) < 1e-6:
                        q = p0
                        break
                    if math.dist(q, p1) < 1e-6:
                        q = p1
                        break
                    e0, e1 = (p0, p1) if p0 < p1 else (p1, p0)                 # one direction for an edge, whichever cell asks
                    dx, dy = e1[0] - e0[0], e1[1] - e0[1]
                    L2 = dx * dx + dy * dy
                    if L2 > 0 and abs(dx * (q[1] - e0[1]) - dy * (q[0] - e0[0])) / math.sqrt(L2) < 1e-6:
                        lam = ((q[0] - e0[0]) * dx + (q[1] - e0[1]) * dy) / L2
                        if 0 < lam < 1:
                            q = (e0[0] + dx * lam, e0[1] + dy * lam) if abs(dx) > 1e-15 and abs(dy) > 1e-15 else \
                                ((e0[0], q[1]) if abs(dx) <= 1e-15 else (q[0], e0[1]))
                            break
                exact.append(q)
            outer = exact
            kept += abs(G.signed_area(outer))
            for a, b, c in G.triangulate(outer):
                pa, pb, pc = lift(*outer[a]), lift(*outer[b]), lift(*outer[c])
                nrm = G.cross3([pb[q] - pa[q] for q in range(3)], [pc[q] - pa[q] for q in range(3)])
                faces.append([vid(*pa), vid(*pb), vid(*pc)] if G.dot3(nrm, normal) > 0 else [vid(*pa), vid(*pc), vid(*pb)])
            for q in outer:                                                    # the points of this cell on the outlet's edge, for its wall
                for e in range(CIRCLE):
                    p0, p1 = polys[h][e], polys[h][(e + 1) % CIRCLE]
                    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
                    L2 = dx * dx + dy * dy
                    if abs(dx * (q[1] - p0[1]) - dy * (q[0] - p0[0])) / math.sqrt(L2) < 1e-6:
                        u_ = ((q[0] - p0[0]) * dx + (q[1] - p0[1]) * dy) / L2
                        slack = 1e-6 / math.sqrt(L2)                           # a micrometre: the boolean snaps to a tenth of one
                        if -slack <= u_ <= 1 + slack:
                            rims[h][e][which].append((min(1.0, max(0.0, u_)), lift(*q)))
        removed[h][which] += abs(G.signed_area(plan)) - kept

    for k in range(last):
        for i in range(n):
            j = (i + 1) % n
            A, B, C, D = rings[k][i], rings[k][j], rings[k + 1][j], rings[k + 1][i]
            square = abs(B[0] - A[0]) < 1e-12 and abs(C[0] - D[0]) < 1e-12
            if square:                                                         # a flat strip between two square stations
                h = reaches([A, B, C, D])
                if h is None:
                    faces += [[vid(*A), vid(*B), vid(*C)], [vid(*A), vid(*C), vid(*D)]]
                else:
                    cut_cell([A, B, C, D], h)
                continue
            for tri in ([A, B, C], [A, C, D]):                                 # beside a mitre the strip is two flat triangles
                h = reaches(tri)
                if h is None:
                    faces.append([vid(*tri[0]), vid(*tri[1]), vid(*tri[2])])
                else:
                    cut_cell(tri, h)
    for h, (tc, sc, r) in enumerate(holes):
        area = abs(G.signed_area(polys[h]))
        for which in ("up", "down"):
            if abs(removed[h][which] - area) > max(1e-7, 1e-4 * area):                 # to the boolean's snapping
                raise G.GeometryError("The outlet does not lie wholly on the bottom of the gutter")
        cx, cy = tc, sc
        for e in range(CIRCLE):
            chains = []
            for which in ("up", "down"):
                chain: list[tuple[float, tuple[float, float, float]]] = []
                for u_, p in sorted(rims[h][e][which], key=lambda q: q[0]):
                    if not chain or u_ - chain[-1][0] > 1e-9:
                        chain.append((u_, p))
                if len(chain) < 2:
                    raise G.GeometryError("The outlet's rim could not be followed round the gutter")
                chains.append(chain)
            up, down = chains
            i = j = 0
            tris: list[tuple[Any, Any, Any]] = []
            while i < len(up) - 1 or j < len(down) - 1:
                if j >= len(down) - 1 or (i < len(up) - 1 and up[i + 1][0] <= down[j + 1][0]):
                    tris.append((up[i][1], up[i + 1][1], down[j][1]))
                    i += 1
                else:
                    tris.append((up[i][1], down[j + 1][1], down[j][1]))
                    j += 1
            P0, Q0, R0 = up[0][1], up[-1][1], down[0][1]                          # one winding for the whole strip of wall, decided from the strip
            nrm = G.cross3([Q0[q] - P0[q] for q in range(3)], [R0[q] - P0[q] for q in range(3)])
            mid = ((P0[0] + Q0[0] + R0[0]) / 3, (P0[1] + Q0[1] + R0[1]) / 3)
            keep = nrm[0] * (cx - mid[0]) + nrm[1] * (cy - mid[1]) >= 0
            for p_, q_, r_ in tris:
                tri = [vid(*p_), vid(*q_), vid(*r_)]
                faces.append(tri if keep else [tri[0], tri[2], tri[1]])
    faces = [fc for fc in faces if len(set(fc)) == 3]
    if not G.is_closed((verts, faces)):
        raise G.GeometryError("The outlet could not be cut cleanly through the gutter")
    return verts, faces


def regenerate(project: Any, oid: str) -> None:
    """Rebuild a gutter and the rest of its run (a corner changes both sides of it), then the downpipes under them."""
    rid, _ = _roof(project, E.meta_of(project, oid))
    members = run_members(project, rid, run_key(project, oid))
    for g in members:
        _build(project, g)
    for g in members:
        for pid in downpipes_of(project, g):
            regenerate_downpipe(project, pid, host=False)


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
    """Rebuild a roof's gutters (and their downpipes) after the roof changed. Refuses, naming the gutter, if an eave one hangs on is gone or a corner
    can no longer be made; the roof edit that caused it is then undone whole by the command that ran it."""
    ids = gutters_of(project, roof_id)
    for gid in ids:
        try:
            _build(project, gid)
        except BadRequest as exc:
            raise BadRequest(f"The roof change leaves the gutter '{project.model.get(gid)['name']}' without its eave: {exc.message}",
                             hint="Delete that gutter first, or keep the eave") from exc
    for gid in ids:
        for pid in downpipes_of(project, gid):
            try:
                regenerate_downpipe(project, pid, host=False)
            except BadRequest as exc:
                raise BadRequest(f"The roof change leaves the downpipe '{project.model.get(pid)['name']}' without a place under its gutter: {exc.message}") from exc


def settle_after_delete(project: Any, roof_id: str, key: str) -> None:
    """After a gutter was deleted, the rest of its run is rebuilt: the neighbours' mitres become capped ends, and a fall that no longer suits the
    shorter chain (a closed loop cut open, a lone straight piece asked to fall to its corners) is reset to the nearest one that does."""
    for g in run_members(project, roof_id, key):
        try:
            frame(project, g)
        except BadRequest:
            E._attach(project, g, {"fall_to": "end"})
    for g in run_members(project, roof_id, key):                               # a downpipe that stood in a corner that is now a free end slides back
        f = frame(project, g)                                                     # along its gutter to the nearest place it fits
        for pid in downpipes_of(project, g):
            d = float(E.m(E.meta_of(project, pid), "diameter"))
            lo, hi = outlet_span(f["chain"], f["seg"], d / 2)
            t = float(E.m(E.meta_of(project, pid), "offset"))
            if lo <= hi and not lo - 1e-9 <= t <= hi + 1e-9:
                E._attach(project, pid, {"offset": min(max(t, lo), hi)})
    for g in run_members(project, roof_id, key):
        _build(project, g)
    for g in run_members(project, roof_id, key):
        for pid in downpipes_of(project, g):
            regenerate_downpipe(project, pid, host=False)


# ------------------------------------------------------------------------------------------------------------------------------ gutters
def _choice_type(value: Any) -> str:
    return E._choice(value, tuple(GUTTER_TYPES), "gutter type")


def _plan_edges(project: Any, rid: str, rmeta: Mapping[str, Any], p: Mapping[str, Any]) -> list[int]:
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
    return wanted


def _inherited(project: Any, p: Mapping[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """The run a new gutter is added to (``run`` is the id of any gutter in it): the run's key and its settings, which the new gutters take as they are."""
    if p.get("run") is None:
        return None
    gid = text(p.get("run"), "run", 80)
    if not E.alive(project, gid) or E.category(project, gid) != "gutter":
        raise NotFound(f"Gutter {gid} does not exist")
    clash = [k for k in ("gutter", "width", "depth", "thickness", "fall", "fall_to", "material", "rainfall", "gap", "drop") if p.get(k) is not None]
    if clash:
        raise BadRequest(f"A gutter added to a run takes the run's settings: {', '.join(clash)} cannot be given here", hint="Change them on the run in Properties")
    meta = E.meta_of(project, gid)
    return run_key(project, gid, meta), {k: E.m(meta, k) for k in RUN_KEYS if E.m(meta, k) is not None}


def plan_gutters(project: Any, p: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a request to hang gutters, and lay it out, without changing the model: the settings, the chains, and for each chain its layout and
    default outlets. ``add_gutters`` builds exactly this and ``preview`` shows it."""
    rid = text(p.get("roof"), "roof", 80)
    if not E.alive(project, rid) or E.category(project, rid) != "roof":
        raise NotFound(f"Roof {rid} does not exist")
    rmeta = E.meta_of(project, rid)
    wanted = _plan_edges(project, rid, rmeta, p)
    inherit = _inherited(project, p)
    boundary = [tuple(q) for q in E.m(rmeta, "boundary")]
    if inherit:
        key, base = inherit
        kind = base["gutter"]
        width, depth, thickness, fall = (float(base[k]) for k in ("width", "depth", "thickness", "fall"))
        fall_to, material, rainfall = base.get("fall_to", "end"), base.get("material", "Zinc"), float(base.get("rainfall", RAINFALL_DEFAULT))
        gap, drop = float(base.get("gap", 0.0)), float(base.get("drop", RIM_DROP))
    else:
        key = None
        kind = _choice_type(p.get("gutter", "half_round"))
        spec = GUTTER_TYPES[kind]
        width = number(p.get("width", spec["width"]), "width", 30, 1000) / 1000.0
        depth = number(p.get("depth", spec["depth"]), "depth", 20, 1000) / 1000.0
        thickness = number(p.get("thickness", THICKNESS_DEFAULT), "thickness", 0.2, 20) / 1000.0
        fall = number(p.get("fall", FALL_DEFAULT), "fall", 0, 200) / 1000.0
        fall_to = p.get("fall_to")
        material = E._choice(p.get("material", "Zinc"), GUTTER_MATERIALS, "material")
        rainfall = number(p.get("rainfall", RAINFALL_DEFAULT), "rainfall", 0.001, 1.0)
        gap = number(p.get("gap", 0.0), "gap", 0, 500) / 1000.0
        drop = number(p.get("drop", RIM_DROP * 1000.0), "drop", 0, 500) / 1000.0
    section(kind, width, depth, thickness)                                      # refuse a sheet too thick for the section before anything is made
    if fall_to is not None:
        fall_to = E._choice(fall_to, FALL_TO, "fall_to")
    ext_start = number(p.get("ext_start", 0.0), "ext_start", -5000, 5000) / 1000.0
    ext_end = number(p.get("ext_end", 0.0), "ext_end", -5000, 5000) / 1000.0
    end_start = E._choice(p.get("end_start", "cap"), END_KINDS, "end_start")
    end_end = E._choice(p.get("end_end", "cap"), END_KINDS, "end_end")
    connect = p.get("connect", True)
    if not isinstance(connect, bool):
        raise BadRequest("connect must be true or false")
    diameter = number(p.get("diameter", PIPE_DEFAULT), "diameter", 20, 500) / 1000.0
    if connect:
        chains = order_edges(len(boundary), wanted)
        if inherit:
            joined = run_members(project, rid, key)
            chains = order_edges(len(boundary), [*wanted, *(int(E.m(E.meta_of(project, g), "edge")) for g in joined)])
            chains = [(o, c) for o, c in chains if set(o) & set(wanted)]
            if any(not (set(o) & {int(E.m(E.meta_of(project, g), "edge")) for g in joined}) for o, _ in chains):
                raise BadRequest("A gutter added to a run must continue it: its eave has to touch an eave the run already hangs on")
    else:
        if inherit:
            raise BadRequest("A gutter added to a run is always connected to it")
        chains = [([e], False) for e in wanted]
    runs = []
    for order, closed in chains:
        news = [e for e in order if e in wanted]
        segments = {e: {"ext_start": 0.0, "ext_end": 0.0, "end_start": "cap", "end_end": "cap"} for e in order}
        if inherit:
            for g in run_members(project, rid, key):
                segments[int(E.m(E.meta_of(project, g), "edge"))] = _segment_params(project, [g])[int(E.m(E.meta_of(project, g), "edge"))]
        first, last = order[0], order[-1]
        if not closed:
            if first in wanted and abs(ext_start) > 0:
                segments[first]["ext_start"] = ext_start
            if last in wanted and abs(ext_end) > 0:
                segments[last]["ext_end"] = ext_end
            if first in wanted:
                segments[first]["end_start"] = end_start
            if last in wanted:
                segments[last]["end_end"] = end_end
        elif abs(ext_start) > 0 or abs(ext_end) > 0:
            raise BadRequest("A closed loop of gutters has no free ends to extend")
        use_fall_to = fall_to or ("corners" if closed else "end")
        chain = layout(boundary, order, closed, width=width, gap=gap, fall_to=use_fall_to, segments=segments)
        runs.append({"order": order, "new": news, "closed": closed, "chain": chain, "segments": segments, "fall_to": use_fall_to,
                     "outlets": default_outlets(chain, diameter)})
    return {"roof": rid, "rmeta": rmeta, "key": key, "kind": kind, "width": width, "depth": depth, "thickness": thickness, "fall": fall, "material": material,
            "rainfall": rainfall, "gap": gap, "drop": drop, "diameter": diameter, "runs": runs, "boundary": boundary}


def preview_gutters(project: Any, p: Mapping[str, Any], to_m: float) -> dict[str, Any]:
    """What ``add_gutters`` would make for the same request, as plan outlines in drawing units, without making it: the chains, each segment's mitred
    footprint, the low points and where the downpipes would stand. A request that cannot be made comes back as ``problems`` (nothing is raised)."""
    try:
        plan = plan_gutters(project, p)
    except BadRequest as exc:
        return {"runs": [], "problems": [exc.message]}
    f = 1.0 / to_m
    runs = []
    for r in plan["runs"]:
        chain = r["chain"]
        by_edge = {sg["edge"]: sg for sg in chain["segments"]}
        runs.append({"edges": r["order"], "new_edges": r["new"], "closed": r["closed"], "fall_to": r["fall_to"], "length_m": round(chain["total"], 6),
                     "lows_m": [round(l, 6) for l in chain["lows"]],
                     "segments": [{"edge": sg["edge"], "length_m": round(sg["length"], 6), "new": sg["edge"] in r["new"],
                                   "start": "mitre" if sg["k0"] is not None else r["segments"][sg["edge"]]["end_start"],
                                   "end": "mitre" if sg["k1"] is not None else r["segments"][sg["edge"]]["end_end"],
                                   "polygon": [[x * f, y * f] for x, y in plan_outline(chain, sg)]} for sg in chain["segments"]],
                     "outlets": [{"edge": e, "offset_m": round(t, 6), "at": [round((by_edge[e]["a"][0] + by_edge[e]["u"][0] * t + by_edge[e]["n"][0] * (plan["gap"] + plan["diameter"] / 2)) * f, 6),
                                                                              round((by_edge[e]["a"][1] + by_edge[e]["u"][1] * t + by_edge[e]["n"][1] * (plan["gap"] + plan["diameter"] / 2)) * f, 6)]}
                                 for e, t in r["outlets"]]})
    return {"runs": runs, "problems": [], "outline": [[x * f, y * f] for x, y in plan["boundary"]]}


def add_gutters(project: Any, p: Mapping[str, Any], to_m: float) -> list[str]:
    """Gutters on the eaves of a roof. ``edges`` lists the outline edges to hang one on (default: every eave of the roof). Edges that follow one another
    are joined into one run with mitred corners (``connect`` false keeps them apart, ``run`` adds to an existing run); a downpipe goes at each low point
    (``downpipe`` false for none). All or none are made."""
    plan = plan_gutters(project, p)
    rid, rmeta, kind = plan["roof"], plan["rmeta"], plan["kind"]
    base = GUTTER_TYPES[kind]
    name = text(p.get("name") or base["name"], "name", 80)
    made: list[str] = []
    try:
        for r in plan["runs"]:
            key = plan["key"]
            ids: dict[int, str] = {}
            for e in r["new"]:
                oid = project.model.add_box(width=1.0, depth=1.0, height=1.0, name=f"{name} · eave {e + 1}")
                made.append(oid)
                ids[e] = oid
                key = key or f"run:{oid}"
                seg = r["segments"][e]
                E._attach(project, oid, {"category": "gutter", "type": base["name"], "gutter": kind, "level": E.m(rmeta, "level"), "roof": rid, "edge": e, "run": key,
                                         "width": plan["width"], "depth": plan["depth"], "thickness": plan["thickness"], "fall": plan["fall"], "fall_to": r["fall_to"],
                                         "material": plan["material"], "rainfall": plan["rainfall"], "gap": plan["gap"], "drop": plan["drop"],
                                         "ext_start": seg["ext_start"], "ext_end": seg["ext_end"], "end_start": seg["end_start"], "end_end": seg["end_end"]})
            if plan["key"]:                                                       # added to a run: it must fall the way the whole run now falls
                for g in run_members(project, rid, key):
                    E._attach(project, g, {"fall_to": r["fall_to"]})
        for oid in made:
            _build(project, oid)
        if p.get("downpipe", True):
            for r in plan["runs"]:
                have = {int(E.m(E.meta_of(project, g), "edge")): g for g in gutters_of(project, rid)}
                for edge, offset in r["outlets"]:
                    if plan["key"] and downpipes_of(project, have[edge]):
                        continue                                                  # the run already drains there
                    made.append(add_downpipe(project, {"gutter": have[edge], "offset": offset, "diameter": plan["diameter"] * 1000.0}, to_m))
    except BadRequest:
        for oid in reversed(made):
            if E.alive(project, oid):
                project.model.remove(oid)
        raise
    return made


def share(project: Any, oid: str, values: Mapping[str, Any]) -> None:
    """A setting of the run (type, size, fall, material ...) is the whole run's: copy it to every other gutter of the run."""
    shared = {k: v for k, v in values.items() if k in RUN_KEYS}
    if not shared:
        return
    rid, _ = _roof(project, E.meta_of(project, oid))
    for g in run_members(project, rid, run_key(project, oid)):
        if g != oid:
            E._attach(project, g, shared)


def set_gutter_property(project: Any, oid: str, meta: Mapping[str, Any], key: str, value: Any) -> dict[str, Any]:
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
        return {"fall_to": E._choice(value, FALL_TO, "fall_to")}
    if key == "material":
        return {"material": E._choice(value, GUTTER_MATERIALS, "material")}
    if key == "rainfall":
        return {"rainfall": number(value, key, 0.001, 1.0)}
    if key == "gap":
        return {"gap": number(value, key, 0, 500) / 1000.0}
    if key == "drop":
        return {"drop": number(value, key, 0, 500) / 1000.0}
    if key in ("ext_start", "ext_end"):
        return {key: number(value, key, -5000, 5000) / 1000.0}                  # a mitred end refuses it when the chain is laid out
    if key in ("end_start", "end_end"):
        return {key: E._choice(value, END_KINDS, key)}
    raise BadRequest(f"'{key}' is not an editable gutter property")


# ---------------------------------------------------------------------------------------------------------------------------- downpipes
# A downpipe is one element made of an ordered *assembly* of components, top to bottom: downpipe sections, offset bends, elbows, a Y-branch, a hopper
# head and a shoe. Connectors are not stored: one is made wherever two downpipe sections meet. Every component has an id that never changes while it
# exists, so the Properties panel, the 3D picker and a right-click edit all name the same component.
#
# Where it hangs: the centre of the outlet lies on the centre line of the gutter's section (half the gutter's width out from its back), at ``offset``
# along it, so the downpipe is centred under the gutter whatever its type, size or turn. The outlet is a real hole cut through the gutter's sheet (the
# bore of the pipe), and the top of the first component follows the gutter's underside exactly round its rim (a half-round gutter's curve, a box
# gutter's flat bottom), so the two touch without a gap or an overlap.
#
# How far down it goes: one downpipe section may be *free* (``auto``): its length is whatever brings the end of the assembly to the discharge height
# ``bottom``. Without a free section the end of the assembly is where its components put it, and ``bottom`` follows. The geometry of every fitting is
# the centre line of the pipe (straight runs and circular bends of ``bend_radius``) with the pipe's section swept along it; nothing is taken from a
# manufacturer's catalogue: the angles 72°, 45° and 40° are offered because the reference sheet names them, and any angle from 5° to 90° can be typed.
COMPONENT_KINDS = {"pipe": "Downpipe section", "offset_bend": "Offset bend", "elbow": "Elbow", "shoe": "Shoe", "y_branch": "Y-branch", "hopper": "Hopper head"}
BEND_PRESETS = (72.0, 45.0, 40.0)                                        # the fitting angles of the reference sheet; any 5-90° is accepted
DIRECTIONS = {"out": "Away from the building", "in": "Towards the building", "start": "Towards the gutter's start", "end": "Towards the gutter's end"}
# What each component is in IFC (the entity and its predefined type, from the shipped IFC 4.3 type catalogue). The connector is generated.
COMPONENT_IFC = {"pipe": ("IfcPipeSegment", "RIGIDSEGMENT"), "offset_bend": ("IfcPipeFitting", "BEND"), "elbow": ("IfcPipeFitting", "BEND"),
                 "shoe": ("IfcPipeFitting", "EXIT"), "y_branch": ("IfcPipeFitting", "JUNCTION"), "hopper": ("IfcPipeFitting", "ENTRY"),
                 "connector": ("IfcPipeFitting", "CONNECTOR")}
MIN_SECTION = 0.05                                                       # m: the shortest downpipe section
ARC_STEPS = 8                                                            # stations along a bend


def _mm(v: float) -> float:
    return round(v * 1000.0, 6)


def components_of(meta: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The stored assembly, or the one a downpipe made before assemblies existed is: one free downpipe section."""
    stored = E.m(meta, "components")
    if not stored:
        return [{"id": "c1", "kind": "pipe", "auto": True}]
    return [dict(c) for c in stored]


def _defaults(kind: str, d: float, wall: float) -> dict[str, Any]:
    """The starting values of a new component, as modelling defaults (every one is editable); lengths in m, angles in degrees."""
    if kind == "pipe":
        return {"auto": False, "length": 1.0}
    if kind == "offset_bend":
        return {"angle": 45.0, "offset": 0.3, "direction": "in"}
    if kind == "elbow":
        return {"angle": 45.0, "direction": "out"}
    if kind == "shoe":
        return {"angle": 45.0, "direction": "out", "length": d}
    if kind == "y_branch":
        return {"angle": 45.0, "direction": "start", "branch_length": 3 * d, "branch_diameter": d, "body_length": 3 * d}
    if kind == "hopper":
        return {"width": max(0.25, 2.5 * d), "depth": max(0.2, 2 * d), "height": max(0.25, 2.5 * d), "drop": 0.05, "thickness": wall}
    raise BadRequest(f"A downpipe component is one of: {', '.join(COMPONENT_KINDS)}")


def _next_id(comps: Sequence[Mapping[str, Any]]) -> str:
    used = [int(str(c["id"])[1:]) for c in comps if str(c.get("id", "")).startswith("c") and str(c["id"])[1:].isdigit()]
    return f"c{max(used, default=0) + 1}"


def _zout(kind: str, w: float, d: float, s: float) -> float:
    """Height of the outside of a gutter's section below its back rim, ``s`` metres out from its back (the lowest point of the outline there)."""
    pts = _outside(kind, w, d)
    best: float | None = None
    for (s0, z0), (s1, z1) in zip(pts, pts[1:]):
        if abs(s1 - s0) < 1e-12:
            continue
        lo, hi = min(s0, s1), max(s0, s1)
        if lo - 1e-12 <= s <= hi + 1e-12:
            z = z0 + (z1 - z0) * (s - s0) / (s1 - s0)
            best = z if best is None else min(best, z)
    if best is None:
        raise BadRequest("The outlet lies outside the gutter's section")
    return best


def underside(g: Mapping[str, Any], x: float, y: float) -> float:
    """Absolute height of the gutter's outside (its underside) at a plan point under it."""
    t = (x - g["a"][0]) * g["u"][0] + (y - g["a"][1]) * g["u"][1]
    s = (x - g["a"][0]) * g["n"][0] + (y - g["a"][1]) * g["n"][1] - g["gap"]
    return rim_at(g, t) + _zout(E.m(g["meta"], "gutter"), g["width"], g["depth"], s)


def pipe_frame(project: Any, oid: str) -> dict[str, Any]:
    """Where a downpipe's outlet is (centred on the gutter's section at ``offset`` along it) and its main sizes. ``top`` is the gutter's underside on
    the outlet's centre line; ``bore`` the radius of the hole cut in the gutter."""
    meta = E.meta_of(project, oid)
    if E.m(meta, "category") != "downpipe":
        raise BadRequest("That element is not a downpipe")
    gid = E.m(meta, "gutter")
    if not gid or not E.alive(project, gid) or E.category(project, gid) != "gutter":
        raise BadRequest("This downpipe has lost its gutter")
    g = frame(project, gid)
    d, t = float(E.m(meta, "diameter")), float(E.m(meta, "offset"))
    s = g["gap"] + g["width"] / 2                                                   # the centre line of the gutter's section
    at = (g["a"][0] + g["u"][0] * t + g["n"][0] * s, g["a"][1] + g["u"][1] * t + g["n"][1] * s)
    top = rim_at(g, t) + _zout(E.m(g["meta"], "gutter"), g["width"], g["depth"], g["width"] / 2)
    wall = float(E.m(meta, "wall"))
    return {"gutter": gid, "g": g, "meta": meta, "diameter": d, "offset": t, "bottom": float(E.m(meta, "bottom")), "top": top, "at": at, "wall": wall,
            "bore": d / 2 - wall, "bend_radius": float(E.m(meta, "bend_radius", d)), "connector_length": float(E.m(meta, "connector_length", d))}


def _dir_vector(g: Mapping[str, Any], name: str) -> tuple[float, float, float]:
    if name not in DIRECTIONS:
        raise BadRequest(f"A direction is one of: {', '.join(DIRECTIONS)}")
    (ux, uy), (nx, ny) = g["u"], g["n"]
    return {"out": (nx, ny, 0.0), "in": (-nx, -ny, 0.0), "start": (-ux, -uy, 0.0), "end": (ux, uy, 0.0)}[name]


def _rotate_toward(d: Sequence[float], toward: Sequence[float], angle: float) -> tuple[float, float, float]:
    b = G.unit3([toward[c] - d[c] * G.dot3(toward, d) for c in range(3)])
    return (math.cos(angle) * d[0] + math.sin(angle) * b[0], math.cos(angle) * d[1] + math.sin(angle) * b[1], math.cos(angle) * d[2] + math.sin(angle) * b[2])


def _angle(c: Mapping[str, Any], what: str) -> float:
    a = float(c.get("angle", 45.0))
    if not 5.0 <= a <= 90.0:
        raise BadRequest(f"The {what}'s angle must be between 5° and 90° (72°, 45° and 40° are the usual fittings)")
    return math.radians(a)


def assembly(project: Any, oid: str, comps: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Every component of a downpipe as a solid in world coordinates, with what it is, where its centre line runs and how long it is, plus the
    generated connectors and the end of the assembly. Pure: reads the model, writes nothing. Raises ``BadRequest`` for an assembly that cannot be built."""
    pf = pipe_frame(project, oid)
    g = pf["g"]
    comps = components_of(pf["meta"]) if comps is None else [dict(c) for c in comps]
    D, wall, R = pf["diameter"], pf["wall"], pf["bend_radius"]
    ro, ri = D / 2, D / 2 - wall
    if not comps:
        raise BadRequest("A downpipe needs at least one component")
    ids = [str(c.get("id")) for c in comps]
    if len(set(ids)) != len(ids):
        raise BadRequest("Two components of the downpipe have the same id")
    autos = [c for c in comps if c["kind"] == "pipe" and c.get("auto")]
    if len(autos) > 1:
        raise BadRequest("Only one downpipe section can take up the remaining length")
    for i, c in enumerate(comps):
        if c["kind"] not in COMPONENT_KINDS:
            raise BadRequest(f"'{c['kind']}' is not a downpipe component")
        if c["kind"] == "hopper" and i != 0:
            raise BadRequest("A hopper head sits at the top of the downpipe, under the gutter's outlet")
        if c["kind"] == "shoe" and i != len(comps) - 1:
            raise BadRequest("A shoe is the last component: it turns the water out at the bottom")
    if R < ro:
        raise BadRequest(f"The bend radius ({_mm(R):.0f} mm) must be at least the pipe's radius ({_mm(ro):.0f} mm)")

    def onto_gutter(p: list[float], axis: Sequence[float]) -> list[float]:
        if abs(axis[2]) < 0.5:
            raise BadRequest("The first component must leave the gutter downwards")
        z = underside(g, p[0], p[1])
        k = (z - p[2]) / axis[2]
        return [p[0] + axis[0] * k, p[1] + axis[1] * k, z]

    def walk(auto_length: float) -> dict[str, Any]:
        pos = [pf["at"][0], pf["at"][1], pf["top"]]
        d = (0.0, 0.0, -1.0)
        parts: list[dict[str, Any]] = []
        first = True
        prev_pipe: dict[str, Any] | None = None
        connectors: list[dict[str, Any]] = []
        auto_dir: tuple[float, float, float] | None = None
        for c in comps:
            kind, cid = c["kind"], str(c["id"])
            start = list(pos)
            start_onto = onto_gutter if first else None
            meshes: list[G.Mesh] = []
            if kind == "hopper":
                W, Dp, H, drop, th = (float(c.get(k, v)) for k, v in (("width", 0.25), ("depth", 0.2), ("height", 0.25), ("drop", 0.05), ("thickness", wall)))
                if not (W - 2 * th > D + 0.002 and Dp - 2 * th > D + 0.002):
                    raise BadRequest(f"The hopper head must be wider than the pipe: its inside is {_mm(W - 2 * th):.0f} × {_mm(Dp - 2 * th):.0f} mm for a {_mm(D):.0f} mm pipe")
                if H < D or drop < 0 or th <= 0 or th >= min(W, Dp) / 4:
                    raise BadRequest("The hopper head needs a height of at least the pipe's diameter, a drop of 0 or more and a sensible sheet thickness")
                z0 = pos[2] - drop
                (ux, uy), (nx, ny) = g["u"], g["n"]
                cx, cy = pos[0], pos[1]

                def corner(hw: float, hd: float, z: float) -> list[list[float]]:
                    return [[cx + ux * a * hw + nx * b * hd, cy + uy * a * hw + ny * b * hd, z] for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
                ot, ob = corner(W / 2, Dp / 2, z0), corner(D / 2, D / 2, z0 - H)
                it, ib = corner(W / 2 - th, Dp / 2 - th, z0), corner(D / 2 - th, D / 2 - th, z0 - H)
                verts = ot + ob + it + ib
                faces: list[list[int]] = []
                for i in range(4):
                    j = (i + 1) % 4
                    faces += [[i, j, 4 + j], [i, 4 + j, 4 + i]]                         # outside
                    faces += [[8 + i, 12 + j, 8 + j], [8 + i, 12 + i, 12 + j]]          # inside
                    faces += [[i, 8 + j, j], [i, 8 + i, 8 + j]]                         # top rim
                    faces += [[4 + i, 4 + j, 12 + j], [4 + i, 12 + j, 12 + i]]          # bottom rim
                meshes.append(E._positive_volume((verts, faces)))
                stub_end = z0 - H / 4                                                    # the outlet spigot drops into the hopper, clear of its sides
                inner_half = min(W / 2 - th, Dp / 2 - th) + (min(D / 2 - th, D / 2 - th) - min(W / 2 - th, Dp / 2 - th)) * 0.25
                if inner_half <= ro + 0.001:
                    raise BadRequest("The outlet would touch the hopper head's sides: make the hopper wider or taller")
                meshes.append(G.tube([(pos, d), ([pos[0], pos[1], stub_end], d)], ro, ri, sides=CIRCLE, start_onto=onto_gutter))
                pos = [cx, cy, z0 - H]
                length = H + drop
                axis = [start, list(pos)]
            elif kind == "pipe":
                dia = float(c.get("diameter", D))
                if abs(dia - D) > 1e-9:
                    raise BadRequest(f"A {_mm(dia):.0f} mm section cannot join the {_mm(D):.0f} mm downpipe: SPADRA has no reducer, so every section has the downpipe's diameter",
                                     hint="Change the downpipe's diameter instead")
                length = auto_length if c.get("auto") else float(c.get("length", 1.0))
                if c.get("auto"):
                    auto_dir = d
                if length < (0.0 if c.get("auto") else MIN_SECTION):
                    raise BadRequest(f"A downpipe section is at least {MIN_SECTION * 1000:.0f} mm long")
                end = G._v3(pos, d, length)
                if length > 1e-9:
                    meshes.append(G.tube([(pos, d), (end, d)], ro, ri, sides=CIRCLE, start_onto=start_onto))
                if prev_pipe is not None and length > 1e-9:
                    Lc = pf["connector_length"]
                    if Lc > min(prev_pipe["length"], length):
                        raise BadRequest(f"The connector ({_mm(Lc):.0f} mm) is longer than a section it joins: make the sections longer or the connector shorter")
                    a0, a1 = G._v3(pos, d, -Lc / 2), G._v3(pos, d, Lc / 2)
                    connectors.append({"id": f"k{len(connectors) + 1}", "between": [prev_pipe["id"], cid], "at": list(pos),
                                       "mesh": G.tube([(a0, d), (a1, d)], ro + wall, ro, sides=CIRCLE)})
                pos = end
                axis = [start, list(pos)]
            elif kind in ("elbow", "offset_bend", "shoe"):
                theta = _angle(c, COMPONENT_KINDS[kind].lower())
                toward = _dir_vector(g, str(c.get("direction", "out")))
                if abs(G.dot3(toward, d)) > 0.999:
                    raise BadRequest("A bend cannot turn toward the direction the pipe already runs")
                path = G.arc_path(pos, d, toward, theta, R, ARC_STEPS)
                pos, d1 = path[-1][0], path[-1][1]
                length = R * theta
                if kind == "offset_bend":
                    off = float(c.get("offset", 0.3))
                    horiz = 2 * R * (1 - math.cos(theta))
                    if abs(d[2]) < 0.999:
                        raise BadRequest("An offset bend is fitted on a vertical run of the downpipe")
                    s = (off - horiz) / math.sin(theta)
                    if s < -1e-9:
                        raise BadRequest(f"An offset of {_mm(off):.0f} mm is too small for two {math.degrees(theta):.0f}° bends of {_mm(R):.0f} mm radius: "
                                         f"the least is {_mm(horiz):.0f} mm", hint="Use a smaller angle, a larger offset or a smaller bend radius")
                    s = max(0.0, s)
                    mid = G._v3(pos, d1, s)
                    back = G.arc_path(mid, d1, d, theta, R, ARC_STEPS)                   # the second bend turns the pipe back to where it ran
                    path = path + [(mid, d1)] + back[1:]
                    pos, d1 = back[-1][0], back[-1][1]
                    length = 2 * R * theta + s
                if kind == "shoe":
                    leg = float(c.get("length", D))
                    if leg < 0:
                        raise BadRequest("The shoe's outlet leg cannot be negative")
                    if leg > 1e-9:
                        tip = G._v3(pos, d1, leg)
                        path = path + [(tip, d1)]
                        pos = tip
                        length += leg
                meshes.append(G.tube(path, ro, ri, sides=CIRCLE, start_onto=start_onto))
                d = d1
                axis = [start, list(pos)]
            else:                                                                        # y_branch
                theta = _angle(c, "Y-branch")
                body = float(c.get("body_length", 3 * D))
                lb = float(c.get("branch_length", 3 * D))
                rb = float(c.get("branch_diameter", D)) / 2
                if body < D or lb <= ro or rb <= wall or rb > ro + 1e-9:
                    raise BadRequest("The Y-branch needs a body at least one diameter long, a branch reaching past the pipe, and a branch no wider than the pipe")
                end = G._v3(pos, d, body)
                meshes.append(G.tube([(pos, d), (end, d)], ro, ri, sides=CIRCLE, start_onto=start_onto))
                toward = _dir_vector(g, str(c.get("direction", "start")))
                if abs(G.dot3(toward, d)) > 0.999:
                    raise BadRequest("The branch cannot leave along the pipe's own axis")
                junction = G._v3(pos, d, body / 2)
                bd = _rotate_toward(d, toward, theta)
                main_d = d

                def onto_main(p: list[float], axis: Sequence[float], J: list[float] = junction, md: Sequence[float] = main_d) -> list[float]:
                    w0 = [p[k] - J[k] for k in range(3)]
                    w0p = [w0[k] - md[k] * G.dot3(w0, md) for k in range(3)]
                    ap = [axis[k] - md[k] * G.dot3(axis, md) for k in range(3)]
                    A, B, C = G.dot3(ap, ap), 2 * G.dot3(w0p, ap), G.dot3(w0p, w0p) - ro * ro
                    disc = B * B - 4 * A * C
                    if C <= 1e-15:                                             # on or inside the pipe: rounding must not lose the root at its surface
                        disc = max(disc, 0.0)
                    if A < 1e-12 or disc < 0:
                        raise BadRequest("The branch does not leave the pipe")
                    k = (-B + math.sqrt(disc)) / (2 * A)
                    return [p[0] + axis[0] * k, p[1] + axis[1] * k, p[2] + axis[2] * k]
                meshes.append(G.tube([(junction, bd), (G._v3(junction, bd, lb), bd)], rb, rb - wall, sides=CIRCLE, start_onto=onto_main))
                pos = end
                length = body
                axis = [start, list(pos)]
            mesh, _ = G.join_meshes(meshes)
            ifc = COMPONENT_IFC[kind]
            parts.append({"id": cid, "kind": kind, "label": COMPONENT_KINDS[kind], "mesh": mesh, "axis": axis, "length": length, "ifc": ifc, "spec": dict(c)})
            prev_pipe = {"id": cid, "length": length} if kind == "pipe" and length > 1e-9 else None
            first = False
        return {"parts": parts, "connectors": connectors, "end": pos, "dir": d, "auto_dir": auto_dir}

    if autos:
        trial = walk(0.0)
        dz = trial["auto_dir"][2] if trial["auto_dir"] else 0.0
        if dz > -0.1:
            raise BadRequest("The free downpipe section must run downwards to reach the discharge height")
        length = (trial["end"][2] - pf["bottom"]) / -dz
        if length < MIN_SECTION:
            raise BadRequest(f"The components above already reach {trial['end'][2]:.3f} m: the free section would be {_mm(length):.0f} mm, "
                             f"less than {MIN_SECTION * 1000:.0f} mm. Lower the discharge height or shorten the components")
        out = walk(length)
    else:
        out = walk(0.0)
    out["frame"] = pf
    out["bottom"] = out["end"][2]
    return out


def _tube(cx: float, cy: float, ro: float, ri: float, z0: float, z1: float) -> tuple[list[list[float]], list[list[int]]]:
    """A straight vertical tube (kept for callers that draw a plain pipe)."""
    return G.tube([([cx, cy, z1], (0.0, 0.0, -1.0)), ([cx, cy, z0], (0.0, 0.0, -1.0))], ro, ri, sides=CIRCLE)


def _check_pipe(f: Mapping[str, Any]) -> None:
    g = f["g"]
    if not 0.02 <= f["diameter"] <= 0.5:
        raise BadRequest("A downpipe is between 20 and 500 mm across")
    if f["diameter"] > g["width"] + 1e-9:
        raise BadRequest(f"A {f['diameter'] * 1000:.0f} mm downpipe is wider than the {g['width'] * 1000:.0f} mm gutter above it")
    if not 0.0005 <= f["wall"] < f["diameter"] / 2:
        raise BadRequest("The pipe wall must be thinner than the pipe's radius")
    lo, hi = outlet_span(g["chain"], g["seg"], f["diameter"] / 2)
    if f["offset"] < lo - 1e-9 or f["offset"] > hi + 1e-9:
        raise BadRequest(f"The downpipe must stand within the gutter piece, between {lo * 1000:.0f} mm and {hi * 1000:.0f} mm along it (centred under the gutter, clear of its ends and mitres)")
    if f["bore"] > g["width"] / 2 - g["thickness"] - 0.001:
        raise BadRequest(f"The outlet ({f['bore'] * 2000:.0f} mm bore) is too wide to cut in the bottom of a {g['width'] * 1000:.0f} mm gutter",
                         hint="Use a smaller downpipe or a wider gutter")


def outlet_holes(project: Any, gutter_id: str, g: Mapping[str, Any] | None = None) -> list[tuple[str, tuple[float, float, float]]]:
    """The holes the downpipes under a gutter cut in it: (downpipe id, (distance along the eave, distance out from the eave line, radius of the bore))."""
    pipes = downpipes_of(project, gutter_id)
    if not pipes:
        return []
    if g is None:
        g = frame(project, gutter_id)
    out = []
    for pid in pipes:
        meta = E.meta_of(project, pid)
        d, t, wall = float(E.m(meta, "diameter")), float(E.m(meta, "offset")), float(E.m(meta, "wall"))
        out.append((pid, (t, g["gap"] + g["width"] / 2, d / 2 - wall)))
    return out


def regenerate_downpipe(project: Any, oid: str, *, host: bool = True) -> None:
    """Rebuild a downpipe's assembly, and (``host``) the gutter piece it hangs under, whose outlet hole follows it."""
    f = pipe_frame(project, oid)
    _check_pipe(f)
    if host:
        _build(project, f["gutter"])
    a = assembly(project, oid)
    if f["top"] - a["bottom"] < 0.1:
        raise BadRequest("A downpipe must run down at least 100 mm from the gutter: lower its discharge height")
    meshes = [p["mesh"] for p in a["parts"]] + [k["mesh"] for k in a["connectors"]]
    mesh, counts = G.join_meshes(meshes)
    runs = [{"id": p["id"], "count": n} for p, n in zip(a["parts"] + a["connectors"], counts)]
    stored = {"component_faces": runs}
    if abs(a["bottom"] - f["bottom"]) > 1e-9:
        stored["bottom"] = a["bottom"]                                                 # no free section: the discharge height is where the components end
    stored["end_point"] = [round(v, 9) for v in a["end"]]
    E._attach(project, oid, stored)
    E._set_mesh(project, oid, E._positive_volume(mesh))


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
                             "diameter": diameter, "bottom": bottom, "wall": PIPE_WALL / 1000.0, "material": E.m(g["meta"], "material", "Zinc"),
                             "components": [{"id": "c1", "kind": "pipe", "auto": True}]})
    try:
        regenerate_downpipe(project, oid)
    except BadRequest:
        project.model.remove(oid)
        _build(project, gid)
        raise
    return oid


def _component_value(kind: str, field: str, value: Any, g: Mapping[str, Any]) -> Any:
    """One validated component field (mm and degrees in, m and degrees stored)."""
    lengths = {"length": (MIN_SECTION * 1000, 100000), "offset": (1, 5000), "branch_length": (10, 5000), "branch_diameter": (20, 500), "body_length": (20, 5000),
               "width": (30, 2000), "depth": (30, 2000), "height": (30, 2000), "drop": (0, 2000), "thickness": (0.5, 20), "diameter": (20, 500)}
    allowed = {"pipe": ("length", "auto", "diameter"), "offset_bend": ("angle", "offset", "direction"), "elbow": ("angle", "direction"),
               "shoe": ("angle", "direction", "length"), "y_branch": ("angle", "direction", "branch_length", "branch_diameter", "body_length"),
               "hopper": ("width", "depth", "height", "drop", "thickness")}[kind]
    if field == "sfb":
        from . import sfb as SFB
        return SFB.normalise(1, value) if value not in (None, "") else None
    if field not in allowed:
        raise BadRequest(f"'{field}' is not a parameter of a {COMPONENT_KINDS[kind].lower()}", hint=f"It has: {', '.join(allowed)}")
    if field == "auto":
        return bool(value) if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on", "free")
    if isinstance(value, str) and value.strip() and field not in ("direction", "auto"):
        try:
            value = float(value)                                              # a choice in the panel sends its value as text
        except ValueError:
            raise BadRequest(f"{field.replace('_', ' ')} must be a number") from None
    if field == "angle":
        return number(value, "angle (°)", 5, 90)
    if field == "direction":
        return E._choice(value, tuple(DIRECTIONS), "direction")
    lo, hi = lengths[field]
    return number(value, f"{field.replace('_', ' ')} (mm)", lo, hi) / 1000.0


def edit_components(project: Any, oid: str, action: str, *, component: str | None = None, kind: str | None = None, index: int | None = None,
                    field: str | None = None, value: Any = None) -> dict[str, Any]:
    """Change a downpipe's assembly: ``add`` a component of ``kind`` (at ``index``, default above a shoe or at the bottom), ``remove``, ``replace`` it
    with another ``kind`` (same id, new defaults), ``move`` it to ``index``, ``set`` one ``field`` to ``value`` (mm / degrees), or ``split`` a downpipe
    section in two. The whole assembly is checked before anything is stored; a change that cannot be built is refused and nothing changes."""
    meta = E.meta_of(project, oid)
    if E.m(meta, "category") != "downpipe":
        raise BadRequest("Select a downpipe to edit its components")
    pf = pipe_frame(project, oid)
    comps = components_of(meta)
    D, wall = pf["diameter"], pf["wall"]

    def where(cid: Any) -> int:
        for i, c in enumerate(comps):
            if str(c["id"]) == str(cid):
                return i
        raise NotFound(f"The downpipe has no component {cid}")

    created: str | None = None
    if action == "add":
        k = E._choice(kind, tuple(COMPONENT_KINDS), "component")
        created = _next_id(comps)
        new = {"id": created, "kind": k, **_defaults(k, D, wall)}
        if k == "hopper":
            pos = 0
        elif k == "shoe":
            pos = len(comps)
        elif index is not None:
            pos = int(index)
        else:
            pos = len(comps) - (1 if comps and comps[-1]["kind"] == "shoe" else 0)
        if not 0 <= pos <= len(comps):
            raise BadRequest("That position is outside the assembly")
        if k in ("hopper", "shoe") and any(c["kind"] == k for c in comps):
            raise BadRequest(f"The downpipe already has a {COMPONENT_KINDS[k].lower()}")
        comps.insert(pos, new)
    elif action == "remove":
        i = where(component)
        if len(comps) == 1:
            raise BadRequest("A downpipe keeps at least one component")
        gone = comps.pop(i)
        if gone.get("auto") and not any(c.get("auto") for c in comps):
            nxt = next((c for c in reversed(comps) if c["kind"] == "pipe"), None)       # the free length passes to the lowest remaining section
            if nxt is not None:
                nxt["auto"] = True
    elif action == "replace":
        i = where(component)
        k = E._choice(kind, tuple(COMPONENT_KINDS), "component")
        comps[i] = {"id": comps[i]["id"], "kind": k, **_defaults(k, D, wall)}
    elif action == "move":
        i = where(component)
        item = comps.pop(i)
        pos = int(index if index is not None else i)
        if not 0 <= pos <= len(comps):
            raise BadRequest("That position is outside the assembly")
        comps.insert(pos, item)
    elif action == "set":
        i = where(component)
        if not field:
            raise BadRequest("Say which parameter of the component to change")
        val = _component_value(comps[i]["kind"], str(field), value, pf["g"])
        if field == "auto" and val:
            for c in comps:
                c.pop("auto", None) if c is not comps[i] else None
            comps[i]["auto"] = True
        elif field == "length" and comps[i].get("auto"):
            comps[i]["auto"] = False
            comps[i]["length"] = val
        elif val is None:
            comps[i].pop(str(field), None)
        else:
            comps[i][str(field)] = val
    elif action == "split":
        i = where(component)
        c = comps[i]
        if c["kind"] != "pipe":
            raise BadRequest("Only a downpipe section can be split into two sections")
        created = _next_id(comps)
        if c.get("auto"):
            upper = {"id": c["id"], "kind": "pipe", "auto": False, "length": float(number(value, "length of the upper section (mm)", MIN_SECTION * 1000, 100000)) / 1000.0 if value is not None else 1.0}
            lower = {"id": created, "kind": "pipe", "auto": True}
        else:
            half = float(c.get("length", 1.0)) / 2
            if half < MIN_SECTION:
                raise BadRequest("The section is too short to split")
            upper = {**c, "length": half}
            lower = {"id": created, "kind": "pipe", "auto": False, "length": half}
        comps[i:i + 1] = [upper, lower]
    else:
        raise BadRequest("The action is one of add, remove, replace, move, set or split")
    for i, c in enumerate(comps):
        if c["kind"] == "hopper" and i != 0:
            raise BadRequest("A hopper head stays at the top of the downpipe")
        if c["kind"] == "shoe" and i != len(comps) - 1:
            raise BadRequest("A shoe stays at the bottom of the downpipe")
    assembly(project, oid, comps)                                                     # refuse what cannot be built, before storing it
    E._attach(project, oid, {"components": comps})
    regenerate_downpipe(project, oid)
    return {"id": oid, "components": [c["id"] for c in comps], "created": created}


def set_downpipe_property(project: Any, meta: Mapping[str, Any], key: str, value: Any) -> dict[str, Any]:
    if key == "offset":
        return {"offset": number(value, key, 0, 100000)}
    if key == "diameter":
        return {"diameter": number(value, key, 20, 500) / 1000.0}
    if key == "wall":
        return {"wall": number(value, key, 0.5, 50) / 1000.0}
    if key == "bottom":
        return {"bottom": number(value, key, -1000, 1000)}
    if key == "bend_radius":
        return {"bend_radius": number(value, "bend radius (mm)", 10, 2000) / 1000.0}
    if key == "connector_length":
        return {"connector_length": number(value, "connector length (mm)", 10, 1000) / 1000.0}
    if key == "length":
        # The gutter connection stays fixed; length moves only the outlet.
        gid = E.m(meta, "gutter")
        if not gid or not E.alive(project, gid):
            raise BadRequest("The downpipe has no gutter to measure its length from")
        g = frame(project, gid)
        t = float(E.m(meta, "offset"))
        top = rim_at(g, t) + _zout(E.m(g["meta"], "gutter"), g["width"], g["depth"], g["width"] / 2)
        return {"bottom": top - number(value, "downpipe length (m)", 0.1, 2000)}
    if key == "material":
        return {"material": E._choice(value, GUTTER_MATERIALS, "material")}
    if key.startswith("comp:"):
        _, cid, field = (key.split(":", 2) + ["", ""])[:3]
        comps = components_of(meta)
        for c in comps:
            if str(c["id"]) == cid:
                pf_g = frame(project, E.m(meta, "gutter"))
                val = _component_value(c["kind"], field, value, pf_g)
                if field == "length" and c.get("auto"):
                    c["auto"] = False
                if val is None:
                    c.pop(field, None)
                elif field == "auto" and val:
                    for o in comps:
                        o.pop("auto", None)
                    c["auto"] = True
                else:
                    c[field] = val
                return {"components": comps}
        raise NotFound(f"The downpipe has no component {cid}")
    raise BadRequest(f"'{key}' is not an editable downpipe property")


def slide_downpipe(project: Any, oid: str, dx: float, dy: float) -> None:
    """Moving a downpipe slides it along its gutter, as a door slides along its wall."""
    meta = E.meta_of(project, oid)
    g = frame(project, E.m(meta, "gutter"))
    E._attach(project, oid, {"offset": float(E.m(meta, "offset")) + dx * g["u"][0] + dy * g["u"][1]})


def component_classification(project: Any, oid: str, comp: Mapping[str, Any]) -> dict[str, Any]:
    """What a component is classified as: its own NL-SfB code if one was set, else the downpipe's, else the gutter's (inherited, never copied, so it
    follows the parent); its IFC entity is its own kind's (a bend is an IfcPipeFitting BEND whatever the downpipe is)."""
    from . import sfb as SFB
    meta = E.meta_of(project, oid)
    own = comp.get("sfb")
    if own:
        code, source = own, "this component"
    elif SFB.read(meta, "sfb"):
        code, source = SFB.read(meta, "sfb"), "the downpipe"
    else:
        gid = E.m(meta, "gutter")
        gcode = SFB.read(E.meta_of(project, gid), "sfb") if gid and E.alive(project, gid) else ""
        code, source = (gcode, "the gutter") if gcode else ("", "")
    entity, predefined = COMPONENT_IFC[str(comp.get("kind", "connector"))]
    return {"sfb": code, "sfb_from": source, "ifc_entity": entity, "ifc_predefined_type": predefined, "system": "RAINWATER", "assembly": oid}


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


def _overlap(lo: float, hi: float, a: float, b: float) -> float:
    return max(0.0, min(hi, b) - max(lo, a))


def run_state(project: Any, oid: str) -> dict[str, Any]:
    """How the rain of one chain of gutters reaches the ground. The chain falls towards its low points; the stretch of gutter that drains to each low
    point (its *basin*, bounded by the high points midway between lows) hands its catchment to the downpipes standing at that low point (within
    ``OUTLET_REACH`` of it, plus the pipe's radius). A low point with no such downpipe is *unserved*: the rain it collects has nowhere to go."""
    f = frame(project, oid)
    chain, ids = f["chain"], f["ids"]
    total, closed = chain["total"], chain["closed"]
    rain = float(E.m(f["meta"], "rainfall", RAINFALL_DEFAULT))
    density = [catchment(project, g) / sg["length"] for g, sg in zip(ids, chain["segments"])]
    lows = sorted(chain["lows"])
    shifts = (-total, 0.0, total) if closed else (0.0,)
    pipes: list[tuple[str, float, float]] = []
    for g, sg in zip(ids, chain["segments"]):
        for pid in downpipes_of(project, g):
            pm = E.meta_of(project, pid)
            pipes.append((pid, sg["p0"] + float(E.m(pm, "offset")), float(E.m(pm, "diameter"))))
    out_lows = []
    pipe_flow: dict[str, float] = {}
    for j, low in enumerate(lows):
        if closed:
            before = lows[j - 1] - (total if j == 0 else 0.0)
            after = lows[(j + 1) % len(lows)] + (total if j == len(lows) - 1 else 0.0)
            lo, hi = (before + low) / 2, (low + after) / 2
        else:
            lo = 0.0 if j == 0 else (lows[j - 1] + low) / 2
            hi = total if j == len(lows) - 1 else (low + lows[j + 1]) / 2
        stretches, area = [], 0.0
        for g, sg, dens in zip(ids, chain["segments"], density):
            a, b = sg["p0"], sg["p0"] + sg["length"]
            for sh in shifts:
                n = _overlap(lo + sh, hi + sh, a, b)
                if n > 1e-9:
                    stretches.append({"gutter": g, "from_m": round(max(lo + sh, a) - a, 6), "to_m": round(min(hi + sh, b) - a, 6)})
                    area += dens * n
        seat = next((i for i, sg in enumerate(chain["segments"]) if sg["p0"] + 1e-9 < low <= sg["p0"] + sg["length"] + 1e-9), 0)
        mine = [pid for pid, q, d in pipes if _distance(q, [low], total, closed) <= OUTLET_REACH + d / 2
                and min(range(len(lows)), key=lambda k: _distance(q, [lows[k]], total, closed)) == j]
        flow = area * rain
        for pid in mine:
            pipe_flow[pid] = flow / len(mine)
        out_lows.append({"at_m": round(low, 6), "gutter": ids[seat], "offset_m": round(low - chain["segments"][seat]["p0"], 6), "basin_m": [round(lo, 6), round(hi, 6)],
                         "catchment_m2": round(area, 6), "flow_l_s": round(flow, 6), "downpipes": mine, "served": bool(mine), "stretches": stretches})
    loose = [pid for pid, _, _ in pipes if pid not in pipe_flow]
    for pid in loose:
        pipe_flow[pid] = 0.0
    return {"ids": ids, "chain": chain, "rainfall": rain, "lows": out_lows, "pipe_flow": pipe_flow, "loose": loose, "downpipes": [p[0] for p in pipes]}


def pipe_flow(project: Any, pipe_id: str) -> float:
    """The design flow (l/s) a downpipe takes: the rain of the basin it serves, shared by the pipes serving that low point; 0 if it stands where no
    water reaches it."""
    return run_state(project, E.m(E.meta_of(project, pipe_id), "gutter"))["pipe_flow"][pipe_id]


def trace(project: Any, gutter_id: str, offset: float = 0.0) -> dict[str, Any]:
    """Follow the rain that falls on a gutter ``offset`` metres along it: down the gutter to the low point it drains to, and out of the downpipe
    there (if there is one)."""
    f = frame(project, gutter_id)
    if not 0.0 <= offset <= f["length"] + 1e-9:
        raise BadRequest(f"offset must lie on the gutter, between 0 and {f['length']:.3f} m")
    st = run_state(project, gutter_id)
    chain = st["chain"]
    total, closed = chain["total"], chain["closed"]
    p = f["seg"]["p0"] + offset
    lows = [l["at_m"] for l in st["lows"]]
    j = min(range(len(lows)), key=lambda k: _distance(p, [lows[k]], total, closed))
    target = lows[j]
    if closed:
        if target - p > total / 2:
            target -= total
        elif p - target > total / 2:
            target += total
    lo, hi = min(p, target), max(p, target)
    steps = []
    for g, sg in zip(st["ids"], chain["segments"]):
        for sh in ((-total, 0.0, total) if closed else (0.0,)):
            a, b = sg["p0"] + sh, sg["p0"] + sg["length"] + sh
            n = _overlap(lo, hi, a, b)
            if n > 1e-9:
                x0, x1 = max(lo, a) - a, min(hi, b) - a
                steps.append({"gutter": g, "name": project.model.get(g)["name"], "from_m": round(x1 if target < p else x0, 6), "to_m": round(x0 if target < p else x1, 6), "_k": max(lo, a)})
    steps.sort(key=lambda s: s["_k"], reverse=target < p)
    for s in steps:
        del s["_k"]
    low = st["lows"][j]
    return {"gutter": gutter_id, "offset_m": round(offset, 6), "distance_m": round(abs(target - p), 6), "path": steps,
            "low": {"gutter": low["gutter"], "offset_m": low["offset_m"], "catchment_m2": low["catchment_m2"], "flow_l_s": low["flow_l_s"]},
            "downpipes": [{"id": pid, "name": project.model.get(pid)["name"], "flow_l_s": round(st["pipe_flow"][pid], 6)} for pid in low["downpipes"]],
            "reaches_downpipe": low["served"]}


def run_problems(project: Any, oid: str) -> list[str]:
    """What is wrong with the way a chain drains: no downpipe at all, or low points no downpipe stands at."""
    st = run_state(project, oid)
    if not st["downpipes"]:
        return ["has no downpipe: the rain it collects has nowhere to go"]
    return [f"has a low point {l['at_m']:.2f} m along the run (on '{project.model.get(l['gutter'])['name']}') with no downpipe within {OUTLET_REACH * 1000:.0f} mm of it: "
            f"{l['catchment_m2']:.1f} m² of roof drains there and the water would stand" for l in st["lows"] if not l["served"]]


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
        states: dict[tuple[str, ...], dict[str, Any]] = {}
        for gid in gutters_of(project, rid):
            st = states.get(tuple(frame(project, gid)["ids"])) or run_state(project, gid)
            states[tuple(st["ids"])] = st
        by_gutter = {g: st for st in states.values() for g in st["ids"]}
        for gid in gutters_of(project, rid):
            obj = project.model.get(gid)
            c = catchment(project, gid)
            rain = float(E.m(obj["metadata"], "rainfall", RAINFALL_DEFAULT))
            st = by_gutter[gid]
            drained += c
            gutters.append({"id": gid, "name": obj["name"], "length_m": round(frame(project, gid)["length"], 6), "catchment_m2": round(c, 6), "rainfall_l_s_m2": rain,
                            "flow_l_s": round(c * rain, 6),
                            "downpipes": [{"id": pid, "name": project.model.get(pid)["name"], "diameter_mm": round(float(E.m(project.model.get(pid)["metadata"], "diameter")) * 1000, 6),
                                           "flow_l_s": round(st["pipe_flow"][pid], 6)} for pid in downpipes_of(project, gid)]})
        boundary = [tuple(q) for q in E.m(rmeta, "boundary")]
        hung = {int(E.m(project.model.get(g["id"])["metadata"], "edge")): g["id"] for g in gutters}
        eaves = [{"edge": e, "length_m": round(math.dist(boundary[e], boundary[(e + 1) % len(boundary)]), 6), "gutter": hung.get(e)} for e in E.roof_eave_edges(rmeta)]
        runs = [{"gutters": st["ids"], "closed": st["chain"]["closed"], "length_m": round(st["chain"]["total"], 6), "fall_to": st["chain"]["fall_to"],
                 "catchment_m2": round(sum(l["catchment_m2"] for l in st["lows"]), 6), "flow_l_s": round(sum(l["flow_l_s"] for l in st["lows"]), 6),
                 "lows": [{k: l[k] for k in ("at_m", "gutter", "offset_m", "catchment_m2", "flow_l_s", "downpipes", "served", "stretches")} for l in st["lows"]],
                 "problems": run_problems(project, st["ids"][0])} for st in states.values()]
        rows.append({"id": rid, "name": project.model.get(rid)["name"], "roof": E.m(rmeta, "roof"), "plan_area_m2": round(area, 6), "drained_m2": round(drained, 6),
                     "undrained_m2": round(max(0.0, area - drained), 6), "eaves": eaves, "gutters": gutters, "runs": runs})
    return {"roofs": rows, "note": NOTE}


NOTE = ("Rain is taken as the roof's plan area times the design rainfall set on each gutter (an assumed value, litres per second per square metre). "
        "A run of connected gutters falls towards its low points; the rain of the stretch that drains to a low point leaves by the downpipes standing there. "
        "Gutters and downpipes are not sized to a code and no pass or fail is given.")


# --------------------------------------------------------------------------------------------------------------------- plan, properties, checks
def plan_item(project: Any, oid: str, f: float) -> dict[str, Any]:
    obj = project.model.get(oid)
    g = frame(project, oid)
    return {"id": oid, "category": "gutter", "name": obj["name"], "run": g["key"], "polygon": [[x * f, y * f] for x, y in plan_outline(g["chain"], g["seg"])]}


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


def _end_rows(project: Any, f: Mapping[str, Any], i: int, mm: Any) -> list[dict[str, Any]]:
    sg, ids = f["seg"], f["ids"]
    rows: list[dict[str, Any]] = []
    for side, (k, kind, ext, other, label) in enumerate(((sg["k0"], sg["end0"], sg["ext0"], ids[i - 1], "Start"), (sg["k1"], sg["end1"], sg["ext1"], ids[(i + 1) % len(ids)], "End"))):
        key = "start" if side == 0 else "end"
        if k is not None:
            rows.append({"label": f"{label} corner", "type": "readonly", "value": f"Mitred to '{project.model.get(other)['name']}'" + (f" ({math.degrees(2 * math.atan(abs(k))):.0f}° turn)" if abs(k) > 1e-9 else " (straight)")})
        else:
            rows.append({"key": f"end_{key}", "label": f"{label} of the run", "type": "choice", "value": kind, "choices": [{"value": "cap", "label": "Closed with a stop end"}, {"value": "open", "label": "Open"}]})
            rows.append({"key": f"ext_{key}", "label": f"{label} extension", "type": "number", "unit": "mm", "value": mm(ext)})
    return rows


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
        st = run_state(project, oid)
        pipes = downpipes_of(project, oid)
        rain = float(E.m(meta, "rainfall", RAINFALL_DEFAULT))
        i = g["ids"].index(oid)
        chain = g["chain"]
        run_rows = [{"label": "Position in the run", "type": "readonly", "value": f"{i + 1} of {len(g['ids'])}" + (" (closed loop)" if chain["closed"] else "")},
                    {"label": "Run length", "type": "readonly", "value": f"{chain['total']:.3f} m"},
                    {"label": "Low points", "type": "readonly", "value": ", ".join(f"{l['at_m']:.2f} m" for l in st["lows"]) + (" along the run" if st["lows"] else "")}]
        low = next((l for l in st["lows"] if any(s["gutter"] == oid for s in l["stretches"])), None)
        drains = ("; ".join(f"{project.model.get(p)['name']} ({st['pipe_flow'][p]:.3f} l/s)" for p in low["downpipes"]) if low and low["downpipes"] else "no downpipe at its low point")
        out["groups"] = [
            {"title": "Fall", "rows": [{"key": "fall", "label": "Fall", "type": "number", "unit": "mm/m", "value": mm(g["fall"])},
                                       {"key": "fall_to", "label": "Falls towards", "type": "choice", "value": g["fall_to"],
                                        "choices": [{"value": "end", "label": "End of the run"}, {"value": "start", "label": "Start of the run"},
                                                    {"value": "middle", "label": "Middle of the run"}, {"value": "corners", "label": "Every corner"}]}]},
            {"title": "Placement", "rows": [{"key": "gap", "label": "Out from the eave line", "type": "number", "unit": "mm", "value": mm(g["gap"])},
                                            {"key": "drop", "label": "Below the eave", "type": "number", "unit": "mm", "value": mm(float(E.m(meta, "drop", RIM_DROP)))},
                                            *_end_rows(project, g, i, mm)]},
            {"title": "Gutter run", "rows": run_rows},
            {"title": "Rainwater drainage", "rows": [{"label": "Roof area draining here", "type": "readonly", "value": f"{c:.2f} m² (plan)"},
                                                     {"key": "rainfall", "label": "Design rainfall", "type": "number", "unit": "l/s·m²", "value": rain},
                                                     {"label": "Design flow", "type": "readonly", "value": f"{c * rain:.3f} l/s"},
                                                     {"label": "Drains to", "type": "readonly", "value": drains},
                                                     {"label": "Downpipes", "type": "readonly", "value": str(len(pipes)) if pipes else "none on this piece"}]},
            {"title": "Geometry", "rows": [{"label": "Length", "type": "readonly", "value": f"{g['length']:.3f} m"}, volume]}]
        return out
    pf = pipe_frame(project, oid)
    c = catchment(project, pf["gutter"])
    flow = pipe_flow(project, oid)
    out["top"] = [{"key": "diameter", "label": "Diameter", "type": "number", "unit": "mm", "value": mm(pf["diameter"])},
                  {"key": "offset", "label": "Distance along gutter", "type": "number", "unit": "m", "value": round(pf["offset"], 6)},
                  {"key": "bottom", "label": "Discharge height", "type": "number", "unit": "m", "value": round(pf["bottom"], 6)},
                  {"key": "wall", "label": "Pipe wall", "type": "number", "unit": "mm", "value": mm(pf["wall"])},
                  {"key": "material", "label": "Material", "type": "choice", "value": E.m(pf["meta"], "material"), "choices": E._choices(GUTTER_MATERIALS)}]
    out["groups"] = [{"title": "Rainwater drainage", "rows": [{"label": "Design flow", "type": "readonly", "value": f"{flow:.3f} l/s" + ("" if flow > 0 else " (not at a low point of the gutter: no water reaches it)")}]},
                     {"title": "Geometry", "rows": [
                         {"key": "length", "label": "Downpipe length", "type": "number", "unit": "m", "value": round(pf["top"] - pf["bottom"], 6)},
                         {"label": "Connected top elevation", "type": "readonly", "value": f"{pf['top']:.3f} m"},
                         {"key": "bottom", "label": "Bottom elevation", "type": "number", "unit": "m", "value": round(pf["bottom"], 6)},
                         volume]}]
    out["groups"].insert(0, _assembly_group(project, oid, pf))
    return out


def _component_summary(c: Mapping[str, Any], part: Mapping[str, Any] | None) -> str:
    kind = str(c["kind"])
    bits = []
    if kind == "pipe":
        bits.append("free length" if c.get("auto") else "fixed length")
    if kind in ("offset_bend", "elbow", "shoe", "y_branch"):
        bits.append(f"{float(c.get('angle', 45)):g}°, {DIRECTIONS.get(str(c.get('direction', 'out')), '').lower()}")
    if kind == "offset_bend":
        bits.append(f"offset {float(c.get('offset', 0.3)) * 1000:.0f} mm")
    if part is not None:
        bits.append(f"{part['length']:.3f} m along its axis")
    return f"{COMPONENT_KINDS[kind]} ({'; '.join(bits)})" if bits else COMPONENT_KINDS[kind]


def _assembly_group(project: Any, oid: str, pf: Mapping[str, Any]) -> dict[str, Any]:
    """The Components group of a downpipe's Properties: every component in order (follow one to edit it), the generated connectors, and the
    assembly's own bend radius and connector length. The assembly is rebuilt to report what is really there."""
    comps = components_of(pf["meta"])
    try:
        a = assembly(project, oid)
        parts = {p["id"]: p for p in a["parts"]}
        connectors = a["connectors"]
    except BadRequest as exc:
        parts, connectors = {}, []
        rows: list[dict[str, Any]] = [{"label": "Assembly", "type": "readonly", "value": exc.message}]
    else:
        rows = []
    for i, c in enumerate(comps):
        rows.append({"label": f"{i + 1}. {c['id']}", "type": "link", "value": _component_summary(c, parts.get(str(c["id"]))),
                     "target": {"select": {"model": oid, "part": f"comp:{c['id']}"}}})
    rows.append({"label": "Connectors (automatic)", "type": "readonly",
                 "value": ", ".join(f"between {k['between'][0]} and {k['between'][1]}" for k in connectors) or "none: no two downpipe sections meet"})
    rows.append({"key": "bend_radius", "label": "Bend radius (centre line)", "type": "number", "unit": "mm", "value": round(pf["bend_radius"] * 1000, 6)})
    rows.append({"key": "connector_length", "label": "Connector length", "type": "number", "unit": "mm", "value": round(pf["connector_length"] * 1000, 6)})
    return {"title": f"Components ({len(comps)})", "rows": rows}


def component_properties(project: Any, oid: str, part: str) -> dict[str, Any]:
    """The Properties panel of one component of a downpipe (``part`` is ``comp:<id>`` or ``conn:<id>``): its own parameters, what it is, how it is
    classified (inherited, with the source named) and where it sits in the assembly."""
    meta = E.meta_of(project, oid)
    name = project.model.get(oid)["name"] or "Downpipe"
    kind_key, _, cid = part.partition(":")
    a = assembly(project, oid)
    if kind_key == "conn":
        k = next((x for x in a["connectors"] if x["id"] == cid), None)
        if k is None:
            raise NotFound(f"The downpipe has no connector {cid}")
        cls = component_classification(project, oid, {"kind": "connector"})
        return {"ref": {"model": oid, "part": part}, "title": f"{name} › Downpipe connector", "category": "downpipe", "kind": "model", "top": [
            {"label": "Joins", "type": "readonly", "value": f"{k['between'][0]} and {k['between'][1]}"},
            {"key": "connector_length", "label": "Length (all connectors)", "type": "number", "unit": "mm", "value": round(a['frame']['connector_length'] * 1000, 6)}],
            "groups": [_classification_group(cls), {"title": "Assembly", "rows": [{"label": "Downpipe", "type": "link", "value": name, "target": {"select": {"model": oid}}}]}]}
    comps = components_of(meta)
    c = next((x for x in comps if str(x["id"]) == cid), None)
    if c is None:
        raise NotFound(f"The downpipe has no component {cid}")
    part_geo = next(p for p in a["parts"] if p["id"] == cid)
    kind = str(c["kind"])
    key = lambda f: f"comp:{cid}:{f}"                                                                   # noqa: E731
    rows: list[dict[str, Any]] = []
    if kind == "pipe":
        rows.append({"key": key("auto"), "label": "Takes up the remaining length", "type": "choice", "value": "true" if c.get("auto") else "false",
                     "choices": [{"value": "true", "label": "Yes (free section)"}, {"value": "false", "label": "No (fixed length)"}]})
        rows.append({"key": key("length"), "label": "Length", "type": "number", "unit": "mm", "value": round(part_geo["length"] * 1000, 3)})
    if kind in ("offset_bend", "elbow", "shoe", "y_branch"):
        rows.append({"key": key("angle"), "label": "Angle", "type": "choice" if float(c.get("angle", 45)) in BEND_PRESETS else "number", "unit": "°",
                     "value": f"{float(c.get('angle', 45)):g}" if float(c.get("angle", 45)) in BEND_PRESETS else float(c.get("angle", 45)),
                     "choices": [{"value": f"{a_:g}", "label": f"{a_:g}°"} for a_ in BEND_PRESETS]})
        rows.append({"key": key("direction"), "label": "Turns", "type": "choice", "value": str(c.get("direction", "out")),
                     "choices": [{"value": k_, "label": v_} for k_, v_ in DIRECTIONS.items()]})
    if kind == "offset_bend":
        rows.append({"key": key("offset"), "label": "Offset", "type": "number", "unit": "mm", "value": round(float(c.get("offset", 0.3)) * 1000, 3)})
    if kind == "shoe":
        rows.append({"key": key("length"), "label": "Outlet leg", "type": "number", "unit": "mm", "value": round(float(c.get("length", 0.08)) * 1000, 3)})
    if kind == "y_branch":
        for f_, label in (("body_length", "Body length"), ("branch_length", "Branch length"), ("branch_diameter", "Branch diameter")):
            rows.append({"key": key(f_), "label": label, "type": "number", "unit": "mm", "value": round(float(c.get(f_, 0.24)) * 1000, 3)})
    if kind == "hopper":
        for f_, label in (("width", "Width (along the gutter)"), ("depth", "Depth (out from the wall)"), ("height", "Height"), ("drop", "Below the gutter"), ("thickness", "Sheet")):
            rows.append({"key": key(f_), "label": label, "type": "number", "unit": "mm", "value": round(float(c.get(f_, 0.2)) * 1000, 3)})
    cls = component_classification(project, oid, c)
    index = [str(x["id"]) for x in comps].index(cid)
    return {"ref": {"model": oid, "part": part}, "title": f"{name} › {COMPONENT_KINDS[kind]} ({cid})", "category": "downpipe", "kind": "model", "top": rows,
            "groups": [_classification_group(cls, key("sfb")),
                       {"title": "Assembly", "rows": [{"label": "Downpipe", "type": "link", "value": name, "target": {"select": {"model": oid}}},
                                                      {"label": "Position", "type": "readonly", "value": f"{index + 1} of {len(comps)} from the gutter down"},
                                                      {"label": "Centre line", "type": "readonly", "value": f"{part_geo['length']:.3f} m, from z {part_geo['axis'][0][2]:.3f} to {part_geo['axis'][1][2]:.3f} m"}]}]}


def _classification_group(cls: Mapping[str, Any], sfb_key: str | None = None) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    if sfb_key:
        rows.append({"key": sfb_key, "label": "NL-SfB override", "type": "text", "value": "" if cls["sfb_from"] != "this component" else cls["sfb"]})
    rows += [{"label": "NL-SfB code", "type": "readonly", "value": (f"{cls['sfb']} (from {cls['sfb_from']})" if cls["sfb"] else "none set on the component, the downpipe or the gutter")},
             {"label": "IFC entity", "type": "readonly", "value": f"{cls['ifc_entity']}.{cls['ifc_predefined_type']}"},
             {"label": "System", "type": "readonly", "value": "Rainwater (IfcDistributionSystem RAINWATER)"}]
    return {"title": "Classification", "rows": rows}


def issues(project: Any, oid: str, label: str, issue: Any) -> list[dict[str, str]]:
    """Model-check findings for a gutter or downpipe."""
    out: list[dict[str, str]] = []
    cat = E.category(project, oid)
    try:
        if cat == "gutter":
            f = frame(project, oid)
            if f["ids"][0] == oid:                                              # a run is reported once, on its first piece
                out += [issue("warning", oid, f"{label}{' (and the ' + str(len(f['ids']) - 1) + ' gutters joined to it)' if len(f['ids']) > 1 else ''} {m}", "drainage")
                        for m in run_problems(project, oid)]
        else:
            f = pipe_frame(project, oid)
            if f["diameter"] > f["g"]["width"] + 1e-9:
                out.append(issue("error", oid, f"{label} is wider than its gutter", "parameters"))
            if f["top"] - f["bottom"] < 0.1:
                out.append(issue("error", oid, f"{label} does not run down from its gutter", "parameters"))
    except BadRequest as exc:
        out.append(issue("error", oid, f"{label}: {exc.message}", "hosting"))
    return out


# ------------------------------------------------------------------------------------------------------------------------ ground drainage
# Three ground-drainage elements take the rain from the paving and the downpipes to the underground drains. Each is parametric and, set in a floor,
# cuts its own opening in that floor and no other (``drain_cutters``, which the floor's ordinary void machinery applies with the "hosted" policy, so
# a multilayer floor is cut layer by layer and the cut follows every edit):
#
# * ``gully``   a point drain (yard gully): a square body under a grating, with a side outlet;
# * ``channel`` a linear channel drain along a line, under a grating, with an outlet under one end or its middle;
# * ``inlet``   a downpipe ground inlet: a round body with an open top that a downpipe drops into; linked to a downpipe it stands under that
#               downpipe's end, and the downpipe ends in its socket.
#
# The sizes are modelling defaults, every one editable; no product, load class (EN 1433 / EN 124) or flow capacity is claimed, and the model check
# says when a body is shallower than the floor it stands in (a recess is not cut: that needs a detail of its own).
DRAIN_KINDS = {"gully": "Point drain (yard gully)", "channel": "Linear channel drain", "inlet": "Downpipe ground inlet"}
DRAIN_COVERS = ("grating", "solid cover", "open")
DRAIN_MATERIALS = ("PVC", "Polymer concrete", "Concrete", "Cast iron", "Steel")
DRAIN_IFC = {"gully": ("IfcWasteTerminal", "GULLYTRAP"), "channel": ("IfcWasteTerminal", "USERDEFINED"), "inlet": ("IfcWasteTerminal", "USERDEFINED")}
DRAIN_OBJECT_TYPE = {"channel": "CHANNELDRAIN", "inlet": "RAINWATERINLET"}
DRAIN_DEFAULTS = {"gully": {"width": 0.3, "depth": 0.45, "wall": 0.012, "cover": "grating", "cover_thickness": 0.025, "outlet_diameter": 0.11,
                            "outlet_height": 0.12, "outlet_direction": 0.0, "outlet_length": 0.15, "material": "PVC"},
                  "channel": {"width": 0.15, "depth": 0.2, "wall": 0.015, "cover": "grating", "cover_thickness": 0.02, "outlet_diameter": 0.11,
                              "outlet_at": "end", "outlet_length": 0.15, "material": "Polymer concrete"},
                  "inlet": {"diameter": 0.25, "depth": 0.45, "wall": 0.01, "cover": "open", "cover_thickness": 0.0, "outlet_diameter": 0.11,
                            "outlet_height": 0.1, "outlet_direction": 0.0, "outlet_length": 0.15, "socket": 0.05, "material": "PVC"}}
DRAIN_SIDES = 32


def drains(project: Any) -> list[str]:
    return list(project.model.query_ids(metadata={f"{E.NS}:category": "drain"}, limit=10_000))


def _floor_top(project: Any, fid: str) -> float:
    from . import attachments as AT
    return AT.floor_top(project, fid)


def _inside_floor(project: Any, fid: str, pts: Sequence[tuple[float, float]]) -> bool:
    from . import attachments as AT
    return AT.inside_floor(project, fid, pts)


def find_floor(project: Any, level: str, pts: Sequence[tuple[float, float]]) -> str | None:
    """The floor a drain stands in (``attachments.find_floor``: the same rule walls and curtain walls stand on a floor by)."""
    from . import attachments as AT
    return AT.find_floor(project, level, pts)


def drain_frame(project: Any, oid: str) -> dict[str, Any]:
    """Where a drain is and how big, in metres: its plan footprint (outside of the body), its top (the floor's top surface when it stands in a floor,
    plus ``offset``), its bottom, and its host floor and linked downpipe."""
    meta = E.meta_of(project, oid)
    kind = str(E.m(meta, "drain_kind"))
    p = {**DRAIN_DEFAULTS[kind], **{k: E.m(meta, k) for k in DRAIN_DEFAULTS[kind] if E.m(meta, k) is not None}}
    host = E.m(meta, "host")
    if host and not (E.alive(project, host) and E.category(project, host) == "floor"):
        host = None
    top = (_floor_top(project, host) if host else E.level_elevation(project, E.m(meta, "level"))) + float(E.m(meta, "offset", 0.0))
    if kind == "channel":
        a, b = (tuple(map(float, E.m(meta, "start"))), tuple(map(float, E.m(meta, "end"))))
        L = math.dist(a, b)
        if L < 0.2:
            raise BadRequest("A channel drain is at least 200 mm long")
        u = ((b[0] - a[0]) / L, (b[1] - a[1]) / L)
        n = (-u[1], u[0])
        hw = p["width"] / 2
        foot = [(a[0] + n[0] * hw, a[1] + n[1] * hw), (a[0] - n[0] * hw, a[1] - n[1] * hw), (b[0] - n[0] * hw, b[1] - n[1] * hw), (b[0] + n[0] * hw, b[1] + n[1] * hw)]
        centre = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        extra = {"a": a, "b": b, "u": u, "n": n, "length": L}
    else:
        centre = tuple(map(float, E.m(meta, "point")))
        if kind == "gully":
            ang = float(E.m(meta, "rotation", 0.0))
            c, s = math.cos(ang), math.sin(ang)
            hw = p["width"] / 2
            foot = [(centre[0] + c * x - s * y, centre[1] + s * x + c * y) for x, y in ((-hw, -hw), (hw, -hw), (hw, hw), (-hw, hw))]
        else:
            foot = G.circle(centre[0], centre[1], p["diameter"] / 2, DRAIN_SIDES)
        extra = {}
    return {"id": oid, "meta": meta, "kind": kind, "p": p, "host": host, "top": top, "bottom": top - p["depth"], "centre": centre, "footprint": foot,
            "downpipe": E.m(meta, "downpipe") if E.m(meta, "downpipe") and E.alive(project, E.m(meta, "downpipe")) else None, **extra}


def _open_box(outer: Sequence[tuple[float, float]], inner: Sequence[tuple[float, float]], z0: float, z_floor: float, z1: float) -> G.Mesh:
    """A body open at the top: ``outer`` stood from z0 to z1, hollowed by ``inner`` from z_floor up (both outlines with the same number of corners)."""
    o = [tuple(q) for q in outer]
    i_ = [tuple(q) for q in inner]
    if G.signed_area(o) < 0:
        o.reverse()
        i_.reverse()
    n = len(o)
    verts = ([[x, y, z0] for x, y in o] + [[x, y, z1] for x, y in o] + [[x, y, z_floor] for x, y in i_] + [[x, y, z1] for x, y in i_])
    faces: list[list[int]] = []
    for a, b, c in G.triangulate(o):
        faces.append([a, c, b])                                                  # underside
    for a, b, c in G.triangulate(i_):
        faces.append([2 * n + a, 2 * n + b, 2 * n + c])                         # the floor of the cavity, facing up
    for k in range(n):
        j = (k + 1) % n
        faces += [[k, j, n + j], [k, n + j, n + k]]                             # outside walls
        faces += [[2 * n + k, 3 * n + j, 2 * n + j], [2 * n + k, 3 * n + k, 3 * n + j]]   # inside walls, facing in
        faces += [[n + k, n + j, 3 * n + j], [n + k, 3 * n + j, 3 * n + k]]     # the rim
    return E._positive_volume((verts, faces))


def drain_parts(project: Any, oid: str) -> list[tuple[str, G.Mesh]]:
    """The solids of a drain: its body, its grating or cover, and its outlet."""
    f = drain_frame(project, oid)
    p, kind, top, bottom = f["p"], f["kind"], f["top"], f["bottom"]
    wall = p["wall"]
    od = p["outlet_diameter"]
    if wall <= 0.002 or p["depth"] <= wall + od or od <= 0.02:
        raise BadRequest("The drain body must be deeper than its floor and outlet, with a wall of more than 2 mm")
    parts: list[tuple[str, G.Mesh]] = []
    gt = p["cover_thickness"] if p["cover"] != "open" else 0.0
    if kind == "channel":
        a, b, u, n, L = f["a"], f["b"], f["u"], f["n"], f["length"]
        hw = p["width"] / 2
        if hw - wall < od / 2 + 0.005:
            raise BadRequest("The channel is too narrow for its outlet")
        inner = [(a[0] + u[0] * wall + n[0] * (hw - wall), a[1] + u[1] * wall + n[1] * (hw - wall)), (a[0] + u[0] * wall - n[0] * (hw - wall), a[1] + u[1] * wall - n[1] * (hw - wall)),
                 (b[0] - u[0] * wall - n[0] * (hw - wall), b[1] - u[1] * wall - n[1] * (hw - wall)), (b[0] - u[0] * wall + n[0] * (hw - wall), b[1] - u[1] * wall + n[1] * (hw - wall))]
        tmid = L / 2 if p["outlet_at"] == "middle" else (wall + od / 2 + 0.02 if p["outlet_at"] == "start" else L - wall - od / 2 - 0.02)
        ox, oy = a[0] + u[0] * tmid, a[1] + u[1] * tmid
        outlet = G.tube([([ox, oy, bottom], (0.0, 0.0, -1.0)), ([ox, oy, bottom - p["outlet_length"]], (0.0, 0.0, -1.0))], od / 2, od / 2 - wall / 2, sides=CIRCLE)
    else:
        if kind == "gully":
            if p["width"] / 2 - wall < 0.05:
                raise BadRequest("A gully body is at least 100 mm across inside")
            inner = []
            ang = float(E.m(f["meta"], "rotation", 0.0))
            c, s = math.cos(ang), math.sin(ang)
            hw = p["width"] / 2 - wall
            cx, cy = f["centre"]
            inner = [(cx + c * x - s * y, cy + s * x + c * y) for x, y in ((-hw, -hw), (hw, -hw), (hw, hw), (-hw, hw))]
        else:
            cx, cy = f["centre"]
            inner = G.circle(cx, cy, p["diameter"] / 2 - wall, DRAIN_SIDES)
        az = math.radians(float(p["outlet_direction"])) + (float(E.m(f["meta"], "rotation", 0.0)) if kind == "gully" else 0.0)
        d = (math.cos(az), math.sin(az), 0.0)
        zc = bottom + p["outlet_height"] + od / 2
        if zc + od / 2 > top - gt - 0.01:
            raise BadRequest("The outlet is too high for the drain body")
        cx, cy = f["centre"]
        half = p["width"] / 2 if kind == "gully" else p["diameter"] / 2
        start = [cx + d[0] * (half - wall / 2), cy + d[1] * (half - wall / 2), zc]
        foot = f["footprint"]

        def onto_body(q: list[float], axis: Sequence[float]) -> list[float]:      # the outlet starts on the outside face of the body
            if kind == "gully":
                best = None
                for k in range(4):
                    p0, p1 = foot[k], foot[(k + 1) % 4]
                    ex, ey = p1[0] - p0[0], p1[1] - p0[1]
                    nx_, ny_ = ey, -ex
                    den = nx_ * axis[0] + ny_ * axis[1]
                    if den > 1e-9:
                        t = (nx_ * (p0[0] - q[0]) + ny_ * (p0[1] - q[1])) / den
                        if best is None or t < best:
                            best = t
                t = best or 0.0
            else:
                r = p["diameter"] / 2
                wx, wy = q[0] - cx, q[1] - cy
                A_ = axis[0] ** 2 + axis[1] ** 2
                B_ = 2 * (wx * axis[0] + wy * axis[1])
                C_ = wx * wx + wy * wy - r * r
                t = (-B_ + math.sqrt(max(B_ * B_ - 4 * A_ * C_, 0.0))) / (2 * A_)
            return [q[0] + axis[0] * t, q[1] + axis[1] * t, q[2] + axis[2] * t]
        outlet = G.tube([(start, d), (G._v3(start, d, half + p["outlet_length"]), d)], od / 2, od / 2 - wall / 2, sides=CIRCLE, start_onto=onto_body)
    parts.append(("body", _open_box(f["footprint"], inner, bottom, bottom + wall, top)))
    if gt > 0:
        parts.append(("cover", G.prism(inner, top - gt, top)))
    parts.append(("outlet", E._positive_volume(outlet)))
    return parts


def regenerate_drain(project: Any, oid: str) -> None:
    f = drain_frame(project, oid)
    if f["kind"] == "inlet" and f["downpipe"]:
        pipe = E.meta_of(project, f["downpipe"])
        if float(E.m(pipe, "diameter")) / 2 + 0.005 > f["p"]["diameter"] / 2 - f["p"]["wall"]:
            raise BadRequest("The ground inlet is too narrow for the downpipe it takes", hint="Make the inlet wider")
    meshes = [m_ for _, m_ in drain_parts(project, oid)]
    mesh, counts = G.join_meshes(meshes)
    E._attach(project, oid, {"component_faces": [{"id": k, "count": n} for (k, _), n in zip(drain_parts(project, oid), counts)], "built_top": round(f["top"], 9),
                             "footprint": [[round(x, 9), round(y, 9)] for x, y in f["footprint"]]})
    E._set_mesh(project, oid, E._positive_volume(mesh))


def drain_cutters(project: Any) -> list[dict[str, Any]]:
    """What the drains cut, in the shape of a void (``forms.voids``): the body's plan footprint over the body's height, acting only on its own floor
    ("hosted"), so a drain never cuts a floor it does not stand in."""
    out = []
    for oid in drains(project):
        try:
            f = drain_frame(project, oid)
        except BadRequest:
            continue
        if not f["host"]:
            continue
        out.append({"id": oid, "name": project.model.get(oid)["name"] or "Drain", "poly": [tuple(q) for q in f["footprint"]], "z0": f["bottom"], "z1": f["top"] + 1e-6,
                    "enabled": True, "policy": "hosted", "targets": [], "host": f["host"], "drain": True})
    return out


def add_drain(project: Any, p: Mapping[str, Any], to_m: float) -> str:
    """A ground drain of ``kind`` gully, channel or inlet on ``level``: ``point`` (gully, inlet) or ``start``/``end`` (channel) in drawing units, or, for an
    inlet, a ``downpipe`` to stand under. It stands in the floor under it (``floor``: an id, ``auto`` (default) or ``none``) and cuts that floor."""
    kind = E._choice(p.get("kind", "gully"), tuple(DRAIN_KINDS), "drain kind")
    values: dict[str, Any] = {"category": "drain", "type": DRAIN_KINDS[kind], "drain_kind": kind, "offset": 0.0}
    pipe = p.get("downpipe")
    if pipe:
        if kind != "inlet":
            raise BadRequest("Only a ground inlet stands under a downpipe")
        if not E.alive(project, pipe) or E.category(project, pipe) != "downpipe":
            raise NotFound(f"Downpipe {pipe} does not exist")
        end = assembly(project, pipe)["end"]
        values["downpipe"] = pipe
        values["point"] = [end[0], end[1]]
        level = p.get("level") or min(project.manifest["levels"], key=lambda l: abs(float(l["elevation"]) - end[2]))["id"]
    else:
        level = p.get("level")
    values["level"] = project.level(text(level, "level", 80))["id"]
    if kind == "channel":
        for key in ("start", "end"):
            q = p.get(key)
            if not isinstance(q, (list, tuple)) or len(q) != 2:
                raise BadRequest(f"A channel drain needs a {key} point [x, y]")
            values[key] = [float(number(q[0], key, -1e9, 1e9)) * to_m, float(number(q[1], key, -1e9, 1e9)) * to_m]
    elif "point" not in values:
        q = p.get("point")
        if not isinstance(q, (list, tuple)) or len(q) != 2:
            raise BadRequest("A drain needs a point [x, y]")
        values["point"] = [float(number(q[0], "point", -1e9, 1e9)) * to_m, float(number(q[1], "point", -1e9, 1e9)) * to_m]
    for k in DRAIN_DEFAULTS[kind]:
        if p.get(k) is not None:
            values.update(set_drain_property(project, {f"{E.NS}:drain_kind": kind}, k, p[k]))
    oid = project.model.add_box(width=1.0, depth=1.0, height=1.0, name=text(p.get("name") or DRAIN_KINDS[kind], "name", 80))
    E._attach(project, oid, values)
    floor = p.get("floor", "auto")
    try:
        if floor == "auto":
            host = find_floor(project, values["level"], drain_frame(project, oid)["footprint"])
        elif floor in (None, "none", ""):
            host = None
        else:
            host = text(floor, "floor", 80)
            if not E.alive(project, host) or E.category(project, host) != "floor":
                raise BadRequest(f"{host} is not a floor")
            if not _inside_floor(project, host, drain_frame(project, oid)["footprint"]):
                raise BadRequest("The drain is not wholly inside that floor")
        E._attach(project, oid, {"host": host})
        regenerate_drain(project, oid)
    except BadRequest:
        project.model.remove(oid)
        raise
    reconcile_drains(project)
    return oid


def set_drain_property(project: Any, meta: Mapping[str, Any], key: str, value: Any) -> dict[str, Any]:
    kind = str(E.m(meta, "drain_kind"))
    mm_keys = {"width": (100, 2000), "depth": (60, 3000), "wall": (3, 200), "cover_thickness": (0, 200), "outlet_diameter": (25, 500), "outlet_height": (0, 2000),
               "outlet_length": (20, 2000), "diameter": (100, 2000), "socket": (0, 500), "offset": (-1000, 1000)}
    if key in mm_keys and (key in DRAIN_DEFAULTS[kind] or key == "offset"):
        lo, hi = mm_keys[key]
        return {key: number(value, f"{key.replace('_', ' ')} (mm)", lo, hi) / 1000.0}
    if key == "outlet_direction" and kind != "channel":
        return {key: number(value, "outlet direction (°)", -360, 360)}
    if key == "rotation" and kind == "gully":
        return {key: math.radians(number(value, "rotation (°)", -360, 360))}
    if key == "cover":
        return {key: E._choice(value, DRAIN_COVERS, "cover")}
    if key == "outlet_at" and kind == "channel":
        return {key: E._choice(value, ("start", "middle", "end"), "outlet position")}
    if key == "material":
        return {key: E._choice(value, DRAIN_MATERIALS, "material")}
    if key == "host":
        if value in (None, "", "none"):
            return {"host": None}
        if value == "auto":
            return {"host": "auto"}                                                  # found again from the footprint after the edit (reconcile_drains)
        if not E.alive(project, value) or E.category(project, value) != "floor":
            raise BadRequest(f"{value} is not a floor")
        return {"host": value}
    raise BadRequest(f"'{key}' is not an editable property of a {DRAIN_KINDS[kind].lower()}")


def move_drain(project: Any, oid: str, dx: float, dy: float) -> None:
    meta = E.meta_of(project, oid)
    if E.m(meta, "downpipe"):
        raise BadRequest("This inlet stands under its downpipe: move the downpipe, or unlink the inlet first")
    if E.m(meta, "drain_kind") == "channel":
        s, e = E.m(meta, "start"), E.m(meta, "end")
        E._attach(project, oid, {"start": [s[0] + dx, s[1] + dy], "end": [e[0] + dx, e[1] + dy]})
    else:
        q = E.m(meta, "point")
        E._attach(project, oid, {"point": [q[0] + dx, q[1] + dy]})


def reconcile_drains(project: Any) -> list[str]:
    """After a command: a ground inlet linked to a downpipe stands under its end again, and the downpipe ends in the inlet's socket; a drain whose
    floor rose, sank or was deleted is rebuilt (a lost floor: it then stands on its level and cuts nothing). Returns the drains rebuilt."""
    changed: list[str] = []
    for oid in drains(project):
        meta = E.meta_of(project, oid)
        values: dict[str, Any] = {}
        host = E.m(meta, "host")
        if host == "auto":
            try:
                values["host"] = find_floor(project, str(E.m(meta, "level")), drain_frame(project, oid)["footprint"])
            except BadRequest:
                values["host"] = None
        elif host and not (E.alive(project, host) and E.category(project, host) == "floor"):
            values["host"] = None
        pipe = E.m(meta, "downpipe")
        if pipe and not E.alive(project, pipe):
            values["downpipe"] = None
        elif pipe:
            for _ in range(3):                                                    # the pipe's end moves only if its free section leans: settle it
                end = assembly(project, pipe)["end"]
                if math.dist(end[:2], E.m(meta, "point")) > 1e-9:
                    values["point"] = [end[0], end[1]]
                    E._attach(project, oid, {"point": values["point"]})
                    meta = E.meta_of(project, oid)
                f = drain_frame(project, oid)
                target = f["top"] - f["p"]["socket"]
                if abs(float(E.m(E.meta_of(project, pipe), "bottom")) - target) <= 1e-9:
                    break
                E._attach(project, pipe, {"bottom": target})
                regenerate_downpipe(project, pipe, host=False)
        if values:
            E._attach(project, oid, values)
        f = drain_frame(project, oid)
        if values or abs(float(E.m(E.meta_of(project, oid), "built_top", f["top"])) - f["top"]) > 1e-9 or \
                [[round(x, 9), round(y, 9)] for x, y in f["footprint"]] != (E.m(E.meta_of(project, oid), "footprint") or []):
            regenerate_drain(project, oid)
            changed.append(oid)
    return changed


def drain_plan_item(project: Any, oid: str, f_: float) -> dict[str, Any]:
    f = drain_frame(project, oid)
    return {"id": oid, "category": "drain", "name": project.model.get(oid)["name"], "polygon": [[x * f_, y * f_] for x, y in f["footprint"]]}


def drain_properties(project: Any, oid: str, out: dict[str, Any], volume: dict[str, Any]) -> dict[str, Any]:
    f = drain_frame(project, oid)
    p, kind = f["p"], f["kind"]
    mm = lambda v: round(v * 1000, 6)                                                              # noqa: E731
    top: list[dict[str, Any]] = []
    for key, label in (("width", "Width"), ("diameter", "Body diameter"), ("depth", "Depth below the top"), ("wall", "Wall")):
        if key in DRAIN_DEFAULTS[kind]:
            top.append({"key": key, "label": label, "type": "number", "unit": "mm", "value": mm(p[key])})
    top.append({"key": "cover", "label": "Grating / cover", "type": "choice", "value": p["cover"], "choices": E._choices(DRAIN_COVERS)})
    if p["cover"] != "open":
        top.append({"key": "cover_thickness", "label": "Grating thickness", "type": "number", "unit": "mm", "value": mm(p["cover_thickness"])})
    top.append({"key": "material", "label": "Material", "type": "choice", "value": p["material"], "choices": E._choices(DRAIN_MATERIALS)})
    outlet = [{"key": "outlet_diameter", "label": "Outlet diameter", "type": "number", "unit": "mm", "value": mm(p["outlet_diameter"])},
              {"key": "outlet_length", "label": "Outlet stub", "type": "number", "unit": "mm", "value": mm(p["outlet_length"])}]
    if kind == "channel":
        outlet.append({"key": "outlet_at", "label": "Outlet under", "type": "choice", "value": p["outlet_at"], "choices": E._choices(("start", "middle", "end"))})
    else:
        outlet += [{"key": "outlet_height", "label": "Outlet above the body floor", "type": "number", "unit": "mm", "value": mm(p["outlet_height"])},
                   {"key": "outlet_direction", "label": "Outlet direction", "type": "number", "unit": "°", "value": float(p["outlet_direction"])}]
    floor_name = project.model.get(f["host"])["name"] if f["host"] else None
    placement = [{"label": "Stands in", "type": "link", "value": floor_name or "No floor (on its level)", **({"target": {"select": {"model": f["host"]}}} if f["host"] else {})},
                 {"key": "offset", "label": "Top above the floor's top", "type": "number", "unit": "mm", "value": mm(float(E.m(f["meta"], "offset", 0.0)))},
                 {"label": "Top / bottom", "type": "readonly", "value": f"{f['top']:.3f} m / {f['bottom']:.3f} m"}]
    if kind == "inlet":
        placement.append({"label": "Takes the downpipe", "type": "link" if f["downpipe"] else "readonly", "value": project.model.get(f["downpipe"])["name"] if f["downpipe"] else "none",
                          **({"target": {"select": {"model": f["downpipe"]}}} if f["downpipe"] else {})})
        placement.append({"key": "socket", "label": "Downpipe enters by", "type": "number", "unit": "mm", "value": mm(p["socket"])})
    entity, predefined = DRAIN_IFC[kind]
    cls = [{"label": "IFC entity", "type": "readonly", "value": f"{entity}.{predefined}" + (f" (ObjectType {DRAIN_OBJECT_TYPE[kind]})" if kind in DRAIN_OBJECT_TYPE else "")},
           {"label": "Note", "type": "readonly", "value": "A modelling default: confirm the entity and type for the product chosen"}]
    out["top"] = top
    out["groups"] = [{"title": "Outlet", "rows": outlet}, {"title": "Placement", "rows": placement}, {"title": "IFC", "rows": cls},
                     {"title": "Geometry", "rows": [volume]}]
    return out


def drain_issues(project: Any, oid: str, label: str, issue: Any) -> list[dict[str, str]]:
    """Model-check findings for a ground drain: a body that does not reach through its floor (a recess the model does not cut), a floor it does not
    stand in, an inlet that lost its downpipe."""
    from . import forms as FX
    out: list[dict[str, str]] = []
    try:
        f = drain_frame(project, oid)
    except BadRequest as exc:
        return [issue("error", oid, f"{label}: {exc.message}", "parameters")]
    if not f["host"]:
        out.append(issue("info", oid, f"{label} stands on its level, not in a floor, so it cuts nothing", "hosting"))
        return out
    fm = E.meta_of(project, f["host"])
    floor_bottom = _floor_top(project, f["host"]) - float(E.m(fm, "thickness"))
    if f["bottom"] > floor_bottom + 1e-6:
        out.append(issue("warning", oid, f"{label} is {(f['bottom'] - floor_bottom) * 1000:.0f} mm shallower than the floor it stands in: the opening is cut only "
                         f"through the layers it passes completely, and the recess below needs a construction detail of its own", "hosting"))
    if f["kind"] == "inlet" and E.m(f["meta"], "downpipe") and not f["downpipe"]:
        out.append(issue("warning", oid, f"{label} lost the downpipe it took", "hosting"))
    notes = [msg for who, msg in FX._cuts(project, f["host"], FX.voids(project))[1] if who == oid]
    out += [issue("info", oid, msg, "hosting") for msg in notes]
    return out
