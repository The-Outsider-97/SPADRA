"""Curtain walls: a framed wall of mullions and transoms with a panel in every field (glazing by default, an opaque panel, or a door).

A curtain wall is one SGEO element. It is *defined by parameters*, never by hand-placed solids, so plan, section, elevation, 3D, schedules, quantities, undo and
save/reload all read the same numbers:

* **Where**: a plan line (``start`` to ``end``), the ``level`` it stands on, ``base_offset`` and ``height``. The frame is centred on that line.
* **How the field is divided** (``v_*`` for the vertical members, the mullions; ``h_*`` for the horizontal ones, the transoms): ``mode`` is
  ``centre`` (hart-op-hart: a fixed centre-to-centre distance, the remainder in one bay placed by ``align``), ``max`` or ``min`` (equal bays, as many as the limit
  needs), or ``count`` (a fixed number of interior members in equal bays). Spacing is measured axis to axis between the axes of the perimeter members, which stand
  half a member width in from the edges. A member can be **removed** (the two fields beside it become one) and one can be **added** at any position.
* **The frame**: material (Steel, Aluminium, Timber), an optional catalogue ``frame_profile`` (see below) which fixes ``frame_width`` (the face seen from the front) and
  ``frame_depth`` (the thickness through the wall), a ``corner_radius`` on the section, and the **join** condition: ``mullions`` run through and the transoms stop at them,
  ``transoms`` run through and the mullions stop at them, or ``mitre`` cuts the four perimeter members at 45° at the corners (the interior mullions then run between
  the top and bottom members and the transoms between the mullions).
* **The panels**: ``panel_type`` (Window, Opaque panel, Door), ``panel_material``, ``panel_thickness`` and ``panel_offset`` (across the wall from the frame's centre line).
  Each field can override any of these; a field in the bottom row can be a **Door** of type Single Flush Door (hinged, with a hinge side and a swing), Double
  Door (a pair) or Sliding Door (two leaves on separate rails), drawn in plan and elevation by the same code that draws a door in a wall.

**Profile library.** ``data/stabalux_profiles.json`` holds the sizes of the Stabalux SR (steel), AL (aluminium) and H (timber) profiles exactly as the catalogues print them,
each with the catalogue, its date and the PDF page it was read from (``data/build_stabalux_profiles.py`` regenerates it). A catalogue profile fixes the width and depth;
nothing else is taken from the manufacturer: a size is not an approval, and the system's tests, structural capacity and fire or burglar ratings stay with the complete Stabalux system.

All lengths in metadata are metres; the properties panel and commands speak millimetres.
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..tools.errors import BadRequest, NotFound
from ..tools.validation import number, text
from . import elements as E
from . import geometry as G

PROFILES = Path(__file__).with_name("data") / "stabalux_profiles.json"
FRAME_MATERIALS = ("Aluminium", "Steel", "Timber")
PANEL_TYPES = ("Window", "Opaque panel", "Door")        # what one field can be
WALL_PANEL_TYPES = ("Window", "Opaque panel")            # what the wall's own panel setting can be: a door only ever stands in a field of the bottom row
PANEL_MATERIALS = ("Glass", "Aluminium", "Steel", "Timber")
MODES = ("centre", "max", "min", "count")
ALIGNS = ("start", "centre", "end")
JOINS = ("mullions", "transoms", "mitre")
DOOR_TYPES = ("Single Flush Door", "Double Door", "Sliding Door")
JOIN_LABELS = {"mullions": "Mullions continuous", "transoms": "Transoms continuous", "mitre": "Mitred at the corners"}
MODE_LABELS = {"centre": "Centre to centre (hart-op-hart)", "max": "Maximum spacing", "min": "Minimum spacing", "count": "Fixed number of interior members"}
ALIGN_LABELS = {"start": "Remainder at the end", "centre": "Remainder split at both ends", "end": "Remainder at the start"}
DEFAULTS_MM = {"frame_width": 50.0, "frame_depth": 90.0, "corner_radius": 0.0, "panel_thickness": 24.0, "panel_offset": 0.0}
DEFAULT_PROFILE = "stabalux-al5090"
DEFAULT_PROFILES = {"Aluminium": "stabalux-al5090", "Steel": "stabalux-sr5090-2", "Timber": "stabalux-h-60"}
H_DEFAULT = 2.1                       # m: transoms by default at door-head height, so a door fits in the bottom row
MAX_LINES = 200                       # per direction
MAX_CELLS = 2000
ARC = 4                               # segments in a rounded section corner
GAP = 0.005                           # m: the clearance round a door leaf in its field
MIN_FIELD = 0.05                      # m: a field narrower than this is not a field
MIN_DOOR_FIELD = (0.4, 0.8)           # m: the least width and height of a field that takes a door (a modelling limit, not a code requirement)
OVERLAP = 0.05                        # m: the two leaves of a sliding door overlap by this much when closed
KINDS = {"frame": "Frame", "glass": "Glazing", "panel": "Opaque panel", "door": "Door leaf"}
PART_ORDER = ("frame", "glass", "panel", "door")


# ------------------------------------------------------------------------------------------------------------------- profile library
@lru_cache(maxsize=1)
def library() -> dict[str, Any]:
    """The Stabalux profile sizes with their provenance, as the data file holds them."""
    try:
        raw = json.loads(PROFILES.read_text(encoding="utf-8"))
    except OSError as exc:
        raise BadRequest("The profile library (data/stabalux_profiles.json) is missing from this installation",
                         hint="Reinstall SPADRA, or run data/build_stabalux_profiles.py on the Stabalux catalogues", details={"error": str(exc)}) from exc
    return {"meta": {k: raw[k] for k in ("manufacturer", "date", "sources", "note", "materials")}, "by_id": {p["id"]: p for p in raw["profiles"]}, "list": raw["profiles"]}


def profile(pid: str) -> dict[str, Any]:
    found = library()["by_id"].get(pid)
    if found is None:
        raise BadRequest(f"'{pid}' is not a profile of the library", hint="Choose one from the list, or give the width and depth yourself")
    return found


def profiles_for(material: str) -> list[dict[str, Any]]:
    return [p for p in library()["list"] if p["material"] == material]


def provenance(pid: str | None) -> str:
    """Where a profile's numbers come from, in words."""
    if not pid:
        return "Custom size entered by hand (no catalogue source)"
    p = profile(pid)
    meta = library()["meta"]
    return f"{meta['manufacturer']} {p['system']} {p['name']}: {meta['sources'][p['material']]}, PDF page {p['page']}, table {p['table']} (catalogue of {meta['date']})"


# ----------------------------------------------------------------------------------------------------------------------- parameters
def _num(meta: Mapping[str, Any], key: str, default: float) -> float:
    return float(E.m(meta, key, default))


def kind_of(meta: Mapping[str, Any]) -> str:
    return "curtainwall"


def frame_of(meta: Mapping[str, Any]) -> dict[str, Any]:
    """The geometry of a curtain wall's plan line in the wall's own frame: ``u`` along it from the start, ``d`` across (positive to the left), ``z`` up."""
    s, e = E.m(meta, "start"), E.m(meta, "end")
    length = math.dist(s, e)
    angle = math.atan2(e[1] - s[1], e[0] - s[0])
    return {"start": (float(s[0]), float(s[1])), "end": (float(e[0]), float(e[1])), "length": length, "angle": angle, "height": _num(meta, "height", 3.0), "base_offset": _num(meta, "base_offset", 0.0)}


def base_z(project: Any, meta: Mapping[str, Any]) -> float:
    return E.level_elevation(project, E.m(meta, "level")) + _num(meta, "base_offset", 0.0)


def _to_world(fr: Mapping[str, Any], z0: float):
    c, s = math.cos(fr["angle"]), math.sin(fr["angle"])
    sx, sy = fr["start"]

    def at(u: float, d: float, z: float) -> list[float]:
        return [sx + u * c - d * s, sy + u * s + d * c, z0 + z]
    return at


