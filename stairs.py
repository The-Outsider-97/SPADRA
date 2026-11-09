"""Straight stairs. A stair is stored as parameters only, like every other element; its solid (a monolithic flight whose treads and risers are
exact) is regenerated from them, and the plan symbol (outline, tread lines, the arrow up) is derived on every read.

A flight climbs from its base level to its top level (plus offsets), in ``risers`` equal risers. The first riser rises at the start point and the
last one lands on the top level, so a flight of ``n`` risers has ``n - 1`` treads and a run of ``(n - 1) * tread``. The only limits applied are
geometric ones (the flight must rise, and its riser and tread must be sensible lengths); building-code rules are not checked."""
from __future__ import annotations

import math
from typing import Any, Mapping

from ..tools.errors import BadRequest
from ..tools.validation import number, text
from . import elements as E
from . import geometry as G

PREFERRED_RISER = 0.175                  # metres: used only to propose a riser count when the person does not give one
RISER_RANGE = (0.05, 0.5)
TREAD_RANGE = (0.05, 2.0)
WIDTH_RANGE = (0.2, 20.0)
MAX_RISERS = 100


def frame(project: Any, oid: str) -> dict[str, Any]:
    """Everything about a flight's placement, in metres and radians."""
    meta = E.meta_of(project, oid)
    if E.m(meta, "category") != "stair":
        raise BadRequest("That element is not a stair")
    base = E.level_elevation(project, E.m(meta, "level")) + float(E.m(meta, "base_offset", 0.0))
    top = E.level_elevation(project, E.m(meta, "top_level")) + float(E.m(meta, "top_offset", 0.0))
    n, tread = int(E.m(meta, "risers")), float(E.m(meta, "tread"))
    return {"start": tuple(E.m(meta, "start")), "angle": float(E.m(meta, "angle")), "width": float(E.m(meta, "width")), "tread": tread, "risers": n,
            "base": base, "top": top, "height": top - base, "riser": (top - base) / n if n else 0.0, "run": (n - 1) * tread, "meta": meta}


def _validate(f: Mapping[str, Any]) -> None:
    if f["height"] <= 0.01:
        raise BadRequest("A stair must rise: its top has to be above its base")
    if not 2 <= f["risers"] <= MAX_RISERS:
        raise BadRequest(f"A stair has between 2 and {MAX_RISERS} risers")
    lo, hi = RISER_RANGE
    if not lo <= f["riser"] <= hi:
        raise BadRequest(f"{f['risers']} risers over {f['height']:.3f} m make each riser {f['riser'] * 1000:.0f} mm; they must be between {lo * 1000:.0f} and {hi * 1000:.0f} mm",
                         hint="Change the number of risers")
    lo, hi = TREAD_RANGE
    if not lo <= f["tread"] <= hi:
        raise BadRequest(f"The tread must be between {lo * 1000:.0f} and {hi * 1000:.0f} mm")
    lo, hi = WIDTH_RANGE
    if not lo <= f["width"] <= hi:
        raise BadRequest(f"The stair width must be between {lo * 1000:.0f} and {hi * 1000:.0f} mm")


def profile(f: Mapping[str, Any]) -> list[tuple[float, float]]:
    """The side of the flight as (distance along the run, height above the base)."""
    n, t, r = f["risers"], f["tread"], f["riser"]
    pts: list[tuple[float, float]] = [(0.0, 0.0)]
    for i in range(n - 1):
        pts += [(i * t, (i + 1) * r), ((i + 1) * t, (i + 1) * r)]
    pts.append(((n - 1) * t, 0.0))                  # the n-th riser is the step up onto the floor above, so the flight itself has n - 1 treads
    return pts


