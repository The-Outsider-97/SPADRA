"""Extrusions, voids and sweeps: solids made from a profile.

* **Extrusion** (``extrusion``): a plan outline ``profile`` stood up from ``base`` (metres above its level) for ``depth`` metres, in a material.
* **Void** (``void``): the same shape, but it removes material instead of adding it. "Make void" turns an extrusion into a void and back.
* **Sweep** (``sweep``): a cross-section (``section``: points ``(u, v)``, ``u`` to the left of the path, ``v`` up from ``base``) carried along a
  ``path`` drawn in plan at one height. Corners are mitred. A sweep may be an additive solid or a persistent swept void.

Two complementary non-destructive cutting strategies share one policy:

* a **floor** or a solid **extrusion** is cut by every void that spans its full thickness: a void wholly inside its outline makes a hole, one that crosses
  or lies along its edge takes a notch out of the outline, and voids that overlap each other act as their union (``planar``, an exact polygon boolean). The solid
  is then an exact B-rep with holes, tessellated by SGEO. A void that would split it into separate pieces, leave nothing of it, or touch another outline at
  a single point is not accepted by the planar path and may be handled by a solid Boolean instead;
* a **wall** gets a rectangular opening when the void is a rectangle whose sides run along and across the wall, passes through the whole
  thickness and overlaps the wall (it can start at the ground, reach the top or cut the wall in two);
* complex intersections (pockets, splits, roofs, beams, inclined walls, and swept cutters) use SLAI's existing SGEO 3D Boolean evaluator. Its numerical
  approximation and manifold/backend limitations remain explicit; failed evaluations are reported, never presented as successful cuts.

Which elements a void acts on is controlled per void (``cutting``): the "Cut Intersecting Geometry" switch and a policy (Automatic, Selected Targets,
Hosted Objects, Disabled), plus element/category exclusions. Source solids remain intact; reversible generated Boolean modifiers carry the general cuts.
Turning a void off or deleting it restores the original solid. ``cutting.classify`` reports successful, unsupported and excluded intersections.

Voids that overlap each other in the same floor or extrusion act as their union. Voids that overlap in a wall each cut their own opening, and the wall is
cut by the union of those openings.
A sweep follows a path in plan at one height: it does not climb, curve or follow a 3D path, and a corner sharper than ``MITER_LIMIT``
thicknesses, or a profile wider than the path can turn, is refused.
"""
from __future__ import annotations

import math

from typing import Any, Mapping, Sequence

from ..tools.errors import BadRequest
from ..tools.validation import boolean, number, text
from . import cutting as CU
from . import elements as E
from . import layers as LY
from . import geometry as G
from . import planar as P

Pt = tuple[float, float]
PathPt = tuple[float, float, float]

CATEGORIES = ("extrusion", "void", "sweep")
SHAPES = ("rectangle", "round")
ROUND_SEGMENTS = 24
ARC_SEGMENTS = 6                       # segments in a quarter-circle when a corner is rounded
MITER_LIMIT = 4.0
TOL = 1e-6
VOID_BASE = -0.5                       # a void made without numbers starts half a metre below its level and stands 4 m: through a slab and a storey wall
VOID_DEPTH = 4.0
MAX_PATH_POINTS = 500
BASE_OFFSET = 0.001                     # a sweep's base is raised this much above the path to avoid touching the floor it stands on
BASE_OFFSET_SLOPE = 0.001              # a sweep's base is raised this much above the path per metre of slope to avoid touching the floor it stands on
END_CAPS = ("flat", "none", "chamfered")


def offset_sweep_base(path: Sequence[PathPt], base: float) -> float:
    """Base elevated above the path so the sweep does not touch the floor it stands on."""
    if len(path) < 2:
        return base + BASE_OFFSET
    z0, z1 = path[0][2], path[-1][2]          # elevation, see §3.1
    return base + BASE_OFFSET + abs(z1 - z0) * BASE_OFFSET_SLOPE

def offset_sweep_path(path: Sequence[PathPt], base: float) -> list[PathPt]:
    """A sweep's path is raised above the floor it stands on to avoid touching it: a fixed offset plus a slope-based offset."""
    if len(path) < 2:
        return [(path[0][0], path[0][1], path[0][2] + base + BASE_OFFSET)]
    z0, z1 = path[0][2], path[-1][2]
    sx, sy = path[0][0], path[0][1]
    ex, ey = path[-1][0], path[-1][1]
    total = math.hypot(ex - sx, ey - sy) or 1.0        # 2-D length, so the ratio is in [0, 1]
    extra = abs(z1 - z0) * BASE_OFFSET_SLOPE
    return [
        (x, y, z + base + BASE_OFFSET + extra * (math.hypot(x - sx, y - sy) / total))
        for x, y, z in path
    ]


# ------------------------------------------------------------------------------------------------------------------ plan geometry
def _seg_hit(a: Pt, b: Pt, c: Pt, d: Pt) -> bool:
    """Whether segments ab and cd touch or cross (shared end points and collinear overlap count)."""
    def orient(p: Pt, q: Pt, r: Pt) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    def on(p: Pt, q: Pt, r: Pt) -> bool:
        return min(p[0], q[0]) - TOL <= r[0] <= max(p[0], q[0]) + TOL and min(p[1], q[1]) - TOL <= r[1] <= max(p[1], q[1]) + TOL

    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    if ((o1 > TOL and o2 < -TOL) or (o1 < -TOL and o2 > TOL)) and ((o3 > TOL and o4 < -TOL) or (o3 < -TOL and o4 > TOL)):
        return True
    return ((abs(o1) <= TOL and on(a, b, c)) or (abs(o2) <= TOL and on(a, b, d)) or (abs(o3) <= TOL and on(c, d, a)) or (abs(o4) <= TOL and on(c, d, b)))


def _edges(poly: Sequence[Pt]) -> list[tuple[Pt, Pt]]:
    return list(zip(poly, list(poly[1:]) + [poly[0]]))


def _inside(poly: Sequence[Pt], p: Pt) -> bool:
    from . import rooms as R
    return R.contains(poly, p)


def relation(outer: Sequence[Pt], inner: Sequence[Pt]) -> str:
    """How polygon ``inner`` lies against polygon ``outer``: ``inside`` (wholly, touching nothing), ``outside`` (apart), ``covers`` (``inner`` holds
    all of ``outer``) or ``crossing`` (they meet along an edge or overlap in part)."""
    for a, b in _edges(outer):
        for c, d in _edges(inner):
            if _seg_hit(a, b, c, d):
                return "crossing"
    if _inside(outer, inner[0]):
        return "inside"
    if _inside(inner, outer[0]):
        return "covers"
    return "outside"


def _round(values: Any, digits: int = 5) -> Any:
    if isinstance(values, (list, tuple)):
        return [_round(v, digits) for v in values]
    return round(float(values) + 0.0, digits)


# ------------------------------------------------------------------------------------------------------------------------ voids
def void_ids(project: Any) -> list[str]:
    ids = list(project.model.query_ids(metadata={f"{E.NS}:category": "void"}, limit=10_000))
    ids.extend(project.model.query_ids(metadata={f"{E.NS}:void_mode": True}, limit=10_000))
    return list(dict.fromkeys(ids))


def z_range(project: Any, meta: Mapping[str, Any]) -> tuple[float, float]:
    z0 = E.level_elevation(project, E.m(meta, "level")) + float(E.m(meta, "base", 0.0))
    return z0, z0 + float(E.m(meta, "depth"))


def voids(project: Any) -> list[dict[str, Any]]:
    """Persistent prism and swept cutters, with their policy and exclusion settings."""
    out = []
    for vid in void_ids(project):
        obj = project.model.get(vid)
        meta = obj["metadata"]
        if E.m(meta, "category") == "sweep":
            out.append({"id": vid, "name": obj["name"] or "Void Sweep", "swept": True, **CU.settings(meta)})
            continue
        z0, z1 = z_range(project, meta)
        out.append({"id": vid, "name": obj["name"] or "Void", "poly": [(float(q[0]), float(q[1])) for q in E.m(meta, "profile")], "z0": z0, "z1": z1, **CU.settings(meta)})
    from . import gutters as GU
    out.extend(GU.drain_cutters(project))                                       # a ground drain cuts the floor it stands in, and only that floor
    return out


class SlabCut(list):
    """The voids that cut a slab-like solid (a floor or an extrusion), as a list, and what is left of its plan: ``holes`` (rings inside it) and ``outline`` (its
    new outline when a void takes a notch out of the edge, otherwise ``None``: the outline is unchanged)."""

    def __init__(self, voids: Sequence[Mapping[str, Any]] = (), outline: Sequence[Pt] | None = None, holes: Sequence[Sequence[Pt]] = ()) -> None:
        super().__init__(voids)
        self.outline = [tuple(q) for q in outline] if outline else None
        self.holes = [[tuple(q) for q in h] for h in holes]


def cut_ids(cuts: Any) -> set[str]:
    """The ids of the voids behind ``_cuts``'s third result, whatever the element: a wall's opening spans (id last), a slab's voids (a ``SlabCut`` of dicts) or,
    for a layered floor, one ``SlabCut`` per layer."""
    out: set[str] = set()
    for c in cuts:
        if isinstance(c, SlabCut):
            out |= cut_ids(c)
        elif isinstance(c, Mapping):
            out.add(c["id"])
        else:
            out.add(c[4])
    return out


def _remaining(outline: Sequence[Pt], voids: Sequence[Mapping[str, Any]], label: str) -> P.Region:
    """What is left of ``outline`` once ``voids`` are taken away: one region, or ``PlanarError`` saying why there is not exactly one."""
    regions = P.difference(outline, [v["poly"] for v in voids])
    if not regions:
        raise P.PlanarError(f"leaves nothing of {label}")
    if len(regions) > 1:
        raise P.PlanarError(f"would split {label} into {len(regions)} separate pieces, which one element cannot hold")
    return regions[0]