def spec(meta: Mapping[str, Any]) -> dict[str, Any]:
    """Every parameter, with its defaults, validated enough to build from (``check`` reports what cannot be built)."""
    fr = frame_of(meta)
    return {**fr, "frame_material": str(E.m(meta, "frame_material", "Aluminium")), "frame_profile": E.m(meta, "frame_profile"),
            "frame_width": _num(meta, "frame_width", DEFAULTS_MM["frame_width"] / 1000), "frame_depth": _num(meta, "frame_depth", DEFAULTS_MM["frame_depth"] / 1000),
            "corner_radius": _num(meta, "corner_radius", 0.0), "join": str(E.m(meta, "join", "mullions")),
            "v": {"mode": str(E.m(meta, "v_mode", "centre")), "value": _num(meta, "v_value", 1.2), "align": str(E.m(meta, "v_align", "start"))},
            "h": {"mode": str(E.m(meta, "h_mode", "centre")), "value": _num(meta, "h_value", H_DEFAULT), "align": str(E.m(meta, "h_align", "start"))},
            "removed": {"v": [float(x) for x in (E.m(meta, "removed_v") or ())], "h": [float(x) for x in (E.m(meta, "removed_h") or ())]},
            "extra": {"v": [float(x) for x in (E.m(meta, "extra_v") or ())], "h": [float(x) for x in (E.m(meta, "extra_h") or ())]},
            "panel_type": str(E.m(meta, "panel_type", "Window")), "panel_material": str(E.m(meta, "panel_material", "Glass")),
            "panel_thickness": _num(meta, "panel_thickness", DEFAULTS_MM["panel_thickness"] / 1000), "panel_offset": _num(meta, "panel_offset", 0.0),
            "panels": dict(E.m(meta, "panels") or {}),
            "door_type": str(E.m(meta, "door_type", "Single Flush Door")), "door_hinge": str(E.m(meta, "door_hinge", "start")), "door_swing": int(E.m(meta, "door_swing", 1)),
            "opening_angle": _num(meta, "opening_angle", E.DEFAULT_OPENING_ANGLE)}


# ------------------------------------------------------------------------------------------------------------------------- layout
def bays(span: float, mode: str, value: float, align: str = "start") -> list[float]:
    """The lengths of the bays a span is divided into, in order. ``span`` is axis to axis."""
    if span <= 1e-9:
        raise BadRequest("The curtain wall is too small for its frame")
    if mode == "centre":
        if value <= 1e-6:
            raise BadRequest("The centre-to-centre distance must be more than zero")
        full = int(span / value + 1e-9)
        rest = span - full * value
        if rest < 1e-4:
            rest = 0.0
        if full == 0:
            out = [span]
        elif align == "start":
            out = [value] * full + ([rest] if rest else [])
        elif align == "end":
            out = ([rest] if rest else []) + [value] * full
        else:
            out = ([rest / 2] if rest else []) + [value] * full + ([rest / 2] if rest else [])
    elif mode == "max":
        if value <= 1e-6:
            raise BadRequest("The maximum spacing must be more than zero")
        n = max(1, math.ceil(span / value - 1e-9))
        out = [span / n] * n
    elif mode == "min":
        if value <= 1e-6:
            raise BadRequest("The minimum spacing must be more than zero")
        n = max(1, int(span / value + 1e-9))
        out = [span / n] * n
    elif mode == "count":
        n = int(round(value))
        if n < 0 or abs(value - n) > 1e-9:
            raise BadRequest("The number of members must be a whole number of zero or more")
        out = [span / (n + 1)] * (n + 1)
    else:
        raise BadRequest(f"mode must be one of {', '.join(MODES)}")
    if len(out) - 1 > MAX_LINES:
        raise BadRequest(f"That spacing makes {len(out) - 1} members in one direction: the most is {MAX_LINES}")
    return out


def _same(a: float, b: float) -> bool:
    return abs(a - b) < 1e-3


def lines(sp: Mapping[str, Any], direction: str) -> list[dict[str, Any]]:
    """The interior members of one direction, from the start (``v``: along the wall; ``h``: up from the base), each as
    ``{"pos": axis position (m), "src": "rule" or "added", "removed": bool}``. A removed member is listed so it can be put back."""
    total = sp["length"] if direction == "v" else sp["height"]
    w = sp["frame_width"]
    span = total - w
    rule = sp[direction]
    out = []
    if span > 1e-9:
        at = w / 2
        for b in bays(span, rule["mode"], rule["value"], rule["align"])[:-1]:
            at += b
            out.append({"pos": at, "src": "rule", "removed": any(_same(at, r) for r in sp["removed"][direction])})
    for x in sp["extra"][direction]:
        out.append({"pos": float(x), "src": "added", "removed": False})
    out.sort(key=lambda q: q["pos"])
    return out


def axes(sp: Mapping[str, Any], direction: str) -> list[float]:
    """Every member axis of one direction that is present: the two perimeter members and the interior ones not removed."""
    total = sp["length"] if direction == "v" else sp["height"]
    w = sp["frame_width"]
    return [w / 2] + [q["pos"] for q in lines(sp, direction) if not q["removed"]] + [total - w / 2]


def cells(sp: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The fields between the members, left to right then bottom to top: ``col``, ``row``, ``u0 u1`` and ``z0 z1`` (the clear opening, between the faces of the members)."""
    V, H = axes(sp, "v"), axes(sp, "h")
    w = sp["frame_width"]
    out = []
    for r in range(len(H) - 1):
        for c in range(len(V) - 1):
            u0, u1, z0, z1 = V[c] + w / 2, V[c + 1] - w / 2, H[r] + w / 2, H[r + 1] - w / 2
            out.append({"col": c, "row": r, "u0": u0, "u1": u1, "z0": z0, "z1": z1, "key": f"{c},{r}"})
    return out


def cell_spec(sp: Mapping[str, Any], cell: Mapping[str, Any]) -> dict[str, Any]:
    """What one field is: the wall's own panel parameters overridden by whatever was set for this field."""
    over = sp["panels"].get(cell["key"]) or {}
    kind = str(over.get("type", sp["panel_type"]))
    base = {"Window": sp["panel_material"] if sp["panel_type"] == "Window" else "Glass", "Opaque panel": sp["panel_material"] if sp["panel_type"] == "Opaque panel" else "Aluminium",
            "Door": sp["frame_material"]}
    material = str(over.get("material") or base.get(kind, "Glass"))
    return {"type": kind, "material": material, "thickness": float(over.get("thickness", sp["panel_thickness"])), "offset": float(over.get("offset", sp["panel_offset"])),
            "door_type": str(over.get("door_type", sp["door_type"])), "hinge": str(over.get("hinge", sp["door_hinge"])), "swing": int(over.get("swing", sp["door_swing"])),
            "slide": str(over.get("slide", "end")), "opening_angle": float(over.get("opening_angle", sp["opening_angle"]))}


def _remap_panels(project: Any, oid: str, old: Sequence[Mapping[str, Any]], old_panels: Mapping[str, Any], new: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Carry each field's own settings over to the field that now contains its centre, so removing or adding a member does not scramble the panels."""
    out: dict[str, Any] = {}
    for c in old:
        over = old_panels.get(c["key"])
        if not over:
            continue
        cu, cz = (c["u0"] + c["u1"]) / 2, (c["z0"] + c["z1"]) / 2
        home = next((n for n in new if n["u0"] - 1e-9 <= cu <= n["u1"] + 1e-9 and n["z0"] - 1e-9 <= cz <= n["z1"] + 1e-9), None)
        if home is not None and home["key"] not in out:
            out[home["key"]] = dict(over)
    return out


# -------------------------------------------------------------------------------------------------------------------------- solids
def ring(width: float, depth: float, radius: float) -> list[tuple[float, float]]:
    """A member's section in (t, d): across its face and through the wall, counter-clockwise, its corners rounded by ``radius``."""
    hw, hd = width / 2, depth / 2
    r = max(0.0, min(radius, hw - 1e-4, hd - 1e-4))
    if r < 1e-6:
        return [(-hw, -hd), (hw, -hd), (hw, hd), (-hw, hd)]
    pts: list[tuple[float, float]] = []
    for cx, cy, a0 in ((hw - r, -hd + r, -90.0), (hw - r, hd - r, 0.0), (-hw + r, hd - r, 90.0), (-hw + r, -hd + r, 180.0)):
        for k in range(ARC + 1):
            a = math.radians(a0 + 90.0 * k / ARC)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def _area(pts: Sequence[tuple[float, float]]) -> float:
    return abs(sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))) / 2


