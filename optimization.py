"""Parametric optimization of a family (P2-01): choose parameter values that minimise or maximise formula-defined objectives subject to formula-defined
constraints, using the family's own parameter graph (so units, bounds and formulas are the ones the model already enforces).

Nothing here changes the project. The result is a *proposal* with the commands that would apply it (``family.type_update``); a person or an approved
agent hand-off runs them through the ordinary command path, where they are transactional and undoable. Every candidate is evaluated through the same
parameter engine and, for the proposed point, through the family's generator, so a proposal that cannot be built is not offered as feasible."""
from __future__ import annotations

import math
import re
from typing import Any, Mapping

from src.utils.spatial_formats.common import optimizer as OPT
from src.utils.spatial_formats.common.errors import ValidationError
from src.utils.spatial_formats.sgeo import families as SG
from src.utils.spatial_formats.sgeo import parameters as SP

from ..tools.errors import BadRequest
from ..tools.validation import number, text
from . import families as FM

_REL = re.compile(r"^(.*?)(<=|>=|==|<|>)(.*)$")
MAX_TERMS = 8


def _quantities(fam: Mapping[str, Any], values: Mapping[str, Any]) -> dict[str, SP.Quantity]:
    out = {}
    for name, v in values.items():
        spec = fam["parameters"].get(name)
        kind = spec["type"] if spec else None
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        out[name] = SP.Quantity(float(v), SP.PARAMETER_DIMENSIONS.get(kind, SP.ZERO))
    return out


def _constants(raw: Any) -> tuple[dict[str, SP.Quantity], dict[str, Any]]:
    out, shown = {}, {}
    if raw in (None, {}):
        return out, shown
    if not isinstance(raw, Mapping) or len(raw) > 32:
        raise BadRequest("constants must map a name to {type, value} (at most 32)")
    for name, c in raw.items():
        if not isinstance(name, str) or not name.isidentifier():
            raise BadRequest(f"'{name}' is not a valid constant name")
        if isinstance(c, Mapping) and "expression" in c:             # a unit expression such as "5 kN / 1 m" (a line load has no named type)
            if set(c) - {"expression"}:
                raise BadRequest(f"constant {name}: give either an expression or a type and value")
            try:
                q = SP.evaluate_expression(str(c["expression"]), {})
            except (ValidationError, SP.ValidationError) as exc:
                raise BadRequest(f"constant {name}: {exc}") from exc
            if isinstance(q.value, bool) or not isinstance(q.value, (int, float)):
                raise BadRequest(f"constant {name} must be a number")
            out[name] = q
            shown[name] = {"expression": c["expression"], "dimension": SP.dimension_name(q.dimension), "si_value": q.value}
            continue
        kind = c.get("type", "number") if isinstance(c, Mapping) else "number"
        value = c.get("value") if isinstance(c, Mapping) else c
        if kind not in SP.PARAMETER_DIMENSIONS:
            raise BadRequest(f"constant {name}: unknown type '{kind}'")
        out[name] = SP.Quantity(float(FM.to_si(kind, value, f"constant {name}")), SP.PARAMETER_DIMENSIONS[kind])
        shown[name] = {"type": kind, "value": value}
    return out, shown


def _terms(raw: Any, label: str) -> list[Mapping[str, Any]]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_TERMS and label == "objectives":
        raise BadRequest(f"{label} must be a list of 1 to {MAX_TERMS} entries")
    if not isinstance(raw, list) or len(raw) > MAX_TERMS:
        raise BadRequest(f"{label} must be a list of at most {MAX_TERMS} entries")
    return raw


def _scalar(q: Any, what: str) -> float:
    if not isinstance(q, SP.Quantity) or isinstance(q.value, bool) or not isinstance(q.value, (int, float)):
        raise ValidationError(f"{what} must be a number")
    return float(q.value)