def regenerate(project: Any, oid: str) -> None:
    f = frame(project, oid)
    _validate(f)
    try:
        verts, faces = G.prism(profile(f), -f["width"] / 2, f["width"] / 2)                    # (s, z, t): along, up, across
    except G.GeometryError as exc:
        raise BadRequest(str(exc)) from exc
    c, s = math.cos(f["angle"]), math.sin(f["angle"])
    x0, y0 = f["start"]
    mapped = [[x0 + c * a - s * t, y0 + s * a + c * t, f["base"] + z] for a, z, t in verts]
    E._set_mesh(project, oid, E._positive_volume((mapped, faces)))


def add_stair(project: Any, p: Mapping[str, Any], to_m: float) -> str:
    level = project.level(text(p.get("level"), "level", 80))
    levels = sorted(project.manifest["levels"], key=lambda l: l["elevation"])
    if p.get("top_level"):
        top = project.level(text(p["top_level"], "top_level", 80))
    else:
        above = [l for l in levels if l["elevation"] > level["elevation"] + 1e-9]
        if not above:
            raise BadRequest("There is no level above this one for the stair to reach", hint="Add a level above it, or choose the top level")
        top = above[0]
    points = []
    for key in ("start", "end"):
        q = p.get(key)
        if not isinstance(q, (list, tuple)) or len(q) != 2:
            raise BadRequest(f"{key} must be a point [x, y]")
        points.append((float(number(q[0], "x", -1e9, 1e9)) * to_m, float(number(q[1], "y", -1e9, 1e9)) * to_m))
    run = math.dist(*points)
    if run < 0.05:
        raise BadRequest("Draw the run of the stair: its start and end points are too close together")
    height = float(top["elevation"]) - float(level["elevation"])
    if p.get("risers") is not None:
        n = int(number(p["risers"], "risers", 2, MAX_RISERS))
    else:
        n = max(2, min(MAX_RISERS, round(height / PREFERRED_RISER))) if height > 0 else 2
    tread = number(p["tread"], "tread", TREAD_RANGE[0] * 1000, TREAD_RANGE[1] * 1000) / 1000.0 if p.get("tread") is not None else run / (n - 1)
    material = E._choice(p.get("material", "Concrete"), E.MATERIALS, "material")
    oid = project.model.add_box(width=1.0, depth=1.0, height=1.0, name=text(p.get("name") or "Stair", "name", 80))
    E._attach(project, oid, {"category": "stair", "type": "Straight Stair", "level": level["id"], "top_level": top["id"], "start": list(points[0]),
                             "angle": math.atan2(points[1][1] - points[0][1], points[1][0] - points[0][0]), "width": number(p.get("width", 1000), "width", WIDTH_RANGE[0] * 1000, WIDTH_RANGE[1] * 1000) / 1000.0,
                             "tread": tread, "risers": n, "base_offset": 0.0, "top_offset": 0.0, "material": material})
    try:
        regenerate(project, oid)
    except BadRequest:
        project.model.remove(oid)
        raise
    return oid


def set_property(project: Any, meta: Mapping[str, Any], key: str, value: Any) -> dict[str, Any]:
    if key == "width":
        return {"width": number(value, key, WIDTH_RANGE[0] * 1000, WIDTH_RANGE[1] * 1000) / 1000.0}
    if key == "tread":
        return {"tread": number(value, key, TREAD_RANGE[0] * 1000, TREAD_RANGE[1] * 1000) / 1000.0}
    if key == "risers":
        n = number(value, key, 2, MAX_RISERS)
        if int(n) != n:
            raise BadRequest("The number of risers must be a whole number")
        return {"risers": int(n)}
    if key in ("base_offset", "top_offset"):
        return {key: number(value, key, -100000, 100000) / 1000.0}
    if key in ("level", "top_level"):
        return {key: project.level(text(value, key, 80))["id"]}
    if key == "material":
        return {"material": E._choice(value, E.MATERIALS, "material")}
    if key == "angle":
        return {"angle": math.radians(number(value, key, -360, 360))}
    if key in ("x", "y"):
        s = list(E.m(meta, "start"))
        s[0 if key == "x" else 1] = number(value, key, -1e6, 1e6) / 1000.0
        return {"start": s}
    raise BadRequest(f"'{key}' is not an editable stair property")


