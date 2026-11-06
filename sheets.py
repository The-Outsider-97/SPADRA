"""Sheets as documents: title block, revision history, notes, and whether what a sheet shows has changed since it was last issued.

A sheet places views (plans, sections, elevations, schedules) on paper at a scale. Its **title block** is drawn from the project and the
sheet's own fields; its **notes** are text placed on the paper; its **revisions** are the issue history. Issuing a revision records, next to the
description and date, a *digest of everything the sheet shows at that moment* (each viewport's derived view with its scale and place, the
notes and the title block). Later, the same digests recomputed from the live model say exactly which viewports have changed since the issue,
so a drawing that went out as revision B can be told apart from one that has been edited after it. Nothing is guessed: a changed digest is a
changed picture.

Views are derived from the model every time they are read, so they are never out of date on screen; "stale" here always means *different from
the last issued revision*."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date as _date
from typing import Any, Mapping

from ..tools.errors import BadRequest, Conflict, NotFound
from ..tools.validation import new_id, number, text

STATUSES = ("Preliminary", "For review", "For approval", "For construction", "As built")
TITLEBLOCK = {"drawn_by": 40, "checked_by": 40, "date": 10}
MAX_NOTES = 100
MAX_REVISIONS = 200
NOTE_SIZE_MM = (1.0, 50.0)
_REV = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,9}$")


def blank_titleblock() -> dict[str, str]:
    return {"drawn_by": "", "checked_by": "", "date": "", "status": STATUSES[0]}


def prepare(sheet: dict[str, Any]) -> dict[str, Any]:
    """Fill in the document fields of a sheet made before they existed (older projects open unchanged and gain them on first use)."""
    sheet.setdefault("titleblock", blank_titleblock())
    sheet.setdefault("notes", [])
    sheet.setdefault("revisions", [])
    return sheet


def find(project: Any, sheet_id: Any) -> dict[str, Any]:
    sid = text(sheet_id, "id", 80)
    sheet = next((s for s in project.manifest["sheets"] if s["id"] == sid), None)
    if sheet is None:
        raise NotFound(f"Sheet {sid} does not exist")
    return prepare(sheet)


# ------------------------------------------------------------------------------------------------------------------- fields
def set_field(project: Any, sheet: dict[str, Any], key: str, value: Any) -> None:
    prepare(sheet)
    if key in TITLEBLOCK:
        value = text(value, key, TITLEBLOCK[key], empty=True)
        if key == "date" and value:
            try:
                _date.fromisoformat(value)
            except ValueError as exc:
                raise BadRequest("The date must be written year-month-day, such as 2026-11-02") from exc
        sheet["titleblock"][key] = value
    elif key == "status":
        if value not in STATUSES:
            raise BadRequest(f"status must be one of {', '.join(STATUSES)}")
        sheet["titleblock"]["status"] = value
    else:
        raise BadRequest(f"'{key}' is not an editable sheet property")


# -------------------------------------------------------------------------------------------------------------------- notes
def add_note(sheet: dict[str, Any], p: Mapping[str, Any], width: float, height: float) -> str:
    prepare(sheet)
    if len(sheet["notes"]) >= MAX_NOTES:
        raise BadRequest(f"A sheet holds at most {MAX_NOTES} notes")
    note = {"id": new_id(), "text": text(p.get("text"), "text", 500, multiline=True), "x": number(p.get("x", 0.06 * width), "x", 0, width),
            "y": number(p.get("y", 0.1 * height), "y", 0, height), "size": number(p.get("size", 3.5), "size", *NOTE_SIZE_MM)}
    sheet["notes"].append(note)
    return note["id"]


def note_of(sheet: dict[str, Any], note_id: Any) -> dict[str, Any]:
    note = next((n for n in prepare(sheet)["notes"] if n["id"] == note_id), None)
    if note is None:
        raise NotFound("That note does not exist")
    return note


def set_note(sheet: dict[str, Any], p: Mapping[str, Any], width: float, height: float) -> None:
    note = note_of(sheet, p.get("note"))
    key, value = text(p.get("key"), "key", 20), p.get("value")
    if key == "text":
        note["text"] = text(value, "text", 500, multiline=True)
    elif key in ("x", "y"):
        note[key] = number(value, key, 0, width if key == "x" else height)
    elif key == "size":
        note["size"] = number(value, key, *NOTE_SIZE_MM)
    else:
        raise BadRequest(f"'{key}' is not an editable note property")


# --------------------------------------------------------------------------------------------------------------------- digests
def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def view_digest(project: Any, view: Mapping[str, Any]) -> str:
    """A fingerprint of what a view shows now. It ignores revision counters, so only the content moves it."""
    key = ("view-digest", view["id"])
    if key not in project._cache:
        if view["kind"] == "schedule":
            from . import documentation as D
            content: Any = D.schedule_data(project, view["category"])
        else:
            data = project.drawing_data(view["id"])
            data.pop("revision", None)
            data.pop("doc_revision", None)
            content = data
        project._cache[key] = hashlib.sha256(_canon(content).encode("utf-8")).hexdigest()[:16]
    return project._cache[key]


def viewport_state(project: Any, vp: Mapping[str, Any], views: Mapping[str, Any]) -> tuple[str, str | None]:
    """(what the viewport is called, a fingerprint of what it shows now - ``None`` when what it shows no longer exists)."""
    from . import sheetviews as SV
    kind = SV.kind_of(vp)
    if kind == "snapshot":
        return vp.get("title") or "3D snapshot", vp["image"]["sha256"] + f"/{vp['width']}"
    if kind == "report":
        entry = next((r for r in project.manifest["reports"] if r["id"] == vp["report_id"]), None)
        return (entry["name"], entry["sha256"] + f"{vp['width']}/{vp['size']}/{vp['lines']}") if entry else ("(missing report)", None)
    view = views.get(vp.get("view_id"))
    if view is None:
        return "(missing)", None
    if kind == "model":
        return SV.model_name(view), SV.model_digest(project, vp["azimuth"], vp["elevation"])
    return view["name"], view_digest(project, view)


def digests(project: Any, sheet: Mapping[str, Any]) -> dict[str, str]:
    """Digest of each viewport (what it shows, scale and place), plus the sheet's own text under ``_text``."""
    out: dict[str, str] = {}
    views = {v["id"]: v for v in project.manifest["views"]}
    for vp in sheet.get("viewports", []):
        _, content = viewport_state(project, vp, views)
        out[vp["id"]] = "missing" if content is None else hashlib.sha256(_canon([content, vp.get("scale"), vp["x"], vp["y"], vp.get("title", "")]).encode()).hexdigest()[:16]    # (unchanged for plain views, so issued revisions stay comparable)
    out["_text"] = hashlib.sha256(_canon([sheet["name"], sheet["number"], sheet["paper"], sheet["titleblock"], sheet["notes"], project.manifest["name"],
                                          project.manifest.get("client", ""), project.manifest.get("number", "")]).encode()).hexdigest()[:16]
    return out