def family_problem(project: Any, p: Mapping[str, Any]) -> dict[str, Any]:
    fam = FM.family(project, p.get("family"))
    type_name = p.get("type")
    if type_name is not None:
        type_name = FM._find_type(fam, text(type_name, "type", 80))
    base = dict((fam.get("types") or {}).get(type_name, {})) if type_name else {}
    raw_vars = p.get("variables")
    if not isinstance(raw_vars, Mapping) or not raw_vars:
        raise BadRequest("variables must name the parameters to vary, each with min and max (display units)")
    variables: dict[str, dict[str, Any]] = {}
    for name, v in raw_vars.items():
        spec = fam["parameters"].get(name)
        if spec is None:
            raise BadRequest(f"{fam['name']} has no parameter '{name}'")
        if "expression" in spec or spec.get("read_only"):
            raise BadRequest(f"Parameter '{name}' is computed by a formula; vary the parameters it depends on")
        kind = spec["type"]
        if kind == "enumeration":
            variables[name] = {"kind": "choice", "choices": list(spec.get("choices") or [])}
        elif kind == "boolean":
            variables[name] = {"kind": "choice", "choices": [False, True]}
        elif kind in FM.DATA_TYPES:
            raise BadRequest(f"Parameter '{name}' is {kind}, which cannot be optimised")
        else:
            lo, hi = number(v.get("min", FM.from_si(kind, spec.get("min"))), f"{name}.min", -1e12, 1e12), number(v.get("max", FM.from_si(kind, spec.get("max"))), f"{name}.max", -1e12, 1e12)
            variables[name] = {"kind": "integer" if kind == "integer" else "real", "min": lo, "max": hi, "step": v.get("step")}
    consts, consts_shown = _constants(p.get("constants"))
    objectives = _terms(p.get("objectives"), "objectives")
    constraints = _terms(p.get("constraints") or [], "constraints")
    senses = []
    for o in objectives:
        if not isinstance(o, Mapping) or o.get("sense", "min") not in ("min", "max"):
            raise BadRequest("each objective is {expression, sense: 'min'|'max', weight?}")
        senses.append(o.get("sense", "min"))
    parsed = []
    for c in constraints:
        expr = c.get("expression") if isinstance(c, Mapping) else c
        m = _REL.match(expr) if isinstance(expr, str) else None
        if not m:
            raise BadRequest(f"constraint '{expr}' must compare two expressions with <=, >=, <, > or ==")
        parsed.append((m.group(1).strip(), m.group(2), m.group(3).strip(), expr))
    to_si = {n: (lambda v, k=fam["parameters"][n]["type"], n=n: v if k in ("boolean", "enumeration") else FM.to_si(k, v, n)) for n in variables}

    dims: dict[str, list] = {"objectives": [], "constraints": []}

    def environment(point: Mapping[str, Any]) -> dict[str, Any]:
        merged = {**base, **{n: to_si[n](v) for n, v in point.items()}}
        return {**_quantities(fam, SP.evaluate_parameters(fam["parameters"], merged)), **consts}

    def evaluate(point: Mapping[str, Any]) -> dict[str, Any]:
        env = environment(point)
        quantities = [SP.evaluate_expression(o["expression"], env) for o in objectives]
        dims["objectives"] = [SP.dimension_name(q.dimension) for q in quantities]
        objs = [_scalar(q, f"objective '{o['expression']}'") for q, o in zip(quantities, objectives)]
        viol = []
        for lhs, op, rhs, _ in parsed:
            qa, qb = SP.evaluate_expression(lhs, env), SP.evaluate_expression(rhs, env)
            if qa.dimension != qb.dimension:
                raise ValidationError(f"incompatible physical dimensions in '{lhs} {op} {rhs}': {SP.dimension_name(qa.dimension)} vs {SP.dimension_name(qb.dimension)}")
            dims["constraints"] = dims["constraints"][:len(viol)] + [SP.dimension_name(qa.dimension)]
            a, b = _scalar(qa, lhs), _scalar(qb, rhs)
            scale = max(abs(b), abs(a), 1e-9)
            gap = (a - b) if op in ("<=", "<") else (b - a) if op in (">=", ">") else abs(a - b)
            viol.append(max(0.0, gap) / scale)
        return {"objectives": objs, "violations": viol}

    start_point = {n: (v["choices"][0] if v["kind"] == "choice" else (v["min"] + v["max"]) / 2) for n, v in variables.items()}
    try:                                                                  # surface syntax, unit and unknown-name errors now, as one clear refusal
        evaluate(start_point)
    except (ValidationError, SP.ValidationError) as exc:
        raise BadRequest(f"The problem cannot be evaluated: {exc}") from exc
    return {"family": fam, "type": type_name, "variables": variables, "senses": senses, "weights": [float(o.get("weight", 1.0)) for o in objectives],
            "evaluate": evaluate, "environment": environment, "dims": dims, "constraints": [c[3] for c in parsed], "objectives": [o["expression"] for o in objectives],
            "constants": consts_shown, "start": start_point}


