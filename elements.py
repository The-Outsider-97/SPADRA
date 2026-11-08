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
import re
from typing import Any, Mapping, NamedTuple

from ..tools.errors import BadRequest, NotFound
from ..tools.validation import number, text
from src.utils.spatial_formats.common.errors import SpatialFormatError

from . import geometry as G
from . import sfb as SF
from . import classifications as CL

NS = "spadra"
MATERIALS = ("Concrete", "Timber", "Glass", "Masonry", "Steel", "Plasterboard")
MATERIAL_COLORS = {"Concrete": "#B8BABF", "Timber": "#B3804D", "Glass": "#8CBFE0", "Masonry": "#B86B57", "Steel": "#808FA3", "Plasterboard": "#EBE8E0"}
BEAM_MATERIALS = ("Steel", "Concrete", "Timber")
CUT_HEIGHT = 1.5                      # default cut plane, relative to the associated level (metres)
DOOR_LEAF = 0.05                      # thickness of the drawn door leaf, m
GLASS = 0.03                          # thickness of the drawn glazing panel, m
ROOF_TYPES = {"flat": "Flat Roof", "shed": "Shed Roof", "gable": "Gable Roof", "hip": "Hip Roof"}      # the four everyday roofs; each can be changed to another later
OPENINGS = ("door", "window")
WINDOW_OPERATIONS = ("fixed", "casement")        # a casement has one sash hinged at one jamb; a fixed window does not open
SPECIAL = ("wall", "door", "window", "opening_profile", "floor", "roof", "structural", "room", "stair", "generic", "extrusion", "void", "sweep", "gutter", "downpipe", "foundation")
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


def color(value: Any) -> str | None:
    """A normalized sRGB color, or None to remove an appearance override."""
    if value is None or value == "":
        return None
    if not isinstance(value, str) or re.fullmatch(r"#[0-9a-fA-F]{6}", value) is None:
        raise BadRequest("A material color must be a six-digit HEX value such as #4477AA, or empty to reset")
    return value.upper()


def appearance_color(project: Any, meta: Mapping[str, Any]) -> str | None:
    """Instance override takes precedence over a project-level material appearance."""
    material = m(meta, "material")
    return m(meta, "color") or project.manifest.get("material_colors", {}).get(material) or MATERIAL_COLORS.get(material)


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
            "top": [(float(a), float(b)) for a, b in (m(meta, "top_profile") or ())],
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
    return join_ends(rows, oid, f["start"], f["end"], f["thickness"], corner_modes=project.manifest.get("wall_corners", {}))


CORNER_MODES = ("through_selected", "through_other", "stepped_open", "miter")


def _corner_key(oid: str, start: bool, other: str, other_start: bool) -> str:
    """Stable end-to-end key; wall identity and endpoint do not depend on geometry or view."""
    return "|".join(sorted((f"{oid}:{'start' if start else 'end'}", f"{other}:{'start' if other_start else 'end'}")))


def _corner_mode_for(corner_modes: Mapping[str, str], key: str, oid: str) -> str:
    """Convert canonical first/second wall priority to the wall being evaluated."""
    saved = corner_modes.get(key, "miter")
    if saved in ("stepped_open", "miter"):
        return saved
    first = key.split("|", 1)[0].rsplit(":", 1)[0]
    return "through_selected" if (saved == "first_through") == (first == oid) else "through_other"


def _far_corner_ends(d: G.Point, t: float, other_d: G.Point, other_t: float) -> tuple[float, float] | None:
    """Trim a through-wall to the FAR face of the crossing wall (its butt end stops at our near face).

    This is a true end-face plane intersection, not an outline-only extension; unequal widths and non-right-angle corners
    produce the appropriate slanted end instead of an overlapped square. Values are in the wall's inward local frame.
    """
    if abs(G.cross(other_d, d)) < math.sin(G.MIN_BUTT_ANGLE):
        return None
    nearest = 1 if G.cross(other_d, d) > 0 else -1
    far_face = G.mul(G.left(other_d), -nearest * other_t / 2)
    result = []
    for face_side in (+1, -1):
        hit = G.intersect_lines(G.mul(G.left(d), face_side * t / 2), d, far_face, other_d)
        if hit is None or abs(hit[0]) > G.MITER_LIMIT * max(t, other_t):
            return None
        result.append(-hit[0])
    return result[0], result[1]


def join_ends(table: Mapping[str, _WallRow], oid: str, start: tuple[float, float], end: tuple[float, float], t: float,
              *, corner_modes: Mapping[str, str] | None = None) -> dict[str, Any]:
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
        # At a T (or a cross) whose centre-line consists of two collinear,
        # equally thick walls, those two walls form the uninterrupted run.
        # The other arms butt against its faces.  A radial mitre of all three
        # arms leaves a small triangular void at the node and interrupts the
        # continuous run even though the walls have separate identities.
        through: tuple[str, str, tuple[float, float], float] | None = None
        if len(members) >= 3:
            pairs = []
            for i, (aid, ad, at) in enumerate(members):
                for bid, bd, bt in members[i + 1:]:
                    if abs(G.cross(ad, bd)) <= 1e-7 and G.dot(ad, bd) < -1 + 1e-7 and abs(at - bt) <= 1e-7:
                        pairs.append((min(aid, bid), max(aid, bid), ad, at))
            if pairs:
                through = min(pairs, key=lambda pair: (pair[0], pair[1]))
        style_ends: tuple[float, float] | None = None
        if len(members) == 2 and corner_modes:
            other_id, other_d, other_t = members[1]
            other_row = table[other_id]
            other_at_start = math.dist(other_row.start, point) <= JOIN_TOLERANCE
            key = _corner_key(oid, at_start, other_id, other_at_start)
            if key in corner_modes:
                mode = _corner_mode_for(corner_modes, key, oid)
                if mode == "stepped_open" or mode == "through_other":
                    style_ends = G.butt_ends(d, t, point, other_d, point, other_t)
                elif mode == "through_selected":
                    style_ends = _far_corner_ends(d, t, other_d, other_t)
                # A saved miter uses the ordinary exact miter evaluator.
        if style_ends is not None:
            left_right: tuple[float, float] | None = style_ends
        elif through and oid in through[:2]:
            left_right: tuple[float, float] | None = (0.0, 0.0)
        elif through:
            left_right = G.butt_ends(d, t, point, through[2], point, through[3])
        else:
            left_right = G.miter_node(members)[oid] if len(members) > 1 else None
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


def wall_corner_controls(project: Any, oid: str, scale: float = 1.0,
                         table: Mapping[str, _WallRow] | None = None) -> list[dict[str, Any]]:
    """Two-wall, non-collinear endpoint corners, with one contextual control per end.

    T/cross junctions are excluded: those use the multi-wall priority resolver and
    must not be silently converted into two-wall schematic corner treatments.
    """
    fr = wall_frame(project, oid)
    table = table if table is not None else _wall_table(project)
    level = str(m(fr["meta"], "level"))
    styles = project.manifest.get("wall_corners", {})
    out = []
    for at_start, point, opposite in ((True, fr["start"], fr["end"]), (False, fr["end"], fr["start"])):
        d = G.unit(G.sub(opposite, point))
        matches = []
        for other, row in table.items():
            if other == oid or row.level != level:
                continue
            for other_at_start, near, far in ((True, row.start, row.end), (False, row.end, row.start)):
                if math.dist(near, point) <= JOIN_TOLERANCE:
                    other_d = G.unit(G.sub(far, near))
                    matches.append((other, other_at_start, other_d))
                    break
        if len(matches) != 1:
            continue
        other, other_at_start, other_d = matches[0]
        if abs(G.cross(d, other_d)) < math.sin(G.MIN_BUTT_ANGLE):
            continue
        key = _corner_key(oid, at_start, other, other_at_start)
        out.append({"endpoint": "start" if at_start else "end", "other": other,
                    "point": [round(point[0] * scale, 9), round(point[1] * scale, 9)],
                    "mode": _corner_mode_for(styles, key, oid)})
    return out


def set_wall_corner_mode(project: Any, oid: str, other: str, endpoint: str, mode: Any) -> None:
    """Persist the selected corner policy with stable wall+end references, then rebuild both independent solids."""
    if mode not in CORNER_MODES:
        raise BadRequest("Wall corner connection must be through_selected, through_other, stepped_open or miter")
    if endpoint not in ("start", "end"):
        raise BadRequest("Wall corner endpoint must be start or end")
    match = next((c for c in wall_corner_controls(project, oid) if c["other"] == other and c["endpoint"] == endpoint), None)
    if match is None:
        raise BadRequest("These walls do not form a two-wall endpoint corner", hint="Select a wall corner in plan; multi-wall junctions use the junction resolver")
    point = tuple(wall_frame(project, oid)[endpoint])
    om = wall_frame(project, other)
    other_at_start = math.dist(om["start"], point) <= JOIN_TOLERANCE
    key = _corner_key(oid, endpoint == "start", other, other_at_start)
    first = key.split("|", 1)[0].rsplit(":", 1)[0]
    canonical = mode if mode in ("stepped_open", "miter") else (
        "first_through" if (mode == "through_selected") == (oid == first) else "second_through")
    styles = project.manifest.setdefault("wall_corners", {})
    if canonical == "miter":
        styles.pop(key, None)
    else:
        styles[key] = canonical
    regenerate_wall(project, oid)


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
    from . import attachments as AT
    AT.refresh(project, oid)                                                    # a wall that follows a roof or level takes its height from it, whatever just changed
    f = wall_frame(project, oid)
    if f["length"] < 1e-6:
        raise BadRequest("A wall needs two different end points")
    if f["thickness"] <= 0 or f["height"] <= 0:
        raise BadRequest("Wall thickness and height must be positive")
    joined = wall_joins(project, oid, table)
    _attach(project, oid, {"ends": [list(joined["ends"][0]), list(joined["ends"][1])], "joins": joined["with"]})
    f = wall_frame(project, oid)
    # A collinear continuation is a real connection even though its resolved
    # end offsets are both zero. Layer extension validation therefore needs
    # endpoint incidence, not merely a non-square miter/butt offset.
    def connected_at(point: tuple[float, float]) -> bool:
        for other in joined["with"]:
            row = table.get(other)
            if row is None or row.level != str(m(f["meta"], "level")):
                continue
            if min(math.dist(point, row.start), math.dist(point, row.end)) <= JOIN_TOLERANCE:
                return True
            if _distance_to_segment(point, row.start, row.end)[0] <= row.thickness / 2 + JOIN_TOLERANCE:
                return True
        return False
    f["connected_ends"] = (connected_at(f["start"]), connected_at(f["end"]))
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
    from . import layers as LY
    LY.prune(project, oid)                                                      # openings that are gone are forgotten by the layers that named them
    roof_surface = AT.roof_surface(project, oid, f)                            # each wall face has its own underside height
    try:
        if LY.is_layered(meta_of(project, oid)):                                # one shell per layer, each with the openings it inherits
            _set_mesh(project, oid, LY.wall_mesh(project, oid, f, every, top_surface=roof_surface),
                      _rot(f["angle"], f["mid"][0], f["mid"][1], f["base"] + height / 2))
        elif every or f["ends"] != G.SQUARE_ENDS or f["top"]:
            local = [(x0 - length / 2, x1 - length / 2, z0 - height / 2, z1 - height / 2) for x0, x1, z0, z1, _ in every]
            top = [(s_ - length / 2, h_ - height / 2) for s_, h_ in f["top"]] or None
            surface = (lambda x, y: roof_surface(x, y) - height / 2) if roof_surface is not None else None
            breaks = AT.roof_breaks(project, oid, (-f["thickness"] / 2, 0.0, f["thickness"] / 2), f) if surface is not None else ()
            _set_mesh(project, oid, G.wall_mesh(length, f["thickness"], height, local, f["ends"], top, surface, breaks),
                      _rot(f["angle"], f["mid"][0], f["mid"][1], f["base"] + height / 2))
        else:
            project.model.set_geometry(oid, {"kind": "box", "center": [0.0, 0.0, 0.0], "size": [length, f["thickness"], height]})
            project.model.set_transform(oid, _rot(f["angle"], f["mid"][0], f["mid"][1], f["base"] + height / 2))
    except G.GeometryError as exc:
        raise BadRequest(str(exc)) from exc
    for hid in (s[4] for s in spans):
        _place_opening(project, hid, f)
        profile = opening_profile(project, hid)
        if profile:
            regenerate_opening_profile(project, profile)


