"""Building elements: walls with real openings, doors, windows, floors, roofs and beams.

Every element is stored as **parameters** (in the SGEO object's ``spadra:`` metadata) and its geometry is derived from them by
``regenerate``. So editing a parameter, moving a wall, or deleting a door always leaves the model consistent, and the floor plan,
the 3D view and the schedules all read the same parameters. Lengths in metadata are metres; the project's drawing unit only appears at
the edges (commands in, plan overlay out).

Hosting: a door or window records the id of its wall (``host``) and where along it (``offset`` from the wall's start to the opening's
centre). The wall's mesh has the opening cut through it; moving, lengthening or re-levelling the wall moves its openings with it, and
anything that would no longer fit (a wall shortened past a door, two openings overlapping) is refused as a whole.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, NamedTuple

from ..tools.errors import BadRequest, NotFound
from ..tools.validation import number, text
from src.utils.spatial_formats.common.errors import SpatialFormatError

from . import geometry as G
from . import sfb as SF
from . import classifications as CL

NS = "spadra"
MATERIALS = ("Concrete", "Timber", "Glass", "Masonry", "Steel", "Plasterboard")
BEAM_MATERIALS = ("Steel", "Concrete", "Timber")
CUT_HEIGHT = 1.2                      # plan view cuts walls 1.2 m above the level, as architects' plans do
DOOR_LEAF = 0.05                      # thickness of the drawn door leaf, m
GLASS = 0.03                          # thickness of the drawn glazing panel, m
ROOF_TYPES = {"flat": "Flat Roof", "shed": "Shed Roof", "gable": "Gable Roof", "hip": "Hip Roof"}      # the four everyday roofs; each can be changed to another later
OPENINGS = ("door", "window")
SPECIAL = ("wall", "door", "window", "floor", "roof", "structural", "room", "stair", "generic", "extrusion", "void", "sweep", "gutter", "downpipe", "foundation")
FORMS = ("extrusion", "void", "sweep")                     # solids made from a profile (see forms.py)
FLOOR_TYPES = ("Generic Floor", "Structural Slab")
IDENTITY = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]


# ---------------------------------------------------------------------------------------------------------------------- access
def meta_of(project: Any, oid: str) -> dict[str, Any]:
    try:
        return project.model.get(oid)["metadata"]
    except SpatialFormatError as exc:
        raise NotFound(f"Element {oid} does not exist") from exc


def category(project: Any, oid: str) -> str:
    return str(meta_of(project, oid).get(f"{NS}:category", "generic"))


def m(meta: Mapping[str, Any], key: str, default: Any = None) -> Any:
    return meta.get(f"{NS}:{key}", default)


def all_ids(doc: Any, **query: Any) -> list[str]:
    """Every id matching a query. SGEO and SDRW cap one query at ``MAX_QUERY_LIMIT`` (10,000); that limit is respected, so page."""
    out: list[str] = []
    while True:
        page = doc.query_ids(limit=10_000, offset=len(out), **query)
        out.extend(page)
        if len(page) < 10_000:
            return out


def element_ids(project: Any) -> list[str]:
    return all_ids(project.model)


def hosted(project: Any, wall_id: str) -> list[str]:
    """The doors and windows in a wall, in document order. SGEO filters on the metadata itself, so no element is copied to find them."""
    return list(project.model.query_ids(metadata={f"{NS}:host": wall_id}, limit=10_000))


def level_elevation(project: Any, level_id: str) -> float:
    return float(project.level(level_id)["elevation"])


def _attach(project: Any, oid: str, values: Mapping[str, Any]) -> None:
    project.model.attach_semantics(oid, dict(values), namespace=NS)


def _rot(angle: float, cx: float, cy: float, cz: float) -> list[float]:
    c, s = math.cos(angle), math.sin(angle)
    return [c, -s, 0.0, cx, s, c, 0.0, cy, 0.0, 0.0, 1.0, cz, 0.0, 0.0, 0.0, 1.0]


def _positive_volume(mesh: G.Mesh) -> G.Mesh:
    """Make the faces face outwards whatever the mapping that produced them did to the winding."""
    verts, faces = mesh
    total = 0.0
    for a, b, c in faces:
        A, B, C = verts[a], verts[b], verts[c]
        total += A[0] * (B[1] * C[2] - B[2] * C[1]) - A[1] * (B[0] * C[2] - B[2] * C[0]) + A[2] * (B[0] * C[1] - B[1] * C[0])
    return (verts, faces) if total >= 0 else (verts, [[a, c, b] for a, b, c in faces])


def _set_mesh(project: Any, oid: str, mesh: G.Mesh, transform: list[float] | None = None) -> None:
    """Store a mesh. Given world-space vertices and no transform, the mesh is stored around its own centre with a translation back to place,
    so the element's origin is its centre: an animated turn or scale then happens about the element itself, not about the model origin."""
    verts, faces = mesh
    if transform is None and verts:
        lo = [min(v[i] for v in verts) for i in range(3)]
        hi = [max(v[i] for v in verts) for i in range(3)]
        c = [(lo[i] + hi[i]) / 2 for i in range(3)]
        verts = [(v[0] - c[0], v[1] - c[1], v[2] - c[2]) for v in verts]
        transform = [1.0, 0.0, 0.0, c[0], 0.0, 1.0, 0.0, c[1], 0.0, 0.0, 1.0, c[2], 0.0, 0.0, 0.0, 1.0]
    project.model.set_geometry(oid, {"kind": "mesh", "vertices": [list(v) for v in verts], "faces": [list(f) for f in faces]})
    project.model.set_transform(oid, transform or IDENTITY)


# ------------------------------------------------------------------------------------------------------------------------ walls
def wall_frame(project: Any, oid: str) -> dict[str, Any]:
    """Everything about a wall's placement: ends, length, angle, mid point and base elevation (all metres / radians), and how its
    two ends are joined to the walls they meet (``ends``, see ``wall_joins``)."""
    meta = meta_of(project, oid)
    if m(meta, "category") != "wall":
        raise BadRequest("That element is not a wall")
    start, end = tuple(m(meta, "start")), tuple(m(meta, "end"))
    length = math.dist(start, end)
    stored = m(meta, "ends") or G.SQUARE_ENDS
    return {"start": start, "end": end, "length": length, "angle": math.atan2(end[1] - start[1], end[0] - start[0]),
            "mid": ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2), "thickness": float(m(meta, "thickness")), "height": float(m(meta, "height")),
            "base": level_elevation(project, m(meta, "level")) + float(m(meta, "base_offset", 0.0)), "meta": meta,
            "ends": ((float(stored[0][0]), float(stored[0][1])), (float(stored[1][0]), float(stored[1][1])))}


# ---------------------------------------------------------------------------------------------------------------------- wall joints
JOIN_TOLERANCE = 0.002            # m: wall ends closer than this are one point; an end this close to the side of a wall meets it


class _WallRow(NamedTuple):
    level: str
    start: tuple[float, float]
    end: tuple[float, float]
    thickness: float
    joins: tuple[str, ...]


def _wall_table(project: Any) -> dict[str, _WallRow]:
    """Every wall in the project, read in one pass over the scene without copying any geometry (SGEO's ``get`` copies, and scans, per call)."""
    scene = project.model._model
    out: dict[str, _WallRow] = {}
    with scene._mutation_lock:
        for obj in scene.objects:
            meta = obj.get("metadata") or {}
            if meta.get(f"{NS}:category") == "wall":
                start, end = tuple(meta[f"{NS}:start"]), tuple(meta[f"{NS}:end"])
                if math.dist(start, end) > 1e-6:
                    out[obj["id"]] = _WallRow(str(meta.get(f"{NS}:level")), start, end, float(meta[f"{NS}:thickness"]), tuple(meta.get(f"{NS}:joins") or ()))
    return out


