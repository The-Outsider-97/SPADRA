"""A typed, revision-aware design workflow (report 7.3): a read-only snapshot, geometry and unit checks, an optimisation problem, a budgeted search, an independent
re-evaluation of the candidate, the specialist checks that exist, and a preview for a person to approve.

The contract says exactly what the run is bound to (project, expected revision, family, the parameters that may vary, units, objective and constraint evidence,
budgets, output schema and a correlation key), and every stage reports a status from a fixed set with the emitting component and the revision it ran against:

    success  partial  unavailable  infeasible  timeout  stale  failed        (a stage that was skipped is ``not_run``)

Nothing here changes the project. A feasible candidate is a *proposal*; ``commit_allowed`` only says the evidence is complete enough to offer it for approval,
never that it is approved or safe. A stage that cannot run (the Verification Agent is not integrated, no simulation model exists for the family) is reported
``unavailable`` with the reason, and the overall status is then at most ``partial``: no stage is ever fabricated, and a changed project revision makes the whole
result ``stale``."""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Mapping

from src.utils.spatial_formats.sgeo import parameters as SP

from ..tools.errors import BadRequest
from ..tools.validation import number, text
from . import families as FM
from . import optimization as OPZ

SCHEMA = "spadra.design_workflow/1"
STATUSES = ("success", "partial", "unavailable", "infeasible", "timeout", "stale", "failed")
REQUIRED = ("snapshot", "geometry", "quantities", "problem", "solve", "recheck")
OPTIONAL = ("simulation", "verification")
CONTRACT_KEYS = {"project_id", "expected_revision", "family_id", "parameter_ids", "input_units", "frame_reference", "objective", "budgets", "output_schema", "correlation_key",
                 "verification"}
MAX_STEPS = 200


