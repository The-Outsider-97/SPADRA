"""Mirror Element: a reflected copy (or a reflection in place) of existing elements across a vertical plane.

This is the *geometric* mirror. It is not Mirror Properties (``property_transfer``), which leaves every target where it is and only reflects a configuration.

The plane
    A vertical plane through the line ``a`` → ``b`` in plan (metres). The model is Z-up, so a reflection changes x and y only.

Mirror by meaning, not by negating numbers
    A reflection turns a left-handed thing into a right-handed one. Each category is mirrored the way its own parameters work:

    * **Wall**: the reflected ends are *swapped*. The reflection of the wall's left side is then still its left side, so the exterior layer stays the
      exterior layer, near stays near, and a door's swing side stays the swing side. What changes is where along the wall things are: a hosted door or window
      moves to ``length - offset``, and a door that was hinged at the start is now hinged at the end. Doors and windows are copied with their wall.
    * **Floor, roof, room**: the outline is reflected and reversed, so it keeps its winding. A pitched roof is tried against the reflection of the original
      (ridge offset, which corner comes first) until its surface is exactly the reflected surface; its roof windows and per-eave end connections move to the
      matching eave. Gutters and roof accessories are not mirrored with it (that is reported).
    * **Column**: its point is reflected and its rotation becomes ``2·φ − rotation`` (φ is the direction of the plane).
    * **Beam, grid line**: both ends are reflected.
    * **Straight stair, family instance, extrusion and void**: reflected the same way, and then checked.

Checked, not assumed
    Wherever the shape could depend on handedness (a roof, a beam section, a family, a stair), the mirrored element's surface is compared with the reflection
    of the original's. If they are not the same shape (an L-section, a family with a handed part) the element is refused, and put back, with that reason.
    A wall is not checked this way, because a wall that meets another wall is joined to it, which changes its corners on purpose.

Each element stands alone: one that cannot be mirrored is reported and left as it was, and the others are still done. The copies get the links and groups the
originals have among themselves (``relationships.clone_structure``).
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from ..tools.errors import BadRequest, NotFound
from . import arrays as AY
from . import documentation as D
from . import elements as E
from . import relationships as RS

MAX_ELEMENTS = 300
TOLERANCE = 1e-3                                              # metres: two surfaces are the same shape when every vertex of one lies this near the other


class Plane:
    """A vertical mirror plane through a line in plan."""

    def __init__(self, a: Sequence[float], b: Sequence[float]) -> None:
        ax, ay, bx, by = float(a[0]), float(a[1]), float(b[0]), float(b[1])
        length = math.hypot(bx - ax, by - ay)
        if length < 1e-9:
            raise BadRequest("The mirror plane needs two different points")
        self.a = (ax, ay)
        self.u = ((bx - ax) / length, (by - ay) / length)
        self.phi = math.atan2(self.u[1], self.u[0])

    def point(self, q: Sequence[float]) -> list[float]:
        vx, vy = q[0] - self.a[0], q[1] - self.a[1]
        along = vx * self.u[0] + vy * self.u[1]
        return [self.a[0] + 2 * along * self.u[0] - vx, self.a[1] + 2 * along * self.u[1] - vy]

    def vertex(self, v: Sequence[float]) -> tuple[float, float, float]:
        x, y = self.point(v)
        return (x, y, float(v[2]))

    def angle(self, theta: float) -> float:
        return 2 * self.phi - float(theta)


# ------------------------------------------------------------------------------------------------------------------------- comparing shapes
def _bucket(p: Sequence[float]) -> tuple[int, int, int]:
    return (round(p[0] / TOLERANCE), round(p[1] / TOLERANCE), round(p[2] / TOLERANCE))


def _within(points: Sequence[Sequence[float]], others: Sequence[Sequence[float]]) -> bool:
    """Every point in ``points`` lies within a tolerance of some point of ``others``."""
    grid: dict[tuple[int, int, int], list[Sequence[float]]] = {}
    for q in others:
        grid.setdefault(_bucket(q), []).append(q)
    for p in points:
        i, j, k = _bucket(p)
        found = False
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for dk in (-1, 0, 1):
                    for q in grid.get((i + di, j + dj, k + dk), ()):
                        if abs(p[0] - q[0]) <= 1.5 * TOLERANCE and abs(p[1] - q[1]) <= 1.5 * TOLERANCE and abs(p[2] - q[2]) <= 1.5 * TOLERANCE:
                            found = True
                            break
                    if found:
                        break
                if found:
                    break
            if found:
                break
        if not found:
            return False
    return True


def _surface(project: Any, oid: str) -> list[tuple[float, float, float]]:
    try:
        return list(D.world_mesh(project, oid)[0])
    except Exception:                                                  # an element with no solid (a room, a grid line)
        return []


def _is_reflection(plane: Plane, original: Sequence[Sequence[float]], mirrored: Sequence[Sequence[float]]) -> bool:
    if not original and not mirrored:
        return True
    expected = [plane.vertex(v) for v in original]
    return _within(expected, mirrored) and _within(mirrored, expected)


# ------------------------------------------------------------------------------------------------------------------------- one element
class _Failed(Exception):
    """This element cannot be mirrored (the reason is the message)."""


def _flip_end(value: Any) -> Any:
    return {"start": "end", "end": "start"}.get(value, value)


def _wall(ctx: "_Context", oid: str, hosted: Sequence[str]) -> None:
    P = ctx.project
    meta = E.meta_of(P, oid)
    start, end = E.m(meta, "start"), E.m(meta, "end")
    length = math.dist(start, end)
    E._attach(P, oid, {"start": ctx.plane.point(end), "end": ctx.plane.point(start)})
    for hid in hosted:
        h = E.meta_of(P, hid)
        values: dict[str, Any] = {"offset": length - float(E.m(h, "offset"))}
        if E.m(h, "category") == "door":
            values["hinge"] = _flip_end(E.m(h, "hinge", "start"))
            if E.m(h, "panels"):
                values["panels"] = [{**q, "hinge": _flip_end(q.get("hinge", "start")), "slide": _flip_end(q.get("slide", "end"))} for q in reversed(list(E.m(h, "panels")))]
        E._attach(P, hid, values)
    E.regenerate_wall(P, oid)


def _outline_candidates(boundary: Sequence[Sequence[float]], plane: Plane) -> list[list[list[float]]]:
    """The reflected outline, reversed to keep its winding, starting at each corner in turn (a pitched roof reads its axes from the first edges)."""
    reflected = [plane.point(q) for q in reversed(list(boundary))]
    n = len(reflected)
    return [reflected[k:] + reflected[:k] for k in range(n if n == 4 else 1)]


def _edge_map(old: Sequence[Sequence[float]], new: Sequence[Sequence[float]], plane: Plane) -> dict[int, int]:
    """Which edge of the new outline is the reflection of each edge of the old one."""
    out: dict[int, int] = {}
    n = len(old)
    for i in range(n):
        a, b = plane.point(old[i]), plane.point(old[(i + 1) % n])
        for j in range(len(new)):
            c, d = new[j], new[(j + 1) % len(new)]
            if (math.dist(a, c) < 1e-6 and math.dist(b, d) < 1e-6) or (math.dist(a, d) < 1e-6 and math.dist(b, c) < 1e-6):
                out[i] = j
                break
    return out


def _windows_of(project: Any, roof: str) -> list[str]:
    return [o for o in E.element_ids(project) if E.category(project, o) == "roof_window" and E.m(E.meta_of(project, o), "host") == roof]


def _region(ctx: "_Context", oid: str, before: Sequence[Sequence[float]], windows: Sequence[str] = ()) -> None:
    """A floor, a roof or a room: its outline is reflected; a pitched roof is checked against the reflected original. ``windows`` are the roof's windows
    (already copied onto this element when it is a copy): each goes to the mirrored eave, measured from the other end where the eave runs the other way."""
    P = ctx.project
    meta = E.meta_of(P, oid)
    cat = E.m(meta, "category")
    old = [list(q) for q in E.m(meta, "boundary")]
    ridge = float(E.m(meta, "ridge_offset", 0.0) or 0.0)
    ends = E.m(meta, "roof_ends") if cat == "roof" else None
    ridges = [ridge] if abs(ridge) < 1e-12 or E.m(meta, "roof") != "gable" else [ridge, -ridge]
    placed = {w: (int(E.m(E.meta_of(P, w), "edge")), float(E.m(E.meta_of(P, w), "x"))) for w in windows}
    last = "its surface is not the reflection of the original"
    for boundary in _outline_candidates(old, ctx.plane):
        mapping = _edge_map(old, boundary, ctx.plane)
        for r in ridges:
            values: dict[str, Any] = {"boundary": boundary}
            if cat == "roof":
                values["ridge_offset"] = r
                if ends:
                    values["roof_ends"] = {str(mapping[int(k)]): _end_config(ctx, v) for k, v in ends.items() if int(k) in mapping}
            E._attach(P, oid, values)
            for w, (edge, x) in placed.items():
                new_edge = mapping.get(edge)
                if new_edge is None:
                    raise _Failed("one of its roof windows sits on an edge that has no mirrored edge")
                a, b = plane_point_pair(ctx.plane, old, edge)
                c, d = boundary[new_edge], boundary[(new_edge + 1) % len(boundary)]
                same_way = math.dist(a, c) < 1e-6
                E._attach(P, w, {"edge": new_edge, "x": x if same_way else math.dist(c, d) - x})
            try:
                E.regenerate(P, oid)
            except BadRequest:
                continue
            if cat == "room" or _is_reflection(ctx.plane, before, _surface(P, oid)):
                return
    raise _Failed(f"{last} for any placement of its outline and ridge")


def plane_point_pair(plane: Plane, boundary: Sequence[Sequence[float]], edge: int) -> tuple[list[float], list[float]]:
    n = len(boundary)
    return plane.point(boundary[edge]), plane.point(boundary[(edge + 1) % n])


def _end_config(ctx: "_Context", config: Mapping[str, Any]) -> dict[str, Any]:
    """An eave's end connection on the mirrored roof: tied to the mirrored wall when that was mirrored too, else to whichever wall is under the eave."""
    out = dict(config)
    wall = out.get("wall")
    if wall:
        if wall in ctx.mapping:
            out["wall"] = ctx.mapping[wall]
        else:
            out.pop("wall")
            ctx.note("an end connection was tied to a wall that was not mirrored: it now finds the wall under its eave")
    return out


