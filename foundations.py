"""Foundations: pad (poer), strip (strook), raft (plaat), foundation beam (funderingsbalk) and the ground-bearing slab (vloer op grondslag).

A foundation is an ordinary SGEO element (category ``foundation``) made from parameters, like every other element, so it is regenerated, moved, copied,
saved and undone the same way. What is modelled:

* **Poer** - a rectangular or round pad of ``width`` x ``length`` x ``height`` centred on ``point``, turned by ``rotation``. Standing on a column
  (``support``) it follows that column's position and turn.
* **Strook** - a strip ``width`` x ``height`` along a line, with an ``offset`` (eccentricity) from that line. Under a wall (``support``) it follows the wall's
  axis; strips that meet are cut to each other with the same joint rule the walls use (mitre, T, star), so the corner is solid once.
* **Funderingsbalk** - a beam of ``width`` x ``height`` between two columns (two ``support`` entries) and follows both.
* **Plaat** - a raft of any simple outline and ``thickness``.
* **Vloer** - a ground-bearing slab of any simple outline and ``thickness``.
  The raft and the slab may carry ``above`` and ``below`` *layers* (insulation, screed, sand bed, blinding...): each is a solid of the same outline, stacked
  upwards from the top of the slab and downwards from its soffit. The slab's thermal resistance is the sum of thickness / conductivity of the layers *whose conductivity
  was given*; if one is missing the figure says it is incomplete rather than assuming a value.

``top`` is the top of the concrete in metres above the level. A foundation that stands on a wall or column is **constrained to it by default**
(``constrain``): when the support moves sideways, turns, is stretched, goes up or down, or changes level, the foundation goes with it and keeps the vertical gap
(``top_gap``, 0 unless asked otherwise) between its top and the underside of what it carries. Switching ``constrain`` off leaves the foundation where it is and
frees it to be moved on its own; switching it on again snaps it back to the support.

**Formwork** is optional (``formwork``: thickness in millimetres, 0 or absent for none; ``formwork_material``). It is modelled as plates of that thickness
against the vertical edges of the concrete, over the concrete's height, in the same element. Where two strips meet the plates stop at the joint, so a corner
is not boxed in twice. Formwork under the base is not modelled.

**Floors.** ``floors`` says what happens to an existing floor the foundation overlaps (in plan and in height): ``adjust`` (default) trims the floor's outline back
to the foundation, ``merge`` lets the floor's outline run over the foundation and lowers the foundation's top to the floor's underside (the foundation keeps
its underside, so the two together are as deep as the foundation was and nothing is counted twice), ``keep`` leaves both alone. The floor's own outline is
remembered (``fd_free``), so moving or deleting the foundation gives the floor its outline back.

**Reinforcement and concrete are declared, not designed.** Bar groups (mark, diameter, spacing or count, role, face), the concrete class and the cover
are metadata the person enters. The *indicative* bar take-off counts the bars those declarations imply (straight bars, no laps, hooks or curtailment) and
a mass when the SLAI engine has a steel density. It is not a design and nothing here checks capacity, bearing pressure, settlement or any structural
code; the quantities and the Properties panel say so.

Limits, stated plainly: no raft edge thickening, no stepped or sloping footings, no piles, no pile caps and no holes in a raft; a round pad is a 48-sided
polygon (its area is 0.14 % below the circle's); a void does not cut a foundation (the void reports it as unsupported); a foundation does not follow
a change in its support's height; and overlaps between foundations are reported (convex pairs only), not removed from the quantities.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from ..tools.errors import BadRequest, NotFound
from ..tools.validation import number, text
from . import elements as E
from . import geometry as G
from . import planar as PL

KINDS: dict[str, dict[str, str]] = {
    "poer": {"name": "Pad foundation (poer)", "type": "Pad Foundation", "sfb": "(16.13)"},
    "strook": {"name": "Strip foundation (strook)", "type": "Strip Foundation", "sfb": "(16.12)"},
    "funderingsbalk": {"name": "Foundation beam (funderingsbalk)", "type": "Foundation Beam", "sfb": "(16.12)"},
    "plaat": {"name": "Raft foundation (plaat)", "type": "Raft Foundation", "sfb": "(16.10)"},
    "vloer": {"name": "Ground-bearing slab (vloer op grondslag)", "type": "Ground-bearing Slab", "sfb": "(13.2)"},
}
LINES = ("strook", "funderingsbalk")
AREAS = ("plaat", "vloer")
LAYERED = AREAS
SHAPES = ("rect", "round")
ROUND_SIDES = 48
PROJECTION = 0.2                        # m: how far a new pad or strip is wider than what stands on it, unless told otherwise
COVER_DEFAULT = 0.05                    # m: the declared cover of a new foundation (an editable parameter, not a code value)
BAR_DIAMETERS = (6, 8, 10, 12, 14, 16, 20, 25, 28, 32, 40)
ROLES: dict[str, tuple[str, ...]] = {"poer": ("x", "y"), "plaat": ("x", "y"), "vloer": ("x", "y"), "strook": ("long", "link"), "funderingsbalk": ("long", "link")}
FACES = ("bottom", "top", "both")
MAX_BAR_GROUPS = 24
MAX_LAYERS = 12
MAX_BARS = 100_000
TOLERANCE = 0.001                       # m: a top this close to the base of what it carries meets it
MATERIALS = ("Concrete", "Masonry")
FORMWORK_MATERIALS = ("EPS", "XPS", "Timber", "Plywood", "Steel")
FORMWORK_DEFAULT_MM = 50
FLOOR_POLICIES = ("adjust", "merge", "keep")
REINFORCED = "Reinforced concrete"      # how a concrete foundation with declared bars is shown: its hatch, tint and 3D colour change with the declaration
PART_COLORS = {"EPS": "#E9E5C8", "XPS": "#9FD4E6", "PIR": "#E8D27A", "Mineral wool": "#E0C95A", "Foam glass": "#B9C9CC", "Sand": "#D9C89A", "Gravel": "#A9A59B",
               "Blinding concrete": "#C9CACD", "Screed": "#D8D4CB", "Floor finish": "#CBB89A", "Damp-proof membrane": "#2B2B2E", "Timber": "#B3804D",
               "Plywood": "#C9A56B", "Steel": "#808FA3", "Concrete": "#B8BABF", "Masonry": "#B86B57", REINFORCED: "#8E939B"}
LAYER_MATERIALS = ("EPS", "XPS", "PIR", "Mineral wool", "Foam glass", "Sand", "Gravel", "Blinding concrete", "Screed", "Concrete", "Floor finish", "Damp-proof membrane")


# -------------------------------------------------------------------------------------------------------------------------- parameters
def kind_of(meta: Mapping[str, Any]) -> str:
    return str(E.m(meta, "kind", "poer"))


def _is(project: Any, oid: str) -> bool:
    return E.alive(project, oid) and E.category(project, oid) == "foundation"


def clean_layers(raw: Any, label: str) -> list[dict[str, Any]]:
    """Validated layers (thickness in mm on the way in, metres in the model). Order is the order they are stacked, away from the slab."""
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)) or len(raw) > MAX_LAYERS:
        raise BadRequest(f"{label} must be a list of at most {MAX_LAYERS} layers")
    out = []
    for n, item in enumerate(raw, 1):
        if not isinstance(item, Mapping):
            raise BadRequest(f"{label}, layer {n}: each layer needs a name, material and thickness")
        material = text(item.get("material"), f"{label}, layer {n} material", 40)
        row: dict[str, Any] = {"name": text(item.get("name") or material, f"{label}, layer {n} name", 60), "material": material,
                               "thickness": number(item.get("thickness"), f"{label}, layer {n} thickness (mm)", 1, 5000) / 1000.0}
        if item.get("conductivity") not in (None, ""):
            row["conductivity"] = number(item["conductivity"], f"{label}, layer {n} conductivity (W/m·K)", 0.005, 500)
        out.append(row)
    return out


def clean_bars(raw: Any) -> list[dict[str, Any]]:
    """Validated reinforcement declarations (see the module notes)."""
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)) or len(raw) > MAX_BAR_GROUPS:
        raise BadRequest(f"Reinforcement must be a list of at most {MAX_BAR_GROUPS} bar groups")
    out = []
    for n, item in enumerate(raw, 1):
        if not isinstance(item, Mapping):
            raise BadRequest(f"Bar group {n}: give a mark, diameter and role")
        diameter = int(number(item.get("diameter"), f"bar group {n} diameter (mm)", 6, 40))
        if diameter not in BAR_DIAMETERS:
            raise BadRequest(f"Bar group {n}: the diameter must be one of {', '.join(str(d) for d in BAR_DIAMETERS)} mm")
        row: dict[str, Any] = {"mark": text(item.get("mark") or f"B{n}", f"bar group {n} mark", 20), "diameter": diameter,
                               "role": text(item.get("role"), f"bar group {n} role", 10), "face": str(item.get("face") or "bottom")}
        if row["face"] not in FACES:
            raise BadRequest(f"Bar group {n}: face must be one of {', '.join(FACES)}")
        spacing, count = item.get("spacing"), item.get("count")
        if (spacing in (None, "")) == (count in (None, "")):
            raise BadRequest(f"Bar group {n}: give either a spacing (mm) or a number of bars, not both and not neither")
        if spacing not in (None, ""):
            row["spacing"] = number(spacing, f"bar group {n} spacing (mm)", 25, 1000)
        else:
            row["count"] = int(number(count, f"bar group {n} count", 1, 10_000))
        out.append(row)
    return out


def check_roles(kind: str, bars: Sequence[Mapping[str, Any]]) -> None:
    for b in bars:
        if b["role"] not in ROLES[kind]:
            raise BadRequest(f"Bar group {b['mark']}: a {KINDS[kind]['name'].split(' (')[0].lower()} takes bars with the role {' or '.join(ROLES[kind])}, not '{b['role']}'")
        if b["role"] == "link" and "spacing" not in b:
            raise BadRequest(f"Bar group {b['mark']}: links are given by spacing")


def _point(value: Any, label: str, to_m: float) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise BadRequest(f"{label} must be a point [x, y]")
    return [float(number(value[0], label, -1e9, 1e9)) * to_m, float(number(value[1], label, -1e9, 1e9)) * to_m]


# ------------------------------------------------------------------------------------------------------------------------------ supports
def support_ids(meta: Mapping[str, Any]) -> list[str]:
    return [str(s) for s in (E.m(meta, "support") or [])]


def support_signature(project: Any, ids: Sequence[str]) -> list[Any]:
    """What a foundation follows in its supports, as plain numbers, so a change can be compared exactly."""
    sig: list[Any] = []
    for sid in ids:
        if not E.alive(project, sid):
            sig.append(None)
            continue
        meta = E.meta_of(project, sid)
        base = round(float(support_base(project, sid) or 0.0), 9)                  # a wall or column that moves up or down changes this
        if E.m(meta, "category") == "wall":
            sig.append([*E.m(meta, "start"), *E.m(meta, "end"), float(E.m(meta, "thickness")), str(E.m(meta, "level")), base])
        elif E.is_column(meta):
            sig.append([*E.m(meta, "point"), float(E.m(meta, "rotation", 0.0)), float(E.m(meta, "width")), float(E.m(meta, "depth")), str(E.m(meta, "level")), base])
        else:
            sig.append("unsupported")
    return sig


def support_base(project: Any, sid: str) -> float | None:
    """Elevation (m, absolute) of the underside of a wall or column, or None if it is gone."""
    if not E.alive(project, sid):
        return None
    meta = E.meta_of(project, sid)
    cat = E.m(meta, "category")
    if cat == "wall":
        return E.level_elevation(project, E.m(meta, "level")) + float(E.m(meta, "base_offset", 0.0))
    if E.is_column(meta):
        return E.level_elevation(project, E.m(meta, "level")) + float(E.m(meta, "base", 0.0))
    return None


def _need_support(project: Any, kind: str, ids: Sequence[str]) -> None:
    want = {"poer": ("column",), "strook": ("wall",), "funderingsbalk": ("column", "column")}[kind]
    if len(ids) != len(want):
        raise BadRequest(f"A {KINDS[kind]['name'].split(' (')[0].lower()} stands on {' and '.join('a ' + w for w in want)}")
    for sid, w in zip(ids, want):
        if not E.alive(project, sid):
            raise NotFound(f"Element {sid} does not exist")
        meta = E.meta_of(project, sid)
        if (w == "wall" and E.m(meta, "category") != "wall") or (w == "column" and not E.is_column(meta)):
            raise BadRequest(f"A {KINDS[kind]['name'].split(' (')[0].lower()} stands on {'a wall' if w == 'wall' else 'a column'}, but {project.model.get(sid)['name'] or sid} is not")


def constrained(project: Any, meta: Mapping[str, Any]) -> bool:
    """Whether the foundation is tied to the wall or column it stands on: it has one that still exists and ``constrain`` has not been switched off."""
    ids = support_ids(meta)
    return bool(ids) and E.m(meta, "constrain", True) is not False and all(E.alive(project, sid) for sid in ids)


def follow(project: Any, oid: str) -> bool:
    """Bring the placement a foundation derives from its supports up to date: where it is in plan, its level and its top (the support's underside plus the
    vertical gap it was given). Returns whether anything changed. A foundation that is not constrained, or whose support has been deleted, stays where it was
    (the model check reports a lost support)."""
    meta = E.meta_of(project, oid)
    kind = kind_of(meta)
    if not constrained(project, meta):
        return False
    ids = support_ids(meta)
    sig = support_signature(project, ids)
    if sig == E.m(meta, "support_sig") and E.m(meta, "top_gap") is not None:
        return False
    sup = [E.meta_of(project, s) for s in ids]
    level = str(E.m(sup[0], "level"))
    base = support_base(project, ids[0])
    gap = E.m(meta, "top_gap")
    if gap is None:                                                              # made before foundations followed vertically: keep the gap it has now
        gap = E.level_elevation(project, E.m(meta, "level")) + float(E.m(meta, "top")) - (base if base is not None else 0.0)
    values: dict[str, Any] = {"support_sig": sig, "top_gap": float(gap), "level": level,
                              "top": (base if base is not None else E.level_elevation(project, level)) - E.level_elevation(project, level) + float(gap)}
    if kind == "poer":
        values.update(point=list(E.m(sup[0], "point")), rotation=float(E.m(sup[0], "rotation", 0.0)))
    elif kind == "strook":
        values.update(start=list(E.m(sup[0], "start")), end=list(E.m(sup[0], "end")))
    elif kind == "funderingsbalk":
        values.update(start=list(E.m(sup[0], "point")), end=list(E.m(sup[1], "point")))
    E._attach(project, oid, values)
    return True


# -------------------------------------------------------------------------------------------------------------------------------- geometry
def line_frame(meta: Mapping[str, Any]) -> dict[str, Any]:
    """The axis of a strip or beam: its ends, direction and the strip's centre line after the eccentricity."""
    s, e = tuple(E.m(meta, "start")), tuple(E.m(meta, "end"))
    length = math.dist(s, e)
    if length < 1e-6:
        raise BadRequest("A strip or foundation beam needs two different end points")
    u = G.unit(G.sub(e, s))
    n = G.left(u)
    off = float(E.m(meta, "offset", 0.0))
    start, end = (s[0] + n[0] * off, s[1] + n[1] * off), (e[0] + n[0] * off, e[1] + n[1] * off)
    return {"start": start, "end": end, "u": u, "n": n, "length": length, "width": float(E.m(meta, "width"))}