def next_label(sheet: Mapping[str, Any]) -> str:
    used = [r["rev"] for r in sheet.get("revisions", [])]
    last = sheet.get("revision") or ""
    if last.isdigit():
        n = int(last) + 1
        label = str(n).zfill(len(last))
    elif len(last) == 1 and last.isalpha():
        label = chr(ord(last) + 1)
    else:
        label = "01"
    while label in used:
        label = label + "1" if not label.isdigit() else str(int(label) + 1).zfill(len(label))
    return label


def issue(project: Any, sheet: dict[str, Any], p: Mapping[str, Any]) -> dict[str, Any]:
    """Record an issued revision of the sheet: label, description, who, when, and the digests of what the sheet shows now."""
    prepare(sheet)
    if len(sheet["revisions"]) >= MAX_REVISIONS:
        raise BadRequest(f"A sheet holds at most {MAX_REVISIONS} revisions")
    label = text(p["revision"], "revision", 10) if p.get("revision") else (next_label(sheet) if sheet["revisions"] else sheet["revision"])    # the first issue is the label the sheet already carries
    if not _REV.match(label):
        raise BadRequest("A revision label is up to 10 letters, digits, '.', '_' or '-'")
    if label in {r["rev"] for r in sheet["revisions"]}:
        raise Conflict(f"Revision {label} has already been issued on sheet {sheet['number']}")
    when = text(p["date"], "date", 10) if p.get("date") else _date.today().isoformat()
    try:
        _date.fromisoformat(when)
    except ValueError as exc:
        raise BadRequest("The date must be written year-month-day, such as 2026-11-02") from exc
    entry = {"id": new_id(), "rev": label, "date": when, "description": text(p.get("description"), "description", 200), "by": text(p.get("by", ""), "by", 40, empty=True),
             "model_revision": project.revision, "digests": digests(project, sheet)}
    sheet["revisions"].append(entry)
    sheet["revision"] = label
    return {"id": entry["id"], "revision": label}


