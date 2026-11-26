"""Corrective proposals for a non-compliant finding.

A correction is computed from the rule's own expression, tried on a throw-away copy of the project, and offered as a *proposal* (``assistant.propose_commands``) that a person
reviews and applies. Nothing here changes the project. Applying is the assistant's all-or-nothing run, which this module backs with two promises:

* before anything is offered, the correction is run on a copy and the engine is run again: the finding must be resolved there, no other element may become non-compliant and the model
  check may not gain an error; otherwise no proposal is made and the reason is given;
* after it is applied, the same engine is run on the real project (``post_check``): if the finding is not resolved, or something else got worse, the assistant undoes the change.
"""
from __future__ import annotations

from typing import Any, Mapping

from ...tools.errors import BadRequest, Conflict, NotFound
from . import datasets as DS
from . import engine as EN
from . import expr as X
from . import facts as FC

_UNIT_PER_M = {"m": 1.0, "mm": 1000.0, "cm": 100.0}


def _dataset(project: Any, report: Mapping[str, Any]) -> DS.Dataset:
    pin = (report.get("config") or {}).get("dataset")
    ds, why = DS.select(report["config"]["jurisdiction"], report["config"].get("reference_date"), pin=pin)
    if ds is None:
        raise Conflict(why or "The regulation dataset is not available")
    return ds


def _rule(ds: DS.Dataset, rule_id: str) -> Mapping[str, Any]:
    for r in ds.data["rules"]:
        if r["id"] == rule_id:
            return r
    raise NotFound("The dataset has no such rule")


def compute(project: Any, report: Mapping[str, Any], finding: Mapping[str, Any], correction_id: str | None = None) -> dict[str, Any]:
    """The correction for a finding: which parameter, from what, to what (in the parameter's own unit). Raises ``Conflict`` with a reason when there is none."""
    if report.get("state") != "ok":
        raise Conflict("Regulation assistance is not ready: " + str(report.get("reason") or report.get("state")))
    if finding["status"] != "non_compliant":
        raise Conflict(f"Only a non-compliant finding has a correction; this one is {finding['status'].replace('_', ' ')}")
    if not finding["correctable"]:
        raise Conflict(finding.get("correction_note") or "This finding has no correction")
    ds = _dataset(project, report)
    rule = _rule(ds, finding["rule"])
    options = [c for c in rule.get("corrections") or [] if "non_compliant" in (c.get("for") or ["non_compliant"])]
    if correction_id:
        options = [c for c in options if c["id"] == correction_id]
    if not options:
        raise Conflict("The rule defines no such correction")
    corr = options[0]
    idx = FC.Index(project)
    ef = FC.door_facts(idx, finding["element"]) if finding["category"] == "door" else None
    if ef is None:
        raise Conflict("Corrections are only defined for doors")
    facts = {**FC.project_facts(report["config"]), **ef.values}
    try:
        target_m = X.evaluate(corr["new_value"], facts, ds.tables)
    except (X.Missing, X.ExprError) as exc:
        raise Conflict(f"The correction cannot be computed ({exc})") from exc
    from ..assistant import ASSISTANT
    row = ASSISTANT._all_rows(project.properties({"model": finding["element"]})).get(corr["key"])
    if row is None or row.get("type") != "number" or row.get("unit") not in _UNIT_PER_M:
        raise Conflict(f"The element has no editable numeric '{corr['key']}' parameter in a known unit")
    factor = _UNIT_PER_M[row["unit"]]
    new_value = round(float(target_m) * factor, 6)
    current = float(row["value"])
    if new_value <= current + 1e-9:
        raise Conflict(f"The rule's correction ({new_value:g} {row['unit']}) is not larger than the current {corr['key']} ({current:g} {row['unit']}), so it would not resolve this finding")
    return {"id": corr["id"], "label": corr.get("label", corr["id"]), "key": corr["key"], "unit": row["unit"], "from": current, "to": new_value, "side_effects": corr.get("side_effects"),
            "step": {"op": "model.set_property", "params": {"id": finding["element"], "key": corr["key"], "value": new_value}}}


