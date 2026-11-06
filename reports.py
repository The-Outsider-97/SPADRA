"""Project reports as SLDC documents.

A report is a real SLAI Document (``.sldc``, version 1.0): a deterministic, data-only container with headings, paragraphs and tables, written and
validated by ``src/utils/sldc``. Everything in it is read from the project at the moment it is made: the project's details, an element count, the
levels, every schedule the project has, the material quantities, the sheets with their revision history and whether each has changed since it was
issued, and the model check with its limitations. Nothing is summarised by guesswork and nothing is estimated.

A report is a snapshot, and says which model revision it was made from. It is stored in the project (``reports/<id>.sldc``) and listed in the Project
Browser; making or deleting one is a project command, so it is undoable like any other change."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Mapping

from src.utils import sldc as S

from ..tools.errors import BadRequest, Conflict, LimitExceeded, NotFound
from ..tools.validation import new_id, text
from . import checks as CK
from . import documentation as D
from . import elements as E
from . import sheets as SH

SECTIONS = {"summary": "Project and model summary", "levels": "Levels", "schedules": "Schedules", "materials": "Material quantities", "sheets": "Sheets and revisions",
            "checks": "Model check", "requirements": "Information requirements (ILS and IDS)"}
MAX_REPORTS = 50
MAX_REPORT_BYTES = 24 * 1024 * 1024
MAX_ISSUES_LISTED = 200


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _table(headers: list[str], rows: list[list[Any]], caption: str = "") -> dict[str, Any]:
    return S.make_table([headers] + [[_cell(c) for c in row] for row in rows], header_rows=1, caption=caption)


def _heading(label: str, level: int = 2) -> dict[str, Any]:
    return S.make_block("heading", label, attributes={"level": level})


def _para(label: str) -> dict[str, Any]:
    return S.make_block("paragraph", label)


def _schedule_table(data: Mapping[str, Any]) -> dict[str, Any]:
    cols = data["columns"]
    headers = [f"{c['label']} ({c['unit']})" if c["unit"] else c["label"] for c in cols]
    rows = [[r.get(c["key"], "") for c in cols] for r in data["rows"]]
    if data["rows"] and data["totals"]:
        rows.append(["Total" if i == 0 else data["totals"].get(c["key"], "") for i, c in enumerate(cols)])
    return _table(headers, rows)


def _blocks(project: Any, sections: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    m = project.manifest
    if "summary" in sections:
        out += [_heading(SECTIONS["summary"]),
                _table(["Item", "Value"], [["Project", project.name], ["Client", m.get("client", "") or "-"], ["Project number", m.get("number", "") or "-"], ["Unit", m["unit"]],
                                           ["Model revision", project.revision], ["Description", m.get("description", "") or "-"]])]
        count = D.schedule_data(project, "count")
        out += [_heading("Elements", 3), _schedule_table(count) if count["rows"] else _para("The model has no elements yet.")]
    if "levels" in sections:
        from . import datums as DT
        out += [_heading(SECTIONS["levels"]),
                _table(["Level", "Elevation (m)", "Elements standing on it"], [[l["name"], l["elevation"], len(DT.users_of(project, l["id"]))] for l in m["levels"]])]
    if "schedules" in sections:
        views = [v for v in m["views"] if v["kind"] == "schedule" and not D.SCHEDULES[v["category"]].get("summary")]
        out.append(_heading(SECTIONS["schedules"]))
        if not views:
            out.append(_para("The project has no schedules. Add one from the Project Browser."))
        for v in views:
            data = D.schedule_data(project, v["category"])
            out += [_heading(data["title"], 3), _schedule_table(data) if data["rows"] else _para("This schedule is empty.")]
    if "materials" in sections:
        data = D.schedule_data(project, "material")
        out += [_heading(SECTIONS["materials"]), _schedule_table(data) if data["rows"] else _para("The model has no geometry to measure."), _para(data["note"])]
    if "sheets" in sections:
        out.append(_heading(SECTIONS["sheets"]))
        if not m["sheets"]:
            out.append(_para("The project has no sheets."))
        else:
            rows = []
            for sheet in m["sheets"]:
                SH.prepare(sheet)
                st = SH.status(project, sheet)
                rows.append([sheet["number"], sheet["name"], sheet["paper"], sheet["revision"], sheet["titleblock"]["status"], st["state"], "; ".join(st["changed"]), len(sheet.get("viewports", []))])
            out.append(_table(["Number", "Name", "Paper", "Revision", "Status", "Since last issue", "Changed", "Views"], rows))
            for sheet in m["sheets"]:
                if sheet["revisions"]:
                    out += [_heading(f"{sheet['number']} {sheet['name']}".strip(), 3), _table(["Revision", "Date", "Description", "By", "Model revision"],
                                                                                           [[r["rev"], r["date"], r["description"], r["by"], r["model_revision"]] for r in sheet["revisions"]])]
    if "checks" in sections:
        result = project.check()
        out += [_heading(SECTIONS["checks"]), _para(f"{result['counts']['error']} error(s), {result['counts']['warning']} warning(s), {result['counts']['info']} note(s).")]
        if result["issues"]:
            shown = result["issues"][:MAX_ISSUES_LISTED]
            out.append(_table(["Severity", "Category", "Finding"], [[i["severity"], i.get("category", ""), i["message"]] for i in shown]))
            if len(result["issues"]) > len(shown):
                out.append(_para(f"{len(result['issues']) - len(shown)} further finding(s) are not listed."))
        out += [_heading("What this check does not do", 3)] + [S.make_block("list_item", t) for t in result["limitations"]]
    if "requirements" in sections:
        from . import requirements as RQ
        ils = RQ.ils_report(project)
        out.append(_heading(SECTIONS["requirements"]))
        if not ils["enabled"] and not RQ.listing(project):
            out.append(_para("No information requirements are active: the BIM basis ILS is off and the project holds no IDS file."))
        if ils["enabled"]:
            out += [_heading(f"BIM basis ILS - {ils['edition']}", 3),
                    _table(["Requirement", "Source", "Grade", "Applies to", "Met", "Not met"],
                           [[f"{r['id']} {r['title']}", r["source"], r["severity"] if r["enabled"] else "off", r["applicable"], r["passed"], r["failed"]] for r in ils["rules"]]),
                    _para(ils["note"])]
        for entry in RQ.listing(project):
            res = RQ.ids_report(project, entry["id"])
            out.append(_heading(f"IDS - {entry['title']} ({entry['filename']})", 3))
            if not res["result"]:
                out.append(_para("This IDS file has not been validated yet."))
                continue
            out.append(_table(["Specification", "Result", "Applicable", "Passed", "Failed", "Not evaluable"],
                              [[x["name"], x["status"], x["applicable"], x["passed"], x["failed"], x["not_evaluable"]] for x in res["result"]["specs"]]))
            out.append(_para(("This result is out of date: the model changed after it was validated. " if res["stale"] else "") + res["result"]["limits"]))
    return out


def build(project: Any, name: str, sections: list[str]) -> tuple[bytes, dict[str, Any]]:
    """The report as SLDC bytes, validated by the SLDC writer, and the entry that describes it."""
    blocks = [_heading(name, 1), _para(f"Made {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} from model revision {project.revision} of {project.name}. "
                                       "A report is a snapshot: later changes to the project are not in it.")] + _blocks(project, sections)
    try:
        package = S.new_package(name, blocks, metadata={"project": project.name, "model_revision": str(project.revision), "sections": ",".join(sections)})
        data = S.dumps_package(package)
    except S.SLDCError as exc:
        raise BadRequest(f"The report could not be written: {exc}") from exc
    if len(data) > MAX_REPORT_BYTES:
        raise LimitExceeded("The report is too large; choose fewer sections")
    return data, {"id": new_id(), "name": name, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "revision": project.revision, "sections": sections,
                  "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "blocks": len(blocks)}


def create(project: Any, p: Mapping[str, Any]) -> dict[str, Any]:
    entries = project.manifest["reports"]
    if len(entries) >= MAX_REPORTS:
        raise LimitExceeded(f"A project holds at most {MAX_REPORTS} reports; delete one first")
    sections = list(SECTIONS) if p.get("sections") is None else p["sections"]
    if not isinstance(sections, list) or not sections or any(s not in SECTIONS for s in sections):
        raise BadRequest(f"sections must be a list of: {', '.join(SECTIONS)}")
    sections = [s for s in SECTIONS if s in sections]
    name = text(p.get("name") or f"{project.name} report {len(entries) + 1}", "name", 80)
    if any(e["name"].lower() == name.lower() for e in entries):
        raise Conflict(f"A report called '{name}' already exists")
    data, entry = build(project, name, sections)
    entries.append(entry)
    project.reports[entry["id"]] = data
    return {"id": entry["id"], "bytes": entry["bytes"]}


def delete(project: Any, report_id: Any) -> None:
    entry = find(project, report_id)
    project.manifest["reports"].remove(entry)                          # the bytes stay in memory so that Undo can bring the entry back; Save writes only listed reports


def find(project: Any, report_id: Any) -> dict[str, Any]:
    rid = text(report_id, "id", 80)
    entry = next((e for e in project.manifest["reports"] if e["id"] == rid), None)
    if entry is None:
        raise NotFound(f"Report {rid} does not exist")
    return entry


def read(project: Any, report_id: Any) -> tuple[bytes, dict[str, Any]]:
    entry = find(project, report_id)
    if entry["id"] not in project.reports:
        raise NotFound("The report's file is missing from the project")
    return project.reports[entry["id"]], entry


def view(project: Any, report_id: Any) -> dict[str, Any]:
    """A report read back from its own bytes (so what is shown is what is stored): its description and its text as Markdown."""
    data, entry = read(project, report_id)
    try:
        package = S.loads_package(data)
        return {"report": entry, "container": S.inspect_package(data), "markdown": S.to_markdown(package)}
    except S.SLDCError as exc:
        raise BadRequest(f"The stored report cannot be read: {exc}") from exc