def _place_opening(project: Any, oid: str, frame: Mapping[str, Any]) -> None:
    """Position a door or window's own solid inside its opening. The object's origin is its hinge: a vertical axis at the hinge edge (for a door, on
    the wall face the plan's swing arc is drawn from; for a window, on the wall's centre line), so an animated turn swings the leaf about its hinge,
    as the plan shows, and not about its own centre. The leaf itself sits where it did: centred in the opening."""
    meta = project.model.get(oid)["metadata"]
    x0, x1, z0, z1 = opening_span(meta)
    door = m(meta, "category") == "door"
    thickness = DOOR_LEAF if door else GLASS
    raw_x0, raw_x1 = x0, x1
    profile = opening_profile(project, oid)
    if profile:
        pm = meta_of(project, profile)
        inset, clearance = float(m(pm, "face_width")), float(m(pm, "clearance", 0.003))
        x0, x1 = x0 + inset + clearance, x1 - inset - clearance
        z0, z1 = z0 + (inset if not door else 0.0) + clearance, z1 - inset - clearance
        if x1 - x0 < MIN_PANEL or z1 - z0 < MIN_PANEL:
            raise BadRequest("The opening profile leaves insufficient clear space for the panel", hint="Reduce the profile face width or enlarge the rough opening")
    hinge_x = x0 if m(meta, "hinge", "start") == "start" else x1
    face = int(m(meta, "swing", 1)) * frame["thickness"] / 2 if door else 0.0
    c, s = math.cos(frame["angle"]), math.sin(frame["angle"])
    if has_panels(meta):                                                          # one leaf per panel, side by side, each its own closed shell; the origin is the door's start edge
        verts: list[list[float]] = []
        faces: list[list[int]] = []
        for _, px0, px1 in panel_spans(meta):
            px0 = x0 + (px0 - raw_x0) * (x1 - x0) / (raw_x1 - raw_x0)
            px1 = x0 + (px1 - raw_x0) * (x1 - x0) / (raw_x1 - raw_x0)
            v, f_ = _box(px0 - x0, px1 - x0, -thickness / 2, thickness / 2, -(z1 - z0) / 2, (z1 - z0) / 2)
            faces += [[a + len(verts), b + len(verts), c_ + len(verts)] for a, b, c_ in f_]
            verts += v
        _set_mesh(project, oid, (verts, faces), _rot(frame["angle"], frame["start"][0] + c * x0, frame["start"][1] + s * x0, frame["base"] + (z0 + z1) / 2))
        _attach(project, oid, {"level": m(frame["meta"], "level")})
        return
    project.model.set_geometry(oid, {"kind": "box", "center": [(x0 + x1) / 2 - hinge_x, -face, 0.0], "size": [x1 - x0, thickness, z1 - z0]})
    project.model.set_transform(oid, _rot(frame["angle"], frame["start"][0] + c * hinge_x - s * face, frame["start"][1] + s * hinge_x + c * face, frame["base"] + (z0 + z1) / 2))
    _attach(project, oid, {"level": m(frame["meta"], "level")})


def opening_profile(project: Any, opening_id: str) -> str | None:
    """The independent profile assembly attached to an opening, if assigned."""
    ids = [oid for oid in all_ids(project.model, metadata={f"{NS}:opening": opening_id})
           if m(meta_of(project, oid), "category") == "opening_profile"]
    if len(ids) > 1:
        raise BadRequest("An opening has more than one frame profile; resolve the duplicate before editing")
    return ids[0] if ids else None


def _opening_section(raw: Any, to_m: float) -> tuple[list[list[float]], float, float]:
    """Validate a *closed* SDRW section in drawing units, normalized to an inward face-width axis.

    A supported profile is a single simple polygon. Nested holes, arcs and
    self-intersections are rejected, not silently replaced by a rectangle.
    Returned points are in metres, with s=0 at the rough-opening edge and
    the section's depth centred on the host wall.
    """
    if not isinstance(raw, (list, tuple)) or not 3 <= len(raw) <= 200:
        raise BadRequest("An opening profile needs one closed SDRW polygon with 3 to 200 vertices")
    points: list[tuple[float, float]] = []
    for q in raw:
        if not isinstance(q, (list, tuple)) or len(q) != 2:
            raise BadRequest("Every profile vertex must be [face width, depth]")
        points.append((number(q[0], "profile x", -1e6, 1e6) * to_m,
                       number(q[1], "profile y", -1e6, 1e6) * to_m))
    if math.dist(points[0], points[-1]) < 1e-8:
        points.pop()
    if len(points) < 3 or not G.is_simple_polygon(points) or abs(G.signed_area(points)) < 1e-9:
        raise BadRequest("The SDRW opening profile must have a non-self-intersecting closed contour with nonzero area")
    min_s, max_s = min(p[0] for p in points), max(p[0] for p in points)
    min_y, max_y = min(p[1] for p in points), max(p[1] for p in points)
    width, depth = max_s - min_s, max_y - min_y
    if not 0.005 <= width <= 0.25 or not 0.005 <= depth <= 5.0:
        raise BadRequest("An opening profile section must be 5–250 mm across its face and 5–5000 mm deep")
    return [[s - min_s, y - (min_y + max_y) / 2] for s, y in points], width, depth


def _opening_profile_section(project: Any, p: Mapping[str, Any], to_m: float) -> tuple[list[list[float]], float, float, dict[str, Any]] | None:
    """One authoritative profile section: direct sketch or existing SDRW closed-polyline entity."""
    raw = p.get("section")
    source: dict[str, Any] = {}
    drawing = p.get("profile_drawing")
    entity = p.get("profile_entity")
    if drawing is not None or entity is not None:
        if raw is not None or drawing is None or entity is None:
            raise BadRequest("Choose either a drawn section or both the SDRW drawing and closed profile entity")
        doc = project.drawing(str(drawing))
        try:
            shape = doc.get(str(entity))
        except Exception as exc:
            raise BadRequest("The specified SDRW profile entity does not exist") from exc
        geom = shape.get("geometry") or {}
        if shape.get("kind") != "polyline" or not geom.get("closed"):
            raise BadRequest("An SDRW opening profile must be a closed polyline or rectangle")
        raw = geom.get("points")
        source = {"drawing": str(drawing), "entity": str(entity)}
    if raw is None:
        return None
    section, width, depth = _opening_section(raw, to_m)
    return section, width, depth, source


def _section_prism(section: list[list[float]], begin: float, end: float, side: str,
                   opening: Mapping[str, Any]) -> G.Mesh:
    """Extrude an SDRW cross-section along one jamb/head/sill, mapping to wall-local XYZ."""
    x0, x1, z0, z1 = opening_span(opening)
    verts, faces = G.prism([tuple(q) for q in section], begin, end)
    if side == "left":
        mapped = [[x0 + s, y, t] for s, y, t in verts]
    elif side == "right":
        mapped = [[x1 - s, y, t] for s, y, t in verts]
    elif side == "head":
        mapped = [[t, y, z1 - s] for s, y, t in verts]
    else:  # window sill
        mapped = [[t, y, z0 + s] for s, y, t in verts]
    return _positive_volume((mapped, faces))


def regenerate_opening_profile(project: Any, oid: str) -> None:
    """Rebuild a door/window profile against its *rough* opening and wall.

    The panel is then independently fitted to the resulting clear opening.
    This keeps the nominal door/window dimensions unchanged in their metadata.
    """
    meta = meta_of(project, oid)
    opid = str(m(meta, "opening", ""))
    if not alive(project, opid) or category(project, opid) not in OPENINGS:
        raise BadRequest("The opening profile has lost its host door or window")
    opening = meta_of(project, opid)
    frame = wall_frame(project, str(m(opening, "host")))
    face_width = float(m(meta, "face_width"))
    clearance = float(m(meta, "clearance", 0.003))
    x0, x1, z0, z1 = opening_span(opening)
    if x1 - x0 - 2 * (face_width + clearance) < MIN_PANEL or z1 - z0 - (2 if category(project, opid) == "window" else 1) * face_width - 2 * clearance < MIN_PANEL:
        raise BadRequest("The opening profile cannot fit within the rough opening", hint="Reduce the frame width or enlarge the door/window")
    depth = frame["thickness"] if m(meta, "depth_mode", "host") == "host" else float(m(meta, "depth"))
    if depth <= 0 or depth > 5.0:
        raise BadRequest("Opening profile depth must be positive and at most 5 m")
    stored = m(meta, "section")
    if stored:
        base_width = max(q[0] for q in stored)
        base_depth = max(q[1] for q in stored) - min(q[1] for q in stored)
        section = [[q[0] * face_width / base_width, q[1] * depth / base_depth] for q in stored]
    else:
        section = [[0.0, -depth / 2], [face_width, -depth / 2], [face_width, depth / 2], [0.0, depth / 2]]
    verts: list[list[float]] = []
    faces: list[list[int]] = []
    segments = [("left", z0 + (face_width if category(project, opid) == "window" else 0.0), z1 - face_width),
                ("right", z0 + (face_width if category(project, opid) == "window" else 0.0), z1 - face_width),
                ("head", x0, x1)]
    if category(project, opid) == "window":
        segments.append(("sill", x0, x1))
    for side, begin, end in segments:
        vv, ff = _section_prism(section, begin, end, side, opening)
        at = len(verts)
        verts.extend([[q[0] - frame["length"] / 2, q[1], q[2] - frame["height"] / 2] for q in vv])
        faces.extend([[i + at for i in face] for face in ff])
    _set_mesh(project, oid, _positive_volume((verts, faces)),
              _rot(frame["angle"], frame["mid"][0], frame["mid"][1], frame["base"] + frame["height"] / 2))
    _attach(project, oid, {"level": m(frame["meta"], "level"), "wall": m(opening, "host"), "depth": depth})


