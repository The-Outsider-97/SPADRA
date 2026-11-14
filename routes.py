"""SPADRA's HTTP API. Handlers are thin: they unpack the request and call the project; the rules live in ``project.py``."""
from __future__ import annotations

import json
from typing import Any

from ..tools.errors import BadRequest, NotFound
from ..tools.server import Request, Response, Router, created, file_response
from ..tools.validation import boolean, choice, integer, text
from .app import SpadraApp
from . import impact as IM
from . import design_spec as DSP
from . import reconstruct as RC
from src.utils.spatial_formats.common.errors import SpatialFormatError
from .project import UNITS, slug

router = Router()


def _report(report: Any) -> Any:
    for attr in ("to_dict", "as_dict"):
        if hasattr(report, attr):
            return getattr(report, attr)()
    return {"text": str(report)}


# ------------------------------------------------------------------------------------------------------------------ project
@router.route("GET", "/api/bootstrap")
def bootstrap(app: SpadraApp, req: Request) -> Any:
    with app.lock:
        return app.bootstrap()


@router.route("POST", "/api/project/new")
def project_new(app: SpadraApp, req: Request) -> Any:
    body = req.json()
    with app.lock:
        project = app.new_project(text(body.get("name", "Untitled project"), "name", 80), choice(body.get("unit", "mm"), UNITS, "unit"),
                                  discard=boolean(body.get("discard", False), "discard"))
        return created(project.summary())


@router.route("POST", "/api/project/open")
def project_open(app: SpadraApp, req: Request) -> Any:
    with app.lock:
        body = req.json()
        return app.open_project(text(body.get("path"), "path", 1024), discard=boolean(body.get("discard", False), "discard")).summary()


@router.route("POST", "/api/project/save")
def project_save(app: SpadraApp, req: Request) -> Any:
    body = req.json()
    with app.lock:
        result = app.save_project(text(body["path"], "path", 1024) if body.get("path") else None,
                                  text(body["name"], "name", 80) if body.get("name") else None,
                                  save_as=boolean(body.get("save_as", False), "save_as"))
        return {**result, "project": app.require().summary()}


@router.route("GET", "/api/project/properties")
def project_properties(app: SpadraApp, req: Request) -> Any:
    with app.lock:
        return app.require().project_properties()


@router.route("POST", "/api/project/recover")
def project_recover(app: SpadraApp, req: Request) -> Any:
    body = req.json()
    with app.lock:
        return app.recover_project(text(body.get("backup"), "backup", 80), discard=boolean(body.get("discard", False), "discard")).summary()


@router.route("POST", "/api/project/close")
def project_close(app: SpadraApp, req: Request) -> Any:
    with app.lock:
        app.close_project(discard=boolean(req.json().get("discard", False), "discard"))
        return {"closed": True}


@router.route("GET", "/api/project/tree")
def project_tree(app: SpadraApp, req: Request) -> Any:
    with app.lock:
        project = app.require()
        return {"project": project.summary(), "tree": project.tree(), "levels": project.manifest["levels"]}


# -------------------------------------------------------------------------------------------------------------------- views
@router.route("GET", "/api/drawing/{view}")
def drawing(app: SpadraApp, req: Request) -> Any:
    with app.lock:
        return app.require().drawing_data(req.params["view"])


@router.route("GET", "/api/schedule/{view}")
def schedule(app: SpadraApp, req: Request) -> Any:
    with app.lock:
        return app.require().schedule(req.params["view"])


@router.route("GET", "/api/sheet/{sheet}")
def sheet(app: SpadraApp, req: Request) -> Any:
    with app.lock:
        return app.require().sheet_data(req.params["sheet"])


@router.route("GET", "/api/scene")
def scene(app: SpadraApp, req: Request) -> Any:
    with app.lock:
        t = req.query.get("time")
        try:
            seconds = float(t) if t not in (None, "") else None
        except ValueError:
            raise BadRequest("time must be a number of seconds") from None
        return app.require().scene_data(app.settings.max_scene_vertices, seconds)


@router.route("GET", "/api/animation")
def animation(app: SpadraApp, req: Request) -> Any:
    with app.lock:
        return app.require().animation_data()


@router.route("GET", "/api/animation/pose")
def animation_pose(app: SpadraApp, req: Request) -> Any:
    try:
        seconds = float(req.query.get("time") or 0.0)
    except ValueError:
        raise BadRequest("time must be a number of seconds") from None
    with app.lock:
        return app.require().animation_pose(text(req.query.get("id"), "id", 80), seconds)


@router.route("GET", "/api/engineering")
def engineering(app: SpadraApp, req: Request) -> Any:
    from .engineering_bridge import BRIDGE
    from .structural import LOAD_TYPES, SUPPORTS
    with app.lock:
        return {**BRIDGE.status(), "supports": list(SUPPORTS), "load_types": list(LOAD_TYPES)}


@router.route("POST", "/api/engineering/beam")
def engineering_beam(app: SpadraApp, req: Request) -> Any:
    body = req.json()
    with app.lock:
        return app.require().analyze_beam(text(body.get("id"), "id", 80), body)


@router.route("POST", "/api/quantities")
def quantities(app: SpadraApp, req: Request) -> Any:
    body = req.json()
    with app.lock:
        return app.require().quantities(body.get("ids"), body.get("densities"))


@router.route("GET", "/api/profiles")
def profiles(app: SpadraApp, req: Request) -> Any:
    from . import profiles as PR
    return PR.catalogue()


@router.route("POST", "/api/drainage")
def drainage(app: SpadraApp, req: Request) -> Any:
    body = req.json()
    with app.lock:
        return app.require().drainage(body.get("roof"))


@router.route("POST", "/api/drainage/trace")
def drainage_trace(app: SpadraApp, req: Request) -> Any:
    body = req.json()
    with app.lock:
        return app.require().trace_drainage(body.get("gutter"), body.get("offset", 0.0))


@router.route("POST", "/api/void/report")
def void_report(app: SpadraApp, req: Request) -> Any:
    body = req.json()
    with app.lock:
        return app.require().cutting_report(body.get("void"))


@router.route("POST", "/api/foundation/report")
def foundation_report(app: SpadraApp, req: Request) -> Any:
    body = req.json()
    with app.lock:
        return app.require().foundation_report(body.get("id"))


@router.route("POST", "/api/curtainwall/preview")
def curtainwall_preview(app: SpadraApp, req: Request) -> Any:
    with app.lock:
        return app.require().preview_curtainwall(req.json())