def _line_table(project: Any, level: str) -> dict[str, Any]:
    """Every strip and foundation beam of a level as a joint-rule row (see ``elements.join_ends``)."""
    table: dict[str, Any] = {}
    for oid in E.all_ids(project.model, metadata={f"{E.NS}:category": "foundation"}):
        meta = E.meta_of(project, oid)
        if kind_of(meta) in LINES and E.m(meta, "level") == level:
            try:
                fr = line_frame(meta)
            except BadRequest:
                continue
            table[oid] = E._WallRow(level, fr["start"], fr["end"], fr["width"], ())
    return table


def derived_ends(project: Any, oid: str, table: Mapping[str, Any] | None = None) -> dict[str, Any]:
    meta = E.meta_of(project, oid)
    fr = line_frame(meta)
    rows = table if table is not None else _line_table(project, str(E.m(meta, "level")))
    rows = dict(rows)
    rows[oid] = E._WallRow(str(E.m(meta, "level")), fr["start"], fr["end"], fr["width"], ())
    return E.join_ends(rows, oid, fr["start"], fr["end"], fr["width"])


def line_polygon(meta: Mapping[str, Any]) -> list[tuple[float, float]]:
    fr = line_frame(meta)
    (sl, sr), (el, er) = E.m(meta, "ends") or G.SQUARE_ENDS
    s, e, u, n, h = fr["start"], fr["end"], fr["u"], fr["n"], fr["width"] / 2
    pts = [(s[0] - u[0] * sr - n[0] * h, s[1] - u[1] * sr - n[1] * h), (e[0] + u[0] * er - n[0] * h, e[1] + u[1] * er - n[1] * h),
           (e[0] + u[0] * el + n[0] * h, e[1] + u[1] * el + n[1] * h), (s[0] - u[0] * sl + n[0] * h, s[1] - u[1] * sl + n[1] * h)]
    if G.signed_area(pts) < 1e-9:
        raise BadRequest("The joints at the two ends of this strip cut it away entirely")
    return pts


def pad_polygon(meta: Mapping[str, Any]) -> list[tuple[float, float]]:
    x, y = E.m(meta, "point")
    w, l, a = float(E.m(meta, "width")), float(E.m(meta, "length")), float(E.m(meta, "rotation", 0.0))
    c, s = math.cos(a), math.sin(a)
    if E.m(meta, "shape", "rect") == "round":                                  # width is the diameter
        r = w / 2
        return [(x + r * math.cos(2 * math.pi * k / ROUND_SIDES), y + r * math.sin(2 * math.pi * k / ROUND_SIDES)) for k in range(ROUND_SIDES)]
    return [(x + px * c - py * s, y + px * s + py * c) for px, py in ((-w / 2, -l / 2), (w / 2, -l / 2), (w / 2, l / 2), (-w / 2, l / 2))]


def outline(meta: Mapping[str, Any]) -> list[tuple[float, float]]:
    """The plan outline of a foundation (its concrete)."""
    kind = kind_of(meta)
    if kind == "poer":
        return pad_polygon(meta)
    if kind in LINES:
        return line_polygon(meta)
    return [(float(q[0]), float(q[1])) for q in E.m(meta, "boundary")]


def depth_of(meta: Mapping[str, Any]) -> float:
    return float(E.m(meta, "thickness" if kind_of(meta) in AREAS else "height"))