def add_opening_profile(project: Any, p: Mapping[str, Any], to_m: float = 0.001) -> str:
    opid = text(p.get("opening"), "opening", 80)
    if not alive(project, opid) or category(project, opid) not in OPENINGS:
        raise BadRequest("Select a door or window to add an opening profile")
    existing = opening_profile(project, opid)
    if existing and not bool(p.get("replace", False)):
        raise BadRequest("This opening already has a profile; select the existing frame to edit it")
    source_section = _opening_profile_section(project, p, to_m)
    width = source_section[1] if source_section else number(p.get("width", 50), "profile face width (mm)", 5, 250) / 1000.0
    clearance = number(p.get("clearance", 3), "panel clearance (mm)", 0, 100) / 1000.0
    depth_mode = "fixed" if source_section or p.get("depth") is not None else "host"
    depth = (source_section[2] if source_section else float(number(p["depth"], "profile depth (mm)", 5, 5000) / 1000.0)) if depth_mode == "fixed" else 0.0
    material = _choice(p.get("material", "Timber"), MATERIALS, "material")
    opening = meta_of(project, opid)
    oid = existing or project.model.add_box(width=1.0, depth=1.0, height=1.0,
                                          name=text(p.get("name") or ("Door Opening Profile" if category(project, opid) == "door" else "Window Opening Profile"), "name", 80))
    _attach(project, oid, {"category": "opening_profile", "type": "Opening Profile", "opening": opid,
                           "wall": m(opening, "host"), "level": m(opening, "level"), "face_width": width,
                           "clearance": clearance, "depth_mode": depth_mode, "depth": depth, "material": material,
                           "section": source_section[0] if source_section else None,
                           "section_source": source_section[3] if source_section else None})
    regenerate_opening_profile(project, oid)
    regenerate_wall(project, str(m(opening, "host")))
    return oid


def remove_opening_profile(project: Any, opening_id: str) -> str:
    profile = opening_profile(project, opening_id)
    if not profile:
        raise NotFound("This door or window has no opening profile")
    remove(project, profile)
    return profile


def refresh_opening_profiles_for_drawing(project: Any, drawing_id: str, to_m: float) -> None:
    """Synchronize referenced SDRW sections after a drawing edit, in the same undoable transaction.

    References are snapshots of the source *shape*, not its absolute drawing position.
    Deleting or invalidating a referenced contour is rejected, preserving the last
    valid model and the drawing. Unchanged sections cause no model recomputation.
    """
    for oid in all_ids(project.model):
        meta = meta_of(project, oid)
        if m(meta, "category") != "opening_profile":
            continue
        source = m(meta, "section_source")
        if not isinstance(source, Mapping) or source.get("drawing") != drawing_id:
            continue
        parsed = _opening_profile_section(project, {"profile_drawing": drawing_id,
                                                    "profile_entity": source.get("entity")}, to_m)
        if parsed is None:
            raise BadRequest("A referenced SDRW opening profile has no valid section")
        section, width, depth, _ = parsed
        if m(meta, "section") == section and m(meta, "face_width") == width and m(meta, "depth") == depth:
            continue
        project.touch("model")
        _attach(project, oid, {"section": section, "face_width": width,
                               "depth": depth, "depth_mode": "fixed"})
        regenerate_opening_profile(project, oid)
        opid = str(m(meta, "opening"))
        regenerate_wall(project, str(m(meta_of(project, opid), "host")))


# ------------------------------------------------------------------------------------------------------------------------ door panels
PANEL_OPERATIONS = ("swing", "fixed")
MIN_PANEL = 0.05                      # m: a panel narrower than this is not a panel


def has_panels(meta: Mapping[str, Any]) -> bool:
    return m(meta, "category") == "door" and bool(m(meta, "panels"))


def door_panels(meta: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The panels of a door, from its start edge to its end. A door that was never given any is one panel with the door's own hinge and swing, so
    every door is already "a door of one panel"; adding a panel writes the list out."""
    stored = m(meta, "panels")
    if stored:
        return [{"id": str(q["id"]), "name": str(q.get("name") or ""), "width": float(q["width"]), "hinge": str(q.get("hinge", "start")),
                 "swing": int(q.get("swing", 1)), "operation": str(q.get("operation", "swing"))} for q in stored]
    return [{"id": "p1", "name": "Panel 1", "width": float(m(meta, "width")), "hinge": str(m(meta, "hinge", "start")), "swing": int(m(meta, "swing", 1)), "operation": "swing"}]


def panel_spans(meta: Mapping[str, Any]) -> list[tuple[dict[str, Any], float, float]]:
    """Each panel with where it lies along the wall: (panel, x0, x1)."""
    x = opening_span(meta)[0]
    out = []
    for q in door_panels(meta):
        out.append((q, x, x + q["width"]))
        x += q["width"]
    return out


def _store_panels(project: Any, oid: str, panels: list[dict[str, Any]]) -> None:
    """Write a door's panels, keep its width the sum of them, and rebuild the wall it is in (which rebuilds the door's solid)."""
    meta = meta_of(project, oid)
    _attach(project, oid, {"panels": panels, "width": sum(q["width"] for q in panels)})
    regenerate_wall(project, str(m(meta, "host")))


def _panel(panels: list[dict[str, Any]], pid: str) -> int:
    for i, q in enumerate(panels):
        if q["id"] == pid:
            return i
    raise NotFound(f"This door has no panel '{pid}'")


def add_panel(project: Any, oid: str) -> str:
    """Split the widest panel of a door in two (the new one hinged on the opposite jamb, as a pair of leaves is), so the opening stays as wide."""
    meta = meta_of(project, oid)
    if m(meta, "category") != "door":
        raise BadRequest("Only a door has panels")
    panels = door_panels(meta)
    if len(panels) >= 8:
        raise BadRequest("A door has at most 8 panels")
    k = max(range(len(panels)), key=lambda i: panels[i]["width"])
    half = panels[k]["width"] / 2
    if half < MIN_PANEL:
        raise BadRequest(f"The widest panel is {panels[k]['width'] * 1000:.0f} mm: it cannot be split into panels of at least {MIN_PANEL * 1000:.0f} mm")
    n = 1 + max(int(q["id"][1:]) for q in panels if q["id"][1:].isdigit())
    new = {**panels[k], "id": f"p{n}", "name": f"Panel {len(panels) + 1}", "width": half, "hinge": "end" if panels[k]["hinge"] == "start" else "start"}
    panels[k]["width"] = half
    panels.insert(k + 1, new)
    _store_panels(project, oid, panels)
    return new["id"]


def remove_panel(project: Any, oid: str, pid: str) -> None:
    """Remove a panel; its width goes to the panel beside it, so the opening stays as wide."""
    panels = door_panels(meta_of(project, oid))
    if len(panels) < 2:
        raise BadRequest("A door keeps at least one panel")
    i = _panel(panels, pid)
    j = i - 1 if i > 0 else 1
    panels[j]["width"] += panels[i]["width"]
    del panels[i]
    _store_panels(project, oid, panels)


def set_panel_field(project: Any, oid: str, pid: str, field: str, value: Any) -> None:
    """Edit one panel: ``width`` (mm; the panel beside it gives or takes the difference, so the door stays as wide), ``hinge``, ``swing``, ``operation``, ``name``."""
    meta = meta_of(project, oid)
    if m(meta, "category") != "door":
        raise BadRequest("Only a door has panels")
    panels = door_panels(meta)
    i = _panel(panels, pid)
    q = panels[i]
    if field == "width":
        if len(panels) < 2:
            raise BadRequest("A door of one panel is as wide as the door: change the door's width")
        new = _mm(value, "width", MIN_PANEL * 1000, 20000)
        j = i + 1 if i < len(panels) - 1 else i - 1
        other = panels[j]["width"] + q["width"] - new
        if other < MIN_PANEL - 1e-9:
            raise BadRequest(f"That would leave '{panels[j]['name'] or panels[j]['id']}' narrower than {MIN_PANEL * 1000:.0f} mm", hint="Make the other panels narrower first, or the door wider")
        q["width"], panels[j]["width"] = new, other
    elif field == "hinge":
        q["hinge"] = _choice(value, ("start", "end"), "hinge")
    elif field == "swing":
        q["swing"] = 1 if _choice(value, ("left", "right"), "swing") == "left" else -1
    elif field == "operation":
        q["operation"] = _choice(value, PANEL_OPERATIONS, "operation")
    elif field == "name":
        q["name"] = text(value, "name", 40)
    else:
        raise BadRequest(f"'{field}' is not a panel property")
    _store_panels(project, oid, panels)


def _box(x0: float, x1: float, y0: float, y1: float, z0: float, z1: float) -> G.Mesh:
    v = [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0], [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]]
    f = [[0, 3, 2], [0, 2, 1], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [2, 3, 7], [2, 7, 6], [0, 4, 7], [0, 7, 3], [1, 2, 6], [1, 6, 5]]
    return v, f


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
    if kind == "window":
        values["operation"] = _choice(p.get("operation", "fixed"), WINDOW_OPERATIONS, "operation")
    values["swing"] = 1 if _choice(p.get("swing", "left"), ("left", "right"), "swing") == "left" else -1      # which side of the wall the leaf opens to
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
    from . import layers as LY
    cut = FX.remember_cuts(project, oid)                                             # voids through the slab's thickness: holes, and notches in its edge
    try:
        if LY.is_layered(meta):                                                      # one slab per layer, each with the voids it inherits
            outline = [tuple(q) for q in m(meta, "boundary")]
            verts: list[Any] = []
            faces: list[Any] = []
            counts: list[int] = []
            for lay, c in zip(LY.stack(project, oid), cut):
                v, f = FX._solid(c.outline or outline, c.holes, top - lay["offset"] - lay["thickness"], top - lay["offset"])
                at = len(verts)
                verts += [list(p) for p in v]
                faces += [[a + at, b + at, c_ + at] for a, b, c_ in f]
                counts.append(len(f))
            LY.record(project, oid, counts)
            _set_mesh(project, oid, _positive_volume((verts, faces)))
            return
        _set_mesh(project, oid, FX._solid(cut.outline or [tuple(q) for q in m(meta, "boundary")], cut.holes, top - float(m(meta, "thickness")), top))
    except G.GeometryError as exc:
        raise BadRequest(str(exc)) from exc


def set_boundary(project: Any, oid: str, points: Any, to_m: float) -> None:
    """Replace the outline of a floor or roof (corner points in drawing units), as stretching an edge or corner does. The element is rebuilt in the same
    command, so a room, a wall attached to the roof, an opening cut or a gutter follows; an outline the element cannot have (a pitched roof that is no
    longer a rectangle, a crossing outline) is refused with the reason and nothing changes."""
    cat = m(meta_of(project, oid), "category")
    if cat not in ("floor", "roof"):
        raise BadRequest("Only a floor or a roof has an outline to stretch")
    _attach(project, oid, {"boundary": _boundary(points, to_m, cat)})
    regenerate_floor(project, oid) if cat == "floor" else regenerate_roof(project, oid)


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


def roof_ridge(meta: Mapping[str, Any]) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Gable ridge endpoints in plan (metres), respecting its transverse offset."""
    if m(meta, "roof") != "gable":
        return None
    centre, along, across, length, _ = _rectangle([list(q) for q in m(meta, "boundary")])
    shift = float(m(meta, "ridge_offset", 0.0))
    return tuple((centre[0] + across[0] * shift + along[0] * t,
                  centre[1] + across[1] * shift + along[1] * t) for t in (-length / 2, length / 2))  # type: ignore[return-value]


Facet = tuple[list[tuple[float, float]], tuple[float, float, float]]