def _buildable(fam: Mapping[str, Any], merged: Mapping[str, Any]) -> tuple[bool, str]:
    """Whether the family's generator accepts these (SI) values: the point is generated as a type of a scratch copy, so nothing is stored."""
    try:
        scratch = {k: v for k, v in fam.items() if k != "instance_count"}
        scratch["types"] = {**(fam.get("types") or {}), "Proposal": {n: v for n, v in merged.items() if n in fam["parameters"]}}
        SG.generate_geometry(scratch, None, "Proposal")
        return True, ""
    except Exception as exc:
        return False, str(exc)[:200]


def _merged(fam: Mapping[str, Any], type_name: Any, point: Mapping[str, Any]) -> dict[str, Any]:
    return {**((fam.get("types") or {}).get(type_name, {}) if type_name else {}),
            **{n: (v if fam["parameters"][n]["type"] in ("boolean", "enumeration") else FM.to_si(fam["parameters"][n]["type"], v, n)) for n, v in point.items()}}


def _diagnostics(prob: Mapping[str, Any], res: Mapping[str, Any], best: Mapping[str, Any] | None) -> dict[str, Any]:
    """What a reviewer needs to judge a search result (report section 7.5): the backend and its termination, the counts and runtime, the tolerances, the residual of every
    constraint at the returned point, the objectives with their units, weights and orientation, and what, if anything, is proved (nothing is)."""
    violations = list(best["violations"]) if best else []
    return {"backend": res["backend"], "termination": res["termination"], "evaluations": res["evaluations"], "failed_evaluations": res["failed_evaluations"],
            "feasible_found": res["feasible_found"], "runtime_ms": res["runtime_ms"], "tolerances": res["tolerances"], "time_limit_s": res.get("time_limit_s"),
            "max_constraint_violation": max(violations) if violations else None,
            "constraint_residuals": [{"constraint": c, "relative_violation": v, "dimension": (prob["dims"]["constraints"] + [None] * len(violations))[i]}
                                     for i, (c, v) in enumerate(zip(prob["constraints"], violations))],
            "objectives": [{"expression": e, "sense": sn, "weight": w, "unit": (prob["dims"]["objectives"] + [None] * len(prob["objectives"]))[i]}
                           for i, (e, sn, w) in enumerate(zip(prob["objectives"], prob["senses"], prob["weights"]))],
            "scalarisation": "weighted sum of the raw SI values (minimise; a maximised objective is negated); the weights are design assumptions, not derived",
            "proof_scope": "none: a search result, not a proof of optimality or of infeasibility, and not an engineering approval"}


