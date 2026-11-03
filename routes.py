"""SPADRA's HTTP API. Handlers are thin: they unpack the request and call the project; the rules live in ``project.py``."""
from __future__ import annotations

import json
from typing import Any

from ..tools.errors import BadRequest, NotFound
from ..tools.server import Request, Response, Router, created, file_response
from ..tools.validation import boolean, choice, integer, text
from .app import SpadraApp
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