def _distance_to_segment(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    """(distance from p to the segment ab, position of p's foot along ab from a)."""
    along = G.dot(G.sub(p, a), G.unit(G.sub(b, a)))
    return G.nearest_on_segment(p, a, b)[1], along


def wall_joins(project: Any, oid: str, table: Mapping[str, _WallRow] | None = None) -> dict[str, Any]:
    """How a wall's two ends meet other walls on its level, from the walls' own parameters (nothing is stored but the result).

    * Walls that end at the same point meet in a mitre (two walls, at any angle) or face to face round the point (three or more).
    * A wall that ends on the side of another (a T) is cut, slanted if need be, flush to that wall's near face; the through wall is untouched.
    * Anything else - walls that merely come near, walls that run along each other - is left square.

    Returns ``{"ends": ((start +y, start -y), (end +y, end -y)), "with": [ids that shaped it]}`` in the wall's own frame."""
    f = wall_frame(project, oid)
    level = str(m(f["meta"], "level"))
    rows = {k: row for k, row in (table if table is not None else _wall_table(project)).items() if row.level == level}     # walls join on their own level only
    return join_ends(rows, oid, f["start"], f["end"], f["thickness"])


def join_ends(table: Mapping[str, _WallRow], oid: str, start: tuple[float, float], end: tuple[float, float], t: float) -> dict[str, Any]:
    """The joint rule for any thick line (a wall, a strip foundation): how the two ends of ``oid`` (from ``start`` to ``end``, ``t`` thick) meet the other
    lines of ``table`` (already limited to one level). See ``wall_joins`` for what is returned."""
    length = math.dist(start, end)
    u = G.unit(G.sub(end, start)) if length > 1e-9 else (1.0, 0.0)
    ends: list[tuple[float, float]] = []
    shaped: set[str] = set()
    for at_start, point, d in ((True, start, u), (False, end, (-u[0], -u[1]))):
        members = [(oid, d, t)]
        for xid, row in table.items():
            if xid == oid:
                continue
            for near, far in ((row.start, row.end), (row.end, row.start)):
                if math.dist(near, point) <= JOIN_TOLERANCE:
                    members.append((xid, G.unit(G.sub(far, near)), row.thickness))
                    shaped.add(xid)
                    break
        left_right: tuple[float, float] | None = G.miter_node(members)[oid] if len(members) > 1 else None
        if left_right is None:                                                 # no wall ends here: does this end touch the side of one?
            best: tuple[float, str] | None = None
            for xid, row in table.items():
                if xid == oid:
                    continue
                dist, along = _distance_to_segment(point, row.start, row.end)
                if JOIN_TOLERANCE < along < math.dist(row.start, row.end) - JOIN_TOLERANCE and dist <= row.thickness / 2 + JOIN_TOLERANCE \
                        and (best is None or dist < best[0]):
                    best = (dist, xid)
            if best is not None:
                row = table[best[1]]
                axis = G.unit(G.sub(row.end, row.start))
                butt = G.butt_ends(d, t, point, axis, _foot(point, row, axis), row.thickness)
                if butt is not None:
                    left_right, shaped = butt, shaped | {best[1]}
        left, right = (round(v, 9) + 0.0 for v in (left_right if left_right is not None else (0.0, 0.0)))     # +0.0: no -0.0 in the stored joint
        # "left" and "right" above are looking along d, into the wall; the wall's own +y face is on the left at its start, the right at its end
        ends.append((left, right) if at_start else (right, left))
    return {"ends": (ends[0], ends[1]), "with": sorted(shaped)}


def _foot(point: tuple[float, float], row: _WallRow, axis: tuple[float, float]) -> tuple[float, float]:
    """The point on the axis of a wall nearest to ``point``."""
    along = G.dot(G.sub(point, row.start), axis)
    return (row.start[0] + axis[0] * along, row.start[1] + axis[1] * along)


def _touching(table: Mapping[str, _WallRow], oid: str, level: str, start: tuple[float, float], end: tuple[float, float], thickness: float) -> set[str]:
    """The walls whose shape depends on a wall at this place: those it shaped (on any level: it may just have left theirs), and those on
    its level that end on it or at its ends."""
    out: set[str] = {x for x, row in table.items() if oid in row.joins}
    for xid, row in table.items():
        if xid == oid or row.level != level:
            continue
        if any(math.dist(p, q) <= JOIN_TOLERANCE for p in (start, end) for q in (row.start, row.end)) or \
                any(_distance_to_segment(q, start, end)[0] <= thickness / 2 + JOIN_TOLERANCE for q in (row.start, row.end)):
            out.add(xid)
    return out


def opening_span(meta: Mapping[str, Any]) -> tuple[float, float, float, float]:
    """(x0, x1, sill, head): along the wall from its start, and up from its base, in metres."""
    centre, width, height, sill = float(m(meta, "offset")), float(m(meta, "width")), float(m(meta, "height")), float(m(meta, "sill", 0.0))
    return centre - width / 2, centre + width / 2, sill, sill + height


def regenerate_wall(project: Any, oid: str) -> None:
    """Rebuild a wall (and everything hosted on it) from parameters, then the walls it joins or used to join, whose ends depend on it.
    Refuses, with the reason, any combination that does not fit."""
    meta = meta_of(project, oid)
    if m(meta, "category") != "wall":
        raise BadRequest("That element is not a wall")
    f = wall_frame(project, oid)
    table = _wall_table(project)
    affected = set(m(meta, "joins") or ()) | (_touching(table, oid, str(m(meta, "level")), f["start"], f["end"], f["thickness"]) if f["length"] > 1e-6 else set())
    _build_wall(project, oid, table)
    _refresh_walls(project, affected - {oid})


def _refresh_walls(project: Any, ids: Any) -> None:
    """Re-derive the joints of these walls (each against its own level), rebuilding only those whose joint actually changed."""
    ids = sorted(ids)
    if not ids:
        return
    table = _wall_table(project)
    for xid in ids:
        if xid not in table:                                                    # gone, or not a wall
            continue
        meta = meta_of(project, xid)
        joined = wall_joins(project, xid, table)
        if _same_ends(joined["ends"], m(meta, "ends")) and joined["with"] == sorted(m(meta, "joins") or ()):
            continue
        _build_wall(project, xid, table)


def _same_ends(a: Any, b: Any) -> bool:
    b = b or G.SQUARE_ENDS
    return all(abs(a[i][j] - b[i][j]) < 1e-9 for i in range(2) for j in range(2))


def _build_wall(project: Any, oid: str, table: Mapping[str, _WallRow]) -> None:
    """Derive one wall's joints and rebuild its solid and the doors and windows hosted in it."""
    f = wall_frame(project, oid)
    if f["length"] < 1e-6:
        raise BadRequest("A wall needs two different end points")
    if f["thickness"] <= 0 or f["height"] <= 0:
        raise BadRequest("Wall thickness and height must be positive")
    joined = wall_joins(project, oid, table)
    _attach(project, oid, {"ends": [list(joined["ends"][0]), list(joined["ends"][1])], "joins": joined["with"]})
    f = wall_frame(project, oid)
    (sl, sr), (el, er) = f["ends"]
    cut_start, cut_end = max(0.0, -sl, -sr), max(0.0, -el, -er)                # how far each end is cut back by its joint, at the most
    length, height = f["length"], f["height"]
    spans: list[tuple[float, float, float, float, str]] = []
    from . import forms as FX
    cuts = FX.remember_cuts(project, oid)                                       # rectangular voids through the whole thickness
    for hid in hosted(project, oid):
        hm = project.model.get(hid)["metadata"]
        x0, x1, z0, z1 = opening_span(hm)
        label = project.model.get(hid)["name"] or str(m(hm, "category")).title()
        if x0 < -1e-9 or x1 > length + 1e-9:
            raise BadRequest(f"{label} does not fit on the wall: it would run past the end of the wall")
        if x0 < cut_start + (1e-6 if cut_start else -1e-9) or x1 > length - cut_end - (1e-6 if cut_end else -1e-9):
            raise BadRequest(f"{label} does not fit on the wall: it would run into the corner where the wall meets another")
        if z0 < -1e-9 or z1 > height + 1e-9:
            raise BadRequest(f"{label} is taller than the wall")
        spans.append((x0, x1, z0, z1, hid))
    every = spans + [tuple(c) for c in cuts]
    for i, a in enumerate(every):
        for b in every[i + 1:]:
            if a[0] < b[1] - 1e-9 and b[0] < a[1] - 1e-9 and a[2] < b[3] - 1e-9 and b[2] < a[3] - 1e-9:
                raise BadRequest("Two openings in the same wall would overlap" if len(cuts) == 0 else "A void and an opening, or two voids, would overlap in the same wall")
    try:
        if every or f["ends"] != G.SQUARE_ENDS:
            local = [(x0 - length / 2, x1 - length / 2, z0 - height / 2, z1 - height / 2) for x0, x1, z0, z1, _ in every]
            _set_mesh(project, oid, G.wall_mesh(length, f["thickness"], height, local, f["ends"]),
                      _rot(f["angle"], f["mid"][0], f["mid"][1], f["base"] + height / 2))
        else:
            project.model.set_geometry(oid, {"kind": "box", "center": [0.0, 0.0, 0.0], "size": [length, f["thickness"], height]})
            project.model.set_transform(oid, _rot(f["angle"], f["mid"][0], f["mid"][1], f["base"] + height / 2))
    except G.GeometryError as exc:
        raise BadRequest(str(exc)) from exc
    for hid in (s[4] for s in spans):
        _place_opening(project, hid, f)


def _place_opening(project: Any, oid: str, frame: Mapping[str, Any]) -> None:
    """Position a door or window's own solid inside its opening. The object's origin is its hinge: a vertical axis at the hinge edge (for a door, on
    the wall face the plan's swing arc is drawn from; for a window, on the wall's centre line), so an animated turn swings the leaf about its hinge,
    as the plan shows, and not about its own centre. The leaf itself sits where it did: centred in the opening."""
    meta = project.model.get(oid)["metadata"]
    x0, x1, z0, z1 = opening_span(meta)
    door = m(meta, "category") == "door"
    thickness = DOOR_LEAF if door else GLASS
    hinge_x = x0 if m(meta, "hinge", "start") == "start" else x1
    face = int(m(meta, "swing", 1)) * frame["thickness"] / 2 if door else 0.0
    c, s = math.cos(frame["angle"]), math.sin(frame["angle"])
    project.model.set_geometry(oid, {"kind": "box", "center": [(x0 + x1) / 2 - hinge_x, -face, 0.0], "size": [x1 - x0, thickness, z1 - z0]})
    project.model.set_transform(oid, _rot(frame["angle"], frame["start"][0] + c * hinge_x - s * face, frame["start"][1] + s * hinge_x + c * face, frame["base"] + (z0 + z1) / 2))
    _attach(project, oid, {"level": m(frame["meta"], "level")})


def add_opening(project: Any, kind: str, p: Mapping[str, Any], to_m: float) -> str:
    """Create a door or window on a wall. ``point`` (drawing units) or ``offset`` (m along the wall) says where; both are kept inside the wall."""
    wall = text(p.get("wall"), "wall", 80)
    f = wall_frame(project, wall)
    width = number(p.get("width", 900 if kind == "door" else 1200), "width", 100, 20000) / 1000.0
    height = number(p.get("height", 2100 if kind == "door" else 1200), "height", 100, 20000) / 1000.0
    sill = 0.0 if kind == "door" else number(p.get("sill", 900), "sill", 0, 20000) / 1000.0
    if p.get("offset") is not None:
        offset = number(p["offset"], "offset", 0, 100000)
    else:
        pt = p.get("point")
        if not isinstance(pt, (list, tuple)) or len(pt) != 2:
            raise BadRequest("Say where on the wall: give a point or an offset")
        ux, uy = math.cos(f["angle"]), math.sin(f["angle"])
        offset = (float(pt[0]) * to_m - f["start"][0]) * ux + (float(pt[1]) * to_m - f["start"][1]) * uy
    if width > f["length"] + 1e-9:
        raise BadRequest(f"The wall is only {f['length']:.2f} m long, too short for a {width:.2f} m {kind}")
    (sl, sr), (el, er) = f["ends"]
    clear_start, clear_end = max(0.0, -sl, -sr), max(0.0, -el, -er)             # the corners at either end take some of the wall
    clear_start, clear_end = clear_start + (0.005 if clear_start else 0.0), clear_end + (0.005 if clear_end else 0.0)
    if width + clear_start + clear_end > f["length"] + 1e-9:
        raise BadRequest(f"The wall is only {f['length']:.2f} m long, and its corners leave no room for a {width:.2f} m {kind}")
    offset = max(width / 2 + clear_start, min(f["length"] - width / 2 - clear_end, offset))
    values: dict[str, Any] = {"category": kind, "type": "Single Flush Door" if kind == "door" else "Fixed Window", "host": wall, "offset": offset,
                              "width": width, "height": height, "sill": sill, "level": m(f["meta"], "level"),
                              "material": p.get("material", "Timber" if kind == "door" else "Glass")}
    if values["material"] not in MATERIALS:
        raise BadRequest(f"material must be one of {', '.join(MATERIALS)}")
    values["hinge"] = _choice(p.get("hinge", "start"), ("start", "end"), "hinge")
    if kind == "door":
        values["swing"] = 1 if _choice(p.get("swing", "left"), ("left", "right"), "swing") == "left" else -1
    name = text(p.get("name") or kind.title(), "name", 80)
    oid = project.model.add_box(width=1.0, depth=1.0, height=1.0, name=name)
    _attach(project, oid, values)
    regenerate_wall(project, wall)
    return oid


# ------------------------------------------------------------------------------------------------------------------------ floors
def _boundary(points: Any, to_m: float, label: str) -> list[list[float]]:
    if not isinstance(points, (list, tuple)) or not 3 <= len(points) <= 2000:
        raise BadRequest(f"A {label} needs 3 or more corner points")
    out = []
    for q in points:
        if not isinstance(q, (list, tuple)) or len(q) != 2:
            raise BadRequest("Every corner must be a point [x, y]")
        out.append([float(number(q[0], "x", -1e9, 1e9)) * to_m, float(number(q[1], "y", -1e9, 1e9)) * to_m])
    if not G.is_simple_polygon([tuple(q) for q in out]):
        raise BadRequest(f"The {label} outline crosses itself or repeats a point")
    if abs(G.signed_area([tuple(q) for q in out])) < 1e-6:
        raise BadRequest(f"The {label} outline has no area")
    return out


def regenerate_floor(project: Any, oid: str) -> None:
    meta = project.model.get(oid)["metadata"]
    top = level_elevation(project, m(meta, "level")) + float(m(meta, "offset", 0.0))
    from . import forms as FX
    cut = FX.remember_cuts(project, oid)                                             # voids through the slab's thickness: holes, and notches in its edge
    try:
        _set_mesh(project, oid, FX._solid(cut.outline or [tuple(q) for q in m(meta, "boundary")], cut.holes, top - float(m(meta, "thickness")), top))
    except G.GeometryError as exc:
        raise BadRequest(str(exc)) from exc


def add_floor(project: Any, p: Mapping[str, Any], to_m: float) -> str:
    level = project.level(text(p.get("level"), "level", 80))
    boundary = _boundary(p.get("points"), to_m, "floor")
    thickness = number(p.get("thickness", 200), "thickness", 10, 5000) / 1000.0
    material = _choice(p.get("material", "Concrete"), MATERIALS, "material")
    oid = project.model.add_box(width=1.0, depth=1.0, height=1.0, name=text(p.get("name") or "Floor", "name", 80))
    _attach(project, oid, {"category": "floor", "type": "Generic Floor", "level": level["id"], "boundary": boundary, "thickness": thickness, "offset": 0.0, "material": material})
    regenerate_floor(project, oid)
    return oid


# ------------------------------------------------------------------------------------------------------------------------- roofs
def _rectangle(boundary: list[list[float]]) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float], float, float]:
    """(centre, long-axis unit vector, short-axis unit vector, long length, short length) of a rectangular outline, else an error."""
    if len(boundary) != 4:
        raise BadRequest("A pitched roof needs a four-corner rectangular outline")
    p = [tuple(q) for q in boundary]
    e = [G.sub(p[(i + 1) % 4], p[i]) for i in range(4)]
    if any(abs(G.dot(e[i], e[(i + 1) % 4])) > 1e-6 * G.norm(e[i]) * G.norm(e[(i + 1) % 4]) for i in range(4)) or abs(G.norm(e[0]) - G.norm(e[2])) > 1e-6 * G.norm(e[0]):
        raise BadRequest("A pitched roof needs a rectangular outline (right-angled corners)")
    a, b = (e[0], e[1]) if G.norm(e[0]) >= G.norm(e[1]) else (e[1], e[0])
    centre = (sum(q[0] for q in p) / 4, sum(q[1] for q in p) / 4)
    return centre, G.unit(a), G.unit(b), G.norm(a), G.norm(b)


