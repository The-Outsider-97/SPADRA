"""The engineering workspace's calculator (master prompt 12.1, 12.2): unit conversion, parameter formulas, constraint checks and measurements.

Everything numerical is done by existing code:

* unit conversion, expressions with units and constraint checks are the **STEM Agent**'s (``units``, ``parametric`` domains) - SPADRA only chooses
  the operation, hands over what you typed and reports the answer, with the agent's own errors when it refuses (an unknown unit, a dimension mismatch);
* the measurements of an element (volume, surface area, mass, centre, area and perimeter of a room) come from SGEO and the model's own parameters
  through the quantities take-off, and distances between elements come from the **Spatial Agent**.

The engine's unit system is the SI units with their prefixes (m, mm, km, kg, s, N, kN, Pa, MPa, GPa, J, W, Hz …). It has no imperial units and no
compound unit symbols, and SPADRA does not add them: a unit the engine does not know is refused with the list of the ones it does.

Each result carries the model revision it was computed for (the app marks it out of date once the model changes) and the agent request that made it.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from ..tools.errors import BadRequest, DependencyMissing
from ..tools.validation import number, text

from . import elements as E
from . import rooms as R
from .agent_hub import HUB
from .engineering_bridge import BRIDGE as STEM
from .spatial_bridge import BRIDGE as SPATIAL

MAX_EXPRESSIONS = 32
MAX_PARAMETERS = 64
NOTE = "Computed by the SLAI STEM Agent from exactly the values and units shown. A formula result is only as meaningful as the formula you wrote."


def status() -> dict[str, Any]:
    if not STEM.available:
        return {"available": False, "reason": STEM.reason}
    known = STEM.task("units", "known_units")["result"]
    return {"available": True, "units": sorted(known), "prefixes": "SI prefixes are accepted (k, M, G, m, c, µ …)", "agent": "SLAI STEM Agent"}


def _require() -> None:
    if not STEM.available:
        raise DependencyMissing("The engineering calculator needs the SLAI STEM Agent", details={"reason": STEM.reason})


def _quantity(value: Any, label: str) -> dict[str, Any] | float:
    """A parameter is a number, or a number with a unit: ``{"value": 3, "unit": "m"}``."""
    if isinstance(value, Mapping):
        if set(value) - {"value", "unit"} or "value" not in value:
            raise BadRequest(f"{label}: give a number, or a number with a unit")
        v = float(number(value["value"], label, -1e150, 1e150))
        unit = text(value.get("unit", "") or "1", f"{label} unit", 20)
        return {"value": v, "unit": unit}
    return float(number(value, label, -1e150, 1e150))


def _clean(quantity: Mapping[str, Any]) -> dict[str, Any]:
    unit = quantity.get("unit") or {}
    magnitude = quantity.get("magnitude")
    si = None if magnitude is None else magnitude * float(unit.get("scale", 1.0)) + float(unit.get("offset", 0.0))
    return {"value": magnitude, "unit": unit.get("symbol"), "si_value": si, "dimension": unit.get("dimension") or {}}


def convert(project: Any | None, p: Mapping[str, Any]) -> dict[str, Any]:
    _require()
    value = float(number(p.get("value"), "value", -1e150, 1e150))
    source, target = text(p.get("from"), "from", 20), text(p.get("to"), "to", 20)
    out = STEM.task("units", "convert", args=[value, source, target])
    return {"kind": "conversion", "input": {"value": value, "unit": source}, "result": {"value": float(out["result"]), "unit": target}, "agent": "SLAI STEM Agent",
            "request_id": out["request_id"], "revision": None if project is None else project.revision, "note": NOTE}


def evaluate(project: Any | None, p: Mapping[str, Any]) -> dict[str, Any]:
    """Named formulas over named parameters, with units, evaluated in dependency order by the agent."""
    _require()
    formulas = p.get("formulas")
    if not isinstance(formulas, Mapping) or not 1 <= len(formulas) <= MAX_EXPRESSIONS:
        raise BadRequest(f"Give between 1 and {MAX_EXPRESSIONS} formulas as name: expression")
    raw = p.get("parameters") or {}
    if not isinstance(raw, Mapping) or len(raw) > MAX_PARAMETERS:
        raise BadRequest(f"Give at most {MAX_PARAMETERS} parameters")
    params = {text(k, "parameter name", 40): _quantity(v, f"parameter {k}") for k, v in raw.items()}
    exprs = {text(k, "formula name", 40): text(v, f"formula {k}", 500) for k, v in formulas.items()}
    out = STEM.task("parametric", "evaluate_serializable", args=[exprs, params])
    HUB.record("STEM Agent", "parametric.evaluate_serializable", out["request_id"])
    return {"kind": "formulas", "inputs": {"formulas": exprs, "parameters": params}, "results": {k: _clean(v) for k, v in out["result"].items()}, "agent": "SLAI STEM Agent",
            "request_id": out["request_id"], "revision": None if project is None else project.revision, "note": NOTE}


def check_constraints(project: Any | None, p: Mapping[str, Any]) -> dict[str, Any]:
    """Inequalities and equalities against stated parameters, with dimensions checked (the agent checks feasibility; it does not solve).

    The engine's expressions do not take unit literals, so a limit with a unit is given as a quantity:
    ``{"left": "e1_height", "op": "<=", "right": {"value": 4, "unit": "m"}}``; a plain string such as ``"a + b <= c"`` is accepted when every name in it is a parameter.
    """
    _require()
    rules = p.get("constraints")
    if not isinstance(rules, Sequence) or isinstance(rules, (str, bytes)) or not 1 <= len(rules) <= 64:
        raise BadRequest("Give between 1 and 64 constraints, for example height <= limit")
    raw = p.get("parameters") or {}
    if not isinstance(raw, Mapping) or len(raw) > MAX_PARAMETERS:
        raise BadRequest(f"Give at most {MAX_PARAMETERS} parameters")
    params = {text(k, "parameter name", 40): _quantity(v, f"parameter {k}") for k, v in raw.items()}
    sent: list[str] = []
    for n, rule in enumerate(rules, 1):
        if isinstance(rule, Mapping):
            if set(rule) - {"left", "op", "right"}:
                raise BadRequest(f"Constraint {n}: use left, op and right")
            op = text(rule.get("op"), "op", 3)
            if op not in ("<", "<=", ">", ">=", "=="):
                raise BadRequest(f"Constraint {n}: the comparison must be one of < <= > >= ==")
            right = rule.get("right")
            if isinstance(right, (Mapping, int, float)) and not isinstance(right, bool):
                name = f"limit_{n}"
                params[name] = _quantity(right, f"constraint {n} limit")
                right = name
            sent.append(f"{text(rule.get('left'), 'left', 150)} {op} {text(right, 'right', 150)}")
        else:
            sent.append(text(rule, "constraint", 300))
    out = STEM.task("parametric", "validate_constraints", args=[sent, params])
    HUB.record("STEM Agent", "parametric.validate_constraints", out["request_id"])
    return {"kind": "constraints", "inputs": {"constraints": sent, "parameters": params}, "feasible": bool(out["result"]["feasible"]), "results": out["result"]["constraints"],
            "agent": "SLAI STEM Agent", "request_id": out["request_id"], "revision": None if project is None else project.revision, "note": NOTE}


# ------------------------------------------------------------------------------------------------------------------------ the model
def element_parameters(project: Any, ids: Sequence[str]) -> dict[str, Any]:
    """The selected elements' parameters as named quantities (e1_height, e1_thickness …) ready to use in a formula."""
    if not ids or len(ids) > 12:
        raise BadRequest("Select between 1 and 12 elements")
    rows: list[dict[str, Any]] = []
    params: dict[str, Any] = {}
    unit_symbol = {"mm": "mm", "m": "m", "°": None}
    for i, oid in enumerate(ids, 1):
        props = project.properties({"model": oid})
        label = props.get("title") or oid
        for r in [*props.get("top", []), *[x for g in props.get("groups", []) for x in g["rows"]]]:
            key, unit, value = r.get("key"), r.get("unit"), r.get("value")
            if r.get("type") == "number" and key and isinstance(value, (int, float)) and unit in unit_symbol and unit_symbol[unit]:
                name = f"e{i}_{key}"
                params[name] = {"value": float(value), "unit": unit_symbol[unit]}
                rows.append({"name": name, "element": oid, "label": f"{label} - {r['label']}", "value": float(value), "unit": unit})
    return {"revision": project.revision, "parameters": params, "rows": rows}


