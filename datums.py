"""Datums: levels and grids.

* A **level** is a named elevation (metres). Levels live in the project manifest, because neither SGEO nor SDRW has a notion of one; every element
  refers to its level by id, and its geometry is regenerated from the level's elevation, so moving a level moves everything built on it.
* A **grid** is a named line in plan. Like a room it is an SGEO group object without geometry of its own (a datum has no volume, so it never
  reaches the quantities or the 3D view) whose ``spadra:`` metadata holds the two end points and where its bubbles are; it is drawn on every floor plan,
  and in a section or elevation wherever it is not parallel to the picture (``grid_marks``). It is one element, so a change to it (``edit_grid``: end
  points, length, angle, label, bubbles; or Move and Rotate) reaches every view at once and is one undo step. Labels are unique.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

from ..tools.errors import BadRequest, Conflict, NotFound
from ..tools.validation import new_id, number, text
from . import elements as E

MAX_LEVELS = 200
MAX_ELEVATION = 1000.0                   # metres, up or down: anything beyond is a typo, not a building
GRID_BUBBLE = 0.5                        # radius of the bubble drawn at a grid's end(s), in metres
BUBBLES = ("start", "end", "both", "none")   # which end(s) of a grid carry the bubble
MIN_GRID = 0.01                          # m: the shortest grid line
AWAY = 0.5                               # a grid shows in a section only if it is at least this far (sine of the angle) from parallel to the picture


# ----------------------------------------------------------------------------------------------------------------------- levels
def users_of(project: Any, level_id: str) -> list[str]:
    """Ids of the elements that stand on a level (including a stair that only *ends* on it)."""
    out = []
    for oid in E.element_ids(project):
        meta = project.model.get(oid)["metadata"]
        if E.m(meta, "level") == level_id or E.m(meta, "top_level") == level_id:
            out.append(oid)
    return out


def plan_view(project: Any, level_id: str) -> dict[str, Any] | None:
    return next((v for v in project.manifest["views"] if v["kind"] == "plan" and v.get("level_id") == level_id), None)


def _name(project: Any, value: Any, ignore: str | None = None) -> str:
    name = text(value, "name", 80)
    if any(l["name"].lower() == name.lower() and l["id"] != ignore for l in project.manifest["levels"]):
        raise Conflict(f"A level named '{name}' already exists", hint="Choose another name")
    return name


def _elevation(value: Any) -> float:
    return number(value, "elevation", -MAX_ELEVATION, MAX_ELEVATION)


def add_level(project: Any, p: Mapping[str, Any], new_drawing: Any) -> dict[str, Any]:
    """A new level and its floor plan. ``new_drawing(name)`` builds the plan's (empty) SDRW document."""
    if len(project.manifest["levels"]) >= MAX_LEVELS:
        raise BadRequest(f"A project holds at most {MAX_LEVELS} levels")
    name = _name(project, p.get("name"))
    level = {"id": new_id(), "name": name, "elevation": _elevation(p.get("elevation"))}
    project.manifest["levels"].append(level)
    project.manifest["levels"].sort(key=lambda l: l["elevation"])           # stable: levels at one height keep the order they were made in
    if p.get("plan", True):
        vid = new_id()
        taken = {v["name"].lower() for v in project.manifest["views"]}
        view_name = name if name.lower() not in taken else f"{name} (plan)"
        project.manifest["views"].append({"id": vid, "kind": "plan", "name": view_name, "level_id": level["id"]})
        project.drawings[vid] = new_drawing(view_name)
    return {"id": level["id"], "plan": plan_view(project, level["id"])["id"] if plan_view(project, level["id"]) else None}


def set_level(project: Any, p: Mapping[str, Any]) -> dict[str, Any]:
    """Rename a level and/or move it; everything standing on it is rebuilt, and the whole change is refused if some of it no longer fits."""
    level = project.level(text(p.get("id"), "id", 80))
    if "name" in p:
        new = _name(project, p["name"], ignore=level["id"])
        view = plan_view(project, level["id"])
        if view is not None and view["name"] == level["name"]:
            view["name"] = new                                               # the plan keeps following its level's name
            project.drawings[view["id"]].rename(new)
        level["name"] = new
    if "elevation" in p:
        level["elevation"] = _elevation(p["elevation"])
        project.manifest["levels"].sort(key=lambda l: l["elevation"])
        E.regenerate_all(project)
    if "name" not in p and "elevation" not in p:
        raise BadRequest("Nothing to change: give a name or an elevation")
    return {"id": level["id"]}


