"""Walls that follow what is above them: attached to a roof, or extended to an upper level.

A wall's height is normally a number a person types. An *attached* wall has none of its own: its top is derived, so it stays right when the thing it
follows is edited, and it never silently disagrees with it.

Model
    ``spadra:top_to`` on a wall says what its top follows: ``{"kind": "roof", "roof": id, "offset": m, "free_height": m}`` or
    ``{"kind": "level", "level": id, "offset": m, "free_height": m}``. ``offset`` is added to the height (negative: stops short), ``free_height`` is
    the height the wall had before, which detaching restores. ``spadra:height`` stays the wall's greatest height, so everything that reads a wall's height
    (openings, quantities, the Properties panel) keeps working; ``spadra:top_profile`` is the roof line as (distance from the wall's start, height above its
    base) points, present only when the top is not level.

Roof
    The height and longitudinal profile come from the host roof underside (``elements.roof_facets``). A second facet evaluation at every wall and
    material-layer face produces the actual transverse roof cut, avoiding the wedge left by a centre-line extrusion. A wall whose centreline lies
    outside the roof is refused. A face that projects just half a wall thickness past an eave receives the adjacent roof plane extrapolation; farther
    unsupported faces are refused. Oblique roof-facet creases are split across wall/layer width where the evaluated facets meet; unsupported
    self-intersecting or ambiguous crease geometry is rejected rather than silently stored as a gap or overlap.

Level
    The top is the upper level's elevation plus the offset. Moving the level moves the top.

Both are re-derived after every command (``reconcile``), inside that command, so an undo takes the wall back together with the roof or level.
Deleting the roof detaches the wall (it keeps the height it had before it was attached).
"""
from __future__ import annotations

import math
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..tools.errors import BadRequest, NotFound
from ..tools.validation import number
from . import elements as E

EPS = 1e-6
MIN_HEIGHT = 0.05                  # m: a wall attached to a roof may not come down to less than this anywhere


def attachment(meta: Mapping[str, Any]) -> dict[str, Any] | None:
    top = E.m(meta, "top_to")
    return dict(top) if isinstance(top, Mapping) and top.get("kind") in ("roof", "level") else None


def is_attached(project: Any, oid: str) -> bool:
    return attachment(E.meta_of(project, oid)) is not None


# ---------------------------------------------------------------------------------------------------------------------------- the roof line
def _inside(poly: Sequence[tuple[float, float]], x: float, y: float) -> bool:
    """Whether a point lies in a plan polygon, its outline included."""
    n, hit = len(poly), False
    for i in range(n):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length > 1e-12 and abs((x - x1) * dy - (y - y1) * dx) / length < 1e-7 and min(x1, x2) - 1e-7 <= x <= max(x1, x2) + 1e-7 and min(y1, y2) - 1e-7 <= y <= max(y1, y2) + 1e-7:
            return True
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * dx / (y2 - y1):
            hit = not hit
    return hit


def _crossings(poly: Sequence[tuple[float, float]], a: tuple[float, float], b: tuple[float, float]) -> list[float]:
    """Where, as a fraction of a→b, the segment crosses the edges of a plan polygon."""
    out: list[float] = []
    dx, dy = b[0] - a[0], b[1] - a[1]
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
        ex, ey = x2 - x1, y2 - y1
        den = dx * ey - dy * ex
        if abs(den) < 1e-12:
            continue                                                              # parallel: any overlap begins and ends at other crossings
        t = ((x1 - a[0]) * ey - (y1 - a[1]) * ex) / den
        u = ((x1 - a[0]) * dy - (y1 - a[1]) * dx) / den
        if -1e-9 <= t <= 1 + 1e-9 and -1e-9 <= u <= 1 + 1e-9:
            out.append(min(1.0, max(0.0, t)))
    return out