def _hip_mesh(centre: tuple[float, float], along: tuple[float, float], across: tuple[float, float], length: float, width: float, rise: float,
              thickness: float, base: float) -> G.Mesh:
    """A hip roof: four slopes of one pitch meeting at a ridge ``length - width`` long (a pyramid when the outline is square). The slab is
    ``thickness`` thick measured straight down, and its edges at the eaves are vertical."""
    ridge = (length - width) / 2
    top = width / 2 * rise
    pts = [(-width / 2, -length / 2, 0.0), (width / 2, -length / 2, 0.0), (width / 2, length / 2, 0.0), (-width / 2, length / 2, 0.0),
           (0.0, -ridge, top), (0.0, ridge, top)]
    n = len(pts)
    merge = {5: 4, n + 5: n + 4} if ridge < 1e-9 else {}                               # a square outline: both ends of the ridge are one apex
    verts = [[centre[0] + across[0] * s + along[0] * t, centre[1] + across[1] * s + along[1] * t, base + z] for s, t, z in pts]
    verts += [[v[0], v[1], v[2] - thickness] for v in verts]
    quads = [(0, 1, 4), (1, 2, 5, 4), (2, 3, 5), (3, 0, 4, 5)]                     # the four slopes, as seen from above
    faces: list[list[int]] = []
    def add(poly: tuple[int, ...], shift: int, up: bool) -> None:
        idx = [merge.get(i + shift, i + shift) for i in (poly if up else reversed(poly))]
        for k in range(1, len(idx) - 1):
            tri = [idx[0], idx[k], idx[k + 1]]
            if len(set(tri)) == 3:
                faces.append(tri)
    for q in quads:
        add(q, 0, True)
        add(q, n, False)
    for i in range(4):                                                                # the vertical edge at each eave
        j = (i + 1) % 4
        faces += [[n + i, n + j, j], [n + i, j, i]]
    if merge:                                                                         # leave out the vertices nothing uses
        keep = [i for i in range(len(verts)) if i not in merge]
        new = {old: k for k, old in enumerate(keep)}
        return [verts[i] for i in keep], [[new[i] for i in f] for f in faces]
    return verts, faces


def roof_rise(meta: Mapping[str, Any]) -> float:
    """How far the top of a pitched roof stands above its eaves (m); 0 for a flat roof."""
    kind = m(meta, "roof")
    if kind == "flat":
        return 0.0
    width = _rectangle([list(q) for q in m(meta, "boundary")])[4]
    return (width if kind == "shed" else width / 2) * math.tan(math.radians(float(m(meta, "pitch"))))


def roof_eave_edges(meta: Mapping[str, Any]) -> list[int]:
    """The outline edges (by index: edge ``i`` runs from corner ``i`` to the next) a roof sheds water to: every edge of a flat or hip roof, the long
    edges of a gable roof (its ridge runs the long way), and the low long edge of a shed roof (it rises towards the other)."""
    boundary = [tuple(q) for q in m(meta, "boundary")]
    kind = m(meta, "roof")
    count = len(boundary)
    if kind in ("flat", "hip"):
        return list(range(count))
    centre, along, across, length, width = _rectangle([list(q) for q in boundary])
    keep = []
    for i in range(count):
        a, b = boundary[i], boundary[(i + 1) % count]
        d = G.sub(b, a)
        if abs(G.dot(d, along)) < 0.5 * G.norm(d):                                     # not along the long axis: a gable end
            continue
        side = G.dot(G.sub(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), centre), across)
        if kind == "shed" and side > 0:                                               # a shed roof rises towards +across; water leaves at -across
            continue
        keep.append(i)
    return keep


def roof_eaves(meta: Mapping[str, Any]) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """The eave edges as plan segments (m), in order round the outline."""
    boundary = [tuple(q) for q in m(meta, "boundary")]
    return [(boundary[i], boundary[(i + 1) % len(boundary)]) for i in roof_eave_edges(meta)]


def regenerate_roof(project: Any, oid: str) -> None:
    meta = project.model.get(oid)["metadata"]
    base = level_elevation(project, m(meta, "level")) + float(m(meta, "offset", 0.0))
    thickness, kind = float(m(meta, "thickness")), m(meta, "roof")
    boundary = m(meta, "boundary")
    try:
        if kind == "flat":
            mesh = G.prism([tuple(q) for q in boundary], base, base + thickness)
        else:
            centre, along, across, length, width = _rectangle(boundary)
            rise_angle = math.tan(math.radians(float(m(meta, "pitch"))))
            if kind == "hip":
                mesh = _positive_volume(_hip_mesh(centre, along, across, length, width, rise_angle, thickness, base))
            else:
                if kind == "gable":
                    r = width / 2 * rise_angle
                    profile = [(-width / 2, -thickness), (0.0, r - thickness), (width / 2, -thickness), (width / 2, 0.0), (0.0, r), (-width / 2, 0.0)]
                else:
                    r = width * rise_angle
                    profile = [(-width / 2, -thickness), (width / 2, r - thickness), (width / 2, r), (-width / 2, 0.0)]
                verts, faces = G.prism(profile, -length / 2, length / 2)             # (s, z, t): across, up, along
                mapped = [[centre[0] + across[0] * s + along[0] * t, centre[1] + across[1] * s + along[1] * t, base + z] for s, z, t in verts]
                mesh = _positive_volume((mapped, faces))
        _set_mesh(project, oid, mesh)
    except G.GeometryError as exc:
        raise BadRequest(str(exc)) from exc


def add_roof(project: Any, p: Mapping[str, Any], to_m: float) -> str:
    level = project.level(text(p.get("level"), "level", 80))
    kind = _choice(p.get("roof", "flat"), tuple(ROOF_TYPES), "roof")
    boundary = _boundary(p.get("points"), to_m, "roof")
    thickness = number(p.get("thickness", 200), "thickness", 10, 5000) / 1000.0
    pitch = number(p.get("pitch", 30), "pitch", 1, 80)
    material = _choice(p.get("material", "Timber"), MATERIALS, "material")
    if kind != "flat":
        _rectangle(boundary)
    oid = project.model.add_box(width=1.0, depth=1.0, height=1.0, name=text(p.get("name") or ROOF_TYPES[kind], "name", 80))
    _attach(project, oid, {"category": "roof", "type": ROOF_TYPES[kind], "roof": kind, "level": level["id"], "boundary": boundary, "thickness": thickness,
                           "pitch": pitch, "offset": 0.0, "material": material})
    regenerate_roof(project, oid)
    return oid


# ------------------------------------------------------------------------------------------------------------------------- beams
def regenerate_beam(project: Any, oid: str) -> None:
    meta = project.model.get(oid)["metadata"]
    start, end = tuple(m(meta, "start")), tuple(m(meta, "end"))
    length = math.dist(start, end)
    if length < 1e-6:
        raise BadRequest("A beam needs two different end points")
    top = level_elevation(project, m(meta, "level")) + float(m(meta, "top"))
    family = str(m(meta, "profile", "rect"))
    if family != "rect":
        from . import profiles as PR
        rs = PR.rolled(PR.rings(family, m(meta, "dims")), float(m(meta, "roll", 0.0)))
        width, depth = PR.bounds(rs)
        verts, faces = PR.prism(rs, length * 1000.0)                                      # (s across, z up, t along) in mm
        ux, uy = (end[0] - start[0]) / length, (end[1] - start[1]) / length
        mapped = [[start[0] + ux * t / 1000.0 - uy * s_ / 1000.0, start[1] + uy * t / 1000.0 + ux * s_ / 1000.0, top + z / 1000.0] for s_, z, t in verts]
        _attach(project, oid, {"width": width / 1000.0, "depth": depth / 1000.0})         # the bounding box of the section as placed
        _set_mesh(project, oid, _positive_volume((mapped, faces)))
        return
    width, depth = float(m(meta, "width")), float(m(meta, "depth"))
    if width <= 0 or depth <= 0:
        raise BadRequest("Beam width and depth must be positive")
    project.model.set_geometry(oid, {"kind": "box", "center": [0.0, 0.0, 0.0], "size": [length, width, depth]})
    project.model.set_transform(oid, _rot(math.atan2(end[1] - start[1], end[0] - start[0]), (start[0] + end[0]) / 2, (start[1] + end[1]) / 2, top - depth / 2))


def beam_section(meta: Mapping[str, Any]) -> dict[str, Any]:
    """A beam's profile as stored: ``family`` ('rect' or a profile family), the ``size`` designation, ``dims`` (mm) and ``roll`` (degrees)."""
    family = str(m(meta, "profile", "rect"))
    if family == "rect":
        return {"family": "rect", "size": "Rectangular", "dims": {}, "roll": 0.0}
    return {"family": family, "size": str(m(meta, "size", "custom")), "dims": dict(m(meta, "dims") or {}), "roll": float(m(meta, "roll", 0.0))}


