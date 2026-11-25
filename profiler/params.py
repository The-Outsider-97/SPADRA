"""Parameter Manager: typed, scoped, unit-aware parameters over SGEO's safe expression engine.

Nothing here evaluates code: formulas go through ``sgeo.parameters.ParameterGraph`` (an allow-listed AST evaluator with physical dimensions, cycle
detection and bounds), the same engine SPADRA's families use. Internal values are SI there; this module converts at the boundary so a person sees
the document unit (length) and degrees (angle). A parameter is *either* a literal value *or* a formula; the person picks which (authoritative PDF p.6).
"""
from __future__ import annotations

import math
from typing import Any, Mapping

from src.utils.spatial_formats.common import units as U
from src.utils.spatial_formats.common.errors import ValidationError
from src.utils.spatial_formats.common.ids import new_id
from src.utils.spatial_formats.sgeo import parameters as P

from . import dims as D
from .errors import ProfilerConflict, ProfilerError, ProfilerNotFound

TYPES = ("length", "angle", "number", "integer", "boolean")
SCOPES = ("document", "profile", "instance", "type")


def _factor(sk, ptype: str) -> float:
    if ptype == "length":
        return U.METERS_PER_UNIT[sk.unit]
    if ptype == "angle":
        return math.pi / 180.0
    return 1.0


def to_si(sk, ptype: str, value: float) -> float:
    return float(value) * _factor(sk, ptype)


def from_si(sk, ptype: str, value: float) -> float:
    """Back to the sketch's unit. Rounded to 12 significant digits: the metre round trip otherwise leaves 79.99999999999999 where 80 was meant."""
    return float("%.12g" % (float(value) / _factor(sk, ptype)))


def _literal(node):
    """The value of an expression made only of numbers, or None."""
    import ast
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        v = _literal(node.operand)
        return None if v is None else (v if isinstance(node.op, ast.UAdd) else -v)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
        a, b = _literal(node.left), _literal(node.right)
        if a is None or b is None:
            return None
        if isinstance(node.op, ast.Add):
            return a + b
        if isinstance(node.op, ast.Sub):
            return a - b
        if isinstance(node.op, ast.Mult):
            return a * b
        return a / b if b else None
    return None


def localize(sk, expr: str) -> str:
    """Let a bare number added to or subtracted from a length (or an angle) mean the document unit: in a millimetre profile ``w - 40`` is ``w - 40 mm``.
    The expression engine keeps numbers dimensionless (so ``w * 2`` stays a factor); only the one case that would otherwise be a unit error is rewritten."""
    import ast
    if not expr or not isinstance(expr, str) or "\n" in expr:
        return expr
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError:
        return expr
    text = expr.strip()
    types = {n: p["type"] for n, p in sk.d["params"].items()}
    edits: list[tuple[int, int, str]] = []

    def kind(node) -> str:
        if _literal(node) is not None:
            return "num"
        if isinstance(node, ast.Name):
            t = types.get(node.id)
            return t if t in ("length", "angle") else ("num" if t in ("number", "integer") else "?")
        if isinstance(node, ast.UnaryOp):
            return kind(node.operand)
        if isinstance(node, ast.BinOp):
            a, b = kind(node.left), kind(node.right)
            if isinstance(node.op, (ast.Add, ast.Sub)):
                if a == b:
                    return a
                if "num" in (a, b) and (a in ("length", "angle") or b in ("length", "angle")):
                    unit = a if a != "num" else b
                    lit = node.right if b == "num" else node.left
                    v = _literal(lit)
                    if v is not None and lit.end_lineno == lit.lineno:
                        edits.append((lit.col_offset, lit.end_col_offset, "%.12g %s" % (v, sk.unit if unit == "length" else "deg")))
                    return unit
                return "?"
            if isinstance(node.op, ast.Mult):
                return a if b == "num" else b if a == "num" else "?"
            if isinstance(node.op, ast.Div):
                return a if b == "num" else "num" if a == b and a != "?" else "?"
        return "?"

    for n in ast.walk(tree):
        if isinstance(n, ast.BinOp):
            kind(n)
    for lo, hi, rep in sorted(set(edits), reverse=True):
        text = text[:lo] + rep + text[hi:]
    return text


def specs(sk, overrides: Mapping[str, float] | None = None, extra: Mapping[str, dict] | None = None) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for name, p in sk.d["params"].items():
        spec: dict[str, Any] = {"type": p["type"]}
        if p.get("expression"):
            spec["expression"] = localize(sk, p["expression"])
        else:
            spec["default"] = to_si(sk, p["type"], p["value"]) if p["type"] != "boolean" else bool(p["value"])
        if p.get("min") is not None:
            spec["min"] = to_si(sk, p["type"], p["min"])
        if p.get("max") is not None:
            spec["max"] = to_si(sk, p["type"], p["max"])
        out[name] = spec
    if extra:
        for k, v in extra.items():
            out[k] = {**v, "expression": localize(sk, v["expression"])} if v.get("expression") else v
    return out