def _distance_to_outline(poly: Sequence[tuple[float, float]], x: float, y: float) -> float:
    """Distance in plan to a roof facet, for a wall face straddling an eave.

    A gable or eave wall commonly lies on the roof footprint's boundary while
    half of its thickness projects outside. For just that half-thickness the
    roof plane is extrapolated to meet the wall face; more distant geometry is
    not silently attached to a surface that does not exist.
    """
    if _inside(poly, x, y):
        return 0.0
    closest = math.inf
    for a, b in zip(poly, (*poly[1:], poly[0])):
        dx, dy = b[0] - a[0], b[1] - a[1]
        d2 = dx * dx + dy * dy
        t = min(1.0, max(0.0, ((x - a[0]) * dx + (y - a[1]) * dy) / d2)) if d2 else 0.0
        closest = min(closest, math.hypot(x - a[0] - t * dx, y - a[1] - t * dy))
    return closest


def roof_surface(project: Any, oid: str, frame: Mapping[str, Any] | None = None,
                 top_to: Mapping[str, Any] | None = None) -> Callable[[float, float], float] | None:
    """Underside height relative to this wall's base, at local (along, across) metres.

    This is evaluated from the host roof's actual facets on every rebuild, not
    stored as a second stale representation. It supports both individual wall
    faces, and each material layer's distinct y interval.
    """
    meta = E.meta_of(project, oid)
    target = top_to or attachment(meta)
    if not target or target["kind"] != "roof":
        return None
    roof_id = str(target["roof"])
    if not E.alive(project, roof_id):
        return None                                  # the reconcile pass detaches a lost host
    facets = E.roof_facets(project, E.meta_of(project, roof_id))
    if not facets:
        raise BadRequest("The selected roof has no valid underside facets")
    fr = E.wall_frame(project, oid) if frame is None else frame
    cx, cy = fr["mid"]
    co, si = math.cos(fr["angle"]), math.sin(fr["angle"])
    base, offset = float(fr["base"]), float(target.get("offset", 0.0))
    reach = float(fr["thickness"]) / 2 + 1e-6

    def height(x: float, y: float) -> float:
        wx, wy = cx + co * x - si * y, cy + si * x + co * y
        located = [(i, plane) for i, (ring, plane) in enumerate(facets) if _inside(ring, wx, wy)]
        if not located:
            nearest = min(((_distance_to_outline(ring, wx, wy), i, plane) for i, (ring, plane) in enumerate(facets)), key=lambda q: (q[0], q[1]))
            if nearest[0] > reach:
                raise BadRequest("A face of the wall extends beyond the roof footprint; the roof cannot provide a continuous attachment",
                                 hint="Increase the roof overhang, move the wall under the roof, or choose another attachment face")
            plane = nearest[2]
        else:
            plane = located[0][1]
        return plane[0] * wx + plane[1] * wy + plane[2] - base + offset

    return height


def roof_breaks(project: Any, oid: str, faces: Sequence[float], frame: Mapping[str, Any] | None = None) -> list[float]:
    """Roof facet boundary locations along given material-layer faces, in wall-local metres.

    Splitting the top mesh at the crossings on both faces exposes any oblique
    facet crease as a diagonal of a top quad instead of approximating it with
    one plane. This shares the same roof facets as the height evaluator.
    """
    meta = E.meta_of(project, oid)
    target = attachment(meta)
    if not target or target["kind"] != "roof":
        return []
    roof_id = str(target["roof"])
    if not E.alive(project, roof_id):
        return []
    fr = E.wall_frame(project, oid) if frame is None else frame
    length = fr["length"]
    if length < 1e-9:
        return []
    start, end = fr["start"], fr["end"]
    c, s = math.cos(fr["angle"]), math.sin(fr["angle"])
    facets = E.roof_facets(project, E.meta_of(project, roof_id))
    out: set[float] = set()
    for y in faces:
        a, b = (start[0] - s * y, start[1] + c * y), (end[0] - s * y, end[1] + c * y)
        for ring, _ in facets:
            out.update(round(t * length - length / 2, 9) for t in _crossings(ring, a, b) if 1e-9 < t < 1 - 1e-9)
    return sorted(out)


