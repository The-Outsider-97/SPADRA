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
PANEL_TYPES = ("Window", "Opaque panel", "Door", "Louvre")        # what one field can be
WALL_PANEL_TYPES = ("Window", "Opaque panel", "Louvre")            # what the wall's own panel setting can be: a door only ever stands in a field of the bottom row
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
KINDS = {"frame": "Frame", "glass": "Glazing", "panel": "Opaque panel", "louvre": "Louvre", "door": "Door leaf"}
PART_ORDER = ("frame", "glass", "panel", "louvre", "door")
NON_GRID_ROLES = ("fin", "shelf")


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
    return {"start": (float(s[0]), float(s[1])),
            "end": (float(e[0]), float(e[1])),
            "length": length, "angle": angle,
            "height": _num(meta, "height", 3.0),
            "base_offset": _num(meta, "base_offset", 0.0),
            "lean": _num(meta, "lean", 0.0)}


def base_z(project: Any, meta: Mapping[str, Any]) -> float:
    return E.level_elevation(project, E.m(meta, "level")) + _num(meta, "base_offset", 0.0)


def _to_world(fr, z0):
    c, s = math.cos(fr["angle"]), math.sin(fr["angle"])
    sx, sy = fr["start"]
    tan_lean = math.tan(math.radians(fr.get("lean", 0.0)))
    def at(u, d, z):
        d_eff = d + z * tan_lean
        return [sx + u * c - d_eff * s, sy + u * s + d_eff * c, z0 + z]
    return at


_BAND_KEYS       = ("type", "material", "thickness", "offset", "door_type", "hinge", "swing", "slide", "opening_angle", "show_open")


def _bands(meta, key):
    raw = E.m(meta, key) or ()
    out = []
    for b in raw:
        band = {k: b[k] for k in _BAND_KEYS if k in b}
        band["from"] = float(b["from"]) / 1000.0   # stored in mm, kept in metres
        band["to"]   = float(b["to"])   / 1000.0
        out.append(band)
    return out


def spec(meta: Mapping[str, Any]) -> dict[str, Any]:
    """Every parameter, with its defaults, validated enough to build from (``check`` reports what cannot be built)."""
    fr = frame_of(meta)
    return {**fr, "frame_material": str(E.m(meta, "frame_material", "Aluminium")), "frame_profile": E.m(meta, "frame_profile"),
            "frame_width": _num(meta, "frame_width", DEFAULTS_MM["frame_width"] / 1000),
            "frame_depth": _num(meta, "frame_depth", DEFAULTS_MM["frame_depth"] / 1000),
            "mullion_depth": _num(meta, "mullion_depth", _num(meta, "frame_depth", DEFAULTS_MM["frame_depth"] / 1000)),
            "transom_depth": _num(meta, "transom_depth", _num(meta, "frame_depth", DEFAULTS_MM["frame_depth"] / 1000)),
            "corner_radius": _num(meta, "corner_radius", 0.0), "join": str(E.m(meta, "join", "mullions")),
            "v": {"mode": str(E.m(meta, "v_mode", "centre")), "value": _num(meta, "v_value", 1.2), "align": str(E.m(meta, "v_align", "start"))},
            "h": {"mode": str(E.m(meta, "h_mode", "centre")), "value": _num(meta, "h_value", H_DEFAULT), "align": str(E.m(meta, "h_align", "start"))},
            "removed": {"v": [float(x) for x in (E.m(meta, "removed_v") or ())], "h": [float(x) for x in (E.m(meta, "removed_h") or ())]},
            "extra": {"v": [float(x) for x in (E.m(meta, "extra_v") or ())], "h": [float(x) for x in (E.m(meta, "extra_h") or ())]},
            "panel_type": str(E.m(meta, "panel_type", "Window")), "panel_material": str(E.m(meta, "panel_material", "Glass")),
            "panel_thickness": _num(meta, "panel_thickness", DEFAULTS_MM["panel_thickness"] / 1000), "panel_offset": _num(meta, "panel_offset", 0.0),
            "panels": dict(E.m(meta, "panels") or {}),
            "door_type": str(E.m(meta, "door_type", "Single Flush Door")), "door_hinge": str(E.m(meta, "door_hinge", "start")), "door_swing": int(E.m(meta, "door_swing", 1)),
            "opening_angle": _num(meta, "opening_angle", E.DEFAULT_OPENING_ANGLE),
            "top_profile": [tuple(map(float, q)) for q in (E.m(meta, "top_profile") or ())] or None,     # a top that follows a roof (attachments)
            "bands": {"rows": _bands(meta, "band_rows"), "cols": _bands(meta, "band_cols")},
            "fin":   {"depth": _num(meta, "fin_depth",   0.0),
                      "width": _num(meta, "fin_width",   0.05),
                      "offset": _num(meta, "fin_offset", 0.0),
                      "side":  str(E.m(meta, "fin_side",  "left"))},
            "shelf": {"depth": _num(meta, "shelf_depth", 0.0),
                      "width": _num(meta, "shelf_width", 0.05),
                      "offset": _num(meta, "shelf_offset", 0.0),
                      "side":  str(E.m(meta, "shelf_side", "up"))},
            "louvre": {"pitch": _num(meta, "louvre_pitch", 0.15),
                       "tilt": _num(meta, "louvre_tilt", 30.0),
                       "depth": _num(meta, "louvre_depth", 0.10),
                       "thickness": _num(meta, "louvre_thickness", 0.002)},
            "end_cond": dict(E.m(meta, "end_cond") or {}), "corner_posts": list(E.m(meta, "corner_posts") or [])}         # what its ends meet (resolve_ends)


# ------------------------------------------------------------------------------------------------------------------------- layout
def bays(span: float, mode: str, value: float, align: str = "start", min_bay: float = 0.0) -> list[float]:
    """The lengths of the bays a span is divided into, in order. ``span`` is axis to axis. With centre-to-centre spacing, a remainder bay shorter than
    ``min_bay`` (too short for a field between two members) is not made: it is folded into the bay next to it, so a rule never produces a member that
    cannot be built (the same as a façade's sliver)."""
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
        elif rest and rest < min_bay - 1e-12:                                     # a sliver: fold it into the neighbouring bay(s)
            if align == "start":
                out = [value] * (full - 1) + [value + rest]
            elif align == "end":
                out = [value + rest] + [value] * (full - 1)
            elif full >= 2:
                out = [value + rest / 2] + [value] * (full - 2) + [value + rest / 2]
            else:
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
        for b in bays(span, rule["mode"], rule["value"], rule["align"], w + MIN_FIELD)[:-1]:
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


def _material_for(sp, kind):
    if kind == "Door":     return sp["frame_material"]
    if kind == "Louvre":   return "Aluminium"
    if sp["panel_type"] == kind:
        return sp["panel_material"]        # the wall's own material, when the type matches
    return {"Window": "Glass", "Opaque panel": "Aluminium"}.get(kind, "Glass")


def cell_spec(sp, cell):
    wall = {"type": sp["panel_type"], "material": None,
            "thickness": sp["panel_thickness"], "offset": sp["panel_offset"],
            "door_type": sp["door_type"], "hinge": sp["door_hinge"],
            "swing": sp["door_swing"], "slide": "end",
            "opening_angle": sp["opening_angle"], "show_open": False}
    layers = [wall]
    for bands, (lo, hi) in ((sp["bands"]["cols"], (cell["u0"], cell["u1"])),
                            (sp["bands"]["rows"], (cell["z0"], cell["z1"]))):
        for b in bands:
            if b["from"] < hi and b["to"] > lo:      # overlap, not containment
                layers.append(b); break
    layers.append(sp["panels"].get(cell["key"]) or {})

    out = dict(wall)
    for l in layers[1:]:
        for k, v in l.items():
            if v is not None and k in out:
                out[k] = v
    if out["material"] is None:
        out["material"] = _material_for(sp, out["type"])
    # if the type was changed by a band but no material was, follow the new type's default
    if out["type"] != sp["panel_type"] and not any("material" in l for l in layers[1:]):
        out["material"] = _material_for(sp, out["type"])
    return out


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


def _prism_uz(poly: Sequence[tuple[float, float]], d0: float, d1: float) -> G.Mesh:
    """A solid from a polygon in the wall's own (u, z) plane, ``d0`` to ``d1`` across the wall."""
    verts, faces = G.prism([tuple(q) for q in poly], d0, d1)
    return _positive([[x, z_, y] for x, y, z_ in verts], faces)


def _line_at(profile: Sequence[tuple[float, float]], u: float) -> float:
    """Height of a polyline of (u, z) points at ``u`` (held level beyond its ends)."""
    if u <= profile[0][0]:
        return profile[0][1]
    for (u0, z0), (u1, z1) in zip(profile, profile[1:]):
        if u <= u1 + 1e-12:
            return z0 if u1 - u0 < 1e-12 else z0 + (z1 - z0) * (u - u0) / (u1 - u0)
    return profile[-1][1]


def top_lines(sp: Mapping[str, Any]) -> tuple[list[tuple[float, float]], list[tuple[float, float]]] | None:
    """For a curtain wall whose top follows a roof: the top line (the roof's underside along the wall, as (u, z) from the base) and the underside of
    the sloped top member, a frame width below it measured square to it. ``None`` for a level top."""
    prof = sp.get("top_profile")
    if not prof:
        return None
    top = [(float(u), float(z)) for u, z in prof]
    w = sp["frame_width"]
    try:
        low = G.offset_chain(top, False, -w)                                             # to the right of travel along +u: below the top line
    except G.GeometryError as exc:
        raise BadRequest(f"The roof line is too sharp for a {w * 1000:.0f} mm top member: {exc}") from exc
    for k, idx in ((0, 0), (1, -1)):                                                      # carry the ends back onto u = 0 and u = L along their own segments
        p, q = (low[0], low[1]) if k == 0 else (low[-1], low[-2])
        target = top[0][0] if k == 0 else top[-1][0]
        if abs(q[0] - p[0]) > 1e-12:
            low[idx] = (target, p[1] + (q[1] - p[1]) * (target - p[0]) / (q[0] - p[0]))
    return top, low


def _clip_below(u0: float, u1: float, z0: float, z1: float, low: Sequence[tuple[float, float]] | None) -> list[list[tuple[float, float]]]:
    """The parts of the rectangle u0..u1 × z0..z1 that lie under the polyline ``low`` (all of it when there is none), as polygons in (u, z)."""
    if low is None:
        return [[(u0, z0), (u1, z0), (u1, z1), (u0, z1)]]
    xs = {u0, u1} | {u for u, _ in low if u0 < u < u1}
    for (a, za), (b, zb) in zip(low, low[1:]):                                            # where the line crosses the rectangle's top and bottom
        for level in (z0, z1):
            if (za - level) * (zb - level) < 0 and abs(zb - za) > 1e-12:
                u = a + (b - a) * (level - za) / (zb - za)
                if u0 < u < u1:
                    xs.add(u)
    xs_sorted = sorted(xs)
    top = [(u, min(z1, _line_at(low, u))) for u in xs_sorted]
    out: list[list[tuple[float, float]]] = []
    run: list[tuple[float, float]] = []
    for u, z in top + [(math.inf, -math.inf)]:
        if z > z0 + 1e-6:
            run.append((u, z))
            continue
        if len(run) >= 2:
            poly = [(run[0][0], z0), (run[-1][0], z0)] + run[::-1]
            pts: list[tuple[float, float]] = []
            for q in poly:
                if not pts or math.dist(pts[-1], q) > 1e-9:
                    pts.append(q)
            if len(pts) >= 3 and abs(G.signed_area(pts)) > 1e-8:
                out.append(pts)
        run = []
    return out


def _field_box(sp: Mapping[str, Any], cell: Mapping[str, Any], low: Sequence[tuple[float, float]] | None) -> tuple[float, float, float, float]:
    """A field's clear opening ``(u0, u1, z0, z1)`` as it is built: stopping at a corner post where an end gives up its perimeter member, and, under a
    sloped top, the top row reaching up to the sloped member's underside."""
    ends = sp["end_cond"]
    s0 = float(ends.get("start", {}).get("setback", 0.0)) if ends.get("start", {}).get("suppress") else None
    s1 = float(ends.get("end", {}).get("setback", 0.0)) if ends.get("end", {}).get("suppress") else None
    u0, u1, z0, z1 = cell["u0"], cell["u1"], cell["z0"], cell["z1"]
    if cell["col"] == 0 and s0 is not None:
        u0 = s0
    if cell["col"] == len(axes(sp, "v")) - 2 and s1 is not None:
        u1 = sp["length"] - s1
    if low is not None and cell["row"] == len(axes(sp, "h")) - 2:
        z1 = sp["height"]
    return u0, u1, z0, z1


def _under(line: Sequence[tuple[float, float]], u0: float, u1: float) -> tuple[float, float]:
    """The lowest and highest height of a polyline over ``u0``..``u1``."""
    zs = [_line_at(line, u0), _line_at(line, u1)] + [z for u, z in line if u0 < u < u1]
    return min(zs), max(zs)


def _door_top(low: Sequence[tuple[float, float]], u0: float, u1: float, z1: float, top_row: bool) -> float | None:
    """Where a door in a field ends under a roof-following top: the field's own top when the roof is above it everywhere over the field, the roof's
    level when the field is the top row and the roof is level over it, and ``None`` when the roof slopes down into it (a leaf is a whole rectangle)."""
    lo, hi = _under(low, u0, u1)
    if lo >= z1 - 1e-9:
        return z1
    return lo if top_row and hi - lo < 1e-9 else None