def _plane(points: list[tuple[float, float, float]]) -> tuple[float, float, float]:
    """(a, b, c) of the plane z = a·x + b·y + c through the first three points that are not in a line (a facet is never vertical)."""
    p0 = points[0]
    for i in range(1, len(points)):
        for j in range(i + 1, len(points)):
            u = (points[i][0] - p0[0], points[i][1] - p0[1], points[i][2] - p0[2])
            v = (points[j][0] - p0[0], points[j][1] - p0[1], points[j][2] - p0[2])
            n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
            if abs(n[2]) > 1e-12:
                a, b = -n[0] / n[2], -n[1] / n[2]
                return a, b, p0[2] - a * p0[0] - b * p0[1]
    raise BadRequest("A roof facet has no plane")


def roof_facets(project: Any, meta: Mapping[str, Any]) -> list[Facet]:
    """The underside of a roof as planar facets: each is a plan polygon (m) and the plane ``z = a·x + b·y + c`` (absolute z) its underside lies in.
    A flat roof is one facet; a shed roof one slope; a gable roof two; a hip roof four (a pyramid when the outline is square). The underside is the
    top surface lowered by the roof's thickness measured straight down, as the roof is built."""
    base = level_elevation(project, m(meta, "level")) + float(m(meta, "offset", 0.0))
    thickness, kind = float(m(meta, "thickness")), m(meta, "roof")
    boundary = [(float(q[0]), float(q[1])) for q in m(meta, "boundary")]
    if kind == "flat":
        return [(boundary, (0.0, 0.0, base))]
    centre, along, across, length, width = _rectangle([list(q) for q in boundary])
    rise = math.tan(math.radians(float(m(meta, "pitch"))))
    world = lambda s, t, z: (centre[0] + across[0] * s + along[0] * t, centre[1] + across[1] * s + along[1] * t, base + z - thickness)       # noqa: E731
    if kind == "shed":
        shapes = [[(-width / 2, -length / 2, 0.0), (width / 2, -length / 2, width * rise), (width / 2, length / 2, width * rise), (-width / 2, length / 2, 0.0)]]
    elif kind == "gable":
        r = width / 2 * rise
        ridge = float(m(meta, "ridge_offset", 0.0))
        if abs(ridge) >= width / 2 - 0.05:
            raise BadRequest("The ridge must remain at least 50 mm inside both eaves")
        shapes = [[(-width / 2, -length / 2, 0.0), (ridge, -length / 2, r), (ridge, length / 2, r), (-width / 2, length / 2, 0.0)],
                  [(ridge, -length / 2, r), (width / 2, -length / 2, 0.0), (width / 2, length / 2, 0.0), (ridge, length / 2, r)]]
    else:                                                                              # hip
        ridge, top = (length - width) / 2, width / 2 * rise
        pts = [(-width / 2, -length / 2, 0.0), (width / 2, -length / 2, 0.0), (width / 2, length / 2, 0.0), (-width / 2, length / 2, 0.0), (0.0, -ridge, top), (0.0, ridge, top)]
        shapes = [[pts[i] for i in q] for q in ((0, 1, 4), (1, 2, 5, 4), (2, 3, 5), (3, 0, 4, 5))]
    out: list[Facet] = []
    for shape in shapes:
        pts3 = [world(*q) for q in shape]
        ring: list[tuple[float, float]] = []
        for q in pts3:                                                                  # a square hip roof has a ridge of no length: drop repeated corners
            if not ring or math.dist(ring[-1], q[:2]) > 1e-9:
                ring.append((q[0], q[1]))
        if len(ring) > 1 and math.dist(ring[0], ring[-1]) < 1e-9:
            ring.pop()
        if len(ring) >= 3:
            out.append((ring, _plane(pts3)))
    return out


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


def _roof_mesh(meta: Mapping[str, Any], base: float, thickness: float) -> G.Mesh:
    """One slab of a roof: its top surface where the roof's top is when its eaves are at ``base``, ``thickness`` thick straight down."""
    kind = m(meta, "roof")
    boundary = m(meta, "boundary")
    if kind == "flat":
        return G.prism([tuple(q) for q in boundary], base, base + thickness)
    centre, along, across, length, width = _rectangle(boundary)
    rise_angle = math.tan(math.radians(float(m(meta, "pitch"))))
    if kind == "hip":
        return _positive_volume(_hip_mesh(centre, along, across, length, width, rise_angle, thickness, base))
    if kind == "gable":
        r = width / 2 * rise_angle
        ridge = float(m(meta, "ridge_offset", 0.0))
        if abs(ridge) >= width / 2 - 0.05:
            raise BadRequest("The ridge must remain at least 50 mm inside both eaves")
        profile = [(-width / 2, -thickness), (ridge, r - thickness), (width / 2, -thickness), (width / 2, 0.0), (ridge, r), (-width / 2, 0.0)]
    else:
        r = width * rise_angle
        profile = [(-width / 2, -thickness), (width / 2, r - thickness), (width / 2, r), (-width / 2, 0.0)]
    verts, faces = G.prism(profile, -length / 2, length / 2)                       # (s, z, t): across, up, along
    mapped = [[centre[0] + across[0] * s + along[0] * t, centre[1] + across[1] * s + along[1] * t, base + z] for s, z, t in verts]
    return _positive_volume((mapped, faces))


def regenerate_roof(project: Any, oid: str) -> None:
    meta = project.model.get(oid)["metadata"]
    base = level_elevation(project, m(meta, "level")) + float(m(meta, "offset", 0.0))
    from . import layers as LY
    try:
        if LY.is_layered(meta):                                                    # one slab per layer, each lowered by the layers above it
            verts: list[Any] = []
            faces: list[Any] = []
            layers = LY.stack(project, oid)
            counts: list[int] = []
            whole = LY.total(layers)
            for lay in layers:
                # a flat roof stands on its level (its top rises as it thickens); a pitched one hangs from its eaves line (its underside drops)
                at_base = base + whole - lay["offset"] - lay["thickness"] if m(meta, "roof") == "flat" else base - lay["offset"]
                v, f = _roof_mesh(meta, at_base, lay["thickness"])
                at = len(verts)
                verts += [list(p) for p in v]
                faces += [[a + at, b + at, c + at] for a, b, c in f]
                counts.append(len(f))
            LY.record(project, oid, counts)
            mesh: G.Mesh = _positive_volume((verts, faces))
        else:
            mesh = _roof_mesh(meta, base, float(m(meta, "thickness")))
        _set_mesh(project, oid, mesh)
        # Ridge members and edge trims are independently editable SGEO solids;
        # their placement is derived from the host roof every time it changes.
        for dependent in element_ids(project):
            if m(meta_of(project, dependent), "roof_host") == oid:
                regenerate_roof_accessory(project, dependent)
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
    ridge_offset = number(p.get("ridge_offset", 0.0), "ridge offset (mm)", -1e6, 1e6) / 1000.0 if kind == "gable" else 0.0
    if kind == "gable" and abs(ridge_offset) >= _rectangle(boundary)[4] / 2 - 0.05:
        raise BadRequest("The ridge must remain at least 50 mm inside both eaves")
    oid = project.model.add_box(width=1.0, depth=1.0, height=1.0, name=text(p.get("name") or ROOF_TYPES[kind], "name", 80))
    _attach(project, oid, {"category": "roof", "type": ROOF_TYPES[kind], "roof": kind, "level": level["id"], "boundary": boundary, "thickness": thickness,
                           "pitch": pitch, "offset": 0.0, "ridge_offset": ridge_offset, "material": material})
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


# ------------------------------------------ roof-hosted members (independent SGEO objects with stable host references)
ROOF_ACCESSORIES = {"ridge_beam": "Ridge Beam", "ridge_strap": "Ridge Strap", "roof_edge": "Roof Edge"}


def _ridge_for_accessory(meta: Mapping[str, Any]) -> tuple[tuple[float, float], tuple[float, float]]:
    ridge = roof_ridge(meta)
    if ridge:
        return ridge
    if m(meta, "roof") == "hip":
        centre, along, _, length, width = _rectangle([list(p) for p in m(meta, "boundary")])
        half = (length - width) / 2
        if half > 0.025:
            return ((centre[0] - along[0] * half, centre[1] - along[1] * half),
                    (centre[0] + along[0] * half, centre[1] + along[1] * half))
    raise BadRequest("Only a roof with a real ridge can host a ridge beam or strap")


def _roof_edge_mesh(project: Any, roof: Mapping[str, Any], edge: int, width: float, depth: float, vertical_offset: float, lateral_offset: float) -> G.Mesh:
    """One continuous watertight edge solid, with mitred *roof* corners and facet breaks.

    The old implementation assembled separately capped boxes along each roof
    plane. Their square ends intersected at the gable ridge (the visible
    diamond) and extended into one another at outline corners. A shared mitre
    station between adjacent centre-line segments replaces those overlapping
    end caps. Boundary corner mitres are computed from the neighbouring roof
    edges even when only one edge component has been installed.
    """
    boundary = [tuple(map(float, p)) for p in m(roof, "boundary")]
    a, b = boundary[edge], boundary[(edge + 1) % len(boundary)]
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length < 1e-7:
        raise BadRequest("A roof boundary edge is too short")
    sigma = 1.0 if G.signed_area(boundary) > 0 else -1.0
    def outward(i: int) -> tuple[float, float, float]:
        start, stop = boundary[i % len(boundary)], boundary[(i + 1) % len(boundary)]
        vx, vy = stop[0] - start[0], stop[1] - start[1]
        norm = math.hypot(vx, vy)
        if norm < 1e-7:
            raise BadRequest("The roof outline contains a zero-length edge")
        return sigma * vy / norm, -sigma * vx / norm, 0.0

    facets = roof_facets(project, roof)
    thickness = float(m(roof, "thickness"))
    # The roof assembly is ordered outer/top to inner/bottom. Locate
    # the structural core from explicit consecutive layer roles, or fall
    # back to the complete legacy assembly when no core has been assigned.
    cursor, core_ranges = 0.0, []
    for layer in m(roof, "layers") or []:
        t = float(layer["thickness"])
        if layer.get("role") == "core":
            core_ranges.append((cursor, cursor + t))
        cursor += t
    core_depth = ((core_ranges[0][0] + core_ranges[-1][1]) / 2
                  if core_ranges else thickness / 2)
    ridge = roof_ridge(roof)
    def facet_distance_sq(poly: list[tuple[float, float]], x: float, y: float) -> float:
        return min((lambda dx, dy: dx * dx + dy * dy)(x - (p[0] + t * vx), y - (p[1] + t * vy))
                   for p, q in zip(poly, poly[1:] + poly[:1])
                   for vx, vy in [(q[0] - p[0], q[1] - p[1])]
                   for t in [max(0.0, min(1.0, ((x - p[0]) * vx + (y - p[1]) * vy) / max(1e-16, vx * vx + vy * vy)))])
    def roof_point(x: float, y: float) -> tuple[float, float, float]:
        # Use the roof facet actually incident on this *boundary edge*.
        # Extrapolated planes from a non-incident facet may be far below it.
        _, plane = min(facets, key=lambda item: facet_distance_sq(item[0], x, y))
        z = plane[0] * x + plane[1] * y + plane[2] + thickness
        return x, y, z + vertical_offset

    def edge_points(i: int) -> list[tuple[float, float, float]]:
        p, q = boundary[i % len(boundary)], boundary[(i + 1) % len(boundary)]
        ts = [0.0, 1.0]
        if ridge:
            line = G.intersect_lines(p, G.sub(q, p), ridge[0], G.sub(ridge[1], ridge[0]))
            if line and 1e-7 < line[0] < 1 - 1e-7 and -1e-7 <= line[1] <= 1 + 1e-7:
                ts.insert(1, line[0])
        return [roof_point(p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t) for t in ts]

    stations = edge_points(edge)
    # An adjacent gable-end slope can change direction at the ridge. Using the
    # neighbour's far corner instead of its *local* facet produces mismatched
    # lower mitre corners, despite sharing the same outline vertex.
    before = edge_points(edge - 1)[-2]
    after = edge_points(edge + 1)[1]
    normals = [outward(edge - 1), *([outward(edge)] * max(0, len(stations) - 2)), outward(edge + 1)]
    this_normal = outward(edge)

    # Roof thickness is explicitly vertical in SPADRA's roof solid. A fascia
    # following its roof-end must therefore have vertical section edges too;
    # an edge-tangent cross product makes those edges lean in plan on slopes.
    def mitre(u: tuple[float, float, float], v: tuple[float, float, float]) -> tuple[float, float, float]:
        """The offset-line intersection: both incident sections project to one mitre."""
        d = 1 + sum(u[i] * v[i] for i in range(3))
        if d < 0.05:
            raise BadRequest("This roof corner is too sharp to mitre with a rectangular edge profile")
        return tuple((u[i] + v[i]) / d for i in range(3))  # type: ignore[return-value]

    verts: list[list[float]] = []
    for i, centre in enumerate(stations):
        prev = before if i == 0 else stations[i - 1]
        nxt = after if i == len(stations) - 1 else stations[i + 1]
        prev_normal = normals[i] if i == 0 else this_normal
        next_normal = normals[i] if i == len(stations) - 1 else this_normal
        horizontal = mitre(prev_normal, next_normal)
        # Keep every profile upright in world coordinates. Stations still
        # rise and fall along the exact gable/hip roof boundary, and XY corner
        # mitres come from the adjoining *plan* edges. Top and bottom corners
        # of each station must share XY; the end cap is never a slanted fascia.
        for side, lower in ((-0.5, False), (0.5, False), (0.5, True), (-0.5, True)):
            v = [centre[0] + horizontal[0] * (lateral_offset + side * width),
                 centre[1] + horizontal[1] * (lateral_offset + side * width),
                 centre[2] + (depth / 2 if not lower else -depth / 2) - core_depth]
            verts.append(v)
    faces: list[list[int]] = [[0, 2, 1], [0, 3, 2]]
    for i in range(len(stations) - 1):
        first, second = 4 * i, 4 * (i + 1)
        for j in range(4):
            k = (j + 1) % 4
            faces += [[first + j, first + k, second + k], [first + j, second + k, second + j]]
    last = 4 * (len(stations) - 1)
    faces += [[last, last + 1, last + 2], [last, last + 2, last + 3]]
    return _positive_volume((verts, faces))