def measure(project: Any, ids: Sequence[str]) -> dict[str, Any]:
    """Geometric figures of one element (from SGEO and the element's own parameters), or the relation between two (from the Spatial Agent)."""
    ids = [text(i, "id", 80) for i in ids]
    if len(ids) == 1:
        oid = ids[0]
        meta = E.meta_of(project, oid)
        category = E.m(meta, "category")
        figures: list[dict[str, Any]] = []
        if category == "room":
            d = R.data(project, oid)
            figures += [{"label": "Area", "value": d["area"], "unit": "m²", "basis": "from the room outline (shoelace formula on its corners)"},
                        {"label": "Perimeter", "value": d["perimeter"], "unit": "m", "basis": "sum of the outline's edge lengths"},
                        {"label": "Volume", "value": d["volume"], "unit": "m³", "basis": R.basis_text(d)}]
        else:
            q = project.quantities([oid], None)["rows"][0]
            if q.get("volume_m3") is not None:
                figures.append({"label": "Volume", "value": q["volume_m3"], "unit": "m³", "basis": "SGEO, openings taken out"})
            if q.get("surface_area_m2") is not None:
                figures.append({"label": "Surface area", "value": q["surface_area_m2"], "unit": "m²", "basis": "SGEO"})
            if q.get("mass_kg") is not None:
                figures.append({"label": "Mass", "value": q["mass_kg"], "unit": "kg", "basis": f"volume × {q.get('density_kg_m3'):g} kg/m³ ({q.get('density_source')})"})
            if q.get("centroid_m") is not None:
                figures.append({"label": "Centre", "value": q["centroid_m"], "unit": "m", "basis": "volume-weighted centroid" + (" (centre of mass)" if q.get("mass_kg") is not None else "")})
            props = project.properties({"model": oid})
            for r in [*props.get("top", []), *[x for g in props.get("groups", []) for x in g["rows"]]]:
                if r.get("label") in ("Length", "Plan area", "Area") and isinstance(r.get("value"), str):
                    figures.append({"label": r["label"], "value": r["value"], "unit": "", "basis": "the element's own parameters"})
        return {"kind": "measure", "revision": project.revision, "elements": [{"id": oid, "name": project.model.get(oid)["name"], "category": category}], "figures": figures}
    if len(ids) == 2:
        for oid in ids:
            if E.m(E.meta_of(project, oid), "category") == "room":
                raise BadRequest("A room is a floor outline without solid geometry; select two solid elements to measure between them")
        pair = SPATIAL.between(project, ids[0], ids[1])
        return {"kind": "measure", "revision": project.revision, "elements": [pair["a"], pair["b"]], "agent": pair["agent"], "basis": pair["basis"],
                "figures": [{"label": "Distance between centres", "value": pair["centre_distance_m"], "unit": "m", "basis": "Spatial Agent"},
                            {"label": "Gap between bounding boxes", "value": pair["box_gap_m"], "unit": "m", "basis": "Spatial Agent (0 when they touch or overlap)"},
                            {"label": "Bounding boxes collide", "value": pair["boxes_collide"], "unit": "", "basis": "Spatial Agent"},
                            {"label": "Relations", "value": ", ".join(pair["relations"]) or "none", "unit": "", "basis": "Spatial Agent, on bounding boxes"}], "note": pair["note"]}
    raise BadRequest("Select one element (its figures) or two (the distance between them)")