@router.route("POST", "/api/curtainwall/pick")
def curtainwall_pick(app: SpadraApp, req: Request) -> Any:
    with app.lock:
        return app.require().pick_part(req.json())


@router.route("GET", "/api/curtainwall/profiles")
def curtainwall_profiles(app: SpadraApp, req: Request) -> Any:
    return app.require().curtainwall_profiles()


@router.route("POST", "/api/gutters/preview")
def gutters_preview(app: SpadraApp, req: Request) -> Any:
    with app.lock:
        return app.require().preview_gutters(req.json())


@router.route("POST", "/api/roof/end/preview")
def roof_end_preview(app: SpadraApp, req: Request) -> Any:
    with app.lock:
        return app.require().preview_roof_end(req.json())


@router.route("POST", "/api/properties")
def properties(app: SpadraApp, req: Request) -> Any:
    with app.lock:
        return app.require().properties(req.json(), relations=True)


@router.route("GET", "/api/check")
def check(app: SpadraApp, req: Request) -> Any:
    with app.lock:
        return app.require().check()


@router.route("GET", "/api/sync")
def sync(app: SpadraApp, req: Request) -> Any:
    with app.lock:
        return app.require().sync_status()


# ----------------------------------------------------------------------------------------------------------------------- families
@router.route("GET", "/api/families")
def families(app: SpadraApp, req: Request) -> Any:
    from . import families as FM
    with app.lock:
        project = app.require()
        return {"families": FM.listing(project), "shapes": {k: list(v[1]) for k, v in FM.SHAPES.items()}, "types": list(FM.TYPES), "levels": project.manifest["levels"]}


@router.route("GET", "/api/families/templates")
def family_templates(app: SpadraApp, req: Request) -> Any:
    """The starter families (door, window, beam) that ``family.from_template`` can create. Read-only."""
    from . import family_templates as FT
    return {"templates": FT.catalog()}


@router.route("POST", "/api/families/formula")
def family_formula(app: SpadraApp, req: Request) -> Any:
    """Validate a formula against a family's parameters and preview its value. Read-only: nothing in the project changes."""
    from . import families as FM
    body = req.json()
    with app.lock:
        return FM.check_formula(app.require(), body)


@router.route("GET", "/api/doors/{door}/swing/verify")
def door_swing_verify(app: SpadraApp, req: Request) -> Any:
    """Sample the evaluated swing animation and check the smoothstep law, monotone motion, angle bounds and a fixed hinge axis. Read-only; kinematics only."""
    from . import door_swing as DS
    with app.lock:
        try:
            samples = int(req.query.get("samples", 49))
        except ValueError:
            raise BadRequest("samples must be a whole number")
        project = app.require()
        return DS.verify_motion(project, project._animatable(req.params["door"]), samples=samples)


@router.route("GET", "/api/doors/{door}/swing")
def door_swing_preview(app: SpadraApp, req: Request) -> Any:
    """How a door would open (the signed turn, and how far the leaf clears other elements). Read-only; ``degrees`` defaults to 90."""
    from . import door_swing as DS
    with app.lock:
        try:
            degrees = float(req.query.get("degrees", 90))
        except ValueError:
            raise BadRequest("degrees must be a number")
        try:
            tolerance = float(req.query.get("tolerance_mm", DS.PENETRATION * 1000))
        except ValueError:
            raise BadRequest("tolerance_mm must be a number")
        return DS.plan(app.require(), req.params["door"], degrees, tolerance_mm=tolerance)


@router.route("GET", "/api/scripts")
def scripts_list(app: SpadraApp, req: Request) -> Any:
    """The project's scripts with their versions, and what isolation this machine actually enforces."""
    from . import scripting as SC
    with app.lock:
        return SC.describe(app.require())


@router.route("GET", "/api/scripts/{script}")
def script_source(app: SpadraApp, req: Request) -> Any:
    from . import scripting as SC
    with app.lock:
        return SC.source_of(app.require(), req.params["script"], req.query.get("version"))


@router.route("POST", "/api/scripts/propose")
def script_propose(app: SpadraApp, req: Request) -> Any:
    """Make a reviewable proposal from a previewed script run. Nothing changes until a person approves it through the assistant."""
    from . import scripting as SC
    from .assistant import ASSISTANT
    body = req.json()
    with app.lock:
        project = app.require()
        pl = SC.plan(project, body)
        rec = pl["run"]
        steps = pl["steps"] + [{"op": "script.record", "params": {"record": pl["record"]}}]
        msg = ASSISTANT.propose_commands(project, source=f"script:{rec['name']} v{rec['version']}", title=f"Apply the result of script “{rec['name']}” (version {rec['version']})",
                                         explanation=(f"The script ran in {rec['isolation']} isolation on a copy of the model at revision {rec['revision']} and returned {len(rec['outputs'])} validated "
                                                      f"output(s). Approving writes them to {len(pl['steps'])} parameter target(s) and records the script version, source hash, seed and result hash."),
                                         steps=steps, targets=[s["params"].get("id") for s in pl["steps"] if s["op"] == "model.set_property"],
                                         evidence={k: pl["record"][k] for k in ("script", "version", "sha256", "seed", "result_sha256")}, evidence_kind="script")
        return {"message": msg, "project": project.summary()}


@router.route("POST", "/api/optimize/family/propose")
def optimize_propose(app: SpadraApp, req: Request) -> Any:
    """Run an optimization and, when it found a feasible buildable point, make a reviewable proposal from it. Nothing changes until a person approves."""
    from . import optimization as OPZ
    from .assistant import ASSISTANT
    with app.lock:
        project = app.require()
        out = OPZ.optimize_family(project, req.json())
        prop = out.get("proposal") or {}
        if not prop.get("commands"):
            return {"result": out, "message": None}
        msg = ASSISTANT.propose_commands(project, source="optimization", title="Apply the optimized parameter values",
                                         explanation=(f"A deterministic search ({out['evaluations']} evaluations, seed {out['seed']}) found values that satisfy the stated constraints. "
                                                      "It is a search result, not a proof of optimality. Approving changes the family type only."),
                                         steps=[{"op": c["command"], "params": c["params"]} for c in prop["commands"]], evidence={k: out[k] for k in ("status", "result_sha256", "objectives", "constraints", "seed")},
                                         evidence_kind="optimization")
        return {"result": out, "message": msg}


@router.route("GET", "/api/model-constraints")
def model_constraints(app: SpadraApp, req: Request) -> Any:
    """The stored constraints between walls, beams and columns, with their values in the project's units and whether each reference still resolves."""
    from . import model_constraints as MC
    with app.lock:
        return {"constraints": MC.describe(app.require())}