def holes_for(vs: Sequence[Mapping[str, Any]], outline: Sequence[Pt], z0: float, z1: float, label: str) -> tuple[SlabCut, list[tuple[str, str]]]:
    """The voids that cut a slab-like solid (outline and height range given) and the plan that is left, with a note for each void that touches it without cutting.
    Voids wholly inside and apart from each other make holes exactly as drawn; any void that crosses or lies along the edge, or overlaps another, goes through the
    planar boolean, where voids that cannot all be applied are applied one at a time in model order and the rest are noted."""
    notes: list[tuple[str, str]] = []
    acting: list[tuple[Mapping[str, Any], str]] = []
    for v in vs:
        if v["z1"] <= z0 + TOL or v["z0"] >= z1 - TOL:
            continue                                                          # apart in height
        rel = relation(outline, v["poly"])
        if rel == "outside":
            continue
        if v["z0"] > z0 + TOL or v["z1"] < z1 - TOL:
            notes.append((v["id"], f"{v['name']} does not span the full thickness of {label}, and a pocket is not cut"))
        elif rel == "covers":
            notes.append((v["id"], f"{v['name']} covers all of {label}, which is not cut away"))
        else:
            acting.append((v, rel))
    if not acting:
        return SlabCut(), notes
    voids = [v for v, _ in acting]
    if all(rel == "inside" for _, rel in acting) and all(relation(a["poly"], b["poly"]) == "outside" for i, a in enumerate(voids) for b in voids[i + 1:]):
        return SlabCut(voids, None, [v["poly"] for v in voids]), notes
    try:
        region = _remaining(outline, voids, label)
    except P.PlanarError:
        kept: list[Mapping[str, Any]] = []
        region = None
        for v in voids:
            try:
                region = _remaining(outline, [*kept, v], label)
                kept.append(v)
            except P.PlanarError as exc:
                notes.append((v["id"], f"{v['name']} is not cut out of {label}: it {exc}" if not str(exc).startswith("two outlines") else f"{v['name']} is not cut out of {label}: {exc}"))
        voids = kept
        if not voids or region is None:
            return SlabCut(), notes
    notched = any(rel != "inside" for v, rel in acting if v in voids)
    return SlabCut(voids, region[0] if notched else None, region[1]), notes


def wall_spans(vs: Sequence[Mapping[str, Any]], frame: Mapping[str, Any], label: str) -> tuple[list[tuple[float, float, float, float, str]], list[tuple[str, str]]]:
    """Openings the voids cut through a wall, as (x0, x1, z0, z1, void id) in the wall's own frame (x along it from its start, z up from its base)."""
    spans: list[tuple[float, float, float, float, str]] = []
    notes: list[tuple[str, str]] = []
    sx, sy = frame["start"]
    c, s = math.cos(frame["angle"]), math.sin(frame["angle"])
    length, half, base, height = frame["length"], frame["thickness"] / 2, frame["base"], frame["height"]
    footprint = [(0.0, -half), (length, -half), (length, half), (0.0, half)]
    for v in vs:
        if v["z1"] <= base + TOL or v["z0"] >= base + height - TOL:
            continue
        local = [((px - sx) * c + (py - sy) * s, -(px - sx) * s + (py - sy) * c) for px, py in v["poly"]]
        if relation(footprint, local) == "outside":
            continue
        xs, ys = [p[0] for p in local], [p[1] for p in local]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        rectangle = len(local) == 4 and all(min(math.dist(p, q) for q in corners) < 1e-4 for p in local) and len({(round(p[0], 4), round(p[1], 4)) for p in local}) == 4
        zs = (max(v["z0"] - base, 0.0), min(v["z1"] - base, height))
        if not rectangle:
            # Not a rectangle itself, but what it leaves of the wall may be: an L-shaped void whose arm runs through the wall square to it, for instance.
            found = _section_spans(footprint, local, half)
            if found is None:
                notes.append((v["id"], f"{v['name']} is not a rectangle along {label}, and only a rectangular opening is cut in a wall"))
            elif found == "niche":
                notes.append((v["id"], f"{v['name']} stops short of the whole thickness of {label}, and a niche is not cut"))
            else:
                spans.extend((cx0, cx1, zs[0], zs[1], v["id"]) for cx0, cx1 in found) # type: ignore
            continue
        if y0 > -half + TOL or y1 < half - TOL:
            notes.append((v["id"], f"{v['name']} stops short of the whole thickness of {label}, and a niche is not cut"))
            continue
        cx0, cx1 = max(x0, 0.0), min(x1, length)
        if cx1 - cx0 < TOL:
            continue
        spans.append((cx0, cx1, zs[0], zs[1], v["id"]))
    return spans, notes


def _section_spans(footprint: Sequence[Pt], void: Sequence[Pt], half: float) -> list[tuple[float, float]] | str | None:
    """The stretches (x0, x1) along a wall where a void of any outline passes square through its whole thickness, ``"niche"`` if the part of it that is
    inside the wall stops short of a face, or ``None`` if what it leaves of the wall's plan is not a set of rectangles square to the wall."""
    try:
        regions = P.intersection(footprint, void)
    except P.PlanarError:
        return None
    stretches: list[tuple[float, float]] = []
    niche = False
    for outer, holes in regions:
        xs, ys = [q[0] for q in outer], [q[1] for q in outer]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        if holes or len(outer) != 4 or any(min(math.dist(q, c) for c in corners) > 1e-4 for q in outer) or len({(round(q[0], 4), round(q[1], 4)) for q in outer}) != 4:
            return None
        if y0 > -half + TOL or y1 < half - TOL:
            niche = True
        elif x1 - x0 >= TOL:
            stretches.append((x0, x1))
    if niche:
        return "niche"
    return stretches


def _targets(project: Any) -> list[str]:
    ids: list[str] = []
    for cat in ("floor", "extrusion", "wall"):
        ids.extend(project.model.query_ids(metadata={f"{E.NS}:category": cat}, limit=10_000))
    return ids


def _cuts(project: Any, oid: str, vs: Sequence[Mapping[str, Any]], policy: bool = True) -> tuple[list[Any], list[tuple[str, str]], list[Any]]:
    """(what the voids cut in this element as comparable numbers, notes for voids that touch it but cut nothing, the cuts themselves). For a wall the numbers
    are its opening spans; for a floor or extrusion they are ``[holes, new outline or []]`` and the cuts are a ``SlabCut``. Only the voids whose
    cutting settings apply to this element are considered, unless ``policy`` is false (the classification asks what a void *would* do)."""
    obj = project.model.get(oid)
    meta, label = obj["metadata"], obj["name"] or str(E.m(obj["metadata"], "category")).title()
    cat = E.m(meta, "category")
    vs = [v for v in vs if not v.get("swept")]
    if policy:
        vs = [v for v in vs if CU.applies(v, oid, cat)[0]]
    if cat == "wall":
        spans, notes = wall_spans(vs, E.wall_frame(project, oid), label)
        return [_round(s[:4]) for s in spans], notes, spans
    if cat == "floor":
        top = E.level_elevation(project, E.m(meta, "level")) + float(E.m(meta, "offset", 0.0))
        z0, z1 = top - float(E.m(meta, "thickness")), top
        from . import layers as LY
        if LY.is_layered(meta):                                              # each layer is cut by the voids it inherits, over its own height range
            outline = [(float(q[0]), float(q[1])) for q in E.m(meta, "boundary")]
            cuts_, sig, notes = [], [], []
            for lay in LY.stack(project, oid):
                mine = LY.inherited(lay, [v["id"] for v in vs])
                c, n = holes_for([v for v in vs if v["id"] in mine], outline, top - lay["offset"] - lay["thickness"], top - lay["offset"], f"{label} ({lay['name']})")
                cuts_.append(c)
                sig.append([[_round(h) for h in c.holes], _round(c.outline) if c.outline else []])
                notes.extend(n)
            return sig, notes, cuts_
    else:
        z0, z1 = z_range(project, meta)
    outline = [(float(q[0]), float(q[1])) for q in E.m(meta, "boundary" if cat == "floor" else "profile")]
    cut, notes = holes_for(vs, outline, z0, z1, label)
    return [[_round(h) for h in cut.holes], _round(cut.outline) if cut.outline else []], notes, cut


def _stored(meta: Mapping[str, Any]) -> Any:
    """What an element's metadata says its cuts were when it was last built, in the shape ``_cuts`` returns them."""
    if E.m(meta, "category") == "wall":
        return E.m(meta, "cut") or []
    if E.m(meta, "category") == "floor" and LY.is_layered(meta):
        return E.m(meta, "cut_layers") or []
    return [E.m(meta, "cut") or [], E.m(meta, "cut_outline") or []]


def remember_cuts(project: Any, oid: str, vs: Sequence[Mapping[str, Any]] | None = None) -> list[Any]:
    """Record on an element which cuts its solid now carries, so ``reconcile`` can tell when a void has changed them. Returns the cuts: the wall's opening spans,
    or a ``SlabCut`` for a floor or extrusion."""
    vs = voids(project) if vs is None else vs
    sig, _, cuts = _cuts(project, oid, vs)
    meta = E.meta_of(project, oid)
    if sig != _stored(meta):
        if E.m(meta, "category") == "wall":
            E._attach(project, oid, {"cut": sig, "cutflag": bool(sig)})
        elif E.m(meta, "category") == "floor" and LY.is_layered(meta):
            E._attach(project, oid, {"cut_layers": sig, "cut": sig[0][0], "cut_outline": sig[0][1], "cutflag": any(h or o for h, o in sig)})
        else:
            E._attach(project, oid, {"cut": sig[0], "cut_outline": sig[1], "cutflag": bool(sig[0] or sig[1])})
    return cuts


def reconcile(project: Any) -> int:
    """Rebuild whatever a void now cuts differently (a void made, moved, resized, converted or deleted; a wall or floor moved under one).
    Runs inside every model command; an element left with nothing to cut is rebuilt whole. Returns how many elements were rebuilt."""
    rebuilt = follow_fixed(project)                                            # sweeps fixed to an element move with it
    vs = voids(project)
    flagged = set(project.model.query_ids(metadata={f"{E.NS}:cutflag": True}, limit=10_000))
    if vs or flagged:
        for oid in (_targets(project) if vs else sorted(flagged)):
            sig = _cuts(project, oid, vs)[0]
            if sig != _stored(E.meta_of(project, oid)):
                E.regenerate(project, oid)
                rebuilt += 1
    rebuilt += _reconcile_boolean_voids(project, vs)
    return rebuilt


