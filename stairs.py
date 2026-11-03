"""Straight stairs. A stair is stored as parameters only, like every other element; its solid (a monolithic flight whose treads and risers are
exact) is regenerated from them, and the plan symbol (outline, tread lines, the arrow up) is derived on every read.

A flight climbs from its base level to its top level (plus offsets), in ``risers`` equal risers. The first riser rises at the start point and the
last one lands on the top level, so a flight of ``n`` risers has ``n - 1`` treads and a run of ``(n - 1) * tread``. The only limits applied are
geometric ones (the flight must rise, and its riser and tread must be sensible lengths); building-code rules are not checked."""
from __future__ import annotations

import math
from typing import Any, Mapping

from ..tools.errors import BadRequest
from ..tools.validation import number, text
from . import elements as E
from . import geometry as G

PREFERRED_RISER = 0.175                  # metres: used only to propose a riser count when the person does not give one
RISER_RANGE = (0.05, 0.5)
TREAD_RANGE = (0.05, 2.0)
WIDTH_RANGE = (0.2, 20.0)
MAX_RISERS = 100


def frame(project: Any, oid: str) -> dict[str, Any]:
    """Everything about a flight's placement, in metres and radians."""
    meta = E.meta_of(project, oid)
    if E.m(meta, "category") != "stair":
        raise BadRequest("That element is not a stair")
    base = E.level_elevation(project, E.m(meta, "level")) + float(E.m(meta, "base_offset", 0.0))
    top = E.level_elevation(project, E.m(meta, "top_level")) + float(E.m(meta, "top_offset", 0.0))
    n, tread = int(E.m(meta, "risers")), float(E.m(meta, "tread"))
    return {"start": tuple(E.m(meta, "start")), "angle": float(E.m(meta, "angle")), "width": float(E.m(meta, "width")), "tread": tread, "risers": n,
            "base": base, "top": top, "height": top - base, "riser": (top - base) / n if n else 0.0, "run": (n - 1) * tread, "meta": meta}


def _validate(f: Mapping[str, Any]) -> None:
    if f["height"] <= 0.01:
        raise BadRequest("A stair must rise: its top has to be above its base")
    if not 2 <= f["risers"] <= MAX_RISERS:
        raise BadRequest(f"A stair has between 2 and {MAX_RISERS} risers")
    lo, hi = RISER_RANGE
    if not lo <= f["riser"] <= hi:
        raise BadRequest(f"{f['risers']} risers over {f['height']:.3f} m make each riser {f['riser'] * 1000:.0f} mm; they must be between {lo * 1000:.0f} and {hi * 1000:.0f} mm",
                         hint="Change the number of risers")
    lo, hi = TREAD_RANGE
    if not lo <= f["tread"] <= hi:
        raise BadRequest(f"The tread must be between {lo * 1000:.0f} and {hi * 1000:.0f} mm")
    lo, hi = WIDTH_RANGE
    if not lo <= f["width"] <= hi:
        raise BadRequest(f"The stair width must be between {lo * 1000:.0f} and {hi * 1000:.0f} mm")


def profile(f: Mapping[str, Any]) -> list[tuple[float, float]]:
    """The side of the flight as (distance along the run, height above the base)."""
    n, t, r = f["risers"], f["tread"], f["riser"]
    pts: list[tuple[float, float]] = [(0.0, 0.0)]
    for i in range(n - 1):
        pts += [(i * t, (i + 1) * r), ((i + 1) * t, (i + 1) * r)]
    pts.append(((n - 1) * t, 0.0))                  # the n-th riser is the step up onto the floor above, so the flight itself has n - 1 treads
    return pts


def regenerate(project: Any, oid: str) -> None:
    f = frame(project, oid)
    _validate(f)
    try:
        verts, faces = G.prism(profile(f), -f["width"] / 2, f["width"] / 2)                    # (s, z, t): along, up, across
    except G.GeometryError as exc:
        raise BadRequest(str(exc)) from exc
    c, s = math.cos(f["angle"]), math.sin(f["angle"])
    x0, y0 = f["start"]
    mapped = [[x0 + c * a - s * t, y0 + s * a + c * t, f["base"] + z] for a, z, t in verts]
    E._set_mesh(project, oid, E._positive_volume((mapped, faces)))


def add_stair(project: Any, p: Mapping[str, Any], to_m: float) -> str:
    level = project.level(text(p.get("level"), "level", 80))
    levels = sorted(project.manifest["levels"], key=lambda l: l["elevation"])
    if p.get("top_level"):
        top = project.level(text(p["top_level"], "top_level", 80))
    else:
        above = [l for l in levels if l["elevation"] > level["elevation"] + 1e-9]
        if not above:
            raise BadRequest("There is no level above this one for the stair to reach", hint="Add a level above it, or choose the top level")
        top = above[0]
    points = []
    for key in ("start", "end"):
        q = p.get(key)
        if not isinstance(q, (list, tuple)) or len(q) != 2:
            raise BadRequest(f"{key} must be a point [x, y]")
        points.append((float(number(q[0], "x", -1e9, 1e9)) * to_m, float(number(q[1], "y", -1e9, 1e9)) * to_m))
    run = math.dist(*points)
    if run < 0.05:
        raise BadRequest("Draw the run of the stair: its start and end points are too close together")
    height = float(top["elevation"]) - float(level["elevation"])
    if p.get("risers") is not None:
        n = int(number(p["risers"], "risers", 2, MAX_RISERS))
    else:
        n = max(2, min(MAX_RISERS, round(height / PREFERRED_RISER))) if height > 0 else 2
    tread = number(p["tread"], "tread", TREAD_RANGE[0] * 1000, TREAD_RANGE[1] * 1000) / 1000.0 if p.get("tread") is not None else run / (n - 1)
    material = E._choice(p.get("material", "Concrete"), E.MATERIALS, "material")
    oid = project.model.add_box(width=1.0, depth=1.0, height=1.0, name=text(p.get("name") or "Stair", "name", 80))
    E._attach(project, oid, {"category": "stair", "type": "Straight Stair", "level": level["id"], "top_level": top["id"], "start": list(points[0]),
                             "angle": math.atan2(points[1][1] - points[0][1], points[1][0] - points[0][0]), "width": number(p.get("width", 1000), "width", WIDTH_RANGE[0] * 1000, WIDTH_RANGE[1] * 1000) / 1000.0,
                             "tread": tread, "risers": n, "base_offset": 0.0, "top_offset": 0.0, "material": material})
    try:
        regenerate(project, oid)
    except BadRequest:
        project.model.remove(oid)
        raise
    return oid