def stack(project: Any, meta: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The solids of a foundation, top to bottom, each ``{name, material, z0, z1, concrete}`` (absolute metres). The concrete is one entry."""
    top = E.level_elevation(project, E.m(meta, "level")) + float(E.m(meta, "top"))
    depth = depth_of(meta)
    out: list[dict[str, Any]] = []
    z = top
    stacked = []
    for ly in E.m(meta, "above") or []:                                        # the first layer sits on the slab, the last is the surface
        stacked.append({"name": ly["name"], "material": ly["material"], "z0": z, "z1": z + float(ly["thickness"]), "concrete": False, "conductivity": ly.get("conductivity")})
        z += float(ly["thickness"])
    out += stacked[::-1]
    out.append({"name": "Concrete", "material": str(E.m(meta, "material", "Concrete")), "z0": top - depth, "z1": top, "concrete": True, "conductivity": E.m(meta, "conductivity")})
    z = top - depth
    for ly in E.m(meta, "below") or []:
        out.append({"name": ly["name"], "material": ly["material"], "z0": z - float(ly["thickness"]), "z1": z, "concrete": False, "conductivity": ly.get("conductivity")})
        z -= float(ly["thickness"])
    return out


def display_material(meta: Mapping[str, Any]) -> str:
    """The material a foundation is *shown* in: concrete with declared bars reads as reinforced concrete (hatch, tint and 3D colour), so declaring or removing
    reinforcement changes how the foundation looks everywhere without touching its ``material``."""
    material = str(E.m(meta, "material", "Concrete"))
    return REINFORCED if material == "Concrete" and E.m(meta, "bars") else material


def formwork_of(meta: Mapping[str, Any]) -> tuple[float, str] | None:
    """(thickness in metres, material) of the formwork, or None when there is none."""
    t = E.m(meta, "formwork")
    if not t or float(t) <= 0:
        return None
    return float(t), str(E.m(meta, "formwork_material") or FORMWORK_MATERIALS[0])


def _joined_ends(project: Any, oid: str, meta: Mapping[str, Any]) -> tuple[bool, bool]:
    """Whether the start and the end of a strip meet another strip (end to end, or against its side). The end face of such a strip is inside the joint, so it has no formwork."""
    if kind_of(meta) not in LINES:
        return False, False
    fr = line_frame(meta)
    level = str(E.m(meta, "level"))
    out = [False, False]
    table = _line_table(project, level)
    for k, point in enumerate((fr["start"], fr["end"])):
        for xid, row in table.items():
            if xid == oid:
                continue
            if min(math.dist(point, row.start), math.dist(point, row.end)) <= E.JOIN_TOLERANCE:
                out[k] = True
                break
            dist, along = E._distance_to_segment(point, row.start, row.end)
            if E.JOIN_TOLERANCE < along < math.dist(row.start, row.end) - E.JOIN_TOLERANCE and dist <= row.thickness / 2 + E.JOIN_TOLERANCE:
                out[k] = True
                break
    return out[0], out[1]


def formwork_plates(project: Any, oid: str | None, meta: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The formwork as one plate per vertical edge of the concrete: ``{"quad": 4 plan points, "inner_length": m}``. Each edge is offset outwards by the
    thickness, except the end faces of a strip that meet another strip; the corner points are where neighbouring offset lines meet (a joined end uses its own
    joint line), so plates join without gaps or overlaps at the outer corner of a mitre and stop at the inner one."""
    spec = formwork_of(meta)
    if spec is None:
        return []
    t = spec[0]
    pts = [tuple(q) for q in outline(meta)]
    if G.signed_area(pts) < 0:
        pts.reverse()
    n = len(pts)
    skip: set[int] = set()
    if kind_of(meta) in LINES and oid is not None:
        start_joined, end_joined = _joined_ends(project, oid, meta)
        if start_joined:
            skip.add(3)
        if end_joined:
            skip.add(1)
    normals, dist = [], []
    for i in range(n):
        d = G.unit(G.sub(pts[(i + 1) % n], pts[i]))
        normals.append((d[1], -d[0]))                                           # to the right of a counter-clockwise outline: outwards
        dist.append(0.0 if i in skip else t)
    out: list[tuple[float, float]] = []
    for i in range(n):
        n0, n1, d0, d1 = normals[i - 1], normals[i], dist[i - 1], dist[i]
        det = n0[0] * n1[1] - n0[1] * n1[0]
        c0 = G.dot(n0, pts[i]) + d0                                             # the two offset lines through this corner: n . x = c
        c1 = G.dot(n1, pts[i]) + d1
        if abs(det) < 1e-6:                                                      # (nearly) collinear edges: the corner just moves out
            out.append((pts[i][0] + n1[0] * d1, pts[i][1] + n1[1] * d1))
            continue
        out.append(((c0 * n1[1] - c1 * n0[1]) / det, (n0[0] * c1 - n1[0] * c0) / det))
        if math.dist(out[-1], pts[i]) > 8 * max(t, 1e-9):
            raise BadRequest("A corner of this foundation is too sharp for formwork of that thickness")
    plates = []
    for i in range(n):
        if i in skip:
            continue
        j = (i + 1) % n
        plates.append({"quad": [pts[i], pts[j], out[j], out[i]], "length": math.dist(pts[i], pts[j])})
    return plates


def concrete_z(project: Any, meta: Mapping[str, Any]) -> tuple[float, float]:
    top = E.level_elevation(project, E.m(meta, "level")) + float(E.m(meta, "top"))
    return top - depth_of(meta), top


def parts_of(project: Any, oid: str | None, meta: Mapping[str, Any]) -> tuple[G.Mesh, list[dict[str, Any]]]:
    """The mesh of a foundation and, in the order its faces run, ``{material, faces}`` for each solid (the concrete and its layers, then all the formwork)."""
    poly = outline(meta)
    if not G.is_simple_polygon(poly) or abs(G.signed_area(poly)) < 1e-9:
        raise BadRequest("The outline crosses itself or has no area")
    verts: list[Any] = []
    faces: list[Any] = []
    parts: list[dict[str, Any]] = []

    def add(shape: Sequence[tuple[float, float]], z0: float, z1: float) -> int:
        v, f = G.prism(shape, z0, z1)
        base = len(verts)
        verts.extend(list(p) for p in v)
        faces.extend([a + base, b + base, c + base] for a, b, c in f)
        return len(f)

    for part in stack(project, meta):
        parts.append({"material": display_material(meta) if part["concrete"] else str(part["material"]), "faces": add(poly, part["z0"], part["z1"])})
    plates = formwork_plates(project, oid, meta)
    if plates:
        z0, z1 = concrete_z(project, meta)
        spec = formwork_of(meta)
        count = 0
        for plate in plates:
            if abs(G.signed_area(plate["quad"])) > 1e-9:
                count += add(plate["quad"], z0, z1)
        parts.append({"material": spec[1] if spec else "Timber", "faces": count, "formwork": True})
    return E._positive_volume((verts, faces)), parts


def build_mesh(project: Any, meta: Mapping[str, Any], oid: str | None = None) -> G.Mesh:
    return parts_of(project, oid, meta)[0]


def loop_materials(project: Any, meta: Mapping[str, Any], loops: Sequence[Sequence[tuple[float, float]]]) -> list[str]:
    """The material of each cut loop of a vertical section: a loop's height (screen y is height) says which layer of the build-up it is. Where formwork stands at the
    same height as the concrete, the largest loop at that height is the concrete and the others are the formwork."""
    parts = stack(project, meta)
    shown = display_material(meta)
    spec = formwork_of(meta)
    z0, z1 = concrete_z(project, meta)

    def area(loop: Sequence[tuple[float, float]]) -> float:
        return abs(G.signed_area([tuple(p) for p in loop]))

    in_concrete = [k for k, loop in enumerate(loops) if z0 - 1e-9 <= (min(p[1] for p in loop) + max(p[1] for p in loop)) / 2 <= z1 + 1e-9]
    main = max(in_concrete, key=lambda k: area(loops[k]), default=None)
    out = []
    for k, loop in enumerate(loops):
        mid = (min(p[1] for p in loop) + max(p[1] for p in loop)) / 2
        if spec is not None and k in in_concrete and k != main:
            out.append(spec[1])
            continue
        hit = next((s for s in parts if s["z0"] - 1e-9 <= mid <= s["z1"] + 1e-9), None)
        if hit is None:
            out.append(shown)
        else:
            out.append(shown if hit["concrete"] else str(hit["material"]))
    return out


def regenerate(project: Any, oid: str) -> None:
    """Rebuild one foundation from its parameters: placement from its supports, joints from the strips it meets, then the solids."""
    follow(project, oid)
    meta = E.meta_of(project, oid)
    kind = kind_of(meta)
    if depth_of(meta) < 0.01:
        raise BadRequest("A foundation must be at least 10 mm thick")
    if kind in LINES:
        j = derived_ends(project, oid)
        E._attach(project, oid, {"ends": [list(j["ends"][0]), list(j["ends"][1])], "joins": j["with"]})
        meta = E.meta_of(project, oid)
    try:
        mesh, parts = parts_of(project, oid, meta)
        E._set_mesh(project, oid, mesh)
        E._attach(project, oid, {"part_faces": [p["faces"] for p in parts], "part_materials": [p["material"] for p in parts]})
    except G.GeometryError as exc:
        raise BadRequest(str(exc)) from exc


def part_colors(project: Any, meta: Mapping[str, Any], face_count: int) -> list[dict[str, Any]]:
    """The colour of each solid of a foundation for the 3D view, in face order (empty if the stored parts no longer match the mesh). An override colour set on the
    element paints the concrete only."""
    counts, mats = E.m(meta, "part_faces") or [], E.m(meta, "part_materials") or []
    if not counts or len(counts) != len(mats) or sum(int(c) for c in counts) != face_count:
        return []
    palette = project.manifest.get("material_colors", {})
    out = []
    for n, mat in zip(counts, mats):
        concrete = mat in (REINFORCED, str(E.m(meta, "material", "Concrete")))
        color = (E.m(meta, "color") if concrete else None) or palette.get(mat) or PART_COLORS.get(mat) or E.MATERIAL_COLORS.get(mat)
        out.append({"count": int(n), "color": color})
    return out


def reconcile(project: Any) -> list[str]:
    """After any command: foundations follow the walls and columns they stand on, a foundation that merges with a floor drops to the floor's underside, strips
    re-cut their joints to their neighbours, and the floors they overlap are trimmed or merged. Only what changed is rebuilt. Runs inside the command, so a
    foundation that can no longer be built rolls the whole command back."""
    ids = E.all_ids(project.model, metadata={f"{E.NS}:category": "foundation"})
    if not ids:
        _restore_floors(project)
        return []
    dirty: set[str] = set()
    for oid in ids:
        if follow(project, oid):
            dirty.add(oid)
    dirty |= set(merge_drops(project, ids))
    tables: dict[str, dict[str, Any]] = {}
    for oid in ids:
        meta = E.meta_of(project, oid)
        if kind_of(meta) not in LINES:
            continue
        level = str(E.m(meta, "level"))
        table = tables.get(level) or tables.setdefault(level, _line_table(project, level))
        if oid not in table:
            continue
        j = derived_ends(project, oid, table)
        stored = E.m(meta, "ends")
        if stored is None or [list(map(float, p)) for p in stored] != [list(p) for p in j["ends"]] or list(E.m(meta, "joins") or []) != j["with"]:
            dirty.add(oid)
    for oid in sorted(dirty):
        regenerate(project, oid)
    reconcile_floors(project)
    return sorted(dirty)


# ------------------------------------------------------------------------------------------------------------------------------ floors
def _bbox(ring: Sequence[Sequence[float]]) -> tuple[float, float, float, float]:
    xs, ys = [q[0] for q in ring], [q[1] for q in ring]
    return min(xs), min(ys), max(xs), max(ys)


def _near(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def _rings(project: Any) -> list[dict[str, Any]]:
    """Every foundation that deals with floors (policy adjust or merge) as a plan ring and the height range of its concrete."""
    rows = []
    for oid in E.all_ids(project.model, metadata={f"{E.NS}:category": "foundation"}):
        meta = E.meta_of(project, oid)
        policy = str(E.m(meta, "floors", "adjust"))
        if policy == "keep":
            continue
        try:
            ring = [(float(q[0]), float(q[1])) for q in outline(meta)]
        except BadRequest:
            continue
        z0, z1 = concrete_z(project, meta)
        rows.append({"id": oid, "policy": policy, "ring": ring, "z0": z0, "z1": z1, "bbox": _bbox(ring)})
    return rows


def _floor_z(project: Any, meta: Mapping[str, Any]) -> tuple[float, float]:
    top = E.level_elevation(project, E.m(meta, "level")) + float(E.m(meta, "offset", 0.0))
    return top - float(E.m(meta, "thickness")), top


def _shares_plan(a: Sequence[tuple[float, float]], b: Sequence[tuple[float, float]]) -> bool:
    try:
        return any(abs(PL.region_area(r)) > 1e-6 for r in PL.intersection(a, b))
    except PL.PlanarError:
        return True                                                              # the exact overlap could not be worked out: treat it as overlapping, the result is checked next


def merge_drops(project: Any, ids: Sequence[str]) -> list[str]:
    """A foundation that merges with a floor stops at the floor's underside: its top is lowered to it and its height shortened by the same amount, so its underside
    stays where it was. Returns the foundations changed."""
    changed: list[str] = []
    floors = E.all_ids(project.model, metadata={f"{E.NS}:category": "floor"})
    if not floors:
        return changed
    for oid in ids:
        meta = E.meta_of(project, oid)
        if str(E.m(meta, "floors", "adjust")) != "merge":
            continue
        try:
            ring = [(float(q[0]), float(q[1])) for q in outline(meta)]
        except BadRequest:
            continue
        z0, z1 = concrete_z(project, meta)
        soffit = None
        for fl in floors:
            fm = E.meta_of(project, fl)
            if E.m(fm, "level") != E.m(meta, "level") and abs(_floor_z(project, fm)[1] - z1) > 5.0:
                continue
            f0, f1 = _floor_z(project, fm)
            if not (min(f1, z1) - max(f0, z0) > 1e-6):                            # only a floor the foundation really reaches into
                continue
            if not _near(_bbox(ring), _bbox([(float(q[0]), float(q[1])) for q in E.m(fm, "boundary")])):
                continue
            if _shares_plan(ring, [(float(q[0]), float(q[1])) for q in E.m(fm, "boundary")]):
                soffit = f0 if soffit is None else min(soffit, f0)
        if soffit is None or soffit >= z1 - 1e-6:
            continue
        if soffit <= z0 + 0.01:
            continue                                                             # the floor is as deep as the foundation: nothing to merge into (reported by the model check)
        level_z = E.level_elevation(project, E.m(meta, "level"))
        values: dict[str, Any] = {"top": soffit - level_z, ("thickness" if kind_of(meta) in AREAS else "height"): soffit - z0}
        if constrained(project, meta):
            base = support_base(project, support_ids(meta)[0])
            values["top_gap"] = soffit - (base if base is not None else level_z)
            values["support_sig"] = None
        E._attach(project, oid, values)
        changed.append(oid)
    return changed


def _ring_list(ring: Sequence[Sequence[float]]) -> list[list[float]]:
    return [[round(float(q[0]), 7), round(float(q[1]), 7)] for q in ring]


def _same_ring(a: Sequence[Sequence[float]], b: Sequence[Sequence[float]]) -> bool:
    return len(a) == len(b) and all(abs(p[0] - q[0]) < 1e-6 and abs(p[1] - q[1]) < 1e-6 for p, q in zip(a, b))


def _restore_floors(project: Any) -> None:
    """With no foundation left, every floor a foundation had trimmed or extended gets its own outline back."""
    reconcile_floors(project)


def reconcile_floors(project: Any) -> dict[str, list[Any]]:
    """Trim or merge the floors the foundations overlap (see the module text) and give a floor its own outline back when nothing overlaps it any more.
    Returns ``{"adjusted", "merged", "restored", "unresolved"}`` (floor ids; unresolved are ``[floor, foundation, reason]``)."""
    report: dict[str, list[Any]] = {"adjusted": [], "merged": [], "restored": [], "unresolved": []}
    floors = E.all_ids(project.model, metadata={f"{E.NS}:category": "floor"})
    if not floors:
        return report
    rows = _rings(project)
    for fl in floors:
        meta = E.meta_of(project, fl)
        current = [[float(q[0]), float(q[1])] for q in E.m(meta, "boundary")]
        free = E.m(meta, "fd_free")
        applied = E.m(meta, "fd_applied")
        if free is not None and (applied is None or not _same_ring(current, applied)):
            free = None                                                          # the person changed the outline themselves: that is the floor's own outline now
        base = [(float(q[0]), float(q[1])) for q in (free if free is not None else current)]
        f0, f1 = _floor_z(project, meta)
        box = _bbox(base)
        cuts, adds, owners = [], [], {}
        for r in rows:
            if not _near(r["bbox"], box):
                continue
            reach = min(f1, r["z1"]) - max(f0, r["z0"])
            if r["policy"] == "adjust" and reach > 1e-6:
                bucket = cuts
            elif r["policy"] == "merge" and reach >= -1e-6:
                bucket = adds
            else:
                continue
            if _shares_plan(base, r["ring"]):
                bucket.append(r["ring"])
                owners[id(r["ring"])] = r["id"]
        unresolved: list[list[str]] = []
        if not cuts and not adds:
            if free is not None:                                                 # nothing overlaps any more: the floor gets its own outline back
                E._attach(project, fl, {"boundary": [list(q) for q in free], "fd_free": None, "fd_applied": None, "fd_unresolved": None})
                E.regenerate_floor(project, fl)
                report["restored"].append(fl)
            elif E.m(meta, "fd_unresolved"):
                E._attach(project, fl, {"fd_unresolved": None})
            continue
        try:
            regions = PL.difference(base, cuts) if cuts else [(base, [])]
            if adds:
                if any(h for _, h in regions):
                    raise PL.PlanarError("the floor would be left with a hole in it")
                regions = PL.union([reg[0] for reg in regions] + adds)
        except PL.PlanarError as exc:
            regions, why = [], str(exc)
        else:
            why = ""
            if not regions:
                why = "the foundation covers the whole floor"
            elif len(regions) > 1:
                why = "the floor would be split in two"
            elif regions[0][1]:
                why = "the floor would be left with a hole in it"
        if why:
            who = sorted({owners[id(r)] for r in cuts + adds})
            note = [[o, why] for o in who]
            values: dict[str, Any] = {}
            if E.m(meta, "fd_unresolved") != note:
                values["fd_unresolved"] = note
            if free is not None:                                                 # what an earlier trim left is no longer right: the floor is as its owner drew it
                values.update(boundary=[list(q) for q in free], fd_free=None, fd_applied=None)
            if values:
                E._attach(project, fl, values)
                if free is not None:
                    E.regenerate_floor(project, fl)
            report["unresolved"] += [[fl, o, why] for o in who]
            continue
        new = _ring_list(regions[0][0])
        if _same_ring(new, current):
            if E.m(meta, "fd_free") is None or E.m(meta, "fd_unresolved"):
                E._attach(project, fl, {"fd_free": _ring_list(base), "fd_applied": new, "fd_unresolved": None})
            continue
        E._attach(project, fl, {"boundary": new, "fd_free": _ring_list(base), "fd_applied": new, "fd_unresolved": None})
        E.regenerate_floor(project, fl)
        report["merged" if adds and not cuts else "adjusted"].append(fl)
    return report


# ---------------------------------------------------------------------------------------------------------------------------- creation
def _flag(value: Any, label: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false", "yes", "no", "on", "off"):
        return value.strip().lower() in ("true", "yes", "on")
    raise BadRequest(f"{label} must be true or false")


def _formwork_values(p: Mapping[str, Any]) -> dict[str, Any]:
    """The formwork parameters of a request: ``formwork`` (thickness, mm; 0 or empty for none) and ``formwork_material``."""
    out: dict[str, Any] = {}
    if "formwork" in p:
        raw = p["formwork"]
        if raw in (None, "", False, 0):
            out["formwork"] = None
        else:
            out["formwork"] = number(FORMWORK_DEFAULT_MM if raw is True else raw, "formwork thickness (mm)", 5, 500) / 1000.0
    if "formwork_material" in p and p["formwork_material"] not in (None, ""):
        out["formwork_material"] = E._choice(p["formwork_material"], FORMWORK_MATERIALS, "formwork material")
    elif out.get("formwork"):
        out["formwork_material"] = FORMWORK_MATERIALS[0]
    return out


def _levelled(project: Any, p: Mapping[str, Any]) -> dict[str, Any]:
    return project.level(text(p.get("level"), "level", 80))


def add_foundation(project: Any, p: Mapping[str, Any], to_m: float) -> str:
    """Make a foundation. ``kind`` is one of ``KINDS``. Points are in drawing units, sizes in millimetres, ``top`` in metres above the level (when it
    is left out and the foundation stands on a wall or column, the base of that support)."""
    kind = p.get("kind")
    if kind not in KINDS:
        raise BadRequest(f"kind must be one of {', '.join(KINDS)}")
    support = [text(s, "support", 80) for s in (p.get("support") or [])] if kind in ("poer", "strook", "funderingsbalk") else []
    if p.get("support") and kind in AREAS:
        raise BadRequest("A raft or slab does not stand on a single wall or column")
    values: dict[str, Any] = {"category": "foundation", "kind": kind, "type": KINDS[kind]["type"], "material": E._choice(p.get("material", "Concrete"), MATERIALS, "material"),
                              "support": support, "cover": number(p.get("cover", COVER_DEFAULT * 1000), "cover (mm)", 10, 150) / 1000.0,
                              "floors": E._choice(p.get("floors", "adjust"), FLOOR_POLICIES, "floors")}
    if support:
        values["constrain"] = _flag(p.get("constrain", True), "constrain")
    values.update(_formwork_values(p))
    if p.get("concrete_class") not in (None, ""):
        values["concrete_class"] = text(p["concrete_class"], "concrete class", 20)
    if p.get("steel") not in (None, ""):
        values["steel"] = text(p["steel"], "reinforcement steel", 20)
    level_id: str | None = None
    sup_meta = None
    if kind in ("poer", "strook", "funderingsbalk"):
        if support or kind in ("funderingsbalk",):
            _need_support(project, kind, support)
            sup_meta = [E.meta_of(project, s) for s in support]
            level_id = str(E.m(sup_meta[0], "level"))
            if len({str(E.m(s, "level")) for s in sup_meta}) > 1:
                raise BadRequest("The columns of a foundation beam are on different levels")
        elif kind == "strook" and not (p.get("start") and p.get("end")):
            raise BadRequest("A strip needs a wall to stand under, or a start and an end")
    level = project.level(text(p.get("level") or level_id, "level", 80)) if (p.get("level") or level_id) else _levelled(project, p)
    values["level"] = level["id"]
    if level_id and level_id != level["id"]:
        raise BadRequest("The foundation must be on the level of what it carries")
    top = p.get("top")
    if top in (None, ""):
        base = support_base(project, support[0]) if support else None
        top = (base - E.level_elevation(project, level["id"])) if base is not None else 0.0
    values["top"] = number(top, "top", -100, 100)
    if support:                                                                  # the gap it keeps from the underside of what it carries (0 when it starts at that underside)
        base_now = support_base(project, support[0])
        values["top_gap"] = (E.level_elevation(project, level["id"]) + values["top"] - base_now) if base_now is not None else 0.0
    if kind == "poer":
        shape = E._choice(p.get("shape", "rect"), SHAPES, "shape")
        if sup_meta:
            col = sup_meta[0]
            point = list(E.m(col, "point"))
            width = number(p.get("width", (float(E.m(col, "width")) + 2 * PROJECTION) * 1000), "width", 100, 20000) / 1000.0
            length = number(p.get("length", (float(E.m(col, "depth")) + 2 * PROJECTION) * 1000), "length", 100, 20000) / 1000.0
            rotation = float(E.m(col, "rotation", 0.0))
        else:
            point = _point(p.get("point"), "point", to_m)
            width = number(p.get("width", 1200), "width", 100, 20000) / 1000.0
            length = number(p.get("length", 1200), "length", 100, 20000) / 1000.0
            rotation = math.radians(number(p.get("rotation", 0.0), "rotation", -360, 360))
        if shape == "round":
            length = width
        values.update(shape=shape, point=point, width=width, length=length, rotation=rotation, height=number(p.get("height", 500), "height", 10, 10000) / 1000.0)
    elif kind in LINES:
        if sup_meta:
            s0, e0 = (list(E.m(sup_meta[0], "start")), list(E.m(sup_meta[0], "end"))) if kind == "strook" else (list(E.m(sup_meta[0], "point")), list(E.m(sup_meta[1], "point")))
        else:
            s0, e0 = _point(p.get("start"), "start", to_m), _point(p.get("end"), "end", to_m)
        default = ((float(E.m(sup_meta[0], "thickness")) + 2 * PROJECTION) * 1000 if kind == "strook" and sup_meta else 600)
        values.update(start=s0, end=e0, width=number(p.get("width", default), "width", 50, 20000) / 1000.0, height=number(p.get("height", 500), "height", 10, 10000) / 1000.0,
                      offset=number(p.get("offset", 0.0), "offset (mm)", -10000, 10000) / 1000.0)
    else:
        values.update(boundary=E._boundary(p.get("points"), to_m, KINDS[kind]["name"].split(" (")[0].lower()), thickness=number(p.get("thickness", 250 if kind == "vloer" else 400), "thickness", 10, 10000) / 1000.0)
        values["above"] = clean_layers(p.get("above"), "Layers above")
        values["below"] = clean_layers(p.get("below"), "Layers below")
        if p.get("conductivity") not in (None, ""):
            values["conductivity"] = number(p["conductivity"], "concrete conductivity (W/m·K)", 0.005, 500)
    bars = clean_bars(p.get("bars"))
    check_roles(kind, bars)
    values["bars"] = bars
    name = text(p.get("name") or KINDS[kind]["name"].split(" (")[0], "name", 80)
    oid = project.model.add_box(width=1.0, depth=1.0, height=1.0, name=name)
    E._attach(project, oid, values)
    try:
        if support:
            E._attach(project, oid, {"support_sig": support_signature(project, support)})
        regenerate(project, oid)
    except BadRequest:
        project.model.remove(oid)
        raise
    from . import sfb as SF
    try:
        SF.set_code(project, oid, "sfb", KINDS[kind]["sfb"])
    except BadRequest:
        pass                                                                    # the code is a convenience; a missing table row must not stop the foundation
    return oid


def add_under(project: Any, p: Mapping[str, Any], to_m: float) -> list[str]:
    """One foundation for each of ``supports`` (walls get strips, columns get pads) with the same sizes. All or none (the command rolls back)."""
    ids = [text(s, "support", 80) for s in (p.get("supports") or [])]
    if not ids or len(ids) > 500:
        raise BadRequest("supports must be a list of 1 to 500 walls and columns")
    out = []
    for sid in ids:
        if not E.alive(project, sid):
            raise NotFound(f"Element {sid} does not exist")
        meta = E.meta_of(project, sid)
        kind = "strook" if E.m(meta, "category") == "wall" else "poer" if E.is_column(meta) else None
        if kind is None:
            raise BadRequest(f"{project.model.get(sid)['name'] or sid} is neither a wall nor a column")
        q = {k: v for k, v in p.items() if k != "supports"}
        out.append(add_foundation(project, {**q, "kind": kind, "support": [sid], "name": f"{'Strip' if kind == 'strook' else 'Pad'} under {project.model.get(sid)['name'] or sid}"}, to_m))
    return out


# ---------------------------------------------------------------------------------------------------------------------------------- edits
def set_property(project: Any, oid: str, meta: Mapping[str, Any], key: str, value: Any) -> dict[str, Any]:
    """The parameters changed by one Properties edit (the caller stores them and rebuilds). Raises BadRequest for a bad value."""
    kind = kind_of(meta)
    sized = {"width": (50, 20000), "length": (100, 20000), "height": (10, 10000), "thickness": (10, 10000)}
    if key == "length" and kind == "poer" and E.m(meta, "shape") == "round":
        raise BadRequest("A round pad has one size: its diameter (width)")
    if key in ("width", "length", "height", "thickness") and (key in ("width", "height") and kind in ("poer", *LINES) or key == "length" and kind == "poer" or key == "thickness" and kind in AREAS):
        v = number(value, key, *sized[key]) / 1000.0
        out = {key: v}
        if key == "width" and kind == "poer" and E.m(meta, "shape", "rect") == "round":
            out["length"] = v
        return out
    if key == "top":
        top = number(value, "top", -100, 100)
        base = support_base(project, support_ids(meta)[0]) if constrained(project, meta) else None
        out = {"top": top}
        if base is not None:                                                     # the person moved the top: the gap to the support is now this
            out["top_gap"] = E.level_elevation(project, E.m(meta, "level")) + top - base
        return out
    if key == "constrain":
        if not support_ids(meta) or not all(E.alive(project, s) for s in support_ids(meta)):
            raise BadRequest("This foundation does not stand on a wall or column, so there is nothing to constrain it to")
        return {"constrain": _flag(value, "constrain"), "support_sig": None}     # switched on: it snaps to its support in this command; off: it stays and may be moved
    if key == "top_gap":
        if not support_ids(meta):
            raise BadRequest("Only a foundation on a wall or column keeps a gap to it")
        return {"top_gap": number(value, "gap to the support (mm)", -20000, 20000) / 1000.0, "support_sig": None}
    if key in ("formwork", "formwork_material"):
        out = _formwork_values({key: value, **({"formwork_material": E.m(meta, "formwork_material")} if key == "formwork" and E.m(meta, "formwork_material") else {})})
        if key == "formwork_material" and not E.m(meta, "formwork"):
            raise BadRequest("Add formwork first (give it a thickness)")
        return out
    if key == "floors":
        return {"floors": E._choice(value, FLOOR_POLICIES, "floors")}
    if key == "offset" and kind in LINES:
        return {"offset": number(value, "offset", -10000, 10000) / 1000.0}
    if key == "shape" and kind == "poer":
        shape = E._choice(value, SHAPES, "shape")
        return {"shape": shape, **({"length": float(E.m(meta, "width"))} if shape == "round" else {})}
    if key == "rotation" and kind == "poer":
        if constrained(project, meta):
            raise BadRequest("This pad turns with its column: rotate the column, or switch 'Constrained to its support' off")
        return {"rotation": math.radians(number(value, "rotation", -360, 360))}
    if key == "material":
        return {"material": E._choice(value, MATERIALS, "material")}
    if key == "cover":
        return {"cover": number(value, "cover", 10, 150) / 1000.0}
    if key in ("concrete_class", "steel"):
        return {key: text(value, key.replace("_", " "), 20) if str(value).strip() else None}
    if key == "conductivity" and kind in AREAS:
        return {"conductivity": number(value, "conductivity", 0.005, 500) if str(value).strip() else None}
    if key == "level":
        return {"level": project.level(text(value, "level", 80))["id"]}
    raise BadRequest(f"'{key}' cannot be edited on this foundation")


def set_data(project: Any, oid: str, p: Mapping[str, Any]) -> None:
    """Replace the layers and/or the reinforcement of a foundation (whatever is given), then rebuild. All or none."""
    if not _is(project, oid):
        raise NotFound(f"Foundation {oid} does not exist")
    meta = E.meta_of(project, oid)
    kind = kind_of(meta)
    values: dict[str, Any] = {}
    for key, label in (("above", "Layers above"), ("below", "Layers below")):
        if key in p:
            if kind not in LAYERED:
                raise BadRequest("Only a raft or a ground-bearing slab has layers")
            values[key] = clean_layers(p[key], label)
    if "bars" in p:
        bars = clean_bars(p["bars"])
        check_roles(kind, bars)
        values["bars"] = bars
    for key in ("concrete_class", "steel"):
        if key in p:
            values[key] = text(p[key], key.replace("_", " "), 20) if str(p[key] or "").strip() else None
    if "cover" in p:
        values["cover"] = number(p["cover"], "cover (mm)", 10, 150) / 1000.0
    values.update(_formwork_values(p))
    if "formwork_material" in p and "formwork" not in p and not E.m(meta, "formwork"):
        raise BadRequest("Add formwork first (give it a thickness)")
    if "floors" in p:
        values["floors"] = E._choice(p["floors"], FLOOR_POLICIES, "floors")
    if not values:
        raise BadRequest("Give layers, bars, a concrete class, a steel quality, a cover, formwork or a floor policy")
    E._attach(project, oid, values)
    regenerate(project, oid)


def move(project: Any, oid: str, meta: Mapping[str, Any], dx: float, dy: float, dz: float) -> None:
    kind = kind_of(meta)
    if constrained(project, meta):
        raise BadRequest("A foundation is constrained to what it stands on: move the wall or column and it goes with it, or switch 'Constrained to its support' off to move it on its own")
    values: dict[str, Any] = {"top": float(E.m(meta, "top")) + dz}
    if kind == "poer":
        x, y = E.m(meta, "point")
        values["point"] = [x + dx, y + dy]
    elif kind in LINES:
        s, e = E.m(meta, "start"), E.m(meta, "end")
        values.update(start=[s[0] + dx, s[1] + dy], end=[e[0] + dx, e[1] + dy])
    else:
        values["boundary"] = [[q[0] + dx, q[1] + dy] for q in E.m(meta, "boundary")]
    E._attach(project, oid, values)


def rotate(project: Any, oid: str, meta: Mapping[str, Any], angle: float, centre: tuple[float, float]) -> None:
    kind = kind_of(meta)
    if constrained(project, meta):
        raise BadRequest("A foundation is constrained to what it stands on: rotate the wall or column and it goes with it, or switch 'Constrained to its support' off to turn it on its own")
    if kind == "poer":
        E._attach(project, oid, {"point": E._turn(E.m(meta, "point"), angle, centre), "rotation": float(E.m(meta, "rotation", 0.0)) + angle})
    elif kind in LINES:
        E._attach(project, oid, {"start": E._turn(E.m(meta, "start"), angle, centre), "end": E._turn(E.m(meta, "end"), angle, centre)})
    else:
        E._attach(project, oid, {"boundary": [E._turn(q, angle, centre) for q in E.m(meta, "boundary")]})


def detach(project: Any, oid: str) -> None:
    """A copy is independent: it stands where it was put and does not snap back to the original's support."""
    meta = E.meta_of(project, oid)
    if E.m(meta, "category") == "foundation" and (E.m(meta, "support") or E.m(meta, "support_sig")):
        E._attach(project, oid, {"support": [], "support_sig": None, "top_gap": None, "constrain": None,
                                 "ends": [[0.0, 0.0], [0.0, 0.0]] if kind_of(meta) in LINES else None, "joins": []})


# ------------------------------------------------------------------------------------------------------------------------------ quantities
def reinforcement(project: Any, meta: Mapping[str, Any]) -> dict[str, Any]:
    """The indicative bar take-off the declarations imply: per group the number of bars, one bar's length, the total length and (if the engine has a steel
    density) the mass. Straight bars between the covers; no laps, hooks, curtailment or cutting lists. Not a design."""
    kind = kind_of(meta)
    cover = float(E.m(meta, "cover", COVER_DEFAULT))
    poly = outline(meta)
    rows = []
    notes: list[str] = []
    if kind in LINES:
        fr = line_frame(meta)
        length, w, h = fr["length"], fr["width"], float(E.m(meta, "height"))
    else:
        xs = [q[0] for q in poly]
        ys = [q[1] for q in poly]
        if kind == "poer":
            w, length, h = float(E.m(meta, "width")), float(E.m(meta, "length")), float(E.m(meta, "height"))
        else:
            ang = 0.0
            lo_x, hi_x, lo_y, hi_y = min(xs), max(xs), min(ys), max(ys)
            w, length, h = hi_x - lo_x, hi_y - lo_y, float(E.m(meta, "thickness"))
            notes.append("bars in a raft or slab are laid along the plan's x and y axes over its bounding box")
    steel = _steel_density()
    for b in E.m(meta, "bars") or []:
        role = b["role"]
        d = float(b["diameter"]) / 1000.0
        if kind in LINES:
            if role == "long":
                each, run = length - 2 * cover, None
                n = int(b.get("count") or 0)
            else:
                each = 2 * (w - 2 * cover) + 2 * (h - 2 * cover)
                run = length - 2 * cover
                n = int(run / (float(b["spacing"]) / 1000.0)) + 1 if run >= 0 else 0
        else:
            along, across = (w, length) if role == "x" else (length, w)
            each = along - 2 * cover
            run = across - 2 * cover
            n = int(b["count"]) if b.get("count") else (int(run / (float(b["spacing"]) / 1000.0)) + 1 if run >= 0 else 0)
        if each <= 0 or (run is not None and run < 0):
            rows.append({**b, "n": 0, "each_m": 0.0, "total_m": 0.0, "mass_kg": None, "problem": "the cover leaves no room for this bar"})
            continue
        total = n * each
        rows.append({**b, "n": n, "each_m": each, "total_m": total, "mass_kg": total * math.pi * d * d / 4 * steel if steel else None})
    massed = [r["mass_kg"] for r in rows if r["mass_kg"] is not None]
    return {"groups": rows, "bars": sum(r["n"] for r in rows), "length_m": math.fsum(r["total_m"] for r in rows),
            "mass_kg": math.fsum(massed) if rows and len(massed) == len(rows) else None, "steel_density": steel, "notes": notes,
            "note": "Indicative only: straight bars between the covers, no laps, hooks or curtailment. The reinforcement is declared, not designed or checked."}


def _steel_density() -> float | None:
    from . import quantities as Q
    return Q.engine_densities().get("Steel")


def resistance(meta: Mapping[str, Any], stacked: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Thermal resistance (m²·K/W) of the assembly: the sum of thickness / conductivity of the layers that have a conductivity. ``complete`` is false (and
    the missing layers are named) if any layer lacks one. No surface resistances and no ground are added: this is the build-up only."""
    r = 0.0
    missing = []
    for s in stacked:
        k = s.get("conductivity")
        if k:
            r += (s["z1"] - s["z0"]) / float(k)
        else:
            missing.append(s["name"])
    return {"r_m2k_w": r, "complete": not missing, "missing": missing, "u_w_m2k": (1.0 / r) if r > 0 and not missing else None}


def quantities(project: Any, oid: str) -> dict[str, Any]:
    """Everything the schedule and the Properties panel report for one foundation, by arithmetic on its parameters (cross-checked against SGEO's volume in
    the tests)."""
    meta = E.meta_of(project, oid)
    kind = kind_of(meta)
    poly = outline(meta)
    area = abs(G.signed_area(poly))
    perimeter = sum(math.dist(a, b) for a, b in zip(poly, poly[1:] + poly[:1]))
    parts = stack(project, meta)
    depth = depth_of(meta)
    rows = [{"name": s["name"], "material": s["material"], "thickness_m": s["z1"] - s["z0"], "volume_m3": area * (s["z1"] - s["z0"]), "concrete": s["concrete"]} for s in parts]
    concrete = next(r for r in rows if r["concrete"])["volume_m3"]
    out: dict[str, Any] = {"kind": kind, "plan_area_m2": area, "perimeter_m": perimeter, "depth_m": depth, "concrete_m3": concrete, "volume_m3": math.fsum(r["volume_m3"] for r in rows),
                           "formwork_m2": perimeter * depth, "layers": rows, "display_material": display_material(meta),
                           "reinforcement": reinforcement(project, meta) if E.m(meta, "bars") else None,
                           "concrete_class": E.m(meta, "concrete_class"), "steel": E.m(meta, "steel"), "cover_m": float(E.m(meta, "cover", COVER_DEFAULT)),
                           "bars": [dict(b) for b in E.m(meta, "bars") or []]}                           # as declared, so the Reinforce dialog can start from them
    if kind in LAYERED:
        out["thermal"] = resistance(meta, parts)
    if kind in LINES:
        out["length_m"] = line_frame(meta)["length"]
    spec = formwork_of(meta)
    if spec is not None:
        plates = formwork_plates(project, oid, meta)
        z0, z1 = concrete_z(project, meta)
        out["formwork_m2"] = math.fsum(pl["length"] for pl in plates) * (z1 - z0)                     # the faces that are formed: a joined end of a strip is inside the joint
        out["formwork"] = {"thickness_m": spec[0], "material": spec[1], "plates": len(plates), "area_m2": out["formwork_m2"],
                           "volume_m3": math.fsum(abs(G.signed_area(pl["quad"])) for pl in plates) * (z1 - z0)}
    elif kind in LINES:
        sj, ej = _joined_ends(project, oid, meta)
        end_edges = [math.dist(poly[i], poly[(i + 1) % len(poly)]) for i in ((3,) if sj else ()) + ((1,) if ej else ())]
        out["formwork_m2"] = (perimeter - math.fsum(end_edges)) * depth            # a joined end face is inside the joint: nothing is cast against it
    else:
        out["formwork_m2"] = perimeter * depth
    out["floors"] = E.m(meta, "floors", "adjust")
    out["note"] = ("Quantities from the foundation's parameters. Formwork is the edge area (perimeter x height); with formwork modelled it is the area of the plates, and a strip's "
                   "joined end faces are left out. No structural verification of any kind is made.")
    return out


# ------------------------------------------------------------------------------------------------------------------------------ plan, panel
def plan_item(project: Any, oid: str, f: float) -> dict[str, Any]:
    obj = project.model.get(oid)
    meta = obj["metadata"]
    item: dict[str, Any] = {"id": oid, "category": "foundation", "name": obj["name"], "kind": kind_of(meta), "polygon": [[q[0] * f, q[1] * f] for q in outline(meta)]}
    if kind_of(meta) in LINES:
        fr = line_frame(meta)
        item["axis"] = {"a": [fr["start"][0] * f, fr["start"][1] * f], "b": [fr["end"][0] * f, fr["end"][1] * f], "thickness": fr["width"] * f}
    return item


def properties(project: Any, oid: str, out: dict[str, Any], volume: Mapping[str, Any]) -> None:
    meta = E.meta_of(project, oid)
    kind = kind_of(meta)
    q = quantities(project, oid)
    mm = lambda v: round(float(v) * 1000, 6)                                                  # noqa: E731
    out["top"] = [{"label": "Type", "type": "readonly", "value": str(E.m(meta, "type", KINDS[kind]["type"]))},
                  {"key": "level", "label": "Level", "type": "choice", "value": E.m(meta, "level"), "choices": E._levels(project)},
                  {"key": "material", "label": "Material", "type": "choice", "value": E.m(meta, "material"), "choices": E._choices(MATERIALS)}]
    rows: list[dict[str, Any]] = []
    if kind == "poer":
        rows += [{"key": "shape", "label": "Shape", "type": "choice", "value": E.m(meta, "shape", "rect"), "choices": [{"value": "rect", "label": "Rectangular"}, {"value": "round", "label": "Round"}]},
                 {"key": "width", "label": "Diameter" if E.m(meta, "shape") == "round" else "Width", "type": "number", "unit": "mm", "value": mm(E.m(meta, "width"))}]
        if E.m(meta, "shape") != "round":
            rows.append({"key": "length", "label": "Length", "type": "number", "unit": "mm", "value": mm(E.m(meta, "length"))})
        rows += [{"key": "height", "label": "Height", "type": "number", "unit": "mm", "value": mm(E.m(meta, "height"))},
                 {"key": "rotation", "label": "Rotation", "type": "number", "unit": "°", "value": round(math.degrees(float(E.m(meta, "rotation", 0.0))), 6)}]
    elif kind in LINES:
        rows += [{"label": "Length", "type": "readonly", "value": f"{q['length_m']:.3f} m"},
                 {"key": "width", "label": "Width", "type": "number", "unit": "mm", "value": mm(E.m(meta, "width"))},
                 {"key": "height", "label": "Height", "type": "number", "unit": "mm", "value": mm(E.m(meta, "height"))},
                 {"key": "offset", "label": "Eccentricity", "type": "number", "unit": "mm", "value": mm(E.m(meta, "offset", 0.0))}]
    else:
        rows += [{"key": "thickness", "label": "Slab thickness", "type": "number", "unit": "mm", "value": mm(E.m(meta, "thickness"))}]
    rows += [{"label": "Plan area", "type": "readonly", "value": f"{q['plan_area_m2']:.3f} m²"}, {"label": "Perimeter", "type": "readonly", "value": f"{q['perimeter_m']:.3f} m"}, volume]
    groups: list[dict[str, Any]] = [{"title": "Geometry", "rows": rows}]
    top_row = {"key": "top", "label": "Top above level", "type": "number", "unit": "m", "value": float(E.m(meta, "top"))}
    ids = support_ids(meta)
    cons = [top_row]
    tied = constrained(project, meta)
    if ids:
        cons.append({"key": "constrain", "label": "Constrained to its support", "type": "choice", "value": "true" if E.m(meta, "constrain", True) is not False else "false",
                     "choices": [{"value": "true", "label": "Yes: it moves with what it stands on"}, {"value": "false", "label": "No: it stays where it is"}]})
        if tied:
            cons.append({"key": "top_gap", "label": "Top relative to the support's underside", "type": "number", "unit": "mm", "value": mm(E.m(meta, "top_gap") or 0.0)})
    for sid in ids:
        alive = E.alive(project, sid)
        cons.append({"label": "Stands on", "type": "link", "value": project.model.get(sid)["name"] if alive else "(deleted)", **({"ref": {"model": sid}} if alive else {})})
        base = support_base(project, sid)
        if base is not None:
            gap = (E.level_elevation(project, E.m(meta, "level")) + float(E.m(meta, "top")) - base) * 1000
            cons.append({"label": "Top to underside of support", "type": "readonly", "value": "meets it" if abs(gap) <= TOLERANCE * 1000 else f"{gap:+.0f} mm"})
    groups.append({"title": "Constraints", "rows": cons})
    spec = formwork_of(meta)
    fw = [{"key": "formwork", "label": "Formwork thickness", "type": "number", "unit": "mm", "value": mm(spec[0]) if spec else ""},
          {"key": "formwork_material", "label": "Formwork material", "type": "choice", "value": spec[1] if spec else FORMWORK_MATERIALS[0], "choices": E._choices(FORMWORK_MATERIALS)}]
    if spec and q.get("formwork"):
        fw.append({"label": "Formwork area", "type": "readonly", "value": f"{q['formwork']['area_m2']:.2f} m² · {q['formwork']['volume_m3']:.3f} m³"})
    fw.append({"key": "floors", "label": "Existing floors it overlaps", "type": "choice", "value": E.m(meta, "floors", "adjust"),
               "choices": [{"value": "adjust", "label": "Trim the floor back to the foundation"}, {"value": "merge", "label": "Merge: the floor runs over the foundation"},
                           {"value": "keep", "label": "Leave both as they are"}]})
    groups.append({"title": "Formwork and floors", "rows": fw})
    if kind in LAYERED:
        lr = [{"label": f"{'Above' if s['z0'] >= E.level_elevation(project, E.m(meta, 'level')) + float(E.m(meta, 'top')) - 1e-12 and not s['concrete'] else 'Below' if not s['concrete'] else 'Slab'}: {s['name']}",
               "type": "readonly", "value": f"{(s['z1'] - s['z0']) * 1000:.0f} mm · {s['material']}" + (f" · λ {s['conductivity']:g}" if s.get("conductivity") else "")} for s in stack(project, meta)]
        th = q["thermal"]
        lr.append({"label": "Thermal resistance of the build-up", "type": "readonly",
                   "value": (f"{th['r_m2k_w']:.2f} m²K/W" if th["complete"] else f"{th['r_m2k_w']:.2f} m²K/W so far; no conductivity for {', '.join(th['missing'])}")})
        groups.append({"title": "Build-up", "rows": lr + [{"key": "conductivity", "label": "Concrete conductivity", "type": "number", "unit": "W/m·K", "value": E.m(meta, "conductivity") or ""}]})
    rf = q["reinforcement"]
    rrows = [{"key": "concrete_class", "label": "Concrete class", "type": "text", "value": E.m(meta, "concrete_class") or ""},
             {"key": "steel", "label": "Reinforcement steel", "type": "text", "value": E.m(meta, "steel") or ""},
             {"key": "cover", "label": "Cover", "type": "number", "unit": "mm", "value": mm(E.m(meta, "cover", COVER_DEFAULT))}]
    if rf:
        for g in rf["groups"]:
            rrows.append({"label": f"{g['mark']} · Ø{g['diameter']} {g['role']} {g['face']}", "type": "readonly",
                          "value": (g.get("problem") or f"{g['n']} × {g['each_m']:.2f} m" + (f" @ {g['spacing']:g}" if g.get("spacing") else ""))})
        rrows.append({"label": "Bars (indicative)", "type": "readonly", "value": f"{rf['bars']} bars · {rf['length_m']:.1f} m" + (f" · {rf['mass_kg']:.0f} kg" if rf["mass_kg"] is not None else "")})
    else:
        rrows.append({"label": "Bar groups", "type": "readonly", "value": "none declared"})
    rrows.append({"label": "Shown as", "type": "readonly", "value": display_material(meta)})
    rrows.append({"label": "Note", "type": "readonly", "value": "Declared, not designed or verified"})
    groups.append({"title": "Concrete and reinforcement", "rows": rrows, "action": {"label": "Reinforce…", "dialog": "reinforce"}})
    groups.append({"title": "Quantities", "rows": [{"label": "Concrete", "type": "readonly", "value": f"{q['concrete_m3']:.3f} m³"},
                                                     {"label": "Formwork (edges)", "type": "readonly", "value": f"{q['formwork_m2']:.2f} m²"}]})
    out["groups"] = groups


# ------------------------------------------------------------------------------------------------------------------------------------ check
def _axes(poly: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    out = []
    for a, b in zip(poly, poly[1:] + poly[:1]):
        d = G.sub(b, a)
        if G.norm(d) > 1e-12:
            out.append(G.unit(G.left(d)))
    return out


def _convex(poly: Sequence[tuple[float, float]]) -> bool:
    signs = set()
    for a, b, c in zip(poly, poly[1:] + poly[:1], poly[2:] + poly[:2]):
        z = G.cross(G.sub(b, a), G.sub(c, b))
        if abs(z) > 1e-12:
            signs.add(z > 0)
    return len(signs) <= 1


def overlap_depth(a: Sequence[tuple[float, float]], b: Sequence[tuple[float, float]]) -> float:
    """How far two convex polygons overlap (the smallest penetration over their edge normals); 0 if they only touch or are apart."""
    best = math.inf
    for ax in _axes(a) + _axes(b):
        pa = [G.dot(p, ax) for p in a]
        pb = [G.dot(p, ax) for p in b]
        best = min(best, min(max(pa), max(pb)) - max(min(pa), min(pb)))
        if best <= 1e-6:
            return 0.0
    return best


def covers(poly: Sequence[tuple[float, float]], corners: Sequence[tuple[float, float]], tol: float = 1e-6) -> bool:
    """Whether every corner lies inside (or on) a convex polygon."""
    n = len(poly)
    sign = 1.0 if G.signed_area(poly) > 0 else -1.0
    return all(all(sign * G.cross(G.sub(poly[(i + 1) % n], poly[i]), G.sub(c, poly[i])) >= -tol for i in range(n)) for c in corners)


def issues(project: Any, oid: str, label: str, issue: Any) -> list[dict[str, str]]:
    """Model-check findings for one foundation. They concern geometry and relations only; no structural check is made."""
    out: list[dict[str, str]] = []
    meta = E.meta_of(project, oid)
    kind = kind_of(meta)
    try:
        poly = outline(meta)
        if not G.is_simple_polygon(poly):
            raise BadRequest("The outline crosses itself")
    except BadRequest as exc:
        return [issue("error", oid, f"{label}: {exc.message}", "parameters")]
    ids = support_ids(meta)
    for sid in ids:
        if not E.alive(project, sid):
            out.append(issue("warning", oid, f"{label} has lost what it stood on; it stays where it was", "hosting"))
            continue
        sm = E.meta_of(project, sid)
        if E.m(sm, "level") != E.m(meta, "level"):
            out.append(issue("error", oid, f"{label} is on a different level from what it stands on", "consistency"))
        base = support_base(project, sid)
        top = E.level_elevation(project, E.m(meta, "level")) + float(E.m(meta, "top"))
        want = float(E.m(meta, "top_gap")) if constrained(project, meta) and E.m(meta, "top_gap") is not None else 0.0     # a chosen gap is not a mismatch
        if base is not None and abs(top - base - want) > TOLERANCE:
            gap = (top - base - want) * 1000
            out.append(issue("warning", oid, f"{label}: its top is {abs(gap):.0f} mm {'above' if gap > 0 else 'below'} the underside of {project.model.get(sid)['name'] or 'its support'}"
                                              f"{', so they overlap' if gap > 0 else ', so there is a gap'}", "hosting"))
        if kind == "poer" and E.is_column(sm):
            w, d, a = float(E.m(sm, "width")), float(E.m(sm, "depth")), float(E.m(sm, "rotation", 0.0))
            x, y = E.m(sm, "point")
            c, s = math.cos(a), math.sin(a)
            corners = [(x + px * c - py * s, y + px * s + py * c) for px, py in ((-w / 2, -d / 2), (w / 2, -d / 2), (w / 2, d / 2), (-w / 2, d / 2))]
            if not covers(poly, corners):
                out.append(issue("warning", oid, f"{label} does not cover the whole foot of its column", "geometry"))
        if kind == "strook" and E.m(sm, "category") == "wall" and float(E.m(meta, "width")) < float(E.m(sm, "thickness")) - 1e-9:
            out.append(issue("warning", oid, f"{label} is narrower than the wall it carries", "geometry"))
    for fl in E.all_ids(project.model, metadata={f"{E.NS}:category": "floor"}):
        for who, why in E.m(E.meta_of(project, fl), "fd_unresolved") or []:
            if who == oid:
                out.append(issue("warning", oid, f"{label} overlaps {project.model.get(fl)['name'] or 'a floor'} and the floor could not be adjusted: {why}", "geometry"))
    if kind == "funderingsbalk" and len(ids) == 2 and ids[0] == ids[1]:
        out.append(issue("error", oid, f"{label} has the same column at both ends", "parameters"))
    bars = E.m(meta, "bars") or []
    if bars:
        for g in reinforcement(project, meta)["groups"]:
            if g.get("problem"):
                out.append(issue("error", oid, f"{label}: bar group {g['mark']}: {g['problem']}", "parameters"))
            elif g["n"] > MAX_BARS:
                out.append(issue("error", oid, f"{label}: bar group {g['mark']} counts {g['n']} bars, which is not plausible", "parameters"))
    if _convex(poly):
        for other in E.all_ids(project.model, metadata={f"{E.NS}:category": "foundation"}):
            if other <= oid:
                continue
            om = E.meta_of(project, other)
            if E.m(om, "level") != E.m(meta, "level"):
                continue
            try:
                op = outline(om)
            except BadRequest:
                continue
            if not _convex(op):
                continue
            a, b = stack(project, meta), stack(project, om)                              # the whole build-up counts: a layer can clash with another's concrete
            if min(a[0]["z1"], b[0]["z1"]) - max(a[-1]["z0"], b[-1]["z0"]) > 1e-9 and overlap_depth(poly, op) > 1e-3:
                out.append(issue("warning", oid, f"{label} overlaps {project.model.get(other)['name'] or 'another foundation'}: the shared volume is counted in both quantities", "geometry"))
    return out
