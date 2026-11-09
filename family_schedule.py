"""Parametric schedules: one schedule per family, its columns the family's own typed parameters, one row per placed instance.

The values are SGEO's resolved values (instance override, then named type, then default, then formula), converted to the units the family editor shows. Nothing is
recomputed here: a derived column is the formula's result, so the schedule changes when a parameter, a type or an instance changes, and it cannot disagree with the
model. Each row also says where each value came from (``sources``: ``instance`` / ``type`` / ``default`` / ``formula``), so a person can tell an override from an inherited value.

A schedule view is an ordinary schedule view (``kind: "schedule"``) with ``category: "family"`` and the family's id; it appears in the project browser, on sheets,
in the CSV export and in the SLDC project report like any other schedule. A family that has been deleted leaves an empty schedule that says so, rather than an error."""
from __future__ import annotations

from typing import Any, Mapping

from . import elements as E
from . import families as FM

CATEGORY = "family"
TOTALS = ("area", "volume", "mass")                      # quantities that add up across instances; a width or a ratio does not


def _col(key: str, label: str, unit: str = "", *, total: bool = False, text: bool = False) -> dict[str, Any]:
    return {"key": key, "label": label, "unit": unit, "total": total, "align": "left" if text else "right"}


def _cell(spec: Mapping[str, Any], value: Any) -> Any:
    """A resolved SI value in the family editor's units, as a number where it is one (so totals and CSV stay numeric)."""
    shown = FM._convert_out(spec, value)
    if isinstance(shown, bool):
        return "Yes" if shown else "No"
    if isinstance(shown, float):
        return round(shown + 0.0, 6)
    if isinstance(shown, list):
        return ", ".join(f"{c:g}" if isinstance(c, (int, float)) and not isinstance(c, bool) else str(c) for c in shown)
    return "" if shown is None else shown


def source_of(fam: Mapping[str, Any], obj: Mapping[str, Any], name: str) -> str:
    spec = fam["parameters"][name]
    if "expression" in spec:
        return "formula"
    if name in (obj.get("parameters") or {}):
        return "instance"
    t = obj.get("family_type")
    if t is not None and name in ((fam.get("types") or {}).get(t) or {}):
        return "type"
    return "default"


def columns_of(fam: Mapping[str, Any]) -> list[dict[str, Any]]:
    cols = [_col("no", "No.", text=True), _col("name", "Name", text=True), _col("type", "Type", text=True), _col("level", "Level", text=True)]
    for name, spec in fam["parameters"].items():
        kind = spec["type"]
        label = FM.LABELS.get(name, name.replace("_", " ").capitalize()) + (" (formula)" if "expression" in spec else "")
        text_col = kind == "boolean" or kind in FM.DATA_TYPES
        cols.append(_col(f"p_{name}", label, "" if text_col else FM.UNIT_OF.get(kind, ""), total=kind in TOTALS, text=text_col))
    return cols


def data(project: Any, family_id: Any) -> dict[str, Any]:
    """The schedule of one family: the rows are read from the instances' resolved parameter values each time."""
    try:
        fam = FM.family(project, family_id)
    except Exception:
        return {"category": CATEGORY, "family": family_id, "title": "Family Schedule", "columns": [_col("no", "No.", text=True)], "rows": [], "totals": {}, "count": 0,
                "note": "The family this schedule was made for no longer exists."}
    cols = columns_of(fam)
    elevations = {l["id"]: float(l["elevation"]) for l in project.manifest["levels"]}
    rows: list[dict[str, Any]] = []
    for oid in FM.instances(project, fam["id"]):
        obj = project.model.get(oid)
        meta = obj["metadata"]
        resolved = FM.values(project, oid)
        level_id = E.m(meta, "level")
        x, y = E.m(meta, "point") or (0.0, 0.0)
        row: dict[str, Any] = {"id": oid, "name": obj["name"], "type": obj.get("family_type") or "-", "level": project.level(level_id)["name"] if level_id in elevations else "-",
                               "sources": {n: source_of(fam, obj, n) for n in fam["parameters"]}}
        for name, spec in fam["parameters"].items():
            row[f"p_{name}"] = _cell(spec, resolved.get(name))
        rows.append({"_order": (elevations.get(level_id, 0.0), x, y, obj["name"]), **row})
    rows.sort(key=lambda r: r["_order"])
    for n, r in enumerate(rows, 1):
        r.pop("_order")
        r["no"] = f"{(fam['name'][:2] or 'F').upper()}{n:02d}"
    totals = {c["key"]: round(sum(float(r.get(c["key"]) or 0) for r in rows), 6) for c in cols if c["total"]}
    return {"category": CATEGORY, "family": fam["id"], "title": f"{fam['name']} Schedule", "columns": cols, "rows": rows, "totals": totals, "count": len(rows),
            "note": "One row per placed instance; values are the family's resolved parameters (instance override, then type, then default; formulas are evaluated). "
                    "Each row's sources say which."}


def add_view(project: Any, family_id: Any) -> dict[str, Any]:
    """Add a schedule view for a family (one per family)."""
    from ..tools.errors import Conflict
    from .project import _unique_view_name, new_id
    fam = FM.family(project, family_id)
    if any(v["kind"] == "schedule" and v.get("category") == CATEGORY and v.get("family") == fam["id"] for v in project.manifest["views"]):
        raise Conflict(f"There is already a schedule for the family {fam['name']}")
    name = f"{fam['name']} Schedule"
    _unique_view_name(project, name)
    vid = new_id()
    project.manifest["views"].append({"id": vid, "kind": "schedule", "name": name, "category": CATEGORY, "family": fam["id"]})
    return {"id": vid, "name": name}