def optimize_family(project: Any, p: Mapping[str, Any]) -> dict[str, Any]:
    prob = family_problem(project, p)
    budget = int(number(p.get("budget", 400), "budget", 1, OPT.MAX_BUDGET))
    seed = int(number(p.get("seed", 0), "seed", 0, 2 ** 31))
    limit = p.get("time_limit_s")
    try:
        res = OPT.optimize(prob["variables"], prob["evaluate"], senses=prob["senses"], weights=prob["weights"], budget=budget, seed=seed, start=prob["start"],
                           time_limit=None if limit is None else number(limit, "time_limit_s", 0.001, 3600))
    except ValidationError as exc:                                         # an ill-posed problem (an empty range, no variables) is a bad request, not a crash
        raise BadRequest(f"The problem is not well posed: {exc}") from exc
    fam, type_name = prob["family"], prob["type"]
    out = {"status": res["status"], "family": fam["id"], "type": type_name, "objectives": prob["objectives"], "constraints": prob["constraints"], "constants": prob["constants"],
           "budget": budget, "evaluations": res["evaluations"], "feasible_found": res["feasible_found"], "failed_evaluations": res["failed_evaluations"], "seed": seed,
           "result_sha256": res["result_sha256"], "proved": False, "note": res["note"], "revision": project.revision, "proposal": None, "pareto": [],
           "diagnostics": _diagnostics(prob, res, res["best"])}
    best = res["best"]
    if best is not None:
        point = best["point"]
        merged = {**((fam.get("types") or {}).get(type_name, {}) if type_name else {}),
                  **{n: (v if fam["parameters"][n]["type"] in ("boolean", "enumeration") else FM.to_si(fam["parameters"][n]["type"], v, n)) for n, v in point.items()}}
        built, why = _buildable(fam, merged)
        feasible = res["status"] in ("converged", "budget_exhausted", "timeout") and built and sum(best["violations"]) <= 1e-9
        proposal = {"point": point, "objectives": best["objectives"], "violations": best["violations"], "feasible": feasible, "buildable": built,
                    **({"why_not_buildable": why} if why else {})}
        if type_name and feasible:
            proposal["commands"] = [{"command": "family.type_update", "params": {"id": fam["id"], "name": type_name, "values": point}}]
        out["proposal"] = proposal
        if not built and out["status"] in ("converged", "budget_exhausted", "timeout"):
            out["status"] = "infeasible"
    out["pareto"] = [{"point": r["point"], "objectives": r["objectives"]} for r in res["pareto"][:50]]
    if res["pareto"]:
        columns = list(zip(*[r["objectives"] for r in res["pareto"]]))
        out["pareto_normalization"] = {"method": "none applied to the front itself; ranges shown so a weighting can be judged",
                                       "objective_ranges": [{"expression": e, "min": min(c), "max": max(c)} for e, c in zip(prob["objectives"], columns)]}
    return out


def alternatives_family(project: Any, p: Mapping[str, Any]) -> dict[str, Any]:
    """Several different, buildable, constraint-satisfying designs for a family, as new named types that a person can review. Deterministic for a seed; changes nothing."""
    prob = family_problem(project, p)
    count = int(number(p.get("count", 4), "count", 1, 10))
    candidates = int(number(p.get("candidates", 300), "candidates", count, 5000))
    seed = int(number(p.get("seed", 0), "seed", 0, 2 ** 31))
    try:
        res = OPT.alternatives(prob["variables"], prob["evaluate"], senses=prob["senses"], weights=prob["weights"], count=count, candidates=candidates, seed=seed,
                               top_fraction=float(number(p.get("top_fraction", 0.5), "top_fraction", 0.01, 1)))
    except ValidationError as exc:
        raise BadRequest(f"The problem is not well posed: {exc}") from exc
    fam, type_name = prob["family"], prob["type"]
    prefix = text(p.get("name_prefix", "Option"), "name_prefix", 60)
    taken = set((fam.get("types") or {}))
    options, n = [], 0
    for alt in res["alternatives"]:
        built, why = _buildable(fam, _merged(fam, type_name, alt["point"]))
        entry = {**alt, "buildable": built, **({"why_not_buildable": why} if why else {})}
        if built:
            n += 1
            while f"{prefix} {n}" in taken:
                n += 1
            name = f"{prefix} {n}"
            taken.add(name)
            entry["type_name"] = name
            entry["command"] = {"command": "family.type_define", "params": {"id": fam["id"], "name": name, "values": alt["point"], **({"copy_from": type_name} if type_name else {})}}
        options.append(entry)
    status = res["status"] if res["status"] in ("failed", "infeasible") or any(o["buildable"] for o in options) else "infeasible"
    return {**{k: res[k] for k in ("requested", "evaluations", "feasible_found", "failed_evaluations", "seed", "result_sha256", "proved", "note", "backend", "runtime_ms", "tolerances")},
            "status": status,
            "family": fam["id"], "type": type_name, "objectives": prob["objectives"], "constraints": prob["constraints"], "constants": prob["constants"],
            "revision": project.revision, "alternatives": options}


