"""The regulation engine: one deterministic evaluation shared by the findings view, the model check and the correction preview.

``evaluate(project)`` reads the project's settings, selects the pinned dataset, derives facts from the model and applies each rule to each element it covers. The same
inputs always give the same findings, with the same ids. The engine changes nothing; a correction is a separate, previewed and approved step (``corrections.py``).

Statuses (``STATUSES``), most serious first, are never blended: *non_compliant* (the value fails the legal limit), *insufficient_data* (a needed fact is not known:
not a pass and not a fail), *approaching_limit* (passes, but within the project's warning margin of the limit; the margin is advice and never the legal limit),
*unsupported* (the dataset does not cover this case, for example another project status), *not_applicable* (the rule does not apply to this element),
*compliant*. When regulation assistance is off the report is ``disabled``; a report made for an older revision is marked ``stale`` by whoever holds it.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Mapping

from . import config as CFG
from . import datasets as DS
from . import expr as X
from . import facts as FC

SCHEMA = "spadra.regulation.report.v1"
STATUSES = ("non_compliant", "insufficient_data", "approaching_limit", "unsupported", "not_applicable", "compliant")
STATUS_LABELS = {"non_compliant": "Non-compliant", "insufficient_data": "Insufficient data", "approaching_limit": "Approaching limit", "unsupported": "Not covered",
                 "not_applicable": "Not applicable", "compliant": "Compliant", "disabled": "Off"}
RANK = {s: i for i, s in enumerate(STATUSES)}
CACHE_KEY = "regulation.report"
MAX_FINDINGS = 20_000


def finding_id(rule_id: str, dataset_sha: str, element_id: str) -> str:
    return hashlib.sha256(f"{rule_id}|{dataset_sha}|{element_id}".encode("utf-8")).hexdigest()[:16]


def _fmt(value: float, unit: str) -> str:
    if unit == "m":
        return f"{value * 1000:.0f} mm"
    if unit == "mm":
        return f"{value:.0f} mm"
    return f"{value:g} {unit}".strip()


def _citation_label(rule: Mapping[str, Any]) -> str:
    c = rule["citation"]
    bits = [str(c.get("instrument", "")), f"art. {c['article']}" if c.get("article") else "", f"lid {c['lid']}" if c.get("lid") else "", f"tabel {c['table']}" if c.get("table") else ""]
    return " ".join(b for b in bits if b)


def _empty(cfg: Mapping[str, Any], state: str, revision: int, reason: str = "", dataset: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {"schema": SCHEMA, "state": state, "enabled": bool(cfg.get("enabled")), "reason": reason, "revision": revision, "config": dict(cfg), "dataset": dataset,
            "findings": [], "elements": {}, "summary": {s: 0 for s in STATUSES}, "computed_ms": 0.0}


def evaluate(project: Any, *, root: Any = None, use_cache: bool = True) -> dict[str, Any]:
    """The regulation report for the project as it is now."""
    cfg = CFG.current(project)
    revision = project.revision
    if not cfg["enabled"]:
        return _empty(cfg, "disabled", revision, "Regulation assistance is off for this project")
    if not cfg["jurisdiction"]:
        return _empty(cfg, "unconfigured", revision, "Choose a jurisdiction")
    ds, reason = DS.select(cfg["jurisdiction"], cfg["reference_date"], pin=cfg["dataset"], root=root)
    if ds is None:
        return _empty(cfg, "unsupported", revision, reason)
    key = (revision, json.dumps(cfg, sort_keys=True), ds.sha256)
    cache = getattr(project, "_cache", None)
    if use_cache and cache is not None and cache.get(CACHE_KEY, (None,))[0] == key:
        return cache[CACHE_KEY][1]
    if not ds.usable:
        return _empty(cfg, "dataset_problem", revision, "; ".join(ds.problems[:3]), ds.describe())
    if ds.data.get("blocked"):
        return _empty(cfg, "unsupported", revision, str(ds.data["blocked"].get("reason", "This dataset is blocked")), ds.describe())
    started = time.perf_counter()
    report = _run(project, cfg, ds)
    report["computed_ms"] = round((time.perf_counter() - started) * 1000, 2)
    if cache is not None:
        cache[CACHE_KEY] = (key, report)
    return report


def _run(project: Any, cfg: Mapping[str, Any], ds: DS.Dataset) -> dict[str, Any]:
    idx = FC.Index(project)
    global_facts = FC.project_facts(cfg)
    margin = float(cfg["warning_margin"])
    trusted = ds.verification == "verified_official"
    findings: list[dict[str, Any]] = []
    elements: dict[str, str] = {}
    by_category: dict[str, list[FC.ElementFacts]] = {}
    for rule in ds.data["rules"]:
        cat = rule["applies_to"]["category"]
        if cat not in by_category:
            by_category[cat] = FC.for_category(idx, cat)
        for ef in by_category[cat]:
            if len(findings) >= MAX_FINDINGS:
                break
            findings.append(_apply(rule, ef, global_facts, ds, margin, trusted, bool(cfg.get("accept_unverified"))))
    for f in findings:
        worst = elements.get(f["element"])
        if worst is None or RANK[f["status"]] < RANK[worst]:
            elements[f["element"]] = f["status"]
    findings.sort(key=lambda f: (RANK[f["status"]], f["level_name"] or "", f["element_name"], f["rule"]))
    summary = {s: 0 for s in STATUSES}
    for f in findings:
        summary[f["status"]] += 1
    return {"schema": SCHEMA, "state": "ok", "enabled": True, "reason": "", "revision": project.revision, "config": dict(cfg), "dataset": ds.describe(), "findings": findings,
            "elements": elements, "summary": summary, "computed_ms": 0.0, "evidence_grade": "official" if trusted else "unverified",
            "rules": [{"id": r["id"], "title": r["title"], "citation": _citation_label(r), "review": r["review"]["status"]} for r in ds.data["rules"]]}


def _apply(rule: Mapping[str, Any], ef: FC.ElementFacts, global_facts: Mapping[str, Any], ds: DS.Dataset, margin: float, trusted: bool, accept_unverified: bool) -> dict[str, Any]:
    check = rule["check"]
    facts = {**global_facts, **ef.values}
    base = {"id": finding_id(rule["id"], ds.sha256, ef.id), "rule": rule["id"], "title": rule["title"], "citation": _citation_label(rule), "element": ef.id, "element_name": ef.name,
            "category": ef.category, "level": ef.level, "level_name": ef.level_name, "unit": check["unit"], "assumptions": [*rule.get("assumptions", []), *ef.assumptions],
            "dataset": {"id": ds.id, "version": ds.version, "sha256": ds.sha256}, "evidence_grade": "official" if trusted else "unverified", "review": rule["review"]["status"],
            "value": None, "limit": None, "headroom": None, "needs": [], "correctable": False, "correction_note": None}
    trace = X.Trace()

    def done(status: str, why: str, **extra: Any) -> dict[str, Any]:
        out = {**base, "status": status, "explanation": why, **extra}
        out["facts"] = {n: facts.get(n) for n in sorted(trace.facts) if facts.get(n) is not None}
        return out

    try:
        scope = rule.get("scope_when")
        if scope is not None and not X.evaluate(scope, facts, ds.tables, trace):
            return done("unsupported", rule.get("scope_note") or "This case is outside what the installed dataset covers.")
        applies = rule.get("applies_when")
        if applies is not None and not X.evaluate(applies, facts, ds.tables, trace):
            return done("not_applicable", rule.get("applies_note") or "The rule does not apply to this element.")
        value = X.evaluate(check["value"], facts, ds.tables, trace)
        limit = X.evaluate(check["limit"], facts, ds.tables, trace)
    except X.Missing as miss:
        why = ef.missing.get(miss.name) or (f"{miss.name}: {miss.why}" if miss.why else None)
        needs = [miss.name]
        if why is None:
            why = {"building.function": "Set the building function in the regulation settings.", "project.status": "Set the project status in the regulation settings.",
                   "project.reference_date": "Set the reference date in the regulation settings."}.get(miss.name, f"{miss.name} is not known for this element.")
        return done("insufficient_data", f"{why} Until then this check is neither passed nor failed.", needs=needs)
    except X.ExprError as exc:
        return done("insufficient_data", f"The rule could not be evaluated for this element ({exc}).", needs=sorted(trace.facts))
    unit = check["unit"]
    minimum = check["kind"] == "minimum"
    ok = value >= limit - X.EPS if minimum else value <= limit + X.EPS
    headroom = (value - limit) if minimum else (limit - value)
    near = (value < limit * (1 + margin)) if minimum else (value > limit * (1 - margin))
    label = "minimum" if minimum else "maximum"
    corrections = [c["id"] for c in rule.get("corrections") or []]
    if not ok:
        status = "non_compliant"
        why = f"{rule['title']}: {_fmt(value, unit)} is {'below' if minimum else 'above'} the {label} of {_fmt(limit, unit)} ({_citation_label(rule)}); {'short' if minimum else 'over'} by {_fmt(-headroom, unit)}."
    elif margin > 0 and near:
        status = "approaching_limit"
        why = (f"{rule['title']}: {_fmt(value, unit)} meets the {label} of {_fmt(limit, unit)} ({_citation_label(rule)}) with only {_fmt(headroom, unit)} to spare, "
               f"inside the project's {margin * 100:g}% warning margin. The margin is advice; the legal limit is {_fmt(limit, unit)}.")
    else:
        status = "compliant"
        why = f"{rule['title']}: {_fmt(value, unit)} meets the {label} of {_fmt(limit, unit)} ({_citation_label(rule)})."
    correctable, note = False, None
    if status == "non_compliant" and corrections:
        if not (trusted or accept_unverified):
            note = "The dataset has not been checked against the official source; corrections stay off until it is, or you accept unverified datasets in the settings."
        else:
            correctable = True
    return done(status, why, value=value, limit=limit, headroom=headroom, correctable=correctable, correction_note=note, corrections=corrections)


def finding(report: Mapping[str, Any], fid: str) -> dict[str, Any] | None:
    for f in report.get("findings", []):
        if f["id"] == fid:
            return f
    return None


def worst(report: Mapping[str, Any]) -> str | None:
    for s in STATUSES:
        if report.get("summary", {}).get(s):
            return s
    return None


def issues(project: Any) -> list[dict[str, str]]:
    """The regulation findings as model-check issues (category ``regulation``), only when the project turned regulation assistance on. Non-compliance is a *warning*,
    never an error: the check's ``ok`` still means the model is internally consistent; a person decides what a screening result means for the design."""
    cfg = CFG.current(project)
    if not cfg["enabled"]:
        return []
    from .. import elements as E
    report = evaluate(project)
    out: list[dict[str, str]] = []
    if report["state"] != "ok":
        return [E.issue("info", "regulation", f"Regulation assistance is on but not evaluating: {report['reason'] or report['state']}", "regulation")]
    for f in report["findings"]:
        if f["status"] == "non_compliant":
            out.append(E.issue("warning", f["element"], f["explanation"], "regulation"))
        elif f["status"] in ("approaching_limit", "insufficient_data"):
            out.append(E.issue("info", f["element"], f["explanation"], "regulation"))
    return out[:200]