def roof_line(project: Any, start: tuple[float, float], end: tuple[float, float], roof_meta: Mapping[str, Any]) -> list[tuple[float, float]]:
    """The roof's underside along a line in plan, as (distance from ``start``, absolute z) points. Refuses a line that is not wholly under the roof."""
    facets = E.roof_facets(project, roof_meta)
    length = math.dist(start, end)
    cuts = {0.0, 1.0}
    for ring, _ in facets:
        cuts.update(_crossings(ring, start, end))
    ts = sorted(cuts)
    pieces: list[tuple[float, float, tuple[float, float, float]]] = []
    outside = 0.0
    for t0, t1 in zip(ts, ts[1:]):
        if t1 - t0 < 1e-9:
            continue
        tm = (t0 + t1) / 2
        x, y = start[0] + (end[0] - start[0]) * tm, start[1] + (end[1] - start[1]) * tm
        plane = next((pl for ring, pl in facets if _inside(ring, x, y)), None)
        if plane is None:
            outside += (t1 - t0) * length
        else:
            pieces.append((t0, t1, plane))
    if outside > 1e-4:
        raise BadRequest(f"{outside:.2f} m of this wall is not under the roof, so its top has nothing to follow",
                         hint="Attach a wall that lies wholly under the roof, or enlarge the roof")
    pts: list[tuple[float, float]] = []
    for t0, t1, (a, b, c) in pieces:
        for t in (t0, t1):
            x, y = start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t
            pts.append((t * length, a * x + b * y + c))
    return pts


