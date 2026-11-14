"""Formula-backed BIM property edits using SGEO's existing safe, unit-aware evaluator.

Only editable numeric properties advertised by the server are eligible. Formulas and
their resolved values travel with the element's SGEO metadata; model transactions
revalidate all dependencies atomically before a command is committed.
"""
from __future__ import annotations

import math
import re
from typing import Any, Mapping

from src.utils.spatial_formats.sgeo import parameters as SP
from src.utils.spatial_formats.common.errors import SpatialFormatError

from ..tools.errors import BadRequest
from . import elements as E

MAX_FORMULAS_PER_ELEMENT = 40
_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_NESTED = re.compile(r"^(?:layer|panel|end):[A-Za-z0-9_:-]+$")
_LITERAL = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_UNIT_LITERAL = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\s*(?:mm|cm|m|deg|rad|%)$")
_UNITS = {"mm": ("length", 1000.0), "cm": ("length", 100.0), "m": ("length", 1.0),
          "°": ("angle", 180.0 / math.pi), "deg": ("angle", 180.0 / math.pi),
          "%": ("number", 1.0), "": ("number", 1.0)}


def is_formula(value: Any) -> bool:
    """Recognize expressions in numeric fields without changing ordinary numbers."""
    if not isinstance(value, str):
        return False
    s = value.strip()
    return bool(s) and (s.startswith("=") or not _LITERAL.fullmatch(s))


def _rows(project: Any, oid: str) -> dict[str, Mapping[str, Any]]:
    props = E.properties(project, oid)
    rows = props["top"] + [row for group in props["groups"] for row in group["rows"]]
    if E.category(project, oid) == "curtainwall":
        from . import curtainwall as CW
        for cell in CW.cells(CW.spec(E.meta_of(project, oid)))[:60]:
            rows.extend(CW.component_properties(project, oid, "cell:" + cell["key"])["top"])
    return {row["key"]: row for row in rows if row.get("type") == "number" and
            isinstance(row.get("value"), (float, int)) and row.get("key") and
            (_KEY.fullmatch(row["key"]) or _NESTED.fullmatch(row["key"])) and row.get("unit", "") in _UNITS}


def _alias(key: str) -> str:
    """Stable SGEO-compatible name for a nested dimension key (e.g. panel_0_0_thickness)."""
    return re.sub(r"[^A-Za-z0-9_]", "_", key)


def _context(project: Any, oid: str) -> tuple[dict[str, Mapping[str, Any]], dict[str, SP.Quantity]]:
    fields = _rows(project, oid)
    variables: dict[str, SP.Quantity] = {}
    for key, row in fields.items():
        kind, scale = _UNITS[row.get("unit", "")]
        value = SP.Quantity(float(row["value"]) / scale, SP.PARAMETER_DIMENSIONS[kind])
        variables[key] = value
        if _alias(key) not in variables:
            variables[_alias(key)] = value
    if "pitch" in variables:
        variables["slopeAngle"] = variables["pitch"]
    from .relations import effective_custom, CUSTOM_UNITS
    for name, definition in effective_custom(project, oid).items():
        value, raw = definition["type"], definition["value"]
        if value in ("length", "area", "volume", "angle", "number", "integer"):
            if isinstance(raw, (int, float)) and name not in variables:
                unit = CUSTOM_UNITS.get(value, "")
                kind, scale = _UNITS.get(unit, (value, 1.0))
                variables[name] = SP.Quantity(float(raw) / scale, SP.PARAMETER_DIMENSIONS[kind])
    return fields, variables