@router.route("POST", "/api/model-constraints/analyze")
def model_constraints_analyze(app: SpadraApp, req: Request) -> Any:
    """Solve a copy of the model's constraints and report status, degrees of freedom, conflicts and where elements would move. Read-only."""
    from . import model_constraints as MC
    body = req.json()
    with app.lock:
        return MC.analyze(app.require(), body.get("fixed") or [])


@router.route("POST", "/api/families/draft")
def family_draft(app: SpadraApp, req: Request) -> Any:
    """A requirement in words -> a typed family draft, from a fixed set of patterns (no language model; see ``family_nl``). Read-only: it creates nothing, lists what it read,
    what it could not model and the choices it left open (``overrides`` answers them with values in mm)."""
    from . import family_nl as NL
    body = req.json()
    return NL.interpret(body.get("text"), overrides=body.get("overrides"))


@router.route("POST", "/api/families/draft/propose")
def family_draft_propose(app: SpadraApp, req: Request) -> Any:
    """Make the draft a reviewable proposal (dry-run on a scratch copy; nothing changes until a person approves). Refused while a blocking choice is neither answered
    (``overrides``) nor accepted at its template default (``accept``: parameter names, or ``["all"]``)."""
    from . import family_nl as NL
    body = req.json()
    with app.lock:
        out = NL.propose(app.require(), body.get("text"), accept=body.get("accept") or (), overrides=body.get("overrides"))
        return {"draft": out["draft"], "message": out["proposal"]}


@router.route("POST", "/api/optimize/beam")
def optimize_beam(app: SpadraApp, req: Request) -> Any:
    """Minimise a rectangular beam family's volume over its width and height under a declared deflection limit (support, load, line load and modulus must all be given:
    a missing one gives status ``unavailable``, never a default). With ``propose: true`` a feasible result becomes a reviewable proposal; nothing changes until approved."""
    from . import optimization as OPZ
    from .assistant import ASSISTANT
    body = req.json()
    with app.lock:
        project = app.require()
        out = OPZ.optimize_beam_family(project, body)
        prop = out.get("proposal") or {}
        if not body.get("propose") or not prop.get("commands"):
            return {"result": out, "message": None}
        msg = ASSISTANT.propose_commands(project, source="optimization", title="Apply the beam section from the deflection-limited search",
                                         explanation=("A deterministic search found a section that meets the stated deflection limit for the declared support, load and modulus. "
                                                      "It checks stiffness only: strength, buckling, vibration, connections and codes are not evaluated. Approving changes the family type only."),
                                         steps=[{"op": c["command"], "params": c["params"]} for c in prop["commands"]],
                                         evidence={k: out[k] for k in ("status", "result_sha256", "objectives", "constraints", "seed")}, evidence_kind="optimization")
        return {"result": out, "message": msg}


@router.route("POST", "/api/workflows/design")
def design_workflow(app: SpadraApp, req: Request) -> Any:
    """Run a typed, revision-aware design workflow for a contract (project, expected revision, family, parameters, objective, budgets, correlation key). Every stage reports
    success, partial, unavailable, infeasible, timeout, stale or failed with the component and revision that produced it. Nothing changes; with ``propose: true`` a result whose
    evidence is complete enough becomes a reviewable proposal (approval is still a person's), and a project that moved on since the expected revision is ``stale``."""
    from . import design_workflow as DW
    from .assistant import ASSISTANT
    body = req.json()
    propose = bool(body.pop("propose", False)) if isinstance(body, dict) else False
    with app.lock:
        project = app.require()
        out = DW.run(project, body)
        if not propose or not out["commit_allowed"]:
            return {"result": out, "message": None}
        prop = out["proposal"]
        msg = ASSISTANT.propose_commands(project, source="workflow", title="Apply the design workflow's candidate",
                                         explanation=(f"Workflow {out['correlation_key']} ({out['status']}): a deterministic search found a candidate that satisfies the stated constraints and was "
                                                      f"re-evaluated independently. Not run: {', '.join(out['not_run_or_unavailable']) or 'nothing'}. Approving changes the family type only."),
                                         steps=[{"op": c["command"], "params": c["params"]} for c in prop["commands"]],
                                         evidence={"schema": out["schema"], "status": out["status"], "correlation_key": out["correlation_key"], "contract_sha256": out["contract_sha256"],
                                                   "stages": [{k: s[k] for k in ("stage", "status", "agent", "revision")} for s in out["stages"]]},
                                         evidence_kind="optimization")
        return {"result": out, "message": msg}


@router.route("POST", "/api/optimize/family/alternatives")
def optimize_alternatives(app: SpadraApp, req: Request) -> Any:
    """Several different feasible, buildable designs (deterministic for a seed). With ``propose: true`` they are offered as one reviewable proposal that adds each as a
    named type; nothing changes until a person approves."""
    from . import optimization as OPZ
    from .assistant import ASSISTANT
    body = req.json()
    with app.lock:
        project = app.require()
        out = OPZ.alternatives_family(project, body)
        steps = [{"op": a["command"]["command"], "params": a["command"]["params"]} for a in out["alternatives"] if a.get("command")]
        if not body.get("propose") or not steps:
            return {"result": out, "message": None}
        msg = ASSISTANT.propose_commands(project, source="alternatives", title=f"Add {len(steps)} design option(s) as family types",
                                         explanation=(f"A deterministic sample ({out['evaluations']} candidates, seed {out['seed']}) gave {len(steps)} different designs that satisfy the stated constraints and can be built. "
                                                      "They are samples, not a proof that nothing better exists. Approving adds each as a named type; nothing else changes."),
                                         steps=steps, evidence={k: out[k] for k in ("status", "result_sha256", "objectives", "constraints", "seed")}, evidence_kind="optimization")
        return {"result": out, "message": msg}


@router.route("POST", "/api/scripts/check")
def script_check(app: SpadraApp, req: Request) -> Any:
    """Syntax and the conservative scan, without storing or running anything."""
    from . import scripting as SC
    return SC.check_source(req.json().get("source"))


@router.route("POST", "/api/scripts/run")
def script_run(app: SpadraApp, req: Request) -> Any:
    """Preview: run a version on a copy of the model. The project does not change; ``script.apply`` is what commits."""
    from . import scripting as SC
    with app.lock:
        return SC.run(app.require(), req.json())


@router.route("POST", "/api/scripts/replay")
def script_replay(app: SpadraApp, req: Request) -> Any:
    """Run a recorded run again from its record and report whether the result hash is reproduced, or why the comparison is not fair (the model changed)."""
    from . import scripting as SC
    with app.lock:
        return SC.replay(app.require(), req.json())