def add_beam(project: Any, p: Mapping[str, Any], to_m: float) -> str:
    level = project.level(text(p.get("level"), "level", 80))
    pts = []
    for key in ("start", "end"):
        q = p.get(key)
        if not isinstance(q, (list, tuple)) or len(q) != 2:
            raise BadRequest(f"{key} must be a point [x, y]")
        pts.append([float(number(q[0], key, -1e9, 1e9)) * to_m, float(number(q[1], key, -1e9, 1e9)) * to_m])
    family = p.get("profile", "rect")
    values: dict[str, Any] = {"category": "structural", "type": "Beam", "level": level["id"], "start": pts[0], "end": pts[1], "top": number(p.get("top", 3.0), "top", 0.1, 500)}
    if family == "rect":
        material = _choice(p.get("material", "Steel"), BEAM_MATERIALS, "material")
        values.update(width=number(p.get("width", 200), "width", 10, 5000) / 1000.0, depth=number(p.get("depth", 400), "depth", 10, 5000) / 1000.0, material=material, profile="rect")
    else:
        from . import profiles as PR
        PR.check_family(family)
        material = _choice(p.get("material", "Steel"), BEAM_MATERIALS, "material")
        if material != "Steel":
            raise BadRequest("Rolled and tube profiles are steel: choose the Steel material, or a rectangular beam")
        size = str(p.get("size") or PR.default_size(family))
        dims = PR.dims_of(family, size, p.get("dims") if isinstance(p.get("dims"), Mapping) else None)
        values.update(profile=family, size=size, dims=dims, roll=number(p.get("roll", 0.0), "roll", -360, 360), material=material, width=0.1, depth=0.1)
    oid = project.model.add_box(width=1.0, depth=1.0, height=1.0, name=text(p.get("name") or "Beam", "name", 80))
    _attach(project, oid, values)
    try:
        regenerate_beam(project, oid)
    except BadRequest:
        project.model.remove(oid)
        raise
    return oid


def is_column(meta: Mapping[str, Any]) -> bool:
    return m(meta, "category") == "structural" and m(meta, "type") == "Column"


def regenerate_column(project: Any, oid: str) -> None:
    """A column is a vertical box centred on ``point``, from ``base`` to ``top`` (metres above its level), turned by ``rotation`` (radians)."""
    meta = project.model.get(oid)["metadata"]
    x, y = m(meta, "point")
    width, depth = float(m(meta, "width")), float(m(meta, "depth"))
    base, top = float(m(meta, "base", 0.0)), float(m(meta, "top"))
    if width <= 0 or depth <= 0:
        raise BadRequest("Column width and depth must be positive")
    if top - base < 0.01:
        raise BadRequest("A column must be at least 10 mm tall: its top has to be above its base")
    z0 = level_elevation(project, m(meta, "level")) + base
    project.model.set_geometry(oid, {"kind": "box", "center": [0.0, 0.0, 0.0], "size": [width, depth, top - base]})
    project.model.set_transform(oid, _rot(float(m(meta, "rotation", 0.0)), x, y, z0 + (top - base) / 2))


def add_column(project: Any, p: Mapping[str, Any], to_m: float) -> str:
    level = project.level(text(p.get("level"), "level", 80))
    q = p.get("point")
    if not isinstance(q, (list, tuple)) or len(q) != 2:
        raise BadRequest("point must be [x, y]")
    point = [float(number(q[0], "x", -1e9, 1e9)) * to_m, float(number(q[1], "y", -1e9, 1e9)) * to_m]
    material = _choice(p.get("material", "Concrete"), BEAM_MATERIALS, "material")
    base = number(p.get("base", 0.0), "base", -100, 500)
    top = number(p.get("top", 3.0), "top", -100, 500)
    oid = project.model.add_box(width=1.0, depth=1.0, height=1.0, name=text(p.get("name") or "Column", "name", 80))
    _attach(project, oid, {"category": "structural", "type": "Column", "level": level["id"], "point": point, "base": base, "top": top,
                           "width": number(p.get("width", 300), "width", 10, 5000) / 1000.0, "depth": number(p.get("depth", 300), "depth", 10, 5000) / 1000.0,
                           "rotation": math.radians(number(p.get("rotation", 0.0), "rotation", -360, 360)), "material": material})
    try:
        regenerate_column(project, oid)
    except BadRequest:
        project.model.remove(oid)
        raise
    return oid


def _choice(value: Any, allowed: tuple[str, ...], label: str) -> str:
    if value not in allowed:
        raise BadRequest(f"{label} must be one of {', '.join(allowed)}")
    return str(value)


# ------------------------------------------------------------------------------------------------------------------- regeneration
def regenerate(project: Any, oid: str) -> None:
    """Rebuild one element, and whatever depends on it, from its parameters."""
    meta = meta_of(project, oid)
    cat = m(meta, "category")
    if cat == "wall":
        regenerate_wall(project, oid)
    elif cat in OPENINGS:
        host = m(meta, "host")
        if host is None or not alive(project, host):
            raise BadRequest(f"This {cat} has lost its wall")
        regenerate_wall(project, host)
    elif cat == "floor":
        regenerate_floor(project, oid)
    elif cat == "roof":
        regenerate_roof(project, oid)
        from . import gutters as GU
        GU.follow(project, oid)                                                        # the gutters hang on the roof's eaves: they follow it
    elif cat == "gutter":
        from . import gutters as GU
        GU.regenerate(project, oid)
    elif cat == "downpipe":
        from . import gutters as GU
        GU.regenerate_downpipe(project, oid)
    elif cat == "structural":
        regenerate_column(project, oid) if is_column(meta) else regenerate_beam(project, oid)
    elif cat == "stair":
        from . import stairs as SR
        SR.regenerate(project, oid)
    elif cat == "generic":
        from . import families as FM
        FM.regenerate(project, oid)
    elif cat in FORMS:
        from . import forms as FX
        FX.regenerate(project, oid)
    elif cat == "foundation":
        from . import foundations as FD
        FD.regenerate(project, oid)
    elif cat == "room":
        from . import rooms as R
        R.check_boundary(meta)


def alive(project: Any, oid: str) -> bool:
    """Whether the model still holds this id. The one place that asks (a missing id raises the library's own error, nothing else is caught)."""
    try:
        project.model.get(oid)
        return True
    except SpatialFormatError:
        return False


def regenerate_all(project: Any) -> None:
    """Rebuild every element. Used after an undo-free structural change and by tests; walls first so their openings are placed."""
    ids = element_ids(project)
    for oid in ids:
        if m(project.model.get(oid)["metadata"], "category") == "wall":
            regenerate_wall(project, oid)
    for oid in ids:
        if m(project.model.get(oid)["metadata"], "category") in ("floor", "roof", "structural", "stair", "generic", "gutter", "downpipe", "foundation", *FORMS):
            regenerate(project, oid)


# ----------------------------------------------------------------------------------------------------------------- edit and move
def _mm(value: Any, label: str, lo: float, hi: float) -> float:
    return number(value, label, lo, hi) / 1000.0


def _beam_profile_values(meta: Mapping[str, Any], key: str, value: Any) -> dict[str, Any]:
    """The parameters a change of profile, size, one dimension or roll sets on a beam."""
    from . import profiles as PR
    family = str(m(meta, "profile", "rect"))
    if key == "profile":
        if value == "rect":
            return {"profile": "rect"}
        fam = PR.check_family(value)
        if m(meta, "material") != "Steel":
            raise BadRequest("Rolled and tube profiles are steel: make the material Steel first")
        size = PR.default_size(fam)
        return {"profile": fam, "size": size, "dims": PR.dims_of(fam, size), "roll": float(m(meta, "roll", 0.0))}
    if family == "rect":
        raise BadRequest("A rectangular beam has a width and a depth: choose a profile first to give it a size")
    if key == "roll":
        return {"roll": number(value, "roll", -360, 360)}
    current = dict(m(meta, "dims") or {})
    if key == "size":
        size = text(value, "size", 60)
        return {"size": size, "dims": PR.dims_of(family, size, current)}
    name = key[4:]
    if name not in {k for k, _ in PR.family_dims(family)}:
        raise BadRequest(f"A {family} has no dimension '{name}'")
    current[name] = number(value, name, 0.1, 3000)
    return {"size": PR.CUSTOM, "dims": PR.validate(family, current)}