def _ordered(formulas: Mapping[str, str], valid: set[str]) -> list[str]:
    if len(formulas) > MAX_FORMULAS_PER_ELEMENT:
        raise BadRequest(f"An element supports at most {MAX_FORMULAS_PER_ELEMENT} formula-driven dimensions")
    dependencies: dict[str, set[str]] = {}
    for key, expr in formulas.items():
        if key not in valid:
            raise BadRequest(f"'{key}' is no longer an editable numeric parameter")
        try:
            uses = SP.expression_dependencies(expr)
        except SpatialFormatError as exc:
            raise BadRequest(f"Formula for {key}: {exc}") from exc
        unknown = uses - valid - {"slopeAngle"}
        if unknown:
            raise BadRequest(f"Formula for {key} refers to unknown parameter(s): {', '.join(sorted(unknown))}")
        dependencies[key] = {target for target in formulas if target in uses or _alias(target) in uses}
        if "slopeAngle" in uses and "pitch" in formulas:
            dependencies[key].add("pitch")
    pending, order = dict(dependencies), []
    while pending:
        ready = sorted(k for k, deps in pending.items() if not deps)
        if not ready:
            raise BadRequest("Circular parameter formulas: " + ", ".join(sorted(pending)))
        order.extend(ready)
        for key in ready:
            pending.pop(key)
        for deps in pending.values():
            deps.difference_update(ready)
    return order


def _display(q: SP.Quantity, row: Mapping[str, Any], name: str) -> float:
    kind, scale = _UNITS[row.get("unit", "")]
    if q.dimension != SP.PARAMETER_DIMENSIONS[kind] or isinstance(q.value, bool):
        raise BadRequest(f"Formula for {name} must produce {kind}, not {SP.dimension_name(q.dimension)}")
    value = float(q.value) * scale
    if not math.isfinite(value) or abs(value) > 1e12:
        raise BadRequest(f"Formula for {name} produced an invalid number")
    return value


def set_formula(project: Any, oid: str, key: str, raw: str) -> None:
    fields, variables = _context(project, oid)
    if key not in fields:
        raise BadRequest(f"'{key}' does not support parametric formulas")
    expr = raw.strip().removeprefix("=").strip()
    if not expr:
        raise BadRequest("Enter a formula after =")
    formulas = dict(E.m(E.meta_of(project, oid), "formulas") or {})
    formulas[key] = expr
    _ordered(formulas, set(variables))
    project.manifest["has_property_formulas"] = True
    E._attach(project, oid, {"formulas": formulas})
    recompute(project, oid)


def remove_formula(project: Any, oid: str, key: str) -> None:
    formulas = dict(E.m(E.meta_of(project, oid), "formulas") or {})
    if key in formulas:
        formulas.pop(key)
        E._attach(project, oid, {"formulas": formulas})


def recompute(project: Any, oid: str) -> None:
    formulas = E.m(E.meta_of(project, oid), "formulas") or {}
    if not formulas:
        return
    fields, variables = _context(project, oid)
    for key in _ordered(formulas, set(variables)):
        try:
            result = SP.evaluate_expression(formulas[key], variables)
        except SpatialFormatError as exc:
            raise BadRequest(f"Formula for {key}: {exc}") from exc
        value = _display(result, fields[key], key)
        if not math.isclose(float(fields[key]["value"]), value, rel_tol=1e-10, abs_tol=1e-7):
            E.set_property(project, oid, key, value, from_formula=True)
            fields, variables = _context(project, oid)
        variables[key] = result
        variables[_alias(key)] = result
        if key == "pitch":
            variables["slopeAngle"] = result


def recompute_all(project: Any) -> None:
    if not project.manifest.get("has_property_formulas"):
        return
    for oid in E.element_ids(project):
        if E.m(E.meta_of(project, oid), "formulas"):
            recompute(project, oid)


# Conditional appearance uses the SAME SGEO parser and quantity context as dimensions;
# color values and spatial transitions remain typed settings, not executable strings.
_COLOR = re.compile(r"#[0-9A-Fa-f]{6}\Z")
_RULE_DEFAULTS: dict[str, Any] = {"enabled": "false", "condition": "", "mode": "solid",
                                "top": "#BFBFC7", "bottom": "#BFBFC7", "transition": "fade",
                                "position": 50.0, "fade_width": 100.0}


def _default_rule(project: Any, meta: Mapping[str, Any]) -> dict[str, Any]:
    material = E.m(meta, "material")
    base = E.m(meta, "color") or project.manifest.get("material_colors", {}).get(material) or E.MATERIAL_COLORS.get(material, "#BFBFC7")
    return {**_RULE_DEFAULTS, "top": base, "bottom": base}