def _column(ctx: "_Context", oid: str, before: Sequence[Sequence[float]]) -> None:
    meta = E.meta_of(ctx.project, oid)
    E._attach(ctx.project, oid, {"point": ctx.plane.point(E.m(meta, "point")), "rotation": ctx.plane.angle(float(E.m(meta, "rotation", 0.0)))})


def _line(ctx: "_Context", oid: str, before: Sequence[Sequence[float]]) -> None:
    meta = E.meta_of(ctx.project, oid)
    E._attach(ctx.project, oid, {"start": ctx.plane.point(E.m(meta, "start")), "end": ctx.plane.point(E.m(meta, "end"))})


def _stair(ctx: "_Context", oid: str, before: Sequence[Sequence[float]]) -> None:
    meta = E.meta_of(ctx.project, oid)
    E._attach(ctx.project, oid, {"start": ctx.plane.point(E.m(meta, "start")), "angle": ctx.plane.angle(float(E.m(meta, "angle", 0.0)))})


def _generic(ctx: "_Context", oid: str, before: Sequence[Sequence[float]]) -> None:
    if ctx.project.model.get(oid).get("family_id") is None:
        raise _Failed("it is generated by a procedural group: mirror the wall it follows")
    meta = E.meta_of(ctx.project, oid)
    if E.m(meta, "point") is None:
        raise _Failed("this family instance has no placement point to reflect")
    E._attach(ctx.project, oid, {"point": ctx.plane.point(E.m(meta, "point")), "rotation": ctx.plane.angle(float(E.m(meta, "rotation", 0.0)))})


