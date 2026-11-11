"""Hinged-door swing: a door's opening motion worked out from the door itself (its hinge side, swing side and leaf), with a swept-leaf clearance check.

A door's object origin is its hinge axis, in the middle of the leaf's thickness on its hinge edge wherever the leaf stands across the wall (see ``elements.leaf_layout`` and
``_place_opening``), so a turn about that origin is the leaf swinging on its hinge, and the plan's swing arc starts at the same point. This module only
decides *which way and how far*: the sign of the turn comes from the hinge edge and the swing side, and the clearance check sweeps the leaf's plan footprint
through the angle against the bounding boxes of the other elements at the same height. The motion itself is written by the ordinary ``spin`` animation preset
(two keys, undoable, evaluated by SGEO); nothing here animates.

What it does not do: doors with several panels (their origin is the start edge, not a hinge), windows, and obstacles that are not boxes or meshes (they are
counted and reported as skipped, not silently ignored). Obstacles are tested by their axis-aligned bounding boxes, which is conservative for diagonal walls.
The keys are fixed values: editing the door's width or hinge afterwards does not re-aim an animation that was already made; run the command again.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from ..tools.errors import BadRequest
from . import elements as E
from .stairs import _world_triangles

MAX_DEGREES = 180.0
STEP_DEGREES = 1.0
Z_TOLERANCE = 0.002                        # m: floors under the leaf and lintels above it touch it without being in its way
PENETRATION = 0.005                        # m: the leaf must enter another element by more than this to count, as in the model's own clash check (checks.PENETRATION);
#                                            the leaf's corner passes a jamb or a frame by a few millimetres as it turns about its hinge


def turn_sign(hinge: str, swing: int) -> int:
    """+1 when a counter-clockwise turn (seen from above, in the wall's own frame) opens the door toward its swing side.

    The leaf lies along +x from a start hinge and along -x from an end hinge; ``swing`` is +1 for the wall's +y side and -1 for the other, which is the face the
    plan's swing arc is drawn from. A start-hinged leaf turned counter-clockwise moves its free edge to +y; an end-hinged leaf, to -y."""
    return (1 if swing >= 0 else -1) * (1 if hinge == "start" else -1)


def _hull(points: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    pts = sorted(set(points))
    if len(pts) < 3:
        return list(pts)

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower: list[tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _depth(a: Sequence[tuple[float, float]], b: Sequence[tuple[float, float]]) -> float:
    """How far two convex polygons interpenetrate: the smallest overlap over all separating axes (zero or negative when they are apart)."""
    best = math.inf
    for poly in (a, b):
        for i in range(len(poly)):
            x1, y1 = poly[i]
            x2, y2 = poly[(i + 1) % len(poly)]
            nx, ny = y1 - y2, x2 - x1
            n = math.hypot(nx, ny)
            if n < 1e-12:
                continue
            pa = [(x * nx + y * ny) / n for x, y in a]
            pb = [(x * nx + y * ny) / n for x, y in b]
            best = min(best, min(max(pa), max(pb)) - max(min(pa), min(pb)))
    return best


def _overlap(a: Sequence[tuple[float, float]], b: Sequence[tuple[float, float]], tolerance: float = 0.0) -> bool:
    """True when the polygons interpenetrate by more than ``tolerance`` (touching is not overlapping)."""
    return _depth(a, b) > tolerance + 1e-9


def _rotate(points: Sequence[tuple[float, float]], about: tuple[float, float], degrees: float) -> list[tuple[float, float]]:
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return [(about[0] + c * (x - about[0]) - s * (y - about[1]), about[1] + s * (x - about[0]) + c * (y - about[1])) for x, y in points]


def sweep(leaf: Sequence[tuple[float, float]], hinge: tuple[float, float], turn: float, obstacles: Mapping[str, Sequence[tuple[float, float]]], *,
          step: float = STEP_DEGREES, tolerance: float = 0.0) -> dict[str, Any]:
    """Turn the leaf's plan footprint about the hinge from 0 to ``turn`` degrees (either sign) in ``step`` increments. Returns the largest angle reached before
    the first contact (``clear_to``, in degrees, with the sign of ``turn``) and what it touched there. Pure geometry, no model."""
    total = abs(turn)
    sign = 1 if turn >= 0 else -1
    n = max(1, int(math.ceil(total / step)))
    clear = 0.0
    for i in range(1, n + 1):
        angle = min(total, i * step)
        poly = _hull(_rotate(leaf, hinge, sign * angle))
        hit = sorted(oid for oid, box in obstacles.items() if _overlap(poly, box, tolerance))
        if hit:
            return {"clear_to": sign * clear, "obstructed_at": sign * angle, "obstructions": hit}
        clear = angle
    return {"clear_to": sign * clear, "obstructed_at": None, "obstructions": []}


def contacts(leaf: Sequence[tuple[float, float]], hinge: tuple[float, float], turn: float, obstacles: Mapping[str, Sequence[tuple[float, float]]], *,
             step: float = STEP_DEGREES) -> dict[str, float]:
    """The deepest the swept leaf enters each obstacle over the whole swing, in metres, whatever any tolerance; elements it never enters are left out."""
    sign = 1 if turn >= 0 else -1
    deepest: dict[str, float] = {}
    for i in range(1, max(1, int(math.ceil(abs(turn) / step))) + 1):
        poly = _hull(_rotate(leaf, hinge, sign * min(abs(turn), i * step)))
        for oid, box in obstacles.items():
            d = _depth(poly, box)
            if d > 1e-9 and d > deepest.get(oid, 0.0):
                deepest[oid] = d
    return deepest


def _box_xy(tris: Any) -> tuple[list[tuple[float, float]], float, float]:
    xs = [v[0] for t in tris for v in t]
    ys = [v[1] for t in tris for v in t]
    zs = [v[2] for t in tris for v in t]
    return [(min(xs), min(ys)), (max(xs), min(ys)), (max(xs), max(ys)), (min(xs), max(ys))], min(zs), max(zs)


def plan(project: Any, door_id: str, degrees: Any = None, *, check: bool = True, tolerance_mm: Any = PENETRATION * 1000) -> dict[str, Any]:
    """What a swing of ``degrees`` would do for this door: the signed turn to key, and (with ``check``) how far the leaf can open before it enters something by more
    than ``tolerance_mm``. ``contacts_mm`` lists every element the leaf enters at all and how deep, so what was tolerated is visible."""
    if isinstance(tolerance_mm, bool) or not isinstance(tolerance_mm, (int, float)) or not math.isfinite(tolerance_mm) or not 0 <= tolerance_mm <= 50:
        raise BadRequest("tolerance_mm must be between 0 and 50")
    if degrees is None:                                                  # no angle asked for: the door's own opening angle
        degrees = float(E.m(E.meta_of(project, door_id), "opening_angle", E.DEFAULT_OPENING_ANGLE))
    if isinstance(degrees, bool) or not isinstance(degrees, (int, float)) or not math.isfinite(degrees) or not 0 < degrees <= MAX_DEGREES:
        raise BadRequest(f"The opening angle must be above 0 and at most {MAX_DEGREES:g} degrees")
    meta = E.meta_of(project, door_id)
    if E.m(meta, "category") != "door":
        raise BadRequest("Only a door can be given a hinged swing", hint="Use the Turn track for other elements")
    if E.has_panels(meta):
        raise BadRequest("This door has panels defined; a swing of the whole door is not meaningful and per-panel swings are not supported yet",
                         hint="Remove the panel layout, or animate a single-leaf door")
    hinge = str(E.m(meta, "hinge", "start"))
    swing = int(E.m(meta, "swing", 1))
    turn = turn_sign(hinge, swing) * float(degrees)
    out: dict[str, Any] = {"door": door_id, "hinge": hinge, "swing": "left" if swing >= 0 else "right", "requested_degrees": float(degrees), "turn_degrees": turn,
                           "checked": False, "clear_to_degrees": None, "obstructions": [], "skipped": 0}
    if not check:
        return out
    tris = _world_triangles(project, door_id)
    if not tris:
        raise BadRequest("The door has no geometry to sweep")
    leaf_xy, z0, z1 = _box_xy(tris)
    t = project.model.get(door_id).get("transform") or E.IDENTITY
    origin = (t[3], t[7])
    leaf_pts = [(v[0], v[1]) for tri in tris for v in tri]
    host = E.m(meta, "host")
    skip = {door_id, host}
    obstacles: dict[str, list[tuple[float, float]]] = {}
    skipped = 0
    for oid in E.all_ids(project.model, has_geometry=True):
        if oid in skip:
            continue
        om = E.meta_of(project, oid)
        if E.m(om, "host") == host and host is not None:               # other openings in the same wall lie in its plane
            continue
        if E.m(om, "category") == "opening_profile":
            continue
        ot = _world_triangles(project, oid)
        if ot is None:
            skipped += 1
            continue
        box, oz0, oz1 = _box_xy(ot)
        if oz1 <= z0 + Z_TOLERANCE or oz0 >= z1 - Z_TOLERANCE:
            continue
        obstacles[oid] = box
    tol = float(tolerance_mm) / 1000.0
    res = sweep(leaf_pts, origin, turn, obstacles, tolerance=tol)
    deep = contacts(leaf_pts, origin, turn, obstacles)
    out.update({"checked": True, "clear_to_degrees": res["clear_to"], "obstructed_at_degrees": res["obstructed_at"], "obstructions": res["obstructions"], "skipped": skipped,
                "tolerance_mm": float(tolerance_mm), "contacts_mm": {k: round(v * 1000, 2) for k, v in sorted(deep.items())},
                "method": "leaf plan footprint swept in 1 degree steps against axis-aligned bounding boxes of elements at the same height; "
                          f"an overlap of {float(tolerance_mm):g} mm or less is not counted"})
    return out


# ------------------------------------------------------------------------------------------------------------------ the motion law (report 8.5)
def smoothstep(u: float) -> float:
    """3u^2 - 2u^3 for u clamped to [0, 1]: zero slope at both ends, monotone, 0 -> 1."""
    u = min(1.0, max(0.0, float(u)))
    return u * u * (3.0 - 2.0 * u)


def eased_angle(t: float, start: float, end: float, theta_max: float) -> float:
    """theta(t) = theta_max * (3u^2 - 2u^3), u = clamp((t - start) / (end - start), 0, 1): the kinematic law the swing is authored with. Not a torque-driven simulation."""
    if end <= start:
        raise BadRequest("The motion needs to end after it starts")
    return float(theta_max) * smoothstep((t - start) / (end - start))


def _turn_track(project: Any, door_id: str) -> tuple[float, float, float, float]:
    """(start, end, first, last) of the door's turn track: key times in seconds and key values in degrees."""
    from . import animation_authoring as AA
    spec = project.model.animation()
    tracks = [t for t in (spec or {}).get("tracks", []) if t["object_id"] == door_id and AA.ui_property(t["property"]) == "turn"]
    if not tracks or len(tracks[0]["keyframes"]) < 2:
        raise BadRequest("This door has no swing animation to verify", hint="Run anim.swing_door first")
    keys = sorted(tracks[0]["keyframes"], key=lambda k: k["time"])
    first = 0
    while first + 1 < len(keys) - 1 and abs(keys[first + 1]["value"] - keys[0]["value"]) < 1e-12:      # a hold key at 0 s before a motion that starts later is not part of the motion
        first += 1
    return keys[first]["time"], keys[-1]["time"], float(AA.ui_value("turn", keys[0]["value"])), float(AA.ui_value("turn", keys[-1]["value"]))


def verify_motion(project: Any, door_id: str, *, samples: int = 49, tolerance_deg: float = 1e-6, pivot_tolerance_m: float = 1e-9) -> dict[str, Any]:
    """Sample SGEO's evaluated animation of a swung door and check what the report asks of a kinematic preview: the angle follows the cubic smoothstep law, it is
    monotone, stays within [0, theta_max], starts and ends where the keys say, and the hinge axis does not move in world coordinates (the transform chain turns the
    leaf about its own origin). Read-only. This is kinematics: it says nothing about forces, closing time or clearance (see ``plan`` for the swept-leaf check)."""
    import math as _m
    from . import animation_bridge as AB
    if isinstance(samples, bool) or not isinstance(samples, int) or not 3 <= samples <= 1000:
        raise BadRequest("samples must be a whole number from 3 to 1000")
    start, end, first, last = _turn_track(project, door_id)
    theta_max = last - first
    base = project.model.world_transform(door_id)
    times = sorted({start + (end - start) * i / (samples - 1) for i in range(samples)} | {start, end})
    rows, worst, pivot_drift = [], 0.0, 0.0
    for t in times:
        state = AB.BRIDGE.evaluate(project.model, t).get_object_state(door_id)
        wt = state["world_transform"]
        angle = _m.degrees(_m.atan2(wt[4], wt[0])) - _m.degrees(_m.atan2(base[4], base[0]))
        angle = (angle + 180.0) % 360.0 - 180.0 if abs(theta_max) < 180 else angle
        expected = first + eased_angle(t, start, end, theta_max)
        worst = max(worst, abs(angle - expected))
        pivot_drift = max(pivot_drift, _m.dist((wt[3], wt[7], wt[11]), (base[3], base[7], base[11])))
        rows.append((t, angle, expected))
    sign = 1.0 if theta_max >= 0 else -1.0
    steps = [sign * (b[1] - a[1]) for a, b in zip(rows, rows[1:])]
    low, high = sorted((first, last))
    checks = {"follows_smoothstep_law": worst <= tolerance_deg, "monotone": all(s >= -tolerance_deg for s in steps),
              "within_angle_bounds": all(low - tolerance_deg <= r[1] <= high + tolerance_deg for r in rows),
              "starts_at_first_key": abs(rows[0][1] - first) <= tolerance_deg, "ends_at_last_key": abs(rows[-1][1] - last) <= tolerance_deg,
              "hinge_axis_fixed_in_world": pivot_drift <= pivot_tolerance_m}
    return {"door": door_id, "ok": all(checks.values()), "checks": checks, "law": "theta(t) = theta_max (3u^2 - 2u^3), u = clamp((t - start) / (end - start), 0, 1)",
            "start_s": start, "end_s": end, "theta_max_deg": theta_max, "samples": len(rows), "max_angle_error_deg": worst, "max_hinge_drift_m": pivot_drift,
            "scope": "kinematic prescription evaluated by SGEO; not a physical simulation, and not a clearance check"}


def hinge_position(project: Any, door_id: str) -> tuple[float, float]:
    """Where the door's hinge axis is, in plan (m): the object's origin."""
    if E.m(E.meta_of(project, door_id), "category") != "door":
        raise BadRequest("Only a door has a hinge")
    t = project.model.world_transform(door_id)
    return (t[3], t[7])


def resize_hinged(project: Any, door_id: str, width_mm: Any, anchor: str = "hinge") -> dict[str, Any]:
    """Change a door's width keeping its hinge where it is in the world (``anchor="hinge"``, the default here) or its centre where it is (``"centre"``, what a plain width
    edit does). With the hinge kept, the free edge moves and the leaf is rebuilt about the same axis, so an animation that turns about the hinge stays on its pivot.
    The edit is one transaction: if the new size does not fit in the wall the door is left as it was."""
    from ..tools.validation import number
    meta = E.meta_of(project, door_id)
    if E.m(meta, "category") != "door":
        raise BadRequest("Only a door can be resized about its hinge")
    if anchor not in ("hinge", "centre"):
        raise BadRequest("anchor must be 'hinge' or 'centre'")
    new_w = number(width_mm, "width", 100, 20000) / 1000.0
    old_w, offset = float(E.m(meta, "width")), float(E.m(meta, "offset"))
    before = hinge_position(project, door_id)
    if anchor == "hinge":
        x0, x1 = offset - old_w / 2, offset + old_w / 2
        new_offset = x0 + new_w / 2 if E.m(meta, "hinge", "start") == "start" else x1 - new_w / 2
    else:
        new_offset = offset
    order = (("width", "offset"), ("offset", "width")) if new_w >= old_w else (("offset", "width"), ("width", "offset"))
    values = {"width": width_mm, "offset": new_offset}
    last: Exception | None = None
    for attempt in order:                                                              # either order reaches the same final state; one may pass a transient check
        try:
            for key in attempt:
                E.set_property(project, door_id, key, values[key])
            break
        except BadRequest as exc:
            last = exc
    else:
        raise last if last else BadRequest("The door does not fit")
    after = hinge_position(project, door_id)
    return {"door": door_id, "anchor": anchor, "width": new_w, "offset": new_offset, "hinge_before": list(before), "hinge_after": list(after),
            "hinge_moved_m": math.dist(before, after)}