@router.route("POST", "/api/stairs/design")
def stair_design(app: SpadraApp, req: Request) -> Any:
    """Preview a stair design for a rise (mm), without creating anything: the riser interval, every candidate and why it passed or failed."""
    from . import stairs as SR
    from ..tools.validation import number
    body = req.json()
    rules = SR.rules_from(body.get("rules"))
    height = number(body.get("height"), "height", 51, 100000) / 1000.0
    return SR.design(height, rules=rules, risers=int(number(body["risers"], "risers", 2, SR.MAX_RISERS)) if body.get("risers") is not None else None,
                     tread=number(body["tread"], "tread", 50, 2000) / 1000.0 if body.get("tread") is not None else None,
                     available=number(body["available"], "available", 100, 1e6) / 1000.0 if body.get("available") is not None else None)


@router.route("GET", "/api/stairs/{stair}/headroom")
def stair_headroom(app: SpadraApp, req: Request) -> Any:
    from . import stairs as SR
    with app.lock:
        minimum = req.query.get("minimum")
        return SR.headroom(app.require(), req.params["stair"], float(minimum) / 1000.0 if minimum else None)


@router.route("GET", "/api/procedural")
def procedural_list(app: SpadraApp, req: Request) -> Any:
    from . import procedural as PG
    with app.lock:
        return {"groups": PG.describe(app.require())}


@router.route("POST", "/api/drawings/constraints/analyze")
def drawing_constraints_analyze(app: SpadraApp, req: Request) -> Any:
    """Degrees of freedom, redundant and conflicting constraints and reference states of a drawing's constraints. Read-only."""
    from ..tools.validation import text
    body = req.json()
    with app.lock:
        doc = app.require().drawing(text(body.get("drawing"), "drawing", 80))
        return doc.analyze_constraints(body.get("constraints"), fixed_ids=body.get("fixed_ids") or [])


@router.route("POST", "/api/optimize/family")
def optimize_family(app: SpadraApp, req: Request) -> Any:
    """Search a family's parameters for the best values against formula objectives and constraints. A proposal only: the project does not change."""
    from . import optimization as OPZ
    with app.lock:
        return OPZ.optimize_family(app.require(), req.json())


@router.route("GET", "/api/formula/functions")
def formula_functions(app: SpadraApp, req: Request) -> Any:
    from . import families as FM
    return {"functions": FM.functions()}


@router.route("GET", "/api/families/{family}/file")
def family_file(app: SpadraApp, req: Request) -> Any:
    from . import families as FM
    with app.lock:
        project = app.require()
        data = FM.export_family_file(project, req.params["family"])
        return file_response(data, f"{slug(FM.family(project, req.params['family'])['name'])}.sgeo", "application/octet-stream")


# ------------------------------------------------------------------------------------------------------------- SLAI agents
@router.route("GET", "/api/agents")
def agents(app: SpadraApp, req: Request) -> Any:
    from .agent_hub import HUB
    with app.lock:
        return {"agents": HUB.status(), "calls": HUB.calls[-50:]}


@router.route("POST", "/api/connections")
def connections(app: SpadraApp, req: Request) -> Any:
    with app.lock:
        return app.require().connections(req.json())


@router.route("POST", "/api/connections/propose")
def connections_propose(app: SpadraApp, req: Request) -> Any:
    with app.lock:
        project = app.require()
        return {"message": project.propose_connection_fix(req.json()), "project": project.summary()}


@router.route("GET", "/api/agents/roles")
def agents_roles(app: SpadraApp, req: Request) -> Any:
    """How each agent role named for parametric design / reconstruction / impact work maps to SPADRA, and whether that agent is really available now."""
    from .agent_hub import roles
    with app.lock:
        return {"roles": roles()}


@router.route("GET", "/api/agents/health")
def agents_health(app: SpadraApp, req: Request) -> Any:
    from .agent_hub import HUB
    with app.lock:
        return HUB.health()


@router.route("GET", "/api/simulation")
def simulation_status(app: SpadraApp, req: Request) -> Any:
    from . import simulation_bridge as SB
    with app.lock:
        return SB.status()


@router.route("POST", "/api/simulation/beam")
def simulation_beam(app: SpadraApp, req: Request) -> Any:
    from . import simulation_bridge as SB
    body = req.json()
    with app.lock:
        return SB.simulate_beam(app.require(), text(body.get("id"), "id", 80), body)


@router.route("GET", "/api/optimization")
def optimization_status(app: SpadraApp, req: Request) -> Any:
    from . import optimization_bridge as OB
    with app.lock:
        return OB.status()


@router.route("POST", "/api/optimization/beam")
def optimization_beam(app: SpadraApp, req: Request) -> Any:
    from . import optimization_bridge as OB
    body = req.json()
    with app.lock:
        return OB.optimize_beam(app.require(), text(body.get("id"), "id", 80), body)


@router.route("GET", "/api/spatial")
def spatial_status(app: SpadraApp, req: Request) -> Any:
    from .spatial_bridge import BRIDGE
    with app.lock:
        return BRIDGE.status()


@router.route("POST", "/api/spatial/connected")
def spatial_connected(app: SpadraApp, req: Request) -> Any:
    from .spatial_bridge import BRIDGE
    body = req.json()
    with app.lock:
        return BRIDGE.connected(app.require(), text(body.get("id"), "id", 80))


@router.route("POST", "/api/spatial/nearby")
def spatial_nearby(app: SpadraApp, req: Request) -> Any:
    from .spatial_bridge import BRIDGE
    body = req.json()
    with app.lock:
        return BRIDGE.nearby(app.require(), text(body.get("id"), "id", 80), body.get("radius_m"))


@router.route("GET", "/api/calculator")
def calculator_status(app: SpadraApp, req: Request) -> Any:
    from . import calculator as C
    with app.lock:
        return C.status()


@router.route("POST", "/api/calculator/convert")
def calculator_convert(app: SpadraApp, req: Request) -> Any:
    from . import calculator as C
    with app.lock:
        return C.convert(app.project, req.json())


@router.route("POST", "/api/calculator/evaluate")
def calculator_evaluate(app: SpadraApp, req: Request) -> Any:
    from . import calculator as C
    with app.lock:
        return C.evaluate(app.project, req.json())


@router.route("POST", "/api/calculator/constraints")
def calculator_constraints(app: SpadraApp, req: Request) -> Any:
    from . import calculator as C
    with app.lock:
        return C.check_constraints(app.project, req.json())