def _reconcile_boolean_voids(project: Any, vs: Sequence[Mapping[str, Any]]) -> int:
    """Keep general 3D cuts as reversible SGEO Boolean modifiers on semantic BIM solids.

    The exact polygon/wall opening path remains preferred. Unsupported cuts
    (pockets, oblique intersections, roofs, beams, etc.) use SLAI's existing
    manifold evaluator without replacing the source geometry. A cutter's ID is
    recorded on the target so moving/removing the cutter can restore it.
    """
    active = [v for v in vs if E.alive(project, v["id"]) and
              (E.category(project, v["id"]) == "void" or v.get("swept"))]
    updated = 0
    for oid in E.element_ids(project):
        cat = E.category(project, oid)
        if cat in CU.NEVER_HIT or E.m(E.meta_of(project, oid), "void_mode") or not project.model.get(oid).get("geometry"):
            continue
        meta = E.meta_of(project, oid)
        existing = dict(E.m(meta, "void_boolean_modifiers") or {})
        desired: set[str] = set()
        if active:
            # Check the unmodified source mesh. Testing evaluated geometry
            # would make a successful cut appear to stop intersecting its void.
            covered: set[str] = set()
            if cat in CU.CUTTABLE:
                covered = cut_ids(_cuts(project, oid, active)[2])
            for v in active:
                if v["id"] not in covered and CU.applies(v, oid, cat)[0] and CU.touches(project, oid, v):
                    desired.add(v["id"])
        known_modifiers = {m["id"] for m in project.model.modifiers()}
        for vid, mid in list(existing.items()):
            if vid not in desired or mid not in known_modifiers:
                if mid in known_modifiers:
                    project.model.remove_modifier(mid)
                    updated += 1
                del existing[vid]
        for vid in sorted(desired - set(existing)):
            mid = project.model.add_modifier(
                oid, kind="sgeo.boolean", operands=[vid], parameters={"operation": "subtract"},
                evaluation_policy={"mode": "tolerance_controlled", "absolute": 1e-6})
            existing[vid] = mid
            updated += 1
        if existing != (E.m(meta, "void_boolean_modifiers") or {}):
            E._attach(project, oid, {"void_boolean_modifiers": existing})
    return updated


def detach_boolean_void(project: Any, vid: str) -> None:
    """Remove generated operand references before deleting the cutter itself."""
    for oid in E.element_ids(project):
        meta = E.meta_of(project, oid)
        modifiers = dict(E.m(meta, "void_boolean_modifiers") or {})
        if vid not in modifiers:
            continue
        mid = modifiers.pop(vid)
        if any(m["id"] == mid for m in project.model.modifiers()):
            project.model.remove_modifier(mid)
        E._attach(project, oid, {"void_boolean_modifiers": modifiers})


def report(project: Any, vid: str) -> dict[str, Any]:
    """Legacy exact-planar cut report; use ``cutting.classify`` for actual 3D Boolean results too."""
    vs = voids(project)
    cut: list[str] = []
    notes: list[str] = []
    for oid in _targets(project):
        obj = project.model.get(oid)
        try:
            _, why, cuts = _cuts(project, oid, vs)
        except BadRequest as exc:
            notes.append(exc.message)
            continue
        if any((c["id"] if isinstance(c, dict) else c[4]) == vid for c in cuts):
            cut.append(obj["name"] or str(E.m(obj["metadata"], "category")).title())
        notes.extend(msg for who, msg in why if who == vid)
    return {"cuts": cut, "notes": notes}


# ---------------------------------------------------------------------------------------------------------------- extrusions & voids
def _solid(profile: Sequence[Pt], holes: Sequence[Sequence[Pt]], z0: float, z1: float) -> G.Mesh:
    if not holes:
        return G.prism(list(profile), z0, z1)
    from src.utils.spatial_formats.sgeo import brep_build, brep_mesh # type: ignore
    mesh = brep_mesh.tessellate_brep(brep_build.brep_extrusion([tuple(p) for p in profile], z1 - z0, [[tuple(p) for p in h] for h in holes]))
    return E._positive_volume(([(x, y, z + z0) for x, y, z in mesh["vertices"]], mesh["faces"])) # type: ignore


def regenerate_extrusion(project: Any, oid: str) -> None:
    meta = E.meta_of(project, oid)
    profile = [(float(q[0]), float(q[1])) for q in E.m(meta, "profile")]
    if not G.is_simple_polygon(profile) or abs(G.signed_area(profile)) < 1e-9:
        raise BadRequest("The outline crosses itself or has no area")
    z0, z1 = z_range(project, meta)
    if z1 - z0 < 1e-3:
        raise BadRequest("The depth must be at least 1 mm")
    holes: Sequence[Sequence[Pt]] = []
    if E.m(meta, "category") == "extrusion":
        cut = remember_cuts(project, oid)
        holes, profile = cut.holes, cut.outline or profile # type: ignore
    try:
        E._set_mesh(project, oid, _solid(profile, holes, z0, z1))
    except G.GeometryError as exc:
        raise BadRequest(str(exc)) from exc


def add_extrusion(project: Any, p: Mapping[str, Any], to_m: float) -> str:
    """An extrusion (or, with ``void`` true, a void) from ``points`` (drawing units) at ``base``/``depth`` (m)."""
    void = boolean(p["void"], "void") if p.get("void") is not None else False
    level = project.level(text(p.get("level"), "level", 80))
    profile = E._boundary(p.get("points"), to_m, "void" if void else "extrusion")
    base = number(p.get("base", VOID_BASE if void else 0.0), "base", -500, 500)
    depth = number(p.get("depth", VOID_DEPTH if void else 1.0), "depth", 0.001, 500)
    name = text(p.get("name") or ("Void" if void else "Extrusion"), "name", 80)
    oid = project.model.add_box(width=1.0, depth=1.0, height=1.0, name=name)
    values: dict[str, Any] = {"category": "void" if void else "extrusion", "type": "Void" if void else "Extrusion", "level": level["id"], "profile": profile,
                              "base": base, "depth": depth}
    if not void:
        values["material"] = E._choice(p.get("material", "Concrete"), E.MATERIALS, "material")
    else:
        values.update(CU.creation_values(project, p))
    E._attach(project, oid, values)
    regenerate_extrusion(project, oid)
    return oid


def make_void(project: Any, oid: str, void: bool) -> bool:
    """Turn an extrusion into a void (it then cuts) or a void into a solid extrusion. False when it already was what was asked for."""
    meta = E.meta_of(project, oid)
    cat = E.m(meta, "category")
    if cat not in ("extrusion", "void"):
        raise BadRequest("Only an extrusion can be made a void")
    if (cat == "void") == void:
        return False
    if void:
        E._attach(project, oid, {"category": "void", "type": "Void", "cut": [], "cutflag": False})
        if "spadra:material" in meta:
            project.model.attach_semantics(oid, {k: v for k, v in E.meta_of(project, oid).items() if k != "spadra:material"}, replace=True)
    else:
        E._attach(project, oid, {"category": "extrusion", "type": "Extrusion", "material": "Concrete"})
    regenerate_extrusion(project, oid)
    return True


# ------------------------------------------------------------------------------------------------------------------------ sweeps
SHAPES = ("rectangle", "round", "ellipse", "tube", "pipe", "i_beam", "channel", "angle", "trapezoid")

def section_polygon(shape: str, width: float, height: float) -> list[list[float]]:
    """A cross-section sitting on the path's height, centred on the path: a rectangle, or an ellipse of ``ROUND_SEGMENTS`` sides."""
    w = float(width)
    h = float(height)
    if w <= 0.0 or h <= 0.0:
        raise BadRequest("Section width and height must be positive")

    def ellipse_points(rx: float, ry: float, inset: float = 0.0) -> list[list[float]]:
        if rx <= 0.0 or ry <= 0.0:
            raise BadRequest("Section axes must be positive")
        inner_x = max(rx - inset, 0.0)
        inner_y = max(ry - inset, 0.0)
        return [[inner_x * math.cos(2.0 * math.pi * i / ROUND_SEGMENTS), ry + inner_y * math.sin(2.0 * math.pi * i / ROUND_SEGMENTS)]
                for i in range(ROUND_SEGMENTS)]

    if shape == "rectangle":
        return [[-w / 2.0, 0.0], [w / 2.0, 0.0], [w / 2.0, h], [-w / 2.0, h]]
    if shape in ("round", "ellipse"):
        return [[(w / 2.0) * math.cos(2.0 * math.pi * i / ROUND_SEGMENTS), (h / 2.0) + (h / 2.0) * math.sin(2.0 * math.pi * i / ROUND_SEGMENTS)]
                for i in range(ROUND_SEGMENTS)]
    if shape == "tube":
        wall = min(w, h) * 0.2
        outer = [[-w / 2.0, 0.0], [w / 2.0, 0.0], [w / 2.0, h], [-w / 2.0, h]]
        inner = [[-w / 2.0 + wall, wall], [w / 2.0 - wall, wall], [w / 2.0 - wall, h - wall], [-w / 2.0 + wall, h - wall]]
        return outer + inner[::-1]
    if shape == "pipe":
        thickness = min(w, h) * 0.2
        outer = [[(w / 2.0) * math.cos(2.0 * math.pi * i / ROUND_SEGMENTS), (h / 2.0) + (h / 2.0) * math.sin(2.0 * math.pi * i / ROUND_SEGMENTS)]
                 for i in range(ROUND_SEGMENTS)]
        inner = [[((w - 2.0 * thickness) / 2.0) * math.cos(2.0 * math.pi * i / ROUND_SEGMENTS),
                  (h / 2.0) + ((h - 2.0 * thickness) / 2.0) * math.sin(2.0 * math.pi * i / ROUND_SEGMENTS)]
                 for i in range(ROUND_SEGMENTS)]
        return outer + inner[::-1]
    if shape == "i_beam":
        web = w * 0.25
        flange = h * 0.25
        return [[-w / 2.0, 0.0], [w / 2.0, 0.0], [w / 2.0, flange], [web / 2.0, flange], [web / 2.0, h - flange],
                [w / 2.0, h - flange], [w / 2.0, h], [-w / 2.0, h], [-w / 2.0, h - flange], [-web / 2.0, h - flange],
                [-web / 2.0, flange], [-w / 2.0, flange]]
    if shape == "channel":
        web = w * 0.25
        flange = h * 0.25
        return [[-w / 2.0, 0.0], [w / 2.0, 0.0], [w / 2.0, h], [-w / 2.0, h], [-w / 2.0, h - flange], [-web / 2.0, h - flange],
                [-web / 2.0, flange], [w / 2.0, flange], [w / 2.0, h - flange]]
    if shape == "angle":
        leg = min(w, h) * 0.5
        return [[-w / 2.0, 0.0], [w / 2.0, 0.0], [w / 2.0, leg], [0.0, leg], [0.0, h], [-w / 2.0, h]]
    if shape == "trapezoid":
        taper = 0.25
        half_top = (w / 2.0) * (1.0 - taper)
        return [[-w / 2.0, 0.0], [w / 2.0, 0.0], [half_top, h], [-half_top, h]]
    raise BadRequest(f"shape must be one of {', '.join(SHAPES)}")