def set_property(project: Any, oid: str, key: str, value: Any) -> None:
    """Change one parameter, then rebuild. Raises ``BadRequest`` (and the caller rolls everything back) if the result does not fit."""
    meta = meta_of(project, oid)
    cat = m(meta, "category")
    values: dict[str, Any] = {}
    if key.startswith("custom:"):
        from . import relations as RL
        RL.set_custom(project, oid, key[7:], RL.parse_custom(value))
        return
    if SF.is_field(key):                                                         # a classification code changes no geometry
        SF.set_code(project, oid, key, value)
        return
    if CL.is_field(key):                                                         # IFC/OmniClass classification is also metadata-only
        CL.set_field(project, oid, key, value)
        return
    old_host = m(meta, "host")
    if cat == "wall":
        if key == "height":
            values["height"] = number(value, "height", 0.01, 500)
        elif key == "thickness":
            values["thickness"] = _mm(value, "thickness", 1, 5000)
        elif key == "base_offset":
            values["base_offset"] = _mm(value, "base_offset", -100000, 100000)
        elif key == "material":
            values["material"] = _choice(value, MATERIALS, "material")
        elif key == "level":
            values["level"] = project.level(text(value, "level", 80))["id"]
        elif key == "length":
            new_len = number(value, "length", 0.001, 100000)
            s, e = m(meta, "start"), m(meta, "end")
            d = math.dist(s, e)
            values["end"] = [s[0] + (e[0] - s[0]) / d * new_len, s[1] + (e[1] - s[1]) / d * new_len]
        elif key in ("start_x", "start_y", "end_x", "end_y"):
            end = key.split("_")[0]
            pt = list(m(meta, end))
            pt[0 if key.endswith("x") else 1] = number(value, key, -1e6, 1e6) / 1000.0
            values[end] = pt
        elif key == "angle":                                                       # turn the wall about its start, keeping its length
            s, e = m(meta, "start"), m(meta, "end")
            d, a = math.dist(s, e), math.radians(number(value, "angle", -360, 360))
            values["end"] = [s[0] + d * math.cos(a), s[1] + d * math.sin(a)]
        else:
            raise BadRequest(f"'{key}' is not an editable wall property")
    elif cat in OPENINGS:
        if key == "width":
            values["width"] = _mm(value, "width", 100, 20000)
        elif key == "height":
            values["height"] = _mm(value, "height", 100, 20000)
        elif key == "sill" and cat == "window":
            values["sill"] = _mm(value, "sill", 0, 20000)
        elif key == "offset":
            values["offset"] = number(value, "offset", 0, 100000)
        elif key == "material":
            values["material"] = _choice(value, MATERIALS, "material")
        elif key == "host":
            new_host = text(value, "host", 80)
            wall_frame(project, new_host)                                           # raises unless it is a wall
            values["host"] = new_host
        elif key == "hinge" and cat in OPENINGS:
            values["hinge"] = _choice(value, ("start", "end"), "hinge")
        elif key == "swing" and cat == "door":
            values["swing"] = 1 if _choice(value, ("left", "right"), "swing") == "left" else -1
        else:
            raise BadRequest(f"'{key}' is not an editable {cat} property")
    elif cat in ("floor", "roof"):
        if key == "type" and cat == "floor":
            values["type"] = _choice(value, FLOOR_TYPES, "type")
        elif key == "thickness":
            values["thickness"] = _mm(value, "thickness", 10, 5000)
        elif key == "offset":
            values["offset"] = _mm(value, "offset", -100000, 100000)
        elif key == "level":
            values["level"] = project.level(text(value, "level", 80))["id"]
        elif key == "material":
            values["material"] = _choice(value, MATERIALS, "material")
        elif key == "roof" and cat == "roof":                                    # change the roof type; a pitched one needs a rectangular outline
            kind = _choice(value, tuple(ROOF_TYPES), "roof type")
            if kind != "flat":
                _rectangle([list(q) for q in m(meta, "boundary")])
            values.update({"roof": kind, "type": ROOF_TYPES[kind]})
        elif key == "pitch" and cat == "roof" and m(meta, "roof") != "flat":
            values["pitch"] = number(value, "pitch", 1, 80)
        elif key == "slope" and cat == "roof" and m(meta, "roof") != "flat":      # the same pitch, as a percentage rise over run
            values["pitch"] = math.degrees(math.atan(number(value, "slope", 0.0, 1e6) / 100.0))
            if not 1.0 - 1e-9 <= values["pitch"] <= 80.0 + 1e-9:
                raise BadRequest("A roof slope must lie between 1.75 % (1°) and 567 % (80°)")
        else:
            raise BadRequest(f"'{key}' is not an editable {cat} property")
    elif cat == "room":
        from . import rooms as R
        values.update(R.set_property(project, oid, key, value))
    elif cat == "structural" and is_column(meta):
        if key in ("width", "depth"):
            values[key] = _mm(value, key, 10, 5000)
        elif key in ("base", "top"):
            values[key] = number(value, key, -100, 500)
        elif key == "rotation":
            values["rotation"] = math.radians(number(value, "rotation", -360, 360))
        elif key in ("x", "y"):
            pt = list(m(meta, "point"))
            pt[0 if key == "x" else 1] = number(value, key, -1e6, 1e6) / 1000.0
            values["point"] = pt
        elif key == "level":
            values["level"] = project.level(text(value, "level", 80))["id"]
        elif key == "material":
            values["material"] = _choice(value, BEAM_MATERIALS, "material")
        else:
            raise BadRequest(f"'{key}' is not an editable column property")
    elif cat == "structural":
        if key in ("profile", "size", "roll") or key.startswith("dim:"):
            values.update(_beam_profile_values(meta, key, value))
        elif key in ("width", "depth") and m(meta, "profile", "rect") != "rect":
            raise BadRequest("A profile beam takes its width and depth from the profile: change the size or the dimensions of the profile")
        elif key == "width":
            values["width"] = _mm(value, "width", 10, 5000)
        elif key == "depth":
            values["depth"] = _mm(value, "depth", 10, 5000)
        elif key == "top":
            values["top"] = number(value, "top", 0.1, 500)
        elif key in ("start_x", "start_y", "end_x", "end_y"):
            end = key.split("_")[0]
            pt = list(m(meta, end))
            pt[0 if key.endswith("x") else 1] = number(value, key, -1e6, 1e6) / 1000.0
            values[end] = pt
        elif key == "level":
            values["level"] = project.level(text(value, "level", 80))["id"]
        elif key == "material":
            values["material"] = _choice(value, BEAM_MATERIALS, "material")
            if values["material"] != "Steel" and m(meta, "profile", "rect") != "rect":
                raise BadRequest("Rolled and tube profiles are steel: make the beam rectangular first to give it another material")
        else:
            raise BadRequest(f"'{key}' is not an editable beam property")
    elif cat in ("gutter", "downpipe"):
        from . import gutters as GU
        values.update(GU.set_gutter_property(project, oid, meta, key, value) if cat == "gutter" else GU.set_downpipe_property(project, meta, key, value))
    elif cat == "stair":
        from . import stairs as SR
        values.update(SR.set_property(project, meta, key, value))
    elif cat == "generic":
        from . import families as FM
        if key.startswith("param:"):
            FM.set_instance_parameter(project, oid, key[6:], value)
            return
        values.update(FM.set_property(project, oid, meta, key, value))
    elif cat in FORMS:
        from . import forms as FX
        changed = FX.set_property(project, oid, meta, key, value)
        if changed is None:                                                         # the solid/void switch rebuilt it already
            return
        values.update(changed)
    elif cat == "foundation":
        from . import foundations as FD
        values.update(FD.set_property(project, oid, meta, key, value))
    elif cat == "array":
        from . import arrays as AY
        AY.set_property(project, oid, key, value)
        return
    elif cat == "grid":
        from . import datums as DT
        part = {"start_x": ("start", 0), "start_y": ("start", 1), "end_x": ("end", 0), "end_y": ("end", 1)}.get(key)
        if key in ("length", "angle", "bubble"):                                     # drawing units are millimetres' worth of metres here: the panel speaks mm
            DT.edit_grid(project, oid, {key: number(value, key, 0.0, 1e9) if key == "length" else value}, 0.001 if key == "length" else 1.0)
            return
        if part is None:
            raise BadRequest(f"'{key}' is not an editable grid property")
        pt = list(m(meta, part[0]))
        pt[part[1]] = number(value, key, -1e6, 1e6) / 1000.0
        DT.set_grid(project, oid, {part[0]: pt})
        return
    else:
        raise BadRequest(f"'{key}' cannot be edited on this element")
    _attach(project, oid, values)
    if cat == "gutter":
        from . import gutters as GU
        GU.share(project, oid, values)                                              # the run's settings are the whole run's
    regenerate(project, oid)
    if key == "host" and cat in OPENINGS and old_host and old_host != values["host"] and alive(project, old_host):
        regenerate_wall(project, old_host)                                         # the wall it left is whole again


def move(project: Any, oid: str, dx: float, dy: float, dz: float) -> None:
    """Move an element by changing its parameters (metres), so the move survives regeneration."""
    meta = meta_of(project, oid)
    cat = m(meta, "category")
    if cat == "wall":
        s, e = m(meta, "start"), m(meta, "end")
        _attach(project, oid, {"start": [s[0] + dx, s[1] + dy], "end": [e[0] + dx, e[1] + dy], "base_offset": float(m(meta, "base_offset", 0.0)) + dz})
    elif cat in OPENINGS:
        host = wall_frame(project, m(meta, "host"))
        along = dx * math.cos(host["angle"]) + dy * math.sin(host["angle"])
        values = {"offset": float(m(meta, "offset")) + along}
        if cat == "window":
            values["sill"] = max(0.0, float(m(meta, "sill", 0.0)) + dz)
        _attach(project, oid, values)
    elif cat in ("floor", "roof"):
        _attach(project, oid, {"boundary": [[q[0] + dx, q[1] + dy] for q in m(meta, "boundary")], "offset": float(m(meta, "offset", 0.0)) + dz})
    elif cat == "room":
        from . import rooms as R
        R.move(project, oid, dx, dy)
    elif cat == "structural" and is_column(meta):
        x, y = m(meta, "point")
        _attach(project, oid, {"point": [x + dx, y + dy], "base": float(m(meta, "base", 0.0)) + dz, "top": float(m(meta, "top")) + dz})
    elif cat == "structural":
        s, e = m(meta, "start"), m(meta, "end")
        _attach(project, oid, {"start": [s[0] + dx, s[1] + dy], "end": [e[0] + dx, e[1] + dy], "top": float(m(meta, "top")) + dz})
    elif cat == "gutter":
        raise BadRequest("A gutter hangs on its roof's eave: move the roof and the gutter goes with it")
    elif cat == "downpipe":
        from . import gutters as GU
        GU.slide_downpipe(project, oid, dx, dy)
    elif cat == "stair":
        s = m(meta, "start")
        _attach(project, oid, {"start": [s[0] + dx, s[1] + dy], "base_offset": float(m(meta, "base_offset", 0.0)) + dz, "top_offset": float(m(meta, "top_offset", 0.0)) + dz})
    elif cat == "generic":
        from . import families as FM
        FM.move(project, oid, meta, dx, dy, dz)
    elif cat in FORMS:
        from . import forms as FX
        FX.move(project, oid, meta, dx, dy, dz)
    elif cat == "foundation":
        from . import foundations as FD
        FD.move(project, oid, meta, dx, dy, dz)
    elif cat == "grid":
        s, e = m(meta, "start"), m(meta, "end")
        _attach(project, oid, {"start": [s[0] + dx, s[1] + dy], "end": [e[0] + dx, e[1] + dy]})
        return
    elif cat == "array":
        raise BadRequest("An array has no place of its own: move its source and the array follows (or release the array first)")
    else:
        project.model.move(oid, dx, dy, dz)
        return
    regenerate(project, oid)


def stretch_wall(project: Any, oid: str, start: tuple[float, float] | None, end: tuple[float, float] | None) -> None:
    """Move one or both ends of a wall to new places (metres), as one change: its joints with the walls it meets, and the doors and windows
    it hosts, are re-derived once the ends are final. Anything that would not fit afterwards (a door left past the end) refuses the whole edit."""
    meta = meta_of(project, oid)
    if m(meta, "category") != "wall":
        raise BadRequest("Only a wall has ends to stretch")
    values: dict[str, Any] = {}
    if start is not None:
        values["start"] = [start[0], start[1]]
    if end is not None:
        values["end"] = [end[0], end[1]]
    if not values:
        raise BadRequest("Give the new start, the new end, or both")
    _attach(project, oid, values)
    regenerate_wall(project, oid)


def _turn(point: Any, angle: float, centre: tuple[float, float]) -> list[float]:
    c, s = math.cos(angle), math.sin(angle)
    dx, dy = point[0] - centre[0], point[1] - centre[1]
    return [centre[0] + dx * c - dy * s, centre[1] + dx * s + dy * c]


def rotate(project: Any, oid: str, angle: float, centre: tuple[float, float], rebuild: bool = True) -> None:
    """Turn an element about a point in plan (``angle`` in radians, counter-clockwise; ``centre`` in metres) by changing its parameters, so the
    turn survives regeneration. A door or window turns with its wall, so it is refused on its own."""
    meta = meta_of(project, oid)
    cat = m(meta, "category")
    if cat in OPENINGS:
        raise BadRequest(f"A {cat} turns with its wall: select the wall to rotate it")
    if cat in ("gutter", "downpipe"):
        raise BadRequest(f"A {cat} turns with its roof: select the roof to rotate it")
    if cat == "array":
        raise BadRequest("An array has no place of its own: turn its source and the array follows (or release the array first)")
    if cat in ("wall", "grid") or (cat == "structural" and not is_column(meta)):
        _attach(project, oid, {"start": _turn(m(meta, "start"), angle, centre), "end": _turn(m(meta, "end"), angle, centre)})
    elif cat in ("floor", "roof", "room"):
        _attach(project, oid, {"boundary": [_turn(q, angle, centre) for q in m(meta, "boundary")]})
    elif cat == "structural":
        _attach(project, oid, {"point": _turn(m(meta, "point"), angle, centre), "rotation": float(m(meta, "rotation", 0.0)) + angle})
    elif cat == "stair":
        _attach(project, oid, {"start": _turn(m(meta, "start"), angle, centre), "angle": float(m(meta, "angle")) + angle})
    elif cat == "generic":
        _attach(project, oid, {"point": _turn(m(meta, "point"), angle, centre), "rotation": float(m(meta, "rotation", 0.0)) + angle})
    elif cat in FORMS:
        from . import forms as FX
        FX.rotate(project, oid, meta, angle, centre)
    elif cat == "foundation":
        from . import foundations as FD
        FD.rotate(project, oid, meta, angle, centre)
    else:
        raise BadRequest(f"A {cat} cannot be rotated in plan")
    if rebuild and cat != "grid":
        regenerate(project, oid)