def plan_item(project: Any, oid: str, f: float) -> dict[str, Any]:
    fr = frame(project, oid)
    obj = project.model.get(oid)
    length = fr["run"]
    c, s = math.cos(fr["angle"]), math.sin(fr["angle"])
    x0, y0 = fr["start"]
    across = fr["width"] / 2
    at = lambda a, t: [(x0 + c * a - s * t) * f, (y0 + s * a + c * t) * f]                       # noqa: E731
    return {"id": oid, "category": "stair", "name": obj["name"], "polygon": E._rect(fr["start"], fr["angle"], 0.0, length, across, f),
            "lines": [[at(i * fr["tread"], -across), at(i * fr["tread"], across)] for i in range(1, fr["risers"] - 1)],
            "arrow": {"from": at(0.0, 0.0), "to": at(length, 0.0), "label": "UP"}}


def properties(project: Any, oid: str, out: dict[str, Any], volume: dict[str, Any]) -> dict[str, Any]:
    fr = frame(project, oid)
    meta = fr["meta"]
    levels = E._levels(project)
    mm = lambda v: round(v * 1000, 6)                                                            # noqa: E731
    out["top"] = [{"label": "Type", "type": "readonly", "value": E.m(meta, "type", "Straight Stair")},
                  {"key": "level", "label": "Base Level", "type": "choice", "value": E.m(meta, "level"), "choices": levels},
                  {"key": "top_level", "label": "Top Level", "type": "choice", "value": E.m(meta, "top_level"), "choices": levels},
                  {"key": "width", "label": "Width", "type": "number", "unit": "mm", "value": mm(fr["width"])},
                  {"key": "risers", "label": "Risers", "type": "number", "value": fr["risers"]},
                  {"key": "tread", "label": "Tread", "type": "number", "unit": "mm", "value": mm(fr["tread"])},
                  {"key": "material", "label": "Material", "type": "choice", "value": E.m(meta, "material", "Concrete"), "choices": E._choices(E.MATERIALS)}]
    out["groups"] = [
        {"title": "Geometry", "rows": [{"label": "Riser height", "type": "readonly", "value": f"{fr['riser'] * 1000:.1f} mm (rise ÷ risers)"},
                                        {"label": "Total rise", "type": "readonly", "value": f"{fr['height']:.3f} m"},
                                        {"label": "Run", "type": "readonly", "value": f"{fr['run']:.3f} m"},
                                        {"key": "angle", "label": "Direction", "type": "number", "unit": "°", "value": round(math.degrees(fr["angle"]), 6)},
                                        {"key": "x", "label": "Start X", "type": "number", "unit": "mm", "value": mm(fr["start"][0])},
                                        {"key": "y", "label": "Start Y", "type": "number", "unit": "mm", "value": mm(fr["start"][1])}, volume]},
        {"title": "Constraints", "rows": [{"key": "base_offset", "label": "Base Offset", "type": "number", "unit": "mm", "value": mm(float(E.m(meta, "base_offset", 0.0)))},
                                          {"key": "top_offset", "label": "Top Offset", "type": "number", "unit": "mm", "value": mm(float(E.m(meta, "top_offset", 0.0)))},
                                          {"label": "Limits", "type": "readonly", "value": f"riser {RISER_RANGE[0] * 1000:.0f}–{RISER_RANGE[1] * 1000:.0f} mm, tread {TREAD_RANGE[0] * 1000:.0f}–{TREAD_RANGE[1] * 1000:.0f} mm (geometric only)"}]}]
    return out