def status(project: Any, sheet: Mapping[str, Any]) -> dict[str, Any]:
    """Has what the sheet shows changed since its last issued revision? Names each viewport that differs."""
    revisions = sheet.get("revisions", [])
    views = {v["id"]: v for v in project.manifest["views"]}
    if not revisions:
        return {"issued": False, "revision": sheet.get("revision", ""), "state": "not issued", "changed": [], "viewports": [
            {"viewport": vp["id"], "view": vp.get("view_id", ""), "name": viewport_state(project, vp, views)[0],
             "state": "missing" if viewport_state(project, vp, views)[1] is None else "not issued"} for vp in sheet.get("viewports", [])]}
    last = revisions[-1]
    now = digests(project, {**sheet, "titleblock": sheet.get("titleblock") or blank_titleblock(), "notes": sheet.get("notes", [])})
    rows, changed = [], []
    for vp in sheet.get("viewports", []):
        name, content = viewport_state(project, vp, views)
        was = last["digests"].get(vp["id"])
        state = "missing" if content is None else "new" if was is None else "current" if was == now[vp["id"]] else "changed"
        rows.append({"viewport": vp["id"], "view": vp.get("view_id", ""), "name": name, "state": state})
        if state != "current":
            changed.append(rows[-1]["name"] + (" (new)" if state == "new" else " (missing)" if state == "missing" else ""))
    removed = [k for k in last["digests"] if k != "_text" and k not in now]
    if removed:
        changed.append(f"{len(removed)} viewport(s) removed")
    if last["digests"].get("_text") != now["_text"]:
        changed.append("title block or notes")
    return {"issued": True, "revision": last["rev"], "date": last["date"], "state": "changed" if changed else "current", "changed": changed, "viewports": rows,
            "model_revision": last["model_revision"]}


def title_block(project: Any, sheet: Mapping[str, Any]) -> dict[str, Any]:
    """The fields the title block prints, with the revision table (newest first, at most six rows)."""
    tb = sheet.get("titleblock") or blank_titleblock()
    scales = sorted({vp["scale"] for vp in sheet.get("viewports", []) if vp.get("scale")})
    return {"project": project.name, "client": str(project.manifest.get("client", "")), "project_number": str(project.manifest.get("number", "")),
            "sheet_number": sheet["number"], "sheet_name": sheet["name"], "scale": sheet["scale"] if not scales else scales[0] if len(scales) == 1 else "As indicated",
            "paper": sheet["paper"], "drawn_by": tb["drawn_by"], "checked_by": tb["checked_by"], "date": tb["date"], "status": tb["status"], "revision": sheet["revision"],
            "revisions": [{"rev": r["rev"], "date": r["date"], "description": r["description"], "by": r["by"]} for r in reversed(sheet.get("revisions", []))][:6]}