PIVOTS = ("top-left", "top", "top-right", "left", "center", "right", "bottom-left", "bottom", "bottom-right")
PIVOT_LABELS = {"top-left": "Top left", "top": "Top", "top-right": "Top right", "left": "Left", "center": "Centre", "right": "Right",
                "bottom-left": "Bottom left", "bottom": "Bottom", "bottom-right": "Bottom right"}
USER_KEYS = ("pivot", "angle", "mirror", "radius", "offset_x", "offset_y", "offset_z")


def pivot_point(polygon: Sequence[Sequence[float]], pivot: str) -> tuple[float, float]:
    """Where the pivot sits on the bounding box of the drawn polygon: one of nine positions, the centre by default."""
    if pivot not in PIVOTS:
        raise BadRequest(f"pivot alignment must be one of {', '.join(PIVOTS)}")
    us, vs = [q[0] for q in polygon], [q[1] for q in polygon]
    lo_u, hi_u, lo_v, hi_v = min(us), max(us), min(vs), max(vs)
    col = "left" if pivot.endswith("left") or pivot == "left" else "right" if pivot.endswith("right") or pivot == "right" else "mid"
    row = "top" if pivot.startswith("top") else "bottom" if pivot.startswith("bottom") else "mid"
    return ({"left": lo_u, "right": hi_u, "mid": (lo_u + hi_u) / 2}[col], {"top": hi_v, "bottom": lo_v, "mid": (lo_v + hi_v) / 2}[row])


def round_corners(polygon: Sequence[Sequence[float]], radius: float) -> list[list[float]]:
    """Every corner of a polygon replaced by a circular arc of ``radius`` (m). A corner whose tangent length would be more than half of either
    neighbouring edge gets the largest radius that fits, so neighbouring arcs never overlap and no edge disappears."""
    n = len(polygon)
    if radius <= 1e-9 or n < 3:
        return [list(map(float, q)) for q in polygon]
    pts = [tuple(map(float, q)) for q in polygon]
    out: list[list[float]] = []
    for i in range(n):
        p0, p1, p2 = pts[i - 1], pts[i], pts[(i + 1) % n]
        a, b = G.unit(G.sub(p0, p1)), G.unit(G.sub(p2, p1)) # type: ignore
        cosang = max(-1.0, min(1.0, G.dot(a, b)))
        angle = math.acos(cosang)                                              # the angle between the two edges at this corner
        if angle < 1e-6 or abs(angle - math.pi) < 1e-6:                        # a spike or a straight run: nothing to round
            out.append(list(p1))
            continue
        tangent = radius / math.tan(angle / 2)
        room = min(math.dist(p0, p1), math.dist(p1, p2)) / 2
        if tangent > room:
            tangent = room
        r = tangent * math.tan(angle / 2)
        start, end = G.add(p1, G.mul(a, tangent)), G.add(p1, G.mul(b, tangent)) # type: ignore
        bis = G.unit(G.add(a, b)) # type: ignore
        centre = G.add(p1, G.mul(bis, r / math.sin(angle / 2))) # type: ignore
        a0, a1 = math.atan2(start[1] - centre[1], start[0] - centre[0]), math.atan2(end[1] - centre[1], end[0] - centre[0])
        sweep = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi                  # the short way round
        steps = max(2, int(math.ceil(abs(sweep) / (math.pi / 2) * ARC_SEGMENTS)))
        for k in range(steps + 1):
            t = a0 + sweep * k / steps
            q = [centre[0] + r * math.cos(t), centre[1] + r * math.sin(t)]
            if not out or math.dist(out[-1], q) > 1e-9:                         # arcs that meet at an edge's middle share that point
                out.append(q)
    if len(out) > 1 and math.dist(out[0], out[-1]) <= 1e-9:
        out.pop()
    return out


def user_section(source: Mapping[str, Any]) -> list[list[float]]:
    """The cross-section of a user-defined sweep from the polygon the person drew (``polygon``, (u, v) in metres: u across the path, v up) and its
    settings: corners rounded, then the pivot moved to the path (``pivot``: nine positions on the polygon's bounding box), mirrored across the
    path (``mirror``), then turned about the pivot (``angle``, degrees, counter-clockwise)."""
    poly = [[float(q[0]), float(q[1])] for q in source["polygon"]]
    px, py = pivot_point(poly, str(source.get("pivot", "center")))
    pts = round_corners(poly, float(source.get("radius", 0.0)))
    pts = [[q[0] - px, q[1] - py] for q in pts]
    if source.get("mirror"):
        pts = [[-q[0], q[1]] for q in pts][::-1]
    angle = math.radians(float(source.get("angle", 0.0)))
    c, s = math.cos(angle), math.sin(angle)
    return [[q[0] * c - q[1] * s, q[0] * s + q[1] * c] for q in pts]


def _user_source(raw: Mapping[str, Any], to_m: float) -> dict[str, Any]:
    """Validate the settings of a user-defined profile. ``polygon`` is in drawing units."""
    poly_raw = raw.get("polygon")
    if not isinstance(poly_raw, (list, tuple)) or not 3 <= len(poly_raw) <= 200:
        raise BadRequest("A user-defined profile needs a closed polygon of 3 to 200 points")
    polygon = []
    for q in poly_raw:
        if not isinstance(q, (list, tuple)) or len(q) != 2:
            raise BadRequest("Every point of the polygon must be [x, y]")
        polygon.append([float(number(q[0], "x", -1e9, 1e9)) * to_m, float(number(q[1], "y", -1e9, 1e9)) * to_m])
    if len(polygon) > 3 and math.dist(polygon[0], polygon[-1]) < 1e-9:
        polygon.pop()
    return {"polygon": polygon, "pivot": E._choice(raw.get("pivot", "center"), PIVOTS, "pivot"), "angle": number(raw.get("angle", 0.0), "angle", -360, 360),
            "mirror": boolean(raw.get("mirror", False), "mirror"), "radius": number(raw.get("radius", 0.0), "radius", 0, 100000) / 1000.0}


def _clean_section(raw: Any) -> list[list[float]]:
    if not isinstance(raw, (list, tuple)) or not 3 <= len(raw) <= 200:
        raise BadRequest("A section needs 3 to 200 points")
    pts = []
    for q in raw:
        if not isinstance(q, (list, tuple)) or len(q) != 2:
            raise BadRequest("Every section point must be [u, v] in metres")
        pts.append([float(number(q[0], "u", -50, 50)), float(number(q[1], "v", -50, 50))])
    if not G.is_simple_polygon([tuple(q) for q in pts]) or abs(G.signed_area([tuple(q) for q in pts])) < 1e-9:
        raise BadRequest("The section crosses itself or has no area")
    return pts


def _path(points: Any, to_m: float, closed: bool) -> list[list[float]]:
    if not isinstance(points, (list, tuple)) or not 2 <= len(points) <= MAX_PATH_POINTS:
        raise BadRequest(f"A path needs 2 to {MAX_PATH_POINTS} points")
    out: list[list[float]] = []
    for q in points:
        if not isinstance(q, (list, tuple)) or len(q) not in (2, 3):
            raise BadRequest("Every path point must be [x, y] or [x, y, z]")
        x = float(number(q[0], "x", -1e9, 1e9)) * to_m
        y = float(number(q[1], "y", -1e9, 1e9)) * to_m
        z = float(number(q[2], "z", -1e6, 1e6)) if len(q) == 3 else 0.0
        pt = [x, y, z]
        if not out or math.dist(pt, out[-1]) > 1e-6:
            out.append(pt)
    if closed and len(out) > 2 and math.dist(out[0], out[-1]) <= 1e-6:
        out.pop()
    if len(out) < (3 if closed else 2):
        raise BadRequest("A closed path needs 3 different points" if closed else "A path needs 2 different points")
    return out


def _check_path(path: Sequence[Sequence[float]], closed: bool) -> None:
    segs = [(tuple(path[i]), tuple(path[(i + 1) % len(path)])) for i in range(len(path) if closed else len(path) - 1)]
    for i, (a, b) in enumerate(segs):
        for j in range(i + 2, len(segs)):
            if closed and i == 0 and j == len(segs) - 1:
                continue
            if _seg_hit(a, b, *segs[j]): # type: ignore
                raise BadRequest("The path crosses itself")


CORNER_STYLES = ("miter", "rounded", "chamfered")