def set_property(project: Any, meta: Mapping[str, Any], key: str, value: Any) -> dict[str, Any]:
    if key == "width":
        return {"width": number(value, key, WIDTH_RANGE[0] * 1000, WIDTH_RANGE[1] * 1000) / 1000.0}
    if key == "tread":
        return {"tread": number(value, key, TREAD_RANGE[0] * 1000, TREAD_RANGE[1] * 1000) / 1000.0}
    if key == "risers":
        n = number(value, key, 2, MAX_RISERS)
        if int(n) != n:
            raise BadRequest("The number of risers must be a whole number")
        return {"risers": int(n)}
    if key in ("base_offset", "top_offset"):
        return {key: number(value, key, -100000, 100000) / 1000.0}
    if key in ("level", "top_level"):
        return {key: project.level(text(value, key, 80))["id"]}
    if key == "material":
        return {"material": E._choice(value, E.MATERIALS, "material")}
    if key == "angle":
        return {"angle": math.radians(number(value, key, -360, 360))}
    if key in ("x", "y"):
        s = list(E.m(meta, "start"))
        s[0 if key == "x" else 1] = number(value, key, -1e6, 1e6) / 1000.0
        return {"start": s}
    raise BadRequest(f"'{key}' is not an editable stair property")


def plan_item(project: Any, oid: str, f: float) -> dict[str, Any]:
    fr = frame(project, oid)
    obj = project.model.get(oid)
    length = fr["run"]
    c, s = math.cos(fr["angle"]), math.sin(fr["angle"])
    x0, y0 = fr["start"]
    across = fr["width"] / 2
    at = lambda a, t: [(x0 + c * a - s * t) * f, (y0 + s * a + c * t) * f]                       # noqa: E731
    return {"id": oid, "category": "stair", "name": obj["name"], "polygon": E._rect(fr["start"], fr["angle"], 0.0, length, across, f),
            "lines": [[at(i * fr["tread"], -across), at(i * fr["tread"], across)] for i in range(1, fr["risers"] - 1)],
            "arrow": {"from": at(0.0, 0.0), "to": at(length, 0.0), "label": "UP"}}


def properties(project: Any, oid: str, out: dict[str, Any], volume: dict[str, Any]) -> dict[str, Any]:
    fr = frame(project, oid)
    meta = fr["meta"]
    levels = E._levels(project)
    mm = lambda v: round(v * 1000, 6)                                                            # noqa: E731
    out["top"] = [{"label": "Type", "type": "readonly", "value": E.m(meta, "type", "Straight Stair")},
                  {"key": "level", "label": "Base Level", "type": "choice", "value": E.m(meta, "level"), "choices": levels},
                  {"key": "top_level", "label": "Top Level", "type": "choice", "value": E.m(meta, "top_level"), "choices": levels},
                  {"key": "width", "label": "Width", "type": "number", "unit": "mm", "value": mm(fr["width"])},
                  {"key": "risers", "label": "Risers", "type": "number", "value": fr["risers"]},
                  {"key": "tread", "label": "Tread", "type": "number", "unit": "mm", "value": mm(fr["tread"])},
                  {"key": "material", "label": "Material", "type": "choice", "value": E.m(meta, "material", "Concrete"), "choices": E._choices(E.MATERIALS)}]
    out["groups"] = [
        {"title": "Geometry", "rows": [{"label": "Riser height", "type": "readonly", "value": f"{fr['riser'] * 1000:.1f} mm (rise ÷ risers)"},
                                        {"label": "Total rise", "type": "readonly", "value": f"{fr['height']:.3f} m"},
                                        {"label": "Run", "type": "readonly", "value": f"{fr['run']:.3f} m"},
                                        {"key": "angle", "label": "Direction", "type": "number", "unit": "°", "value": round(math.degrees(fr["angle"]), 6)},
                                        {"key": "x", "label": "Start X", "type": "number", "unit": "mm", "value": mm(fr["start"][0])},
                                        {"key": "y", "label": "Start Y", "type": "number", "unit": "mm", "value": mm(fr["start"][1])}, volume]},
        {"title": "Constraints", "rows": [{"key": "base_offset", "label": "Base Offset", "type": "number", "unit": "mm", "value": mm(float(E.m(meta, "base_offset", 0.0)))},
                                          {"key": "top_offset", "label": "Top Offset", "type": "number", "unit": "mm", "value": mm(float(E.m(meta, "top_offset", 0.0)))},
                                          {"label": "Limits", "type": "readonly", "value": f"riser {RISER_RANGE[0] * 1000:.0f}–{RISER_RANGE[1] * 1000:.0f} mm, tread {TREAD_RANGE[0] * 1000:.0f}–{TREAD_RANGE[1] * 1000:.0f} mm (geometric only)"}]}]
    return out