def _rule_group(settings: Mapping[str, Any], prefix: str, title: str) -> dict[str, Any]:
    def choice(field: str, label: str, items: tuple[str, ...]) -> dict[str, Any]:
        return {"key": f"{prefix}:{field}", "label": label, "type": "choice", "value": settings[field],
                "choices": [{"value": item, "label": item.capitalize()} for item in items]}
    return {"title": title, "collapsed": True, "rows": [
        choice("enabled", "Rule enabled", ("false", "true")),
        {"key": f"{prefix}:condition", "label": "Condition (=formula)", "type": "text", "value": settings["condition"]},
        choice("mode", "Appearance", ("solid", "gradient")),
        {"key": f"{prefix}:top", "label": "Color / top color", "type": "color", "value": settings["top"]},
        {"key": f"{prefix}:bottom", "label": "Bottom color", "type": "color", "value": settings["bottom"]},
        choice("transition", "Transition", ("fade", "abrupt")),
        {"key": f"{prefix}:position", "label": "Boundary position", "type": "number", "unit": "%", "value": settings["position"]},
        {"key": f"{prefix}:fade_width", "label": "Fade range", "type": "number", "unit": "%", "value": settings["fade_width"]},
    ]}


def material_rule_group(project: Any, meta: Mapping[str, Any]) -> dict[str, Any]:
    settings = {**_default_rule(project, meta), **(E.m(meta, "material_rule") or {})}
    return _rule_group(settings, "material_rule", "Conditional material appearance (instance)")


def scoped_material_groups(project: Any, oid: str) -> list[dict[str, Any]]:
    meta = E.meta_of(project, oid)
    from . import layers as LY
    layers = LY.stack(project, oid) if E.category(project, oid) in LY.LAYERED else []
    groups = []
    for n, lay in enumerate(layers, 1):
        material = lay["material"]
        base = project.manifest.get("material_colors", {}).get(material) or E.MATERIAL_COLORS.get(material, "#BFBFC7")
        rule = (E.m(meta, "layer_material_rules") or {}).get(lay["id"]) or {}
        groups.append(_rule_group({**_RULE_DEFAULTS, "top": base, "bottom": base, **rule},
                                  f"material_layer:{lay['id']}", f"Conditional appearance: layer {n} ({lay['name']})"))
    materials = sorted({*([E.m(meta, "material")] if E.m(meta, "material") else []), *(lay["material"] for lay in layers)} - {None})
    for material in materials:
        base = project.manifest.get("material_colors", {}).get(material) or E.MATERIAL_COLORS.get(material, "#BFBFC7")
        rule = project.manifest.get("material_rules", {}).get(material) or {}
        groups.append(_rule_group({**_RULE_DEFAULTS, "top": base, "bottom": base, **rule},
                                  f"material_def:{material}", f"Shared material definition: {material}"))
    return groups


def _condition(project: Any, oid: str, expression: str) -> bool:
    _, variables = _context(project, oid)
    try:
        result = SP.evaluate_expression(expression.removeprefix("=").strip(), variables)
    except SpatialFormatError as exc:
        raise BadRequest(f"Material condition: {exc}") from exc
    if not isinstance(result.value, bool) or result.dimension != SP.PARAMETER_DIMENSIONS["boolean"]:
        raise BadRequest("Material condition must evaluate to true or false")
    return result.value


def _validated_rule(project: Any, oid: str, field: str, value: Any, rule: dict[str, Any], default: dict[str, Any]) -> dict[str, Any]:
    if field not in _RULE_DEFAULTS:
        raise BadRequest(f"Unknown material rule setting '{field}'")
    if field in ("enabled", "mode", "transition"):
        allowed = {"enabled": ("true", "false"), "mode": ("solid", "gradient"), "transition": ("fade", "abrupt")}[field]
        if value not in allowed:
            raise BadRequest(f"{field} must be one of {', '.join(allowed)}")
    elif field in ("top", "bottom"):
        if value == "":
            # The existing color picker has a reset-to-inherited action.
            value = default[field]
        if not isinstance(value, str) or not _COLOR.fullmatch(value):
            raise BadRequest("Rule colors must be six-digit HEX values (e.g. #123ABC)")
        value = value.upper()
    elif field in ("position", "fade_width"):
        from ..tools.validation import number
        value = number(value, field, 0, 100)
    elif field == "condition":
        if not isinstance(value, str) or len(value) > SP.MAX_EXPR_LENGTH:
            raise BadRequest("Material condition must be text (maximum 512 characters)")
    rule[field] = value
    if rule["enabled"] == "true":
        if not str(rule["condition"]).strip():
            raise BadRequest("Enter a condition before enabling this material rule")
        _condition(project, oid, rule["condition"])
    return rule