@router.route("POST", "/api/calculator/parameters")
def calculator_parameters(app: SpadraApp, req: Request) -> Any:
    from . import calculator as C
    body = req.json()
    ids = body.get("ids")
    if not isinstance(ids, list):
        raise BadRequest("ids must be a list of element ids")
    with app.lock:
        return C.element_parameters(app.require(), [text(i, "id", 80) for i in ids])


@router.route("POST", "/api/calculator/measure")
def calculator_measure(app: SpadraApp, req: Request) -> Any:
    from . import calculator as C
    body = req.json()
    ids = body.get("ids")
    if not isinstance(ids, list):
        raise BadRequest("ids must be a list of element ids")
    with app.lock:
        return C.measure(app.require(), [text(i, "id", 80) for i in ids])


# ----------------------------------------------------------------------------------------------------------- classifications
@router.route("GET", "/api/classifications")
def classification_info(app: SpadraApp, req: Request) -> Any:
    from . import classifications as CL
    return CL.info()


@router.route("GET", "/api/classifications/search")
def classification_search(app: SpadraApp, req: Request) -> Any:
    """Search the official IFC type catalog or locally licensed OmniClass and assembly entries."""
    from . import classifications as CL
    kind = choice(req.query.get("kind", "ifc"), ("ifc", "omniclass", "assembly"), "kind")
    q = text(req.query.get("q") or "", "q", 100, empty=True)
    try:
        table, limit = int(req.query.get("table", "21")), int(req.query.get("limit", "30"))
    except (ValueError, TypeError):
        raise BadRequest("table and limit must be whole numbers") from None
    if table not in CL.OMNICLASS_TABLES:
        raise BadRequest("Unknown OmniClass table; use /api/classifications for supported tables")
    limit = integer(limit, "limit", 1, 200)
    return {"results": CL.search(kind, q, table=table, limit=limit)}


@router.route("GET", "/api/classifications/browse")
def classification_browse(app: SpadraApp, req: Request) -> Any:
    """One level of the imported UniFormat / assembly-code tree under ``parent`` (top level when absent)."""
    from . import classifications as CL
    return CL.browse(choice(req.query.get("kind", "assembly"), ("assembly",), "kind"), text(req.query.get("parent") or "", "parent", 100, empty=True))


# ---------------------------------------------------------------------------------------------------------------- NL-SfB
@router.route("GET", "/api/sfb")
def sfb_info(app: SpadraApp, req: Request) -> Any:
    from . import sfb
    return sfb.info()


@router.route("GET", "/api/sfb/search")
def sfb_search(app: SpadraApp, req: Request) -> Any:
    """Codes of one table (``table`` 0 to 4; all when absent) whose number or name matches ``q``."""
    from . import sfb
    def whole(name: str, default: str | None, low: int, high: int) -> int | None:
        raw = req.query.get(name) or default                                     # a query string is text; a whole number is what the search wants
        if raw is None:
            return None
        if not raw.lstrip("-").isdigit():
            raise BadRequest(f"{name} must be a whole number from {low} to {high}")
        return integer(int(raw), name, low, high)

    return {"results": sfb.search(text(req.query.get("q") or "", "q", 80, empty=True), whole("table", None, 0, 4), whole("limit", "30", 1, 200) or 30)}


# ---------------------------------------------------------------------------------------------------------------- assistant
@router.route("GET", "/api/assistant")
def assistant_history(app: SpadraApp, req: Request) -> Any:
    from .assistant import ASSISTANT
    with app.lock:
        return ASSISTANT.history(app.require())


@router.route("POST", "/api/assistant/ask")
def assistant_ask(app: SpadraApp, req: Request) -> Any:
    from .assistant import ASSISTANT
    body = req.json()
    with app.lock:
        project = app.require()
        message = ASSISTANT.ask(project, body.get("request"), body.get("context"))
        return {"message": message, "project": project.summary()}


@router.route("POST", "/api/assistant/approve")
def assistant_approve(app: SpadraApp, req: Request) -> Any:
    from .assistant import ASSISTANT
    body = req.json()
    expected = integer(body["expected_revision"], "expected_revision", 0, 2**31) if body.get("expected_revision") is not None else None
    with app.lock:
        message = app.commits.approve(text(body.get("proposal"), "proposal", 80), expected_revision=expected,
                                      confirm_high_risk=boolean(body.get("confirm_high_risk", False), "confirm_high_risk"))
        return {"message": message, "project": app.require().summary()}


@router.route("POST", "/api/assistant/reject")
def assistant_reject(app: SpadraApp, req: Request) -> Any:
    from .assistant import ASSISTANT
    with app.lock:
        return {"proposal": ASSISTANT.reject(app.require(), req.json().get("proposal"))}


@router.route("POST", "/api/assistant/cancel")
def assistant_cancel(app: SpadraApp, req: Request) -> Any:
    from .assistant import ASSISTANT
    with app.lock:
        return {"proposal": ASSISTANT.cancel(app.require(), req.json().get("proposal"))}


# ----------------------------------------------------------------------------------------------------------------- commands
@router.route("POST", "/api/command")
def command(app: SpadraApp, req: Request) -> Any:
    body = req.json()
    expected = integer(body["expected_revision"], "expected_revision", 0, 2**31) if body.get("expected_revision") is not None else None
    params = body.get("params", {})
    if not isinstance(params, dict):
        raise BadRequest("params must be an object")
    op = text(body.get("op"), "op", 60)
    with app.lock:
        project = app.require()
        try:
            outcome = project.execute(op, params, expected_revision=expected)
        except Exception as exc:                                       # the interface gets the error as before; the log gets it too
            app.diagnostics.record(f"command:{op}", exc, project=project.name, revision=project.revision)
            raise
        return {**outcome, "project": project.summary()}


@router.route("POST", "/api/undo")
def undo(app: SpadraApp, req: Request) -> Any:
    with app.lock:
        project = app.require()
        return {**project.undo(), "project": project.summary()}


@router.route("POST", "/api/redo")
def redo(app: SpadraApp, req: Request) -> Any:
    with app.lock:
        project = app.require()
        return {**project.redo(), "project": project.summary()}


# ------------------------------------------------------------------------------------------------------------------ exports
@router.route("GET", "/api/interop")
def interop(app: SpadraApp, req: Request) -> Any:
    from .interop import availability
    return availability()


@router.route("POST", "/api/import/inspect")
def import_inspect(app: SpadraApp, req: Request) -> Any:
    """Look into a file before importing it: its real type, whether it parses, version, unit, contents and anything suspicious. Changes nothing."""
    from .interop import decode, inspect_file
    body = req.json()
    raw = decode(body.get("data"))
    with app.lock:
        app.require()
    return inspect_file(choice(body.get("target", "model"), ("model", "drawing"), "target"), text(body.get("format"), "format", 8).lower(), raw,
                        name=text(body.get("name") or "file", "name", 120), unit=text(body["unit"], "unit", 8) if body.get("unit") else None)