def _form(ctx: "_Context", oid: str, before: Sequence[Sequence[float]]) -> None:
    meta = E.meta_of(ctx.project, oid)
    if E.m(meta, "category") == "sweep":
        raise _Failed("a sweep's path and profile are not mirrored yet")
    profile = E.m(meta, "profile")
    if not profile:
        raise _Failed("it has no outline to reflect")
    E._attach(ctx.project, oid, {"profile": [ctx.plane.point(q) for q in reversed(list(profile))]})


#: category -> (handler, does it need its surface compared with the reflection afterwards)
_HANDLERS: dict[str, tuple[Any, bool]] = {
    "floor": (_region, False), "roof": (_region, False), "room": (_region, False),
    "column": (_column, True), "beam": (_line, True), "grid": (_line, False),
    "stair": (_stair, True), "generic": (_generic, True), "extrusion": (_form, True), "void": (_form, True),
}

_WHY_NOT = {
    "door": "a door or window is mirrored with its wall: select the wall",
    "window": "a door or window is mirrored with its wall: select the wall",
    "roof_window": "a roof window is mirrored with its roof: select the roof",
    "gutter": "a gutter hangs on its roof's eave: select the roof",
    "downpipe": "a downpipe follows its gutter: select the roof",
    "flashing": "a drip edge lies on its roof: select the roof",
    "array": "an array has no place of its own: mirror its source, or release the array first",
    "opening_profile": "an opening's outline follows its door or window",
}