def _contract(project: Any, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise BadRequest("The workflow contract must be an object")
    unknown = sorted(set(raw) - CONTRACT_KEYS)
    if unknown:
        raise BadRequest(f"Unknown contract field(s): {', '.join(unknown)}", details={"fields": sorted(CONTRACT_KEYS)})
    missing = [k for k in ("project_id", "expected_revision", "family_id", "parameter_ids", "objective", "correlation_key") if raw.get(k) is None]
    if missing:
        raise BadRequest(f"The contract needs {', '.join(missing)}")
    if text(raw["project_id"], "project_id", 80) != project.manifest["id"]:
        raise BadRequest("The contract is for a different project", details={"project_id": project.manifest["id"]})
    revision = raw["expected_revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise BadRequest("expected_revision must be a whole number")
    names = raw["parameter_ids"]
    if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names) or len(set(names)) != len(names):
        raise BadRequest("parameter_ids must list the distinct parameters that may vary")
    objective = raw["objective"]
    if not isinstance(objective, Mapping) or objective.get("kind") not in ("family", "beam"):
        raise BadRequest("objective.kind must be 'family' or 'beam'")
    budgets = raw.get("budgets") or {}
    if not isinstance(budgets, Mapping) or set(budgets) - {"evaluations", "time_limit_s"}:
        raise BadRequest("budgets takes evaluations and time_limit_s")
    if raw.get("output_schema", SCHEMA) != SCHEMA:
        raise BadRequest(f"Only the output schema {SCHEMA} is produced")
    verification = raw.get("verification", "optional")
    if verification not in ("optional", "required"):
        raise BadRequest("verification must be 'optional' or 'required'")
    return {"project_id": project.manifest["id"], "expected_revision": revision, "family_id": text(raw["family_id"], "family_id", 80), "parameter_ids": list(names),
            "input_units": raw.get("input_units") or {"length": "mm", "angle": "deg", "pressure": "MPa", "force": "kN"},
            "frame_reference": raw.get("frame_reference") or {"frame": "family-local", "note": "no model frame is involved: the family is evaluated at the origin"},
            "objective": dict(objective), "budgets": dict(budgets), "output_schema": SCHEMA, "correlation_key": text(raw["correlation_key"], "correlation_key", 80),
            "verification": verification}


def _stage(name: str, status: str, agent: str, revision: int, **fields: Any) -> dict[str, Any]:
    if status not in STATUSES + ("not_run",):
        raise ValueError(status)
    return {"stage": name, "status": status, "agent": agent, "revision": revision, **fields}


def _spec(project: Any, c: Mapping[str, Any]) -> tuple[dict[str, Any] | None, Mapping[str, Any]]:
    """The generic problem for the contract: ``(spec, context)``, or ``(None, typed_refusal)``."""
    objective = dict(c["objective"])
    budgets = c["budgets"]
    if objective.pop("kind") == "beam":
        objective.update(family=c["family_id"])
    else:
        objective.update(family=c["family_id"])
    if "evaluations" in budgets:
        objective["budget"] = budgets["evaluations"]
    if "time_limit_s" in budgets:
        objective["time_limit_s"] = budgets["time_limit_s"]
    if c["objective"]["kind"] == "beam":
        return OPZ.beam_spec(project, objective)
    return objective, {}


def _overall(stages: list[dict[str, Any]], verification: str) -> str:
    by = {s["stage"]: s["status"] for s in stages}
    if "stale" in by.values():
        return "stale"
    if any(by.get(n) == "failed" for n in REQUIRED):
        return "failed"
    if any(by.get(n) == "unavailable" for n in REQUIRED):                                          # a declared assumption is missing or unsupported
        return "unavailable"
    if by.get("solve") == "timeout":
        return "timeout"
    if by.get("solve") == "infeasible":
        return "infeasible"
    if any(by.get(n) not in ("success", "partial") for n in REQUIRED):
        return "failed" if any(by.get(n) == "not_run" for n in REQUIRED) else "unavailable"
    if verification == "required" and by.get("verification") != "success":
        return "unavailable"
    if any(by.get(n) == "partial" for n in REQUIRED) or any(by.get(n) != "success" for n in OPTIONAL):
        return "partial"
    return "success"


def run(project: Any, raw_contract: Any) -> dict[str, Any]:
    """Run the workflow for a contract. Returns the typed result; raises ``BadRequest`` only for a malformed contract."""
    started = time.monotonic()
    c = _contract(project, raw_contract)
    rev = project.revision
    stages: list[dict[str, Any]] = []
    proposal: dict[str, Any] | None = None
    solve_result: dict[str, Any] | None = None

    def skip(name: str, because: str) -> None:
        stages.append(_stage(name, "not_run", "spadra.workflow", project.revision, reason=f"not run: {because}"))

    def halted() -> str | None:
        bad = [s for s in stages if s["stage"] in REQUIRED and s["status"] not in ("success", "partial", "not_run")]      # an unavailable required stage halts too
        return f"{bad[0]['stage']} was {bad[0]['status']}" if bad else None

    # 1. snapshot: read-only, bound to the expected revision
    if rev != c["expected_revision"]:
        stages.append(_stage("snapshot", "stale", "spadra.project", rev, reason=f"the project is at revision {rev}, not the expected {c['expected_revision']}",
                             expected_revision=c["expected_revision"]))
    else:
        stages.append(_stage("snapshot", "success", "spadra.project", rev, expected_revision=c["expected_revision"], read_only=True))
    # 2. geometry at the starting point
    spec, ctx = (None, {})
    if not halted():
        try:
            spec, ctx = _spec(project, c)
        except BadRequest as exc:
            stages.append(_stage("geometry", "failed", "spadra.families", rev, reason=str(exc)))
        else:
            if spec is None:                                                                      # a typed refusal: a missing or unsupported assumption
                stages.append(_stage("geometry", "unavailable", "spadra.optimization", rev, reason=ctx["reason"], refusal=ctx.get("status"), missing=ctx.get("missing")))
            else:
                fam = FM.family(project, c["family_id"])
                if sorted(spec["variables"]) != sorted(c["parameter_ids"]):
                    stages.append(_stage("geometry", "failed", "spadra.workflow", rev, reason="parameter_ids do not match the parameters the objective varies",
                                         parameter_ids=c["parameter_ids"], varied=sorted(spec["variables"])))
                else:
                    mid = {n: (v["min"] + v["max"]) / 2 for n, v in spec["variables"].items() if "min" in v}
                    ok, why = OPZ._buildable(fam, OPZ._merged(fam, spec.get("type") and FM._find_type(fam, spec["type"]), mid)) if mid else (True, "")
                    stages.append(_stage("geometry", "success" if ok else "failed", "spadra.families", rev, family=fam["name"], at=mid,
                                         **({"reason": why} if not ok else {})))
    else:
        skip("geometry", halted() or "")
    # 3. dimensional quantities and 4. problem definition
    prob = None
    if not halted() and spec is not None:
        try:
            prob = OPZ.family_problem(project, spec)
            stages.append(_stage("quantities", "success", "spadra.parameter-engine", rev, objectives=[{"expression": e, "unit": u} for e, u in zip(prob["objectives"], prob["dims"]["objectives"])],
                                 constraints=list(prob["constraints"]), constants=prob["constants"]))
            stages.append(_stage("problem", "success", "spadra.optimization", rev, variables=prob["variables"], senses=prob["senses"], weights=prob["weights"]))
        except BadRequest as exc:
            stages.append(_stage("quantities", "failed", "spadra.parameter-engine", rev, reason=str(exc)))
    if not any(s["stage"] == "quantities" for s in stages):
        skip("quantities", halted() or "no problem could be built")
    if not any(s["stage"] == "problem" for s in stages):
        skip("problem", halted() or "no problem could be built")
    # 5. the budgeted search
    if not halted() and prob is not None:
        try:
            solve_result = OPZ.optimize_family(project, spec)
            raw = solve_result["status"]
            status = {"converged": "success", "budget_exhausted": "partial", "timeout": "timeout", "infeasible": "infeasible"}.get(raw, "failed")
            stages.append(_stage("solve", status, solve_result["diagnostics"]["backend"]["name"], rev, solver_status=raw, evaluations=solve_result["evaluations"],
                                 diagnostics=solve_result["diagnostics"], result_sha256=solve_result["result_sha256"],
                                 **({"reason": solve_result["diagnostics"]["termination"]} if status != "success" else {})))
        except BadRequest as exc:
            stages.append(_stage("solve", "failed", "spadra.optimization", rev, reason=str(exc)))
    else:
        skip("solve", halted() or "no problem")
    # 6. independent re-evaluation of the candidate
    best = (solve_result or {}).get("proposal")
    if best and not halted():
        recheck: dict[str, Any] = {"method": "the candidate is rebuilt as a type of a scratch family and re-evaluated from scratch"}
        again = OPZ.family_problem(project, spec)["evaluate"](best["point"])
        recheck.update(objectives=again["objectives"], violations=again["violations"], solver_objectives=best["objectives"],
                       agrees=all(abs(a - b) <= 1e-9 * max(1.0, abs(a)) for a, b in zip(again["objectives"], best["objectives"])))
        feasible = max(again["violations"], default=0.0) <= 1e-9
        if c["objective"]["kind"] == "beam":
            recheck["analytical_check"] = OPZ.beam_check(project, spec, ctx, best["point"])
            feasible = feasible and recheck["analytical_check"]["within_limit"] and recheck["analytical_check"]["agrees_with_engine"]
        ok = recheck["agrees"] and feasible and best.get("buildable", False)
        stages.append(_stage("recheck", "success" if ok else "failed", "spadra.recheck", rev, **recheck,
                             **({} if ok else {"reason": "the re-evaluation disagrees with the search, or the candidate is not feasible and buildable"})))
        if ok and best.get("commands"):
            proposal = {"commands": best["commands"], "point": best["point"], "objectives": best["objectives"], "feasible": True, "approved": False}
    elif not halted():
        stages.append(_stage("recheck", "failed", "spadra.recheck", rev, reason="the search produced no candidate to re-evaluate"))
    else:
        skip("recheck", halted() or "")
    # 7-8. the specialist checks that may exist: reported as they are, never invented
    from .agent_hub import HUB
    stages.append(_stage("simulation", "unavailable", "slai.simulation", rev, requested=False,
                         reason="no simulation model is defined for this workflow" + ("" if HUB.available("simulation") else f"; {HUB.reason('simulation')}")))
    stages.append(_stage("verification", "unavailable", "slai.verification", rev, coverage="none",
                         reason=("The Verification Agent is not integrated into SPADRA" + ("" if HUB.available("verification") else f": {HUB.reason('verification')}"))))
    # 9. the revision is compared again, last, so evidence for a project that moved on is stale
    if project.revision != c["expected_revision"]:
        stages.append(_stage("revision_check", "stale", "spadra.project", project.revision, reason="the project changed while the workflow ran", expected_revision=c["expected_revision"]))
    else:
        stages.append(_stage("revision_check", "success", "spadra.project", project.revision, expected_revision=c["expected_revision"]))
    status = _overall(stages, c["verification"])
    commit_allowed = bool(proposal) and status in ("success", "partial")
    result = {"schema": SCHEMA, "correlation_key": c["correlation_key"], "status": status, "contract": c,
              "contract_sha256": hashlib.sha256(json.dumps(c, sort_keys=True, default=str).encode()).hexdigest(), "stages": stages,
              "revision_at_start": rev, "revision_now": project.revision, "runtime_ms": round((time.monotonic() - started) * 1000.0, 3),
              "proposal": proposal if commit_allowed else None, "commit_allowed": commit_allowed,
              "approval": {"required": True, "state": "not_approved",
                           "note": "A candidate is not an approval. The checks marked unavailable were not run, and nothing here is a compliance or safety statement."},
              "not_run_or_unavailable": [s["stage"] for s in stages if s["status"] in ("not_run", "unavailable")],
              "beam": ({"assumptions": OPZ.beam_assumptions(ctx["inputs"]), "checks_not_performed": list(OPZ.BEAM_NOT_CHECKED)}
                       if c["objective"]["kind"] == "beam" and "inputs" in ctx else
                       {"assumptions": ctx.get("assumptions"), "checks_not_performed": list(OPZ.BEAM_NOT_CHECKED)} if c["objective"]["kind"] == "beam" and ctx else None)}
    return result
