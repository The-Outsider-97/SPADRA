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

``top`` is the top of the concrete in metres above the level. When a foundation is made on a support it starts at the base of that support, and after
that it is an ordinary parameter: if the support moves up or down the model check says the two no longer meet, it does not silently move the foundation.

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
        if E.m(meta, "category") == "wall":
            sig.append([*E.m(meta, "start"), *E.m(meta, "end"), float(E.m(meta, "thickness")), str(E.m(meta, "level"))])
        elif E.is_column(meta):
            sig.append([*E.m(meta, "point"), float(E.m(meta, "rotation", 0.0)), float(E.m(meta, "width")), float(E.m(meta, "depth")), str(E.m(meta, "level"))])
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


def follow(project: Any, oid: str) -> bool:
    """Bring the placement a foundation derives from its supports up to date; returns whether anything changed. A support that has been deleted leaves the
    foundation where it was (the model check reports it)."""
    meta = E.meta_of(project, oid)
    kind = kind_of(meta)
    ids = support_ids(meta)
    if not ids or any(not E.alive(project, s) for s in ids):
        return False
    sig = support_signature(project, ids)
    if sig == E.m(meta, "support_sig"):
        return False
    values: dict[str, Any] = {"support_sig": sig}
    sup = [E.meta_of(project, s) for s in ids]
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


def loop_materials(project: Any, meta: Mapping[str, Any], loops: Sequence[Sequence[tuple[float, float]]]) -> list[str]:
    """The material of each cut loop of a vertical section: a loop's height (screen y is height) says which layer of the build-up it is."""
    parts = stack(project, meta)
    out = []
    for loop in loops:
        mid = (min(p[1] for p in loop) + max(p[1] for p in loop)) / 2
        hit = next((s for s in parts if s["z0"] - 1e-9 <= mid <= s["z1"] + 1e-9), None)
        out.append(str(hit["material"]) if hit else str(E.m(meta, "material", "Concrete")))
    return out


def build_mesh(project: Any, meta: Mapping[str, Any]) -> G.Mesh:
    poly = outline(meta)
    if not G.is_simple_polygon(poly) or abs(G.signed_area(poly)) < 1e-9:
        raise BadRequest("The outline crosses itself or has no area")
    verts: list[Any] = []
    faces: list[Any] = []
    for part in stack(project, meta):
        v, f = G.prism(poly, part["z0"], part["z1"])
        base = len(verts)
        verts += [list(p) for p in v]
        faces += [[a + base, b + base, c + base] for a, b, c in f]
    return E._positive_volume((verts, faces))


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
        E._set_mesh(project, oid, build_mesh(project, meta))
    except G.GeometryError as exc:
        raise BadRequest(str(exc)) from exc


def reconcile(project: Any) -> list[str]:
    """After any command: foundations follow the walls and columns they stand on, and strips re-cut their joints to their neighbours. Only what changed
    is rebuilt. Runs inside the command, so a foundation that can no longer be built rolls the whole command back."""
    ids = E.all_ids(project.model, metadata={f"{E.NS}:category": "foundation"})
    if not ids:
        return []
    dirty: set[str] = set()
    for oid in ids:
        if follow(project, oid):
            dirty.add(oid)
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
    return sorted(dirty)


# ---------------------------------------------------------------------------------------------------------------------------- creation
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
                              "support": support, "cover": number(p.get("cover", COVER_DEFAULT * 1000), "cover (mm)", 10, 150) / 1000.0}
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
        return {"top": number(value, "top", -100, 100)}
    if key == "offset" and kind in LINES:
        return {"offset": number(value, "offset", -10000, 10000) / 1000.0}
    if key == "shape" and kind == "poer":
        shape = E._choice(value, SHAPES, "shape")
        return {"shape": shape, **({"length": float(E.m(meta, "width"))} if shape == "round" else {})}
    if key == "rotation" and kind == "poer":
        if support_ids(meta):
            raise BadRequest("This pad turns with its column: rotate the column")
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
    if not values:
        raise BadRequest("Give layers, bars, a concrete class, a steel quality or a cover")
    E._attach(project, oid, values)
    regenerate(project, oid)


def move(project: Any, oid: str, meta: Mapping[str, Any], dx: float, dy: float, dz: float) -> None:
    kind = kind_of(meta)
    if support_ids(meta) and any(E.alive(project, s) for s in support_ids(meta)):
        raise BadRequest("A foundation follows what it stands on: move the wall or column and it goes with it")
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
    if support_ids(meta) and any(E.alive(project, s) for s in support_ids(meta)):
        raise BadRequest("A foundation follows what it stands on: rotate the wall or column and it goes with it")
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
        E._attach(project, oid, {"support": [], "support_sig": None, "ends": [[0.0, 0.0], [0.0, 0.0]] if kind_of(meta) in LINES else None, "joins": []})


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
                           "formwork_m2": perimeter * depth, "layers": rows,
                           "reinforcement": reinforcement(project, meta) if E.m(meta, "bars") else None,
                           "concrete_class": E.m(meta, "concrete_class"), "steel": E.m(meta, "steel"), "cover_m": float(E.m(meta, "cover", COVER_DEFAULT))}
    if kind in LAYERED:
        out["thermal"] = resistance(meta, parts)
    if kind in LINES:
        out["length_m"] = line_frame(meta)["length"]
    out["note"] = ("Quantities from the foundation's parameters. Formwork is the edge area (perimeter x thickness) only. No structural verification of any kind is made.")
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
    for sid in ids:
        alive = E.alive(project, sid)
        cons.append({"label": "Stands on", "type": "link", "value": project.model.get(sid)["name"] if alive else "(deleted)", **({"ref": {"model": sid}} if alive else {})})
        base = support_base(project, sid)
        if base is not None:
            gap = (E.level_elevation(project, E.m(meta, "level")) + float(E.m(meta, "top")) - base) * 1000
            cons.append({"label": "Top to underside of support", "type": "readonly", "value": "meets it" if abs(gap) <= TOLERANCE * 1000 else f"{gap:+.0f} mm"})
    groups.append({"title": "Constraints", "rows": cons})
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
    rrows.append({"label": "Note", "type": "readonly", "value": "Declared, not designed or verified"})
    groups.append({"title": "Concrete and reinforcement", "rows": rrows})
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
        if base is not None and abs(top - base) > TOLERANCE:
            gap = (top - base) * 1000
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