@router.route("GET", "/api/export/drawing/{view}")
def export_drawing(app: SpadraApp, req: Request) -> Any:
    from .interop import DRAWING_EXPORT, MEDIA, export_drawing as export
    fmt = choice(req.query.get("format", "svg"), DRAWING_EXPORT, "format")
    with app.lock:
        project = app.require()
        doc, view = project.drawing(req.params["view"]), project.view(req.params["view"])
        data, report = export(doc, fmt, name=view["name"])
        if req.query.get("report"):
            return {"report": report}
        return file_response(data, f"{slug(view['name'])}.{fmt}", MEDIA[fmt])


@router.route("GET", "/api/report/{report}")
def report_view(app: SpadraApp, req: Request) -> Any:
    from . import reports as RP
    with app.lock:
        return RP.view(app.require(), req.params["report"])


@router.route("GET", "/api/export/report/{report}")
def export_report(app: SpadraApp, req: Request) -> Any:
    from . import reports as RP
    with app.lock:
        data, entry = RP.read(app.require(), req.params["report"])
        return file_response(data, f"{slug(entry['name'])}.sldc", "application/x-slai-sldc+zip")


@router.route("GET", "/api/export/sheet/{sheet}")
def export_sheet(app: SpadraApp, req: Request) -> Any:
    """Require a rendered page; never quietly export the old A4 text report as a drawing sheet."""
    from . import sheets as SH
    if req.query.get("legacy_text") != "1":
        from ..tools.errors import Conflict
        raise Conflict("A drawing-sheet SLDC export needs its print-matched page rendering",
                       hint="Open the sheet in SPADRA and use Export .sldc. For an old text-only report, request ?legacy_text=1 explicitly.")
    with app.lock:
        data, name = SH.to_sldc(app.require(), req.params["sheet"])
        return file_response(data, name, "application/x-slai-sldc+zip")


@router.route("POST", "/api/export/sheet/{sheet}")
def export_sheet_printable(app: SpadraApp, req: Request) -> Any:
    """Preserve the live SVG sheet as a full-size visual plus editable semantic sheet data."""
    import base64
    import binascii
    from . import sheets as SH
    body = req.json()
    raw = body.get("preview_png")
    vector = body.get("preview_svg")
    if not isinstance(raw, str) or len(raw) > 12_000_000:
        raise BadRequest("A printable PNG preview of the sheet is required")
    if not isinstance(vector, str) or not vector.startswith("<svg") or len(vector.encode("utf-8")) > 5_000_000:
        raise BadRequest("The full printable SVG sheet is required and must be at most 5 MB")
    try:
        preview = base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise BadRequest("The printable preview is not valid base64") from exc
    with app.lock:
        project = app.require()
        if body.get("revision") != project.revision:
            from ..tools.errors import Conflict
            raise Conflict("The project changed while the sheet was being exported; try again")
        data, name = SH.to_sldc(project, req.params["sheet"], preview, vector)
        return {"data": base64.b64encode(data).decode("ascii"), "filename": name}


@router.route("GET", "/api/export/sheet/{sheet}/verify")
def verify_sheet_export(app: SpadraApp, req: Request) -> Any:
    """Export the sheet, read the file back and list every difference from the sheet as it is now (none means the file preserves it)."""
    from . import sheets as SH
    with app.lock:
        project = app.require()
        data, _ = SH.to_sldc(project, req.params["sheet"])
        return SH.verify_sldc(project, data, req.params["sheet"])


@router.route("POST", "/api/modifier/preview")
def modifier_preview(app: SpadraApp, req: Request) -> Any:
    """Read-only native modifier preview evaluated on the project's scratch copy.

    A preview uses exactly the production command and kernel; it cannot change
    the source revision or undo stack, and it returns bounded derived geometry.
    """
    from . import documentation as D
    from ..tools.errors import LimitExceeded
    with app.lock:
        project = app.require()
        params = req.json()
        if not isinstance(params, dict):
            raise BadRequest("Modifier preview must be an object")
        kind = params.get("kind")
        op = "model.modifier" if kind == "model" else "draw.modifier" if kind == "drawing" else ""
        if not op:
            raise BadRequest("Preview kind must be model or drawing")
        draft = project.scratch()
        request = {k: v for k, v in params.items() if k != "kind"}
        if kind == "model":
            oid = text(params.get("id"), "id", 80)
            before_vertices, before_faces = D.world_mesh(project, oid)
            try:
                result = draft.execute(op, request)["result"]
                failure = None
            except (BadRequest, SpatialFormatError) as exc:             # the kernel's refusal is the finding: report it with the analysis instead of a bare error
                result, failure = None, exc
            analysis = IM.analyze_model_modifier(project, draft, request, result, failure)
            if result is None:                                                # the same 400 as before, now carrying the analysis (what was measured, what is affected)
                raise BadRequest(str(failure), hint=getattr(failure, "hint", None), details={"analysis": analysis})
            after_vertices, after_faces = D.world_mesh(draft, result["id"])
            if len(after_faces) > 25000 or len(after_vertices) > 30000:
                raise LimitExceeded("Modifier preview exceeds the interactive geometry budget")
            return {"revision": project.revision, "result": result,
                    "before": {"vertices": before_vertices, "faces": before_faces},
                    "after": {"vertices": after_vertices, "faces": after_faces}, "analysis": analysis}
        view = text(params.get("drawing"), "drawing", 80)
        source = text(params.get("id"), "id", 80)
        previous = project.drawing(view).get(source)
        try:
            result = draft.execute(op, request)["result"]
            failure = None
        except (BadRequest, SpatialFormatError) as exc:
            result, failure = None, exc
        analysis = IM.analyze_drawing_modifier(project, draft, request, result, failure)
        if result is None:
            raise BadRequest(str(failure), hint=getattr(failure, "hint", None), details={"analysis": analysis})
        current = draft.drawing(view).get(result["id"]) if result else None
        return {"revision": project.revision, "result": result, "before": previous, "after": current, "analysis": analysis}


@router.route("GET", "/api/specs")
def specs_list(app: SpadraApp, req: Request) -> Any:
    """The project's design specifications and what this installation can run (constraint kinds, operation contracts, backends probed rather than assumed)."""
    with app.lock:
        return {"specs": DSP.list_specs(app.require()), "capabilities": DSP.capabilities()}