# ------------------------------------------------------------------------------------------------------------------------- design
# The rules below are *design defaults the person can change*, not a building code: no jurisdiction is checked. Every check reports its value and its limit.
RULES = {"riser_min": 0.10, "riser_max": 0.20, "tread_min": 0.25, "tread_max": 0.40, "comfort_min": 0.60, "comfort_max": 0.65,
         "max_risers_per_flight": 16, "landing_depth": 1.0, "headroom": 2.0}


def rules_from(raw: Any, to_m: float = 0.001) -> dict[str, Any]:
    """The defaults with the person's overrides (lengths in mm; ``max_risers_per_flight`` a count). Every value is checked."""
    out = dict(RULES)
    if raw in (None, {}):
        return out
    if not isinstance(raw, Mapping):
        raise BadRequest("rules must map a rule name to its value")
    for key, value in raw.items():
        if key not in RULES:
            raise BadRequest(f"Unknown stair rule '{key}'", details={"rules": sorted(RULES)})
        out[key] = int(number(value, key, 2, MAX_RISERS)) if key == "max_risers_per_flight" else number(value, key, 1, 10000) * to_m
    if out["riser_min"] >= out["riser_max"] or out["tread_min"] >= out["tread_max"] or out["comfort_min"] >= out["comfort_max"]:
        raise BadRequest("Each rule's minimum must be below its maximum")
    return out


def riser_interval(height: float, rules: Mapping[str, Any]) -> tuple[int, int]:
    """The whole numbers of risers that keep every riser inside its limits: ceil(H / r_max) <= N <= floor(H / r_min). Empty when the first exceeds the second."""
    eps = 1e-9
    return max(2, math.ceil(height / rules["riser_max"] - eps)), min(MAX_RISERS, math.floor(height / rules["riser_min"] + eps))


LAYOUTS = ("straight", "L", "U")
MIN_RISERS_TURNING = 4                                   # two flights of at least two risers each
WELL_GAP = 0.1                                           # m between the two flights of a U stair unless the person gives one


