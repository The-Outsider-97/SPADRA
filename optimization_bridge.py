"""SPADRA's connection to the real SLAI Optimization Agent (``src/agents/optimization_agent.py``), for bounded parametric optimisation (master prompt 14.9).

The one problem SPADRA poses is stated in full in every result: choose the **depth** (or width) of one beam, between bounds you give, that **minimises
the beam's volume** subject to **one constraint - its maximum deflection under the load and support you entered must not exceed span ÷ ratio**.

* the objective is the beam's volume from its own dimensions and span;
* the constraint's deflection is evaluated, for every trial value the solver asks about, by the **STEM Agent** (section properties and beam response);
* the **Optimization Agent** chooses and runs the solver (it records which one, its status, its feasibility and optimality evidence, the evaluations used and a
  fingerprint of the problem), and SPADRA reports that evidence as returned, without upgrading "locally optimal" to "globally optimal";
* the answer is then re-evaluated once independently through the STEM Agent at the returned value, and that check is reported next to the agent's own figures.

Only the deflection limit is a constraint. Strength, buckling, vibration, connections and every code check are **not** considered, so a result is a stiffness
limit, not a design. Changing the beam is a separate, approved step; this module never edits the model.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

from ..tools.errors import BadRequest, DependencyMissing
from ..tools.validation import choice, number

from . import elements as E
from . import structural as S
from .agent_hub import HUB
from .engineering_bridge import BRIDGE as STEM

VARIABLES = ("depth", "width")
NOTE = ("Optimised against the deflection limit alone, for the one load and support you entered, with linear elastic behaviour and no self-weight. Strength, buckling, "
        "vibration, fire, connections and code requirements were not considered. It is a starting point for an engineer, not a design.")
MAX_EVALUATIONS = 400


def status() -> dict[str, Any]:
    opt, stem = HUB.available("optimization"), STEM.available
    out: dict[str, Any] = {"available": opt and stem, "agents": {"optimization": opt, "stem": stem}, "variables": list(VARIABLES), "limit_evaluations": MAX_EVALUATIONS}
    if not opt:
        out["reason"] = HUB.reason("optimization")
    elif not stem:
        out["reason"] = STEM.reason
    return out


def optimize_beam(project: Any, oid: str, p: Mapping[str, Any]) -> dict[str, Any]:
    """Find the smallest value of one beam dimension that meets the deflection limit. Reads the project; changes nothing."""
    st = status()
    if not st["available"]:
        raise DependencyMissing("Optimisation needs the SLAI Optimization Agent and STEM Agent", details={"reason": st.get("reason")})
    meta = E.meta_of(project, oid)
    if E.m(meta, "category") != "structural" or E.is_column(meta):
        raise BadRequest("Optimisation applies to beams")
    if E.beam_section(meta)["family"] != "rect":
        raise BadRequest("Optimisation varies the width or depth of a rectangular beam. This beam has a rolled or tube profile: change it to Rectangular in Properties to optimise it.")
    variable = choice(p.get("variable", "depth"), VARIABLES, "variable")
    ratio = float(number(p.get("deflection_ratio", 360), "deflection_ratio", 10, 10000))
    lower = float(number(p["lower_mm"], "lower_mm", 10, 5000)) if p.get("lower_mm") not in (None, "") else None
    upper = float(number(p["upper_mm"], "upper_mm", 10, 5000)) if p.get("upper_mm") not in (None, "") else None
    if lower is None or upper is None:
        raise BadRequest("Give the lower and upper bound (mm) of the dimension to optimise; SPADRA does not choose them for you", details={"needs": ["lower_mm", "upper_mm"]})
    if lower >= upper:
        raise BadRequest("The lower bound must be smaller than the upper bound")
    current = S.analyze_beam(project, oid, p)                       # validates support, load and modulus exactly as beam analysis does, and gives the current figures
    inp = current["inputs"]
    span, width0, depth0 = inp["span_m"], inp["width_m"], inp["depth_m"]
    load_n = float(inp["load"]) * 1000.0
    modulus = inp["youngs_modulus_gpa"] * 1e9
    limit = span / ratio
    evaluations = {"n": 0}

    def deflection(value_mm: float) -> float:
        evaluations["n"] += 1
        width, depth = (width0, value_mm / 1000.0) if variable == "depth" else (value_mm / 1000.0, depth0)
        section = STEM.section(width, depth)
        r = STEM.beam_response(span_m=span, load=load_n, load_type=inp["load_type"], support=inp["support"], second_moment_m4=float(section["second_moment_m4"]), youngs_modulus_pa=modulus)
        return float(r["outputs"]["max_deflection_m"]["value"])

    def volume(value_mm: float) -> float:
        width, depth = (width0, value_mm / 1000.0) if variable == "depth" else (value_mm / 1000.0, depth0)
        return width * depth * span

    start = (depth0 if variable == "depth" else width0) * 1000.0
    start = min(max(start, lower), upper)
    problem = {"name": f"beam_{variable}", "variables": {variable + "_mm": {"type": "real", "lower": lower, "upper": upper, "initial": start}},
               "objectives": {"volume_m3": {"sense": "minimize", "function": lambda a: volume(a[variable + "_mm"])}},
               "constraints": [{"name": "max_deflection_m", "sense": "<=", "rhs": limit, "function": lambda a: deflection(a[variable + "_mm"])}]}
    agent = HUB.require("optimization")
    answer = agent.optimize({"problem": problem, "max_evaluations": MAX_EVALUATIONS, "time_limit_seconds": 30})
    HUB.record("Optimization Agent", "optimize", answer.get("run_id"))
    HUB.record("STEM Agent", f"engineering_analysis.beam_response ({evaluations['n']} evaluations for the constraint)", None)
    solution = answer.get("solution") or {}
    values = solution.get("decision_variables") or {}
    best = values.get(variable + "_mm")
    result: dict[str, Any] = {
        "id": oid, "name": project.model.get(oid)["name"], "revision": project.revision, "variable": variable,
        "problem": {"minimise": "beam volume (m³)", "variable": f"{variable} between {lower:g} and {upper:g} mm", "constraint": f"max deflection ≤ span ÷ {ratio:g} = {limit * 1000:.3f} mm",
                    "support": inp["support"], "load_type": inp["load_type"], "load": inp["load"], "load_unit": inp["load_unit"], "span_m": span,
                    "other_dimension_mm": (width0 if variable == "depth" else depth0) * 1000.0, "material": inp["material"],
                    "youngs_modulus_gpa": inp["youngs_modulus_gpa"], "youngs_modulus_source": inp["youngs_modulus_source"]},
        "current": {"value_mm": (depth0 if variable == "depth" else width0) * 1000.0, "volume_m3": volume((depth0 if variable == "depth" else width0) * 1000.0),
                    "max_deflection_m": float(current["outputs"]["max_deflection_m"]["value"])},
        "agent": {"name": "SLAI Optimization Agent", "run_id": answer.get("run_id"), "status": answer.get("status"), "success": bool(answer.get("success")),
                  "solver": solution.get("solver") or answer.get("selected_solver"), "solver_status": solution.get("solver_status"),
                  "termination": solution.get("termination_reason"), "feasibility": solution.get("feasibility"), "optimality": solution.get("optimality"),
                  "evaluations": solution.get("evaluations"), "iterations": solution.get("iterations"), "max_constraint_violation": solution.get("max_constraint_violation"),
                  "problem_fingerprint": answer.get("problem_fingerprint"), "warnings": list(answer.get("warnings") or []) + list(solution.get("warnings") or [])},
        "note": NOTE, "found": False,
    }
    if best is None or solution.get("feasibility") not in ("feasible",):
        result["message"] = "The agent did not return a feasible value for these bounds and this limit. Widen the bounds or relax the limit."
        return result
    best = float(best)
    checked = deflection(best)                                         # an independent evaluation at the returned value
    result["found"] = True
    result["best"] = {"value_mm": best, "volume_m3": volume(best), "max_deflection_m": checked, "limit_m": limit, "utilisation": checked / limit,
                      "reported_by_agent": {"volume_m3": (solution.get("objective_values") or {}).get("volume_m3")},
                      "check": {"basis": "re-evaluated through the STEM Agent at the returned value", "meets_limit": checked <= limit * (1 + 1e-6),
                                "agrees_with_agent": math.isclose(volume(best), float((solution.get("objective_values") or {}).get("volume_m3", math.nan)), rel_tol=1e-6)}}
    result["change"] = {"id": oid, "key": variable, "value": math.ceil(best * 10) / 10.0, "unit": "mm"}           # rounded up to 0.1 mm so the value that is applied still meets the limit
    return result