def issues_of(project: Any) -> list[dict[str, str]]:
    """Documentation findings for the model check: revision consistency and sheets that differ from their last issue."""
    from . import elements as E
    out: list[dict[str, str]] = []
    for sheet in project.manifest["sheets"]:
        revisions = sheet.get("revisions", [])
        labels = [r["rev"] for r in revisions]
        if len(set(labels)) != len(labels):
            out.append(E.issue("error", sheet["id"], f"Sheet {sheet['number']} has the same revision issued twice", "documentation"))
        if revisions and sheet["revision"] != revisions[-1]["rev"]:
            out.append(E.issue("error", sheet["id"], f"Sheet {sheet['number']} shows revision {sheet['revision']} but its last issued revision is {revisions[-1]['rev']}", "documentation"))
        state = status(project, sheet)
        if state["state"] == "changed":
            out.append(E.issue("warning", sheet["id"], f"Sheet {sheet['number']} has changed since revision {state['revision']} was issued: {', '.join(state['changed'])}", "documentation"))
        if not sheet.get("viewports"):
            out.append(E.issue("warning", sheet["id"], f"Sheet {sheet['number']} has no views on it", "documentation"))
    return out


# ------------------------------------------------------------------------------------------------------------------------ .sldc export
# A sheet leaves SPADRA as a real SLAI document (``.sldc``). It carries two things: the sheet as a person reads it (title block, viewports with their
# scale and place, revisions and notes, as headings and tables, and each 3D picture as a figure) and, in the document extension ``spadra.sheet``, the whole
# sheet exactly as SPADRA draws it: every drawing entity, model outline, hatch, symbol, dimension and schedule row of every viewport, the layout and the title
# block. Reading the file back gives that data unchanged, so what is preserved is checked by comparison (``verify_sldc``) and not claimed.
SLDC_EXTENSION = "spadra.sheet"
SLDC_FORMAT = "spadra.sheet.v1"
MAX_SHEET_BYTES = 15 * 1024 * 1024


def _blocks(project: Any, data: Mapping[str, Any], figures: dict[str, Any]) -> list[dict[str, Any]]:
    from src.utils import sldc as S
    tb = data["title_block"]
    rows = lambda pairs: S.make_table([[k, "" if v is None else str(v)] for k, v in pairs], header_rows=0)       # noqa: E731
    blocks = [S.make_block("heading", f"Sheet {data['number']}  {data['name']}".strip(), attributes={"level": 1}),
              S.make_block("paragraph", f"{data['paper']} ({data['width']:g} × {data['height']:g} mm), scale {data['scale']}, revision {data['revision']}. "
                                        f"Made from model revision {data['revision_number']} of {data['project']}."),
              S.make_block("heading", "Title block", attributes={"level": 2}),
              rows([("Project", tb["project"]), ("Client", tb["client"]), ("Project no.", tb["project_number"]), ("Sheet", tb["sheet_number"]), ("Name", tb["sheet_name"]),
                    ("Scale", tb["scale"]), ("Paper", tb["paper"]), ("Status", tb["status"]), ("Drawn by", tb["drawn_by"]), ("Checked by", tb["checked_by"]), ("Date", tb["date"]),
                    ("Revision", tb["revision"])]),
              S.make_block("heading", "Viewports", attributes={"level": 2})]
    table = [["Title", "Shows", "Scale", "x (mm)", "y (mm)", "Locked"]]
    for vp in data["viewports"]:
        table.append([str(vp.get("title") or vp["view"].get("name", "")), SV_kind(vp), str(vp.get("scale", "")), f"{vp['x']:g}", f"{vp['y']:g}", "yes" if vp.get("locked") else ""])
    blocks.append(S.make_table(table, header_rows=1))
    for vp in data["viewports"]:
        if vp.get("kind") == "snapshot" and vp["id"] in figures:
            blocks.append(figures[vp["id"]])
    if data["revisions"]:
        blocks += [S.make_block("heading", "Revisions", attributes={"level": 2}),
                   S.make_table([["Revision", "Date", "Description", "By"]] + [[r["rev"], r["date"], r["description"], r["by"]] for r in data["revisions"]], header_rows=1)]
    if data["notes"]:
        blocks += [S.make_block("heading", "Notes", attributes={"level": 2})] + [S.make_block("list_item", n["text"]) for n in data["notes"]]
    return blocks