class _Context:
    def __init__(self, project: Any, plane: Plane, mapping: dict[str, str]) -> None:
        self.project, self.plane, self.mapping = project, plane, mapping
        self.notes: list[str] = []

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)


def _kind(project: Any, oid: str) -> str:
    """The category as the handlers know it: a structural element is a column or a beam."""
    cat = E.category(project, oid)
    if cat == "structural":
        return "column" if E.is_column(E.meta_of(project, oid)) else "beam"
    return cat


def refusal(project: Any, oid: str) -> str | None:
    """Why an element cannot be mirrored at all (None when it can be tried)."""
    cat = _kind(project, oid)
    if cat in _WHY_NOT:
        return _WHY_NOT[cat]
    if E.m(E.meta_of(project, oid), "roof_host"):
        return "an accessory follows its roof: select the roof"
    if cat not in _HANDLERS and cat != "wall":
        return f"a {cat} cannot be mirrored yet"
    if AY.of(project, oid):
        return "it is an item of an array: change the array, or make the item independent first"
    return None


def _dependants(project: Any, oid: str, cat: str) -> list[str]:
    """Elements that hang on this one and are not mirrored with it."""
    if cat != "roof":
        return []
    out = []
    for other in E.element_ids(project):
        meta = E.meta_of(project, other)
        if E.m(meta, "roof_host") == oid or (E.m(meta, "category") == "gutter" and oid in (E.m(meta, "host"), E.m(meta, "roof"))):
            out.append(other)
    return out