def delete_level(project: Any, p: Mapping[str, Any]) -> dict[str, Any]:
    level = project.level(text(p.get("id"), "id", 80))
    if len(project.manifest["levels"]) <= 1:
        raise Conflict("A project needs at least one level")
    users = users_of(project, level["id"])
    if users:
        raise Conflict(f"{len(users)} element{'s' if len(users) != 1 else ''} stand on {level['name']}", hint="Move or delete them first",
                       details={"elements": users[:50]})
    view = plan_view(project, level["id"])
    if view is not None:
        for sheet in project.manifest["sheets"]:
            if any(vp.get("view_id") == view["id"] for vp in sheet.get("viewports", [])):
                raise Conflict(f"The plan of {level['name']} is placed on sheet {sheet['number']}", hint="Remove its viewport from the sheet first")
        if project.drawings[view["id"]].count() > 0:
            raise Conflict(f"The plan of {level['name']} holds drawn items", hint="Delete them first so that nothing is lost")
        project.manifest["views"].remove(view)                              # its (empty) drawing stays in memory so that Undo brings the plan back
    project.manifest["levels"].remove(level)
    return {"removed": level["id"]}


# ------------------------------------------------------------------------------------------------------------------------ grids
def grids(project: Any) -> list[str]:
    return [oid for oid in E.element_ids(project) if E.category(project, oid) == "grid"]


def _unique_label(project: Any, label: str, ignore: str | None = None) -> str:
    label = text(label, "label", 20)
    for oid in grids(project):
        if oid != ignore and project.model.get(oid)["name"].lower() == label.lower():
            raise Conflict(f"A grid labelled '{label}' already exists")
    return label


def next_label(project: Any) -> str:
    taken = {project.model.get(o)["name"] for o in grids(project)}
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if letter not in taken:
            return letter
    n = 1
    while str(n) in taken:
        n += 1
    return str(n)


def add_grid(project: Any, p: Mapping[str, Any], to_m: float) -> str:
    """A grid line between two points (drawing units)."""
    ends = []
    for key in ("start", "end"):
        q = p.get(key)
        if not isinstance(q, (list, tuple)) or len(q) != 2:
            raise BadRequest(f"{key} must be a point [x, y]")
        ends.append([float(number(q[0], "x", -1e9, 1e9)) * to_m, float(number(q[1], "y", -1e9, 1e9)) * to_m])
    if math.dist(*ends) < 0.01:
        raise BadRequest("A grid line needs two points at least 10 mm apart")
    label = _unique_label(project, p["label"]) if p.get("label") else next_label(project)
    bubble = E._choice(p.get("bubble", "start"), BUBBLES, "bubble")
    oid = project.model.add_group(name=label)
    E._attach(project, oid, {"category": "grid", "type": "Grid", "start": ends[0], "end": ends[1], "bubble": bubble})
    return oid


def set_grid(project: Any, oid: str, values: dict[str, Any]) -> None:
    meta = E.meta_of(project, oid)
    start, end = list(values.get("start", E.m(meta, "start"))), list(values.get("end", E.m(meta, "end")))
    if math.dist(start, end) < 0.01:
        raise BadRequest("A grid line needs two points at least 10 mm apart")
    E._attach(project, oid, {"start": start, "end": end})


def bubbles_of(meta: Mapping[str, Any]) -> str:
    return str(E.m(meta, "bubble") or "start")                      # a grid made before bubbles could be chosen has its bubble at the start


def edit_grid(project: Any, oid: str, p: Mapping[str, Any], to_m: float) -> None:
    """Change a grid line in one step. ``start`` / ``end`` are points (drawing units); ``length`` (drawing units) and ``angle`` (degrees, counter-clockwise
    from +x) re-set the line from its start, or from its end with ``hold: "end"`` (``"middle"`` keeps the middle); ``label`` renames it; ``bubble`` says which
    end carries the bubble. Anything that would leave a grid shorter than 10 mm, or a duplicate label, is refused whole."""
    require_grid(project, oid)
    meta = E.meta_of(project, oid)
    start, end = list(E.m(meta, "start")), list(E.m(meta, "end"))
    changed = False
    for key, ref in (("start", start), ("end", end)):
        if p.get(key) is not None:
            q = p[key]
            if not isinstance(q, (list, tuple)) or len(q) != 2:
                raise BadRequest(f"{key} must be a point [x, y]")
            ref[:] = [float(number(q[0], "x", -1e9, 1e9)) * to_m, float(number(q[1], "y", -1e9, 1e9)) * to_m]
            changed = True
    if p.get("length") is not None or p.get("angle") is not None:
        if p.get("start") is not None and p.get("end") is not None:
            raise BadRequest("Give both ends, or a length and an angle, not both")
        hold = str(p.get("hold", "start"))
        if hold not in ("start", "end", "middle"):
            raise BadRequest("hold must be start, end or middle")
        current = math.dist(start, end)
        length = float(number(p["length"], "length", MIN_GRID / to_m, 1e9)) * to_m if p.get("length") is not None else current
        angle = math.radians(float(number(p["angle"], "angle", -360, 360))) if p.get("angle") is not None else math.atan2(end[1] - start[1], end[0] - start[0])
        d = (math.cos(angle), math.sin(angle))
        if hold == "start":
            end[:] = [start[0] + d[0] * length, start[1] + d[1] * length]
        elif hold == "end":
            start[:] = [end[0] - d[0] * length, end[1] - d[1] * length]
        else:
            mid = [(start[0] + end[0]) / 2, (start[1] + end[1]) / 2]
            start[:] = [mid[0] - d[0] * length / 2, mid[1] - d[1] * length / 2]
            end[:] = [mid[0] + d[0] * length / 2, mid[1] + d[1] * length / 2]
        changed = True
    values: dict[str, Any] = {}
    if changed:
        if math.dist(start, end) < MIN_GRID:
            raise BadRequest("A grid line needs two points at least 10 mm apart")
        values.update(start=start, end=end)
    if p.get("bubble") is not None:
        values["bubble"] = E._choice(p["bubble"], BUBBLES, "bubble")
    if p.get("label") is not None:
        label = _unique_label(project, p["label"], ignore=oid)
        if label != project.model.get(oid)["name"]:
            project.model.rename_object(oid, label)
    if not values and p.get("label") is None:
        raise BadRequest("Nothing to change: give an end, a length, an angle, a label or a bubble")
    if values:
        E._attach(project, oid, values)