def round_path(path, closed, radius, style="rounded", arc_segments=ARC_SEGMENTS):
    """Insert arc / chamfer points at every interior corner of a polyline."""
    pts = list(path)
    n = len(pts)
    out = []
    for i, p in enumerate(pts):
        if not closed and i in (0, n - 1):
            out.append(p); continue
        p0, p1, p2 = pts[i - 1], p, pts[(i + 1) % n]
        a, b = G.unit(G.sub(p0, p1)), G.unit(G.sub(p2, p1))
        ang = math.acos(max(-1.0, min(1.0, G.dot(a, b))))
        if ang < 1e-6 or abs(ang - math.pi) < 1e-6:
            out.append(p); continue
        tangent = min(radius / math.tan(ang / 2),
                      math.dist(p0, p1) / 2, math.dist(p1, p2) / 2)
        start = G.add(p1, G.mul(a, tangent))
        end   = G.add(p1, G.mul(b, tangent))
        if style == "chamfered":
            out.extend([start, end]); continue
        centre = G.add(p1, G.mul(G.unit(G.add(a, b)),
                                 tangent / math.sin(ang / 2)))
        a0 = math.atan2(start[1] - centre[1], start[0] - centre[0])
        a1 = math.atan2(end[1]   - centre[1], end[0]   - centre[0])
        sweep = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
        steps = max(2, int(math.ceil(abs(sweep) / (math.pi / 2) * arc_segments)))
        for k in range(steps + 1):
            t = a0 + sweep * k / steps
            out.append([centre[0] + radius * math.cos(t),
                        centre[1] + radius * math.sin(t)])
    if closed and math.dist(out[0], out[-1]) <= 1e-9:
        out.pop()
    return out


def _to3d(path: Sequence[Sequence[float]]) -> list[tuple[float, float, float]]:
    """Promote a 2‑D plan path to 3‑D (z = 0) without touching a path that is already 3‑D."""
    out: list[tuple[float, float, float]] = []
    for p in path:
        if len(p) == 2:
            out.append((float(p[0]), float(p[1]), 0.0))
        elif len(p) == 3:
            out.append((float(p[0]), float(p[1]), float(p[2])))
        else:
            raise BadRequest("A path point must be [x, y] or [x, y, z]")
    return out


def _cross3(a, b):
    """3‑D cross product; `G.cross` is 2‑D (a scalar) and stays that way for the wall module."""
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _dot3(a, b):
    """3‑D dot product; `G.dot` is 2‑D and stays that way for the wall module."""
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _frames(path: Sequence[Sequence[float]], closed: bool, section: Sequence[Sequence[float]]) -> list[dict[str, Any]]:
    """For every path point: the section's left axis ``m``, its up axis ``v``, the forward tangent ``t``, and the mitre ``scale``
    (a section point at ``u`` sits ``u * scale`` along ``m``, and at ``v`` along ``v``). Refuses the corners the profile cannot get round.

    The path may be plan-only (``[x, y]``, treated as z = 0, exactly the old behaviour) or 3-D (``[x, y, z]``). In 3-D the section is tilted
    to stay perpendicular to the path's tangent, and the mitre is still computed in plan so a climbing sweep turns the way it does when drawn
    in plan.
    """
    pts = _to3d(path)
    n = len(pts)
    count = n if closed else n - 1
    if count < 1:
        raise BadRequest("A path needs at least two points")

    tangents: list[tuple[float, float, float]] = []
    for i in range(count):
        dx = pts[(i + 1) % n][0] - pts[i][0]
        dy = pts[(i + 1) % n][1] - pts[i][1]
        dz = pts[(i + 1) % n][2] - pts[i][2]
        length = math.sqrt(dx * dx + dy * dy + dz * dz)
        if length < 1e-9:
            raise BadRequest("A path segment must have non-zero length")
        tangents.append((dx / length, dy / length, dz / length))
    lengths       = [math.dist(pts[(i + 1) % n], pts[i]) for i in range(count)]
    left_extent   = max(max(u  for u, _ in section), 0.0)
    right_extent  = max(max(-u for u, _ in section), 0.0)
    up = (0.0, 0.0, 1.0)

    def add3(a, b):
        return (a[0] + b[0], a[1] + b[1], a[2] + b[2])

    def mul3(a, k):
        return (a[0] * k, a[1] * k, a[2] * k)

    def perp(v, t):
        """Component of ``v`` perpendicular to the unit vector ``t``."""
        k = _dot3(v, t)
        return (v[0] - t[0] * k, v[1] - t[1] * k, v[2] - t[2] * k)

    def unit_or_raise(v, msg):
        size = math.sqrt(_dot3(v, v))
        if size < 1e-9:
            raise BadRequest(msg)
        return mul3(v, 1.0 / size)

    frames: list[dict[str, Any]] = []
    for i in range(n):
        if closed:
            t_in, t_out = tangents[i - 1], tangents[i]
        else:
            t_in  = tangents[i - 1] if i > 0 else tangents[0]
            t_out = tangents[i]     if i < count else tangents[-1]

        # The forward tangent at the node: the average of the two segment tangents. It is what the chamfer pushes past the end along,
        # and what the v-axis is projected perpendicular to.
        t_node = unit_or_raise(add3(t_in, t_out), "The path doubles back on itself")

        # The section's "up": global up projected perpendicular to the (possibly climbing) tangent. A purely vertical tangent has no
        # canonical section orientation, so it is refused with a clear message.
        v_axis = unit_or_raise(perp(up, t_node),
                               "A path point whose tangent is vertical cannot carry a section: a sweep cannot go straight up")

        # Mitre in plan: the horizontal lefts of the two tangents, bisected.
        u_in  = unit_or_raise(_cross3(up, t_in),  "The path turns too sharply for a mitred corner")
        u_out = unit_or_raise(_cross3(up, t_out), "The path turns too sharply for a mitred corner")
        m     = unit_or_raise(add3(u_in, u_out), "The path doubles back on itself")
        cos_half = _dot3(m, u_out)
        if cos_half <= 1e-9 or 1.0 / cos_half > MITER_LIMIT:
            raise BadRequest("The path turns too sharply for a mitred corner",
                             hint=f"Corners may turn at most about {math.degrees(2 * math.acos(1 / MITER_LIMIT)):.0f}°")

        # The section's "left" at the node: the plan mitre direction, projected perpendicular to the (climbed) tangent.
        u_axis = unit_or_raise(perp(m, t_node), "The path turns too sharply for a mitred corner")

        # Reach: how far each corner's mitre extends into its segment, for the "profile too wide" check. The sign of the 3-D cross's
        # z-component (i.e. the plan turn) picks the inside face.
        turn = _cross3(t_in, t_out)[2]
        dot_in_out = _dot3(t_in, t_out)
        denom = 1.0 + dot_in_out
        tan_half = math.sqrt(max(0.0, 1.0 - dot_in_out * dot_in_out)) / denom if denom > 1e-9 else 0.0
        inside = left_extent if turn > 0 else right_extent

        frames.append({
            "p":     pts[i],
            "m":     u_axis,             # section's u axis (mitered "left")
            "v":     v_axis,             # section's v axis (perpendicular to tangent, closest to global up)
            "t":     t_node,             # forward tangent (used by chamfered end caps)
            "scale": 1.0 / cos_half,     # u miter scale; v is unscaled (matches the old planar behaviour)
            "reach": tan_half * inside,
        })

    for i in range(count):
        if frames[i]["reach"] + frames[(i + 1) % n]["reach"] > lengths[i] + 1e-9:
            raise BadRequest("The profile is too wide for the corners of this path",
                             hint="Make the profile narrower or the segments longer")
    return frames


def sweep_mesh(
    path: Sequence[Sequence[float]],
    closed: bool,
    section: Sequence[Sequence[float]],
    z0: float,
    *,
    end_cap: str = "flat",
    end_chamfer: float = 0.0,
) -> G.Mesh:
    """A section swept along a path, capped at the ends of an open path.

    ``end_cap`` controls how the two open ends are closed:

    * ``"flat"`` (the default, unchanged) — each end is triangulated exactly as drawn, matching the section at the path's end point;
    * ``"none"`` — nothing closes the ends, so a pipe run or railing that continues past the drawn path stays open;
    * ``"chamfered"`` — each end is pushed past the drawn path by ``end_chamfer`` (m) along the path's forward tangent, and the section is
      scaled about its centroid so the tip is inset by ``end_chamfer`` on every side. A short side band joins the drawn end to that smaller
      ring, so the drawn length is unchanged and the sweep simply finishes with a bevel instead of a flat face.

    A closed path ignores ``end_cap``. A chamfered end needs ``end_chamfer > 0`` and is refused when the chamfer is wider than the section it
    must sit in.
    """
    if end_cap not in END_CAPS:
        raise BadRequest(f"end cap must be one of {', '.join(END_CAPS)}")
    chamfer = float(end_chamfer)
    if end_cap == "chamfered":
        if closed:
            end_cap = "flat"                                    # a closed sweep has no ends to cap
        elif chamfer <= 1e-9:
            raise BadRequest(
                "A chamfered end cap needs a positive chamfer length",
                hint="Give end_chamfer in metres, e.g. 0.01 for 10 mm",
            )

    section: list[Pt] = [(float(q[0]), float(q[1])) for q in section]
    if G.signed_area(section) < 0:
        section.reverse()
    frames = _frames(path, closed, section)
    s, n = len(section), len(frames)

    # Centroid and outer radius, used to shrink chamfered caps so the farthest point is inset by exactly ``end_chamfer``.
    cx = sum(u for u, _ in section) / s
    cy = sum(v for _, v in section) / s
    outer = max(math.hypot(u - cx, v - cy) for u, v in section)
    scale = 1.0
    if end_cap == "chamfered":
        if outer <= 1e-12:
            end_cap = "flat"
        elif chamfer >= outer:
            raise BadRequest(
                "The chamfer is wider than the section it is cutting",
                hint="Use a smaller end_chamfer or no chamfered cap",
            )
        else:
            scale = (outer - chamfer) / outer

    def _ring(frame: Mapping[str, Any], *, along: float = 0.0, factor: float = 1.0) -> list[tuple[float, float, float]]:
        """One ring: the section centred on ``(cx, cy)``, scaled by ``factor``, placed on the frame, pushed ``along`` metres past it in the
        frame's forward tangent direction (0 for body rings). Each section point contributes to all three axes so a climbing sweep tilts
        correctly and a horizontal sweep reproduces the old output exactly (``m[2]=0``, ``v[2]=1``)."""
        px, py, pz = frame["p"]
        if along:
            tx, ty, tz = frame["t"]
            px += tx * along
            py += ty * along
            pz += tz * along
        mx, my, mz = frame["m"]
        vx, vy, vz = frame["v"]
        k = frame["scale"]
        out: list[tuple[float, float, float]] = []
        for u, v in section:
            uu = (cx + (u - cx) * factor) * k
            vv =  cy + (v - cy) * factor
            out.append((px + mx * uu + vx * vv,
                        py + my * uu + vy * vv,
                        pz + z0 + mz * uu + vz * vv))
        return out

    rings = [_ring(f) for f in frames]
    verts: list[list[float]] = [list(v) for r in rings for v in r]
    faces: list[list[int]] = []

    for i in range(n if closed else n - 1):
        j = (i + 1) % n
        for k in range(s):
            a, b = i * s + k, i * s + (k + 1) % s
            c, d = j * s + (k + 1) % s, j * s + k
            faces += [[a, b, c], [a, c, d]]

    if not closed:
        if end_cap == "flat":
            for t in G.triangulate(section):
                faces.append([(n - 1) * s + t[0], (n - 1) * s + t[1], (n - 1) * s + t[2]])
                faces.append([t[0], t[2], t[1]])
        elif end_cap == "chamfered":
            end_small   = _ring(frames[-1], along=+chamfer, factor=scale)
            start_small = _ring(frames[0],  along=-chamfer, factor=scale)
            end_base   = len(verts); verts += [list(v) for v in end_small]
            start_base = len(verts); verts += [list(v) for v in start_small]
            last = (n - 1) * s
            for k in range(s):
                k2 = (k + 1) % s
                faces += [[last + k, last + k2, end_base + k2],
                          [last + k, end_base + k2, end_base + k]]
                faces += [[start_base + k, start_base + k2, k2],
                          [start_base + k, k2, k]]
            for t in G.triangulate(section):
                faces.append([end_base   + t[0], end_base   + t[1], end_base   + t[2]])
                faces.append([start_base + t[0], start_base + t[2], start_base + t[1]])
        # end_cap == "none": leave both ends open.

    return E._positive_volume((verts, faces))