def _translate(exc: Exception, name: str | None = None) -> ProfilerError:
    msg = getattr(exc, "message", None) or str(exc)
    return ProfilerError(("parameter %s: " % name if name else "") + msg, details={"kind": type(exc).__name__, **(getattr(exc, "context", None) or {})})


def evaluate(sk, overrides: Mapping[str, float] | None = None, extra: Mapping[str, dict] | None = None) -> dict[str, dict[str, Any]]:
    """Evaluate every parameter in dependency order. Raises ``ProfilerError`` naming the cycle / unit mismatch / bound violation."""
    sp = specs(sk, extra=extra)
    si_over = {}
    for k, v in (overrides or {}).items():
        if k not in sk.d["params"]:
            raise ProfilerNotFound("no parameter %s" % k)
        si_over[k] = to_si(sk, sk.d["params"][k]["type"], v)
    try:
        vals = P.evaluate_parameters(sp, si_over or None)
    except (ValidationError, ValueError) as exc:
        raise _translate(exc)
    out = {}
    for name, v in vals.items():
        p = sk.d["params"].get(name)
        ptype = p["type"] if p else sp[name]["type"]
        out[name] = {"value": from_si(sk, ptype, v) if ptype != "boolean" else bool(v), "si": v, "type": ptype}
    return out


def graph(sk) -> dict[str, Any]:
    sp = specs(sk)
    try:
        g = P.ParameterGraph(sp)
    except (ValidationError, ValueError) as exc:
        raise _translate(exc)
    return {"order": list(g.order), "dependencies": {k: sorted(v) for k, v in g.dependencies.items()}, "dependents": {k: sorted(v) for k, v in g.dependents.items()}}


def listing(sk) -> list[dict[str, Any]]:
    """Rows for the Parameter Manager: authored fields plus the evaluated value, dependencies, validity and the constraints/dimensions that use it."""
    try:
        ev, valid, err = evaluate(sk), True, None
        deps = graph(sk)
    except ProfilerError as exc:
        ev, valid, err, deps = {}, False, exc.message, {"dependencies": {}, "dependents": {}}
    rows = []
    for name, p in sk.d["params"].items():
        used = [{"dimension": did, "type": d["type"]} for did, d in sk.d["dimensions"].items() if d.get("param") == name]
        rows.append({"id": p["id"], "name": name, "type": p["type"], "scope": p["scope"], "unit": sk.unit if p["type"] == "length" else ("deg" if p["type"] == "angle" else ""),
                     "value": ev.get(name, {}).get("value", p.get("value")), "expression": p.get("expression"), "derived": bool(p.get("expression")),
                     "read_only": bool(p.get("read_only") or p.get("expression") and not p.get("editable_formula", True)), "min": p.get("min"), "max": p.get("max"),
                     "description": p.get("description", ""), "depends_on": deps["dependencies"].get(name, []), "used_by": deps["dependents"].get(name, []),
                     "dimensions": used, "valid": valid, "error": err})
    return rows


def _check_name(sk, name: str) -> None:
    if not isinstance(name, str) or not name.isidentifier() or len(name) > 64 or name in P.RESERVED_NAMES or name in P.CONSTANTS or name in P.FUNCTIONS:
        raise ProfilerError("%r is not a valid parameter name (letters, digits, underscore; no reserved words or function names)" % (name,))


def add_param(sk, name: str, ptype: str = "length", value: float | None = None, expression: str | None = None, *, scope: str = "profile",
              read_only: bool = False, minimum: float | None = None, maximum: float | None = None, description: str = "") -> str:
    _check_name(sk, name)
    if name in sk.d["params"]:
        raise ProfilerError("a parameter named %s already exists" % name)
    if ptype not in TYPES or scope not in SCOPES:
        raise ProfilerError("type is one of %s and scope one of %s" % (", ".join(TYPES), ", ".join(SCOPES)))
    if value is None and not expression:
        raise ProfilerError("give a value or a formula")
    sk.d["params"][name] = {"id": new_id(), "type": ptype, "value": 0.0 if value is None else value, "expression": expression or None, "scope": scope,
                            "read_only": read_only, "min": minimum, "max": maximum, "description": description}
    try:
        ev = evaluate(sk)
    except ProfilerError:
        del sk.d["params"][name]
        raise
    if expression:
        sk.d["params"][name]["value"] = ev[name]["value"]
    return sk.d["params"][name]["id"]


def remove_param(sk, name: str) -> None:
    if name not in sk.d["params"]:
        raise ProfilerNotFound("no parameter %s" % name)
    users = [n for n, p in sk.d["params"].items() if p.get("expression") and name in P.expression_dependencies(p["expression"])]
    dimension_users = [d for d, dim in sk.d["dimensions"].items() if dim.get("param") == name]
    if users or dimension_users:
        raise ProfilerError("parameter %s is still used by %s" % (name, ", ".join(users + ["a dimension"] * bool(dimension_users))),
                            details={"parameters": users, "dimensions": dimension_users})
    del sk.d["params"][name]