def grid_item(project: Any, oid: str, f: float) -> dict[str, Any]:
    """Plan item of a grid: its line (as a two-point polyline for picking and snapping, and ``axis`` for the end grips), the bubble(s) and its label."""
    obj = project.model.get(oid)
    s, e = E.m(obj["metadata"], "start"), E.m(obj["metadata"], "end")
    where = bubbles_of(obj["metadata"])
    return {"id": oid, "category": "grid", "name": obj["name"], "polygon": [[s[0] * f, s[1] * f], [e[0] * f, e[1] * f]],
            "axis": {"a": [s[0] * f, s[1] * f], "b": [e[0] * f, e[1] * f], "thickness": 0.0},
            "label": {"name": obj["name"], "at": [s[0] * f, s[1] * f], "radius": GRID_BUBBLE * f, "bubbles": [k for k, on in (("start", where in ("start", "both")), ("end", where in ("end", "both"))) if on]}}


def grid_marks(project: Any, fr: Any, f: float) -> list[dict[str, Any]]:
    """The grids a section or elevation shows: each as a vertical line at the place along the picture where the grid meets it. A grid parallel to the picture
    (within ``AWAY``) is not shown. In a section that is where the grid crosses the cut plane, and only if that is on the cut line; in an elevation it is the
    end of the grid nearest the viewer."""
    out = []
    for oid in grids(project):
        meta = E.meta_of(project, oid)
        s, e = tuple(E.m(meta, "start")), tuple(E.m(meta, "end"))
        length = math.dist(s, e)
        ds, de = fr.depth((s[0], s[1], 0.0)), fr.depth((e[0], e[1], 0.0))
        if abs(de - ds) < AWAY * length:
            continue
        if fr.cut:
            t = (0.0 - ds) / (de - ds)
            if not 0.0 <= t <= 1.0:
                continue
            p = (s[0] + (e[0] - s[0]) * t, s[1] + (e[1] - s[1]) * t, 0.0)
            if not 0.0 <= fr.across(p) <= float(fr.span or 0.0):
                continue
        else:
            p = (s[0], s[1], 0.0) if ds < de else (e[0], e[1], 0.0)
        out.append({"id": oid, "name": project.model.get(oid)["name"], "x": round(fr.across(p) * f, 4)})
    return out


def grid_properties(project: Any, oid: str, out: dict[str, Any], volume: Mapping[str, Any] | None = None) -> dict[str, Any]:
    meta = project.model.get(oid)["metadata"]
    s, e = E.m(meta, "start"), E.m(meta, "end")
    mm = lambda v: round(v * 1000, 6)                                                    # noqa: E731
    out["top"] = [{"label": "Type", "type": "readonly", "value": "Grid"},
                  {"key": "name", "label": "Label", "type": "text", "value": project.model.get(oid)["name"]}]
    out["groups"] = [{"title": "Geometry", "rows": [
        {"key": "start_x", "label": "Start X", "type": "number", "unit": "mm", "value": mm(s[0])},
        {"key": "start_y", "label": "Start Y", "type": "number", "unit": "mm", "value": mm(s[1])},
        {"key": "end_x", "label": "End X", "type": "number", "unit": "mm", "value": mm(e[0])},
        {"key": "end_y", "label": "End Y", "type": "number", "unit": "mm", "value": mm(e[1])},
        {"key": "length", "label": "Length", "type": "number", "unit": "mm", "value": mm(math.dist(s, e))},
        {"key": "angle", "label": "Angle", "type": "number", "unit": "°", "value": round(math.degrees(math.atan2(e[1] - s[1], e[0] - s[0])), 6)},
        {"key": "bubble", "label": "Bubbles", "type": "choice", "value": bubbles_of(meta), "choices": [{"value": b, "label": b.title()} for b in BUBBLES]},
        *([dict(volume)] if volume else [])]},
        {"title": "Constraints", "rows": [{"label": "Extent", "type": "readonly", "value": "Every floor plan (a grid is not tied to a level)"}]}]
    return out


def require_grid(project: Any, oid: str) -> None:
    if E.category(project, oid) != "grid":
        raise NotFound(f"{oid} is not a grid")
