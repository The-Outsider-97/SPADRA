"""Rooms: a BIM room is a bounded space on a level with a name, a number and a height. It has no solid of its own (so it never appears in
the 3D view or in the quantities of materials); it is an SGEO group object whose ``spadra:`` metadata holds the outline, and everything about
it is derived from that outline and from the walls, doors, windows and columns of the same level.

A wall *bounds* a room when part of the wall's centre line runs along one of the room's edges (the edge may be drawn on the wall's face or on
its axis): parallel within 1°, no further from the axis than half the wall's thickness plus 20 mm, and overlapping the edge by at least 50 mm.
A door or window *belongs to* the room when it is hosted by a bounding wall and its centre lies along the stretch of the edge that wall covers.
A column is *in* the room when its centre is inside the outline. These are geometric tests on the stored parameters, nothing is inferred.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from ..tools.errors import BadRequest
from ..tools.validation import number, text
from . import elements as E
from . import geometry as G

DEFAULT_HEIGHT = 2.7                 # metres; a parameter the person edits (a room has no upper level to read it from)
ANGLE_TOLERANCE = math.radians(1.0)
FACE_TOLERANCE = 0.02                # m beyond half the wall thickness
MIN_OVERLAP = 0.05                   # m of shared length for a wall to count as bounding an edge
Pt = tuple[float, float]


def boundary_of(meta: Mapping[str, Any]) -> list[Pt]:
    return [(float(q[0]), float(q[1])) for q in E.m(meta, "boundary", [])]


def check_boundary(meta: Mapping[str, Any]) -> None:
    poly = boundary_of(meta)
    if len(poly) < 3 or not G.is_simple_polygon(poly) or abs(G.signed_area(poly)) < 1e-6:
        raise BadRequest("The room outline must be a closed shape with an area that does not cross itself")


def metrics(poly: Sequence[Pt]) -> dict[str, Any]:
    """Area, perimeter and the centre of the area (m, m², all from the outline)."""
    area2 = 0.0
    cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(poly, list(poly[1:]) + [poly[0]]):
        w = x0 * y1 - x1 * y0
        area2 += w
        cx += (x0 + x1) * w
        cy += (y0 + y1) * w
    area = area2 / 2.0
    perimeter = sum(math.dist(a, b) for a, b in zip(poly, list(poly[1:]) + [poly[0]]))
    return {"area": abs(area), "perimeter": perimeter, "centre": (cx / (6.0 * area), cy / (6.0 * area)) if abs(area) > 1e-12 else poly[0]}


def contains(poly: Sequence[Pt], p: Pt) -> bool:
    """Even-odd point-in-polygon; a point exactly on an edge counts as inside."""
    inside = False
    for (x0, y0), (x1, y1) in zip(poly, list(poly[1:]) + [poly[0]]):
        cross = (x1 - x0) * (p[1] - y0) - (y1 - y0) * (p[0] - x0)
        if abs(cross) < 1e-9 and min(x0, x1) - 1e-9 <= p[0] <= max(x0, x1) + 1e-9 and min(y0, y1) - 1e-9 <= p[1] <= max(y0, y1) + 1e-9:
            return True
        if (y0 > p[1]) != (y1 > p[1]) and p[0] < (x1 - x0) * (p[1] - y0) / (y1 - y0) + x0:
            inside = not inside
    return inside


def interior_point(poly: Sequence[Pt]) -> Pt:
    """A point well inside a simple polygon: the centre of its largest ear triangle."""
    tris = G.triangulate(poly)
    best = max(tris, key=lambda t: abs(G.cross(G.sub(poly[t[1]], poly[t[0]]), G.sub(poly[t[2]], poly[t[0]]))))
    return (sum(poly[i][0] for i in best) / 3, sum(poly[i][1] for i in best) / 3)


def add_room(project: Any, p: Mapping[str, Any], to_m: float) -> str:
    level = project.level(text(p.get("level"), "level", 80))
    points = p.get("points")
    if not isinstance(points, (list, tuple)) or not 3 <= len(points) <= 2000:
        raise BadRequest("A room needs 3 or more corner points")
    outline = []
    for q in points:
        if not isinstance(q, (list, tuple)) or len(q) != 2:
            raise BadRequest("Every corner must be a point [x, y]")
        outline.append([float(number(q[0], "x", -1e9, 1e9)) * to_m, float(number(q[1], "y", -1e9, 1e9)) * to_m])
    number_text = text(p.get("number", ""), "number", 20, empty=True) if p.get("number") not in (None, "") else None
    check_boundary({"spadra:boundary": outline})
    spot = interior_point([(q[0], q[1]) for q in outline])
    for rid in E.element_ids(project):                       # a room drawn by hand takes the place of the automatic one that already holds this space
        meta = E.meta_of(project, rid)
        if E.m(meta, "category") == "room" and E.m(meta, "auto") and E.m(meta, "level") == level["id"] and contains(boundary_of(meta), spot):
            project.model.remove(rid)
    return create(project, level["id"], outline, name=text(p["name"], "name", 80) if p.get("name") is not None else "Room", number=number_text,
                  height=number(p.get("height", DEFAULT_HEIGHT), "height", 0.1, 500))


def create(project: Any, level_id: str, outline: Sequence[Sequence[float]], *, name: str = "Room", number: str | None = None, height: float = DEFAULT_HEIGHT,
           auto: bool = False, probe: Sequence[float] | None = None) -> str:
    """Make a room from an outline in metres. ``auto`` marks one the walls decide (see ``autorooms``); ``probe`` is a point inside it."""
    check_boundary({"spadra:boundary": [list(q) for q in outline]})
    oid = project.model.add_group(name=name)
    values: dict[str, Any] = {"category": "room", "type": "Room", "level": level_id, "boundary": [[q[0], q[1]] for q in outline],
                              "number": number if number is not None else _next_number(project, level_id), "height": height}
    if auto:
        values["auto"] = True
        values["auto_number"] = values["number"]
        if probe:
            values["probe"] = [probe[0], probe[1]]
    E._attach(project, oid, values)
    return oid


def _next_number(project: Any, level_id: str) -> str:
    """The next free room number on a level: level index (1-based) × 100 + a running count, so Level 01 rooms are 101, 102 …"""
    used = {str(E.m(project.model.get(o)["metadata"], "number", "")) for o in E.element_ids(project) if E.m(project.model.get(o)["metadata"], "category") == "room"}
    index = [l["id"] for l in project.manifest["levels"]].index(level_id) + 1
    n = index * 100 + 1
    while str(n) in used:
        n += 1
    return str(n)


def move(project: Any, oid: str, dx: float, dy: float) -> None:
    meta = E.meta_of(project, oid)
    E._attach(project, oid, {"boundary": [[q[0] + dx, q[1] + dy] for q in E.m(meta, "boundary")]})


def set_property(project: Any, oid: str, key: str, value: Any) -> dict[str, Any]:
    """The editable room parameters; the caller attaches the returned values."""
    if key == "number":
        return {"number": text(value, "number", 20, empty=True)}
    if key == "height":
        return {"height": number(value, "height", 0.1, 500)}
    if key == "level":
        return {"level": project.level(text(value, "level", 80))["id"]}
    raise BadRequest(f"'{key}' is not an editable room property")


# ------------------------------------------------------------------------------------------------------------------ relationships
def _edges(poly: Sequence[Pt]) -> list[tuple[Pt, Pt]]:
    return list(zip(poly, list(poly[1:]) + [poly[0]]))


def _along(a: Pt, b: Pt, p: Pt) -> tuple[float, float]:
    """(distance along a→b of p's projection, perpendicular distance of p from the line a-b)."""
    ux, uy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(ux, uy)
    ux, uy = ux / length, uy / length
    return (p[0] - a[0]) * ux + (p[1] - a[1]) * uy, abs(-(p[0] - a[0]) * uy + (p[1] - a[1]) * ux)


def bounding_walls(project: Any, oid: str) -> list[dict[str, Any]]:
    """Walls on the room's level that run along an edge of its outline, with the shared length and which edge, in model order."""
    meta = E.meta_of(project, oid)
    poly, level = boundary_of(meta), E.m(meta, "level")
    out: list[dict[str, Any]] = []
    for wid in E.element_ids(project):
        wmeta = E.meta_of(project, wid)
        if E.m(wmeta, "category") != "wall" or E.m(wmeta, "level") != level:
            continue
        s, e = tuple(E.m(wmeta, "start")), tuple(E.m(wmeta, "end"))
        wl = math.dist(s, e)
        if wl < 1e-6:
            continue
        half = float(E.m(wmeta, "thickness")) / 2 + FACE_TOLERANCE
        for i, (a, b) in enumerate(_edges(poly)):
            el = math.dist(a, b)
            if el < 1e-9:
                continue
            sin = abs(G.cross(((e[0] - s[0]) / wl, (e[1] - s[1]) / wl), ((b[0] - a[0]) / el, (b[1] - a[1]) / el)))
            if sin > math.sin(ANGLE_TOLERANCE):
                continue
            t0, d0 = _along(a, b, s)
            t1, d1 = _along(a, b, e)
            if max(d0, d1) > half:
                continue
            overlap = min(el, max(t0, t1)) - max(0.0, min(t0, t1))
            if overlap >= MIN_OVERLAP:
                out.append({"id": wid, "name": project.model.get(wid)["name"], "edge": i, "shared_length": overlap})
                break
    return out


def related(project: Any, oid: str) -> dict[str, list[dict[str, Any]]]:
    """Doors, windows and columns that belong to the room, and the walls that bound it. Read-only."""
    meta = E.meta_of(project, oid)
    poly, level = boundary_of(meta), E.m(meta, "level")
    walls = bounding_walls(project, oid)
    by_wall = {w["id"]: w for w in walls}
    edges = _edges(poly)
    doors: list[dict[str, Any]] = []
    windows: list[dict[str, Any]] = []
    for hid in E.element_ids(project):
        hmeta = E.meta_of(project, hid)
        cat = E.m(hmeta, "category")
        host = E.m(hmeta, "host")
        if cat in E.OPENINGS and host in by_wall:
            fr = E.wall_frame(project, host)
            offset = float(E.m(hmeta, "offset"))
            c, s = math.cos(fr["angle"]), math.sin(fr["angle"])
            centre = (fr["start"][0] + c * offset, fr["start"][1] + s * offset)
            a, b = edges[by_wall[host]["edge"]]
            t, d = _along(a, b, centre)
            if -1e-9 <= t <= math.dist(a, b) + 1e-9 and d <= fr["thickness"] / 2 + FACE_TOLERANCE:
                (doors if cat == "door" else windows).append({"id": hid, "name": project.model.get(hid)["name"], "wall": host})
    columns = [{"id": cid, "name": project.model.get(cid)["name"]} for cid in E.element_ids(project)
               if E.is_column(E.meta_of(project, cid)) and E.m(E.meta_of(project, cid), "level") == level
               and contains(poly, tuple(E.m(E.meta_of(project, cid), "point")))]
    return {"walls": walls, "doors": doors, "windows": windows, "columns": columns}


def data(project: Any, oid: str) -> dict[str, Any]:
    """Everything a room schedule row or the Properties panel needs, from the outline and the model."""
    meta = E.meta_of(project, oid)
    poly = boundary_of(meta)
    met = metrics(poly)
    height = float(E.m(meta, "height", DEFAULT_HEIGHT))
    rel = related(project, oid)
    return {"area": met["area"], "perimeter": met["perimeter"], "centre": met["centre"], "height": height, "volume": met["area"] * height,
            "number": str(E.m(meta, "number", "")), "level": E.m(meta, "level"), **rel}
