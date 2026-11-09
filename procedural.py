"""Procedural geometry in SPADRA (P1-04): a façade of panels on a wall, cut around the wall's real openings, a structural frame of columns and
beams on a grid, and the lights (glass panes and mullions) of a window. Both are *generators over ordinary elements*: the panels are generic elements, the columns and beams are the ordinary structural
elements, so selecting, scheduling, undo, save and every existing check apply to them and nothing here is a second model.

A generator's recipe is stored as a group (``manifest["procedural"]``): its kind, its parameters in SI, and which element each *key* became. Keys are
positions in the pattern (``"3:1:0"`` is column 3, row 1, piece 0; ``"c:2:1"`` the column at grid point 2,1), so regenerating after a change keeps the same
element id for the same key: an element that is still wanted is updated in place, a new key makes a new element, a key that is no longer produced
removes its element. An element a person deleted by hand comes back at the next regeneration; one they moved is put back (the recipe is the truth).
A group follows its wall: any command that moves the wall or changes its openings regenerates the group in the same transaction, and a group whose
wall is gone reports ``host: "missing"`` and keeps its elements untouched."""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from typing import Any, Mapping

from src.utils.spatial_formats.sgeo import parameters as SP

from ..tools.errors import BadRequest, NotFound
from ..tools.validation import number, text
from . import elements as E

MAX_MEMBERS = 5000
MIN_PIECE = 0.05                      # m: a cut-away remainder smaller than this in either direction is dropped, and counted
FACADE_PATTERNS = ("grid", "running_bond", "rhythm")
MIN_LIGHT = 0.02                      # m: a window light (or the clear span it sits in) narrower than this is refused, never drawn as a sliver or a negative region
KEY = "procedural"


def _groups(project: Any) -> list[dict[str, Any]]:
    return project.manifest.setdefault("procedural", [])


def group(project: Any, gid: Any) -> dict[str, Any]:
    for g in _groups(project):
        if g["id"] == gid:
            return g
    raise NotFound(f"There is no generated group {gid}")


# ------------------------------------------------------------------------------------------------------------------------- geometry helpers
def _subtract(rect: tuple[float, float, float, float], hole: tuple[float, float, float, float]) -> list[tuple[float, float, float, float]]:
    """``rect`` minus ``hole`` as at most four rectangles (u0, u1, v0, v1), in a fixed order: below, above, left, right."""
    u0, u1, v0, v1 = rect
    h0, h1, g0, g1 = hole
    if h1 <= u0 or h0 >= u1 or g1 <= v0 or g0 >= v1:
        return [rect]
    out = []
    if g0 > v0:
        out.append((u0, u1, v0, g0))
    if g1 < v1:
        out.append((u0, u1, g1, v1))
    a, b = max(v0, g0), min(v1, g1)
    if h0 > u0:
        out.append((u0, h0, a, b))
    if h1 < u1:
        out.append((h1, u1, a, b))
    return out


def _box_spec(a: tuple[float, float], angle: float, u_mid: float, n_mid: float, z_mid: float, du: float, dn: float, dz: float) -> dict[str, Any]:
    """A box whose long side runs along the wall: centred ``u_mid`` along it from ``a`` and ``n_mid`` to its left, at height ``z_mid``."""
    c, s = math.cos(angle), math.sin(angle)
    x, y = a[0] + c * u_mid - s * n_mid, a[1] + s * u_mid + c * n_mid
    return {"geometry": {"kind": "box", "center": [0.0, 0.0, 0.0], "size": [du, dn, dz]},
            "transform": [c, -s, 0.0, x, s, c, 0.0, y, 0.0, 0.0, 1.0, z_mid, 0.0, 0.0, 0.0, 1.0]}