def regenerate_roof_accessory(project: Any, oid: str) -> None:
    meta = meta_of(project, oid)
    roof_id = str(m(meta, "roof_host", ""))
    if not alive(project, roof_id) or category(project, roof_id) != "roof":
        raise BadRequest("The roof accessory has lost its host roof")
    roof = meta_of(project, roof_id)
    kind = m(meta, "accessory_kind")
    if kind == "roof_edge":
        edge = int(m(meta, "edge"))
        boundary = m(roof, "boundary")
        if edge < 0 or edge >= len(boundary):
            raise BadRequest("The roof edge reference is no longer valid")
        _set_mesh(project, oid, _roof_edge_mesh(project, roof, edge, float(m(meta, "width")), float(m(meta, "depth")), float(m(meta, "vertical_offset", 0.0)),
                                                float(m(meta, "lateral_offset", 0.0))))
        start, end = boundary[edge], boundary[(edge + 1) % len(boundary)]
        vx, vy = end[0] - start[0], end[1] - start[1]
        at = float(m(meta, "lateral_offset", 0.0)) / math.hypot(vx, vy)
        moved = ((start[0] + vy * at, start[1] - vx * at), (end[0] + vy * at, end[1] - vx * at))
        _attach(project, oid, {"level": m(roof, "level"), "start": list(moved[0]), "end": list(moved[1]),
                               "top": float(m(roof, "offset", 0.0)) + roof_rise(roof)})
        return
    a, b = _ridge_for_accessory(roof)
    lateral = float(m(meta, "lateral_offset", 0.0))
    if kind == "ridge_strap":
        _, along, across, _, _ = _rectangle([list(p) for p in m(roof, "boundary")])
        centre = ((a[0] + b[0]) / 2 + along[0] * lateral, (a[1] + b[1]) / 2 + along[1] * lateral)
        span = float(m(meta, "span", 0.8))
        a, b = ((centre[0] - across[0] * span / 2, centre[1] - across[1] * span / 2),
                (centre[0] + across[0] * span / 2, centre[1] + across[1] * span / 2))
    else:
        _, _, across, _, _ = _rectangle([list(p) for p in m(roof, "boundary")])
        a, b = ((a[0] + across[0] * lateral, a[1] + across[1] * lateral),
                (b[0] + across[0] * lateral, b[1] + across[1] * lateral))
    top = float(m(roof, "offset", 0.0)) + roof_rise(roof) - float(m(roof, "thickness")) + float(m(meta, "vertical_offset", 0.0))
    if kind == "ridge_strap":
        top -= float(m(meta, "span", 0.8)) / 2 * math.tan(math.radians(float(m(roof, "pitch"))))
    if top < 0.1:
        raise BadRequest("The roof accessory cannot fit below the roof at this level")
    _attach(project, oid, {"start": list(a), "end": list(b), "top": top, "level": m(roof, "level")})
    regenerate_beam(project, oid)


def add_roof_accessory(project: Any, p: Mapping[str, Any]) -> list[str]:
    roof_id = text(p.get("roof"), "roof", 80)
    if not alive(project, roof_id) or category(project, roof_id) != "roof":
        raise BadRequest("Select a roof to add a ridge or edge component")
    kind = _choice(p.get("kind"), tuple(ROOF_ACCESSORIES), "roof accessory")
    roof = meta_of(project, roof_id)
    if kind != "roof_edge":
        _ridge_for_accessory(roof)
    boundary = m(roof, "boundary")
    if kind == "roof_edge":
        requested = str(p.get("edge", "all"))
        if requested != "all" and (not requested.isdigit() or not 1 <= int(requested) <= len(boundary)):
            raise BadRequest(f"Choose roof edge 1 through {len(boundary)}, or all")
        edges = list(range(len(boundary))) if requested == "all" else [int(requested) - 1]
    else:
        edges = [-1]
    existing = {(m(meta_of(project, oid), "accessory_kind"), m(meta_of(project, oid), "edge")): oid for oid in element_ids(project)
                if m(meta_of(project, oid), "roof_host") == roof_id}
    created: list[str] = []
    for edge in edges:
        if (kind, edge) in existing:
            continue
        width = number(p.get("width", 60 if kind == "roof_edge" else 200 if kind == "ridge_beam" else 50), "width", 10, 1000) / 1000.0
        depth = number(p.get("depth", 180 if kind == "roof_edge" else 300 if kind == "ridge_beam" else 12), "depth", 10, 1000) / 1000.0
        material = _choice(p.get("material", "Timber" if kind != "ridge_strap" else "Steel"), BEAM_MATERIALS, "material")
        name = ROOF_ACCESSORIES[kind] + (f" {edge + 1}" if edge >= 0 else "")
        oid = project.model.add_box(width=1.0, depth=1.0, height=1.0, name=name)
        _attach(project, oid, {"category": "structural", "type": ROOF_ACCESSORIES[kind], "profile": "rect", "level": m(roof, "level"),
                               "roof_host": roof_id, "accessory_kind": kind, "edge": edge, "width": width, "depth": depth, "material": material,
                               "span": number(p.get("span", 800), "span", 200, 3000) / 1000.0,
                               "vertical_offset": number(p.get("vertical_offset", 0), "vertical offset", -5000, 5000) / 1000.0,
                               "lateral_offset": number(p.get("lateral_offset", 0), "lateral offset", -5000, 5000) / 1000.0})
        regenerate_roof_accessory(project, oid)
        created.append(oid)
    if not created:
        raise BadRequest("This roof already has the selected accessory; select its existing element to edit it")
    if kind == "roof_edge":
        from . import gutters as GU
        GU.follow(project, roof_id)                     # new trim displaces any existing gutter runs without overwriting their requested gap
    return created


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
    elif cat == "opening_profile":
        regenerate_opening_profile(project, oid)
        opening = str(m(meta, "opening"))
        if alive(project, opening):
            regenerate_wall(project, str(m(meta_of(project, opening), "host")))
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
        if m(meta, "roof_host"):
            regenerate_roof_accessory(project, oid)
            if m(meta, "accessory_kind") == "roof_edge":
                from . import gutters as GU
                GU.follow(project, str(m(meta, "roof_host")))
        else:
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
    if key.startswith("corner_join:"):
        if cat != "wall":
            raise BadRequest("Corner join controls apply only to walls")
        _, other, endpoint = (key.split(":", 2) + [""])[:3]
        set_wall_corner_mode(project, oid, other, endpoint, value)
        return
    if key == "description":
        _attach(project, oid, {"description": text(value, "description", 2000, empty=True, multiline=True)})
        return
    if key == "color":
        if not m(meta, "material"):
            raise BadRequest("This element has no assignable material appearance")
        _attach(project, oid, {"color": color(value)})
        return
    if key == "material_color":
        material = m(meta, "material")
        if not material:
            raise BadRequest("This element has no assignable material appearance")
        chosen = color(value)
        palette = project.manifest.setdefault("material_colors", {})
        if chosen is None:
            palette.pop(material, None)
        else:
            palette[material] = chosen
        return
    if key.startswith("custom:"):
        from . import relations as RL
        RL.set_custom(project, oid, key[7:], RL.parse_custom(value))
        return
    if key.startswith("layer:"):
        from . import layers as LY
        LY.set_field(project, oid, key, value)
        return
    if key in ("thickness", "material") and cat in ("wall", "floor", "roof") and m(meta, "layers"):
        from . import layers as LY
        rows_ = LY.stack(project, oid)
        if len(rows_) > 1:
            raise BadRequest(f"This {cat} has {len(rows_)} layers: its {key} comes from them", hint="Change the thickness or material of a layer in the Layers group")
        LY.set_field(project, oid, f"layer:{rows_[0]['id']}:{key}", value)
        return
    if key.startswith(("pset:", "psett:", "qto:")):                                  # IFC property and quantity sets are metadata, not geometry
        from . import bimdata as BD
        BD.set_property(project, oid, key, value)
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
            from . import attachments as AT
            if AT.is_attached(project, oid):
                raise BadRequest("This wall's height follows what it is attached to", hint="Detach it from the roof or level first")
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
        if key.startswith("panel:"):
            _, pid, field = key.split(":", 2)
            set_panel_field(project, oid, pid, field, value)
            return
        if key in ("hinge", "swing") and has_panels(meta):
            raise BadRequest("This door has panels: set the hinge and the swing side on each panel")
        if key == "width":
            values["width"] = _mm(value, "width", 100, 20000)
            if has_panels(meta):                                                  # the panels keep their proportions
                old = float(m(meta, "width"))
                values["panels"] = [{**q, "width": q["width"] * values["width"] / old} for q in door_panels(meta)]
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
        elif key == "swing" and cat in OPENINGS:
            values["swing"] = 1 if _choice(value, ("left", "right"), "swing") == "left" else -1
        elif key == "operation" and cat == "window":
            values["operation"] = _choice(value, WINDOW_OPERATIONS, "operation")
        else:
            raise BadRequest(f"'{key}' is not an editable {cat} property")
    elif cat == "opening_profile":
        if key == "face_width":
            values[key] = _mm(value, "profile face width", 5, 250)
        elif key == "section":
            section, width, depth = _opening_section(value, 0.001)
            values.update({"section": section, "section_source": None, "face_width": width, "depth": depth, "depth_mode": "fixed"})
        elif key == "clearance":
            values[key] = _mm(value, "panel clearance", 0, 100)
        elif key == "depth":
            values.update({"depth": _mm(value, "profile depth", 5, 5000), "depth_mode": "fixed"})
        elif key == "depth_mode":
            values["depth_mode"] = _choice(value, ("host", "fixed"), "depth mode")
        elif key == "material":
            values["material"] = _choice(value, MATERIALS, "material")
        else:
            raise BadRequest(f"'{key}' is not an editable opening-profile property")
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
            values.update({"roof": kind, "type": ROOF_TYPES[kind], "ridge_offset": float(m(meta, "ridge_offset", 0.0)) if kind == "gable" else 0.0})
        elif key == "ridge_offset" and cat == "roof" and m(meta, "roof") == "gable":
            half_width = _rectangle([list(q) for q in m(meta, "boundary")])[4] / 2
            shift = _mm(value, "ridge offset", -100000, 100000)
            if abs(shift) >= half_width - 0.05:
                raise BadRequest("The ridge must remain at least 50 mm inside both eaves")
            values["ridge_offset"] = shift
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
        if m(meta, "roof_host") and key in ("vertical_offset", "lateral_offset"):
            values[key] = _mm(value, key.replace("_", " "), -5000, 5000)
        elif m(meta, "roof_host") and key == "span" and m(meta, "accessory_kind") == "ridge_strap":
            values[key] = _mm(value, "strap span", 200, 3000)
        elif m(meta, "roof_host") and (key in ("top", "level", "start_x", "start_y", "end_x", "end_y", "profile", "size", "roll") or key.startswith("dim:")):
            raise BadRequest("This roof accessory follows its host geometry: adjust its offsets or edit the roof")
        elif key in ("profile", "size", "roll") or key.startswith("dim:"):
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
        if m(meta, "roof_host"):
            kind, roof = m(meta, "accessory_kind"), meta_of(project, m(meta, "roof_host"))
            if kind == "ridge_beam":
                axis = _rectangle([list(p) for p in m(roof, "boundary")])[2]
            elif kind == "ridge_strap":
                axis = _rectangle([list(p) for p in m(roof, "boundary")])[1]
            else:
                s, e = m(meta, "start"), m(meta, "end")
                vx, vy = e[0] - s[0], e[1] - s[1]
                length = math.hypot(vx, vy)
                axis = (vy / length, -vx / length)
            lateral = dx * axis[0] + dy * axis[1]
            if math.hypot(dx - lateral * axis[0], dy - lateral * axis[1]) > 1e-5:
                raise BadRequest("Move this roof accessory laterally or vertically; its other position follows the host roof")
            _attach(project, oid, {"lateral_offset": float(m(meta, "lateral_offset", 0.0)) + lateral,
                                   "vertical_offset": float(m(meta, "vertical_offset", 0.0)) + dz})
        else:
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
    if m(meta, "roof_host"):
        raise BadRequest("This accessory's orientation follows its roof boundary: rotate the roof or edit its offsets")
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
            profile = opening_profile(project, hid)
            if profile:
                project.model.remove(profile)
            project.model.remove(hid)
        project.model.remove(oid)
        corners = project.manifest.get("wall_corners", {})
        if isinstance(corners, dict):
            # A saved corner override references both participants. Once one
            # wall is deleted, no stale preference should affect a later join.
            for key in list(corners):
                if oid in [side.split(":", 1)[0] for side in str(key).split("|")]:
                    del corners[key]
        _refresh_walls(project, affected - {oid})                                   # the walls it joined are square-ended again
    elif cat in OPENINGS:
        host = m(meta, "host")
        profile = opening_profile(project, oid)
        if profile:
            project.model.remove(profile)
        project.model.remove(oid)
        if host and alive(project, host):
            regenerate_wall(project, host)
    elif cat == "opening_profile":
        opening = str(m(meta, "opening", ""))
        project.model.remove(oid)
        if alive(project, opening):
            host = str(m(meta_of(project, opening), "host", ""))
            if alive(project, host):
                regenerate_wall(project, host)
    else:
        from . import gutters as GU
        run = (m(meta, "roof"), GU.run_key(project, oid, meta)) if cat == "gutter" and alive(project, m(meta, "roof")) else None
        for did in GU.dependents(project, oid):                                    # a roof takes its gutters and their downpipes, a gutter its downpipes
            project.model.remove(did)
        if cat == "roof":
            for did in element_ids(project):
                if m(meta_of(project, did), "roof_host") == oid:
                    project.model.remove(did)
        project.model.remove(oid)
        if m(meta, "accessory_kind") == "roof_edge" and alive(project, m(meta, "roof_host")):
            GU.follow(project, str(m(meta, "roof_host")))
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