@router.route("GET", "/api/specs/{id}")
def specs_get(app: SpadraApp, req: Request) -> Any:
    with app.lock:
        ent = DSP.entry(app.require(), req.params["id"])
        return {"id": req.params["id"], "revision": ent["revision"], "spec": ent["spec"], "state": ent.get("state"), "values": ent.get("values") or {},
                "status": ent.get("status"), "ok": ent.get("ok")}


@router.route("POST", "/api/specs/check")
def specs_check(app: SpadraApp, req: Request) -> Any:
    """Static validation of a specification that is not stored: contracts, references, cycles, parameter table. Touches nothing."""
    with app.lock:
        spec = DSP.normalise(req.json().get("spec"))
        diags, _, order = DSP.static_check(spec)
        return {"ok": not any(d["severity"] == "error" for d in diags), "diagnostics": diags, "order": order}


@router.route("POST", "/api/specs/{id}/verify")
def specs_verify(app: SpadraApp, req: Request) -> Any:
    """Run the specification on the project's throw-away copy and return the full report with measured evidence. Nothing in the project changes."""
    with app.lock:
        project = app.require()
        body = req.json()
        return {"revision": project.revision, "report": DSP.verify(project, req.params["id"], body.get("overrides"), body.get("on_drift"))}


@router.route("POST", "/api/specs/{id}/repairs")
def specs_repairs(app: SpadraApp, req: Request) -> Any:
    """Re-validated repair proposals (parameter changes, constraint removals). None is applied; each carries the commands that would apply it."""
    with app.lock:
        project = app.require()
        body = req.json()
        return DSP.propose_repairs(project, req.params["id"], body.get("overrides"))


@router.route("POST", "/api/specs/{id}/repairs/propose")
def specs_repairs_propose(app: SpadraApp, req: Request) -> Any:
    """Turn one re-validated repair into an assistant proposal (dry-run on a scratch copy, then the person approves or rejects). Nothing is applied here."""
    from .assistant import ASSISTANT
    with app.lock:
        project = app.require()
        body = req.json()
        sid = req.params["id"]
        out = DSP.propose_repairs(project, sid, body.get("overrides"))
        index = integer(body.get("index", 0), "index", 0, 10_000)
        if index >= len(out["proposals"]):
            raise BadRequest("There is no repair with that index", details={"proposals": len(out["proposals"]), "unrepairable": out["unrepairable"]})
        r = out["proposals"][index]
        steps = [{"op": c["op"], "params": c["params"]} for c in r["commands"]]
        message = ASSISTANT.propose_commands(
            project, source="spec-repair", title=f"Set {r['parameter']} to {r['to']:g} in specification {sid}", steps=steps, targets=[], risk="medium",
            explanation=(f"Verification of the specification failed ({r['fixes']}). Changing the parameter {r['parameter']} from {r['from']} to {r['to']:g} re-verified "
                         f"{'with every expectation met' if r['revalidated'] else 'but expectations still fail: ' + '; '.join(r['remaining_failures'])}. "
                         "The specification itself is not edited; approving runs it once with this value."),
            evidence=r, evidence_kind="spec-repair")
        return {"message": message, "project": project.summary()}


@router.route("GET", "/api/reconstruct/capabilities")
def reconstruct_capabilities(app: SpadraApp, req: Request) -> Any:
    """What 2D-to-BIM reconstruction can read in this installation (raster needs OpenCV and Tesseract for printed dimensions, PDF needs pdfplumber) and its stated limits."""
    return RC.capabilities()


@router.route("POST", "/api/reconstruct/run")
def reconstruct_run(app: SpadraApp, req: Request) -> Any:
    """Read a dimensioned plan (``kind`` raster or pdf with base64 ``data``, or sdrw with the ``drawing`` view) and return the reconstruction: scale, dimensions, walls, openings, spaces,
    the constraint report, diagnostics with a confidence on everything, and an ``overlay`` in source coordinates to check it against. ``options`` and ``corrections`` re-run it
    deterministically. Read-only: nothing is created until ``reconstruct.build`` / ``reconstruct.draw`` is executed with the result."""
    from .interop import decode
    body = req.json()
    kind = choice(body.get("kind"), ("raster", "pdf", "sdrw"), "kind")
    with app.lock:
        project = app.require()
        source: dict[str, Any] = {"kind": kind}
        if kind == "sdrw":
            source["drawing"] = text(body.get("drawing"), "drawing", 80)
        else:
            source["data"] = decode(body.get("data"), label="plan")
        return RC.run(source, body.get("options"), body.get("corrections"), project=project)


@router.route("POST", "/api/modifier/predict")
def modifier_predict(app: SpadraApp, req: Request) -> Any:
    """The deterministic prediction for a model modifier, without running the kernel: cheap enough to call on every keystroke (``analysis.predicted`` of a full preview
    is the same data). Read-only."""
    with app.lock:
        project = app.require()
        params = req.json()
        if not isinstance(params, dict):
            raise BadRequest("Modifier prediction must be an object")
        oid = text(params.get("id"), "id", 80)
        before = IM.element_state(project, oid)
        return {"revision": project.revision, "target": oid, "before": before, "predicted": IM.predict(project, params), "learned": IM.learned_prediction()}


@router.route("GET", "/api/model/{id}/layers/{layer}/preview")
def layer_preview(app: SpadraApp, req: Request) -> Any:
    """What a layer would be cut by if its openings were set like this (``?openings=all|none|selected&ids=a,b``): read-only, nothing is changed."""
    with app.lock:
        ids = [i for i in str(req.query.get("ids", "")).split(",") if i]
        return app.require().layer_preview(req.params["id"], req.params["layer"], str(req.query.get("openings", "all")), ids)


@router.route("POST", "/api/model/{id}/layers/preview")
def preview_new_layer(app: SpadraApp, req: Request) -> Any:
    """A real, read-only geometry preview: execute the exact command in the project's throw-away copy.

    No revision, undo history, model geometry or file in the user's project is touched.
    Any collision/geometry error is the same error the real commit would raise.
    """
    from . import documentation as D
    with app.lock:
        project = app.require()
        params = req.json()
        oid = req.params["id"]
        before = D.world_mesh(project, oid)
        draft = project.scratch()
        result = draft.execute("model.add_layer", {**params, "id": oid})["result"]
        after = D.world_mesh(draft, oid)
        if len(after[1]) > 25000:
            from ..tools.errors import LimitExceeded
            raise LimitExceeded("The layer preview is too complex to draw")
        return {"revision": project.revision, "layer": result["layer"],
                "before": {"vertices": before[0], "faces": before[1]},
                "after": {"vertices": after[0], "faces": after[1]}}


