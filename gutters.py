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


def default_outlets(chain: Mapping[str, Any], diameter: float) -> list[tuple[int, float]]:
    """Where a downpipe goes at each low point of a chain: (edge, distance along that eave), kept clear of the ends by the pipe's radius."""
    margin = diameter / 2 + 0.005
    out = []
    for low in chain["lows"]:
        placed = None
        for sg in chain["segments"]:
            if sg["p0"] - 1e-9 <= low <= sg["p0"] + sg["length"] + 1e-9:
                t = min(max(low - sg["p0"], margin), sg["length"] - margin)
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
    width, gap = float(E.m(meta, "width")), float(E.m(meta, "gap", 0.0))
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


def segment_mesh(f: Mapping[str, Any], kind: str) -> tuple[list[list[float]], list[list[int]]]:
    """The solid of one gutter segment: the channel swept along its eave with mitred ends where it joins its neighbours, a station wherever the fall
    changes direction, and a stop-end plate at each free end that is capped."""
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
    stations = [first, *[((lambda s, t=t: t), t) for t in inside], final]
    parts = [_sweep(chan, stations, f)]
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
    try:
        mesh = segment_mesh(f, E.m(f["meta"], "gutter"))
    except G.GeometryError as exc:
        raise BadRequest(str(exc)) from exc
    E._set_mesh(project, oid, E._positive_volume(mesh))


def regenerate(project: Any, oid: str) -> None:
    """Rebuild a gutter and the rest of its run (a corner changes both sides of it), then the downpipes under them."""
    rid, _ = _roof(project, E.meta_of(project, oid))
    members = run_members(project, rid, run_key(project, oid))
    for g in members:
        _build(project, g)
    for g in members:
        for pid in downpipes_of(project, g):
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
                regenerate_downpipe(project, pid)
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
    for g in run_members(project, roof_id, key):
        _build(project, g)
    for g in run_members(project, roof_id, key):
        for pid in downpipes_of(project, g):
            regenerate_downpipe(project, pid)


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
            "at": (g["a"][0] + g["u"][0] * t + g["n"][0] * (g["gap"] + d / 2), g["a"][1] + g["u"][1] * t + g["n"][1] * (g["gap"] + d / 2)), "wall": float(E.m(meta, "wall"))}


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
                     {"title": "Geometry", "rows": [{"label": "Length", "type": "readonly", "value": f"{pf['top'] - pf['bottom']:.3f} m"}, volume]}]
    return out


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