def anchor_of(project: Any, oid: str) -> list[float]:
    """The reference point of an element a sweep is fixed to: the centre of its footprint and the foot of it, in metres."""
    lo, hi = project.model.world_bounds(oid)
    return [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]]


def _follow(project: Any, meta: Mapping[str, Any]) -> list[float]:
    """How far the element a sweep is fixed to has moved since the sweep was fixed to it: (dx, dy, dz)."""
    host = E.m(meta, "fixed_to")
    if not host or not E.alive(project, str(host)):
        return [0.0, 0.0, 0.0]
    now, then = anchor_of(project, str(host)), E.m(meta, "fixed_ref")
    return [now[i] - float(then[i]) for i in range(3)]


def placed(project: Any, meta: Mapping[str, Any]) -> tuple[list[list[float]], float]:
    """The path (m) and the height of the section's origin as the sweep stands: the stored path and base, moved by the offsets X, Y, Z and by the
    element it is fixed to.

    The path is returned in 3‑D: a 2‑D stored path is promoted with ``z = 0``, and the level elevation and base are folded into each point's z.
    The second return value is kept for backwards compatibility and is ``0.0`` unless the caller passes a non-zero ``z0`` explicitly; ``sweep_mesh``
    still accepts that as an additional vertical offset.
    """
    dx, dy, dz = _follow(project, meta)
    ox, oy, oz = (float(E.m(meta, k, 0.0)) for k in ("offset_x", "offset_y", "offset_z"))
    base = E.level_elevation(project, E.m(meta, "level")) + float(E.m(meta, "base", 0.0)) + oz + dz
    path = _to3d(E.m(meta, "path"))
    if E.m(meta, "avoid_floor", False):
        path = offset_sweep_path(path, base)
        base = 0.0                                       # already folded into the path
    return [(q[0] + ox + dx, q[1] + oy + dy, q[2] + base) for q in path], 0.0 # type: ignore


def regenerate_sweep(project: Any, oid: str) -> None:
    meta = E.meta_of(project, oid)
    closed = bool(E.m(meta, "closed", False))
    source = E.m(meta, "profile_source")
    if source:
        E._attach(project, oid, {"section": _clean_section(user_section(source))})
        meta = E.meta_of(project, oid)
    section = _clean_section(E.m(meta, "section"))
    path, z0 = placed(project, meta)
    _check_path(path, closed)
    E._set_mesh(project, oid, sweep_mesh(
        path, closed, section, z0,
        end_cap=str(E.m(meta, "end_cap", "flat")),
        end_chamfer=float(E.m(meta, "end_chamfer", 0.0)),
    ))
    if E.m(meta, "fixed_to"):
        E._attach(project, oid, {"fixed_seen": anchor_of(project, str(E.m(meta, "fixed_to")))})


def fix_to(project: Any, oid: str, host: str | None) -> None:
    """Fix a sweep to an element (it then moves with it), or release it (``None``): the sweep stays where it stands."""
    meta = E.meta_of(project, oid)
    if E.m(meta, "category") != "sweep":
        raise BadRequest("Only a sweep can be fixed to an element")
    if host:
        if host == oid or not E.alive(project, host):
            raise BadRequest("Choose another element that exists to fix the profile to")
        E._attach(project, oid, {"fixed": True, "fixed_to": host, "fixed_ref": anchor_of(project, host), "fixed_seen": anchor_of(project, host)})
    else:
        dx, dy, dz = _follow(project, meta)                                     # released where it stands: what it followed becomes part of its offsets
        E._attach(project, oid, {"fixed": False, "fixed_to": None, "fixed_ref": None, "fixed_seen": None, "offset_x": float(E.m(meta, "offset_x", 0.0)) + dx,
                                 "offset_y": float(E.m(meta, "offset_y", 0.0)) + dy, "offset_z": float(E.m(meta, "offset_z", 0.0)) + dz})
    regenerate_sweep(project, oid)


def follow_fixed(project: Any) -> int:
    """After a command: every sweep fixed to an element moves with it. A sweep whose element is gone is released where it stands."""
    done = 0
    for oid in project.model.query_ids(metadata={f"{E.NS}:fixed": True}, limit=10_000):
        meta = E.meta_of(project, oid)
        host = str(E.m(meta, "fixed_to") or "")
        if not host or not E.alive(project, host):
            fix_to_released(project, oid)
            done += 1
        elif any(abs(a - b) > 1e-9 for a, b in zip(anchor_of(project, host), E.m(meta, "fixed_seen") or [0, 0, 0])):
            regenerate_sweep(project, oid)
            done += 1
    return done


def fix_to_released(project: Any, oid: str) -> None:
    meta = E.meta_of(project, oid)
    seen, then = E.m(meta, "fixed_seen"), E.m(meta, "fixed_ref")
    d = [float(seen[i]) - float(then[i]) for i in range(3)] if seen and then else [0.0, 0.0, 0.0]        # where it last stood
    E._attach(project, oid, {"fixed": False, "fixed_to": None, "fixed_ref": None, "fixed_seen": None, "offset_x": float(E.m(meta, "offset_x", 0.0)) + d[0],
                             "offset_y": float(E.m(meta, "offset_y", 0.0)) + d[1], "offset_z": float(E.m(meta, "offset_z", 0.0)) + d[2]})
    regenerate_sweep(project, oid)


def add_sweep(project: Any, p: Mapping[str, Any], to_m: float) -> str:
    """A sweep along ``points`` (drawing units, plan or 3‑D). The section is a ``section`` polygon (m), or ``shape`` with ``width`` and
    ``height`` (mm)."""
    level = project.level(text(p.get("level"), "level", 80))
    closed = boolean(p["closed"], "closed") if p.get("closed") is not None else False
    path = _path(p.get("points"), to_m, closed)
    is_void = boolean(p["void"], "void") if p.get("void") is not None else False
    end_cap = E._choice(p.get("end_cap", "flat"), END_CAPS, "end_cap")
    end_chamfer = number(p.get("end_chamfer", 0.0), "end_chamfer", 0.0, 5000.0) / 1000.0
    if closed:                                                              # a closed sweep has no ends to cap
        end_cap, end_chamfer = "flat", 0.0
    values: dict[str, Any] = {
        "category": "sweep", "type": "Void Sweep" if is_void else "Sweep",
        "void_mode": is_void, "level": level["id"], "path": path, "closed": closed,
        "base": number(p.get("base", 0.0), "base", -500, 500),
        "material": E._choice(p.get("material", "Concrete"), E.MATERIALS, "material"),
        "end_cap": end_cap,
        "end_chamfer": end_chamfer,
        "avoid_floor": boolean(p.get("avoid_floor", False), "avoid_floor"),
    }
    if is_void:
        values.update(CU.creation_values(project, p))
    if p.get("profile") == "user":
        values["profile_source"] = _user_source(p, to_m)
        values["section"] = _clean_section(user_section(values["profile_source"]))
        for k in ("offset_x", "offset_y", "offset_z"):
            values[k] = number(p.get(k, 0.0), k, -1e6, 1e6) / 1000.0
    elif p.get("section") is not None:
        values["section"] = _clean_section(p["section"])
    else:
        shape = E._choice(p.get("shape", "rectangle"), SHAPES, "shape")
        width, height = number(p.get("width", 200), "width", 1, 50000) / 1000.0, number(p.get("height", 200), "height", 1, 50000) / 1000.0
        values.update(shape=shape, width=width, height=height, section=section_polygon(shape, width, height))
    oid = project.model.add_box(width=1.0, depth=1.0, height=1.0, name=text(p.get("name") or ("Void Sweep" if is_void else "Sweep"), "name", 80))
    E._attach(project, oid, values)
    regenerate_sweep(project, oid)
    if p.get("fixed_to"):
        fix_to(project, oid, text(p["fixed_to"], "fixed_to", 80))
    return oid


def regenerate(project: Any, oid: str) -> None:
    if E.m(E.meta_of(project, oid), "category") == "sweep":
        regenerate_sweep(project, oid)
    else:
        regenerate_extrusion(project, oid)