def door_conflicts(sp: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The door fields a roof-following top cuts into (a door leaf is a whole rectangle and cannot be cut), each with its measures in metres:
    ``cell`` (key), ``col``, ``row``, ``u0 u1 z0 z1``, ``clear`` (the lowest height the frame leaves under the roof over the field) and ``roof`` (the roof's
    own lowest and highest height over it)."""
    both = top_lines(sp)
    if both is None:
        return []
    top, low = both
    out = []
    for cell in cells(sp):
        if cell_spec(sp, cell)["type"] != "Door":
            continue
        u0, u1, z0, z1 = _field_box(sp, cell, low)
        top_row = cell["row"] == len(axes(sp, "h")) - 2
        zt = _door_top(low, u0, u1, z1, top_row)
        if zt is not None and zt - z0 >= MIN_DOOR_FIELD[1]:
            continue
        out.append({"cell": cell["key"], "col": cell["col"], "row": cell["row"], "u0": u0, "u1": u1, "z0": z0, "z1": z1,
                    "clear": _under(low, u0, u1)[0], "roof": _under(top, u0, u1)})
    return out


def _slats(u0, u1, z0, z1, yd, lo):
    """Stack of tilted slats filling the field; each is a thin box rotated in the (d, z) plane about its centre."""
    p  = max(lo["pitch"], 0.005)
    th = lo["tilt"] * math.pi / 180.0
    c, s = math.cos(th), math.sin(th)
    slats = []
    z = z0 + p / 2
    while z < z1 - 1e-6:
        # unrotated box: u0..u1 × [yd - depth/2, yd + depth/2] × [z - t/2, z + t/2]
        verts = [
            [u0, yd - lo["depth"]/2, z - lo["thickness"]/2],
            [u1, yd - lo["depth"]/2, z - lo["thickness"]/2],
            [u1, yd + lo["depth"]/2, z - lo["thickness"]/2],
            [u0, yd + lo["depth"]/2, z - lo["thickness"]/2],
            [u0, yd - lo["depth"]/2, z + lo["thickness"]/2],
            [u1, yd - lo["depth"]/2, z + lo["thickness"]/2],
            [u1, yd + lo["depth"]/2, z + lo["thickness"]/2],
            [u0, yd + lo["depth"]/2, z + lo["thickness"]/2],
        ]
        # rotate each vertex about the slat's centre line (d = yd, z = z)
        rot = [[v[0],
                yd + (v[1] - yd) * c - (v[2] - z) * s,
                z  + (v[1] - yd) * s + (v[2] - z) * c] for v in verts]
        slats.append(_positive(rot, [
            [0,1,2],[0,2,3], [4,6,5],[4,7,6],
            [0,4,5],[0,5,1], [1,5,6],[1,6,2],
            [2,6,7],[2,7,3], [3,7,4],[3,4,0],
        ]))
        z += p
    return slats


def members(project: Any, meta: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every solid of the curtain wall in the wall's own frame (u along, d across, z up from the base), in drawing order: the frame, then each field's
    glazing, panel or door leaves. Each carries its ``kind``, ``material``, ``mesh``, what it is (``role``, ``cell``, ``length``) and its component
    ``id`` (``v:start``, ``v:<n>``, ``v:end`` for the vertical members, ``h:...`` for the horizontal ones, ``top`` for a sloped top member, ``cell:c,r``
    for a field, ``corner:start`` / ``corner:end`` for a corner post), so a click, the Properties panel and the plan all name the same part.

    Two things beyond the grid: a top that follows a roof (``top_profile``) becomes one sloped top member, the members and fields under it are cut
    along its underside and those wholly above it are left out; and an end that meets another curtain wall (``end_cond``) gives up its perimeter
    member to the corner post and stops where the post begins."""
    return _members(project, spec(meta))


def _members(project: Any, sp: Mapping[str, Any], doors: bool = True) -> list[dict[str, Any]]:
    """``members`` for a given spec (so a correction can be tried on a changed copy of it); ``doors=False`` leaves the check that a door is whole to the caller."""
    L, H, w = sp["length"], sp["height"], sp["frame_width"]
    Dv, Dh = sp["mullion_depth"], sp["transom_depth"]
    sec_v = ring(w, Dv, sp["corner_radius"]);  area_v = _area(sec_v)
    sec_h = ring(w, Dh, sp["corner_radius"]);  area_h = _area(sec_h)
    V, Hh = axes(sp, "v"), axes(sp, "h")
    v_ids = ["v:start"] + [f"v:{i}" for i, q in enumerate(lines(sp, "v")) if not q["removed"]] + ["v:end"]
    h_ids = ["h:bottom"] + [f"h:{i}" for i, q in enumerate(lines(sp, "h")) if not q["removed"]] + ["h:top"]
    ends = sp["end_cond"]
    s0 = float(ends.get("start", {}).get("setback", 0.0)) if ends.get("start", {}).get("suppress") else None
    s1 = float(ends.get("end", {}).get("setback", 0.0)) if ends.get("end", {}).get("suppress") else None
    lines_top = top_lines(sp)
    low = lines_top[1] if lines_top else None
    out: list[dict[str, Any]] = []

    def frame(cid, role, horizontal, axis, a0, a1, k0=0.0, k1=0.0):
        if a1 - a0 < 1e-6:
            return
        s_, ar_ = (sec_h, area_h) if horizontal else (sec_v, area_v)
        out.append({"kind": "frame", "material": sp["frame_material"], "role": role, "cell": None,
                    "id": cid, "length": a1 - a0, "area": ar_,
                    "mesh": _member(s_, horizontal, axis, a0, a1, k0, k1)})

    def vertical(cid: str, role: str, u: float, z_lo: float, z_hi: float, k0: float = 0.0) -> None:
        """A vertical member from z_lo up to z_hi, or, under a sloped top, up to the top member's underside (cut along it)."""
        if low is None:
            frame(cid, role, False, u, z_lo, z_hi, k0, 0.0)
            return
        za, zb = _line_at(low, u - w / 2), _line_at(low, u + w / 2)
        top = min(z_hi, (za + zb) / 2)
        k1 = (zb - za) / w if top < z_hi - 1e-9 else 0.0
        if top - max(abs(k1) * w / 2, 0) - z_lo > 1e-6:
            frame(cid, role, False, u, z_lo, top, k0, k1)

    def horizontal(cid: str, role: str, z: float, a0: float, a1: float, k0: float = 0.0, k1: float = 0.0) -> None:
        """A horizontal member from a0 to a1 at height z; under a sloped top it is trimmed where its top face would rise into the top member, and
        its end is then cut along the top member's underside (so the two meet on that plane without overlapping)."""
        if low is None:
            frame(cid, role, True, z, a0, a1, k0, k1)
            return
        need = z + w / 2
        pts = [(a0, _line_at(low, a0)), *[(u, zz) for u, zz in low if a0 < u < a1], (a1, _line_at(low, a1))]
        ok = [zz >= need - 1e-9 for _, zz in pts]
        if all(ok):
            frame(cid, role, True, z, a0, a1, k0, k1)
            return
        if not any(ok):
            return
        first, last = ok.index(True), len(ok) - 1 - ok[::-1].index(True)
        if not all(ok[first:last + 1]):
            raise BadRequest("The roof dips into the middle of a horizontal member of the curtain wall: remove that member or lower it")
        lo_u, hi_u = a0, a1
        if first > 0:                                                                     # it rises past the line on the left: cut along it there
            (pu, pz), (qu, qz) = pts[first - 1], pts[first]
            slope = (qz - pz) / (qu - pu)
            u_top = pu + (qu - pu) * (need - pz) / (qz - pz)
            lo_u, k0 = u_top - (w / 2) / slope, 1.0 / slope
        if last < len(pts) - 1:                                                           # and on the right
            (pu, pz), (qu, qz) = pts[last], pts[last + 1]
            slope = (qz - pz) / (qu - pu)
            u_top = pu + (qu - pu) * (need - pz) / (qz - pz)
            hi_u, k1 = u_top - (w / 2) / slope, 1.0 / slope
        if hi_u - lo_u > w:
            frame(cid, role, True, z, max(lo_u, a0 - w), min(hi_u, a1 + w), k0, k1)

    join = sp["join"]
    last_v, last_h = len(V) - 1, len(Hh) - 1
    top_index = last_h if low is None else last_h - 1                                    # a sloped top replaces the top perimeter member
    for i, u in enumerate(V):
        perimeter = i in (0, last_v)
        if (i == 0 and s0 is not None) or (i == last_v and s1 is not None):
            continue                                                                      # given up to the corner post
        role = "perimeter" if perimeter else "mullion"
        cid = v_ids[i]
        if low is not None:
            vertical(cid, role, u, 0.0 if perimeter and join in ("mullions", "mitre") else w, H)
        elif join == "mullions":
            frame(cid, role, False, u, 0.0, H)
        elif join == "transoms":
            for r in range(len(Hh) - 1):
                frame(cid, role, False, u, Hh[r] + w / 2, Hh[r + 1] - w / 2)
        elif perimeter:                                                                  # mitre: the perimeter members are cut at 45° where they meet
            left = i == 0
            frame(cid, role, False, u, w / 2, H - w / 2, 1.0 if left else -1.0, -1.0 if left else 1.0)
        else:
            frame(cid, role, False, u, w, H - w)
    start_u = s0 if s0 is not None else None
    end_u = L - s1 if s1 is not None else None
    for r, z in enumerate(Hh):
        if r > top_index:
            continue
        perimeter = r in (0, last_h)
        role = "perimeter" if perimeter else "transom"
        cid = h_ids[r]
        if join == "transoms" or low is not None:
            a0 = start_u if start_u is not None else (0.0 if join == "transoms" else V[0] + w / 2)
            a1 = end_u if end_u is not None else (L if join == "transoms" else V[-1] - w / 2)
            if low is not None and join != "transoms":
                for c in range(len(V) - 1):
                    b0 = start_u if (c == 0 and start_u is not None) else V[c] + w / 2
                    b1 = end_u if (c == len(V) - 2 and end_u is not None) else V[c + 1] - w / 2
                    horizontal(cid, role, z, b0, b1)
            else:
                horizontal(cid, role, z, a0, a1)
        elif join == "mitre" and perimeter:
            bottom = r == 0
            a0 = start_u if start_u is not None else w / 2
            a1 = end_u if end_u is not None else L - w / 2
            k0 = 0.0 if start_u is not None else (1.0 if bottom else -1.0)
            k1 = 0.0 if end_u is not None else (-1.0 if bottom else 1.0)
            frame(cid, role, True, z, a0, a1, k0, k1)
        else:
            for c in range(len(V) - 1):
                b0 = start_u if (c == 0 and start_u is not None) else V[c] + w / 2
                b1 = end_u if (c == len(V) - 2 and end_u is not None) else V[c + 1] - w / 2
                frame(cid, role, True, z, b0, b1)
    if lines_top is not None:                                                            # the sloped top member, from end to end (or post to post)
        top, lowl = lines_top
        a0 = start_u if start_u is not None else 0.0
        a1 = end_u if end_u is not None else L
        poly = [(u, z) for u, z in top if a0 - 1e-9 <= u <= a1 + 1e-9]
        poly = [(a0, _line_at(top, a0))] + [q for q in poly if a0 + 1e-9 < q[0] < a1 - 1e-9] + [(a1, _line_at(top, a1))]
        under = [(a0, _line_at(lowl, a0))] + [q for q in lowl if a0 + 1e-9 < q[0] < a1 - 1e-9] + [(a1, _line_at(lowl, a1))]
        outline = poly + under[::-1]
        length = sum(math.dist(p, q) for p, q in zip(poly, poly[1:]))
        out.append({"kind": "frame", "material": sp["frame_material"], "role": "perimeter",
                    "cell": None, "id": "top", "length": length, "area": w * sp["transom_depth"],
                    "mesh": _prism_uz(outline, -sp["transom_depth"] / 2, sp["transom_depth"] / 2)})
    for post in sp["corner_posts"]:                                                      # a corner post this wall carries for its junction
        poly = [(float(u), float(d)) for u, d in post["poly"]]
        zt = H if low is None else _line_at(lines_top[0], 0.0 if post["end"] == "start" else L)
        verts, faces = G.prism(poly, 0.0, zt)
        out.append({"kind": "frame", "material": sp["frame_material"], "role": "corner", "cell": None, "id": f"corner:{post['end']}", "length": zt,
                    "area": abs(G.signed_area(poly)), "mesh": _positive([[x, y, z_] for x, y, z_ in verts], faces)})
    # fields
    for cell in cells(sp):
        cs = cell_spec(sp, cell)
        kind = {"Window": "glass", "Opaque panel": "panel", "Door": "door", "Louvre": "louvre"}[cs["type"]]
        u0, u1, z0, z1 = _field_box(sp, cell, low)
        yd, t = cs["offset"], cs["thickness"]
        regions = _clip_below(u0, u1, z0, z1, [(u, zz) for u, zz in low] if low is not None else None)
        if kind != "door":
            for poly in regions:
                out.append({"kind": kind, "material": cs["material"], "role": "panel", "cell": cell["key"], "id": f"cell:{cell['key']}", "area": abs(G.signed_area(poly)),
                            "thickness": t, "mesh": _prism_uz(poly, yd - t / 2, yd + t / 2)})
            continue
        if kind in ("glass", "panel"):
            for poly in regions:
                out.append({"kind": kind, "material": cs["material"], "role": "panel", "cell": cell["key"],
                            "id": f"cell:{cell['key']}", "area": abs(G.signed_area(poly)),
                            "thickness": t, "mesh": _prism_uz(poly, yd - t / 2, yd + t / 2)})
            continue
        if kind == "louvre":
            for poly in regions:
                u0m, u1m = poly[0][0], poly[1][0]
                z0m, z1m = poly[0][1], poly[2][1]
                lo = sp["louvre"]
                for slat in _slats(u0m, u1m, z0m, z1m, yd, lo):
                    out.append({"kind": "louvre", "material": cs["material"], "role": "slat",
                                "cell": cell["key"], "id": f"cell:{cell['key']}", "length": u1m - u0m,
                                "area": lo["thickness"] * lo["depth"], "thickness": lo["thickness"],
                                "mesh": slat})
            continue
        if low is not None:
            zt = _door_top(low, u0, u1, z1, cell["row"] == len(Hh) - 2)
            if zt is None and doors:
                raise BadRequest(f"The roof comes down into door field {cell['col'] + 1}.{cell['row'] + 1}: a door needs a field the roof does not cut")
            z1 = zt if zt is not None else z1
        for n, (a, b, y) in enumerate(door_leaves(cs, u0, u1, yd)):
            mesh = _box(a, b, y - E.DOOR_LEAF / 2, y + E.DOOR_LEAF / 2, z0 + GAP, z1 - GAP)
            if cs["show_open"]:
                mesh = _open_leaf(mesh, cs, n, a, b, y)
            out.append({"kind": "door", "material": cs["material"], "role": "leaf", "cell": cell["key"], "id": f"cell:{cell['key']}", "leaf": n,
                        "area": (b - a) * (z1 - z0 - 2 * GAP), "thickness": E.DOOR_LEAF, "mesh": mesh})

    fin, shelf = sp["fin"], sp["shelf"]
    if fin["depth"] > 1e-6:
        for u in axes(sp, "v"):
            sides = (1,) if fin["side"] == "left" else (-1,) if fin["side"] == "right" else (1, -1)
            for s in sides:
                d0 = s * (Dv / 2 + fin["offset"]) if s > 0 else -(-s * (Dv / 2 + fin["offset"]))
                # simpler and clearer:
                base_d = s * (Dv / 2 + fin["offset"])
                d_in  = base_d
                d_out = base_d + s * fin["depth"]
                dmin, dmax = sorted((d_in, d_out))
                out.append({"kind": "frame", "material": sp["frame_material"], "role": "fin",
                            "cell": None, "id": f"fin:{u:.4f}:{s}",
                            "length": H, "area": fin["width"] * fin["depth"],
                            "mesh": _box(u - fin["width"] / 2, u + fin["width"] / 2, dmin, dmax, 0.0, H)})
    if shelf["depth"] > 1e-6:
        for z in axes(sp, "h"):
            sides = (1,) if shelf["side"] == "up" else (-1,) if shelf["side"] == "down" else (1, -1)
            for s in sides:
                base_d = s * (Dh / 2 + shelf["offset"])
                d_in, d_out = base_d, base_d + s * shelf["depth"]
                dmin, dmax = sorted((d_in, d_out))
                out.append({"kind": "frame", "material": sp["frame_material"], "role": "shelf",
                            "cell": None, "id": f"shelf:{z:.4f}:{s}",
                            "length": L, "area": shelf["width"] * shelf["depth"],
                            "mesh": _box(0.0, L, dmin, dmax, z - shelf["width"] / 2, z + shelf["width"] / 2)})
    out.sort(key=lambda q: PART_ORDER.index(q["kind"]))                                   # stable: the frame first, then glazing, panels and leaves, so faces group by part
    return out


def leaf_hinges(cs: Mapping[str, Any], a: float, b: float, y: float, n: int) -> tuple[float, float, int]:
    """Where a door leaf turns (u, d of its hinge axis) and which way (+1 counter-clockwise seen from above, in the wall's frame), by the same rule as a door
    in a wall (``door_swing.turn_sign``): a single leaf on its hinge side, a pair on both jambs."""
    from .door_swing import turn_sign
    hinge = cs["hinge"] if cs["door_type"] == "Single Flush Door" else ("start" if n == 0 else "end")
    swing = int(cs["swing"])
    u = a if hinge == "start" else b
    d = y + swing * E.DOOR_LEAF / 2                                                       # on the leaf's face toward the swing side, as a wall's door
    return u, d, turn_sign(hinge, swing)


def _open_leaf(mesh: G.Mesh, cs: Mapping[str, Any], n: int, a: float, b: float, y: float) -> G.Mesh:
    """A closed leaf shown open: turned by the field's opening angle about its hinge (a sliding leaf slides its own width instead)."""
    verts, faces = mesh
    if cs["door_type"] == "Sliding Door":
        if n != 0:
            return mesh
        shift = (b - a) * (1 if cs["slide"] == "end" else -1)
        return [[x + shift, d, z] for x, d, z in verts], faces
    hu, hd, sign = leaf_hinges(cs, a, b, y, n)
    ang = math.radians(cs["opening_angle"]) * sign
    c, s = math.cos(ang), math.sin(ang)
    return [[hu + (x - hu) * c - (d - hd) * s, hd + (x - hu) * s + (d - hd) * c, z] for x, d, z in verts], faces


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
    resolve_ends(project, oid)                                                            # what its ends meet decides where its frame stops
    meta = E.meta_of(project, oid)
    check_spec(project, meta, oid)
    mesh, runs = _parts(project, meta)
    E._attach(project, oid, {"parts": runs, "component_faces": _component_runs(project, meta), "built_sig": _signature(meta)})
    E._set_mesh(project, oid, mesh)


def _component_runs(project: Any, meta: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Which part every face of the built mesh belongs to, as runs in face order (the same order ``_parts`` builds them in)."""
    runs: list[dict[str, Any]] = []
    for q in members(project, meta):
        n = len(q["mesh"][1])
        if runs and runs[-1]["id"] == q["id"]:
            runs[-1]["count"] += n
        else:
            runs.append({"id": q["id"], "count": n})
    return runs


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
def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


DOOR_MIN_HEIGHT = 1.8                  # m: the least clear height a correction may leave a door (what is offered, not what is allowed)
DOOR_MIN_WIDTH = 0.7                   # m: and the least width of a field a door is moved to


def door_conflict(project: Any, sp: Mapping[str, Any], hit: Mapping[str, Any], oid: str | None, free: Mapping[str, Any] | None = None) -> BadRequest:
    """The error for a roof that comes down into a door field: it names the field and says where the roof is over it and by how much the door
    would be cut, then offers the corrections that leave every door whole, each verified here on a changed copy of the wall (``details.fixes``). The corrections
    run on the wall as it is stored (``free``: its own height, not yet following the roof) and the change that was refused is then made again, so each must
    work in both states."""
    w = sp["frame_width"]
    col, row, key = hit["col"], hit["row"], hit["cell"]
    mm = lambda v: f"{v * 1000:,.0f}"                                                      # noqa: E731
    cut = hit["z1"] - hit["clear"]
    name = (project.model.get(oid)["name"] or "Curtain wall") if oid and E.alive(project, oid) else "The curtain wall"
    follows = free is not None and abs(free["height"] - sp["height"]) > 1e-6
    message = (f"{name}: the roof comes down across the door in field {col + 1}.{row + 1} ({_ordinal(col + 1)} field from the start, {_ordinal(row + 1)} from the base). "
               + (f"With its top following the roof the wall is {mm(sp['height'])} mm high, which makes that door field {mm(hit['z1'] - hit['z0'])} mm tall "
                  f"({mm(hit['u1'] - hit['u0'])} mm wide). " if follows else
                  f"The door is {mm(hit['u1'] - hit['u0'])} mm wide and {mm(hit['z1'] - hit['z0'])} mm tall. ")
               + f"Over that field the roof's underside is only {mm(hit['roof'][0])} to {mm(hit['roof'][1])} mm above the base and the top frame member needs "
               f"{mm(w)} mm under it, so the door would be cut off by {mm(cut)} mm. A door cannot be cut: it needs a field the roof does not reach")
    fixes: list[dict[str, Any]] = []
    blocked = ""
    before = len(door_conflicts(sp))
    if oid:
        set_ = lambda k, value: {"op": "model.set_property", "params": {"id": oid, "key": k, "value": value}}          # noqa: E731

        def works(changed: Mapping[str, Any]) -> bool:
            """The changed wall has room everywhere, fewer cut doors than now, and builds; and the same change leaves the stored (free) wall buildable."""
            try:
                trial = {**sp, **changed}
                if not _fits(trial) or len(door_conflicts(trial)) >= before:
                    return False
                _members(project, trial, doors=False)
                stored = {**(free or {**sp, "top_profile": None}), **changed}
                if not _fits(stored):
                    return False
                _members(project, stored, doors=False)
                return True
            except (BadRequest, G.GeometryError):
                return False

        def panels_with(edit: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
            out = {k: dict(v) for k, v in sp["panels"].items()}
            for k, v in edit.items():
                out[k] = {**out.get(k, {}), **v}
            return out

        over = sp["panels"].get(key) or {}
        carried = [(f, over[f]) for f in ("door_type", "hinge", "swing", "slide", "opening_angle") if f in over]
        names = {"swing": lambda v: "left" if int(v) == 1 else "right"}
        # 1. a transom just under the roof's lowest point over the field: the door keeps its width, loses only the part the roof would cut
        at = math.floor((hit["clear"] - w / 2) * 1000) / 1000
        door_h = at - w / 2 - hit["z0"]
        stored_top = (free or sp)["height"] - w - MIN_FIELD / 2
        if door_h >= DOOR_MIN_HEIGHT and at > stored_top:
            blocked = (f"a transom at {mm(at)} mm would keep the door {mm(door_h)} mm tall, but this wall is only {mm((free or sp)['height'])} mm high before it follows "
                       f"the roof: make it taller first")
        if door_h >= DOOR_MIN_HEIGHT and at - w / 2 > hit["z0"]:
            extra = {**sp["extra"], "h": list(sp["extra"]["h"]) + [at]}
            trial = {**sp, "extra": extra}
            trial["panels"] = _remap_panels(project, oid or "", cells(sp), sp["panels"], cells(trial))
            if works({"extra": extra, "panels": trial["panels"]}):
                fixes.append({"label": f"Add a transom at {mm(at)} mm above the base: the door stays {mm(hit['u1'] - hit['u0'])} mm wide and becomes {mm(door_h)} mm tall, "
                                       f"and the roof cuts only the glazing above it", "steps": [set_("add_line:h", at * 1000)]})
        # 2. the nearest field the roof does not reach takes the door, this one becomes glazing
        mid = (hit["u0"] + hit["u1"]) / 2
        both = top_lines(sp)
        low = both[1] if both else None
        spare = []
        for cell in cells(sp):
            if cell["key"] == key or cell["row"] != 0 or cell_spec(sp, cell)["type"] == "Door":                  # a door stands on the floor: the bottom row only
                continue
            u0, u1, z0, z1 = _field_box(sp, cell, low)
            zt = z1 if low is None else _door_top(low, u0, u1, z1, cell["row"] == len(axes(sp, "h")) - 2)
            if zt is not None and u1 - u0 >= DOOR_MIN_WIDTH and zt - z0 >= DOOR_MIN_HEIGHT:
                spare.append((abs((u0 + u1) / 2 - mid), cell))
        for _, cell in sorted(spare, key=lambda t: t[0])[:1]:
            tk = cell["key"]
            edit = {key: {"type": "Window"}, tk: {"type": "Door", **dict(carried)}}
            if works({"panels": panels_with(edit)}):
                steps = [set_(f"panel:{col}:{row}:type", "Window"), set_(f"panel:{cell['col']}:{cell['row']}:type", "Door")]
                steps += [set_(f"panel:{cell['col']}:{cell['row']}:{f}", names[f](v) if f in names else v) for f, v in carried]
                fixes.append({"label": f"Move the door to field {cell['col'] + 1}.{cell['row'] + 1} ({mm(cell['u1'] - cell['u0'])} mm wide, fully under the roof) "
                                       f"and make field {col + 1}.{row + 1} a window", "steps": steps})
        # 3. just make the field glazing
        if works({"panels": panels_with({key: {"type": "Window"}})}):
            fixes.append({"label": f"Make field {col + 1}.{row + 1} a window instead of a door", "steps": [set_(f"panel:{col}:{row}:type", "Window")]})
    hint = ("Or leave the wall's top free of the roof: choose Leave it." if fixes else
            "Make that field a window or panel, or add a transom under the roof, or leave the wall's top free of the roof.")
    return BadRequest(message, hint=(f"{blocked[0].upper()}{blocked[1:]}. {hint}" if blocked else hint),
                      details={"element": oid, "part": f"cell:{key}", "cell": key, "cut_mm": round(cut * 1000, 3), "clear_mm": round(hit["clear"] * 1000, 3),
                               "fixes": fixes})


def spacing_conflict(project: Any, sp: Mapping[str, Any], d: str, i: int, oid: str | None) -> BadRequest:
    """The error for two members too close for a field between them: it names the wall, both members and where they are, says how much room is
    missing, and offers the corrections that would make it buildable, each as ready-to-run commands with exact values (``details.fixes``)."""
    w = sp["frame_width"]
    pos = axes(sp, d)
    a, b = pos[i], pos[i + 1]
    total = sp["length"] if d == "v" else sp["height"]
    noun, nouns, along = ("mullion", "mullions", "from the start") if d == "v" else ("transom", "transoms", "above the base")
    listing = lines(sp, d)
    present = [k for k, q in enumerate(listing) if not q["removed"]]

    def member(j: int) -> tuple[str, str, int | None]:
        """Label, part id and (for an interior member) its index in ``lines``."""
        if j == 0:
            return ("the start member" if d == "v" else "the bottom member"), f"{d}:{'start' if d == 'v' else 'bottom'}", None
        if j == len(pos) - 1:
            return ("the end member" if d == "v" else "the top member"), f"{d}:{'end' if d == 'v' else 'top'}", None
        k = present[j - 1]
        return f"{noun} {k + 1}", f"{d}:{k}", k

    (la, pa, ka), (lb, pb, kb) = member(i), member(i + 1)
    gap = b - a - w
    name = (project.model.get(oid)["name"] or "Curtain wall") if oid and E.alive(project, oid) else "The curtain wall"
    message = (f"{name}: {la} ({a * 1000:,.0f} mm {along}) and {lb} ({b * 1000:,.0f} mm) are {(b - a) * 1000:,.0f} mm apart axis to axis. With a "
               f"{w * 1000:.0f} mm frame that leaves {max(gap, 0) * 1000:.0f} mm between them, and a panel needs at least {MIN_FIELD * 1000:.0f} mm")
    fixes: list[dict[str, Any]] = []
    if oid:
        set_ = lambda key, value: {"op": "model.set_property", "params": {"id": oid, "key": key, "value": value}}          # noqa: E731

        def offer(label: str, steps: list[dict[str, Any]], changed: Mapping[str, Any]) -> None:
            """Offer a correction only when the wall it leads to really has room for every panel (checked here, on the same rules)."""
            if _fits({**sp, **changed}):
                fixes.append({"label": label, "steps": steps})

        for label, k, at in ((la, ka, a), (lb, kb, b)):
            if k is None:
                continue
            q = listing[k]
            gone = ({"removed": {**sp["removed"], d: list(sp["removed"][d]) + [q["pos"]]}} if q["src"] == "rule"
                    else {"extra": {**sp["extra"], d: [x for x in sp["extra"][d] if not _same(x, q["pos"])]}})
            offer(f"Remove {label} (at {at * 1000:,.0f} mm)", [set_(f"line:{d}:{k}", "removed")], gone)
        rule, span, need = sp[d], total - w, w + MIN_FIELD
        fresh = {"removed": {**sp["removed"], d: []}, "extra": {**sp["extra"], d: []}}
        own = bays(span, rule["mode"], rule["value"], rule["align"], need)
        if min(own) >= need - 1e-9:
            pass                                                                          # the rule itself leaves room: the clash is a hand-placed member
        elif rule["mode"] == "centre":
            n0 = max(1, int(span / rule["value"]))
            for n in (n0, n0 + 1, n0 - 1):
                if n >= 1 and span / n >= need:
                    value = round(span / n * 1000, 1)
                    offer(f"Space the {nouns} {value:,.1f} mm apart ({n} equal bays)", [set_(f"{d}_value", value)], {d: {**rule, "value": value / 1000}, **fresh})
                    break
        elif rule["mode"] == "count":
            most = max(0, int(span / need + 1e-9) - 1)
            offer(f"Use {most} interior {nouns}, the most that leave room for panels", [set_(f"{d}_value", most)], {d: {**rule, "value": float(most)}, **fresh})
        else:
            value = math.ceil(need * 1000)
            offer(f"Set the {rule['mode']}imum spacing to {value:,} mm", [set_(f"{d}_value", value)], {d: {**rule, "value": value / 1000}, **fresh})
        narrow = math.floor(((b - a) - MIN_FIELD) * 1000)
        if narrow >= max(10, w * 1000 / 2):                                                # a frame less than half as wide is not a sensible suggestion
            offer(f"Make the frame {narrow} mm wide (a custom size instead of the catalogue profile)", [set_("frame_width", narrow)], {"frame_width": narrow / 1000})
    return BadRequest(message, hint=(f"Or change it yourself: a wider spacing, one {noun} fewer, or a narrower frame." if fixes else f"Use a wider spacing, remove a {noun}, or make the frame narrower."),
                      details={"element": oid, "part": pb if kb is not None else pa, "direction": d, "positions_mm": [round(a * 1000, 3), round(b * 1000, 3)],
                               "gap_mm": round(gap * 1000, 3), "needed_mm": MIN_FIELD * 1000, "fixes": fixes})


def _fits(sp: Mapping[str, Any]) -> bool:
    """Whether every field of a (possibly changed) spec has room for a panel in both directions."""
    w = sp["frame_width"]
    try:
        return all(b - a - w >= MIN_FIELD - 1e-9 for d in ("v", "h") for a, b in zip(axes(sp, d), axes(sp, d)[1:]))
    except BadRequest:
        return False


def check_spec(project: Any, meta: Mapping[str, Any], oid: str | None = None) -> None:
    """Raise ``BadRequest`` for parameters that cannot make a curtain wall (a message for the person, not a stack trace). With ``oid`` the error can
    offer corrections as commands on that element."""
    sp = spec(meta)
    if sp["length"] < 0.1:
        raise BadRequest("The curtain wall is too short: its start and end must be at least 100 mm apart")
    if sp["height"] < 0.1:
        raise BadRequest("The curtain wall is too low: its height must be at least 100 mm")
    if sp["frame_material"] not in FRAME_MATERIALS:
        raise BadRequest(f"The frame material must be one of {', '.join(FRAME_MATERIALS)}")
    if sp["join"] not in JOINS:
        raise BadRequest(f"The join must be one of {', '.join(JOINS)}")
    if sp["fin"]["side"] not in ("left", "right", "both"):
        raise BadRequest("fin_side must be left, right or both")
    if sp["shelf"]["side"] not in ("up", "down", "both"):
        raise BadRequest("shelf_side must be up, down or both")
    if not -60.0 <= sp["lean"] <= 60.0:
        raise BadRequest("lean must be between -60° and 60°")
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
    for d in ("v", "h"):
        pos = axes(sp, d)
        for i, (a, b) in enumerate(zip(pos, pos[1:])):
            if b - a - w < MIN_FIELD - 1e-9:
                raise spacing_conflict(project, sp, d, i, oid)
    top_to = E.m(meta, "top_to")
    free_height = float(top_to["free_height"]) if isinstance(top_to, Mapping) and top_to.get("free_height") else sp["height"]
    for hit in door_conflicts(sp):
        raise door_conflict(project, sp, hit, oid, {**sp, "height": free_height, "top_profile": None})
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
        values["mullion_depth"] = values["frame_depth"] if p.get("mullion_depth") is None else number(p["mullion_depth"], "mullion_depth", 10, 1000) / 1000.0
        values["transom_depth"] = values["frame_depth"] if p.get("transom_depth") is None else number(p["transom_depth"], "transom_depth", 10, 1000) / 1000.0
        if p.get("frame_width") is not None and abs(float(p["frame_width"]) - prof["width_mm"]) > 1e-6:
            raise BadRequest(f"{prof['name']} is {prof['width_mm']} mm wide: leave the width out, or choose no catalogue profile to give your own")
        if prof["depth_mm"] and p.get("frame_depth") is not None and abs(float(p["frame_depth"]) - prof["depth_mm"]) > 1e-6:
            raise BadRequest(f"{prof['name']} is {prof['depth_mm']} mm deep: leave the depth out, or choose no catalogue profile to give your own")
    else:
        values["frame_profile"]  = None
        values["frame_width"]    = number(p.get("frame_width",    DEFAULTS_MM["frame_width"]),  "frame_width",  10, 1000) / 1000.0
        values["frame_depth"]    = number(p.get("frame_depth",    DEFAULTS_MM["frame_depth"]),  "frame_depth",  10, 1000) / 1000.0
        values["mullion_depth"]  = values["frame_depth"] if p.get("mullion_depth") is None else number(p["mullion_depth"], "mullion_depth", 10, 1000) / 1000.0
        values["transom_depth"]  = values["frame_depth"] if p.get("transom_depth") is None else number(p["transom_depth"], "transom_depth", 10, 1000) / 1000.0
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
    values["lean"] = number(p.get("lean", 0.0), "lean", -60, 60) if p.get("lean") is not None else 0.0
    values["band_rows"] = _validate_bands(p.get("band_rows") or [], "band_rows") if p.get("band_rows") else []
    values["band_cols"] = _validate_bands(p.get("band_cols") or [], "band_cols") if p.get("band_cols") else []
    return values


def add_curtainwall(project: Any, p: Mapping[str, Any], to_m: float) -> str:
    """A curtain wall on the line ``start`` to ``end`` (drawing units) of ``level``: ``height`` and ``base_offset`` in mm, then the frame, spacing and panel
    parameters named in the module's description. All or nothing. (``add_curtainwalls`` makes a connected run from ``points``.)"""
    return add_curtainwalls(project, p, to_m)[0]


def add_curtainwalls(project: Any, p: Mapping[str, Any], to_m: float) -> list[str]:
    """One curtain wall per segment of ``points`` (or the one segment ``start`` to ``end``), all with the same parameters, joined end to end: each junction
    gets the corner that suits its angle (``corner``: auto, or one corner type for all) and can be changed afterwards. Each segment stays its own
    element with its own id. A base on a floor of its level stands on that floor's top surface (``base_floor``: auto, the default, or none); an end
    placed on a wall is hosted by it. All or nothing."""
    if p.get("points") is not None:
        raw = p.get("points")
        if not isinstance(raw, (list, tuple)) or len(raw) < 2:
            raise BadRequest("A run of curtain walls needs at least two points")
        pts = [_point(q, f"point {i + 1}", to_m) for i, q in enumerate(raw)]
    else:
        pts = [_point(p.get("start"), "start", to_m), _point(p.get("end"), "end", to_m)]
    pts = [q for i, q in enumerate(pts) if i == 0 or math.dist(q, pts[i - 1]) > 1e-9]
    if len(pts) < 2:
        raise BadRequest("The curtain wall's points are all at one place")
    closed = bool(p.get("closed")) and len(pts) >= 3
    level = project.level(text(p.get("level"), "level", 80))
    height = number(p.get("height", 3000), "height", 100, 500000) / 1000.0
    setup = _setup(p, to_m)
    corner = p.get("corner", "auto")
    if corner != "auto":
        corner = E._choice(corner, tuple(CORNER_TYPES), "corner type")
    base_offset = number(p.get("base_offset", 0.0), "base_offset", -100000, 100000) / 1000.0
    made: list[str] = []
    segs = list(zip(pts, pts[1:])) + ([(pts[-1], pts[0])] if closed else [])
    name = text(p.get("name") or "Curtain Wall", "name", 80)
    try:
        for i, (a, b) in enumerate(segs):
            values = {**setup, "category": "curtainwall", "type": "Curtain Wall", "level": level["id"], "start": list(a), "end": list(b), "height": height,
                      "base_offset": base_offset, "removed_v": [], "removed_h": [], "extra_v": [], "extra_h": [], "panels": {},
                      "material": setup["frame_material"], "opening_angle": number(p.get("opening_angle", E.DEFAULT_OPENING_ANGLE), "opening_angle", E.MIN_OPENING_ANGLE, E.MAX_OPENING_ANGLE)}
            oid = project.model.add_box(width=1.0, depth=1.0, height=1.0, name=name if len(segs) == 1 else f"{name} {i + 1}")
            E._attach(project, oid, values)
            made.append(oid)
            if p.get("base_floor", "auto") == "auto":
                from . import attachments as AT
                AT.auto_base(project, oid)
        for i in range(len(made) - 1 + (1 if closed else 0)):                              # the run's own junctions, end to start
            link(project, made[i], "end", made[(i + 1) % len(made)], "start")
        for oid in made:
            detect(project, oid)                                                          # and what the run's free ends land on
        if corner != "auto":
            for i in range(len(made) - 1 + (1 if closed else 0)):
                j = _junction(project, made[i], "end")
                if j is not None and j["angle"] < INLINE:
                    set_corner(project, made[i], "end", {"type": corner})
        for oid in made:
            build(project, oid)
    except BadRequest:
        for oid in made:
            if E.alive(project, oid):
                project.model.remove(oid)
        raise
    return made


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
        elif field == "show_open":                                                        # the 3D view shows the leaf open at its opening angle
            over["show_open"] = value in (True, "open", "true", "1", 1)
        elif field == "open_to":                                                          # dragged in 3D: the angle, and shown open there
            over["opening_angle"] = number(value, "opening angle", E.MIN_OPENING_ANGLE, E.MAX_OPENING_ANGLE)
            over["show_open"] = True
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


def _validate_bands(value, label):
    if not isinstance(value, (list, tuple)):
        raise BadRequest(f"{label} must be a list of bands")
    out = []
    for i, b in enumerate(value):
        if not isinstance(b, Mapping): raise BadRequest(f"{label}[{i}] is not a band")
        f, t = number(b.get("from"), f"{label}[{i}].from", -1e6, 1e6), number(b.get("to"), f"{label}[{i}].to", -1e6, 1e6)
        if t <= f: raise BadRequest(f"{label}[{i}]: to must be greater than from")
        if "type" in b and b["type"] not in PANEL_TYPES:
            raise BadRequest(f"{label}[{i}]: type must be one of {', '.join(PANEL_TYPES)}")
        out.append({"from": f, "to": t, **{k: b[k] for k in _BAND_KEYS if k in b}})
    return out


def set_property(project: Any, oid: str, meta: Mapping[str, Any], key: str, value: Any) -> dict[str, Any]:
    """The stored values a change of ``key`` sets (the caller attaches them and rebuilds). Line and panel keys edit the element themselves and return nothing to store."""
    if key.startswith("corner:"):
        _, end, field = (key.split(":", 2) + ["", ""])[:3]
        set_corner(project, oid, end, {field: value})
        return {}
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
    if key in ("mullion_depth", "transom_depth"):
        return {key: number(value, key.replace("_", " "), 10, 1000) / 1000.0,
                **({"frame_profile": None} if E.m(meta, "frame_profile") else {})}
    if key == "lean":
        v = number(value, "lean", -60, 60)
        return {"lean": v}
    if key in ("fin_depth", "fin_width", "fin_offset", "shelf_depth", "shelf_width", "shelf_offset"):
        limits = (-1000, 1000) if key.endswith("_offset") else (0, 1000) if key.endswith("_depth") else (1, 1000)
        return {key: number(value, key.replace("_", " "), *limits) / 1000.0}
    if key in ("fin_side", "shelf_side"):
        return {key: E._choice(value, ("left", "right", "both") if key == "fin_side" else ("up", "down", "both"), key)}
    if key == "band_rows":
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (ValueError, TypeError) as exc:
                raise BadRequest("Row bands must be a JSON list of band objects") from exc
        return {"band_rows": _validate_bands(value, "band_rows")}
    if key == "band_cols":
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (ValueError, TypeError) as exc:
                raise BadRequest("Column bands must be a JSON list of band objects") from exc
        return {"band_cols": _validate_bands(value, "band_cols")}
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
    if key in ("louvre_pitch", "louvre_depth", "louvre_thickness"):
        return {key: number(value, key.replace("_", " "), 2, 1000) / 1000.0}
    if key == "louvre_tilt":
        return {key: number(value, "louvre tilt", -80, 80)}
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
        end = [s[0] + math.cos(fr["angle"]) * new, s[1] + math.sin(fr["angle"]) * new]
        E._attach(project, oid, {"end": end})
        follow_links(project, oid, {"start": list(fr["start"]), "end": list(fr["end"])})
        return {"end": end, "removed_v": [], "extra_v": []}
    raise BadRequest(f"'{key}' is not an editable curtain wall property")


def _clear_if_width(project: Any, oid: str, meta: Mapping[str, Any], key: str) -> None:
    """A different member width shifts every axis (they stand half a width in from the edges), so individually removed or added members no longer line up."""
    if key == "frame_width":
        E._attach(project, oid, {"removed_v": [], "removed_h": [], "extra_v": [], "extra_h": []})


def move(project: Any, oid: str, meta: Mapping[str, Any], dx: float, dy: float, dz: float) -> None:
    """Move a curtain wall; the ends of the curtain walls linked to it go with its ends, so a connected run stays connected. An end hosted by a wall
    is let go when the move takes it off that wall (it is placed again where it lands)."""
    s, e = E.m(meta, "start"), E.m(meta, "end")
    values: dict[str, Any] = {"start": [s[0] + dx, s[1] + dy], "end": [e[0] + dx, e[1] + dy]}
    if abs(dz) > 1e-12:
        base_to = E.m(meta, "base_to")
        if isinstance(base_to, Mapping):
            values["base_to"] = {**base_to, "offset": float(base_to.get("offset", 0.0)) + dz}
        values["base_offset"] = float(E.m(meta, "base_offset", 0.0)) + dz
    if abs(dx) + abs(dy) > 1e-12:
        values["end_hosts"] = {}
    E._attach(project, oid, values)
    follow_links(project, oid, {"start": s, "end": e})
    if abs(dx) + abs(dy) > 1e-12:
        detect(project, oid)


def rotate(project: Any, oid: str, meta: Mapping[str, Any], angle: float, centre: Sequence[float]) -> None:
    s, e = E.m(meta, "start"), E.m(meta, "end")
    centre_t = (float(centre[0]), float(centre[1]))
    E._attach(project, oid, {"start": E._turn(s, angle, centre_t), "end": E._turn(e, angle, centre_t), "end_hosts": {}})
    follow_links(project, oid, {"start": s, "end": e})


# ------------------------------------------------------------------------------------------------------------------------- drawing
def _footprint(sp: Mapping[str, Any], f: float) -> list[list[float]]:
    return E._rect(sp["start"], sp["angle"], 0.0, sp["length"], sp["frame_depth"] / 2, f)


def _door_cells(sp: Mapping[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    return [(c, cs) for c in cells(sp) if (cs := cell_spec(sp, c))["type"] == "Door"]


def plan_symbols(project: Any, oid: str, f: float, cut_z: float, show_opening: bool = True) -> list[dict[str, Any]]:
    """The plan notation of every door field the cut plane passes through: its leaves, and for a swing leaf the arc and open leaf, drawn by the code that draws a
    door in a wall (so the swing is the same in plan, elevation and 3D). A sliding door shows both leaves, the open position dashed, a track and an arrow."""
    def at(u, d):
        z_local = cut_z - base_z(project, meta)
        d_eff = d + z_local * math.tan(math.radians(sp.get("lean", 0.0)))
        return [(sp["start"][0] + math.cos(sp["angle"]) * u - math.sin(sp["angle"]) * d_eff) * f,
                (sp["start"][1] + math.sin(sp["angle"]) * u + math.cos(sp["angle"]) * d_eff) * f]

    meta = E.meta_of(project, oid)
    sp = spec(meta)
    z0 = base_z(project, meta)
    fr = {"start": sp["start"], "angle": sp["angle"], "thickness": sp["frame_depth"], "base": z0}
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
    corners = []
    for end in ("start", "end"):                                                  # the corner at each joined end, for the on-canvas corner control
        j = _junction(project, oid, end)
        if j is not None:
            corners.append({"endpoint": end, "other": j["other"], "point": [j["P"][0] * f, j["P"][1] * f], "type": corner_type(j), "angle": round(j["angle"], 3)})
    if corners:
        item["cw_corners"] = corners
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
    frame = [q for q in ms if q["kind"] == "frame" and q["role"] not in NON_GRID_ROLES]
    by_role: dict[str, dict[str, float]] = {}
    for q in ms:                                          # was: for q in frame
        if q["kind"] != "frame":
            continue
        r = by_role.setdefault(q["role"], {"count": 0, "length": 0.0})
        r["count"] += 1
        r["length"] += q["length"]
    total = sum(q["length"] for q in frame)
    panel = {k: sum(q["area"] for q in ms if q["kind"] == k) for k in ("glass", "panel", "door")}
    doors = [c for c, cs in _door_cells(sp)]
    leaves = sum(1 for q in ms if q["kind"] == "door")
    fins = by_role.get("fin",   {"count": 0, "length": 0.0})
    shelves = by_role.get("shelf", {"count": 0, "length": 0.0})
    return {"mullion_lines": len(axes(sp, "v")), "transom_lines": len(axes(sp, "h")), "frame_length": total, "frame_members": len(frame), "by_role": by_role, "frame_volume": sum(q["area"] * q["length"] for q in frame),
            "glazing_area": panel["glass"], "panel_area": panel["panel"], "door_leaf_area": panel["door"], "panel_count": len(cells(sp)), "door_count": len(doors), "door_leaves": leaves,
            "gross_area": sp["length"] * sp["height"], "mass_per_m": prof["mass_kg_per_m"] if prof and prof.get("mass_kg_per_m") else None,
            "frame_mass": total * prof["mass_kg_per_m"] if prof and prof.get("mass_kg_per_m") else None, "profile": provenance(pid),
            "fin_count": fins["count"],   "fin_length": fins["length"],   "fin_depth": sp["fin"]["depth"],
            "shelf_count": shelves["count"], "shelf_length": shelves["length"], "shelf_depth": sp["shelf"]["depth"],
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
                  {"key": "mullion_depth", "label": "Mullion depth", "type": "number", "unit": "mm", "value": mm(sp["mullion_depth"])},
                  {"key": "transom_depth", "label": "Transom depth", "type": "number", "unit": "mm", "value": mm(sp["transom_depth"])},
                  {"key": "corner_radius", "label": "Corner radius of the section", "type": "number", "unit": "mm", "value": mm(sp["corner_radius"])},
                  {"key": "join", "label": "Join condition", "type": "choice", "value": sp["join"], "choices": opts(JOINS, JOIN_LABELS)},
                  {"label": "Profile source", "type": "readonly", "value": provenance(pid)}]
    geometry_rows = [{"key": "lean", "label": "Wall lean", "type": "number", "unit": "°", "value": sp["lean"]}]
    accessory_rows = []
    for kind, label in (("fin", "Vertical fins"), ("shelf", "Horizontal shelves")):
        settings = sp[kind]
        accessory_rows += [{"key": f"{kind}_{field}", "label": f"{label}: {field}", "type": "number", "unit": "mm", "value": mm(settings[field])}
                           for field in ("depth", "width", "offset")]
        accessory_rows.append({"key": f"{kind}_side", "label": f"{label}: side", "type": "choice", "value": settings["side"],
                               "choices": opts(("left", "right", "both") if kind == "fin" else ("up", "down", "both"))})
    band_rows = [{"key": "band_rows", "label": "Horizontal bands (JSON; positions in mm)", "type": "multiline", "value": json.dumps(E.m(meta, "band_rows") or [])},
                 {"key": "band_cols", "label": "Vertical bands (JSON; positions in mm)", "type": "multiline", "value": json.dumps(E.m(meta, "band_cols") or [])}]
    panel_rows = [{"key": "panel_type", "label": "Panel type", "type": "choice", "value": sp["panel_type"], "choices": E._choices(WALL_PANEL_TYPES)},
                  {"key": "panel_material", "label": "Panel material", "type": "choice", "value": sp["panel_material"], "choices": E._choices(PANEL_MATERIALS)},
                  {"key": "panel_thickness", "label": "Panel thickness", "type": "number", "unit": "mm", "value": mm(sp["panel_thickness"])},
                  {"key": "panel_offset", "label": "Panel offset (from the frame's centre)", "type": "number", "unit": "mm", "value": mm(sp["panel_offset"])},
                  {"key": "door_type", "label": "Door type (new door fields)", "type": "choice", "value": sp["door_type"], "choices": E._choices(DOOR_TYPES)},
                  {"key": "door_hinge", "label": "Door hinge (new door fields)", "type": "choice", "value": sp["door_hinge"], "choices": [{"value": "start", "label": "Start side"}, {"value": "end", "label": "End side"}]},
                  {"key": "door_swing", "label": "Door swing (new door fields)", "type": "choice", "value": "left" if sp["door_swing"] > 0 else "right", "choices": E._choices(("left", "right"))},
                  {"key": "louvre_pitch",     "label": "Louvre pitch",         "type": "number", "unit": "mm", "value": mm(sp["louvre"]["pitch"])},
                  {"key": "louvre_tilt",      "label": "Louvre tilt",          "type": "number", "unit": "°",  "value": sp["louvre"]["tilt"]},
                  {"key": "louvre_depth",     "label": "Louvre slat depth",    "type": "number", "unit": "mm", "value": mm(sp["louvre"]["depth"])},
                  {"key": "louvre_thickness", "label": "Louvre slat thickness","type": "number", "unit": "mm", "value": mm(sp["louvre"]["thickness"])}]
    if sp["bands"]["rows"] or sp["bands"]["cols"]:
        panel_rows.append({"label": "Bands", "type": "readonly", "value": " · ".join(f"{b.get('type', '…')} {b['from']*1000:,.0f}–{b['to']*1000:,.0f} mm" for b in (sp["bands"]["rows"] or sp["bands"]["cols"]))})

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
    out["groups"] = [{"title": "Division", "rows": rows}, {"title": "Frame", "rows": frame_rows},
                     {"title": "Geometry", "rows": geometry_rows}, {"title": "Fins and shelves", "rows": accessory_rows},
                     {"title": "Panel bands", "rows": band_rows, "collapsed": True}, {"title": "Panels", "rows": panel_rows},
                     {"title": "Members (remove or add)", "rows": line_rows}, {"title": "Fields", "rows": cell_rows},
                     {"title": "Quantities", "rows": [{"label": "Frame members", "type": "readonly", "value": f"{q['frame_members']} ({q['frame_length']:.2f} m)"},
                                                      {"label": "Frame mass", "type": "readonly", "value": mass},
                                                      {"label": "Fins", "type": "readonly", "value": f"{q['fin_count']} ({q['fin_depth']:.2f} m depth)"},
                                                      {"label": "Shelves", "type": "readonly", "value": f"{q['shelf_count']} ({q['shelf_depth']:.2f} m depth)"},
                                                      {"label": "Glazing area", "type": "readonly", "value": f"{q['glazing_area']:.2f} m²"},
                                                      {"label": "Opaque panel area", "type": "readonly", "value": f"{q['panel_area']:.2f} m²"},
                                                      {"label": "Doors", "type": "readonly", "value": f"{q['door_count']} ({q['door_leaves']} leaves)"}, volume]},
                     {"title": "Identity", "rows": [{"key": "name", "label": "Name", "type": "text", "value": project.model.get(oid)["name"]}]}]
    out["groups"].insert(1, {"title": "Ends and attachment", "rows": _connection_rows(project, oid, meta)})
    return out


def _connection_rows(project: Any, oid: str, meta: Mapping[str, Any]) -> list[dict[str, Any]]:
    """What the ends meet (a curtain wall with its corner, a wall that hosts the end, or nothing) and what the base and top are attached to."""
    from . import attachments as AT
    rows: list[dict[str, Any]] = []
    hosts = E.m(meta, "end_hosts") or {}
    for end in ("start", "end"):
        j = _junction(project, oid, end)
        label = f"{end.title()}"
        if j is not None:
            kind = corner_type(j)
            other = project.model.get(j["other"])["name"] or "Curtain Wall"
            rows.append({"label": f"{label} meets", "type": "link", "value": f"{other} at {j['angle']:.0f}°", "target": {"select": {"model": j["other"]}}})
            if kind == "inline":
                rows.append({"label": f"{label} corner", "type": "readonly", "value": "In line: one shared member"})
            else:
                rows.append({"key": f"corner:{end}:type", "label": f"{label} corner", "type": "choice", "value": str(j["cfg"].get("type", "auto")),
                             "choices": [{"value": "auto", "label": f"Automatic ({CORNER_TYPES[kind]})"}] + [{"value": k, "label": v} for k, v in CORNER_TYPES.items()]})
                rows.append({"label": f"{label} corner post", "type": "link", "value": f"{CORNER_TYPES[kind]}: edit its sizes",
                             "target": {"select": {"model": oid, "part": f"corner:{end}"}}})
        elif hosts.get(end) and E.alive(project, hosts[end].get("wall", "")):
            wid = hosts[end]["wall"]
            rows.append({"label": f"{label} on wall", "type": "link", "value": project.model.get(wid)["name"] or "Wall", "target": {"select": {"model": wid}}})
        else:
            rows.append({"label": f"{label}", "type": "readonly", "value": "free"})
    base = AT.base_attachment(meta)
    floor_choices = [{"value": "none", "label": "Its level (not on a floor)"}, {"value": "auto", "label": "The floor under it"}]
    rows.append({"key": "base_host", "label": "Base stands on", "type": "choice", "value": base["floor"] if base else "none",
                 "choices": floor_choices + ([{"value": base["floor"], "label": project.model.get(base["floor"])["name"] or "Floor"}] if base and E.alive(project, base["floor"]) else [])})
    rows.append({"label": "Top", "type": "readonly", "value": AT.describe(project, meta)})
    return rows


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
        if cs["type"] == "Louvre" and (c["z1"] - c["z0"]) < 2 * sp["louvre"]["pitch"]:
            out.append(issue("warning", oid,
                f"{label}: louvre field {c['col']+1}.{c['row']+1} is shorter than two slat pitches; it will read as empty", "geometry"))
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
    return {"mullions": [round(x * 1000, 3) for x in axes(sp, "v")],
            "transoms": [round(x * 1000, 3) for x in axes(sp, "h")], "panels": len(cs),
            "lean_deg": setup.get("lean", 0.0),
            "fin_extents_mm":  [round(f * 1000, 3) for f in (sp["fin"]["depth"], sp["fin"]["width"])],
            "shelf_extents_mm":[round(f * 1000, 3) for f in (sp["shelf"]["depth"], sp["shelf"]["width"])],
            "bands_active":    bool(sp["bands"]["rows"] or sp["bands"]["cols"]),
            "louvre_count":    sum(1 for c in cells(sp) if cell_spec(sp, c)["type"] == "Louvre"),
            "frame_width_mm": round(sp["frame_width"] * 1000, 3),
            "frame_depth_mm": round(sp["frame_depth"] * 1000, 3),
            "profile": provenance(setup["frame_profile"]),
            "bays_v_mm": [round(b * 1000, 3) for b in bays(sp["length"] - sp["frame_width"], sp["v"]["mode"], sp["v"]["value"], sp["v"]["align"], sp["frame_width"] + MIN_FIELD)],
            "bays_h_mm": [round(b * 1000, 3) for b in bays(sp["height"] - sp["frame_width"], sp["h"]["mode"], sp["h"]["value"], sp["h"]["align"], sp["frame_width"] + MIN_FIELD)], **{k: round(v * 1000, 3) for k, v in q.items()}}


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


# ------------------------------------------------------------------------------------------------------------------- connections
# A curtain wall knows what its two ends meet.
#
# * Another curtain wall. Ends that are joined (drawn as one connected run, or one end placed on another's) keep a stable link to each other
#   (``links``) and move together. The junction gets a **corner**: one of four corner posts, chosen per junction and kept until it is changed or can
#   no longer be built (see ``CORNER_TYPES``). The post belongs to one of the two walls (the *owner*: the one with the smaller id), so it is never made
#   twice; both walls give up their own perimeter member at that end and stop where the post begins. Two walls that run on in a straight line share
#   the owner's perimeter member instead.
# * A wall. An end placed on a wall (in it, or on its face) is *hosted* by it (``end_hosts``): the curtain wall's end stays on that wall's face
#   toward it, along its own line, when either of them moves, turns or changes thickness.
CORNER_TYPES = {"L": "L-Corner", "quad": "Quad Corner", "trapezoid": "Trapezoid Corner", "V": "V-Corner"}
# The angle between the two walls (degrees, measured inside the corner) each corner can be built for. A corner outside its range is refused with the
# reason; "auto" picks the first of quad, trapezoid, V that fits.
CORNER_RANGES = {"L": (45.0, 135.0), "quad": (30.0, 150.0), "trapezoid": (60.0, 175.0), "V": (15.0, 165.0)}
INLINE = 175.0                       # degrees: from here on two walls run on in a line and share one member
JOIN = 0.002                         # m: ends this close are one junction


def links_of(meta: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    return {k: dict(v) for k, v in (E.m(meta, "links") or {}).items() if isinstance(v, Mapping) and v.get("id")}


def _end_point(meta: Mapping[str, Any], end: str) -> tuple[float, float]:
    q = E.m(meta, "start" if end == "start" else "end")
    return float(q[0]), float(q[1])


def curtainwalls(project: Any) -> list[str]:
    return list(project.model.query_ids(metadata={f"{E.NS}:category": "curtainwall"}, limit=10_000))


def link(project: Any, a: str, a_end: str, b: str, b_end: str) -> None:
    """Join the ``a_end`` of curtain wall ``a`` to the ``b_end`` of ``b`` (both ways)."""
    for x, xe, y, ye in ((a, a_end, b, b_end), (b, b_end, a, a_end)):
        meta = E.meta_of(project, x)
        ls = links_of(meta)
        ls[xe] = {"id": y, "end": ye}
        E._attach(project, x, {"links": ls})


def unlink(project: Any, oid: str, end: str) -> None:
    meta = E.meta_of(project, oid)
    ls = links_of(meta)
    other = ls.pop(end, None)
    E._attach(project, oid, {"links": ls})
    if other and E.alive(project, other["id"]):
        om = E.meta_of(project, other["id"])
        ols = links_of(om)
        if ols.get(other["end"], {}).get("id") == oid:
            ols.pop(other["end"])
            E._attach(project, other["id"], {"links": ols})


def detect(project: Any, oid: str) -> None:
    """Link each free end of a curtain wall to another curtain wall's free end at the same place on the same level, and host an end that lies on a
    wall by that wall. Run when a curtain wall is made or one of its ends is placed again."""
    meta = E.meta_of(project, oid)
    ls = links_of(meta)
    level = E.m(meta, "level")
    for end in ("start", "end"):
        if end in ls:
            continue
        p = _end_point(meta, end)
        for other in curtainwalls(project):
            if other == oid:
                continue
            om = E.meta_of(project, other)
            if E.m(om, "level") != level:
                continue
            ols = links_of(om)
            hit = next((oe for oe in ("start", "end") if oe not in ols and math.dist(p, _end_point(om, oe)) <= JOIN), None)
            if hit and _angle_between(meta, end, om, hit) < min(lo for lo, _ in CORNER_RANGES.values()):
                hit = None                                                            # lying along each other, not meeting at a corner
            if hit:
                link(project, oid, end, other, hit)
                ls = links_of(E.meta_of(project, oid))
                break
        else:
            wall = _wall_at(project, level, p)
            if wall:
                hosts = dict(E.m(E.meta_of(project, oid), "end_hosts") or {})
                hosts[end] = {"wall": wall}
                E._attach(project, oid, {"end_hosts": hosts})


def _angle_between(ma: Mapping[str, Any], ea: str, mb: Mapping[str, Any], eb: str) -> float:
    """The angle (degrees) inside the corner two curtain walls would make if joined at these ends."""
    P = _end_point(ma, ea)
    fa, fb = _end_point(ma, "end" if ea == "start" else "start"), _end_point(mb, "end" if eb == "start" else "start")
    da, db = (fa[0] - P[0], fa[1] - P[1]), (fb[0] - P[0], fb[1] - P[1])
    na, nb = math.hypot(*da), math.hypot(*db)
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return math.degrees(math.acos(max(-1.0, min(1.0, (da[0] * db[0] + da[1] * db[1]) / (na * nb)))))


def _wall_at(project: Any, level: Any, p: tuple[float, float]) -> str | None:
    """A wall of the same level that a point lies in or on (its footprint grown by the join tolerance)."""
    for wid in project.model.query_ids(metadata={f"{E.NS}:category": "wall"}, limit=10_000):
        wm = E.meta_of(project, wid)
        if E.m(wm, "level") != level:
            continue
        s, e = tuple(E.m(wm, "start")), tuple(E.m(wm, "end"))
        L = math.dist(s, e)
        if L < 1e-9:
            continue
        u = ((e[0] - s[0]) / L, (e[1] - s[1]) / L)
        along = (p[0] - s[0]) * u[0] + (p[1] - s[1]) * u[1]
        across = abs(-(p[0] - s[0]) * u[1] + (p[1] - s[1]) * u[0])
        if -JOIN <= along <= L + JOIN and across <= float(E.m(wm, "thickness")) / 2 + JOIN:
            return wid
    return None


def _follow_host(project: Any, oid: str) -> bool:
    """Put each hosted end back on its wall's face (the face toward the curtain wall), along the curtain wall's own line. A wall that is gone, or that
    the line no longer meets, lets the end go (it stays where it is). Returns whether an end moved."""
    meta = E.meta_of(project, oid)
    hosts = dict(E.m(meta, "end_hosts") or {})
    if not hosts:
        return False
    moved = False
    for end, h in list(hosts.items()):
        wid = h.get("wall")
        if not wid or not E.alive(project, wid) or E.category(project, wid) != "wall":
            hosts.pop(end)
            moved = True
            continue
        wm = E.meta_of(project, wid)
        a, b = _end_point(meta, "start"), _end_point(meta, "end")
        here, there = (a, b) if end == "start" else (b, a)
        d = (here[0] - there[0], here[1] - there[1])
        Ld = math.hypot(*d)
        s, e = tuple(E.m(wm, "start")), tuple(E.m(wm, "end"))
        Lw = math.dist(s, e)
        if Ld < 1e-9 or Lw < 1e-9:
            continue
        wu = ((e[0] - s[0]) / Lw, (e[1] - s[1]) / Lw)
        wn = (-wu[1], wu[0])
        side = 1.0 if (there[0] - s[0]) * wn[0] + (there[1] - s[1]) * wn[1] > 0 else -1.0       # the wall's face toward the rest of the curtain wall
        face = (s[0] + wn[0] * side * float(E.m(wm, "thickness")) / 2, s[1] + wn[1] * side * float(E.m(wm, "thickness")) / 2)
        hit = G.intersect_lines(there, d, face, wu)
        if hit is None:
            hosts.pop(end)
            moved = True
            continue
        q = (there[0] + d[0] * hit[0], there[1] + d[1] * hit[0])
        if math.dist(q, here) > 1e-9:
            E._attach(project, oid, {end: [q[0], q[1]]})
            meta = E.meta_of(project, oid)
            moved = True
    E._attach(project, oid, {"end_hosts": hosts})
    return moved


def _junction(project: Any, oid: str, end: str) -> dict[str, Any] | None:
    """What an end of a curtain wall meets when it is linked to another curtain wall: the shared point, the directions of both walls away from it,
    the angle inside the corner, the owner and the corner settings."""
    meta = E.meta_of(project, oid)
    lk = links_of(meta).get(end)
    if not lk or not E.alive(project, lk["id"]) or E.category(project, lk["id"]) != "curtainwall":
        return None
    other, oend = lk["id"], lk["end"]
    om = E.meta_of(project, other)
    P = _end_point(meta, end)
    if math.dist(P, _end_point(om, oend)) > JOIN:
        return None
    far = _end_point(meta, "end" if end == "start" else "start")
    ofar = _end_point(om, "end" if oend == "start" else "start")
    dA = G.unit((far[0] - P[0], far[1] - P[1]))
    dB = G.unit((ofar[0] - P[0], ofar[1] - P[1]))
    angle = math.degrees(math.acos(max(-1.0, min(1.0, dA[0] * dB[0] + dA[1] * dB[1]))))
    owner = oid if oid < other else other
    owner_end = end if owner == oid else oend
    cfg = dict((E.m(E.meta_of(project, owner), "corners") or {}).get(owner_end) or {})
    return {"me": oid, "end": end, "other": other, "other_end": oend, "P": P, "dA": dA, "dB": dB, "angle": angle, "owner": owner, "owner_end": owner_end,
            "cfg": cfg, "depth": spec(meta)["mullion_depth"], "other_depth": spec(om)["mullion_depth"], "width": spec(meta)["frame_width"]}


def corner_type(j: Mapping[str, Any]) -> str:
    """The corner a junction has: the one chosen for it, or (``auto``) the first of quad, trapezoid and V whose range takes the angle; ``none`` when
    the walls lie along each other more closely than any corner can be built for."""
    chosen = str(j["cfg"].get("type", "auto"))
    if j["angle"] >= INLINE:
        return "inline"
    if chosen != "auto":
        return chosen
    for kind in ("quad", "trapezoid", "V"):
        lo, hi = CORNER_RANGES[kind]
        if lo <= j["angle"] <= hi:
            return kind
    return "none"


def corner_post(j: Mapping[str, Any]) -> list[tuple[float, float]] | None:
    """The plan outline of a junction's corner post (world, metres), by its type. Raises ``BadRequest`` (with the angle and the range) for a type that
    cannot be built at this angle or with these sizes; ``None`` for walls in line (they share a member, no post)."""
    kind = corner_type(j)
    if kind in ("inline", "none"):
        return None
    if kind not in CORNER_TYPES:
        raise BadRequest(f"A curtain-wall corner is one of: {', '.join(CORNER_TYPES.values())}")
    lo, hi = CORNER_RANGES[kind]
    if not lo - 1e-9 <= j["angle"] <= hi + 1e-9:
        raise BadRequest(f"A {CORNER_TYPES[kind]} is made for walls meeting at {lo:g}° to {hi:g}°; these meet at {j['angle']:.1f}°",
                         hint="Choose another corner type for this junction")
    P, dA, dB = j["P"], j["dA"], j["dB"]
    cfg = j["cfg"]
    DA, DB = j["depth"], j["other_depth"]
    nA, nB = G.left(dA), G.left(dB)
    bis = G.unit((dA[0] + dB[0], dA[1] + dB[1]))                                         # into the corner (the inside of the angle)
    if nA[0] * bis[0] + nA[1] * bis[1] > 0:                                              # each normal turned to the outside of the corner
        nA = (-nA[0], -nA[1])
    if nB[0] * bis[0] + nB[1] * bis[1] > 0:
        nB = (-nB[0], -nB[1])

    def at(p: tuple[float, float], d: tuple[float, float], k: float) -> tuple[float, float]:
        return (p[0] + d[0] * k, p[1] + d[1] * k)

    def cross(p: tuple[float, float], d: tuple[float, float], q: tuple[float, float], e: tuple[float, float]) -> tuple[float, float]:
        hit = G.intersect_lines(p, d, q, e)
        if hit is None:
            raise BadRequest("The two walls of this corner are parallel")
        return at(p, d, hit[0])
    if kind == "quad":                                                                   # four sides, each parallel to one wall: covers both walls' frames
        qa, qb = float(cfg.get("depth_a", DA)), float(cfg.get("depth_b", DB))
        lines_ = [(at(P, nA, qa / 2), dA), (at(P, nB, qb / 2), dB), (at(P, nA, -qa / 2), dA), (at(P, nB, -qb / 2), dB)]
        poly = [cross(*lines_[i], *lines_[(i + 1) % 4]) for i in range(4)]
    elif kind == "trapezoid":                                                            # sides square to each wall, at ``leg`` from the corner
        leg = float(cfg.get("leg", max(DA, DB) / 2))
        a_out, a_in = at(at(P, dA, leg), nA, DA / 2), at(at(P, dA, leg), nA, -DA / 2)
        b_out, b_in = at(at(P, dB, leg), nB, DB / 2), at(at(P, dB, leg), nB, -DB / 2)
        poly = [a_out, a_in, b_in, b_out]
    elif kind == "L":                                                                    # an angle on the outside faces: two legs of ``thickness``
        th = float(cfg.get("thickness", j["width"]))
        reach = th / math.tan(math.radians(j["angle"]) / 2)                              # how far along a leg its inner corner lies from the outer one
        la, lb = float(cfg.get("leg_a", max(DA, DB, reach + j["width"]))), float(cfg.get("leg_b", max(DA, DB, reach + j["width"])))
        oa, ob = (at(P, nA, DA / 2), dA), (at(P, nB, DB / 2), dB)
        ia, ib = (at(P, nA, DA / 2 - th), dA), (at(P, nB, DB / 2 - th), dB)
        corner_o = cross(*oa, *ob)
        corner_i = cross(*ia, *ib)
        end_a_o = at(oa[0], dA, (corner_o[0] - oa[0][0]) * dA[0] + (corner_o[1] - oa[0][1]) * dA[1] + la)
        end_b_o = at(ob[0], dB, (corner_o[0] - ob[0][0]) * dB[0] + (corner_o[1] - ob[0][1]) * dB[1] + lb)
        poly = [corner_o, end_a_o, at(end_a_o, nA, -th), corner_i, at(end_b_o, nB, -th), end_b_o]
    else:                                                                                # V: two legs on the walls' centre lines, mitred in the corner
        th = float(cfg.get("thickness", j["width"]))
        reach = th / 2 / math.tan(math.radians(j["angle"]) / 2)
        la, lb = float(cfg.get("leg_a", max(DA, DB, reach + j["width"]))), float(cfg.get("leg_b", max(DA, DB, reach + j["width"])))
        ao, ai = (at(P, nA, th / 2), dA), (at(P, nA, -th / 2), dA)
        bo, bi = (at(P, nB, th / 2), dB), (at(P, nB, -th / 2), dB)
        tip_o, tip_i = cross(*ao, *bo), cross(*ai, *bi)
        poly = [tip_o, at(ao[0], dA, la), at(ai[0], dA, la), tip_i, at(bi[0], dB, lb), at(bo[0], dB, lb)]
    clean: list[tuple[float, float]] = []
    for q in poly:
        if not clean or math.dist(clean[-1], q) > 1e-9:
            clean.append(q)
    if len(clean) < 3 or not G.is_simple_polygon(clean) or abs(G.signed_area(clean)) < 1e-6:
        raise BadRequest(f"These sizes do not make a {CORNER_TYPES[kind]} at {j['angle']:.1f}°", hint="Change the corner's legs or depths, or choose another corner")
    return clean


def leg_default(j: Mapping[str, Any], share: float) -> float:
    """The leg an L (``share`` 1) or V (``share`` 0.5) corner starts with: a frame depth, and always long enough to reach past its inner corner."""
    th = float(j["cfg"].get("thickness", j["width"]))
    return max(j["depth"], j["other_depth"], share * th / math.tan(math.radians(j["angle"]) / 2) + j["width"])


def resolve_ends(project: Any, oid: str) -> dict[str, Any]:
    """Work out, from the junctions as they are now, how this curtain wall ends at each end (``end_cond``: whether its perimeter member is given up
    and how far its first field stands back from the junction) and which corner posts it carries (``corner_posts``, in its own u, d frame). Stored on
    the wall and returned."""
    meta = E.meta_of(project, oid)
    sp = spec(meta)
    L = sp["length"]
    c, s = math.cos(sp["angle"]), math.sin(sp["angle"])
    sx, sy = sp["start"]
    to_local = lambda q: (round((q[0] - sx) * c + (q[1] - sy) * s, 9), round(-(q[0] - sx) * s + (q[1] - sy) * c, 9))     # noqa: E731
    ends: dict[str, Any] = {}
    posts: list[dict[str, Any]] = []
    for end in ("start", "end"):
        j = _junction(project, oid, end)
        if j is None:
            continue
        kind = corner_type(j)
        if kind == "none":
            continue                                                                      # no corner can be built: each keeps its own member (the check says so)
        if kind == "inline":
            if j["owner"] != oid:                                                        # walls in line: the owner's member serves both
                ends[end] = {"suppress": True, "setback": 0.0, "with": j["other"], "type": "inline"}
            continue
        poly = corner_post(j)
        if poly is None:
            continue
        P, dA = j["P"], j["dA"]
        setback = max(0.0, max((q[0] - P[0]) * dA[0] + (q[1] - P[1]) * dA[1] for q in poly))
        if setback > L / 2:
            raise BadRequest(f"The {CORNER_TYPES[kind]} reaches {setback * 1000:.0f} mm along a curtain wall that is only {L * 1000:.0f} mm long")
        ends[end] = {"suppress": True, "setback": setback, "with": j["other"], "type": kind}
        if j["owner"] == oid:
            posts.append({"end": end, "type": kind, "poly": [list(to_local(q)) for q in poly]})
    E._attach(project, oid, {"end_cond": ends, "corner_posts": posts})
    return {"end_cond": ends, "corner_posts": posts}


def set_corner(project: Any, oid: str, end: str, values: Mapping[str, Any]) -> dict[str, Any]:
    """Choose the corner of the junction at ``end`` of curtain wall ``oid`` (``type``: L, quad, trapezoid, V or auto) and its sizes (mm: ``depth_a``,
    ``depth_b`` for a quad corner, ``leg`` for a trapezoid, ``leg_a``, ``leg_b`` and ``thickness`` for an L or V). Stored once, on the junction's owner,
    and checked against the angle before anything is kept."""
    if end not in ("start", "end"):
        raise BadRequest("end is start or end")
    j = _junction(project, oid, end)
    if j is None:
        raise BadRequest("This end of the curtain wall does not meet another curtain wall")
    om = E.meta_of(project, j["owner"])
    corners = {k: dict(v) for k, v in (E.m(om, "corners") or {}).items()}
    cfg = dict(corners.get(j["owner_end"]) or {})
    swap = j["owner"] != oid                                                             # "a" is always the wall the edit was made on
    for key, value in values.items():
        if key == "type":
            cfg["type"] = value if value == "auto" else E._choice(value, tuple(CORNER_TYPES), "corner type")
        elif key in ("depth_a", "depth_b", "leg", "leg_a", "leg_b", "thickness"):
            stored = {"depth_a": "depth_b", "depth_b": "depth_a", "leg_a": "leg_b", "leg_b": "leg_a"}.get(key, key) if swap else key
            cfg[stored] = number(value, key.replace("_", " ") + " (mm)", 10, 2000) / 1000.0
        else:
            raise BadRequest(f"'{key}' is not a corner setting")
    corners[j["owner_end"]] = cfg
    E._attach(project, j["owner"], {"corners": corners})
    corner_post(j)                                                                       # refuse a corner that cannot be built at this angle
    for x in (oid, j["other"]):
        resolve_ends(project, x)
        build(project, x)
    return {"type": corner_type(j), "owner": j["owner"]}


def reconcile(project: Any) -> list[str]:
    """After a command: hosted ends follow their walls, broken links are let go, and every curtain wall whose ends or corners came out different is
    rebuilt. Returns what was rebuilt."""
    ids = curtainwalls(project)
    if not ids:
        return []
    for oid in ids:
        meta = E.meta_of(project, oid)
        ls = links_of(meta)
        for end, lk in list(ls.items()):
            if not E.alive(project, lk["id"]):
                ls.pop(end)
        if ls != links_of(meta):
            E._attach(project, oid, {"links": ls})
        _follow_host(project, oid)
    rebuilt = []
    for oid in ids:
        meta = E.meta_of(project, oid)
        before = (E.m(meta, "end_cond") or {}, E.m(meta, "corner_posts") or [])
        sig = _signature(meta)
        after = resolve_ends(project, oid)
        if (after["end_cond"], after["corner_posts"]) != before or sig != E.m(meta, "built_sig"):
            build(project, oid)
            rebuilt.append(oid)
    return rebuilt


def _signature(meta):
    return [E.m(meta, "start"), E.m(meta, "end"), E.m(meta, "height"),
            E.m(meta, "base_offset"), E.m(meta, "top_profile"),
            E.m(meta, "frame_depth"), E.m(meta, "frame_width"),
            E.m(meta, "mullion_depth"), E.m(meta, "transom_depth"),
            E.m(meta, "lean"),
            E.m(meta, "fin_depth"), E.m(meta, "fin_width"), E.m(meta, "fin_offset"), E.m(meta, "fin_side"),
            E.m(meta, "shelf_depth"), E.m(meta, "shelf_width"), E.m(meta, "shelf_offset"), E.m(meta, "shelf_side"),
            E.m(meta, "band_rows"), E.m(meta, "band_cols"),
            E.m(meta, "louvre_pitch"), E.m(meta, "louvre_tilt"),
            E.m(meta, "louvre_depth"), E.m(meta, "louvre_thickness")]


def follow_links(project: Any, oid: str, before: Mapping[str, Sequence[float]]) -> None:
    """After an end of a curtain wall moved (from ``before``), the linked end of its neighbour moves with it, so a connected run stays connected."""
    meta = E.meta_of(project, oid)
    for end, lk in links_of(meta).items():
        if not E.alive(project, lk["id"]):
            continue
        now = _end_point(meta, end)
        if math.dist(now, tuple(before[end])) > 1e-12:
            E._attach(project, lk["id"], {lk["end"]: [now[0], now[1]]})


# ------------------------------------------------------------------------------------------------------------------- components
def component_list(project: Any, oid: str) -> list[dict[str, Any]]:
    """Every selectable part of a curtain wall: its members (perimeter, mullions, transoms, sloped top), its fields and its corner posts, with a label
    and where it is in the wall's frame (u along from the start, z up from the base)."""
    meta = E.meta_of(project, oid)
    sp = spec(meta)
    out = []
    seen = set()
    for q in members(project, meta):
        if q["id"] in seen:
            continue
        seen.add(q["id"])
        verts = q["mesh"][0]
        box = [min(v[0] for v in verts), max(v[0] for v in verts), min(v[2] for v in verts), max(v[2] for v in verts)]
        out.append({"id": q["id"], "kind": q["kind"], "role": q["role"], "box": box, "label": _part_label(sp, q["id"])})
    return out


def _part_label(sp: Mapping[str, Any], pid: str) -> str:
    if pid.startswith("cell:"):
        c, r = (int(x) for x in pid[5:].split(","))
        return f"Panel {c + 1}.{r + 1}"
    if pid == "top":
        return "Top member (follows the roof)"
    if pid.startswith("corner:"):
        return f"Corner post at the {pid[7:]}"
    d, _, k = pid.partition(":")
    noun = "Mullion" if d == "v" else "Transom"
    if k in ("start", "end", "bottom", "top"):
        return f"Perimeter member ({k})"
    return f"{noun} {int(k) + 1}"


def pick(project: Any, oid: str, point: Sequence[float], z: float | None = None) -> dict[str, Any]:
    """The part of a curtain wall at a point (world metres; ``z`` absolute, or the point's own third coordinate). In plan (no height) a field is chosen
    at the plan's cut height. The smallest part whose box holds the point wins, so a mullion is chosen over the field behind it."""
    meta = E.meta_of(project, oid)
    sp = spec(meta)
    c, s = math.cos(sp["angle"]), math.sin(sp["angle"])
    x, y = float(point[0]) - sp["start"][0], float(point[1]) - sp["start"][1]
    u = x * c + y * s
    zz = (float(point[2]) if len(point) > 2 and z is None else float(z if z is not None else 0.0)) - base_z(project, meta)
    best = None
    for part in component_list(project, oid):
        u0, u1, z0, z1 = part["box"]
        if u0 - 1e-6 <= u <= u1 + 1e-6 and z0 - 1e-6 <= zz <= z1 + 1e-6:
            size = (u1 - u0) * (z1 - z0)
            if best is None or size < best[0]:
                best = (size, part)
    if best is None:
        raise NotFound("Nothing of the curtain wall is at that point")
    return {"id": oid, "part": best[1]["id"], "label": best[1]["label"], "kind": best[1]["kind"]}


def component_properties(project: Any, oid: str, part: str) -> dict[str, Any]:
    """The Properties panel of one part of a curtain wall: a field (its type, material, thickness, offset; a door's type, hinge, swing, opening angle and
    whether it is shown open), a member (where it is, its size, and removing or putting it back), or a corner post (its corner type and sizes)."""
    meta = E.meta_of(project, oid)
    sp = spec(meta)
    name = project.model.get(oid)["name"] or "Curtain Wall"
    mm = lambda v: round(v * 1000, 6)                                                       # noqa: E731
    back = {"label": "Curtain wall", "type": "link", "value": name, "target": {"select": {"model": oid}}}
    head = {"ref": {"model": oid, "part": part}, "category": "curtainwall", "kind": "model"}
    if part.startswith("cell:"):
        key = part[5:]
        cell = next((c for c in cells(sp) if c["key"] == key), None)
        if cell is None:
            raise NotFound(f"This curtain wall has no panel {key}")
        cs = cell_spec(sp, cell)
        pre = f"panel:{cell['col']}:{cell['row']}:"
        rows = [{"key": pre + "type", "label": "Type", "type": "choice", "value": cs["type"], "choices": E._choices(PANEL_TYPES)}]
        if cs["type"] == "Door":
            rows += [{"key": pre + "door_type", "label": "Door type", "type": "choice", "value": cs["door_type"], "choices": E._choices(DOOR_TYPES)},
                     {"key": pre + "hinge", "label": "Hinge", "type": "choice", "value": cs["hinge"], "choices": [{"value": "start", "label": "Start side"}, {"value": "end", "label": "End side"}]},
                     {"key": pre + "swing", "label": "Swing", "type": "choice", "value": "left" if cs["swing"] > 0 else "right", "choices": E._choices(("left", "right"))},
                     {"key": pre + "opening_angle", "label": "Opening angle", "type": "number", "unit": "°", "value": cs["opening_angle"]},
                     {"key": pre + "show_open", "label": "Shown in 3D", "type": "choice", "value": "open" if cs["show_open"] else "closed",
                      "choices": [{"value": "closed", "label": "Closed"}, {"value": "open", "label": "Open at its opening angle"}]}]
            if cs["door_type"] == "Sliding Door":
                rows.append({"key": pre + "slide", "label": "Slides toward", "type": "choice", "value": cs["slide"], "choices": [{"value": "start", "label": "Start"}, {"value": "end", "label": "End"}]})
        else:
            rows += [{"key": pre + "material", "label": "Material", "type": "choice", "value": cs["material"], "choices": E._choices(PANEL_MATERIALS)},
                     {"key": pre + "thickness", "label": "Thickness", "type": "number", "unit": "mm", "value": mm(cs["thickness"])},
                     {"key": pre + "offset", "label": "Offset from the frame's centre", "type": "number", "unit": "mm", "value": mm(cs["offset"])}]
        size = f"{(cell['u1'] - cell['u0']) * 1000:,.0f} × {(cell['z1'] - cell['z0']) * 1000:,.0f} mm"
        return {**head, "title": f"{name} › Panel {cell['col'] + 1}.{cell['row'] + 1}", "top": rows,
                "groups": [{"title": "Field", "rows": [{"label": "Clear opening", "type": "readonly", "value": size},
                                                       {"label": "From the start / base", "type": "readonly", "value": f"{cell['u0'] * 1000:,.0f} mm / {cell['z0'] * 1000:,.0f} mm"},
                                                       back],
                            "action": {"label": "Reset to the wall's panel", "command": "model.set_property", "params": {"id": oid, "key": pre + "reset", "value": "1"}}}]}
    if part.startswith("corner:"):
        end = part[7:]
        j = _junction(project, oid, end)
        if j is None:
            raise NotFound("That end has no corner")
        kind = corner_type(j)
        rows = [{"key": f"corner:{end}:type", "label": "Corner", "type": "choice", "value": kind if kind != "inline" else "auto",
                 "choices": [{"value": "auto", "label": "Automatic (by the angle)"}] + [{"value": k, "label": v} for k, v in CORNER_TYPES.items()]}]
        cfg = j["cfg"]
        sizes = {"quad": (("depth_a", "Depth along this wall", j["depth"]), ("depth_b", "Depth along the other wall", j["other_depth"])),
                 "trapezoid": (("leg", "Leg from the corner", max(j["depth"], j["other_depth"]) / 2),),
                 "L": (("leg_a", "Leg along this wall", leg_default(j, 1.0)), ("leg_b", "Leg along the other wall", leg_default(j, 1.0)), ("thickness", "Thickness", j["width"])),
                 "V": (("leg_a", "Leg along this wall", leg_default(j, 0.5)), ("leg_b", "Leg along the other wall", leg_default(j, 0.5)), ("thickness", "Thickness", j["width"]))}.get(kind, ())
        swap = j["owner"] != oid
        for k, label, default in sizes:
            stored = {"depth_a": "depth_b", "depth_b": "depth_a", "leg_a": "leg_b", "leg_b": "leg_a"}.get(k, k) if swap else k
            rows.append({"key": f"corner:{end}:{k}", "label": label, "type": "number", "unit": "mm", "value": mm(float(cfg.get(stored, default)))})
        other = project.model.get(j["other"])["name"] or "Curtain Wall"
        return {**head, "title": f"{name} › {CORNER_TYPES.get(kind, 'Corner')}", "top": rows,
                "groups": [{"title": "Junction", "rows": [{"label": "Angle inside the corner", "type": "readonly", "value": f"{j['angle']:.1f}°"},
                                                          {"label": "Meets", "type": "link", "value": other, "target": {"select": {"model": j["other"]}}},
                                                          {"label": "Post carried by", "type": "readonly", "value": "this wall" if j["owner"] == oid else other}, back]}]}
    label = _part_label(sp, part)
    found = next((q for q in members(project, meta) if q["id"] == part), None)
    if found is None:
        raise NotFound(f"This curtain wall has no part {part}")
    rows: list[dict[str, Any]] = [{"label": "Member", "type": "readonly", "value": label},
                                  {"label": "Profile", "type": "readonly", "value": f"{sp['frame_width'] * 1000:.0f} × {sp['frame_depth'] * 1000:.0f} mm ({sp['frame_material']})"}]
    d, _, k = part.partition(":")
    if d in ("v", "h") and k.isdigit():
        q = lines(sp, d)[int(k)]
        rows.append({"key": f"line:{d}:{k}", "label": "In the wall", "type": "choice", "value": "removed" if q["removed"] else "present",
                     "choices": [{"value": "present", "label": "Present"}, {"value": "removed", "label": "Remove"}]})
        rows.append({"label": "Axis at", "type": "readonly", "value": f"{q['pos'] * 1000:,.0f} mm {'from the start' if d == 'v' else 'above the base'}"})
    total = sum(m_["length"] for m_ in members(project, meta) if m_["id"] == part)
    rows.append({"label": "Length", "type": "readonly", "value": f"{total:.3f} m"})
    return {**head, "title": f"{name} › {label}", "top": rows,
            "groups": [{"title": "Frame (shared by every member)", "rows": [
                {"key": "frame_width", "label": "Frame width (face)", "type": "number", "unit": "mm", "value": mm(sp["frame_width"])},
                {"key": "frame_depth", "label": "Frame thickness (depth)", "type": "number", "unit": "mm", "value": mm(sp["frame_depth"])},
                {"key": "frame_material", "label": "Frame material", "type": "choice", "value": sp["frame_material"], "choices": E._choices(FRAME_MATERIALS)}, back]}]}


def door_handles(project: Any, oid: str) -> list[dict[str, Any]]:
    """For the 3D view: one handle per swinging door leaf of the curtain wall, at its free edge, with the hinge axis it turns about and the field it
    belongs to, so the opening angle can be dragged."""
    meta = E.meta_of(project, oid)
    sp = spec(meta)
    at = _to_world(frame_of(meta), base_z(project, meta))
    out = []
    for c, cs in _door_cells(sp):
        if cs["door_type"] == "Sliding Door":
            continue
        for n, (a, b, y) in enumerate(door_leaves(cs, c["u0"], c["u1"], cs["offset"])):
            hu, hd, sign = leaf_hinges(cs, a, b, y, n)
            free = b if abs(hu - a) < 1e-9 else a
            ang = math.radians(cs["opening_angle"] if cs["show_open"] else 0.0) * sign
            fu, fd = hu + (free - hu) * math.cos(ang), hd + (free - hu) * math.sin(ang)
            zmid = (c["z0"] + c["z1"]) / 2
            out.append({"part": f"cell:{c['key']}", "key": f"panel:{c['col']}:{c['row']}", "hinge": at(hu, hd, zmid), "free": at(fu, fd, zmid),
                        "closed": at(free, hd, zmid), "sign": sign, "angle": cs["opening_angle"], "open": cs["show_open"]})
    return out


def window_leaves(cs: Mapping[str, Any], u0: float, u1: float, offset: float) -> list[tuple[float, float, float]]:
    """The u0, u1 and y of each operable leaf of a window field, in order from the start of the wall. The leaves are the same size and evenly spaced, with
    the first leaf's free edge at the start of the field."""
    if cs["type"] != "Window":
        raise BadRequest("Not a window")
    n = cs["leaves"]
    if n < 1:
        raise BadRequest("A window must have at least one leaf")
    width = (u1 - u0) / n
    return [(u0 + i * width, u0 + (i + 1) * width, offset) for i in range(n)]


def window_handles(project: Any, oid: str) -> list[dict[str, Any]]:
    """For the 3D view: one handle per operable window leaf of the curtain wall, at its free edge, with the hinge axis it turns about and the field it
    belongs to, so the opening angle can be dragged."""
    meta = E.meta_of(project, oid)
    sp = spec(meta)
    at = _to_world(frame_of(meta), base_z(project, meta))
    out = []
    for c in cells(sp):
        cs = cell_spec(sp, c)
        if cs["type"] != "Window":
            continue
        for n, (a, b, y) in enumerate(window_leaves(cs, c["u0"], c["u1"], cs["offset"])):
            hu, hd, sign = leaf_hinges(cs, a, b, y, n)
            free = b if abs(hu - a) < 1e-9 else a
            ang = math.radians(cs["opening_angle"] if cs["show_open"] else 0.0) * sign
            fu, fd = hu + (free - hu) * math.cos(ang), hd + (free - hu) * math.sin(ang)
            zmid = (c["z0"] + c["z1"]) / 2
            out.append({"part": f"cell:{c['key']}", "key": f"panel:{c['col']}:{c['row']}", "hinge": at(hu, hd, zmid), "free": at(fu, fd, zmid),
                        "closed": at(free, hd, zmid), "sign": sign, "angle": cs["opening_angle"], "open": cs["show_open"]})
    return out