def plan_items(project: Any, level_id: str, f: float, *, cut_elevation: float | None = None, show_opening: bool = True, show_hatching: bool = True,
               top_elevation: float | None = None, bottom_elevation: float | None = None, depth_elevation: float | None = None) -> list[dict[str, Any]]:
    """What the model shows on the plan of a level, in drawing units (``f`` converts metres). Read from parameters on every call, so it
    can never disagree with the 3D model."""
    out: list[dict[str, Any]] = []
    walls: dict[str, dict[str, Any]] = {}
    corner_table: dict[str, _WallRow] | None = None
    cut_z = level_elevation(project, level_id) + CUT_HEIGHT if cut_elevation is None else float(cut_elevation)
    level_z = level_elevation(project, level_id)
    top_z = cut_z + 1.5 if top_elevation is None else float(top_elevation)
    bottom_z = level_z - 0.2 if bottom_elevation is None else float(bottom_elevation)
    depth_z = level_z - 1.0 if depth_elevation is None else float(depth_elevation)
    if top_z < cut_z - 1e-9 or bottom_z > cut_z + 1e-9 or depth_z > bottom_z + 1e-9:
        raise BadRequest("Plan view range must satisfy top ≥ cut plane ≥ bottom ≥ view depth")
    # The single world transform map is reused for every relevant host. Model
    # elevation, not the level name, determines whether a cut intersects it.
    matrices = None
    def visible_geometry(oid: str, on_level: bool) -> tuple[bool, list[list[list[float]]]]:
        nonlocal matrices
        from . import documentation as DOC
        if matrices is None:
            matrices = project.model._model.world_transforms()
        verts, faces = DOC.world_mesh(project, oid, matrices)
        if not verts:
            return False, []
        low, high = min(v[2] for v in verts), max(v[2] for v in verts)
        if high < depth_z - 1e-9 or low > top_z + 1e-9:
            return False, []
        crosses = low + 1e-8 < cut_z < high - 1e-8
        # The associated level's own elements may be projected within its view
        # range (including overhead beams); unrelated storeys do not leak in.
        if not crosses and not (on_level and high >= depth_z and low <= top_z + 1e-9):
            return False, []
        cuts = DOC.horizontal_cut_loops(verts, faces, cut_z) if crosses else []
        return True, [[[x * f, y * f] for x, y in loop] for loop in cuts]

    wall_cuts: dict[str, float] = {}
    auto_room_spaces: list[dict[str, Any]] | None = None
    for oid in element_ids(project):
        obj = project.model.get(oid)
        meta = obj["metadata"]
        cat = m(meta, "category")
        if cat == "downpipe":
            from . import gutters as GU
            try:
                pipe = GU.pipe_frame(project, oid)
                cut = pipe["bottom"] - 1e-9 <= cut_z <= pipe["top"] + 1e-9
                projected = (m(meta, "level") == level_id and
                             pipe["top"] >= depth_z - 1e-9 and
                             pipe["bottom"] <= top_z + 1e-9)
                if cut or projected:
                    item = GU.pipe_plan_item(project, oid, f)
                    item["cut"] = cut
                    item["overhead"] = not cut and pipe["bottom"] > cut_z
                    out.append(item)
            except BadRequest:
                pass  # Missing gutter: model checking reports the broken association.
            continue
        if cat in ("grid", "array") or cat in OPENINGS:
            # Grids are level-independent, openings are emitted with their
            # host wall, and an array controller has no mesh of its own.
            # Its individual geometric members are processed separately.
            continue
        if cat == "wall":
            frame = wall_frame(project, oid)
            relative_cut = cut_z - frame["base"]
            if not 0.0 <= relative_cut < frame["height"]:
                continue
            wall_cuts[oid] = relative_cut
        elif cat != "room":
            # Imported/legacy metadata groups can also be non-geometric.
            # They are not drawable, but must not make a sheet digest or the
            # entire Project Browser fail. Geometry validation is separate.
            if obj.get("geometry") is None:
                continue
            visible, cut_polygons = visible_geometry(oid, m(meta, "level") == level_id)
            if not visible:
                continue
        elif m(meta, "level") != level_id:
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
            local_height = wall_cuts[oid]
            cuts = sorted(opening_span(project.model.get(h)["metadata"])[:2] for h in hosted(project, oid)
                          if opening_span(project.model.get(h)["metadata"])[2] <= local_height < opening_span(project.model.get(h)["metadata"])[3])
            cursor = 0.0
            from . import layers as LY
            layered = LY.is_layered(meta)
            if corner_table is None:
                corner_table = _wall_table(project)
            corner_joins = wall_corner_controls(project, oid, f, corner_table)
            first = True
            for x0, x1 in cuts + [(fr["length"], fr["length"])]:
                if x0 - cursor > 1e-9:
                    piece = {"id": oid, "category": "wall", "name": obj["name"], "polygon": _wall_piece(fr, cursor, x0, f), "axis": axis,
                             "joins": list(m(meta, "joins") or ())}
                    if corner_joins:
                        piece["corner_joins"] = corner_joins
                    if show_hatching and not layered:
                        from . import documentation as DOC
                        piece["hatch"] = DOC.hatch_pattern(project, m(meta, "material"))
                    if layered and first:                                                                  # every layer's band, once for the whole wall
                        piece["bands"] = LY.plan_bands(project, oid, fr, [{"id": h, "x0": opening_span(project.model.get(h)["metadata"])[0], "x1": opening_span(project.model.get(h)["metadata"])[1]}
                                                                          for h in hosted(project, oid) if opening_span(project.model.get(h)["metadata"])[2] <= local_height < opening_span(project.model.get(h)["metadata"])[3]], f)
                        if show_hatching:
                            from . import documentation as DOC
                            for band in piece["bands"]:
                                band["hatch"] = DOC.hatch_pattern(project, band["material"])
                        first = False
                    out.append(piece)
                cursor = max(cursor, x1)
        elif cat == "opening_profile":
            # Frame profiles are independently modelled SGEO solids. Draw
            # their true horizontal intersection loops, not a symbolic proxy.
            if cut_polygons:
                out.append({"id": oid, "category": "opening_profile",
                            "name": obj["name"], "polygon": cut_polygons[0],
                            "cut_polygons": cut_polygons})
        elif cat == "floor":
            item = {"id": oid, "category": "floor", "name": obj["name"], "polygon": [[q[0] * f, q[1] * f] for q in (m(meta, "cut_outline") or m(meta, "boundary"))],
                    "outline": [[q[0] * f, q[1] * f] for q in m(meta, "boundary")]}
            if cut_polygons:
                item["cut_polygons"] = cut_polygons
                if show_hatching:
                    from . import documentation as DOC
                    item["hatch"] = DOC.hatch_pattern(project, m(meta, "material"))
            if m(meta, "cut"):
                item["holes"] = [[[q[0] * f, q[1] * f] for q in h] for h in m(meta, "cut")]
            out.append(item)
        elif cat == "roof":
            # Roof plans show the roof's projected footprint below the cut
            # plane, including its editable boundary. Sections retain the
            # actual pitched surfaces and material cut geometry.
            boundary = [[q[0] * f, q[1] * f] for q in m(meta, "boundary")]
            item = {"id": oid, "category": "roof", "name": obj["name"], "polygon": boundary, "outline": boundary,
                    **({"cut_polygons": cut_polygons} if cut_polygons else {})}
            ridge = roof_ridge(meta)
            if ridge:
                item["ridge"] = [[p[0] * f, p[1] * f] for p in ridge]
                item["ridge_offset"] = float(m(meta, "ridge_offset", 0.0)) * f
                item["ridge_across"] = list(_rectangle([list(q) for q in m(meta, "boundary")])[2])
            if cut_polygons and show_hatching:
                from . import documentation as DOC
                item["hatch"] = DOC.hatch_pattern(project, m(meta, "material"))
            out.append(item)
        elif cat in FORMS:
            from . import forms as FX
            item = FX.plan_item(project, oid, f)
            if cut_polygons:
                item["cut_polygons"] = cut_polygons
            out.append(item)
        elif cat == "foundation":
            from . import foundations as FD
            out.append(FD.plan_item(project, oid, f))
        elif cat == "structural" and is_column(meta):
            x, y = m(meta, "point")
            ang = float(m(meta, "rotation", 0.0))
            out.append({"id": oid, "category": "column", "name": obj["name"],
                        "polygon": cut_polygons[0] if cut_polygons else _column_outline((x, y), ang, float(m(meta, "width")), float(m(meta, "depth")), f),
                        **({"cut_polygons": cut_polygons} if cut_polygons else {})})
        elif cat == "structural":
            s, e = m(meta, "start"), m(meta, "end")
            ang = math.atan2(e[1] - s[1], e[0] - s[0])
            out.append({"id": oid, "category": "structural", "name": obj["name"],
                        "polygon": cut_polygons[0] if cut_polygons else _rect(tuple(s), ang, 0.0, math.dist(s, e), float(m(meta, "width")) / 2, f),
                        **({"cut_polygons": cut_polygons} if cut_polygons else {})})
        elif cat == "stair":
            from . import stairs as SR
            item = SR.plan_item(project, oid, f)
            if cut_polygons:
                item["cut_polygons"] = cut_polygons
            out.append(item)
        elif cat == "generic":
            from . import families as FM
            item = FM.plan_item(project, oid, f)
            if cut_polygons:
                item["cut_polygons"] = cut_polygons
            out.append(item)
        elif cat == "room":
            from . import rooms as R
            poly = R.boundary_of(meta)
            met = R.metrics(poly)
            valid, warning = True, ""
            if m(meta, "auto"):
                from . import autorooms as AR
                if auto_room_spaces is None:
                    auto_room_spaces = AR.spaces(AR._walls_on(project, level_id), project=project)
                state = R.enclosure_status(project, oid, found=auto_room_spaces)
                valid, warning = bool(state["valid"]), str(state["message"])
            out.append({"id": oid, "category": "room", "name": obj["name"], "polygon": [[q[0] * f, q[1] * f] for q in poly],
                        "label": {"name": obj["name"], "number": str(m(meta, "number", "")),
                                  "area_m2": met["area"] if valid else None,
                                  "volume_m3": R.enclosure(project, oid)["volume"] if valid else None,
                                  "valid": valid, "warning": warning,
                                  "at": [met["centre"][0] * f, met["centre"][1] * f]}})
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
        if fr["base"] + head < depth_z - 1e-9 or fr["base"] + sill > top_z + 1e-9:
            continue  # Opening entirely outside the plan's real view range.
        cut = sill <= wall_cuts[m(meta, "host")] < head
        item: dict[str, Any] = {"id": oid, "category": cat, "name": obj["name"], "polygon": _rect(fr["start"], fr["angle"], x0, x1, fr["thickness"] / 2, f),
                                "cut": cut, "overhead": not cut and fr["base"] + sill >= cut_z}
        item["symbol"] = _symbol(cat, meta, fr, x0, x1, f, show_opening=show_opening and cut)
        out.append(item)
    return out