def rotate_many(project: Any, ids: list[str], angle: float, centre: tuple[float, float]) -> None:
    """Turn several elements together: every parameter is changed first and the elements are rebuilt after, so walls that turn together are
    joined as they end up, not as they pass through."""
    from . import gutters as GU
    chosen = set(ids)
    ids = [oid for oid in ids if not (m(meta_of(project, oid), "category") in ("gutter", "downpipe") and chosen & set(GU.hosts_of(project, oid)))]     # they turn with their roof
    for oid in ids:
        rotate(project, oid, angle, centre, rebuild=False)
    for oid in ids:
        if m(meta_of(project, oid), "category") != "grid":
            regenerate(project, oid)


def remove(project: Any, oid: str) -> None:
    """Delete an element. A wall takes its doors and windows with it; a door or window leaves its wall whole again."""
    meta = meta_of(project, oid)
    cat = m(meta, "category")
    if cat == "array":
        from . import arrays as AY
        AY.remove(project, oid)
        return
    if cat == "wall":
        f = wall_frame(project, oid)
        affected = set(m(meta, "joins") or ()) | (_touching(_wall_table(project), oid, str(m(meta, "level")), f["start"], f["end"], f["thickness"])
                                                   if f["length"] > 1e-6 else set())
        for hid in hosted(project, oid):
            project.model.remove(hid)
        project.model.remove(oid)
        _refresh_walls(project, affected - {oid})                                   # the walls it joined are square-ended again
    elif cat in OPENINGS:
        host = m(meta, "host")
        project.model.remove(oid)
        if host and alive(project, host):
            regenerate_wall(project, host)
    else:
        from . import gutters as GU
        run = (m(meta, "roof"), GU.run_key(project, oid, meta)) if cat == "gutter" and alive(project, m(meta, "roof")) else None
        for did in GU.dependents(project, oid):                                    # a roof takes its gutters and their downpipes, a gutter its downpipes
            project.model.remove(did)
        project.model.remove(oid)
        if run:
            GU.settle_after_delete(project, *run)                                   # its neighbours lose the mitre and get a stop end


# ------------------------------------------------------------------------------------------------------------------------ plan
def _rect(a: tuple[float, float], angle: float, x0: float, x1: float, half: float, f: float) -> list[list[float]]:
    """The footprint of the stretch x0..x1 along a wall that starts at ``a``, ``half`` either side of its axis, in drawing units."""
    c, s = math.cos(angle), math.sin(angle)
    nx, ny = -s * half, c * half
    p0, p1 = (a[0] + c * x0, a[1] + s * x0), (a[0] + c * x1, a[1] + s * x1)
    return [[(p0[0] + nx) * f, (p0[1] + ny) * f], [(p1[0] + nx) * f, (p1[1] + ny) * f], [(p1[0] - nx) * f, (p1[1] - ny) * f], [(p0[0] - nx) * f, (p0[1] - ny) * f]]


def _wall_piece(fr: Mapping[str, Any], x0: float, x1: float, f: float) -> list[list[float]]:
    """The footprint of the stretch x0..x1 of a wall, with the slanted end faces of its joints where the stretch reaches an end."""
    (sl, sr), (el, er) = fr["ends"]
    half, length = fr["thickness"] / 2, fr["length"]
    c, s = math.cos(fr["angle"]), math.sin(fr["angle"])
    at_start, at_end = x0 <= 1e-9, x1 >= length - 1e-9
    corners = [(x0 - sl if at_start else x0, half), (x1 + el if at_end else x1, half),             # +y face: start, end
               (x1 + er if at_end else x1, -half), (x0 - sr if at_start else x0, -half)]             # -y face: end, start
    return [[(fr["start"][0] + c * x - s * y) * f, (fr["start"][1] + s * x + c * y) * f] for x, y in corners]


def plan_items(project: Any, level_id: str, f: float) -> list[dict[str, Any]]:
    """What the model shows on the plan of a level, in drawing units (``f`` converts metres). Read from parameters on every call, so it
    can never disagree with the 3D model."""
    out: list[dict[str, Any]] = []
    walls: dict[str, dict[str, Any]] = {}
    for oid in element_ids(project):
        obj = project.model.get(oid)
        meta = obj["metadata"]
        cat = m(meta, "category")
        if cat == "downpipe":
            from . import gutters as GU
            try:
                if GU.pipe_on_level(project, oid, level_id):
                    out.append(GU.pipe_plan_item(project, oid, f))
            except BadRequest:
                pass                                                                     # a downpipe without its gutter is reported by the model check, not drawn
            continue
        if m(meta, "level") != level_id:
            continue
        if cat == "gutter":
            from . import gutters as GU
            try:
                out.append(GU.plan_item(project, oid, f))
            except BadRequest:
                pass
        elif cat == "wall":
            fr = wall_frame(project, oid)
            if fr["length"] < 1e-9:
                continue
            walls[oid] = fr
            axis = {"a": [fr["start"][0] * f, fr["start"][1] * f], "b": [fr["end"][0] * f, fr["end"][1] * f], "thickness": fr["thickness"] * f}
            cuts = sorted(opening_span(project.model.get(h)["metadata"])[:2] for h in hosted(project, oid)
                          if opening_span(project.model.get(h)["metadata"])[2] <= CUT_HEIGHT < opening_span(project.model.get(h)["metadata"])[3])
            cursor = 0.0
            for x0, x1 in cuts + [(fr["length"], fr["length"])]:
                if x0 - cursor > 1e-9:
                    out.append({"id": oid, "category": "wall", "name": obj["name"], "polygon": _wall_piece(fr, cursor, x0, f), "axis": axis})
                cursor = max(cursor, x1)
        elif cat == "floor":
            item = {"id": oid, "category": "floor", "name": obj["name"], "polygon": [[q[0] * f, q[1] * f] for q in (m(meta, "cut_outline") or m(meta, "boundary"))]}
            if m(meta, "cut"):
                item["holes"] = [[[q[0] * f, q[1] * f] for q in h] for h in m(meta, "cut")]
            out.append(item)
        elif cat in FORMS:
            from . import forms as FX
            out.append(FX.plan_item(project, oid, f))
        elif cat == "foundation":
            from . import foundations as FD
            out.append(FD.plan_item(project, oid, f))
        elif cat == "structural" and is_column(meta):
            x, y = m(meta, "point")
            ang = float(m(meta, "rotation", 0.0))
            out.append({"id": oid, "category": "column", "name": obj["name"],
                        "polygon": _column_outline((x, y), ang, float(m(meta, "width")), float(m(meta, "depth")), f)})
        elif cat == "structural":
            s, e = m(meta, "start"), m(meta, "end")
            ang = math.atan2(e[1] - s[1], e[0] - s[0])
            out.append({"id": oid, "category": "structural", "name": obj["name"], "polygon": _rect(tuple(s), ang, 0.0, math.dist(s, e), float(m(meta, "width")) / 2, f)})
        elif cat == "stair":
            from . import stairs as SR
            out.append(SR.plan_item(project, oid, f))
        elif cat == "generic":
            from . import families as FM
            out.append(FM.plan_item(project, oid, f))
        elif cat == "room":
            from . import rooms as R
            poly = R.boundary_of(meta)
            met = R.metrics(poly)
            out.append({"id": oid, "category": "room", "name": obj["name"], "polygon": [[q[0] * f, q[1] * f] for q in poly],
                        "label": {"name": obj["name"], "number": str(m(meta, "number", "")), "area_m2": met["area"], "at": [met["centre"][0] * f, met["centre"][1] * f]}})
    from . import datums as DT
    out.extend(DT.grid_item(project, oid, f) for oid in DT.grids(project))                    # grids belong to no level: every plan shows them
    for oid in element_ids(project):
        obj = project.model.get(oid)
        meta = obj["metadata"]
        cat = m(meta, "category")
        if cat not in OPENINGS or m(meta, "host") not in walls:
            continue
        fr = walls[m(meta, "host")]
        x0, x1, sill, head = opening_span(meta)
        item: dict[str, Any] = {"id": oid, "category": cat, "name": obj["name"], "polygon": _rect(fr["start"], fr["angle"], x0, x1, fr["thickness"] / 2, f),
                                "cut": sill <= CUT_HEIGHT < head}
        item["symbol"] = _symbol(cat, meta, fr, x0, x1, f)
        out.append(item)
    return out


def _column_outline(centre: tuple[float, float], angle: float, width: float, depth: float, f: float) -> list[list[float]]:
    c, s = math.cos(angle), math.sin(angle)
    return [[(centre[0] + c * dx - s * dy) * f, (centre[1] + s * dx + c * dy) * f]
            for dx, dy in ((-width / 2, -depth / 2), (width / 2, -depth / 2), (width / 2, depth / 2), (-width / 2, depth / 2))]


def _symbol(cat: str, meta: Mapping[str, Any], fr: Mapping[str, Any], x0: float, x1: float, f: float) -> dict[str, Any]:
    c, s = math.cos(fr["angle"]), math.sin(fr["angle"])
    at = lambda x, y: ((fr["start"][0] + c * x - s * y) * f, (fr["start"][1] + s * x + c * y) * f)       # noqa: E731
    if cat == "window":
        return {"kind": "window", "a": list(at(x0, 0.0)), "b": list(at(x1, 0.0))}
    swing = int(m(meta, "swing", 1))
    face = swing * fr["thickness"] / 2
    hinge_x, free_x = (x0, x1) if m(meta, "hinge", "start") == "start" else (x1, x0)
    hinge, jamb = at(hinge_x, face), at(free_x, face)
    leaf_end = at(hinge_x, face + swing * (x1 - x0))
    radius = (x1 - x0) * f
    a_leaf = math.atan2(leaf_end[1] - hinge[1], leaf_end[0] - hinge[0])
    a_jamb = math.atan2(jamb[1] - hinge[1], jamb[0] - hinge[0])
    start, end = (a_leaf, a_jamb) if G.cross((math.cos(a_leaf), math.sin(a_leaf)), (math.cos(a_jamb), math.sin(a_jamb))) > 0 else (a_jamb, a_leaf)
    while end <= start:
        end += 2 * math.pi
    return {"kind": "door", "hinge": list(hinge), "leaf_end": list(leaf_end), "radius": radius, "start_angle": start, "end_angle": end}