# ------------------------------------------------------------------------------------------------------------------ rectangular beam (report 7.4)
BEAM_PARAMETERS = ("width", "height", "depth", "second_moment", "volume")
# ordered so no intermediate exceeds the parameter engine's 1e12 magnitude cap (384 * E in pascals would)
DEFLECTION = "5 * load_w * depth ** 4 / (modulus_E * second_moment) / 384"
BEAM_NOT_CHECKED = ("bending strength", "shear", "buckling", "lateral-torsional stability", "connection resistance", "vibration", "fire performance", "self-weight",
                    "load combinations", "the design code of any jurisdiction")


def beam_assumptions(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """The assumption table that travels with every beam result (report Table 13); values are those the caller declared, never defaults."""
    return {"support": inputs.get("support"), "load": inputs.get("load"), "line_load_n_per_m": inputs.get("line_load_n_per_m"), "youngs_modulus_gpa": inputs.get("youngs_modulus_gpa"),
            "model": "Euler-Bernoulli, small displacement, homogeneous linear-elastic, shear deformation neglected",
            "deflection_formula": "delta_max = 5 w L^4 / (384 E I) for a simply supported beam under a uniform line load",
            "limit": f"delta_max <= L / {inputs.get('limit_ratio', 360)}", "not_checked": list(BEAM_NOT_CHECKED),
            "compliance": "not evaluated: a deflection inequality does not establish safety"}


def _typed(status: str, reason: str, inputs: Mapping[str, Any], **extra: Any) -> dict[str, Any]:
    """A refusal that is an analysable result, not an exception: what is missing or unsupported, with the assumptions as given. Nothing is substituted."""
    return {"status": status, "reason": reason, "proposal": None, "assumptions": beam_assumptions(inputs), "proved": False, **extra}


def beam_spec(project: Any, p: Mapping[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Validate a beam request and build the generic problem for it: ``(spec, context)``, or ``(None, typed_refusal)`` for a missing or unsupported assumption.

    Every assumption must be stated by the caller (support, load, line load, Young's modulus, bounds): a missing one is status ``unavailable`` naming it, an
    unsupported case is ``unsupported``, and neither is ever filled in from a default or inferred from the element."""
    inputs = {k: p.get(k) for k in ("support", "load", "line_load_n_per_m", "youngs_modulus_gpa", "limit_ratio")}
    inputs["limit_ratio"] = 360 if p.get("limit_ratio") is None else p["limit_ratio"]
    missing = [k for k in ("support", "load", "line_load_n_per_m", "youngs_modulus_gpa") if p.get(k) is None]
    if missing:
        return None, _typed("unavailable", "Missing declared assumption(s): " + ", ".join(missing) + ". None is assumed for you.", inputs, missing=missing)
    if p["support"] != "simply_supported" or p["load"] != "uniform":
        return None, _typed("unsupported", "Only a simply supported beam under a uniform line load is implemented. The support case is never inferred from the element's category.",
                            inputs, supported={"support": ["simply_supported"], "load": ["uniform"]})
    fam = FM.family(project, p.get("family"))
    absent = [n for n in BEAM_PARAMETERS if n not in fam["parameters"]]
    if absent:
        return None, _typed("unsupported", f"{fam['name']} is not a rectangular-beam family: it lacks " + ", ".join(absent) + " (start from the 'beam' template).", inputs,
                            missing_parameters=absent)
    load = number(p["line_load_n_per_m"], "line_load_n_per_m", 0.001, 1e9)
    modulus = number(p["youngs_modulus_gpa"], "youngs_modulus_gpa", 0.001, 900)          # 900 GPa = 9e11 Pa, inside the engine's magnitude cap
    ratio = number(inputs["limit_ratio"], "limit_ratio", 10, 10000)
    ranges = {}
    for key, name in (("width_mm", "width"), ("height_mm", "height")):
        r = p.get(key)
        if not (isinstance(r, (list, tuple)) and len(r) == 2):
            return None, _typed("unavailable", f"Give {key} as [min, max]: the bounds are design inputs and are not guessed.", inputs, missing=[key])
        ranges[name] = {"min": number(r[0], f"{key}[0]", 1, 1e6), "max": number(r[1], f"{key}[1]", 1, 1e6)}
    pareto = bool(p.get("pareto", False))
    objectives = [{"expression": "volume", "sense": "min"}] + ([{"expression": DEFLECTION, "sense": "min"}] if pareto else [])
    spec = {"family": p.get("family"), "type": p.get("type"), "variables": ranges,
            "constants": {"load_w": {"expression": f"{load!r} N / 1 m"}, "modulus_E": {"type": "pressure", "value": modulus * 1000.0}},
            "objectives": objectives, "constraints": [f"{DEFLECTION} <= depth / {ratio!r}"], "budget": p.get("budget", 400), "seed": p.get("seed", 0)}
    if p.get("time_limit_s") is not None:
        spec["time_limit_s"] = p["time_limit_s"]
    return spec, {"inputs": {**inputs, "limit_ratio": ratio}, "load": load, "modulus_gpa": modulus, "ratio": ratio, "ranges": ranges, "pareto": pareto}


def beam_check(project: Any, spec: Mapping[str, Any], ctx: Mapping[str, Any], point: Mapping[str, Any]) -> dict[str, Any]:
    """The closed-form deflection at ``point`` recomputed in plain floats, outside the parameter engine, next to the engine's own value."""
    prob = family_problem(project, spec)
    env = prob["environment"](point)
    span = env["depth"].value
    b, h = FM.to_si("length", point["width"], "width"), FM.to_si("length", point["height"], "height")
    second = b * h ** 3 / 12.0
    delta = 5.0 * ctx["load"] * span ** 4 / (384.0 * ctx["modulus_gpa"] * 1e9 * second)
    engine = SP.evaluate_expression(DEFLECTION, env).value
    allowable = span / ctx["ratio"]
    return {"method": "closed form 5wL^4/(384EI) recomputed in plain floats, outside the parameter engine", "span_m": span, "second_moment_m4": second,
            "deflection_m": delta, "allowable_m": allowable, "utilisation": delta / allowable, "engine_deflection_m": engine,
            "agrees_with_engine": math.isclose(delta, engine, rel_tol=1e-9), "within_limit": delta <= allowable * (1 + 1e-9), "volume_m3": b * h * span}


def optimize_beam_family(project: Any, p: Mapping[str, Any]) -> dict[str, Any]:
    """Minimise the volume of a rectangular beam family over its width and height subject to a deflection limit, for one declared support and load case.

    The result carries the assumption table, the independent closed-form recomputation at the proposed point, and which variable bounds are active (when a bound is
    active the bound, not the deflection limit, decided the answer). It is a stiffness-limited starting point, not a structural design."""
    spec, ctx = beam_spec(project, p)
    if spec is None:
        return ctx
    out = optimize_family(project, spec)
    pareto, ranges = ctx["pareto"], ctx["ranges"]
    out.update(assumptions=beam_assumptions(ctx["inputs"]), objective_names=["volume (m3)"] + (["deflection (m)"] if pareto else []),
               optimum_asserted=False, checks_not_performed=list(BEAM_NOT_CHECKED))
    prop = out.get("proposal")
    if prop:
        prop["analytical_check"] = beam_check(project, spec, ctx, prop["point"])
        point = prop["point"]
        prop["active_bounds"] = [n for n, r in ranges.items()
                                 if math.isclose(point[n], r["min"], rel_tol=1e-6) or math.isclose(point[n], r["max"], rel_tol=1e-6)]
        prop["active_bounds_note"] = ("a variable at a bound means the bound, not the deflection limit, decided it" if prop["active_bounds"] else "no variable is at a bound")
        if not prop["analytical_check"]["within_limit"]:
            prop["feasible"] = False
            prop.pop("commands", None)
            out["status"] = "infeasible"
            out["note"] = "the independent recomputation disagrees with the search's feasibility; the candidate is withdrawn"
    return out