def _member(section: Sequence[tuple[float, float]], horizontal: bool, axis: float, a0: float, a1: float, k0: float = 0.0, k1: float = 0.0) -> G.Mesh:
    """A prism along a member's axis from ``a0`` to ``a1``. ``horizontal``: it runs along u at height ``axis``; otherwise it rises along z at u = ``axis``.
    ``k0`` and ``k1`` slant the two ends (the along-coordinate of a section point at ``t`` is ``a + k * t``): 0 is square, ±1 is a 45° mitre."""
    def at(a: float, t: float, d: float) -> list[float]:
        return [a, d, axis + t] if horizontal else [axis + t, d, a]
    n = len(section)
    verts = [at(a0 + k0 * t, t, d) for t, d in section] + [at(a1 + k1 * t, t, d) for t, d in section]
    faces: list[list[int]] = []
    for i in range(n):
        j = (i + 1) % n
        faces += [[i, n + i, n + j], [i, n + j, j]]
    for i in range(1, n - 1):
        faces.append([0, i, i + 1])                          # the start cap
        faces.append([n, n + i + 1, n + i])                  # the end cap
    return _positive(verts, faces)


def _positive(verts: list[list[float]], faces: list[list[int]]) -> G.Mesh:
    return E._positive_volume((verts, faces))


def _box(u0: float, u1: float, d0: float, d1: float, z0: float, z1: float) -> G.Mesh:
    return E._positive_volume(E._box(u0, u1, d0, d1, z0, z1))