@router.route("GET", "/api/export/schedule/{view}")
def export_schedule(app: SpadraApp, req: Request) -> Any:
    from .documentation import schedule_csv
    with app.lock:
        project = app.require()
        data = project.schedule(req.params["view"])
        return file_response(schedule_csv(data).encode("utf-8-sig"), f"{slug(data['view']['name'])}.csv", "text/csv; charset=utf-8")    # BOM: Excel opens it as UTF-8


@router.route("GET", "/api/export/model")
def export_model(app: SpadraApp, req: Request) -> Any:
    from .interop import EXTENSION, MEDIA, MODEL_EXPORT, export_model as export
    fmt = choice(req.query.get("format", "glb"), MODEL_EXPORT, "format")
    ids = [i for i in req.query.get("ids", "").split(",") if i] or None
    with app.lock:
        project = app.require()
        if not project.model.query_ids(limit=1):
            raise NotFound("The model is empty, so there is nothing to export")
        data, report = export(project, fmt, ids)
        if req.query.get("report"):
            return {"report": report}
        return file_response(data, f"{slug(project.name)}.{EXTENSION[fmt]}", MEDIA[fmt])


# ------------------------------------------------------------------------------------------------------------ background jobs
@router.route("POST", "/api/jobs")
def job_start(app: SpadraApp, req: Request) -> Any:
    """Start a background operation: ``{"kind": "import.model", "params": {...}}``. The answer is immediate; progress is read from ``/api/jobs/{id}``."""
    body = req.json()
    return app.jobs.submit(text(body.get("kind"), "kind", 40), body.get("params") or {}).public()


@router.route("GET", "/api/jobs")
def job_list(app: SpadraApp, req: Request) -> Any:
    return {"jobs": app.jobs.list()}


@router.route("GET", "/api/jobs/{job}")
def job_get(app: SpadraApp, req: Request) -> Any:
    return app.jobs.get(req.params["job"]).public()


@router.route("POST", "/api/jobs/{job}/cancel")
def job_cancel(app: SpadraApp, req: Request) -> Any:
    return app.jobs.cancel(req.params["job"]).public()


@router.route("POST", "/api/jobs/{job}/dismiss")
def job_dismiss(app: SpadraApp, req: Request) -> Any:
    app.jobs.dismiss(req.params["job"])
    return {"dismissed": True}


@router.route("GET", "/api/jobs/{job}/file")
def job_file(app: SpadraApp, req: Request) -> Any:
    job = app.jobs.get(req.params["job"])
    if job.file is None:
        raise NotFound("This operation produced no file" if job.status == "done" else "The operation has not produced a file (yet)")
    data, name, mime = job.file
    return file_response(data, name, mime)


@router.route("GET", "/api/diagnostics")
def diagnostics(app: SpadraApp, req: Request) -> Any:
    """The latest recorded failures, newest first. They hold codes, messages and the project's name; never the content of a project or a file."""
    try:
        limit = int(req.query.get("limit", "100"))                    # a query string is text
    except (TypeError, ValueError):
        raise BadRequest("limit must be a whole number from 1 to 500") from None
    return {"entries": app.diagnostics.recent(integer(limit, "limit", 1, 500))}


# ----------------------------------------------------------------------------------------------------------------------- BIM data
@router.route("GET", "/api/bim")
def bim_info(app: SpadraApp, req: Request) -> Any:
    from . import bimdata as BD
    return BD.info()


@router.route("GET", "/api/bim/properties/search")
def bim_search(app: SpadraApp, req: Request) -> Any:
    from . import bimdata as BD
    return {"results": BD.search(text(req.query.get("q") or "", "q", 80, empty=True), entity=text(req.query.get("entity") or "", "entity", 40, empty=True) or None,
                                 limit=integer(int(req.query.get("limit") or 40) if str(req.query.get("limit") or "40").isdigit() else 40, "limit", 1, 200))}


@router.route("POST", "/api/bim/quantities")
def bim_quantities(app: SpadraApp, req: Request) -> Any:
    from . import bimdata as BD
    body = req.json()
    with app.lock:
        return BD.schedule(app.require(), body.get("ids"), body.get("set"))


@router.route("POST", "/api/bim/exchange")
def bim_exchange(app: SpadraApp, req: Request) -> Any:
    from . import bimdata as BD
    body = req.json()
    with app.lock:
        return BD.exchange(app.require(), body.get("ids"))


@router.route("GET", "/api/bsdd/dictionaries")
def bsdd_dictionaries(app: SpadraApp, req: Request) -> Any:
    from . import bimdata as BD
    return BD.CLIENT.dictionaries(offline=req.query.get("offline") == "1")


@router.route("GET", "/api/bsdd/search")
def bsdd_search(app: SpadraApp, req: Request) -> Any:
    from . import bimdata as BD
    return BD.CLIENT.search(text(req.query.get("q") or "", "q", 100), dictionary=text(req.query.get("dictionary") or "", "dictionary", 300, empty=True),
                            offline=req.query.get("offline") == "1")


@router.route("GET", "/api/bsdd/class")
def bsdd_class(app: SpadraApp, req: Request) -> Any:
    from . import bimdata as BD
    return BD.CLIENT.class_detail(text(req.query.get("uri") or "", "uri", 400), offline=req.query.get("offline") == "1")


@router.route("POST", "/api/bsdd/suggest")
def bsdd_suggest(app: SpadraApp, req: Request) -> Any:
    from . import bimdata as BD
    body = req.json()
    with app.lock:
        project = app.require()
        oid = text(body.get("id"), "id", 80)
        if not BD.E.alive(project, oid):
            raise NotFound(f"Element {oid} does not exist")
        return BD.suggest(project, oid, dictionary=str(body.get("dictionary") or ""), offline=bool(body.get("offline", False)))


@router.route("GET", "/api/ids")
def ids_list(app: SpadraApp, req: Request) -> Any:
    from . import requirements as RQ
    with app.lock:
        return {"files": RQ.listing(app.require())}


@router.route("GET", "/api/ids/report")
def ids_report(app: SpadraApp, req: Request) -> Any:
    from . import requirements as RQ
    with app.lock:
        return RQ.ids_report(app.require(), text(req.query.get("id") or "", "id", 40))


@router.route("GET", "/api/ils")
def ils(app: SpadraApp, req: Request) -> Any:
    from . import requirements as RQ
    with app.lock:
        project = app.require()
        return {"config": RQ.ils_config(project), "report": RQ.ils_report(project)}