def _turning_split(n: int) -> list[int]:
    """The two flights of an L or U stair: as equal as a whole number allows (the first takes the odd riser)."""
    return [n - n // 2, n // 2] if n >= MIN_RISERS_TURNING else [n]


def _split(n: int, most: int) -> list[int]:
    flights = max(1, math.ceil(n / most))
    base, extra = divmod(n, flights)
    return [base + (1 if i < extra else 0) for i in range(flights)]


def design(height: float, *, rules: Mapping[str, Any] | None = None, risers: int | None = None, tread: float | None = None,
           available: float | None = None, layout: str = "straight", width: float | None = None) -> dict[str, Any]:
    """Choose a riser count and tread for a total rise (all lengths in metres). ``available`` is the horizontal length the stair may take, landings included.
    The answer states every candidate it considered and why a rejected one failed; an infeasible request says which limit makes it so and returns no
    stair. Deterministic: ties go to the lower riser count.

    ``layout`` is ``straight`` (equal flights in a line, a landing between them), or ``L`` / ``U``: exactly two flights and one landing, turned a quarter or a half turn.
    For those ``available`` is the length of the first arm, the first flight plus the landing, whose depth is the larger of the landing-depth rule and the stair ``width``."""
    if layout not in LAYOUTS:
        raise BadRequest(f"layout must be one of {', '.join(LAYOUTS)}")
    turning = layout != "straight"
    rules = dict(rules or RULES)
    if not math.isfinite(height) or height <= 0.05:
        raise BadRequest("The total rise must be greater than 50 mm")
    lo, hi = riser_interval(height, rules)
    mid = (rules["comfort_min"] + rules["comfort_max"]) / 2
    counts = [int(risers)] if risers is not None else list(range(lo, hi + 1))
    cands = []
    for n in counts:
        r = height / n
        flights = _turning_split(n) if turning else _split(n, int(rules["max_risers_per_flight"]))
        landings = len(flights) - 1
        landing = max(rules["landing_depth"], width or 0.0) if turning else rules["landing_depth"]       # a turning landing is at least as deep as the stair is wide
        first_arm = (flights[0] - 1) if turning else None                      # treads in the first flight; the landing follows it
        room = None if available is None else available - landings * landing
        if tread is not None:
            t = tread
        elif room is not None and room > 0 and (first_arm if turning else n - len(flights)) > 0:
            t = room / (first_arm if turning else n - len(flights))             # one fewer tread than risers in every flight
        else:
            t = min(max(mid - 2 * r, rules["tread_min"]), rules["tread_max"])
        run = (n - len(flights)) * t
        length_used = (first_arm * t + landing) if turning else run + landings * landing
        checks = [
            {"name": "riser", "ok": rules["riser_min"] - 1e-9 <= r <= rules["riser_max"] + 1e-9, "value": r, "limit": [rules["riser_min"], rules["riser_max"]]},
            {"name": "tread", "ok": rules["tread_min"] - 1e-9 <= t <= rules["tread_max"] + 1e-9, "value": t, "limit": [rules["tread_min"], rules["tread_max"]]},
            {"name": "comfort 2R+T", "ok": rules["comfort_min"] - 1e-9 <= 2 * r + t <= rules["comfort_max"] + 1e-9, "value": 2 * r + t, "limit": [rules["comfort_min"], rules["comfort_max"]]},
            {"name": "risers per flight", "ok": max(flights) <= rules["max_risers_per_flight"], "value": max(flights), "limit": rules["max_risers_per_flight"]}]
        if turning:
            checks.append({"name": "two flights of at least 2 risers", "ok": len(flights) == 2 and min(flights) >= 2, "value": n, "limit": MIN_RISERS_TURNING})
        if available is not None:
            checks.append({"name": "fits the available length", "ok": length_used <= available + 1e-9, "value": length_used, "limit": available})
        cands.append({"risers": n, "riser": r, "tread": t, "run": run, "comfort": 2 * r + t, "flights": flights, "landings": landings, "landing_depth": landing, "layout": layout, "checks": checks,
                      "feasible": all(c["ok"] for c in checks), "score": abs(2 * r + t - mid)})
    good = sorted((c for c in cands if c["feasible"]), key=lambda c: (round(c["score"], 9), c["risers"]))
    out: dict[str, Any] = {"height": height, "rules": dict(rules), "riser_interval": [lo, hi], "candidates": cands[:30]}
    if good:
        out.update(status="feasible", chosen=good[0])
        return out
    reasons = []
    if lo > hi:
        reasons.append(f"No whole number of risers keeps the riser between {rules['riser_min'] * 1000:.0f} and {rules['riser_max'] * 1000:.0f} mm over a rise of {height * 1000:.0f} mm "
                       f"(it would need at least {lo} and at most {hi})")
    failed = sorted({c["name"] for cand in cands for c in cand["checks"] if not c["ok"]})
    if cands and not reasons:
        reasons.append("Every riser count in the allowed interval fails at least one check: " + ", ".join(failed))
    out.update(status="infeasible", chosen=None, reasons=reasons, failing_checks=failed)
    return out


def design_stair(project: Any, p: Mapping[str, Any], to_m: float) -> dict[str, Any]:
    """Create the flights a design calls for, using the ordinary stair element (and an ordinary floor for each landing): one straight stair when the
    risers fit in a flight, otherwise equal flights in a line with a landing between them. Nothing is created unless the design is feasible."""
    level = project.level(text(p.get("level"), "level", 80))
    levels = sorted(project.manifest["levels"], key=lambda l: l["elevation"])
    top = project.level(text(p["top_level"], "top_level", 80)) if p.get("top_level") else next((l for l in levels if l["elevation"] > level["elevation"] + 1e-9), None)
    if top is None:
        raise BadRequest("There is no level above this one for the stair to reach")
    height = float(top["elevation"]) - float(level["elevation"])
    pts = []
    for key in ("start", "end"):
        q = p.get(key)
        if not isinstance(q, (list, tuple)) or len(q) != 2:
            raise BadRequest(f"{key} must be a point [x, y]")
        pts.append((float(number(q[0], "x", -1e9, 1e9)) * to_m, float(number(q[1], "y", -1e9, 1e9)) * to_m))
    length = math.dist(*pts)
    if length < 0.05:
        raise BadRequest("The start and end points are too close together")
    rules = rules_from(p.get("rules"))
    layout = p.get("layout", "straight")
    width = number(p.get("width", 1000), "width", WIDTH_RANGE[0] * 1000, WIDTH_RANGE[1] * 1000) / 1000.0
    d = design(height, rules=rules, risers=int(number(p["risers"], "risers", 2, MAX_RISERS)) if p.get("risers") is not None else None,
               tread=number(p["tread"], "tread", 50, 2000) / 1000.0 if p.get("tread") is not None else None, available=length, layout=layout, width=width)
    if d["status"] != "feasible":
        raise BadRequest("No stair fits: " + "; ".join(d["reasons"]), details={"design": d})
    best = d["chosen"]
    ux, uy = (pts[1][0] - pts[0][0]) / length, (pts[1][1] - pts[0][1]) / length
    if layout != "straight":
        return _build_turning(project, p, to_m, level, top, pts[0], (ux, uy), width, rules, d, layout)
    made: list[str] = []
    landings: list[str] = []
    s, climbed = 0.0, 0
    for i, n in enumerate(best["flights"]):
        run = (n - 1) * best["tread"]
        a = (pts[0][0] + ux * s, pts[0][1] + uy * s)
        b = (a[0] + ux * run, a[1] + uy * run)
        oid = add_stair(project, {"level": level["id"], "top_level": top["id"], "start": [a[0] / to_m, a[1] / to_m], "end": [b[0] / to_m, b[1] / to_m], "risers": n,
                                  "tread": best["tread"] * 1000, "width": width * 1000, "material": p.get("material", "Concrete"), "name": p.get("name") or "Stair"}, to_m)
        E._attach(project, oid, {"base_offset": climbed * best["riser"], "top_offset": -(sum(best["flights"]) - climbed - n) * best["riser"],
                                 "design": {"flight": i + 1, "flights": len(best["flights"]), "riser": best["riser"], "rules": rules}})
        regenerate(project, oid)
        made.append(oid)
        climbed += n
        s += run
        if i < len(best["flights"]) - 1:
            lx, ly = pts[0][0] + ux * s, pts[0][1] + uy * s
            nx, ny = -uy * width / 2, ux * width / 2
            depth = rules["landing_depth"]
            corners = [[(lx + nx) / to_m, (ly + ny) / to_m], [(lx + ux * depth + nx) / to_m, (ly + uy * depth + ny) / to_m],
                       [(lx + ux * depth - nx) / to_m, (ly + uy * depth - ny) / to_m], [(lx - nx) / to_m, (ly - ny) / to_m]]
            fl = E.add_floor(project, {"level": level["id"], "points": corners, "thickness": 150, "name": "Stair landing"}, to_m)
            E._attach(project, fl, {"offset": climbed * best["riser"]})
            E.regenerate_floor(project, fl)
            landings.append(fl)
            s += depth
    return {"flights": made, "landings": landings, "design": {k: best[k] for k in ("risers", "riser", "tread", "run", "comfort", "flights", "landings")},
            "riser_interval": d["riser_interval"]}


def _build_turning(project: Any, p: Mapping[str, Any], to_m: float, level: Mapping[str, Any], top: Mapping[str, Any], origin: tuple[float, float], u: tuple[float, float],
                   width: float, rules: Mapping[str, Any], d: Mapping[str, Any], layout: str) -> dict[str, Any]:
    """An L (quarter turn) or U (half turn) stair: two ordinary straight flights and one ordinary floor for the landing, laid out in the frame of the first flight (``u`` along it,
    ``v`` to its left) so the turn direction is a sign. The landing is ``max(landing_depth, width)`` long and the width of the stair (L) or of both flights and the well between
    them (U). Each flight starts exactly where its riser meets the landing, so the flights, the landing and the heights meet without a gap. Nothing is checked beyond that:
    clearance to walls, headroom and fire/egress rules are separate checks."""
    turn = p.get("turn", "left")
    if turn not in ("left", "right"):
        raise BadRequest("turn must be 'left' or 'right'")
    sigma = 1.0 if turn == "left" else -1.0
    gap = number(p.get("gap", WELL_GAP * 1000), "gap", 0, 5000) / 1000.0 if layout == "U" else 0.0
    best = d["chosen"]
    n1, n2 = best["flights"]
    t, r = best["tread"], best["riser"]
    run1, run2 = (n1 - 1) * t, (n2 - 1) * t
    lu = best["landing_depth"]
    v = (-u[1], u[0])
    at = lambda a, b: (origin[0] + u[0] * a + v[0] * b, origin[1] + u[1] * a + v[1] * b)         # noqa: E731  a along the first flight, b to its left, metres
    if layout == "L":
        start2, dir2 = at(run1 + lu - width / 2, sigma * width / 2), (sigma * v[0], sigma * v[1])
        across = (-width / 2, width / 2)
    else:
        start2, dir2 = at(run1, sigma * (width + gap)), (-u[0], -u[1])
        across = (-width / 2, width / 2 + gap + width) if sigma > 0 else (-(width / 2 + gap + width), width / 2)
    end1, end2 = at(run1, 0.0), (start2[0] + dir2[0] * run2, start2[1] + dir2[1] * run2)
    made: list[str] = []
    flights = [((origin, end1), n1, 0, -n2), (((start2), end2), n2, n1, 0)]
    for i, ((a, b), n, climbed, top_back) in enumerate(flights):
        oid = add_stair(project, {"level": level["id"], "top_level": top["id"], "start": [a[0] / to_m, a[1] / to_m], "end": [b[0] / to_m, b[1] / to_m], "risers": n,
                                  "tread": t * 1000, "width": width * 1000, "material": p.get("material", "Concrete"), "name": p.get("name") or "Stair"}, to_m)
        E._attach(project, oid, {"base_offset": climbed * r, "top_offset": top_back * r,
                                 "design": {"flight": i + 1, "flights": 2, "riser": r, "rules": rules, "layout": layout, "turn": turn}})
        regenerate(project, oid)
        made.append(oid)
    corners = [at(run1, across[0]), at(run1 + lu, across[0]), at(run1 + lu, across[1]), at(run1, across[1])]
    fl = E.add_floor(project, {"level": level["id"], "points": [[x / to_m, y / to_m] for x, y in corners], "thickness": 150, "name": "Stair landing"}, to_m)
    E._attach(project, fl, {"offset": n1 * r})
    E.regenerate_floor(project, fl)
    pts = [origin, end1, start2, end2, *corners]
    half = width / 2
    xs, ys = [q[0] for q in pts], [q[1] for q in pts]
    return {"layout": layout, "turn": turn, "flights": made, "landings": [fl],
            "design": {k: best[k] for k in ("risers", "riser", "tread", "run", "comfort", "flights", "landings")},
            "riser_interval": d["riser_interval"], "landing": {"depth": lu, "across": [across[0], across[1]], "height_above_base": n1 * r},
            "footprint_centre_lines_m": {"min": [min(xs), min(ys)], "max": [max(xs), max(ys)], "note": "bounding box of the centre lines and the landing; flights extend half the width beyond their centre lines"},
            "half_width_m": half, "gap_m": gap if layout == "U" else None}


# ------------------------------------------------------------------------------------------------------------------------- headroom
def _world_triangles(project: Any, oid: str) -> list[tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]] | None:
    obj = project.model.get(oid)
    geo = project.model.geometry(oid) if hasattr(project.model, "geometry") else obj.get("geometry")
    if not isinstance(geo, Mapping):
        return None
    if geo.get("kind") == "box":                                   # columns, beams and generated panels are boxes: twelve triangles
        cx, cy, cz = geo.get("center", [0, 0, 0])
        sx, sy, sz = (v / 2 for v in geo["size"])
        local = [(cx + a * sx, cy + b * sy, cz + c * sz) for a in (-1, 1) for b in (-1, 1) for c in (-1, 1)]
        quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
        geo = {"kind": "mesh", "vertices": local, "faces": [f for q in quads for f in ((q[0], q[1], q[2]), (q[0], q[2], q[3]))]}
    if geo.get("kind") != "mesh":
        return None
    t = obj.get("transform") or E.IDENTITY
    verts = [(t[0] * x + t[1] * y + t[2] * z + t[3], t[4] * x + t[5] * y + t[6] * z + t[7], t[8] * x + t[9] * y + t[10] * z + t[11]) for x, y, z in geo["vertices"]]
    return [(verts[a], verts[b], verts[c]) for a, b, c in geo["faces"]]