# ------------------------------------------------------------------------------------------------------------------------- façade
def _facade_params(raw: Mapping[str, Any], base: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Parameters from the person's units (mm) to SI, merged over ``base``."""
    out = dict(base or {"panel_width": 1.2, "panel_height": 1.5, "joint": 0.01, "thickness": 0.03, "offset": 0.0, "opening_margin": 0.0, "side": "left", "material": "Glass"})
    known = {"wall", "panel_width", "panel_height", "joint", "thickness", "offset", "opening_margin", "side", "material", "name", "pattern", "bond_offset", "rhythm"}
    unknown = sorted(set(raw) - known - {"kind", "id"})
    if unknown:
        raise BadRequest(f"Unknown façade parameter(s): {', '.join(unknown)}", details={"parameters": sorted(known)})
    if "wall" in raw:
        out["wall"] = text(raw["wall"], "wall", 80)
    for key, lo, hi in (("panel_width", 50, 20000), ("panel_height", 50, 20000), ("joint", 0, 200), ("thickness", 1, 2000), ("offset", -2000, 5000), ("opening_margin", 0, 2000)):
        if key in raw:
            out[key] = number(raw[key], key, lo, hi) / 1000.0
    if "side" in raw:
        if raw["side"] not in ("left", "right"):
            raise BadRequest("side must be 'left' or 'right' of the wall's direction")
        out["side"] = raw["side"]
    if "material" in raw:
        out["material"] = E._choice(raw["material"], E.MATERIALS, "material")
    if "name" in raw:
        out["name"] = text(raw["name"], "name", 80)
    if "pattern" in raw:
        if raw["pattern"] not in FACADE_PATTERNS:
            raise BadRequest(f"pattern must be one of {', '.join(FACADE_PATTERNS)}")
        out["pattern"] = raw["pattern"]
    if "bond_offset" in raw:
        out["bond_offset"] = float(number(raw["bond_offset"], "bond_offset", 0.05, 0.95))
    if "rhythm" in raw:
        r = raw["rhythm"]
        if r is None:
            out.pop("rhythm", None)
        else:
            if not isinstance(r, (list, tuple)) or not 1 <= len(r) <= 16:
                raise BadRequest("rhythm must be a list of 1 to 16 panel widths in mm")
            out["rhythm"] = [number(w, "rhythm width", 50, 20000) / 1000.0 for w in r]
    if raw.get("pattern", "rhythm") != "rhythm" and "rhythm" not in raw:
        out.pop("rhythm", None)                                              # changing away from the rhythm pattern drops its list
    if raw.get("pattern", "running_bond") != "running_bond" and "bond_offset" not in raw:
        out.pop("bond_offset", None)
    pattern = out.get("pattern", "grid")
    if pattern == "rhythm" and not out.get("rhythm"):
        raise BadRequest("The rhythm pattern needs a rhythm: the repeating list of panel widths in mm")
    if pattern != "rhythm" and out.get("rhythm"):
        raise BadRequest("A rhythm only applies to the rhythm pattern")
    if pattern != "running_bond" and "bond_offset" in raw:
        raise BadRequest("bond_offset only applies to the running_bond pattern")
    if out["joint"] >= min(out["panel_width"], out["panel_height"], *(out.get("rhythm") or [1.0])):
        raise BadRequest("The joint must be narrower than the panels")
    return out


def _column_breaks(prm: Mapping[str, Any], length: float, row: int) -> list[float]:
    """Where the panel columns of one row start and end along the wall (0 .. length). ``grid``: equal columns, the same in every row. ``running_bond``: the same columns, but every
    odd row is shifted by ``bond_offset`` of a column, so the vertical joints do not line up and the row begins and ends with a partial panel. ``rhythm``: columns of the listed
    widths repeated along the wall, the last one trimmed to the wall; a remainder under the minimum piece is folded into the panel before it."""
    pattern = prm.get("pattern", "grid")
    if pattern == "rhythm":
        widths, out, x, i = prm["rhythm"], [0.0], 0.0, 0
        while length - x > 1e-9:
            x = min(length, x + widths[i % len(widths)])
            i += 1
            if i > MAX_MEMBERS:
                raise BadRequest(f"That rhythm makes more than {MAX_MEMBERS} columns along the wall", hint="Use wider panels")
            if length - x < MIN_PIECE and x < length:
                x = length                                                # a sliver at the end becomes part of the last panel
            out.append(x)
        return out
    nu = max(1, math.ceil(length / prm["panel_width"] - 1e-9))
    breaks = [i * length / nu for i in range(nu + 1)]
    if pattern == "running_bond" and row % 2 == 1:
        w = length / nu
        shift = prm.get("bond_offset", 0.5) * w
        breaks = [0.0] + [shift + i * w for i in range(nu)] + [length]
    return breaks


def _facade_desired(project: Any, prm: Mapping[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    wall = prm["wall"]
    if not E.alive(project, wall):
        raise NotFound(f"The wall {wall} does not exist")
    f = E.wall_frame(project, wall)
    length, height, base, angle = f["length"], f["height"], f["base"], f["angle"]
    nv = max(1, math.ceil(height / prm["panel_height"] - 1e-9))
    j = prm["joint"]
    openings = []
    for oid in E.hosted(project, wall):
        x0, x1, z0, z1 = E.opening_span(E.meta_of(project, oid))
        mg = prm["opening_margin"]
        openings.append((x0 - mg, x1 + mg, z0 - mg, z1 + mg))
    sign = 1.0 if prm["side"] == "left" else -1.0
    n_mid = sign * (f["thickness"] / 2 + prm["offset"] + prm["thickness"] / 2)
    desired: dict[str, dict[str, Any]] = {}
    cut = dropped = 0
    nu = 0
    for k in range(nv):
        breaks = _column_breaks(prm, length, k)
        nu = max(nu, len(breaks) - 1)
        for i in range(len(breaks) - 1):
            cell = (breaks[i] + j / 2, breaks[i + 1] - j / 2, k * height / nv + j / 2, (k + 1) * height / nv - j / 2)
            pieces = [cell]
            for hole in openings:
                pieces = [q for p in pieces for q in _subtract(p, hole)]
            if pieces != [cell]:
                cut += 1
            for piece, (u0, u1, v0, v1) in enumerate(pieces):
                if u1 - u0 < MIN_PIECE or v1 - v0 < MIN_PIECE:
                    dropped += 1
                    continue
                spec = _box_spec(f["start"], angle, (u0 + u1) / 2, n_mid, base + (v0 + v1) / 2, u1 - u0, prm["thickness"], v1 - v0)
                spec.update(name=prm.get("name") or "Facade Panel",
                            meta={"category": "generic", "type": "Facade Panel", "level": E.m(f["meta"], "level"), "material": prm["material"], "facade_wall": wall,
                                  "panel": {"column": i, "row": k, "piece": piece, "width": u1 - u0, "height": v1 - v0}})
                desired[f"{i}:{k}:{piece}"] = spec
    if not desired:
        raise BadRequest("These settings produce no panels (every piece is smaller than 50 mm or inside an opening)", hint="Use larger panels or a narrower joint")
    if len(desired) > MAX_MEMBERS:
        raise BadRequest(f"That makes {len(desired)} panels; a group holds at most {MAX_MEMBERS}", hint="Use larger panels")
    area = sum(s["meta"]["panel"]["width"] * s["meta"]["panel"]["height"] for s in desired.values())
    openings_area = sum((o[1] - o[0]) * (o[3] - o[2]) for o in openings)
    report = {"pattern": prm.get("pattern", "grid"), "columns": nu, "rows": nv, "panels": len(desired), "panels_cut_by_openings": cut, "pieces_dropped_below_min_size": dropped, "openings": len(openings),
              "panel_area": area, "wall_area": length * height, "openings_area": openings_area}
    return desired, report


def _facade_inputs(project: Any, prm: Mapping[str, Any]) -> Any:
    wall = prm["wall"]
    if not E.alive(project, wall):
        return "missing"
    f = E.wall_frame(project, wall)
    return [f["start"], f["end"], f["thickness"], f["height"], f["base"], sorted((o, E.opening_span(E.meta_of(project, o))) for o in E.hosted(project, wall))]


# ------------------------------------------------------------------------------------------------------------------------- structural frame
def _frame_params(raw: Mapping[str, Any], base: Mapping[str, Any] | None, to_m: float) -> dict[str, Any]:
    out = dict(base or {"origin": [0.0, 0.0], "bays_x": [6.0], "bays_y": [6.0], "column": {"width": 0.4, "depth": 0.4, "material": "Concrete"},
                        "beam": {"width": 0.3, "depth": 0.5, "material": "Concrete"}, "beam_top": None, "column_top": None})
    known = {"level", "origin", "bays_x", "bays_y", "column", "beam", "beam_top", "column_top", "name"}
    unknown = sorted(set(raw) - known - {"kind", "id"})
    if unknown:
        raise BadRequest(f"Unknown frame parameter(s): {', '.join(unknown)}", details={"parameters": sorted(known)})
    if "level" in raw:
        out["level"] = text(raw["level"], "level", 80)
    if "origin" in raw:
        q = raw["origin"]
        if not isinstance(q, (list, tuple)) or len(q) != 2:
            raise BadRequest("origin must be a point [x, y]")
        out["origin"] = [float(number(q[0], "x", -1e9, 1e9)) * to_m, float(number(q[1], "y", -1e9, 1e9)) * to_m]
    for key in ("bays_x", "bays_y"):
        if key in raw:
            v = raw[key]
            if not isinstance(v, (list, tuple)) or not 1 <= len(v) <= 40:
                raise BadRequest(f"{key} must list 1 to 40 bay spacings")
            out[key] = [float(number(b, key, 100, 1e6)) * to_m for b in v]
    for key, mats in (("column", E.BEAM_MATERIALS), ("beam", E.BEAM_MATERIALS)):
        if key in raw:
            r = raw[key]
            if not isinstance(r, Mapping) or set(r) - {"width", "depth", "material"}:
                raise BadRequest(f"{key} takes width, depth (mm) and material")
            cur = dict(out[key])
            for d in ("width", "depth"):
                if d in r:
                    cur[d] = number(r[d], f"{key}.{d}", 10, 5000) / 1000.0
            if "material" in r:
                cur["material"] = E._choice(r["material"], mats, "material")
            out[key] = cur
    for key in ("beam_top", "column_top"):
        if key in raw:
            out[key] = None if raw[key] is None else number(raw[key], key, 0.1, 500)          # metres above the level
    if "name" in raw:
        out["name"] = text(raw["name"], "name", 80)
    if "level" not in out:
        raise BadRequest("A frame needs a level")
    if (len(out["bays_x"]) + 1) * (len(out["bays_y"]) + 1) > 1600:
        raise BadRequest("That grid has more than 1600 columns")
    return out


def _frame_desired(project: Any, prm: Mapping[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    level = project.level(prm["level"])
    nxt = [l for l in sorted(project.manifest["levels"], key=lambda l: l["elevation"]) if l["elevation"] > level["elevation"] + 1e-9]
    storey = (nxt[0]["elevation"] - level["elevation"]) if nxt else 3.0
    col_top = prm.get("column_top") or storey
    beam_top = prm.get("beam_top") or col_top
    xs = [prm["origin"][0]]
    for b in prm["bays_x"]:
        xs.append(xs[-1] + b)
    ys = [prm["origin"][1]]
    for b in prm["bays_y"]:
        ys.append(ys[-1] + b)
    c, bm = prm["column"], prm["beam"]
    desired: dict[str, dict[str, Any]] = {}
    for i, x in enumerate(xs):
        for j, y in enumerate(ys):
            desired[f"c:{i}:{j}"] = {"name": "Column", "kind": "column", "meta": {"category": "structural", "type": "Column", "level": level["id"], "point": [x, y], "base": 0.0, "top": col_top,
                                     "width": c["width"], "depth": c["depth"], "rotation": 0.0, "material": c["material"], "grid": [i, j]}}
    spans = []
    for j, y in enumerate(ys):
        for i in range(len(xs) - 1):
            desired[f"bx:{i}:{j}"] = {"name": "Beam", "kind": "beam", "meta": {"category": "structural", "type": "Beam", "level": level["id"], "start": [xs[i], y], "end": [xs[i + 1], y], "top": beam_top,
                                      "width": bm["width"], "depth": bm["depth"], "material": bm["material"], "profile": "rect", "supports": [f"c:{i}:{j}", f"c:{i + 1}:{j}"]}}
            spans.append({"key": f"bx:{i}:{j}", "span": xs[i + 1] - xs[i], "depth": bm["depth"], "span_over_depth": (xs[i + 1] - xs[i]) / bm["depth"]})
    for i, x in enumerate(xs):
        for j in range(len(ys) - 1):
            desired[f"by:{i}:{j}"] = {"name": "Beam", "kind": "beam", "meta": {"category": "structural", "type": "Beam", "level": level["id"], "start": [x, ys[j]], "end": [x, ys[j + 1]], "top": beam_top,
                                      "width": bm["width"], "depth": bm["depth"], "material": bm["material"], "profile": "rect", "supports": [f"c:{i}:{j}", f"c:{i}:{j + 1}"]}}
            spans.append({"key": f"by:{i}:{j}", "span": ys[j + 1] - ys[j], "depth": bm["depth"], "span_over_depth": (ys[j + 1] - ys[j]) / bm["depth"]})
    if len(desired) > MAX_MEMBERS:
        raise BadRequest(f"That makes {len(desired)} members; a group holds at most {MAX_MEMBERS}")
    unsupported = [k for k, v in desired.items() if v["kind"] == "beam" and not all(s in desired for s in v["meta"]["supports"])]
    report = {"columns": sum(1 for v in desired.values() if v["kind"] == "column"), "beams": len(spans), "storey_height": storey, "longest_span": max(s["span"] for s in spans) if spans else 0.0,
              "max_span_over_depth": max((s["span_over_depth"] for s in spans), default=0.0), "beams_without_supports": unsupported,
              "scope": "layout and connectivity only: no loads or member sizing are computed here (see the beam analysis for a single member)"}
    return desired, report


# ------------------------------------------------------------------------------------------------------------------------- window lights
def _lights_params(raw: Mapping[str, Any], base: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Parameters from the person's units (mm) to SI, merged over ``base``."""
    out = dict(base or {"panels": 3, "frame": 0.06, "mullion": 0.04, "glass_thickness": 0.024, "mullion_depth": 0.06, "offset": 0.0, "material": "Glass",
                        "mullion_material": "Steel"})
    known = {"opening", "panels", "frame", "mullion", "glass_thickness", "mullion_depth", "offset", "material", "mullion_material", "name"}
    unknown = sorted(set(raw) - known - {"kind", "id"})
    if unknown:
        raise BadRequest(f"Unknown window-light parameter(s): {', '.join(unknown)}", details={"parameters": sorted(known)})
    if "opening" in raw:
        out["opening"] = text(raw["opening"], "opening", 80)
    if "panels" in raw:
        n = number(raw["panels"], "panels", 1, 12)
        if int(n) != n:
            raise BadRequest("panels must be a whole number of lights")
        out["panels"] = int(n)
    for key, lo, hi in (("frame", 0, 500), ("mullion", 5, 500), ("glass_thickness", 1, 200), ("mullion_depth", 5, 500), ("offset", -2000, 2000)):
        if key in raw:
            out[key] = number(raw[key], key, lo, hi) / 1000.0
    for key in ("material", "mullion_material"):
        if key in raw:
            out[key] = E._choice(raw[key], E.MATERIALS, key)
    if "name" in raw:
        out["name"] = text(raw["name"], "name", 80)
    if "opening" not in out:
        raise BadRequest("Window lights need a window (opening)")
    return out


def lights_values(span: tuple[float, float, float, float], prm: Mapping[str, Any]) -> dict[str, Any]:
    """The derived dimensions of a window's lights, through SGEO's typed formulas (the same engine and bounds as a family's formulas).

    ``span`` is the window's (x0, x1, sill, head). A combination that leaves a negative or sliver glazing region is refused with the number that failed.
    """
    x0, x1, z0, z1 = span
    table = {"width": {"type": "length", "default": x1 - x0}, "height": {"type": "length", "default": z1 - z0}, "frame": {"type": "length", "default": prm["frame"]},
             "mullion": {"type": "length", "default": prm["mullion"]}, "panels": {"type": "integer", "default": prm["panels"], "min": 1, "max": 12},
             "show_muntin": {"type": "boolean", "expression": "panels > 1"},
             "clear_span": {"type": "length", "expression": "width - 2 * frame", "min": MIN_LIGHT},
             "clear_height": {"type": "length", "expression": "height - 2 * frame", "min": MIN_LIGHT},
             "light_width": {"type": "length", "expression": "(clear_span - (panels - 1) * mullion) / panels", "min": MIN_LIGHT}}
    try:
        return SP.evaluate_parameters(table)
    except Exception as exc:
        raise BadRequest(f"These lights do not fit the window: {exc}",
                         hint="Use fewer lights, narrower mullions or a narrower frame, or make the window larger") from exc


def _lights_desired(project: Any, prm: Mapping[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    opid = prm["opening"]
    if not E.alive(project, opid):
        raise NotFound(f"The window {opid} does not exist")
    opening = E.meta_of(project, opid)
    if E.m(opening, "category") != "window":
        raise BadRequest("Lights can only be generated in a window")
    f = E.wall_frame(project, str(E.m(opening, "host")))
    x0, x1, z0, z1 = E.opening_span(opening)
    v = lights_values((x0, x1, z0, z1), prm)
    light, clear_h, n, mull = v["light_width"], v["clear_height"], prm["panels"], prm["mullion"]
    level = E.m(f["meta"], "level")
    base, angle, n_mid = f["base"], f["angle"], prm["offset"]
    v0 = z0 + prm["frame"]
    desired: dict[str, dict[str, Any]] = {}
    for i in range(n):
        u = x0 + prm["frame"] + i * (light + mull)
        spec = _box_spec(f["start"], angle, u + light / 2, n_mid, base + v0 + clear_h / 2, light, prm["glass_thickness"], clear_h)
        spec.update(name=prm.get("name") or "Window Light",
                    meta={"category": "generic", "type": "Window Light", "level": level, "material": prm["material"], "window": opid,
                          "light": {"index": i, "width": light, "height": clear_h}})
        desired[f"g:{i}"] = spec
    for i in range(n - 1 if v["show_muntin"] else 0):
        u = x0 + prm["frame"] + (i + 1) * light + i * mull + mull / 2
        spec = _box_spec(f["start"], angle, u, n_mid, base + v0 + clear_h / 2, mull, prm["mullion_depth"], clear_h)
        spec.update(name="Window Mullion", meta={"category": "generic", "type": "Window Mullion", "level": level, "material": prm["mullion_material"], "window": opid})
        desired[f"m:{i}"] = spec
    report = {"window": opid, "lights": n, "mullions": n - 1 if v["show_muntin"] else 0, "light_width": light, "light_height": clear_h, "clear_span": v["clear_span"],
              "glazed_area": n * light * clear_h, "window_width": x1 - x0, "window_height": z1 - z0, "window_centre_offset": (x0 + x1) / 2,
              "scope": "glazing layout only: no thermal, safety-glazing, structural or accessibility performance is evaluated"}
    return desired, report


def _lights_inputs(project: Any, prm: Mapping[str, Any]) -> Any:
    opid = prm["opening"]
    if not E.alive(project, opid):
        return "missing"
    opening = E.meta_of(project, opid)
    f = E.wall_frame(project, str(E.m(opening, "host")))
    return [list(E.opening_span(opening)), f["start"], f["end"], f["thickness"], f["base"], E.m(opening, "category")]


# ------------------------------------------------------------------------------------------------------------------------- reconcile
def _apply(project: Any, g: dict[str, Any], desired: Mapping[str, Mapping[str, Any]]) -> dict[str, int]:
    members: dict[str, str] = g["members"]
    stats = {"created": 0, "updated": 0, "removed": 0}
    for key in sorted(set(members) - set(desired)):
        oid = members.pop(key)
        if E.alive(project, oid):
            project.model.remove(oid)
            stats["removed"] += 1
    for key in sorted(desired):
        spec = desired[key]
        oid = members.get(key)
        if oid is None or not E.alive(project, oid):
            oid = project.model.add_box(width=1.0, depth=1.0, height=1.0, name=spec["name"])
            members[key] = oid
            stats["created"] += 1
        else:
            stats["updated"] += 1
        meta = dict(spec["meta"])
        meta[KEY] = {"group": g["id"], "key": key}
        E._attach(project, oid, meta)
        if spec.get("kind") == "column":
            E.regenerate_column(project, oid)
        elif spec.get("kind") == "beam":
            E.regenerate_beam(project, oid)
        else:
            project.model.set_geometry(oid, spec["geometry"])
            project.model.set_transform(oid, spec["transform"])
    return stats


def _host(g: Mapping[str, Any]) -> str | None:
    """The element a group follows (a façade's wall, a window's lights' opening); a frame follows only the levels."""
    return g["params"].get({"facade": "wall", "lights": "opening"}.get(g["kind"], ""))


def _compute(project: Any, g: Mapping[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    if g["kind"] == "facade":
        return _facade_desired(project, g["params"])
    return _lights_desired(project, g["params"]) if g["kind"] == "lights" else _frame_desired(project, g["params"])


def _fingerprint(project: Any, g: Mapping[str, Any]) -> str:
    if g["kind"] == "facade":
        inputs = _facade_inputs(project, g["params"])
    elif g["kind"] == "lights":
        inputs = _lights_inputs(project, g["params"])
    else:
        inputs = [g["params"], project.manifest["levels"]]
    return hashlib.sha256(json.dumps([g["params"], inputs], sort_keys=True, default=str).encode()).hexdigest()


def regenerate(project: Any, gid: Any) -> dict[str, Any]:
    g = group(project, gid)
    desired, report = _compute(project, g)
    stats = _apply(project, g, desired)
    g["input_hash"] = _fingerprint(project, g)
    g["report"] = report
    return {"id": g["id"], **stats, "report": report}


def reconcile(project: Any) -> None:
    """Called inside every model command: a group whose wall (or levels) changed is regenerated; one whose wall is gone is marked and left alone."""
    for g in _groups(project):
        if _host(g) is not None and not E.alive(project, _host(g)):
            g["host"] = "missing"
            continue
        g["host"] = "resolved"
        if g.get("input_hash") != _fingerprint(project, g):
            regenerate(project, g["id"])


def _create(project: Any, kind: str, params: dict[str, Any], name: Any) -> dict[str, Any]:
    if len(_groups(project)) >= 100:
        raise BadRequest("A project holds at most 100 generated groups")
    g = {"id": "gen-" + uuid.uuid4().hex[:8], "kind": kind, "name": text(name, "name", 80) if name else {"facade": "Facade", "lights": "Window lights"}.get(kind, "Structural frame"),
         "params": params, "members": {}, "host": "resolved"}
    _groups(project).append(g)
    try:
        return {**regenerate(project, g["id"]), "members": dict(g["members"])}
    except Exception:
        _groups(project).remove(g)
        raise


def add_facade(project: Any, p: Mapping[str, Any]) -> dict[str, Any]:
    return _create(project, "facade", _facade_params(p), p.get("name"))


def add_lights(project: Any, p: Mapping[str, Any]) -> dict[str, Any]:
    prm = _lights_params(p)
    if any(g["kind"] == "lights" and g["params"]["opening"] == prm["opening"] for g in _groups(project)):
        raise BadRequest("This window already has generated lights", hint="Edit the existing group instead")
    return _create(project, "lights", prm, p.get("name"))


def add_frame(project: Any, p: Mapping[str, Any], to_m: float) -> dict[str, Any]:
    return _create(project, "frame", _frame_params(p, None, to_m), p.get("name"))


def update(project: Any, p: Mapping[str, Any], to_m: float) -> dict[str, Any]:
    """Change a group's parameters (same units as when it was made) and regenerate it; a change that cannot be generated is refused and nothing changes."""
    g = group(project, p.get("id"))
    values = {k: v for k, v in p.items() if k != "id"}
    if g["kind"] == "facade":
        g["params"] = _facade_params(values, g["params"])
    elif g["kind"] == "lights":
        g["params"] = _lights_params(values, g["params"])
    else:
        g["params"] = _frame_params(values, g["params"], to_m)
    if "name" in values:
        g["name"] = g["params"].get("name", g["name"])
    return regenerate(project, g["id"])


def delete(project: Any, gid: Any, keep_elements: bool = False) -> int:
    g = group(project, gid)
    n = 0
    for oid in g["members"].values():
        if E.alive(project, oid):
            if keep_elements:
                E._attach(project, oid, {KEY: None})
            else:
                project.model.remove(oid)
                n += 1
    _groups(project).remove(g)
    return n


def describe(project: Any) -> list[dict[str, Any]]:
    return [{"id": g["id"], "name": g["name"], "kind": g["kind"], "host": g.get("host", "resolved"), "members": len(g["members"]), "params": g["params"], "report": g.get("report")} for g in _groups(project)]