def _column_outline(centre: tuple[float, float], angle: float, width: float, depth: float, f: float) -> list[list[float]]:
    c, s = math.cos(angle), math.sin(angle)
    return [[(centre[0] + c * dx - s * dy) * f, (centre[1] + s * dx + c * dy) * f]
            for dx, dy in ((-width / 2, -depth / 2), (width / 2, -depth / 2), (width / 2, depth / 2), (-width / 2, depth / 2))]


def opens(meta: Mapping[str, Any]) -> bool:
    """True when a door or window has a leaf that swings: every door, and a window whose operation is a casement."""
    cat = m(meta, "category")
    if cat == "door":
        return any(q["operation"] == "swing" for q in door_panels(meta))
    return cat == "window" and m(meta, "operation", "fixed") == "casement"


def _leaf_symbol(cat: str, fr: Mapping[str, Any], f: float, x0: float, x1: float, swing: int, hinge_side: str) -> dict[str, Any]:
    """One leaf's swing in plan: the hinge, the open leaf's end, and the arc between the open and the closed leaf (angles counter-clockwise)."""
    c, s = math.cos(fr["angle"]), math.sin(fr["angle"])
    at = lambda x, y: ((fr["start"][0] + c * x - s * y) * f, (fr["start"][1] + s * x + c * y) * f)       # noqa: E731
    face = swing * fr["thickness"] / 2 if cat == "door" else 0.0                                         # a sash hangs on the wall's centre line
    hinge_x, free_x = (x0, x1) if hinge_side == "start" else (x1, x0)
    hinge, jamb = at(hinge_x, face), at(free_x, face)
    leaf_end = at(hinge_x, face + swing * (x1 - x0))
    radius = (x1 - x0) * f
    a_leaf = math.atan2(leaf_end[1] - hinge[1], leaf_end[0] - hinge[0])
    a_jamb = math.atan2(jamb[1] - hinge[1], jamb[0] - hinge[0])
    start, end = (a_leaf, a_jamb) if G.cross((math.cos(a_leaf), math.sin(a_leaf)), (math.cos(a_jamb), math.sin(a_jamb))) > 0 else (a_jamb, a_leaf)
    while end <= start:
        end += 2 * math.pi
    return {"hinge": list(hinge), "leaf_end": list(leaf_end), "radius": radius, "start_angle": start, "end_angle": end}


def _symbol(cat: str, meta: Mapping[str, Any], fr: Mapping[str, Any], x0: float, x1: float, f: float, *, show_opening: bool = True) -> dict[str, Any]:
    """The plan symbol of a door or window. The swing arc and open leaf are the opening direction: ``show_opening=False`` leaves them out.
    A door of several panels draws each swinging panel's own leaf: the first in the symbol itself, the others in ``more``."""
    c, s = math.cos(fr["angle"]), math.sin(fr["angle"])
    at = lambda x, y: ((fr["start"][0] + c * x - s * y) * f, (fr["start"][1] + s * x + c * y) * f)       # noqa: E731
    out: dict[str, Any] = {"kind": cat}
    if cat == "window":
        out.update({"a": list(at(x0, 0.0)), "b": list(at(x1, 0.0))})
    if not show_opening or not opens(meta):
        return out
    if cat == "door" and has_panels(meta):
        leaves = [_leaf_symbol(cat, fr, f, px0, px1, q["swing"], q["hinge"]) for q, px0, px1 in panel_spans(meta) if q["operation"] == "swing"]
        out.update(leaves[0])
        if len(leaves) > 1:
            out["more"] = leaves[1:]
        return out
    out.update(_leaf_symbol(cat, fr, f, x0, x1, int(m(meta, "swing", 1)), str(m(meta, "hinge", "start"))))
    return out


def _elevation_leaf(fr: Mapping[str, Any], look: tuple[float, float], x0: float, x1: float, sill: float, head: float, swing: int, hinge_side: str, kind: str) -> dict[str, Any] | None:
    c, s = math.cos(fr["angle"]), math.sin(fr["angle"])
    normal = (-s * swing, c * swing)                                              # the way the leaf opens, in plan
    facing = normal[0] * look[0] + normal[1] * look[1]
    if abs(facing) < 1e-6:
        return None
    hinge_x, free_x = (x0, x1) if hinge_side == "start" else (x1, x0)
    pt = lambda x, z: [fr["start"][0] + c * x, fr["start"][1] + s * x, fr["base"] + z]                      # noqa: E731
    return {"kind": kind, "apex": pt(hinge_x, (sill + head) / 2), "head": pt(free_x, head), "sill": pt(free_x, sill), "toward": facing < 0}


def elevation_symbol(project: Any, oid: str, look: tuple[float, float]) -> dict[str, Any] | None:
    """The opening-direction symbol of a door or window in a section or elevation that looks along ``look`` (a unit vector in plan).

    Two lines run from the hinge edge, at mid height, to the head and the sill of the free edge: the apex is on the hinge side. The symbol
    is *solid* when the leaf opens toward the viewer and *dashed* when it opens away. It returns the three points in world metres
    (``apex``, ``head``, ``sill``, each ``[x, y, z]``) and ``toward``; None for a leaf that does not swing, or a wall seen edge-on (the
    opening is then neither toward nor away).  A door of several panels gives one symbol per swinging panel: the first as the result, the
    others in ``more``.  The caller projects the points onto its own picture."""
    meta = project.model.get(oid)["metadata"]
    if not opens(meta) or not alive(project, m(meta, "host", "")):
        return None
    fr = wall_frame(project, m(meta, "host"))
    x0, x1, sill, head = opening_span(meta)
    kind = str(m(meta, "category"))
    if has_panels(meta):
        found = [sym for q, px0, px1 in panel_spans(meta) if q["operation"] == "swing"
                 if (sym := _elevation_leaf(fr, look, px0, px1, sill, head, q["swing"], q["hinge"], kind)) is not None]
        if not found:
            return None
        return {**found[0], **({"more": found[1:]} if len(found) > 1 else {})}
    return _elevation_leaf(fr, look, x0, x1, sill, head, int(m(meta, "swing", 1)), str(m(meta, "hinge", "start")), kind)


# -------------------------------------------------------------------------------------------------------------------- properties
def _levels(project: Any) -> list[dict[str, str]]:
    return [{"value": l["id"], "label": l["name"]} for l in project.manifest["levels"]]


def _choices(values: tuple[str, ...]) -> list[dict[str, str]]:
    return [{"value": v, "label": v} for v in values]