def _simplified(pts: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """Drop repeated and collinear points, so a flat top is two points and a gable's is three."""
    out: list[tuple[float, float]] = []
    for p in pts:
        if out and abs(p[0] - out[-1][0]) < 1e-9:
            out[-1] = p
            continue
        out.append(p)
    changed = True
    while changed and len(out) > 2:
        changed = False
        for i in range(1, len(out) - 1):
            (x0, z0), (x1, z1), (x2, z2) = out[i - 1], out[i], out[i + 1]
            if abs(z0 + (z2 - z0) * (x1 - x0) / (x2 - x0) - z1) < 1e-7:
                del out[i]
                changed = True
                break
    return out


# -------------------------------------------------------------------------------------------------------------------------- deriving a top
def derive(project: Any, oid: str, top_to: Mapping[str, Any]) -> tuple[float, list[list[float]] | None]:
    """(height, roof line) a wall must have to follow what it is attached to. The roof line is None when the top is level."""
    meta = E.meta_of(project, oid)
    base = E.level_elevation(project, E.m(meta, "level")) + float(E.m(meta, "base_offset", 0.0))
    offset = float(top_to.get("offset", 0.0))
    if top_to["kind"] == "level":
        elevation = E.level_elevation(project, str(top_to["level"]))              # a level that is gone raises NotFound: the wall is then detached
        height = elevation + offset - base
        if height < MIN_HEIGHT:
            raise BadRequest(f"The level is only {elevation - base:.3f} m above this wall's base: there is no wall to extend",
                             hint="Choose a level above the wall")
        return height, None
    roof = str(top_to["roof"])
    if not E.alive(project, roof) or E.m(E.meta_of(project, roof), "category") != "roof":
        raise NotFound("The roof this wall was attached to no longer exists")
    start, end = tuple(E.m(meta, "start")), tuple(E.m(meta, "end"))
    pts = _simplified([(s, z - base + offset) for s, z in roof_line(project, start, end, E.meta_of(project, roof))])
    low, high = min(h for _, h in pts), max(h for _, h in pts)
    # The two exposed faces of a wall can meet the underside at different
    # heights. Centre-line extrema alone leave a wedge of gap or penetration.
    fr = E.wall_frame(project, oid)
    surface = roof_surface(project, oid, fr, top_to)
    if surface is None:
        raise BadRequest("The roof attachment target is unavailable")
    length, half = fr["length"], fr["thickness"] / 2
    facets = E.roof_facets(project, E.meta_of(project, roof))
    co, si = math.cos(fr["angle"]), math.sin(fr["angle"])
    for y in (-half, half):
        a = (start[0] - si * y, start[1] + co * y)
        b = (end[0] - si * y, end[1] + co * y)
        positions = {0.0, 1.0}
        for ring, _ in facets:
            positions.update(_crossings(ring, a, b))
        for t in positions:
            h = surface(length * (t - 0.5), y)
            low, high = min(low, h), max(high, h)
    if low < MIN_HEIGHT:
        raise BadRequest(f"The roof comes down to {low + base - offset:.3f} m under this wall, which is {max(low, 0.0) * 1000:.0f} mm above its base: no wall can reach it",
                         hint="Move the roof up, or lower this wall's base")
    if high - low < EPS:
        return high, None
    return high, [[round(s, 9), round(h, 9)] for s, h in pts]


def _same(profile: Any, other: Any) -> bool:
    if not profile and not other:
        return True
    if not profile or not other or len(profile) != len(other):
        return False
    return all(abs(a[0] - b[0]) < EPS and abs(a[1] - b[1]) < EPS for a, b in zip(profile, other))


def refresh(project: Any, oid: str) -> bool:
    """Re-derive one attached wall's height and roof line and store them if they changed (the caller rebuilds). A wall whose roof is gone is detached.
    Returns whether anything was written."""
    meta = E.meta_of(project, oid)
    top_to = attachment(meta)
    if top_to is None:
        if E.m(meta, "top_profile"):                                              # a stray roof line on a free wall is dropped
            E._attach(project, oid, {"top_profile": None})
            return True
        return False
    try:
        height, profile = derive(project, oid, top_to)
    except NotFound:
        free = float(top_to.get("free_height") or E.m(meta, "height"))
        E._attach(project, oid, {"top_to": None, "top_profile": None, "height": free})
        return True
    if abs(height - float(E.m(meta, "height"))) < EPS and _same(profile, E.m(meta, "top_profile")):
        return False
    E._attach(project, oid, {"height": height, "top_profile": profile})
    return True


def attached_ids(project: Any) -> list[str]:
    """Every wall that follows something, read in one pass over the scene without copying any geometry."""
    scene = project.model._model
    with scene._mutation_lock:
        return [obj["id"] for obj in scene.objects if (obj.get("metadata") or {}).get(f"{E.NS}:top_to")]


def reconcile(project: Any) -> list[str]:
    """After a command: every attached wall follows its roof or level again. Returns the walls that changed."""
    ids = attached_ids(project)
    changed = [oid for oid in ids if refresh(project, oid)]
    for oid in changed:
        E.regenerate_wall(project, oid)
    return changed


# -------------------------------------------------------------------------------------------------------------------------------- commands
def _walls(project: Any, ids: Iterable[str]) -> list[str]:
    out = []
    for oid in ids:
        if not E.alive(project, oid):
            raise NotFound(f"Element {oid} does not exist")
        if E.m(E.meta_of(project, oid), "category") == "wall":
            out.append(oid)
    return out


def attach_to_roof(project: Any, ids: Sequence[str], roof: str | None = None, offset_mm: Any = 0) -> dict[str, Any]:
    """Attach the top of each wall in ``ids`` to a roof. ``roof`` may be left out when the ids hold exactly one roof."""
    ids = list(dict.fromkeys(ids))
    roofs = [i for i in ids if E.alive(project, i) and E.m(E.meta_of(project, i), "category") == "roof"]
    if roof is None:
        if len(roofs) != 1:
            raise BadRequest("Select the walls and one roof to attach them to" if not roofs else "More than one roof is selected: say which one the walls attach to")
        roof = roofs[0]
    elif not E.alive(project, roof) or E.m(E.meta_of(project, roof), "category") != "roof":
        raise BadRequest(f"{roof} is not a roof")
    walls = _walls(project, ids)
    if not walls:
        raise BadRequest("Select at least one wall to attach to the roof")
    offset = number(offset_mm, "offset", -100000, 100000) / 1000.0
    for oid in walls:
        meta = E.meta_of(project, oid)
        prior = attachment(meta)
        free = float(prior["free_height"]) if prior and prior.get("free_height") else float(E.m(meta, "height"))
        top_to = {"kind": "roof", "roof": roof, "offset": offset, "free_height": free}
        derive(project, oid, top_to)                                              # refuses a wall that is not under the roof before anything is written
        E._attach(project, oid, {"top_to": top_to})
        refresh(project, oid)
        E.regenerate_wall(project, oid)
    return {"walls": walls, "roof": roof}


def extend_to_level(project: Any, ids: Sequence[str], level: str | None = None, offset_mm: Any = 0) -> dict[str, Any]:
    """Extend each wall up to a level (by default the next one above the wall's own), so it follows that level's elevation."""
    walls = _walls(project, ids)
    if not walls:
        raise BadRequest("Select at least one wall to extend")
    offset = number(offset_mm, "offset", -100000, 100000) / 1000.0
    if level is not None:
        project.level(level)
    for oid in walls:
        meta = E.meta_of(project, oid)
        base = E.level_elevation(project, E.m(meta, "level")) + float(E.m(meta, "base_offset", 0.0))
        target = level
        if target is None:
            above = sorted((l for l in project.manifest["levels"] if float(l["elevation"]) > base + MIN_HEIGHT), key=lambda l: float(l["elevation"]))
            if not above:
                raise BadRequest("There is no level above this wall to extend it to", hint="Add a level in Levels first")
            target = above[0]["id"]
        prior = attachment(meta)
        free = float(prior["free_height"]) if prior and prior.get("free_height") else float(E.m(meta, "height"))
        top_to = {"kind": "level", "level": target, "offset": offset, "free_height": free}
        derive(project, oid, top_to)
        E._attach(project, oid, {"top_to": top_to})
        refresh(project, oid)
        E.regenerate_wall(project, oid)
    return {"walls": walls, "level": level}


def detach(project: Any, ids: Sequence[str], kind: str | None = None) -> dict[str, Any]:
    """Free the top of each wall (of the given kind, ``roof`` or ``level``, or either): it keeps the height it had before it was attached."""
    freed: list[str] = []
    for oid in _walls(project, ids):
        meta = E.meta_of(project, oid)
        top_to = attachment(meta)
        if top_to is None or (kind and top_to["kind"] != kind):
            continue
        free = float(top_to.get("free_height") or E.m(meta, "height"))
        E._attach(project, oid, {"top_to": None, "top_profile": None, "height": free})
        E.regenerate_wall(project, oid)
        freed.append(oid)
    if not freed:
        raise BadRequest("None of the selected walls is " + {"roof": "attached to a roof", "level": "extended to a level"}.get(kind or "", "attached to a roof or a level"))
    return {"walls": freed}


def describe(project: Any, meta: Mapping[str, Any]) -> str:
    top_to = attachment(meta)
    if top_to is None:
        return "Free (its own height)"
    off = float(top_to.get("offset", 0.0))
    tail = f", offset {off * 1000:g} mm" if abs(off) > 1e-9 else ""
    try:
        if top_to["kind"] == "roof":
            return f"Attached to roof {project.model.get(top_to['roof'])['name'] or top_to['roof']}{tail}"
        return f"Extended to level {project.level(top_to['level'])['name']}{tail}"
    except Exception:                                                  # a stale reference is shown as it is; reconcile detaches it
        return "Attached (target missing)"