def _run(ctx: "_Context", source: str, target: str, hosted_copies: Sequence[str]) -> None:
    P = ctx.project
    cat = _kind(P, source)
    before = _surface(P, source) if cat != "wall" else []
    if cat == "wall":
        _wall(ctx, target, hosted_copies)
        return
    handler, check = _HANDLERS[cat]
    if cat == "roof":
        copies = []
        for w in _windows_of(P, source):
            if target == source:
                copies.append(w)
            else:
                new = P.model.duplicate(w, with_children=False, name_suffix="")[w]
                E._attach(P, new, {"host": target})
                copies.append(new)
        _region(ctx, target, before, copies)
        return
    handler(ctx, target, before)
    if cat in ("floor", "room"):
        return                                                              # _region already compared its candidates
    E.regenerate(P, target)
    if check and not _is_reflection(ctx.plane, before, _surface(P, target)):
        raise _Failed("its mirror image is not the same shape as it (its section or parts are handed), so it cannot be a mirrored copy")


def mirror(project: Any, ids: Sequence[str], a: Sequence[float], b: Sequence[float], *, keep: bool = True, follow: bool = True, groups: bool = True) -> dict[str, Any]:
    """Mirror elements across the vertical plane through the plan line ``a`` → ``b`` (metres). ``keep`` makes mirrored copies; otherwise the elements
    themselves are reflected (and so are the elements that follow them through a link, with ``follow``). Returns what was done, skipped and failed."""
    chosen = list(dict.fromkeys(ids or ()))
    if not chosen or len(chosen) > MAX_ELEMENTS:
        raise BadRequest(f"Select between 1 and {MAX_ELEMENTS} elements to mirror")
    for oid in chosen:
        if not E.alive(project, oid):
            raise NotFound(f"Element {oid} does not exist")
    plane = Plane(a, b)
    order = RS.expand(project, chosen, groups=groups, links=follow and not keep)
    in_set = set(order)
    todo = [o for o in order if not (E.category(project, o) in E.OPENINGS and E.m(E.meta_of(project, o), "host") in in_set)]      # they go with their wall
    todo.sort(key=lambda o: 0 if E.category(project, o) == "wall" else 1)                                                             # walls first: a roof's end connections follow them
    names = {o: _name(project, o) for o in todo}
    mapping: dict[str, str] = {}
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []
    for oid in todo:
        why = refusal(project, oid)
        if why:
            skipped.append({"id": oid, "name": names[oid], "reason": why})
            continue
        saved = (project.model.copy(fresh_id=False), _clone(project.manifest))
        ctx = _Context(project, plane, mapping)
        try:
            cat = _kind(project, oid)
            hosted = E.hosted(project, oid) if cat == "wall" else []
            if keep:
                target, made = AY._copy(project, oid, " (mirrored)")
                hosted_copies = made[1:]
            else:
                target, hosted_copies = oid, hosted
            _run(ctx, oid, target, hosted_copies)
            if _dependants(project, oid, cat):
                ctx.note("its gutters and roof accessories were not mirrored with it")
        except (_Failed, BadRequest, NotFound) as exc:
            project.model, project.manifest = saved[0], saved[1]
            project._cache.clear()
            reason = str(exc)
            failed.append({"id": oid, "name": names[oid], "reason": reason[0].upper() + reason[1:] if reason else "It could not be mirrored"})
            continue
        mapping[oid] = target
        applied.append({"id": oid, "copy": target if keep else None, "name": names[oid], "category": _kind(project, oid), "notes": ctx.notes})
    structure = RS.clone_structure(project, mapping) if keep and mapping else {"links": 0, "groups": []}
    if not applied:
        detail = "; ".join(f"{r['name']}: {r['reason']}" for r in (failed + skipped)[:4])
        raise BadRequest("Nothing could be mirrored" + (f" ({detail})" if detail else ""))
    return {"mode": "copy" if keep else "move", "ids": [m for m in mapping.values()] if keep else list(mapping), "applied": applied, "skipped": skipped, "failed": failed,
            "structure": structure}


def _name(project: Any, oid: str) -> str:
    try:
        return project.model.get(oid)["name"] or E.category(project, oid).title()
    except Exception:
        return oid


def _clone(value: Any) -> Any:
    import copy
    return copy.deepcopy(value)