def members(project: Any, meta: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every solid of the curtain wall in the wall's own frame (u along, d across, z up from the base), in drawing order: the frame, then each field's
    glazing, panel or door leaves. Each carries its ``kind``, ``material``, ``mesh`` and what it is (``role``, ``cell``, ``length``)."""
    sp = spec(meta)
    L, H, w, D = sp["length"], sp["height"], sp["frame_width"], sp["frame_depth"]
    sec = ring(w, D, sp["corner_radius"])
    area = _area(sec)
    V, Hh = axes(sp, "v"), axes(sp, "h")
    out: list[dict[str, Any]] = []

    def frame(role: str, horizontal: bool, axis: float, a0: float, a1: float, k0: float = 0.0, k1: float = 0.0) -> None:
        if a1 - a0 < 1e-6:
            return
        out.append({"kind": "frame", "material": sp["frame_material"], "role": role, "cell": None, "length": a1 - a0, "area": area, "mesh": _member(sec, horizontal, axis, a0, a1, k0, k1)})

    join = sp["join"]
    last_v, last_h = len(V) - 1, len(Hh) - 1
    # vertical members
    for i, u in enumerate(V):
        perimeter = i in (0, last_v)
        role = "perimeter" if perimeter else "mullion"
        if join == "mullions":
            frame(role, False, u, 0.0, H)
        elif join == "transoms":
            for r in range(len(Hh) - 1):
                frame(role, False, u, Hh[r] + w / 2, Hh[r + 1] - w / 2)
        elif perimeter:                                                                  # mitre: the perimeter members are cut at 45° where they meet
            left = i == 0
            frame(role, False, u, w / 2, H - w / 2, 1.0 if left else -1.0, -1.0 if left else 1.0)
        else:
            frame(role, False, u, w, H - w)
    # horizontal members
    for r, z in enumerate(Hh):
        perimeter = r in (0, last_h)
        role = "perimeter" if perimeter else "transom"
        if join == "transoms":
            frame(role, True, z, 0.0, L)
        elif join == "mitre" and perimeter:
            bottom = r == 0
            frame(role, True, z, w / 2, L - w / 2, 1.0 if bottom else -1.0, -1.0 if bottom else 1.0)
        else:
            for c in range(len(V) - 1):
                frame(role, True, z, V[c] + w / 2, V[c + 1] - w / 2)
    # fields
    for cell in cells(sp):
        cs = cell_spec(sp, cell)
        kind = {"Window": "glass", "Opaque panel": "panel", "Door": "door"}[cs["type"]]
        u0, u1, z0, z1, yd, t = cell["u0"], cell["u1"], cell["z0"], cell["z1"], cs["offset"], cs["thickness"]
        if kind != "door":
            out.append({"kind": kind, "material": cs["material"], "role": "panel", "cell": cell["key"], "area": (u1 - u0) * (z1 - z0), "thickness": t, "mesh": _box(u0, u1, yd - t / 2, yd + t / 2, z0, z1)})
            continue
        for n, (a, b, y) in enumerate(door_leaves(cs, u0, u1, yd)):
            out.append({"kind": "door", "material": cs["material"], "role": "leaf", "cell": cell["key"], "leaf": n, "area": (b - a) * (z1 - z0 - 2 * GAP), "thickness": E.DOOR_LEAF,
                        "mesh": _box(a, b, y - E.DOOR_LEAF / 2, y + E.DOOR_LEAF / 2, z0 + GAP, z1 - GAP)})
    out.sort(key=lambda q: PART_ORDER.index(q["kind"]))                                   # stable: the frame first, then glazing, panels and leaves, so faces group by part
    return out


def door_leaves(cs: Mapping[str, Any], u0: float, u1: float, yd: float) -> list[tuple[float, float, float]]:
    """The closed leaves of a door field as ``(u start, u end, across position)``. One leaf, a pair meeting in the middle, or two leaves on separate rails."""
    a, b = u0 + GAP, u1 - GAP
    kind = cs["door_type"]
    if kind == "Single Flush Door":
        return [(a, b, yd)]
    if kind == "Double Door":
        mid = (a + b) / 2
        return [(a, mid - GAP / 2, yd), (mid + GAP / 2, b, yd)]
    lw = ((b - a) + OVERLAP) / 2                                                     # sliding: two leaves, each half the opening and an overlap
    s = int(cs["swing"])
    rail = (E.DOOR_LEAF + E.SLIDE_GAP) / 2
    if cs["slide"] == "end":
        return [(a, a + lw, yd + s * rail), (b - lw, b, yd - s * rail)]                 # the moving leaf is the first: it travels toward the end over the fixed one
    return [(a, a + lw, yd - s * rail), (b - lw, b, yd + s * rail)]


def world_members(project: Any, oid: str) -> list[dict[str, Any]]:
    """The same solids in world coordinates (``verts``, ``faces``), for the drawings."""
    meta = E.meta_of(project, oid)
    at = _to_world(frame_of(meta), base_z(project, meta))
    out = []
    for q in members(project, meta):
        verts, faces = q["mesh"]
        out.append({**{k: v for k, v in q.items() if k != "mesh"}, "verts": [at(*v) for v in verts], "faces": [tuple(f) for f in faces]})
    return out


def _parts(project: Any, meta: Mapping[str, Any]) -> tuple[G.Mesh, list[dict[str, Any]]]:
    """The whole mesh in world space and the runs of faces by (kind, material), in face order, so 3D can colour frame, glazing and leaves separately."""
    at = _to_world(frame_of(meta), base_z(project, meta))
    verts: list[list[float]] = []
    faces: list[list[int]] = []
    runs: list[dict[str, Any]] = []
    for q in members(project, meta):
        mv, mf = q["mesh"]
        n = len(verts)
        verts += [at(*v) for v in mv]
        faces += [[a + n, b + n, c + n] for a, b, c in mf]
        if runs and runs[-1]["kind"] == q["kind"] and runs[-1]["material"] == q["material"]:
            runs[-1]["count"] += len(mf)
        else:
            runs.append({"kind": q["kind"], "material": q["material"], "count": len(mf)})
    return (verts, faces), runs


def build(project: Any, oid: str) -> None:
    meta = E.meta_of(project, oid)
    check_spec(project, meta)
    mesh, runs = _parts(project, meta)
    E._attach(project, oid, {"parts": runs})
    E._set_mesh(project, oid, mesh)


def regenerate(project: Any, oid: str) -> None:
    build(project, oid)


def part_colors(project: Any, meta: Mapping[str, Any], face_count: int) -> list[dict[str, Any]]:
    """Face runs with their colours for the 3D view: the frame in its material's colour, glazing in glass, leaves in theirs. Empty when the stored runs no longer
    add up to the mesh (the view then uses the element's single colour)."""
    runs = E.m(meta, "parts") or []
    if not runs or sum(int(r["count"]) for r in runs) != face_count:
        return []
    palette = project.manifest.get("material_colors", {})
    return [{"count": int(r["count"]), "color": E.m(meta, "color") if (E.m(meta, "color") and r["kind"] == "frame") else
             (palette.get(r["material"]) or E.MATERIAL_COLORS.get(r["material"]) or "#B9C0C8")} for r in runs]


# ----------------------------------------------------------------------------------------------------------------------- validation
def check_spec(project: Any, meta: Mapping[str, Any]) -> None:
    """Raise ``BadRequest`` for parameters that cannot make a curtain wall (a message for the person, not a stack trace)."""
    sp = spec(meta)
    if sp["length"] < 0.1:
        raise BadRequest("The curtain wall is too short: its start and end must be at least 100 mm apart")
    if sp["height"] < 0.1:
        raise BadRequest("The curtain wall is too low: its height must be at least 100 mm")
    if sp["frame_material"] not in FRAME_MATERIALS:
        raise BadRequest(f"The frame material must be one of {', '.join(FRAME_MATERIALS)}")
    if sp["join"] not in JOINS:
        raise BadRequest(f"The join must be one of {', '.join(JOINS)}")
    if sp["panel_type"] not in WALL_PANEL_TYPES:
        raise BadRequest(f"The panel type must be one of {', '.join(WALL_PANEL_TYPES)}")
    if sp["panel_material"] not in PANEL_MATERIALS:
        raise BadRequest(f"The panel material must be one of {', '.join(PANEL_MATERIALS)}")
    w, D = sp["frame_width"], sp["frame_depth"]
    if not 0.01 <= w <= 1.0 or not 0.01 <= D <= 1.0:
        raise BadRequest("The frame width and depth must each be between 10 mm and 1000 mm")
    if min(sp["length"], sp["height"]) < 2 * w + MIN_FIELD:
        raise BadRequest("The frame is too wide for the curtain wall: there is no room left for a panel")
    if sp["corner_radius"] < 0 or sp["corner_radius"] > min(w, D) / 2 + 1e-9:
        raise BadRequest(f"The corner radius cannot be more than half the frame's width or depth ({min(w, D) / 2 * 1000:.1f} mm)")
    for d in ("v", "h"):
        if sp[d]["mode"] not in MODES or sp[d]["align"] not in ALIGNS:
            raise BadRequest(f"The {'vertical' if d == 'v' else 'horizontal'} spacing mode must be one of {', '.join(MODES)}")
    for d, name in (("v", "mullions"), ("h", "transoms")):
        pos = axes(sp, d)
        for a, b in zip(pos, pos[1:]):
            if b - a - w < MIN_FIELD - 1e-9:
                raise BadRequest(f"Two {name} are only {(b - a) * 1000:.0f} mm apart (axis to axis): with a {w * 1000:.0f} mm frame no panel is left between them",
                                 hint="Use a wider spacing, remove a member, or make the frame narrower")
    n = len(cells(sp))
    if n > MAX_CELLS:
        raise BadRequest(f"That makes {n} panels: the most is {MAX_CELLS}")
    for q in cells(sp):
        cs = cell_spec(sp, q)
        if cs["type"] not in PANEL_TYPES or cs["material"] not in PANEL_MATERIALS:
            raise BadRequest(f"Panel {q['col'] + 1}.{q['row'] + 1} has an unknown type or material")
        if not 0.002 <= cs["thickness"] <= 0.5:
            raise BadRequest(f"Panel {q['col'] + 1}.{q['row'] + 1}: the thickness must be between 2 mm and 500 mm")
        if cs["type"] == "Door":
            if q["row"] != 0:
                raise BadRequest(f"Panel {q['col'] + 1}.{q['row'] + 1} is not in the bottom row: a door stands on the floor")
            if cs["door_type"] not in DOOR_TYPES:
                raise BadRequest(f"The door type must be one of {', '.join(DOOR_TYPES)}")
            if q["u1"] - q["u0"] < MIN_DOOR_FIELD[0] or q["z1"] - q["z0"] < MIN_DOOR_FIELD[1]:
                raise BadRequest(f"Panel {q['col'] + 1}.{q['row'] + 1} is {(q['u1'] - q['u0']) * 1000:.0f} × {(q['z1'] - q['z0']) * 1000:.0f} mm: too small for a door",
                                 hint="Remove a mullion or widen the field first")
            if cs["hinge"] not in ("start", "end") or cs["swing"] not in (1, -1):
                raise BadRequest("A door's hinge is start or end, and its swing left or right")
            if not E.MIN_OPENING_ANGLE <= cs["opening_angle"] <= E.MAX_OPENING_ANGLE:
                raise BadRequest(f"The opening angle must be between {E.MIN_OPENING_ANGLE:g}° and {E.MAX_OPENING_ANGLE:g}°")


# ------------------------------------------------------------------------------------------------------------------------ creation
def _setup(p: Mapping[str, Any], to_m: float) -> dict[str, Any]:
    """The validated, stored form of every creation parameter (metres)."""
    values: dict[str, Any] = {}
    material = E._choice(p.get("frame_material", "Aluminium"), FRAME_MATERIALS, "frame_material")
    values["frame_material"] = material
    pid = p.get("frame_profile")
    if pid is None and p.get("frame_width") is None and p.get("frame_depth") is None:
        pid = DEFAULT_PROFILES[material]                                          # nothing said: a catalogue size, so the default is traceable too
    if pid == "custom":
        pid = None
    if pid:
        prof = profile(str(pid))
        if prof["material"] != material:
            raise BadRequest(f"{prof['name']} is a {prof['material'].lower()} profile: the frame material is {material}")
        values["frame_profile"] = prof["id"]
        values["frame_width"] = prof["width_mm"] / 1000.0
        values["frame_depth"] = (number(p["frame_depth"], "frame_depth", 10, 1000) if p.get("frame_depth") is not None else (prof["depth_mm"] or DEFAULTS_MM["frame_depth"])) / 1000.0
        if p.get("frame_width") is not None and abs(float(p["frame_width"]) - prof["width_mm"]) > 1e-6:
            raise BadRequest(f"{prof['name']} is {prof['width_mm']} mm wide: leave the width out, or choose no catalogue profile to give your own")
        if prof["depth_mm"] and p.get("frame_depth") is not None and abs(float(p["frame_depth"]) - prof["depth_mm"]) > 1e-6:
            raise BadRequest(f"{prof['name']} is {prof['depth_mm']} mm deep: leave the depth out, or choose no catalogue profile to give your own")
    else:
        values["frame_profile"] = None
        values["frame_width"] = number(p.get("frame_width", DEFAULTS_MM["frame_width"]), "frame_width", 10, 1000) / 1000.0
        values["frame_depth"] = number(p.get("frame_depth", DEFAULTS_MM["frame_depth"]), "frame_depth", 10, 1000) / 1000.0
    values["corner_radius"] = number(p.get("corner_radius", 0.0), "corner_radius", 0, 500) / 1000.0
    values["join"] = E._choice(p.get("join", "mullions"), JOINS, "join")
    for d, label in (("v", "vertical"), ("h", "horizontal")):
        mode = E._choice(p.get(f"{d}_mode", "centre"), MODES, f"{label} spacing mode")
        raw = p.get(f"{d}_value", 3 if mode == "count" else (1200 if d == "v" else H_DEFAULT * 1000))
        values[f"{d}_mode"] = mode
        values[f"{d}_value"] = float(number(raw, f"{label} spacing", 0, 1000)) if mode == "count" else number(raw, f"{label} spacing", 1, 100000) / 1000.0
        values[f"{d}_align"] = E._choice(p.get(f"{d}_align", "start"), ALIGNS, f"{label} alignment")
    values["panel_type"] = E._choice(p.get("panel_type", "Window"), WALL_PANEL_TYPES, "panel_type")
    values["panel_material"] = E._choice(p.get("panel_material", "Glass"), PANEL_MATERIALS, "panel_material")
    values["panel_thickness"] = number(p.get("panel_thickness", DEFAULTS_MM["panel_thickness"]), "panel_thickness", 2, 500) / 1000.0
    values["panel_offset"] = number(p.get("panel_offset", 0.0), "panel_offset", -1000, 1000) / 1000.0
    values["door_type"] = E._choice(p.get("door_type", "Single Flush Door"), DOOR_TYPES, "door_type")
    values["door_hinge"] = E._choice(p.get("door_hinge", "start"), ("start", "end"), "door_hinge")
    values["door_swing"] = 1 if E._choice(p.get("door_swing", "left"), ("left", "right"), "door_swing") == "left" else -1
    return values


def add_curtainwall(project: Any, p: Mapping[str, Any], to_m: float) -> str:
    """A curtain wall on the line ``start`` to ``end`` (drawing units) of ``level``: ``height`` and ``base_offset`` in mm, then the frame, spacing and panel
    parameters named in the module's description. All or nothing."""
    start, end = _point(p.get("start"), "start", to_m), _point(p.get("end"), "end", to_m)
    level = project.level(text(p.get("level"), "level", 80))
    height = number(p.get("height", 3000), "height", 100, 500000) / 1000.0
    setup = _setup(p, to_m)
    values = {**setup, "category": "curtainwall", "type": "Curtain Wall", "level": level["id"], "start": list(start), "end": list(end), "height": height,
              "base_offset": number(p.get("base_offset", 0.0), "base_offset", -100000, 100000) / 1000.0, "removed_v": [], "removed_h": [], "extra_v": [], "extra_h": [], "panels": {},
              "material": setup["frame_material"], "opening_angle": number(p.get("opening_angle", E.DEFAULT_OPENING_ANGLE), "opening_angle", E.MIN_OPENING_ANGLE, E.MAX_OPENING_ANGLE)}
    oid = project.model.add_box(width=1.0, depth=1.0, height=1.0, name=text(p.get("name") or "Curtain Wall", "name", 80))
    E._attach(project, oid, values)
    try:
        build(project, oid)
    except BadRequest:
        project.model.remove(oid)
        raise
    return oid


def _point(value: Any, label: str, to_m: float) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise BadRequest(f"{label} must be a point [x, y]")
    return (float(number(value[0], f"{label}.x", -1e9, 1e9)) * to_m, float(number(value[1], f"{label}.y", -1e9, 1e9)) * to_m)


# ------------------------------------------------------------------------------------------------------------------------- editing
def _store_grid_edit(project: Any, oid: str, meta: Mapping[str, Any], values: dict[str, Any]) -> None:
    """Write a change that re-divides the field, carrying each panel's own settings to the field that now holds its centre."""
    old_sp = spec(meta)
    old_cells = cells(old_sp)
    E._attach(project, oid, values)
    new_sp = spec(E.meta_of(project, oid))
    E._attach(project, oid, {"panels": _remap_panels(project, oid, old_cells, old_sp["panels"], cells(new_sp))})


def _cell_key(project: Any, oid: str, key: str) -> tuple[str, str]:
    parts = key.split(":")
    if len(parts) != 4 or parts[0] != "panel":
        raise BadRequest(f"'{key}' is not a panel property")
    try:
        c, r = int(parts[1]), int(parts[2])
    except ValueError as exc:
        raise BadRequest(f"'{key}' does not name a panel") from exc
    if not any(q["key"] == f"{c},{r}" for q in cells(spec(E.meta_of(project, oid)))):
        raise NotFound(f"This curtain wall has no panel {c + 1}.{r + 1}")
    return f"{c},{r}", parts[3]


def set_panel_field(project: Any, oid: str, cell: str, field: str, value: Any) -> None:
    """Change one field's own panel: ``type``, ``material``, ``thickness`` (mm), ``offset`` (mm), and for a door ``door_type``, ``hinge``, ``swing`` (left/right),
    ``slide`` (start/end) and ``opening_angle``. ``reset`` (any value) returns the field to the wall's panel settings."""
    meta = E.meta_of(project, oid)
    panels = {k: dict(v) for k, v in (E.m(meta, "panels") or {}).items()}
    over = panels.get(cell, {})
    if field == "reset":
        panels.pop(cell, None)
    else:
        if field == "type":
            over["type"] = E._choice(value, PANEL_TYPES, "panel type")
            over.pop("material", None)
        elif field == "material":
            over["material"] = E._choice(value, PANEL_MATERIALS, "panel material")
        elif field == "thickness":
            over["thickness"] = number(value, "panel thickness", 2, 500) / 1000.0
        elif field == "offset":
            over["offset"] = number(value, "panel offset", -1000, 1000) / 1000.0
        elif field == "door_type":
            over["door_type"] = E._choice(value, DOOR_TYPES, "door type")
        elif field == "hinge":
            over["hinge"] = E._choice(value, ("start", "end"), "hinge")
        elif field == "swing":
            over["swing"] = 1 if E._choice(value, ("left", "right"), "swing") == "left" else -1
        elif field == "slide":
            over["slide"] = E._choice(value, ("start", "end"), "slide direction")
        elif field == "opening_angle":
            over["opening_angle"] = number(value, "opening angle", E.MIN_OPENING_ANGLE, E.MAX_OPENING_ANGLE)
        else:
            raise BadRequest(f"'{field}' is not a panel property")
        panels[cell] = over
    E._attach(project, oid, {"panels": panels})


def line_edit(project: Any, oid: str, direction: str, action: str, *, index: int | None = None, at: float | None = None) -> None:
    """Remove (``action`` remove, by the member's ``index`` in ``lines``), put back (restore), or add (add, ``at`` mm from the wall start for a mullion, from the base for a transom)."""
    if direction not in ("v", "h"):
        raise BadRequest("direction must be v (mullions) or h (transoms)")
    meta = E.meta_of(project, oid)
    sp = spec(meta)
    listing = lines(sp, direction)
    kr, ke = f"removed_{direction}", f"extra_{direction}"
    removed, extra = list(sp["removed"][direction]), list(sp["extra"][direction])
    if action == "add":
        if at is None:
            raise BadRequest("Say where to add the member (at, in mm)")
        pos = float(number(at, "position", -1e6, 1e6)) / 1000.0
        total, w = (sp["length"], sp["frame_width"]) if direction == "v" else (sp["height"], sp["frame_width"])
        if pos < w + MIN_FIELD / 2 or pos > total - w - MIN_FIELD / 2:
            raise BadRequest(f"A member must stand between {(w + MIN_FIELD / 2) * 1000:.0f} and {(total - w - MIN_FIELD / 2) * 1000:.0f} mm")
        if any(abs(q["pos"] - pos) < w + MIN_FIELD - 1e-9 and not q["removed"] for q in listing) or abs(pos - w / 2) < w + MIN_FIELD or abs(total - w / 2 - pos) < w + MIN_FIELD:
            raise BadRequest("That is too close to another member: leave room for a panel between them")
        extra.append(pos)
    else:
        if index is None or not 0 <= int(index) < len(listing):
            raise BadRequest(f"There is no member {index}: this wall has {len(listing)} interior {'mullions' if direction == 'v' else 'transoms'}")
        q = listing[int(index)]
        if action == "remove":
            if q["src"] == "added":
                extra = [x for x in extra if not _same(x, q["pos"])]
            elif not q["removed"]:
                removed.append(q["pos"])
        elif action == "restore":
            if q["src"] == "rule":
                removed = [x for x in removed if not _same(x, q["pos"])]
        else:
            raise BadRequest("action must be remove, restore or add")
    _store_grid_edit(project, oid, meta, {kr: removed, ke: extra})


_LAYOUT_KEYS = {"v_mode", "v_value", "v_align", "h_mode", "h_value", "h_align"}


def set_property(project: Any, oid: str, meta: Mapping[str, Any], key: str, value: Any) -> dict[str, Any]:
    """The stored values a change of ``key`` sets (the caller attaches them and rebuilds). Line and panel keys edit the element themselves and return nothing to store."""
    if key.startswith("panel:"):
        cell, field = _cell_key(project, oid, key)
        set_panel_field(project, oid, cell, field, value)
        return {}
    if key.startswith("line:"):
        _, d, i = (key.split(":") + ["", ""])[:3]
        line_edit(project, oid, d, "remove" if value in ("removed", "remove", True) else "restore", index=int(i))
        return {}
    if key.startswith("add_line:"):
        line_edit(project, oid, key.split(":")[1], "add", at=float(number(value, "position", -1e6, 1e6)))
        return {}
    if key in ("v_mode", "h_mode"):
        d = key[0]
        v = {key: E._choice(value, MODES, "spacing mode"), f"removed_{d}": [], f"extra_{d}": []}
        if v[key] == "count" and E.m(meta, f"{d}_mode") != "count":
            v[f"{d}_value"] = 3.0
        elif v[key] != "count" and E.m(meta, f"{d}_mode") == "count":
            v[f"{d}_value"] = 1.2 if d == "v" else H_DEFAULT
        _store_grid_edit(project, oid, meta, v)
        return {}
    if key in ("v_value", "h_value"):
        d = key[0]
        mode = str(E.m(meta, f"{d}_mode", "centre"))
        stored = float(number(value, "spacing", 0, 1000)) if mode == "count" else number(value, "spacing", 1, 100000) / 1000.0
        _store_grid_edit(project, oid, meta, {key: stored, f"removed_{d}": [], f"extra_{d}": []})
        return {}
    if key in ("v_align", "h_align"):
        _store_grid_edit(project, oid, meta, {key: E._choice(value, ALIGNS, "alignment"), f"removed_{key[0]}": [], f"extra_{key[0]}": []})
        return {}
    if key == "frame_material":
        material = E._choice(value, FRAME_MATERIALS, "frame material")
        pid = E.m(meta, "frame_profile")
        return {"frame_material": material, **({"frame_profile": None} if pid and profile(str(pid))["material"] != material else {})}
    if key == "frame_profile":
        if value in (None, "", "custom"):
            return {"frame_profile": None}
        prof = profile(str(value))
        return {"frame_profile": prof["id"], "frame_material": prof["material"], "frame_width": prof["width_mm"] / 1000.0,
                **({"frame_depth": prof["depth_mm"] / 1000.0} if prof["depth_mm"] else {})}
    if key in ("frame_width", "frame_depth"):
        out: dict[str, Any] = {key: number(value, key, 10, 1000) / 1000.0}
        pid = E.m(meta, "frame_profile")
        if pid and (key == "frame_width" or profile(str(pid))["depth_mm"]):
            out["frame_profile"] = None                                                  # a typed size is no longer the catalogue's
        _clear_if_width(project, oid, meta, key)
        return out
    if key == "corner_radius":
        return {"corner_radius": number(value, "corner radius", 0, 500) / 1000.0}
    if key == "join":
        return {"join": E._choice(value, JOINS, "join")}
    if key == "panel_type":
        return {"panel_type": E._choice(value, WALL_PANEL_TYPES, "panel type")}
    if key == "panel_material":
        return {"panel_material": E._choice(value, PANEL_MATERIALS, "panel material")}
    if key == "panel_thickness":
        return {"panel_thickness": number(value, "panel thickness", 2, 500) / 1000.0}
    if key == "panel_offset":
        return {"panel_offset": number(value, "panel offset", -1000, 1000) / 1000.0}
    if key == "door_type":
        return {"door_type": E._choice(value, DOOR_TYPES, "door type")}
    if key == "door_hinge":
        return {"door_hinge": E._choice(value, ("start", "end"), "hinge")}
    if key == "door_swing":
        return {"door_swing": 1 if E._choice(value, ("left", "right"), "swing") == "left" else -1}
    if key == "opening_angle":
        return {"opening_angle": number(value, "opening angle", E.MIN_OPENING_ANGLE, E.MAX_OPENING_ANGLE)}
    if key == "height":
        return {"height": number(value, "height", 100, 500000) / 1000.0, **{f"removed_h": [], "extra_h": []}}
    if key == "base_offset":
        return {"base_offset": number(value, "base offset", -100000, 100000) / 1000.0}
    if key == "level":
        return {"level": project.level(text(value, "level", 80))["id"]}
    if key == "length":
        fr = frame_of(meta)
        new = number(value, "length", 100, 1e9) / 1000.0
        s = fr["start"]
        return {"end": [s[0] + math.cos(fr["angle"]) * new, s[1] + math.sin(fr["angle"]) * new], "removed_v": [], "extra_v": []}
    raise BadRequest(f"'{key}' is not an editable curtain wall property")


def _clear_if_width(project: Any, oid: str, meta: Mapping[str, Any], key: str) -> None:
    """A different member width shifts every axis (they stand half a width in from the edges), so individually removed or added members no longer line up."""
    if key == "frame_width":
        E._attach(project, oid, {"removed_v": [], "removed_h": [], "extra_v": [], "extra_h": []})


def move(project: Any, oid: str, meta: Mapping[str, Any], dx: float, dy: float, dz: float) -> None:
    s, e = E.m(meta, "start"), E.m(meta, "end")
    E._attach(project, oid, {"start": [s[0] + dx, s[1] + dy], "end": [e[0] + dx, e[1] + dy], "base_offset": float(E.m(meta, "base_offset", 0.0)) + dz})


def rotate(project: Any, oid: str, meta: Mapping[str, Any], angle: float, centre: Sequence[float]) -> None:
    E._attach(project, oid, {"start": E._turn(E.m(meta, "start"), angle, centre), "end": E._turn(E.m(meta, "end"), angle, centre)})


# ------------------------------------------------------------------------------------------------------------------------- drawing
def _footprint(sp: Mapping[str, Any], f: float) -> list[list[float]]:
    return E._rect(sp["start"], sp["angle"], 0.0, sp["length"], sp["frame_depth"] / 2, f)


def _door_cells(sp: Mapping[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    return [(c, cs) for c in cells(sp) if (cs := cell_spec(sp, c))["type"] == "Door"]


def plan_symbols(project: Any, oid: str, f: float, cut_z: float, show_opening: bool = True) -> list[dict[str, Any]]:
    """The plan notation of every door field the cut plane passes through: its leaves, and for a swing leaf the arc and open leaf, drawn by the code that draws a
    door in a wall (so the swing is the same in plan, elevation and 3D). A sliding door shows both leaves, the open position dashed, a track and an arrow."""
    meta = E.meta_of(project, oid)
    sp = spec(meta)
    z0 = base_z(project, meta)
    fr = {"start": sp["start"], "angle": sp["angle"], "thickness": sp["frame_depth"], "base": z0}
    at = lambda x, y: [(sp["start"][0] + math.cos(sp["angle"]) * x - math.sin(sp["angle"]) * y) * f, (sp["start"][1] + math.sin(sp["angle"]) * x + math.cos(sp["angle"]) * y) * f]   # noqa: E731
    out = []
    for c, cs in _door_cells(sp):
        if not z0 + c["z0"] <= cut_z < z0 + c["z1"]:
            continue
        leaves = door_leaves(cs, c["u0"], c["u1"], cs["offset"])
        sym: dict[str, Any] = {"kind": "door", "cell": c["key"], "door_type": cs["door_type"], "leaf": {"width": (c["u1"] - c["u0"]) * f, "y": cs["offset"] * f, "frame_y": None}}
        rect = lambda a, b, y: [at(a, y - E.DOOR_LEAF / 2), at(b, y - E.DOOR_LEAF / 2), at(b, y + E.DOOR_LEAF / 2), at(a, y + E.DOOR_LEAF / 2)]       # noqa: E731
        shapes = [{"t": "poly", "p": rect(a, b, y), "dash": False, "role": "leaf"} for a, b, y in leaves]
        if cs["door_type"] == "Sliding Door":
            a, b, y = leaves[0]
            sign = 1 if cs["slide"] == "end" else -1
            travel = b - a
            ox0, ox1 = a + sign * travel, b + sign * travel
            shapes.append({"t": "poly", "p": rect(ox0, ox1, y), "dash": True, "role": "open"})
            lo, hi = min(a, b, ox0, ox1), max(a, b, ox0, ox1)
            shapes.append({"t": "line", "p": [at(lo, y), at(hi, y)], "dash": True, "role": "track"})
            ya = y + cs["swing"] * (E.DOOR_LEAF / 2 + 0.10)
            cx = (a + b) / 2
            shapes.append({"t": "arrow", "p": [at(cx, ya), at(cx + sign * min(0.6 * travel, 0.5), ya)], "dash": False, "role": "direction"})
        sym["shapes"] = shapes
        if show_opening and cs["door_type"] != "Sliding Door":
            swings = [E._leaf_symbol("door", fr, f, a, b, cs["swing"], hinge, y, cs["opening_angle"])
                      for (a, b, y), hinge in zip(leaves, ([cs["hinge"]] if len(leaves) == 1 else ["start", "end"]))]
            sym.update(swings[0])
            if len(swings) > 1:
                sym["more"] = swings[1:]
        out.append(sym)
    return out


def plan_item(project: Any, oid: str, f: float, cut_z: float, show_hatching: bool = True, show_opening: bool = True) -> dict[str, Any] | None:
    """The curtain wall on a plan: its footprint, the exact cut of every frame member, panel and leaf at the cut plane (each with its material and, unless hidden, its hatch),
    and the door symbols. ``None`` when nothing of it reaches the plane."""
    from . import documentation as DOC
    meta = E.meta_of(project, oid)
    sp = spec(meta)
    obj = project.model.get(oid)
    loops: list[list[list[float]]] = []
    materials: list[str] = []
    kinds: list[str] = []
    for q in world_members(project, oid):
        zs = [v[2] for v in q["verts"]]
        if not min(zs) + 1e-8 < cut_z < max(zs) - 1e-8:
            continue
        for loop in DOC.horizontal_cut_loops(q["verts"], q["faces"], cut_z):
            loops.append([[x * f, y * f] for x, y in loop])
            materials.append(q["material"])
            kinds.append(q["kind"])
    item: dict[str, Any] = {"id": oid, "category": "curtainwall", "name": obj["name"], "polygon": _footprint(sp, f), "outline": _footprint(sp, f),
                            "axis": {"a": [sp["start"][0] * f, sp["start"][1] * f], "b": [sp["end"][0] * f, sp["end"][1] * f], "thickness": sp["frame_depth"] * f}}
    if loops:
        item["cut_polygons"] = loops
        item["cut_materials"] = materials
        item["cut_kinds"] = kinds
        if show_hatching:
            item["cut_hatches"] = [DOC.hatch_pattern(project, mat) for mat in materials]
    symbols = plan_symbols(project, oid, f, cut_z, show_opening)
    if symbols:
        item["symbols"] = symbols
    return item


def elevation_symbol(project: Any, oid: str, look: tuple[float, float]) -> dict[str, Any] | None:
    """The opening-direction symbol of every swinging door leaf in a section or elevation looking along ``look`` (same form and rules as a door in a wall: see
    ``elements.elevation_symbol``). The first is the result and the rest are in ``more``."""
    meta = E.meta_of(project, oid)
    sp = spec(meta)
    z0 = base_z(project, meta)
    fr = {"start": sp["start"], "angle": sp["angle"], "base": z0}
    found = []
    for c, cs in _door_cells(sp):
        if cs["door_type"] == "Sliding Door":
            continue
        leaves = door_leaves(cs, c["u0"], c["u1"], cs["offset"])
        for (a, b, y), hinge in zip(leaves, ([cs["hinge"]] if len(leaves) == 1 else ["start", "end"])):
            sym = E._elevation_leaf(fr, look, a, b, c["z0"] + GAP, c["z1"] - GAP, cs["swing"], hinge, "door")
            if sym is not None:
                found.append(sym)
    if not found:
        return None
    return {**found[0], **({"more": found[1:]} if len(found) > 1 else {})}


def cut_parts(project: Any, oid: str) -> list[dict[str, Any]]:
    """The solids as world meshes with their material, for a section's cut: one entry per member so every cut loop knows its material."""
    return [{"material": q["material"], "kind": q["kind"], "verts": q["verts"], "faces": q["faces"]} for q in world_members(project, oid)]


# ---------------------------------------------------------------------------------------------------------------------- quantities
def quantities(project: Any, oid: str) -> dict[str, Any]:
    """Lengths, volumes, areas and (where the catalogue gives a mass per metre) indicative mass. Derived from the same solids that are drawn."""
    meta = E.meta_of(project, oid)
    sp = spec(meta)
    pid = sp["frame_profile"]
    prof = profile(str(pid)) if pid else None
    ms = members(project, meta)
    frame = [q for q in ms if q["kind"] == "frame"]
    by_role: dict[str, dict[str, float]] = {}
    for q in frame:
        r = by_role.setdefault(q["role"], {"count": 0, "length": 0.0})
        r["count"] += 1
        r["length"] += q["length"]
    total = sum(q["length"] for q in frame)
    panel = {k: sum(q["area"] for q in ms if q["kind"] == k) for k in ("glass", "panel", "door")}
    doors = [c for c, cs in _door_cells(sp)]
    leaves = sum(1 for q in ms if q["kind"] == "door")
    return {"mullion_lines": len(axes(sp, "v")), "transom_lines": len(axes(sp, "h")), "frame_length": total, "frame_members": len(frame), "by_role": by_role, "frame_volume": sum(q["area"] * q["length"] for q in frame),
            "glazing_area": panel["glass"], "panel_area": panel["panel"], "door_leaf_area": panel["door"], "panel_count": len(cells(sp)), "door_count": len(doors), "door_leaves": leaves,
            "gross_area": sp["length"] * sp["height"], "mass_per_m": prof["mass_kg_per_m"] if prof and prof.get("mass_kg_per_m") else None,
            "frame_mass": total * prof["mass_kg_per_m"] if prof and prof.get("mass_kg_per_m") else None, "profile": provenance(pid),
            "volumes": {k: sum(q["area"] * q["thickness"] for q in ms if q["kind"] == k) for k in ("glass", "panel", "door")}}


# --------------------------------------------------------------------------------------------------------------------- properties
def properties(project: Any, oid: str, out: dict[str, Any], volume: dict[str, Any]) -> dict[str, Any]:
    meta = E.meta_of(project, oid)
    sp = spec(meta)
    mm = lambda v: round(v * 1000, 6)                                                       # noqa: E731
    opts = lambda values, labels=None: [{"value": v, "label": (labels or {}).get(v, v)} for v in values]       # noqa: E731
    pid = sp["frame_profile"]
    profile_choices = [{"value": "custom", "label": "Custom size (no catalogue source)"}] + [
        {"value": p["id"], "label": f"{p['system']} {p['name']} — {p['width_mm']}" + (f" × {p['depth_mm']} mm" if p["depth_mm"] else " mm wide") + f" (p. {p['page']})"} for p in profiles_for(sp["frame_material"])]
    out["top"] = [{"label": "Type", "type": "readonly", "value": "Curtain Wall"},
                  {"key": "name", "label": "Name", "type": "text", "value": project.model.get(oid)["name"]},
                  {"key": "level", "label": "Level", "type": "choice", "value": E.m(meta, "level"), "choices": E._levels(project)},
                  {"key": "height", "label": "Height", "type": "number", "unit": "mm", "value": mm(sp["height"])},
                  {"key": "base_offset", "label": "Base offset", "type": "number", "unit": "mm", "value": mm(sp["base_offset"])},
                  {"key": "length", "label": "Length", "type": "number", "unit": "mm", "value": mm(sp["length"])}]
    rows = []
    for d, name, label in (("v", "Vertical members (mullions)", "mullion"), ("h", "Horizontal members (transoms)", "transom")):
        rule = sp[d]
        count_mode = rule["mode"] == "count"
        rows += [{"key": f"{d}_mode", "label": f"{name}: spacing rule", "type": "choice", "value": rule["mode"], "choices": opts(MODES, MODE_LABELS)},
                 {"key": f"{d}_value", "label": f"{name}: " + ("number of members" if count_mode else {"centre": "centre-to-centre distance", "max": "maximum spacing", "min": "minimum spacing"}[rule["mode"]]),
                  "type": "number", "unit": "" if count_mode else "mm", "value": rule["value"] if count_mode else mm(rule["value"])}]
        if rule["mode"] == "centre":
            rows.append({"key": f"{d}_align", "label": f"{name}: remainder", "type": "choice", "value": rule["align"], "choices": opts(ALIGNS, ALIGN_LABELS)})
    frame_rows = [{"key": "frame_material", "label": "Frame material", "type": "choice", "value": sp["frame_material"], "choices": E._choices(FRAME_MATERIALS)},
                  {"key": "frame_profile", "label": "Profile (catalogue)", "type": "choice", "value": pid or "custom", "choices": profile_choices},
                  {"key": "frame_width", "label": "Frame width (face)", "type": "number", "unit": "mm", "value": mm(sp["frame_width"])},
                  {"key": "frame_depth", "label": "Frame thickness (depth)", "type": "number", "unit": "mm", "value": mm(sp["frame_depth"])},
                  {"key": "corner_radius", "label": "Corner radius of the section", "type": "number", "unit": "mm", "value": mm(sp["corner_radius"])},
                  {"key": "join", "label": "Join condition", "type": "choice", "value": sp["join"], "choices": opts(JOINS, JOIN_LABELS)},
                  {"label": "Profile source", "type": "readonly", "value": provenance(pid)}]
    panel_rows = [{"key": "panel_type", "label": "Panel type", "type": "choice", "value": sp["panel_type"], "choices": E._choices(WALL_PANEL_TYPES)},
                  {"key": "panel_material", "label": "Panel material", "type": "choice", "value": sp["panel_material"], "choices": E._choices(PANEL_MATERIALS)},
                  {"key": "panel_thickness", "label": "Panel thickness", "type": "number", "unit": "mm", "value": mm(sp["panel_thickness"])},
                  {"key": "panel_offset", "label": "Panel offset (from the frame's centre)", "type": "number", "unit": "mm", "value": mm(sp["panel_offset"])},
                  {"key": "door_type", "label": "Door type (new door fields)", "type": "choice", "value": sp["door_type"], "choices": E._choices(DOOR_TYPES)},
                  {"key": "door_hinge", "label": "Door hinge (new door fields)", "type": "choice", "value": sp["door_hinge"], "choices": [{"value": "start", "label": "Start side"}, {"value": "end", "label": "End side"}]},
                  {"key": "door_swing", "label": "Door swing (new door fields)", "type": "choice", "value": "left" if sp["door_swing"] > 0 else "right", "choices": E._choices(("left", "right"))}]
    line_rows: list[dict[str, Any]] = []
    for d, noun in (("v", "Mullion"), ("h", "Transom")):
        for i, q in enumerate(lines(sp, d)):
            line_rows.append({"key": f"line:{d}:{i}", "label": f"{noun} {i + 1} at {q['pos'] * 1000:,.0f} mm" + (" (added)" if q["src"] == "added" else ""), "type": "choice",
                              "value": "removed" if q["removed"] else "present", "choices": [{"value": "present", "label": "Present"}, {"value": "removed", "label": "Removed" if q["src"] == "rule" else "Remove"}]})
        line_rows.append({"key": f"add_line:{d}", "label": f"Add a {noun.lower()} at (mm {'along' if d == 'v' else 'up'})", "type": "number", "unit": "mm", "value": ""})
    cell_rows: list[dict[str, Any]] = []
    all_cells = cells(sp)
    for c in all_cells[:60]:
        cs = cell_spec(sp, c)
        n = f"Panel {c['col'] + 1}.{c['row'] + 1} ({(c['u1'] - c['u0']) * 1000:,.0f} × {(c['z1'] - c['z0']) * 1000:,.0f})"
        pre = f"panel:{c['col']}:{c['row']}:"
        cell_rows.append({"key": pre + "type", "label": f"{n}: type", "type": "choice", "value": cs["type"], "choices": E._choices(PANEL_TYPES)})
        if cs["type"] == "Door":
            cell_rows += [{"key": pre + "door_type", "label": f"{n}: door type", "type": "choice", "value": cs["door_type"], "choices": E._choices(DOOR_TYPES)},
                          {"key": pre + "hinge", "label": f"{n}: hinge", "type": "choice", "value": cs["hinge"], "choices": [{"value": "start", "label": "Start side"}, {"value": "end", "label": "End side"}]},
                          {"key": pre + "swing", "label": f"{n}: swing", "type": "choice", "value": "left" if cs["swing"] > 0 else "right", "choices": E._choices(("left", "right"))}]
        else:
            cell_rows.append({"key": pre + "material", "label": f"{n}: material", "type": "choice", "value": cs["material"], "choices": E._choices(PANEL_MATERIALS)})
    if len(all_cells) > 60:
        cell_rows.append({"label": "More panels", "type": "readonly", "value": f"{len(all_cells) - 60} more panels not listed here"})
    q = quantities(project, oid)
    mass = f"{q['frame_mass']:.1f} kg (catalogue mass per metre × length: indicative)" if q["frame_mass"] is not None else "not given by the catalogue"
    out["groups"] = [{"title": "Division", "rows": rows}, {"title": "Frame", "rows": frame_rows}, {"title": "Panels", "rows": panel_rows},
                     {"title": "Members (remove or add)", "rows": line_rows}, {"title": "Fields", "rows": cell_rows},
                     {"title": "Quantities", "rows": [{"label": "Frame members", "type": "readonly", "value": f"{q['frame_members']} ({q['frame_length']:.2f} m)"},
                                                      {"label": "Frame mass", "type": "readonly", "value": mass},
                                                      {"label": "Glazing area", "type": "readonly", "value": f"{q['glazing_area']:.2f} m²"},
                                                      {"label": "Opaque panel area", "type": "readonly", "value": f"{q['panel_area']:.2f} m²"},
                                                      {"label": "Doors", "type": "readonly", "value": f"{q['door_count']} ({q['door_leaves']} leaves)"}, volume]},
                     {"title": "Identity", "rows": [{"key": "name", "label": "Name", "type": "text", "value": project.model.get(oid)["name"]}]}]
    return out


def issues(project: Any, oid: str, label: str, issue: Any) -> list[dict[str, str]]:
    """Model-check findings for a curtain wall."""
    out: list[dict[str, str]] = []
    meta = E.meta_of(project, oid)
    try:
        check_spec(project, meta)
    except BadRequest as exc:
        out.append(issue("error", oid, f"{label}: {exc}", "parameters"))
        return out
    sp = spec(meta)
    if E.m(meta, "level") not in [l["id"] for l in project.manifest["levels"]]:
        out.append(issue("error", oid, f"{label} refers to a level that no longer exists", "references"))
    half = sp["frame_depth"] / 2
    for c in cells(sp):
        cs = cell_spec(sp, c)
        reach = abs(cs["offset"]) + (E.DOOR_LEAF if cs["type"] == "Door" else cs["thickness"]) / 2
        if reach > half + 1e-9 and cs["type"] != "Door":
            out.append(issue("warning", oid, f"{label}: panel {c['col'] + 1}.{c['row'] + 1} sticks out of the frame's depth ({reach * 1000:.0f} mm from the centre, the frame reaches {half * 1000:.0f} mm)", "geometry"))
    return out


# --------------------------------------------------------------------------------------------------------------------------- preview
def preview(project: Any, p: Mapping[str, Any], to_m: float) -> dict[str, Any]:
    """What ``add_curtainwall`` would make for the same parameters, without making it: the member positions, the fields and the totals, so a dialog can show them live."""
    start, end = _point(p.get("start", [0, 0]), "start", to_m), _point(p.get("end", [3.0 / to_m, 0]), "end", to_m)
    setup = _setup(p, to_m)
    meta = {f"{E.NS}:{k}": v for k, v in {**setup, "start": list(start), "end": list(end), "height": number(p.get("height", 3000), "height", 100, 500000) / 1000.0,
                                         "removed_v": [], "removed_h": [], "extra_v": [], "extra_h": [], "panels": {}}.items()}
    check_spec(project, meta)
    sp = spec(meta)
    cs = cells(sp)
    q = {"length": sp["length"], "height": sp["height"]}
    return {"mullions": [round(x * 1000, 3) for x in axes(sp, "v")], "transoms": [round(x * 1000, 3) for x in axes(sp, "h")], "panels": len(cs),
            "frame_width_mm": round(sp["frame_width"] * 1000, 3), "frame_depth_mm": round(sp["frame_depth"] * 1000, 3), "profile": provenance(setup["frame_profile"]),
            "bays_v_mm": [round(b * 1000, 3) for b in bays(sp["length"] - sp["frame_width"], sp["v"]["mode"], sp["v"]["value"], sp["v"]["align"])],
            "bays_h_mm": [round(b * 1000, 3) for b in bays(sp["height"] - sp["frame_width"], sp["h"]["mode"], sp["h"]["value"], sp["h"]["align"])], **{k: round(v * 1000, 3) for k, v in q.items()}}


def material_volumes(project: Any, oid: str) -> list[dict[str, Any]]:
    """The volume of each material of the curtain wall (frame, glazing, panels, leaves), from the solids that are drawn."""
    totals: dict[str, float] = {}
    for q in members(project, E.meta_of(project, oid)):
        vol = q["area"] * q["length"] if q["kind"] == "frame" else q["area"] * q["thickness"]
        totals[q["material"]] = totals.get(q["material"], 0.0) + vol
    return [{"material": k, "volume_m3": v} for k, v in sorted(totals.items())]


def library_listing() -> dict[str, Any]:
    """The profile library for a client: every profile with its provenance."""
    lib = library()
    return {"manufacturer": lib["meta"]["manufacturer"], "date": lib["meta"]["date"], "note": lib["meta"]["note"], "materials": lib["meta"]["materials"],
            "profiles": [{**p, "provenance": provenance(p["id"])} for p in lib["list"]]}