def _lowest_above(tris: Any, x: float, y: float, z: float) -> float | None:
    """The lowest height above ``z`` at which the vertical line through (x, y) meets a triangle."""
    best = None
    for (ax, ay, az), (bx, by, bz), (cx, cy, cz) in tris:
        d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(d) < 1e-12:
            continue
        l1 = ((by - cy) * (x - cx) + (cx - bx) * (y - cy)) / d
        l2 = ((cy - ay) * (x - cx) + (ax - cx) * (y - cy)) / d
        l3 = 1 - l1 - l2
        if min(l1, l2, l3) < -1e-9:
            continue
        h = l1 * az + l2 * bz + l3 * cz
        if h > z + 1e-9 and (best is None or h < best):
            best = h
    return best


def headroom(project: Any, oid: str, minimum: float | None = None) -> dict[str, Any]:
    """Clear height above every tread of a flight, measured with a vertical line against the real triangles of the floors, roofs, beams and other
    stairs above it (so a slab opening is respected). The sample points are the middle of each tread at the centre and both edges. Elements that
    are not meshes are not tested, and the result says how many."""
    f = frame(project, oid)
    need = float(minimum if minimum is not None else RULES["headroom"])
    c, s = math.cos(f["angle"]), math.sin(f["angle"])
    x0, y0 = f["start"]
    others = [i for i in E.element_ids(project) if i != oid and E.category(project, i) in ("floor", "roof", "structural", "stair")]
    tris, skipped = {}, 0
    for i in others:
        t = _world_triangles(project, i)
        if t is None:
            skipped += 1
        else:
            tris[i] = t
    worst: dict[str, Any] | None = None
    for k in range(f["risers"] - 1):
        z = f["base"] + (k + 1) * f["riser"]
        a = (k + 0.5) * f["tread"]
        for across in (-f["width"] / 2 + 0.01, 0.0, f["width"] / 2 - 0.01):
            x, y = x0 + c * a - s * across, y0 + s * a + c * across
            for i, tr in tris.items():
                h = _lowest_above(tr, x, y, z)
                if h is not None and (worst is None or h - z < worst["clearance"]):
                    worst = {"clearance": h - z, "tread": k + 1, "element": i, "point": [x, y]}
    ok = worst is None or worst["clearance"] >= need - 1e-9
    return {"stair": oid, "required": need, "ok": ok, "minimum_clearance": None if worst is None else worst["clearance"], "worst": worst,
            "elements_tested": len(tris), "elements_not_tested": skipped,
            "basis": "vertical line through the middle of each tread (centre and both edges) against the triangles of floors, roofs, structural members and other stairs"}
