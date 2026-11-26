"""The regulation assistance HTTP API. Thin: unpack, call, return. The rules live in ``engine``, ``datasets`` and ``corrections``.

Settings change through the ``regulation.configure`` command (so they are undoable, revision-fenced and in the project's history); a correction becomes a proposal and is
applied through the same approve path as every assistant proposal (``/api/regulation/apply`` only checks that the proposal is a regulation one).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from typing import Any

from ...tools.errors import BadRequest, Conflict, NotFound
from ...tools.server import Request, file_response
from ...tools.validation import boolean, integer, text
from . import config as CFG
from . import corrections as CO
from . import datasets as DS
from . import engine as EN

JURISDICTION_LABELS = {"NL": "Netherlands: Besluit bouwwerken leefomgeving (Bbl)", "AW": "Aruba: Bouw- en woningbesluit", "US": "United States: ICC codes as adopted by the AHJ"}


def options(cfg: dict[str, Any]) -> dict[str, Any]:
    datasets = DS.load_all()
    jurisdictions = []
    for j in DS.JURISDICTIONS:
        mine = [d for d in datasets if d.jurisdiction == j]
        blocked = [d.data["blocked"]["reason"] for d in mine if d.data.get("blocked")]
        jurisdictions.append({"id": j, "label": JURISDICTION_LABELS[j], "datasets": [d.describe() for d in mine],
                              "available": any(d.usable and not d.data.get("blocked") and d.data.get("rules") for d in mine), "blocked_reason": blocked[0] if blocked else None})
    selected, _ = DS.select(cfg["jurisdiction"], cfg["reference_date"], pin=cfg["dataset"]) if cfg["jurisdiction"] else (None, "")
    return {"jurisdictions": jurisdictions, "project_statuses": [{"id": k, "label": v} for k, v in CFG.STATUS_LABELS.items()],
            "functions": selected.functions() if selected else [], "status_labels": EN.STATUS_LABELS}


def bundle(app: Any, project: Any, *, fresh: bool = False) -> dict[str, Any]:
    cfg = CFG.current(project)
    report = EN.evaluate(project, use_cache=not fresh)
    history = app.provenance.status(project)
    return {"revision": project.revision, "config": cfg, "report": report, "options": options(cfg), "history": {"state": history.get("state"), "records": history.get("records")}}


def _record_check(app: Any, project: Any, report: dict[str, Any]) -> None:
    if report["state"] != "ok":
        return
    digest = hashlib.sha256(json.dumps([(f["id"], f["status"]) for f in report["findings"]], separators=(",", ":")).encode("utf-8")).hexdigest()
    cfg_digest = hashlib.sha256(json.dumps(report["config"], sort_keys=True).encode("utf-8")).hexdigest()[:12]
    app.provenance.record(project, "regulation.checked", subject={"elements": [f["element"] for f in report["findings"] if f["status"] == "non_compliant"][:200]},
                          revision={"at": project.revision},
                          payload={"dataset": {k: report["dataset"][k] for k in ("id", "version", "sha256", "verification")}, "summary": report["summary"], "findings_sha256": digest,
                                   "evidence_grade": report.get("evidence_grade"), "config": {k: v for k, v in report["config"].items() if k != "dataset"}},
                          event_id=f"{project.manifest['id']}:regulation.checked:{project.revision}:{cfg_digest}:{digest[:12]}")


def register(router) -> None:
    from ..app import SpadraApp

    @router.route("GET", "/api/regulation")
    def regulation_state(app: SpadraApp, req: Request) -> Any:
        with app.lock:
            return bundle(app, app.require())

    @router.route("POST", "/api/regulation/configure")
    def regulation_configure(app: SpadraApp, req: Request) -> Any:
        body = req.json()
        with app.lock:
            project = app.require()
            expected = integer(body["expected_revision"], "expected_revision", 0, 2**31) if body.get("expected_revision") is not None else None
            patch = {k: v for k, v in body.items() if k != "expected_revision"}
            project.execute("regulation.configure", patch, expected_revision=expected)
            return {**bundle(app, project), "project": project.summary()}

    @router.route("POST", "/api/regulation/check")
    def regulation_check(app: SpadraApp, req: Request) -> Any:
        with app.lock:
            project = app.require()
            out = bundle(app, project, fresh=True)
            _record_check(app, project, out["report"])
            return out

    @router.route("GET", "/api/regulation/finding/{id}")
    def regulation_finding(app: SpadraApp, req: Request) -> Any:
        with app.lock:
            project = app.require()
            report = EN.evaluate(project)
            f = EN.finding(report, text(req.params["id"], "id", 40))
            if f is None:
                raise NotFound("There is no such finding at this revision; check again")
            rule = None
            ds, _ = DS.select(report["config"]["jurisdiction"], report["config"].get("reference_date"), pin=report["config"].get("dataset"))
            if ds is not None:
                rule = next((r for r in ds.data["rules"] if r["id"] == f["rule"]), None)
            return {"finding": f, "rule": {"id": rule["id"], "title": rule["title"], "citation": rule["citation"], "review": rule["review"], "corrections": [
                {"id": c["id"], "label": c.get("label"), "key": c["key"], "side_effects": c.get("side_effects")} for c in rule.get("corrections", [])]} if rule else None,
                    "dataset": report["dataset"], "revision": report["revision"], "history": app.provenance.element(project, f["element"])}

    @router.route("POST", "/api/regulation/correct")
    def regulation_correct(app: SpadraApp, req: Request) -> Any:
        body = req.json()
        with app.lock:
            project = app.require()
            if body.get("expected_revision") is not None and project.revision != integer(body["expected_revision"], "expected_revision", 0, 2**31):
                raise Conflict(f"The project changed (now revision {project.revision}); check again before asking for a correction", hint="Check again")
            return CO.propose(project, text(body.get("finding"), "finding", 40), text(body["correction"], "correction", 60) if body.get("correction") else None)

    @router.route("POST", "/api/regulation/apply")
    def regulation_apply(app: SpadraApp, req: Request) -> Any:
        from ..assistant import ASSISTANT
        body = req.json()
        with app.lock:
            project = app.require()
            pid = text(body.get("proposal"), "proposal", 80)
            proposal = ASSISTANT.proposals.get(pid)
            if proposal is None or (proposal.get("post_check") or {}).get("kind") != "regulation":
                raise NotFound("There is no such regulation proposal")
            expected = integer(body["expected_revision"], "expected_revision", 0, 2**31) if body.get("expected_revision") is not None else None
            msg = app.commits.approve(pid, expected_revision=expected, confirm_high_risk=boolean(body.get("confirm_high_risk", False), "confirm_high_risk"))
            return {"message": msg, **bundle(app, project, fresh=True), "project": project.summary()}

    @router.route("GET", "/api/regulation/sources")
    def regulation_sources(app: SpadraApp, req: Request) -> Any:
        return DS.source_register()

    @router.route("GET", "/api/regulation/export")
    def regulation_export(app: SpadraApp, req: Request) -> Any:
        fmt = req.query.get("format", "json")
        if fmt not in ("json", "csv"):
            raise BadRequest("format must be json or csv")
        with app.lock:
            project = app.require()
            report = EN.evaluate(project)
            if report["state"] != "ok":
                raise Conflict("There is nothing to export: " + str(report.get("reason") or report["state"]))
            stem = f"regulation-{report['dataset']['id']}-r{report['revision']}"
            if fmt == "json":
                data = json.dumps({**report, "exported_for": {"project": project.manifest["id"], "name": project.name}, "note": "Screening result from the installed dataset; "
                                   "it is not a permit decision. Evidence grade says whether the dataset was checked against the official source."}, indent=2, ensure_ascii=False)
                return file_response(data.encode("utf-8"), f"{stem}.json", "application/json")
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["status", "rule", "citation", "level", "element", "element_id", "value", "limit", "unit", "headroom", "evidence_grade", "dataset", "dataset_version", "explanation"])
            for f in report["findings"]:
                w.writerow([f["status"], f["rule"], f["citation"], f["level_name"] or "", f["element_name"], f["element"], f["value"], f["limit"], f["unit"], f["headroom"],
                            f["evidence_grade"], f["dataset"]["id"], f["dataset"]["version"], f["explanation"]])
            return file_response(("﻿" + buf.getvalue()).encode("utf-8"), f"{stem}.csv", "text/csv")