def properties(project: Any, oid: str, relations: bool = False) -> dict[str, Any]:
    """The Properties panel for any element: its editable parameters first, then read-only figures, then what every element shares
    (identification and custom properties; with ``relations`` also the links to what it is connected to, which costs a little more)."""
    from . import layers as LY
    from . import relations as RL
    out = _properties(project, oid)
    meta = meta_of(project, oid)
    material = m(meta, "material")
    if out.get("kind") == "model" and material:
        shared = project.manifest.get("material_colors", {}).get(material) or MATERIAL_COLORS.get(material, "#BFBFC7")
        at = next((i for i, g in enumerate(out["groups"]) if g["title"] == "Identity"), len(out["groups"]))
        out["groups"].insert(at, {"title": "Appearance", "rows": [
            {"key": "color", "label": "Element color override", "type": "color", "value": m(meta, "color") or "", "text": shared},
            {"key": "material_color", "label": f"Shared {material} color", "type": "color", "value": shared},
        ]})
    group = LY.rows(project, oid) if out.get("kind") == "model" else None
    if group and len(LY.stack(project, oid)) > 1:                               # with several layers the totals are derived: shown, not edited
        for row in out["top"] + [r for g in out["groups"] for r in g["rows"]]:
            if row.get("key") in ("thickness", "material"):
                row["type"] = "readonly"
                row["value"] = f"{row['value']:g} mm" if row["key"] == "thickness" and isinstance(row["value"], (int, float)) else row["value"]
                row.pop("key", None)
                row.pop("unit", None)
                row.pop("choices", None)
    if group:
        at = next((i for i, g in enumerate(out["groups"]) if g["title"] == "Identity"), len(out["groups"]))
        out["groups"].insert(at, group)
    if out.get("kind") == "model" and m(meta_of(project, oid), "category") == "door":
        meta = meta_of(project, oid)
        panels = door_panels(meta)
        if has_panels(meta):                                                      # the hinge and swing belong to the panels now
            for g in out["groups"]:
                g["rows"] = [r for r in g["rows"] if r.get("key") not in ("hinge", "swing")]
        rows: list[dict[str, Any]] = []
        for n, q in enumerate(panels, 1):
            key = lambda field: f"panel:{q['id']}:{field}"                       # noqa: E731
            rows += [{"key": key("name"), "label": f"{n}. Name", "type": "text", "value": q["name"] or f"Panel {n}", "removable": len(panels) > 1},
                     {"key": key("operation"), "label": f"{n}. Operation", "type": "choice", "value": q["operation"], "choices": _choices(PANEL_OPERATIONS)},
                     {"key": key("hinge"), "label": f"{n}. Hinge", "type": "choice", "value": q["hinge"], "choices": _choices(("start", "end"))},
                     {"key": key("swing"), "label": f"{n}. Swing side", "type": "choice", "value": "left" if q["swing"] > 0 else "right", "choices": _choices(("left", "right"))}]
            if len(panels) > 1:
                rows.append({"key": key("width"), "label": f"{n}. Width", "type": "number", "unit": "mm", "value": round(q["width"] * 1000, 6)})
        rows.append({"label": "Total width", "type": "readonly", "value": f"{sum(q['width'] for q in panels) * 1000:g} mm"})
        at = next((i for i, g in enumerate(out["groups"]) if g["title"] == "Identity"), len(out["groups"]))
        out["groups"].insert(at, {"title": f"Panels ({len(panels)})", "rows": rows, "add": "panel"})
    if out.get("kind") == "model" and m(meta_of(project, oid), "category") == "wall":
        from . import attachments as AT
        meta = meta_of(project, oid)
        rows = [{"label": "Top", "type": "readonly", "value": AT.describe(project, meta)}]
        if AT.attachment(meta):
            for row in out["top"]:
                if row.get("key") == "height":                                    # derived while attached: shown, not edited
                    row["type"] = "readonly"
                    row["value"] = f"{row['value']:.3f} m"
                    row.pop("key", None)
                    row.pop("unit", None)
            if m(meta, "top_profile"):
                prof = m(meta, "top_profile")
                rows.append({"label": "Lowest / highest", "type": "readonly", "value": f"{min(h for _, h in prof):.3f} / {max(h for _, h in prof):.3f} m"})
        at = next((i for i, g in enumerate(out["groups"]) if g["title"] == "Identity"), len(out["groups"]))
        out["groups"].insert(at, {"title": "Top constraint", "rows": rows})
    return RL.augment(project, oid, out, relations)


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
                {"label": "Area", "type": "readonly", "value": f"{(sum((b[0] - a[0]) * (a[1] + b[1]) / 2 for a, b in zip(fr['top'], fr['top'][1:])) if fr['top'] else fr['length'] * fr['height']) - open_area:.3f} m²"},
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
        if cat == "window":
            placement.append({"key": "operation", "label": "Operation", "type": "choice", "value": m(meta, "operation", "fixed"), "choices": _choices(WINDOW_OPERATIONS)})
        placement.append({"key": "hinge", "label": "Hinge", "type": "choice", "value": m(meta, "hinge", "start"), "choices": _choices(("start", "end"))})
        if opens(meta):
            placement += [{"key": "swing", "label": "Swing side", "type": "choice", "value": "left" if int(m(meta, "swing", 1)) > 0 else "right", "choices": _choices(("left", "right"))}]
        out["groups"] = [{"title": "Placement", "rows": placement}, {"title": "Identity", "rows": [name_row]}]
        return out
    if cat == "opening_profile":
        opening = str(m(meta, "opening", ""))
        host_name = project.model.get(opening)["name"] if alive(project, opening) else "(missing opening)"
        rows = [{"key": "face_width", "label": "Face width", "type": "number", "unit": "mm", "value": round(float(m(meta, "face_width")) * 1000, 6)},
                {"key": "clearance", "label": "Panel clearance", "type": "number", "unit": "mm", "value": round(float(m(meta, "clearance", 0.003)) * 1000, 6)},
                {"key": "depth_mode", "label": "Profile depth follows", "type": "choice", "value": m(meta, "depth_mode", "host"), "choices": _choices(("host", "fixed"))},
                {"key": "depth", "label": "Depth", "type": "number", "unit": "mm", "value": round(float(m(meta, "depth")) * 1000, 6)},
                {"key": "material", "label": "Material", "type": "choice", "value": m(meta, "material"), "choices": _choices(MATERIALS)}]
        out["groups"] = [{"title": "Opening profile", "rows": [
            {"label": "Host opening", "type": "readonly", "value": host_name},
            {"label": "Sides", "type": "readonly", "value": "3 (jambs/head)" if alive(project, opening) and category(project, opening) == "door" else "4 (jambs/head/sill)"},
            *rows, volume]},
            {"title": "Identity", "rows": [name_row]}]
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
            if m(meta, "roof") == "gable":
                out["top"].append({"key": "ridge_offset", "label": "Ridge offset", "type": "number", "unit": "mm", "value": round(float(m(meta, "ridge_offset", 0.0)) * 1000, 6)})
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
        enclosure = R.enclosure_status(project, oid) if m(meta, "auto") else {"valid": True, "message": ""}
        reliable = bool(enclosure["valid"])
        unknown = "Unavailable — check room boundary"
        out["top"] = [{"label": "Type", "type": "readonly", "value": "Room"},
                      {"key": "number", "label": "Number", "type": "text", "value": d["number"]},
                      {"key": "level", "label": "Level", "type": "choice", "value": m(meta, "level"), "choices": _levels(project)},
                      {"key": "height", "label": "Height", "type": "number", "unit": "m", "value": d["parameter_height"]}]
        out["groups"] = [{"title": "Geometry", "rows": [
                                                        *([{"label": "Boundary", "type": "readonly", "value": enclosure["message"]}] if not reliable else []),
                                                        {"label": "Area", "type": "readonly", "value": f"{d['area']:.3f} m²" if reliable else unknown},
                                                        {"label": "Perimeter", "type": "readonly", "value": f"{d['perimeter']:.3f} m" if reliable else unknown},
                                                        {"label": "Volume", "type": "readonly", "value": f"{d['volume']:.3f} m³ ({R.basis_text(d)})" if reliable else unknown},
                                                        {"label": "Mean height", "type": "readonly", "value": f"{d['height']:.3f} m" if reliable else unknown}]},
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
        roof_host = m(meta, "roof_host")
        if roof_host:
            roof_name = project.model.get(roof_host)["name"] if alive(project, roof_host) else "(missing roof)"
            for row in [*out["top"], *out["groups"][0]["rows"]]:
                if row.get("key") in ("level", "profile", "start_x", "start_y", "end_x", "end_y"):
                    row["type"] = "readonly"
                    row.pop("key", None)
            controls = [{"label": "Host roof", "type": "readonly", "value": roof_name},
                        {"key": "vertical_offset", "label": "Vertical offset", "type": "number", "unit": "mm", "value": float(m(meta, "vertical_offset", 0.0)) * 1000},
                        {"key": "lateral_offset", "label": "Lateral offset", "type": "number", "unit": "mm", "value": float(m(meta, "lateral_offset", 0.0)) * 1000}]
            if m(meta, "accessory_kind") == "ridge_strap":
                controls.append({"key": "span", "label": "Strap span", "type": "number", "unit": "mm", "value": float(m(meta, "span", 0.8)) * 1000})
            if m(meta, "accessory_kind") == "roof_edge":
                controls.append({"label": "Host boundary edge", "type": "readonly", "value": int(m(meta, "edge", 0)) + 1})
            out["groups"][1]["rows"] = controls
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
    room_spaces: dict[str, list[dict[str, Any]]] = {}
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
        elif cat == "opening_profile":
            opening = str(m(meta, "opening", ""))
            if not alive(project, opening) or category(project, opening) not in OPENINGS:
                issues.append(issue("error", oid, f"{label} has lost its door/window opening", "hosting"))
            elif str(m(meta, "wall", "")) != str(m(meta_of(project, opening), "host", "")):
                issues.append(issue("error", oid, f"{label} no longer references its opening's wall", "consistency"))
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
                from . import autorooms as AR
                room_level = str(m(meta, "level"))
                if room_level not in room_spaces:
                    room_spaces[room_level] = AR.spaces(AR._walls_on(project, room_level), project=project)
                enclosure = R.enclosure_status(project, oid, found=room_spaces[room_level])
                if not enclosure["valid"]:
                    issues.append(issue("warning", oid, f"{label}: {enclosure['message']}", "consistency"))
    # The third illustrated corner is deliberately point-contact, unlike a
    # watertight miter or either face-to-face through/butt termination. Keep
    # it available as an explicit design choice and report that consequence.
    for key, mode in (project.manifest.get("wall_corners") or {}).items():
        if mode != "stepped_open":
            continue
        try:
            a, b = key.split("|", 1)
            oid, endpoint = a.rsplit(":", 1)
            other, _ = b.rsplit(":", 1)
            active = alive(project, oid) and any(c["endpoint"] == endpoint and c["other"] == other and c["mode"] == "stepped_open"
                                                for c in wall_corner_controls(project, oid))
        except (ValueError, BadRequest):
            active = False
        if active:
            issues.append(issue("warning", oid, "The stepped/open wall corner intentionally meets at a single point, not a continuous joint; choose a through or miter connection for a watertight corner", "geometry"))
    return issues