def set_material_rule(project: Any, oid: str, field: str, value: Any) -> None:
    meta = E.meta_of(project, oid)
    default = _default_rule(project, meta)
    rule = _validated_rule(project, oid, field, value, {**default, **(E.m(meta, "material_rule") or {})}, default)
    E._attach(project, oid, {"material_rule": rule})


def set_scoped_material_rule(project: Any, oid: str, key: str, value: Any) -> None:
    kind, sep, remainder = key.partition(":")
    target, sep, field = remainder.rpartition(":")
    if not sep:
        raise BadRequest("Specify a material layer or material definition and a field")
    meta = E.meta_of(project, oid)
    from . import layers as LY
    layers = LY.stack(project, oid) if E.category(project, oid) in LY.LAYERED else []
    if kind == "material_layer":
        lay = next((l for l in layers if l["id"] == target), None)
        if lay is None:
            raise BadRequest("The selected material layer no longer exists")
        material = lay["material"]
        current = dict(E.m(meta, "layer_material_rules") or {})
        owned = current.get(target) or {}
    elif kind == "material_def":
        available = {E.m(meta, "material"), *(l["material"] for l in layers)}
        if target not in available:
            raise BadRequest("This element does not use the selected material definition")
        material = target
        current = project.manifest.setdefault("material_rules", {})
        owned = current.get(target) or {}
    else:
        raise BadRequest("Unknown material scope")
    base = project.manifest.get("material_colors", {}).get(material) or E.MATERIAL_COLORS.get(material, "#BFBFC7")
    default = {**_RULE_DEFAULTS, "top": base, "bottom": base}
    rule = _validated_rule(project, oid, field, value, {**default, **owned}, default)
    current[target] = rule
    if kind == "material_layer":
        E._attach(project, oid, {"layer_material_rules": current})


def active_rule(project: Any, oid: str, rule: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(rule, dict) or rule.get("enabled") != "true":
        return None
    return dict(rule) if _condition(project, oid, str(rule.get("condition") or "")) else None


def scoped_layer_appearance(project: Any, oid: str, meta: Mapping[str, Any], count: int,
                            colors: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any] | None]] | None:
    """Per-layer material precedence without cloning or recoloring adjacent layers."""
    from . import layers as LY
    if E.category(project, oid) not in LY.LAYERED:
        return None
    layers = LY.stack(project, oid)
    if len(layers) == 1 and not colors:
        lay = layers[0]
        base = E.m(meta, "color") or lay.get("color") or project.manifest.get("material_colors", {}).get(lay["material"]) or E.MATERIAL_COLORS.get(lay["material"])
        colors = [{"count": count, "color": base}]
    if len(colors) != len(layers) or sum(c["count"] for c in colors) != count:
        return None
    current = dict(E.m(meta, "layer_material_rules") or {})
    own = active_rule(project, oid, E.m(meta, "material_rule"))
    result, gradients = [], []
    for lay, c in zip(layers, colors):
        selected = (active_rule(project, oid, current.get(lay["id"])) or own or
                    active_rule(project, oid, project.manifest.get("material_rules", {}).get(lay["material"])))
        result.append({**c, "color": selected["top"] if selected and selected["mode"] == "solid" else c["color"]})
        gradients.append(selected if selected and selected["mode"] == "gradient" else None)
    return result, gradients


def appearance_rule(project: Any, meta: Mapping[str, Any], oid: str | None = None) -> dict[str, Any] | None:
    if oid is None:
        return None
    return active_rule(project, oid, E.m(meta, "material_rule")) or active_rule(project, oid, project.manifest.get("material_rules", {}).get(E.m(meta, "material")))