def SV_kind(vp: Mapping[str, Any]) -> str:
    kind = vp.get("kind") or ("schedule" if vp.get("schedule") else "view")
    return {"view": "drawing", "model": "3D model", "snapshot": "3D picture", "report": "report", "schedule": "schedule"}.get(kind, kind)


def to_sldc(project: Any, sheet_id: Any) -> tuple[bytes, str]:
    """The sheet as ``.sldc`` bytes (validated by the SLDC writer) and the file name to offer."""
    import copy
    from src.utils import sldc as S
    from ..tools.errors import LimitExceeded
    sheet = find(project, sheet_id)
    data = copy.deepcopy(project.sheet_data(sheet["id"]))
    placed = {vp["id"]: vp for vp in sheet.get("viewports", [])}
    assets, figures = [], {}
    for vp in data["viewports"]:
        raw = placed[vp["id"]]
        vp["locked"] = bool(raw.get("locked"))
        vp["manual"] = bool(raw.get("manual"))
        if vp.get("kind") == "snapshot":
            png = project.snapshots[vp["id"]]
            asset = S.make_asset(png)
            assets.append(asset)
            vp["image"] = {k: v for k, v in vp["image"].items() if k != "href"} | {"asset": asset.id}
            w = vp["paper_width"]
            figures[vp["id"]] = S.make_figure(asset, f"3D picture on sheet {data['number']}", caption=str(vp.get("title") or "3D snapshot"), width_mm=round(w, 3))      # width alone: a height would need SLDC 1.1
    envelope = {"format": SLDC_FORMAT, "sheet": data, "order": [vp["id"] for vp in data["viewports"]],
                "unit": project.manifest["unit"], "model_revision": project.revision}
    try:
        package = S.new_package(f"Sheet {data['number']} {data['name']}".strip(), _blocks(project, data, figures), assets=assets,
                                metadata={"project": project.name, "sheet": data["number"], "paper": data["paper"], "model_revision": str(project.revision)},
                                extensions={SLDC_EXTENSION: envelope})
        out = S.dumps_package(package)
    except S.SLDCError as exc:
        raise BadRequest(f"The sheet could not be written: {exc}") from exc
    if len(out) > MAX_SHEET_BYTES:
        raise LimitExceeded("The sheet is too large to export as one .sldc file")
    from .utils.spadra_helpers import slug
    return out, f"{slug(project.name)}-{slug(str(data['number']))}.sldc"


def read_sldc(data: bytes) -> dict[str, Any]:
    """The envelope of a sheet file (``format``, ``sheet``, ``order``, ``unit``) and the pictures it holds (``assets``, by id), or a plain-language refusal."""
    from src.utils import sldc as S
    try:
        package = S.loads_package(data)
    except S.SLDCError as exc:
        raise BadRequest(f"That is not a readable .sldc file: {exc}") from exc
    envelope = (package.document.get("extensions") or {}).get(SLDC_EXTENSION)
    if not isinstance(envelope, dict) or envelope.get("format") != SLDC_FORMAT:
        raise BadRequest("This .sldc file was not made from a SPADRA sheet")
    return {**envelope, "assets": {k: bytes(a.data) for k, a in package.assets.items()}}