# -------------------------------------------------------------------------------------------------------------------- properties
def _levels(project: Any) -> list[dict[str, str]]:
    return [{"value": l["id"], "label": l["name"]} for l in project.manifest["levels"]]


def _choices(values: tuple[str, ...]) -> list[dict[str, str]]:
    return [{"value": v, "label": v} for v in values]


def properties(project: Any, oid: str, relations: bool = False) -> dict[str, Any]:
    """The Properties panel for any element: its editable parameters first, then read-only figures, then what every element shares
    (identification and custom properties; with ``relations`` also the links to what it is connected to, which costs a little more)."""
    from . import relations as RL
    return RL.augment(project, oid, _properties(project, oid), relations)


# ---------------------------------------------------------------------------------------------------------------------- solid status
class Solid(NamedTuple):
    """What can honestly be said about an element's volume. ``state`` is one of ``solid`` (a valid closed solid, ``volume`` in m³), ``surface`` (a valid open
    surface or shell: no volume), ``none`` (an element with no geometry of its own: a grid, a group), ``unresolved`` (an instance whose geometry has not been
    built yet) or ``invalid`` (geometry that SGEO refuses to measure; ``message`` says why). A missing volume is never reported as zero."""
    state: str
    volume: float | None
    message: str


def solid_status(project: Any, oid: str) -> Solid:
    """Classify an element's geometry and measure its volume once per model revision (the answer is cached until the revision moves)."""
    key = ("solid-status", oid)
    cached = project._cache.get(key)
    if cached is not None:
        return cached
    obj = project.model.get(oid)
    geometry = obj.get("geometry")
    cat = str(m(obj["metadata"], "category", "generic"))
    if geometry is None:
        if obj.get("family_id"):
            result = Solid("unresolved", None, "The geometry of this family instance has not been built yet")
        elif cat == "grid":
            result = Solid("none", None, "Not applicable")
        else:
            result = Solid("none", None, "No volumetric geometry")
    else:
        kind = geometry.get("kind")
        try:
            if kind == "mesh":
                from src.utils.spatial_formats.sgeo.analysis import analyze_mesh
                if not analyze_mesh(geometry).is_closed_oriented_surface:
                    raise _OpenSurface()
            result = Solid("solid", float(project.model.volume(oid, unit="m")), "")
        except _OpenSurface:
            result = Solid("surface", None, "Open surface: it encloses no volume")
        except SpatialFormatError as exc:
            result = Solid("invalid", None, str(exc))
    project._cache[key] = result
    return result


class _OpenSurface(Exception):
    pass


def volume_row(project: Any, oid: str) -> dict[str, Any]:
    """The Properties row for an element's volume: the figure for a valid solid, otherwise the reason there is none."""
    st = solid_status(project, oid)
    if st.state == "solid":
        return {"label": "Volume", "type": "readonly", "value": f"{st.volume:.3f} m³"}
    row: dict[str, Any] = {"label": "Volume", "type": "readonly", "value": st.message if st.state != "invalid" else f"Invalid geometry: {st.message}"}
    if st.state == "invalid":
        row["error"] = st.message
    return row


def _properties(project: Any, oid: str) -> dict[str, Any]:
    obj = project.model.get(oid) if alive(project, oid) else None
    if obj is None:
        raise NotFound(f"Element {oid} does not exist")
    meta = obj["metadata"]
    cat = str(m(meta, "category", "generic"))
    out: dict[str, Any] = {"ref": {"model": oid}, "title": obj["name"] or cat.title(), "category": cat, "kind": "model", "groups": [], "top": []}
    name_row = {"key": "name", "label": "Name", "type": "text", "value": obj["name"]}
    mm = lambda v: f"{v * 1000:,.0f} mm"                                                    # noqa: E731
    volume = volume_row(project, oid) if cat not in ("room", "void") else {}                # a room has no solid; a void's volume is not a quantity
    if cat in ("stair", "generic", "grid", "gutter", "downpipe", "foundation", "array", *FORMS):
        if cat == "array":
            from . import arrays as AY
            return AY.properties(project, oid, out)
        if cat in ("gutter", "downpipe"):
            from . import gutters as GU
            GU.properties(project, oid, out, volume)
        elif cat == "foundation":
            from . import foundations as FD
            FD.properties(project, oid, out, volume)
        elif cat in FORMS:
            from . import forms as FX
            FX.properties(project, oid, out, {} if cat == "void" else volume)
        elif cat == "stair":
            from . import stairs as SR
            SR.properties(project, oid, out, volume)
        elif cat == "generic" and obj.get("family_id"):
            from . import families as FM
            FM.properties(project, oid, out, volume)
        elif cat == "generic":                                                      # an object that is not a family instance (a group, an imported object)
            out["top"] = [{"label": "Type", "type": "readonly", "value": m(meta, "type", "Generic Model")}, name_row]
            out["groups"] = [{"title": "Geometry", "rows": [volume]}]
        else:
            from . import datums as DT
            DT.grid_properties(project, oid, out, volume)
            out["top"][1] = name_row | {"label": "Label"}
        out["groups"].append({"title": "Identity", "rows": [name_row]})
        return out
    if cat == "wall":
        fr = wall_frame(project, oid)
        start, end = fr["start"], fr["end"]
        top = [{"label": "Type", "type": "readonly", "value": m(meta, "type", "Basic Wall")},
               {"key": "level", "label": "Level", "type": "choice", "value": m(meta, "level"), "choices": _levels(project)},
               {"key": "height", "label": "Height", "type": "number", "unit": "m", "value": fr["height"]},
               {"key": "thickness", "label": "Thickness", "type": "number", "unit": "mm", "value": round(fr["thickness"] * 1000, 6)},
               {"key": "material", "label": "Material", "type": "choice", "value": m(meta, "material", "Concrete"), "choices": _choices(MATERIALS)}]
        open_area = sum((s[1] - s[0]) * (s[3] - s[2]) for s in (opening_span(project.model.get(h)["metadata"]) for h in hosted(project, oid)))
        out["top"] = top
        out["groups"] = [
            {"title": "Geometry", "rows": [
                {"key": "length", "label": "Length", "type": "number", "unit": "m", "value": fr["length"]},
                {"key": "start_x", "label": "Start X", "type": "number", "unit": "mm", "value": round(start[0] * 1000, 6)},
                {"key": "start_y", "label": "Start Y", "type": "number", "unit": "mm", "value": round(start[1] * 1000, 6)},
                {"key": "end_x", "label": "End X", "type": "number", "unit": "mm", "value": round(end[0] * 1000, 6)},
                {"key": "end_y", "label": "End Y", "type": "number", "unit": "mm", "value": round(end[1] * 1000, 6)},
                {"key": "angle", "label": "Angle", "type": "number", "unit": "°", "value": round(math.degrees(fr["angle"]), 6)},
                {"label": "Area", "type": "readonly", "value": f"{fr['length'] * fr['height'] - open_area:.3f} m²"},
                volume]},
            {"title": "Constraints", "rows": [
                {"label": "Base Constraint", "type": "readonly", "value": project.level(m(meta, "level"))["name"]},
                {"key": "base_offset", "label": "Base Offset", "type": "number", "unit": "mm", "value": float(m(meta, "base_offset", 0.0)) * 1000}]},
            {"title": "Identity", "rows": [name_row]}]
        return out
    out["top"] = [{"label": "Type", "type": "readonly", "value": m(meta, "type", cat.title())}]
    if cat in OPENINGS:
        host = project.model.get(m(meta, "host"))["name"] if alive(project, m(meta, "host", "")) else "(missing)"
        w, h = float(m(meta, "width")), float(m(meta, "height"))
        rows: list[dict[str, Any]] = [{"key": "width", "label": "Width", "type": "number", "unit": "mm", "value": round(w * 1000, 6)},
                                      {"key": "height", "label": "Height", "type": "number", "unit": "mm", "value": round(h * 1000, 6)}]
        if cat == "window":
            rows.append({"key": "sill", "label": "Sill Height", "type": "number", "unit": "mm", "value": round(float(m(meta, "sill", 0.0)) * 1000, 6)})
        rows.append({"key": "material", "label": "Material", "type": "choice", "value": m(meta, "material"), "choices": _choices(MATERIALS)})
        out["top"] += rows
        walls = [{"value": w, "label": f"{project.model.get(w)['name']} ({project.level(m(project.model.get(w)['metadata'], 'level'))['name']})"}
                 for w in element_ids(project) if m(project.model.get(w)["metadata"], "category") == "wall"]
        placement = [{"key": "host", "label": "Host wall", "type": "choice", "value": m(meta, "host"), "choices": walls} if alive(project, m(meta, "host", "")) else {"label": "Host", "type": "readonly", "value": host},
                     {"key": "offset", "label": "Offset along wall", "type": "number", "unit": "m", "value": float(m(meta, "offset"))},
                     {"label": "Area", "type": "readonly", "value": f"{w * h:.3f} m²"}]
        placement.append({"key": "hinge", "label": "Hinge", "type": "choice", "value": m(meta, "hinge", "start"), "choices": _choices(("start", "end"))})
        if cat == "door":
            placement += [{"key": "swing", "label": "Swing side", "type": "choice", "value": "left" if int(m(meta, "swing", 1)) > 0 else "right", "choices": _choices(("left", "right"))}]
        out["groups"] = [{"title": "Placement", "rows": placement}, {"title": "Identity", "rows": [name_row]}]
        return out
    if cat in ("floor", "roof"):
        boundary = [tuple(q) for q in m(meta, "boundary")]
        area = abs(G.signed_area(boundary))
        perimeter = sum(math.dist(a, b) for a, b in zip(boundary, boundary[1:] + boundary[:1]))
        if cat == "floor":
            out["top"] = [{"key": "type", "label": "Type", "type": "choice", "value": m(meta, "type", "Generic Floor"), "choices": _choices(FLOOR_TYPES)}]
        out["top"] += [{"key": "level", "label": "Level", "type": "choice", "value": m(meta, "level"), "choices": _levels(project)},
                       {"key": "thickness", "label": "Thickness", "type": "number", "unit": "mm", "value": round(float(m(meta, "thickness")) * 1000, 6)},
                       {"key": "material", "label": "Material", "type": "choice", "value": m(meta, "material"), "choices": _choices(MATERIALS)}]
        if cat == "roof":
            out["top"].insert(0, {"key": "roof", "label": "Roof type", "type": "choice", "value": m(meta, "roof"), "choices": [{"value": k, "label": v} for k, v in ROOF_TYPES.items()]})
        if cat == "roof" and m(meta, "roof") != "flat":
            pitch = float(m(meta, "pitch"))
            out["top"] += [{"key": "pitch", "label": "Pitch", "type": "number", "unit": "°", "value": round(pitch, 6)},
                           {"key": "slope", "label": "Slope", "type": "number", "unit": "%", "value": round(math.tan(math.radians(pitch)) * 100, 6)}]
        extra = []
        if cat == "roof" and m(meta, "roof") != "flat":
            extra = [{"label": "Rise to ridge", "type": "readonly", "value": f"{roof_rise(meta):.3f} m"}]
        out["groups"] = [{"title": "Geometry", "rows": [{"label": "Plan area", "type": "readonly", "value": f"{area:.3f} m²"},
                                                        {"label": "Perimeter", "type": "readonly", "value": f"{perimeter:.3f} m"}, *extra, volume]},
                         {"title": "Constraints", "rows": [{"key": "offset", "label": "Level Offset", "type": "number", "unit": "mm", "value": float(m(meta, "offset", 0.0)) * 1000}]},
                         {"title": "Identity", "rows": [name_row]}]
        return out
    if cat == "room":
        from . import rooms as R
        d = R.data(project, oid)
        out["top"] = [{"label": "Type", "type": "readonly", "value": "Room"},
                      {"key": "number", "label": "Number", "type": "text", "value": d["number"]},
                      {"key": "level", "label": "Level", "type": "choice", "value": m(meta, "level"), "choices": _levels(project)},
                      {"key": "height", "label": "Height", "type": "number", "unit": "m", "value": d["height"]}]
        out["groups"] = [{"title": "Geometry", "rows": [{"label": "Area", "type": "readonly", "value": f"{d['area']:.3f} m²"},
                                                        {"label": "Perimeter", "type": "readonly", "value": f"{d['perimeter']:.3f} m"},
                                                        {"label": "Volume", "type": "readonly", "value": f"{d['volume']:.3f} m³ (area × height)"}]},
                         {"title": "Related elements", "rows": [{"label": "Bounding walls", "type": "readonly", "value": str(len(d["walls"]))},
                                                                {"label": "Doors", "type": "readonly", "value": str(len(d["doors"]))},
                                                                {"label": "Windows", "type": "readonly", "value": str(len(d["windows"]))},
                                                                {"label": "Columns inside", "type": "readonly", "value": str(len(d["columns"]))}]},
                         {"title": "Identity", "rows": [name_row]}]
        return out
    if cat == "structural" and is_column(meta):
        out["top"] += [{"key": "level", "label": "Level", "type": "choice", "value": m(meta, "level"), "choices": _levels(project)},
                       {"key": "width", "label": "Width", "type": "number", "unit": "mm", "value": round(float(m(meta, "width")) * 1000, 6)},
                       {"key": "depth", "label": "Depth", "type": "number", "unit": "mm", "value": round(float(m(meta, "depth")) * 1000, 6)},
                       {"key": "material", "label": "Material", "type": "choice", "value": m(meta, "material"), "choices": _choices(BEAM_MATERIALS)}]
        x, y = m(meta, "point")
        out["groups"] = [{"title": "Geometry", "rows": [{"key": "x", "label": "Position X", "type": "number", "unit": "mm", "value": round(x * 1000, 6)},
                                                        {"key": "y", "label": "Position Y", "type": "number", "unit": "mm", "value": round(y * 1000, 6)},
                                                        {"key": "rotation", "label": "Rotation", "type": "number", "unit": "°", "value": round(math.degrees(float(m(meta, "rotation", 0.0))), 6)},
                                                        volume]},
                         {"title": "Constraints", "rows": [{"key": "base", "label": "Base above level", "type": "number", "unit": "m", "value": float(m(meta, "base", 0.0))},
                                                            {"key": "top", "label": "Top above level", "type": "number", "unit": "m", "value": float(m(meta, "top"))}]},
                         {"title": "Identity", "rows": [name_row]}]
        return out
    if cat == "structural":
        from . import profiles as PR
        s, e = m(meta, "start"), m(meta, "end")
        sec = beam_section(meta)
        out["top"] += [{"key": "level", "label": "Level", "type": "choice", "value": m(meta, "level"), "choices": _choices_levels(project)},
                       {"key": "profile", "label": "Profile", "type": "choice", "value": sec["family"],
                        "choices": [{"value": "rect", "label": "Rectangular"}] + [{"value": k, "label": v["label"]} for k, v in PR.FAMILIES.items()]}]
        section_rows: list[dict[str, Any]] = []
        if sec["family"] == "rect":
            out["top"] += [{"key": "width", "label": "Width", "type": "number", "unit": "mm", "value": round(float(m(meta, "width")) * 1000, 6)},
                           {"key": "depth", "label": "Depth", "type": "number", "unit": "mm", "value": round(float(m(meta, "depth")) * 1000, 6)}]
        else:
            sizes = [{"value": n, "label": n} for n in PR.FAMILIES[sec["family"]]["sizes"]] + [{"value": PR.CUSTOM, "label": "Custom"}]
            out["top"] += [{"key": "size", "label": "Size", "type": "choice", "value": sec["size"], "choices": sizes}]
            out["top"] += [{"key": f"dim:{k}", "label": label, "type": "number", "unit": "mm", "value": float(sec["dims"].get(k, 0.0))} for k, label in PR.family_dims(sec["family"])]
            out["top"] += [{"key": "roll", "label": "Roll", "type": "number", "unit": "°", "value": round(sec["roll"], 6)}]
            props = PR.properties(PR.rolled(PR.rings(sec["family"], sec["dims"]), sec["roll"]))
            section_rows = [{"label": "Section area", "type": "readonly", "value": f"{props['area_mm2'] / 100:,.2f} cm²"},
                            {"label": "Second moment (horizontal axis)", "type": "readonly", "value": f"{props['ix_mm4'] / 1e4:,.1f} cm⁴"},
                            {"label": "Overall size", "type": "readonly", "value": "{:.1f} × {:.1f} mm".format(*PR.bounds(PR.rolled(PR.rings(sec["family"], sec["dims"]), sec["roll"])))},
                            {"label": "Section data", "type": "readonly", "value": "nominal table size; radii not modelled"}]
        out["top"] += [{"key": "material", "label": "Material", "type": "choice", "value": m(meta, "material"), "choices": _choices(BEAM_MATERIALS)}]
        out["groups"] = [{"title": "Geometry", "rows": [{"key": "start_x", "label": "Start X", "type": "number", "unit": "mm", "value": round(s[0] * 1000, 6)},
                                                        {"key": "start_y", "label": "Start Y", "type": "number", "unit": "mm", "value": round(s[1] * 1000, 6)},
                                                        {"key": "end_x", "label": "End X", "type": "number", "unit": "mm", "value": round(e[0] * 1000, 6)},
                                                        {"key": "end_y", "label": "End Y", "type": "number", "unit": "mm", "value": round(e[1] * 1000, 6)},
                                                        {"label": "Length", "type": "readonly", "value": f"{math.dist(s, e):.3f} m"}, *section_rows, volume]},
                         {"title": "Constraints", "rows": [{"key": "top", "label": "Top above level", "type": "number", "unit": "m", "value": float(m(meta, "top"))}]},
                         {"title": "Identity", "rows": [name_row]}]
        return out
    out["top"] = [name_row]
    out["groups"] = [{"title": "Geometry", "rows": [volume]}]
    return out


