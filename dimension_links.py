"""Dimensions that stay on the wall faces they were placed on.

An aligned dimension in a floor plan is two points. When a point is placed on a wall's face (or on its centre line) the dimension remembers
*which wall, how far along it and which face*, not just where that was. Whenever a command changes the model - a layer added to the wall, the
wall made thicker, stretched or moved - the dimension is brought up to date inside that same command, so the number it shows is the number the
plan shows and one Undo puts the wall and its dimension back together.

* A point that is not on a wall stays exactly where the person put it. Nothing is ever linked by guesswork: a point is linked only when it lies
  on a face or centre line to within :data:`TOLERANCE_MM`.
* A person who drags a dimension's grip away from the wall has changed the point: the link is let go (and made again only if it lands on a wall).
* If the wall is deleted the dimension keeps its last position and is no longer linked.
* The dimension line keeps its distance: it moves by the average of what its two points moved.
"""
from __future__ import annotations

import math
from typing import Any

from . import elements as E

KEY = "spadra:anchors"
TOLERANCE_MM = 1.0                           # how close to a face a point must be to count as on it


def _unit_mm(project: Any) -> float:
    from .project import MM_PER
    return MM_PER[project.manifest["unit"]]


def _frames(project: Any, view_id: str) -> dict[str, dict[str, Any]]:
    """Every wall in this plan: its axis, unit vectors and the offsets of its two faces from the axis (drawing units)."""
    out: dict[str, dict[str, Any]] = {}
    for item in project.plan_overlay(view_id):
        axis = item.get("axis")
        if item.get("category") != "wall" or not axis:
            continue
        a, b = axis["a"], axis["b"]
        length = math.dist(a, b)
        if length < 1e-9:
            continue
        ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
        nx, ny = -uy, ux
        polygon = item.get("polygon") or []
        offsets = [(q[0] - a[0]) * nx + (q[1] - a[1]) * ny for q in polygon] or [axis["thickness"] / 2, -axis["thickness"] / 2]
        out[item["id"]] = {"a": a, "u": (ux, uy), "n": (nx, ny), "length": length, "hi": max(offsets), "lo": min(offsets),
                           "thickness": float(axis["thickness"]), "polygon": polygon}
    return out


def _anchor_for(point: list[float], walls: dict[str, dict[str, Any]], tol: float) -> dict[str, Any] | None:
    """The wall face or centre line this point lies on, or None."""
    best: tuple[float, dict[str, Any]] | None = None
    for wid, w in walls.items():
        dx, dy = point[0] - w["a"][0], point[1] - w["a"][1]
        along = dx * w["u"][0] + dy * w["u"][1]
        off = dx * w["n"][0] + dy * w["n"][1]
        if not -w["thickness"] <= along <= w["length"] + w["thickness"]:         # a mitred corner lies a little beyond the centre line's end
            continue
        for face, wanted in ((1, w["hi"]), (-1, w["lo"]), (0, 0.0)):
            gap = abs(off - wanted)
            if gap <= tol and (best is None or gap < best[0]):
                best = (gap, {"wall": wid, "t": along / w["length"], "face": face, "at": [point[0], point[1]]})
    return best[1] if best else None


def _resolve(anchor: dict[str, Any], walls: dict[str, dict[str, Any]], tol: float) -> list[float] | None:
    """Where the anchored point is now, or None when its wall is gone."""
    w = walls.get(anchor["wall"])
    if w is None:
        return None
    off = w["hi"] if anchor["face"] > 0 else w["lo"] if anchor["face"] < 0 else 0.0
    t = float(anchor["t"]) * w["length"]
    target = [w["a"][0] + w["u"][0] * t + w["n"][0] * off, w["a"][1] + w["u"][1] * t + w["n"][1] * off]
    # At a joined corner the face ends where the walls meet: take the polygon's own corner when one is close.
    near = [q for q in w["polygon"] if math.dist(q, target) <= 0.75 * w["thickness"]
            and abs((q[0] - w["a"][0]) * w["n"][0] + (q[1] - w["a"][1]) * w["n"][1] - off) <= tol]
    if near:
        return list(min(near, key=lambda q: math.dist(q, target)))
    return target


def _dimensions(doc: Any) -> list[str]:
    return [eid for eid in E.all_ids(doc, kind="dimension") if doc.get(eid)["geometry"].get("dim_type") == "aligned"]


def _plans_with_dimensions(project: Any) -> list[tuple[str, Any, list[str]]]:
    out = []
    live = {v["id"]: v for v in project.manifest["views"]}
    for vid, doc in project.drawings.items():
        view = live.get(vid)
        if view is None or view["kind"] != "plan" or not view.get("level_id"):
            continue
        ids = _dimensions(doc)
        if ids:
            out.append((vid, doc, ids))
    return out


def sync(project: Any) -> int:
    """Link dimension points that lie on a wall face (those not linked yet) and move the linked ones with their faces.
    Returns how many dimensions changed. Called inside the command that changed the model."""
    return _run(project, move=True)


def bind(project: Any) -> int:
    """Only link: used before a command changes the model, so that dimensions drawn earlier follow the change that command makes."""
    return _run(project, move=False)


def _run(project: Any, move: bool) -> int:
    changed = 0
    tol = TOLERANCE_MM / _unit_mm(project)
    for vid, doc, ids in _plans_with_dimensions(project):
        walls = _frames(project, vid)
        for eid in ids:
            ent = doc.get(eid)
            g = dict(ent["geometry"])
            stored = ent.get("metadata", {}).get(KEY)
            anchors = list(stored) if isinstance(stored, list) and len(stored) == 2 else [None, None]
            old_points = [list(g["p1"]), list(g["p2"])]
            new_anchors, new_points = [], []
            for point, anchor in zip(old_points, anchors):
                if isinstance(anchor, dict) and math.dist(anchor.get("at", [math.inf, math.inf]), point) <= 1e-6:
                    resolved = _resolve(anchor, walls, tol) if move else point            # still the point the link made: it follows its face
                    if resolved is None:
                        new_anchors.append(None)
                        new_points.append(point)
                    else:
                        new_anchors.append({**anchor, "at": resolved})
                        new_points.append(resolved)
                else:                                                                       # new, or moved by a person: look for a face again
                    new_anchors.append(_anchor_for(point, walls, tol) if walls else None)
                    new_points.append(point)
            moved = [math.dist(o, n) > 1e-9 for o, n in zip(old_points, new_points)]
            if not any(moved) and new_anchors == anchors:
                continue
            project.touch(f"drawing:{vid}")
            if any(moved):
                shift = [sum(n[i] - o[i] for o, n in zip(old_points, new_points)) / 2 for i in (0, 1)]
                g["p1"], g["p2"] = [float(x) for x in new_points[0]], [float(x) for x in new_points[1]]
                g["dimline_point"] = [g["dimline_point"][0] + shift[0], g["dimline_point"][1] + shift[1]]
                doc.update(eid, geometry=g)
            if new_anchors != anchors:
                doc.attach_semantics(eid, {"anchors": new_anchors}, namespace="spadra")
            changed += 1
    return changed