def verify_sldc(project: Any, data: bytes, sheet_id: Any) -> dict[str, Any]:
    """Whether the file holds the sheet as it is now: every difference found is listed, so an empty list is what 'preserved' means."""
    import copy
    got = read_sldc(data)
    sheet = find(project, sheet_id)
    now = copy.deepcopy(project.sheet_data(sheet["id"]))
    differences: list[str] = []
    saved = got["sheet"]
    for key in sorted(set(now) | set(saved)):
        if key == "viewports":
            continue
        if now.get(key) != saved.get(key):
            differences.append(f"sheet field '{key}' differs")
    ids_now, ids_saved = [v["id"] for v in now["viewports"]], [v["id"] for v in saved["viewports"]]
    if ids_now != ids_saved:
        differences.append("the viewports or their drawing order differ")
    placed = {vp["id"]: vp for vp in sheet.get("viewports", [])}
    saved_by = {v["id"]: v for v in saved["viewports"]}
    for vp in now["viewports"]:
        other = saved_by.get(vp["id"])
        if other is None:
            continue
        for key in sorted(set(vp) | set(other)):
            if key in ("image", "locked", "manual"):
                continue
            if vp.get(key) != other.get(key):
                differences.append(f"viewport {vp['id']}: '{key}' differs")
        if vp.get("kind") == "snapshot":
            asset = (other.get("image") or {}).get("asset")
            if got["assets"].get(asset) != project.snapshots.get(vp["id"]):
                differences.append(f"viewport {vp['id']}: the picture differs")
        if bool(placed[vp["id"]].get("locked")) != bool(other.get("locked")):
            differences.append(f"viewport {vp['id']}: lock differs")
    return {"identical": not differences, "differences": differences, "viewports": len(ids_now)}


def import_sldc(project: Any, data: bytes) -> dict[str, Any]:
    """Make a new sheet from a sheet file: paper, title block, notes and revisions, and every viewport whose view (or report) exists in this project, at the
    saved place, scale and lock. What cannot be reconnected (its view is not in this project) is listed, never replaced by a guess."""
    from ..tools.validation import new_id
    got = read_sldc(data)
    saved = got["sheet"]
    if got.get("unit") != project.manifest["unit"]:
        raise BadRequest(f"The file was drawn in {got.get('unit')} and this project uses {project.manifest['unit']}; the placements would not match")
    existing = {s["number"] for s in project.manifest["sheets"]}
    number_ = saved["number"]
    while number_ in existing:
        number_ = number_ + "-copy"
    views = {v["id"] for v in project.manifest["views"]}
    reports = {r["id"] for r in project.manifest["reports"]}
    sheet = {"id": new_id(), "number": number_, "name": saved["name"], "paper": saved["paper"], "scale": saved["scale"], "revision": saved["revision"], "viewports": [],
             "titleblock": {k: saved["title_block"][k] for k in TITLEBLOCK | {"status": 0} if k in saved["title_block"]}, "notes": [dict(n) for n in saved["notes"]],
             "revisions": []}
    skipped = []
    for vp in saved["viewports"]:
        kind = vp.get("kind") or "view"
        keep = {k: vp[k] for k in ("x", "y", "scale", "anchor", "azimuth", "elevation", "title", "width", "size", "lines", "locked", "manual", "view_id", "report_id", "kind") if k in vp}
        keep["id"] = new_id()
        if kind == "snapshot":
            asset = (vp.get("image") or {}).get("asset")
            if asset not in got["assets"]:
                skipped.append({"viewport": vp["id"], "why": "its picture is not in the file"})
                continue
            project.snapshots[keep["id"]] = got["assets"][asset]
            keep["image"] = {k: v for k, v in vp["image"].items() if k != "asset"}
        elif kind == "report" and vp.get("report_id") not in reports:
            skipped.append({"viewport": vp["id"], "why": "its report is not in this project"})
            continue
        elif kind != "report" and vp.get("view_id") not in views:
            skipped.append({"viewport": vp["id"], "why": "its view is not in this project"})
            continue
        sheet["viewports"].append(keep)
    project.manifest["sheets"].append(sheet)
    return {"id": sheet["id"], "number": number_, "viewports": len(sheet["viewports"]), "skipped": skipped}