# ----------------------------------------------------------------------------------------------------------------- edit, move, turn
def move(project: Any, oid: str, meta: Mapping[str, Any], dx: float, dy: float, dz: float) -> None:
    if E.m(meta, "category") == "sweep":
        old = E.m(meta, "path")
        new_path: list[list[float]] = []
        for q in old:
            if len(q) >= 3:
                new_path.append([q[0] + dx, q[1] + dy, q[2]])           # z moves via base, not the path itself
            else:
                new_path.append([q[0] + dx, q[1] + dy])
        E._attach(project, oid, {"path": new_path, "base": float(E.m(meta, "base", 0.0)) + dz})
    else:
        E._attach(project, oid, {"profile": [[q[0] + dx, q[1] + dy] for q in E.m(meta, "profile")],
                                 "base": float(E.m(meta, "base", 0.0)) + dz})


def rotate(project: Any, oid: str, meta: Mapping[str, Any], angle: float, centre: tuple[float, float]) -> None:
    if E.m(meta, "category") == "sweep":
        new_path: list[list[float]] = []
        for q in E.m(meta, "path"):
            turned = E._turn(q[:2], angle, centre)
            if len(q) >= 3:
                new_path.append([turned[0], turned[1], q[2]])           # rotate in plan only, keep elevation
            else:
                new_path.append([turned[0], turned[1]])
        E._attach(project, oid, {"path": new_path})
    else:
        E._attach(project, oid, {"profile": [E._turn(q, angle, centre) for q in E.m(meta, "profile")]})


def set_property(project: Any, oid: str, meta: Mapping[str, Any], key: str, value: Any) -> dict[str, Any] | None:
    """The parameter changes for one edited property (``None`` when the edit was made here, as for the solid/void switch)."""
    cat = E.m(meta, "category")
    if key == "level":
        return {"level": project.level(text(value, "level", 80))["id"]}
    if key == "base":
        return {"base": number(value, "base", -500, 500)}
    if (cat == "void" or (cat == "sweep" and E.m(meta, "void_mode"))) and key in ("cut_enabled", "cut_policy", "target_add", "target_remove", "host",
                                 "exclude_add", "exclude_remove", "exclude_category_add", "exclude_category_remove"):
        return CU.set_property(project, oid, meta, key, value)
    if cat == "sweep" and key == "sweep_mode":
        is_void = value == "Void"
        if value not in ("Solid", "Void"):
            raise BadRequest("Sweep mode must be Solid or Void")
        if bool(E.m(meta, "void_mode")) != is_void:
            E._attach(project, oid, {"void_mode": is_void, "type": "Void Sweep" if is_void else "Sweep"})
        return None
    if key == "form" and cat in ("extrusion", "void"):
        make_void(project, oid, value == "Void") if (value == "Void") != (cat == "void") else None
        return None
    if cat in ("extrusion", "void"):
        if key == "depth":
            return {"depth": number(value, "depth", 0.001, 500)}
        if key == "material" and cat == "extrusion":
            return {"material": E._choice(value, E.MATERIALS, "material")}
    if cat == "sweep":
        if key == "material":
            return {"material": E._choice(value, E.MATERIALS, "material")}
        if key in ("offset_x", "offset_y", "offset_z"):
            return {key: number(value, key, -1e6, 1e6) / 1000.0}
        if key == "fixed_to":
            host = str(value or "").strip() or None
            fix_to(project, oid, host)
            return None
        if key == "end_cap":
            if bool(E.m(meta, "closed", False)):
                raise BadRequest("A closed sweep has no ends to cap")
            return {"end_cap": E._choice(value, END_CAPS, "end_cap")}
        if key == "end_chamfer":
            return {"end_chamfer": number(value, "end_chamfer", 0.0, 5000.0) / 1000.0}
        if key == "avoid_floor":
            return {"avoid_floor": boolean(value, "avoid_floor")}
        source = E.m(meta, "profile_source")
        if source and key in USER_KEYS[:4]:
            new = dict(source)
            if key == "pivot":
                new["pivot"] = E._choice(value, PIVOTS, "pivot")
            elif key == "angle":
                new["angle"] = number(value, "angle", -360, 360)
            elif key == "mirror":
                new["mirror"] = value in (True, "true", "True", "Yes", "yes", 1, "1")
            else:
                new["radius"] = number(value, "radius", 0, 100000) / 1000.0
            return {"profile_source": new}
        if key in ("width", "height") and E.m(meta, "shape"):
            width = number(value, key, 1, 50000) / 1000.0 if key == "width" else float(E.m(meta, "width"))
            height = number(value, key, 1, 50000) / 1000.0 if key == "height" else float(E.m(meta, "height"))
            return {"width": width, "height": height, "section": section_polygon(str(E.m(meta, "shape")), width, height)}
        if key == "shape" and E.m(meta, "shape"):
            shape = E._choice(value, SHAPES, "shape")
            return {"shape": shape, "section": section_polygon(shape, float(E.m(meta, "width")), float(E.m(meta, "height")))}
    raise BadRequest(f"'{key}' is not an editable property of this element")


# --------------------------------------------------------------------------------------------------------------------- plan and panel
def plan_item(project: Any, oid: str, f: float) -> dict[str, Any]:
    obj = project.model.get(oid)
    meta = obj["metadata"]
    cat = str(E.m(meta, "category"))
    if cat == "sweep":
        closed = bool(E.m(meta, "closed", False))
        path = [tuple(q) for q in placed(project, meta)[0]]
        section = _clean_section(E.m(meta, "section"))
        frames = _frames(path, closed, section)
        lo, hi = min(u for u, _ in section), max(u for u, _ in section)
        left = [(fr["p"][0] + fr["m"][0] * hi * fr["scale"], fr["p"][1] + fr["m"][1] * hi * fr["scale"]) for fr in frames]
        right = [(fr["p"][0] + fr["m"][0] * lo * fr["scale"], fr["p"][1] + fr["m"][1] * lo * fr["scale"]) for fr in frames]
        item: dict[str, Any] = {"id": oid, "category": "void" if E.m(meta, "void_mode") else "sweep", "name": obj["name"], "path": [[q[0] * f, q[1] * f] for q in path], "closed": closed}
        if closed:
            outer, inner = (left, right) if abs(G.signed_area(left)) >= abs(G.signed_area(right)) else (right, left)
            item["polygon"] = [[q[0] * f, q[1] * f] for q in outer]
            item["holes"] = [[[q[0] * f, q[1] * f] for q in inner]]
        else:
            item["polygon"] = [[q[0] * f, q[1] * f] for q in left + right[::-1]]
        return item
    outline = (E.m(meta, "cut_outline") or E.m(meta, "profile")) if cat == "extrusion" else E.m(meta, "profile")            # a notched extrusion is drawn as it is left
    item = {"id": oid, "category": cat, "name": obj["name"], "polygon": [[q[0] * f, q[1] * f] for q in outline]}
    if cat == "void" and not (CU.settings(meta)["enabled"] and CU.settings(meta)["policy"] != "disabled"):
        item["off"] = True
    holes = E.m(meta, "cut") or []
    if holes and cat == "extrusion":
        item["holes"] = [[[q[0] * f, q[1] * f] for q in h] for h in holes]
    return item


def _target_choices(project: Any, vid: str, exclude: Sequence[str] = ()) -> list[dict[str, Any]]:
    return [{"value": o, "label": f"{project.model.get(o)['name'] or str(E.category(project, o)).title()} ({E.category(project, o)})"}
            for o in E.element_ids(project) if o != vid and o not in exclude and E.category(project, o) not in CU.NEVER_HIT
            and not E.m(E.meta_of(project, o), "void_mode")]


def _cutting_rows(project: Any, vid: str, meta: Mapping[str, Any]) -> list[dict[str, Any]]:
    s = CU.settings(meta)
    rows: list[dict[str, Any]] = [
        {"key": "cut_enabled", "label": "Cut Intersecting Geometry", "type": "choice", "value": "Enabled" if s["enabled"] else "Disabled",
         "choices": [{"value": "Enabled", "label": "Enabled"}, {"value": "Disabled", "label": "Disabled"}]},
        {"key": "cut_policy", "label": "Cutting policy", "type": "choice", "value": s["policy"], "choices": [{"value": k, "label": v} for k, v in CU.POLICIES.items()]}]
    if s["policy"] == "targets":
        rows.append({"label": "Selected targets", "type": "readonly", "value": ", ".join(project.model.get(t)["name"] if E.alive(project, t) else f"{t} (deleted)" for t in s["targets"]) or "none chosen"})
        rows.append({"key": "target_add", "label": "Add a target", "type": "choice", "value": "", "choices": [{"value": "", "label": "Choose an element…"}, *_target_choices(project, vid, s["targets"])]})
        if s["targets"]:
            rows.append({"key": "target_remove", "label": "Remove a target", "type": "choice", "value": "",
                         "choices": [{"value": "", "label": "Choose a target…"}, *({"value": t, "label": project.model.get(t)["name"] if E.alive(project, t) else f"{t} (deleted)"} for t in s["targets"])]})
    elif s["policy"] == "hosted":
        rows.append({"key": "host", "label": "Host element", "type": "choice", "value": s["host"] or "", "choices": [{"value": "", "label": "None chosen"}, *_target_choices(project, vid)]})
    rows.append({"label": "Excluded elements", "type": "readonly", "value": ", ".join(
        project.model.get(t)["name"] if E.alive(project, t) else f"{t} (deleted)" for t in s["excluded"]) or "none"})
    rows.append({"key": "exclude_add", "label": "Exclude element", "type": "choice", "value": "",
                 "choices": [{"value": "", "label": "Choose an element…"}, *_target_choices(project, vid, s["excluded"])]})
    if s["excluded"]:
        rows.append({"key": "exclude_remove", "label": "Restore element cuts", "type": "choice", "value": "",
                     "choices": [{"value": "", "label": "Choose an element…"}] + [
                         {"value": t, "label": project.model.get(t)["name"] if E.alive(project, t) else f"{t} (deleted)"} for t in s["excluded"]]})
    rows.append({"label": "Excluded categories", "type": "readonly", "value": ", ".join(s["excluded_categories"]) or "none"})
    categories = CU.eligible_categories(project)
    rows.append({"key": "exclude_category_add", "label": "Exclude category", "type": "choice", "value": "",
                 "choices": [{"value": "", "label": "Choose a category…"}] + [
                     {"value": c, "label": c.title()} for c in categories if c not in s["excluded_categories"]]})
    if s["excluded_categories"]:
        rows.append({"key": "exclude_category_remove", "label": "Restore category cuts", "type": "choice", "value": "",
                     "choices": [{"value": "", "label": "Choose a category…"}] + [
                         {"value": c, "label": c.title()} for c in s["excluded_categories"]]})
    return rows