def preview(project: Any, finding_id: str, correction_id: str | None = None) -> dict[str, Any]:
    """Try the correction on a copy and say what would happen. Raises ``Conflict`` when it should not be offered. Changes nothing."""
    report = EN.evaluate(project)
    finding = EN.finding(report, finding_id)
    if finding is None:
        raise NotFound("There is no such finding at this revision; check again")
    corr = compute(project, report, finding, correction_id)
    scratch = project.scratch()
    try:
        scratch.execute(corr["step"]["op"], corr["step"]["params"])
    except Exception as exc:                                                       # noqa: BLE001 - the project refuses it: say why
        raise Conflict(f"The project would refuse this correction: {getattr(exc, 'message', None) or exc}") from exc
    after = EN.evaluate(scratch, use_cache=False)
    again = EN.finding(after, finding_id)
    before_bad = {f["id"] for f in report["findings"] if f["status"] == "non_compliant"}
    new_bad = [f for f in after["findings"] if f["status"] == "non_compliant" and f["id"] not in before_bad]
    if again is None or again["status"] in ("non_compliant", "insufficient_data"):
        raise Conflict("Tried on a copy, the correction does not resolve the finding" + (f" ({again['explanation']})" if again else ""))
    if new_bad:
        raise Conflict(f"Tried on a copy, the correction makes {len(new_bad)} other element(s) non-compliant")
    return {"non_compliant_before": sorted(before_bad), "finding": finding, "correction": corr, "predicted": {"status": again["status"], "value": again["value"], "limit": again["limit"], "headroom": again["headroom"],
                                                                 "explanation": again["explanation"]},
            "report_revision": report["revision"], "dataset": report["dataset"]}


def propose(project: Any, finding_id: str, correction_id: str | None = None) -> dict[str, Any]:
    """Make the proposal (the assistant's message with the proposal in it). Raises ``Conflict`` when the correction should not be offered."""
    from ..assistant import ASSISTANT
    pre = preview(project, finding_id, correction_id)
    f, c = pre["finding"], pre["correction"]
    unit = c["unit"]
    title = f"{c['label']}: {f['element_name']} {c['key']} {c['from']:g} → {c['to']:g} {unit}"
    why = (f"{f['explanation']} Setting the {c['key']} to {c['to']:g} {unit} is predicted to give: {pre['predicted']['explanation']}"
           + (f" Side effects to check: {c['side_effects']}" if c.get("side_effects") else ""))
    evidence = {"finding": f["id"], "rule": f["rule"], "citation": f["citation"], "dataset": pre["dataset"] and {k: pre["dataset"].get(k) for k in ("id", "version", "sha256", "verification")},
                "value": f["value"], "limit": f["limit"], "unit": f["unit"], "correction": c["id"], "predicted": pre["predicted"]}
    return ASSISTANT.propose_commands(project, source="regulation", title=title, explanation=why, steps=[c["step"]], targets=[f["element"]], evidence=evidence,
                                      evidence_kind="regulation.finding", risk="medium", post_check={"kind": "regulation", "finding": f["id"], "rule": f["rule"], "dataset_sha256": f["dataset"]["sha256"],
                                                  "non_compliant_before": pre["non_compliant_before"][:5000]})


def post_check(project: Any, proposal: Mapping[str, Any]) -> list[dict[str, Any]]:
    """After a regulation proposal ran: the finding must now be resolved and nothing else non-compliant that was not before. ``strict``: a failure undoes the change."""
    chk = proposal["post_check"]
    report = EN.evaluate(project, use_cache=False)
    again = EN.finding(report, chk["finding"])
    ev = proposal.get("evidence") or {}
    ok = again is not None and again["status"] in ("compliant", "approaching_limit")
    rows = [{"label": f"{ev.get('citation', 'regulation')} finding resolved", "wanted": "compliant", "found": again["status"] if again else "finding no longer exists", "ok": bool(ok), "strict": True}]
    prior = (ev.get("predicted") or {})
    others = [f for f in report.get("findings", []) if f["status"] == "non_compliant" and f["id"] != chk["finding"]]
    expected_others = chk.get("non_compliant_before")
    if expected_others is not None:
        new_bad = [f for f in others if f["id"] not in set(expected_others)]
        rows.append({"label": "no other element made non-compliant", "wanted": 0, "found": len(new_bad), "ok": not new_bad, "strict": True})
    rows.append({"label": "value after the change", "wanted": prior.get("value"), "found": again["value"] if again else None,
                 "ok": again is not None and prior.get("value") is not None and abs(again["value"] - prior["value"]) < 1e-6, "strict": False})
    return rows