def link_dimension(sk, did: str, name: str) -> None:
    dim = D.get(sk, did)
    p = sk.d["params"].get(name)
    if p is None:
        raise ProfilerNotFound("no parameter %s" % name)
    need = {"length": ("length",), "angle": ("angle",)}
    t = dim["type"]
    ptype = "angle" if t == "angular" else "length"
    if p["type"] != ptype:
        raise ProfilerError("a %s dimension needs a %s parameter, %s is %s" % (t, ptype, name, p["type"]))
    if not D.eligible(t):
        raise ProfilerError("a %s dimension cannot be parameter-driven" % t)
    dim["param"] = name


def dimension_to_parameter(sk, did: str, name: str, *, expression: str | None = None) -> str:
    """'Convert a measurement to a parameter' (authoritative p.6): the parameter takes the dimension's current value; the dimension becomes driven by it."""
    dim = D.get(sk, did)
    if not D.eligible(dim["type"]) or dim.get("three_point"):
        raise ProfilerError("a %s dimension is a measurement only and cannot become a parameter" % dim["type"])
    m = D.measure(sk, dim)
    ptype = "angle" if dim["type"] == "angular" else "length"
    value = math.degrees(m["value"]) if ptype == "angle" else m["value"]
    pid = add_param(sk, name, ptype, value=value, expression=expression)
    dim["param"] = name
    dim["mode"] = "formula" if expression else "driving"
    dim["value"] = m["value"]
    return pid


def apply(sk, *, extra_dim_exprs: bool = True) -> dict[str, Any]:
    """Push evaluated parameters into every linked dimension and re-solve the sketch. Raises (and the caller rolls back) on any failure."""
    extra, owners = {}, {}
    for did, dim in sk.d["dimensions"].items():
        if dim.get("expr"):
            key = "_d" + did.replace("-", "")[:12]
            extra[key] = {"type": "angle" if dim["type"] == "angular" else "length", "expression": dim["expr"]}
            owners[did] = key
    ev = evaluate(sk, extra=extra)
    for name, p in sk.d["params"].items():
        if p.get("expression"):
            p["value"] = ev[name]["value"]
        _bounds(name, p, ev[name]["value"])
    for did, dim in sk.d["dimensions"].items():
        if dim.get("state") == "unresolved":
            continue
        src = None
        if did in owners:
            src, dim["mode"] = ev[owners[did]], "formula"
        elif dim.get("param"):
            src = ev[dim["param"]]
            dim["mode"] = "formula" if sk.d["params"][dim["param"]].get("expression") else "driving"
        if src is not None:
            v = src["si"] if dim["type"] == "angular" else src["value"]
            if v <= 0 and dim["type"] != "angular":
                raise ProfilerError("dimension %s would become %.6g: a length must be positive" % (did[:8], v))
            dim["value"] = float(v)
    res = sk.solve()
    if not res.ok:
        raise ProfilerConflict("the parameter values cannot be satisfied by the geometry: " + res.message,
                               details={"conflicting": sk._names(res.conflicting), "residuals": res.residuals})
    sk.apply_solution(res)
    return {"parameters": {k: v["value"] for k, v in ev.items() if not k.startswith("_d")}, "solve": res.to_dict()}


def _bounds(name, p, value):
    if isinstance(value, bool):
        return
    if p.get("min") is not None and value < p["min"] - 1e-12:
        raise ProfilerError("parameter %s = %.6g is below its minimum %.6g" % (name, value, p["min"]))
    if p.get("max") is not None and value > p["max"] + 1e-12:
        raise ProfilerError("parameter %s = %.6g is above its maximum %.6g" % (name, value, p["max"]))


def set_param(sk, name: str, *, value: float | None = None, expression: str | None | bool = False) -> dict[str, Any]:
    """Change a parameter's literal value, or give it / remove its formula (``expression=None`` removes it, keeping the evaluated value as a literal).
    The caller (the session) rolls the sketch back if this raises."""
    p = sk.d["params"].get(name)
    if p is None:
        raise ProfilerNotFound("no parameter %s" % name)
    if p.get("read_only"):
        raise ProfilerError("parameter %s is read-only" % name)
    if expression is not False:
        if expression is None:
            p["value"] = evaluate(sk)[name]["value"]
            p["expression"] = None
        else:
            p["expression"] = expression
    if value is not None:
        if p.get("expression"):
            raise ProfilerError("parameter %s is formula-controlled; remove its formula first to give it a value" % name)
        p["value"] = value
    return apply(sk)


def save_variant(sk, vname: str) -> None:
    sk.d["variants"][vname] = {n: p["value"] for n, p in sk.d["params"].items() if not p.get("expression")}


def apply_variant(sk, vname: str) -> dict[str, Any]:
    v = sk.d["variants"].get(vname)
    if v is None:
        raise ProfilerNotFound("no variant %s" % vname)
    for n, val in v.items():
        if n in sk.d["params"] and not sk.d["params"][n].get("expression"):
            sk.d["params"][n]["value"] = val
    return apply(sk)