def _diagnostic_rows(project: Any, vid: str) -> list[dict[str, Any]]:
    rows = CU.classify(project, vid)
    out: list[dict[str, Any]] = [{"label": "Effect", "type": "readonly", "value": CU.summary(rows)}]
    word = {"cut": "Cut", "unsupported": "Not cut", "excluded": "Left alone"}
    out += [{"label": f"{r['name']} ({r['category']})", "type": "readonly", "value": f"{word[r['state']]}: {r['reason']}"} for r in rows]
    out += [{"label": "Missing", "type": "readonly", "value": f"{i} no longer exists"} for i in CU.stale(project, next(v for v in voids(project) if v["id"] == vid))]
    return out


def properties(project: Any, oid: str, out: dict[str, Any], volume: Mapping[str, Any]) -> None:
    meta = E.meta_of(project, oid)
    cat = str(E.m(meta, "category"))
    levels = E._levels(project)
    out["top"] = [{"label": "Type", "type": "readonly", "value": str(E.m(meta, "type", cat.title()))},
                  {"key": "level", "label": "Level", "type": "choice", "value": E.m(meta, "level"), "choices": levels}]
    if cat in ("extrusion", "void"):
        poly = [tuple(q) for q in E.m(meta, "profile")]
        out["top"] += [{"key": "form", "label": "Solid / Void", "type": "choice", "value": "Void" if cat == "void" else "Solid",
                        "choices": [{"value": "Solid", "label": "Solid"}, {"value": "Void", "label": "Void"}]}]
        if cat == "extrusion":
            out["top"].append({"key": "material", "label": "Material", "type": "choice", "value": E.m(meta, "material"), "choices": E._choices(E.MATERIALS)})
        rows = [{"label": "Plan area", "type": "readonly", "value": f"{abs(G.signed_area(poly)):.3f} m²"},
                {"label": "Perimeter", "type": "readonly", "value": f"{sum(math.dist(a, b) for a, b in _edges(poly)):.3f} m"}]
        if cat == "extrusion":
            rows.append(volume)
        constraints = [{"key": "base", "label": "Base above level", "type": "number", "unit": "m", "value": float(E.m(meta, "base", 0.0))},
                       {"key": "depth", "label": "Depth", "type": "number", "unit": "m", "value": float(E.m(meta, "depth"))}]
        if cat == "extrusion" and E.m(meta, "cut"):
            rows.append({"label": "Holes cut by voids", "type": "readonly", "value": str(len(E.m(meta, "cut")))})
        if cat == "extrusion" and E.m(meta, "cut_outline"):
            rows.append({"label": "Edge cut by voids", "type": "readonly", "value": "yes"})
        out["groups"] = [{"title": "Geometry", "rows": rows}, {"title": "Constraints", "rows": constraints}]
        if cat == "void":
            out["groups"][0]["title"] = "Geometry"
            out["groups"] += [{"title": "Cutting", "rows": _cutting_rows(project, oid, meta)}, {"title": "Diagnostics", "rows": _diagnostic_rows(project, oid)}]
        return
    path = [tuple(q) for q in E.m(meta, "path")]
    closed = bool(E.m(meta, "closed", False))
    length = G.chain_length(path, closed)
    swept_void = bool(E.m(meta, "void_mode"))
    out["top"].append({"key": "sweep_mode", "label": "Solid / Void", "type": "choice", "value": "Void" if swept_void else "Solid",
                       "choices": [{"value": "Solid", "label": "Solid"}, {"value": "Void", "label": "Void"}]})
    if not swept_void:
        out["top"].append({"key": "material", "label": "Material", "type": "choice", "value": E.m(meta, "material"), "choices": E._choices(E.MATERIALS)})
    rows: list[dict[str, Any]] = [{"label": "Path length", "type": "readonly", "value": f"{length:.3f} m"}, {"label": "Path", "type": "readonly", "value": "closed" if closed else "open"}]
    if E.m(meta, "shape"):
        rows[:0] = [{"key": "shape", "label": "Profile", "type": "choice", "value": E.m(meta, "shape"), "choices": [{"value": s, "label": s.title()} for s in SHAPES]},
                    {"key": "width", "label": "Profile width", "type": "number", "unit": "mm", "value": round(float(E.m(meta, "width")) * 1000, 6)},
                    {"key": "height", "label": "Profile height", "type": "number", "unit": "mm", "value": round(float(E.m(meta, "height")) * 1000, 6)}]
    source = E.m(meta, "profile_source")
    if source:
        rows[:0] = [{"label": "Profile", "type": "readonly", "value": f"User defined ({len(source['polygon'])} points)"},
                    {"key": "pivot", "label": "Pivot alignment", "type": "choice", "value": source.get("pivot", "center"),
                     "choices": [{"value": k, "label": PIVOT_LABELS[k]} for k in PIVOTS], "layout": "pivot"},
                    {"key": "angle", "label": "Angle", "type": "number", "unit": "°", "value": float(source.get("angle", 0.0))},
                    {"key": "mirror", "label": "Mirror", "type": "choice", "value": "Yes" if source.get("mirror") else "No", "choices": [{"value": "No", "label": "No"}, {"value": "Yes", "label": "Yes"}]},
                    {"key": "radius", "label": "Corner radius", "type": "number", "unit": "mm", "value": round(float(source.get("radius", 0.0)) * 1000, 6)}]
    if not swept_void:
        rows.append(volume)
    constraints = [{"key": "base", "label": "Base above level", "type": "number", "unit": "m", "value": float(E.m(meta, "base", 0.0))}]
    if source:
        constraints += [{"key": f"offset_{a}", "label": f"Offset {a.upper()}", "type": "number", "unit": "mm", "value": round(float(E.m(meta, f"offset_{a}", 0.0)) * 1000, 6)} for a in "xyz"]
        host = E.m(meta, "fixed_to")
        constraints.append({"key": "fixed_to", "label": "Fixed to", "type": "choice", "value": host or "",
                            "choices": [{"value": "", "label": "Nothing"}] + [{"value": i, "label": project.model.get(i)["name"] or str(E.m(project.model.get(i)["metadata"], "category")).title()}
                                                                                for i in E.element_ids(project) if i != oid and E.m(project.model.get(i)["metadata"], "category") not in ("door", "window")]})
    out["groups"] = [{"title": "Geometry", "rows": rows}, {"title": "Constraints", "rows": constraints}]
    if swept_void:
        out["groups"].extend([{"title": "Cutting", "rows": _cutting_rows(project, oid, meta)},
                              {"title": "Diagnostics", "rows": _diagnostic_rows(project, oid)}])


def check(project: Any, oid: str, label: str) -> list[dict[str, str]]:
    """Problems with one extrusion, void or sweep: an outline that no longer fits, and a void that cuts nothing where it overlaps something."""
    meta = E.meta_of(project, oid)
    cat = E.m(meta, "category")
    out: list[dict[str, str]] = []
    try:
        if cat == "sweep":
            path, _ = placed(project, meta)
            closed = bool(E.m(meta, "closed", False))
            _check_path(path, closed)
            _frames(path, closed, _clean_section(E.m(meta, "section")))
            cap = str(E.m(meta, "end_cap", "flat"))
            if cap not in END_CAPS:
                raise BadRequest(f"end cap must be one of {', '.join(END_CAPS)}")
            if cap == "chamfered" and not closed and float(E.m(meta, "end_chamfer", 0.0)) <= 1e-9:
                raise BadRequest("A chamfered end cap needs a positive chamfer length")
        elif not G.is_simple_polygon([tuple(q) for q in E.m(meta, "profile", [])]):
            raise BadRequest("The outline crosses itself")
    except BadRequest as exc:
        return [E.issue("error", oid, f"{label}: {exc.message}", "parameters")]
    if cat == "void" or (cat == "sweep" and E.m(meta, "void_mode")):
        s = CU.settings(meta)
        rows = CU.classify(project, oid)
        out += [E.issue("warning", oid, f"{label}: {r['name']} is reached but not cut: {r['reason']}", "geometry") for r in rows if r["state"] == "unsupported"]
        out += [E.issue("warning", oid, f"{label}: {i} no longer exists", "hosting") for i in CU.stale(project, next(v for v in voids(project) if v["id"] == oid))]
        if s["enabled"] and s["policy"] == "targets" and not s["targets"]:
            out.append(E.issue("warning", oid, f"{label} cuts nothing: its policy is Selected Targets and none is chosen", "geometry"))
        elif s["enabled"] and s["policy"] == "hosted" and not s["host"]:
            out.append(E.issue("warning", oid, f"{label} cuts nothing: its policy is Hosted Objects and it has no host", "geometry"))
        elif s["enabled"] and s["policy"] != "disabled" and not rows:
            out.append(E.issue("warning", oid, f"{label} does not touch anything it can cut", "geometry"))
    return out


def check_all(project: Any) -> list[dict[str, str]]:
    """Problems with all extrusions, voids and sweeps: an outline that no longer fits, and a void that cuts nothing where it overlaps something."""
    out: list[dict[str, str]] = []
    for oid in project.model.query_ids(metadata={f"{E.NS}:category": "extrusion"}, limit=10_000):
        out += check(project, oid, project.model.get(oid)["name"] or f"Extrusion {oid}")
    for oid in project.model.query_ids(metadata={f"{E.NS}:category": "void"}, limit=10_000):
        out += check(project, oid, project.model.get(oid)["name"] or f"Void {oid}")
    for oid in project.model.query_ids(metadata={f"{E.NS}:category": "sweep"}, limit=10_000):
        out += check(project, oid, project.model.get(oid)["name"] or f"Sweep {oid}")
    return out