def _choices_levels(project: Any) -> list[dict[str, str]]:
    return _levels(project)


# ---------------------------------------------------------------------------------------------------------------------- checks
def issue(severity: str, ref: str, message: str, category: str) -> dict[str, str]:
    """One finding of a model check. ``category`` says what kind of problem it is: references, hosting, parameters, consistency, geometry,
    associations or documentation."""
    return {"severity": severity, "ref": ref, "message": message, "category": category}


def check(project: Any) -> list[dict[str, str]]:
    """Consistency problems in the parameters (not regulatory checks)."""
    issues: list[dict[str, str]] = []
    level_ids = {l["id"] for l in project.manifest["levels"]}
    for oid in element_ids(project):
        obj = project.model.get(oid)
        meta = obj["metadata"]
        cat = m(meta, "category")
        label = obj["name"] or str(cat).title()
        if cat in SPECIAL and m(meta, "level") not in level_ids:
            issues.append(issue("error", oid, f"{label} refers to a level that no longer exists", "references"))
        if cat not in ("room", "void", "grid") and obj.get("geometry") is not None:
            st = solid_status(project, oid)
            if st.state == "invalid":
                issues.append(issue("error", oid, f"{label} has invalid solid geometry: {st.message}", "geometry"))
        if cat == "wall":
            s, e = m(meta, "start"), m(meta, "end")
            if s and e and math.dist(s, e) < 1e-6:
                issues.append(issue("error", oid, "Wall has zero length", "parameters"))
        elif cat in OPENINGS:
            host = str(m(meta, "host", ""))
            if not alive(project, host):
                issues.append(issue("error", oid, f"{label} has lost its wall", "hosting"))
            else:
                hm = project.model.get(host)["metadata"]
                if m(hm, "category") != "wall":
                    issues.append(issue("error", oid, f"{label} is hosted by something that is not a wall", "hosting"))
                elif m(hm, "level") != m(meta, "level"):
                    issues.append(issue("error", oid, f"{label} is on a different level from its wall", "consistency"))
                else:
                    x0, x1, z0, z1 = opening_span(meta)
                    length, height = wall_frame(project, host)["length"], float(m(hm, "height"))
                    if x0 < -1e-9 or x1 > length + 1e-9 or z1 > height + 1e-9 or z0 < -1e-9:
                        issues.append(issue("error", oid, f"{label} does not fit inside its wall", "hosting"))
        elif cat in ("floor", "roof"):
            if not G.is_simple_polygon([tuple(q) for q in m(meta, "boundary", [])]):
                issues.append(issue("error", oid, f"{label} has an invalid outline", "parameters"))
        elif cat == "structural" and is_column(meta):
            if float(m(meta, "top")) - float(m(meta, "base", 0.0)) < 0.01:
                issues.append(issue("error", oid, f"{label} has its top at or below its base", "parameters"))
        elif cat == "structural":
            s, e = m(meta, "start"), m(meta, "end")
            if s and e and math.dist(s, e) < 1e-6:
                issues.append(issue("error", oid, "Beam has zero length", "parameters"))
        elif cat in ("gutter", "downpipe"):
            from . import gutters as GU
            issues.extend(GU.issues(project, oid, label, issue))
        elif cat == "foundation":
            from . import foundations as FD
            issues.extend(FD.issues(project, oid, label, issue))
        elif cat == "stair":
            from . import stairs as SR
            if m(meta, "top_level") not in level_ids:
                issues.append(issue("error", oid, f"{label} refers to a top level that no longer exists", "references"))
            else:
                try:
                    SR._validate(SR.frame(project, oid))
                except BadRequest as exc:
                    issues.append(issue("error", oid, f"{label}: {exc.message}", "parameters"))
        elif cat == "generic":
            from . import families as FM
            try:
                FM.family(project, obj.get("family_id"))
            except NotFound:
                issues.append(issue("error", oid, f"{label} refers to a family that no longer exists", "references"))
        elif cat in FORMS:
            from . import forms as FX
            issues.extend(FX.check(project, oid, label))
        elif cat == "room":
            from . import rooms as R
            try:
                R.check_boundary(meta)
            except BadRequest as exc:
                issues.append(issue("error", oid, f"{label}: {exc.message}", "parameters"))
            else:
                if m(meta, "open"):
                    issues.append(issue("warning", oid, f"{label} is no longer enclosed by walls: its outline is as it was when the walls last closed it", "consistency"))
                elif not R.bounding_walls(project, oid):
                    issues.append(issue("warning", oid, f"{label} has no wall along any of its edges, so it is not enclosed", "consistency"))
    return issues
