"""What an element is connected to, and the properties every element shares.

Everything here is read from data the project already holds: the hosting and level parameters in the SGEO metadata, the rooms' geometric
relations, the tags in the drawings, the viewports on the sheets, and the animation. Nothing is stored twice and nothing is inferred; a link is
shown only when the data to follow it exists.

Also here: **custom properties**, the free-form name/value pairs a person can attach to any element. They are stored in the element's SGEO
metadata under the ``custom:`` namespace, so they travel with the element through save, copy, GLB/SGEO export and import."""
from __future__ import annotations

import re
from typing import Any, Mapping

from ..tools.errors import BadRequest, NotFound
from ..tools.validation import text
from . import elements as E
from . import sfb as SF
from . import classifications as CL

CUSTOM_NS = "custom"
_CUSTOM_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
MAX_CUSTOM = 40
MAX_PROJECTION_WORK = 600                # elements × section/elevation views above which "appears in" is not evaluated for sections


# --------------------------------------------------------------------------------------------------------------- custom properties
def custom_of(meta: Mapping[str, Any]) -> dict[str, Any]:
    prefix = f"{CUSTOM_NS}:"
    return {k[len(prefix):]: v for k, v in meta.items() if k.startswith(prefix)}


def set_custom(project: Any, oid: str, name: Any, value: Any) -> None:
    """Set a custom property, or remove it when ``value`` is None."""
    name = text(name, "name", 40)
    if not _CUSTOM_NAME.match(name):
        raise BadRequest(f"'{name}' is not a usable property name", hint="Start with a letter; letters, digits and _ only (up to 40)")
    meta = E.meta_of(project, oid)
    current = custom_of(meta)
    if value is None:
        if name not in current:
            raise BadRequest(f"There is no custom property '{name}' to remove")
        project.model.attach_semantics(oid, {k: v for k, v in meta.items() if k != f"{CUSTOM_NS}:{name}"}, replace=True)
        return
    if isinstance(value, bool) or isinstance(value, (int, float)):
        if isinstance(value, float) and (value != value or abs(value) == float("inf")):
            raise BadRequest("A numeric property must be a finite number")
    elif isinstance(value, str):
        value = text(value, name, 200, empty=True)
    else:
        raise BadRequest("A custom property is text, a number or yes/no")
    if name not in current and len(current) >= MAX_CUSTOM:
        raise BadRequest(f"An element holds at most {MAX_CUSTOM} custom properties")
    project.model.attach_semantics(oid, {name: value}, namespace=CUSTOM_NS)


def parse_custom(raw: Any) -> Any:
    """What the Properties panel sends for a custom value: a number when it reads as one, yes/no for true/false, otherwise the text."""
    if isinstance(raw, (bool, int, float)) or raw is None:
        return raw
    s = str(raw).strip()
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    try:
        v = float(s)
        return int(v) if v.is_integer() and "." not in s and "e" not in s.lower() else v
    except ValueError:
        return str(raw)


# ----------------------------------------------------------------------------------------------------------------------- relations
def _link(label: str, value: str, *, select: Mapping[str, Any] | None = None, open_: Mapping[str, Any] | None = None, dialog: str | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {"label": label, "type": "link", "value": value}
    target: dict[str, Any] = {}
    if select:
        target["select"] = dict(select)
    if open_:
        target["open"] = dict(open_)
    if dialog:
        target["dialog"] = dialog
    if target:
        row["target"] = target
    return row


def _view_open(view: Mapping[str, Any]) -> dict[str, Any]:
    return {"kind": "schedule" if view["kind"] == "schedule" else "3d" if view["kind"] == "3d" else "drawing", "id": view["id"]}


def tags_of(project: Any, oid: str) -> list[dict[str, Any]]:
    """Tag annotations that point at this element, in every drawing."""
    from . import annotations as AN
    return AN.tags_for(project, oid)


def views_showing(project: Any, oid: str) -> list[dict[str, Any]]:
    """Views that show the element: its level's plan, and the sections and elevations whose projection contains it."""
    meta = E.meta_of(project, oid)
    cat, level = E.m(meta, "category"), E.m(meta, "level")
    if cat in E.OPENINGS and E.m(meta, "host"):
        level = E.m(E.meta_of(project, E.m(meta, "host")), "level") if E.alive(project, E.m(meta, "host")) else level
    out: list[dict[str, Any]] = []
    views = project.manifest["views"]
    for v in views:
        if v["kind"] == "plan" and (v.get("level_id") == level or cat == "grid"):
            out.append(v)
    cutting = [v for v in views if v["kind"] in ("section", "elevation")]
    if cutting and cat not in ("room", "grid"):
        from . import documentation as D
        if len(E.element_ids(project)) * len(cutting) <= MAX_PROJECTION_WORK:
            for v in cutting:
                key = ("projection-ids", v["id"])
                if key not in project._cache:
                    project._cache[key] = {i["id"] for i in D.project_view(project, v, 1.0)["items"]}
                if oid in project._cache[key]:
                    out.append(v)
    return out


def sheets_showing(project: Any, view_ids: set[str]) -> list[dict[str, Any]]:
    return [s for s in project.manifest["sheets"] if any(vp.get("view_id") in view_ids for vp in s.get("viewports", []))]


def related(project: Any, oid: str) -> list[dict[str, Any]]:
    """The 'Relationships' rows of the Properties panel: each is a link the person can follow."""
    meta = E.meta_of(project, oid)
    cat = str(E.m(meta, "category", "generic"))
    obj = project.model.get(oid)
    rows: list[dict[str, Any]] = []
    name_of = lambda i: project.model.get(i)["name"] or i                                                  # noqa: E731

    level_id = E.m(meta, "level")
    if level_id:
        try:
            level = project.level(level_id)
            plan = next((v for v in project.manifest["views"] if v["kind"] == "plan" and v.get("level_id") == level_id), None)
            rows.append(_link("Level", f"{level['name']} ({level['elevation']:g} m)", open_=_view_open(plan) if plan else None))
        except NotFound:
            rows.append({"label": "Level", "type": "readonly", "value": "(missing)"})
    if E.m(meta, "top_level"):
        try:
            rows.append({"label": "Top level", "type": "readonly", "value": project.level(E.m(meta, "top_level"))["name"]})
        except NotFound:
            rows.append({"label": "Top level", "type": "readonly", "value": "(missing)"})
    host = E.m(meta, "host")
    if cat in E.OPENINGS:
        rows.append(_link("Host wall", name_of(host), select={"model": host}) if host and E.alive(project, host) else {"label": "Host wall", "type": "readonly", "value": "(missing)"})
    if cat == "wall":
        for hid in E.hosted(project, oid):
            hm = project.model.get(hid)["metadata"]
            rows.append(_link(str(E.m(hm, "category")).title() + " hosted", name_of(hid), select={"model": hid}))
    if "family_id" in obj:
        from . import families as FM
        rows.append(_link("Family", FM.family(project, obj["family_id"])["name"], dialog="families"))
    if cat in ("wall", "door", "window") or E.is_column(meta):
        rooms = _rooms_of(project, oid, cat, level_id, host)
        for rid in rooms:
            rows.append(_link("In room", name_of(rid), select={"model": rid}))
    if cat == "room":
        from . import rooms as R
        d = R.data(project, oid)
        for w in d["walls"]:
            rows.append(_link("Bounded by", w["name"], select={"model": w["id"]}))
        for item in d["doors"] + d["windows"] + d["columns"]:
            rows.append(_link("Contains", item["name"], select={"model": item["id"]}))
    shown = views_showing(project, oid)
    for v in shown:
        rows.append(_link("Appears in", v["name"], open_=_view_open(v)))
    for s in sheets_showing(project, {v["id"] for v in shown}):
        rows.append(_link("On sheet", f"{s['number']} {s['name']}".strip(), open_={"kind": "sheet", "id": s["id"]}))
    for v in project.manifest["views"]:
        if v["kind"] == "schedule" and v.get("category") in (cat, "count", "material"):
            rows.append(_link("Listed in", v["name"], open_=_view_open(v)))
    for tag in tags_of(project, oid):
        rows.append(_link("Tagged in", tag["view_name"], open_={"kind": "drawing", "id": tag["view"]}, select={"drawing": tag["view"], "entity": tag["entity"]}))
    spec = project.model.animation()
    if spec is not None:
        n = sum(1 for t in spec["tracks"] if t["object_id"] == oid)
        if n:
            rows.append(_link("Animation", f"{n} track{'s' if n != 1 else ''}", open_={"kind": "3d"}))
    return rows


def _rooms_of(project: Any, oid: str, cat: str, level_id: Any, host: Any) -> list[str]:
    from . import rooms as R
    if cat in E.OPENINGS and host and E.alive(project, host):
        level_id = E.m(E.meta_of(project, host), "level")
    out = []
    for rid in E.element_ids(project):
        rm = E.meta_of(project, rid)
        if E.m(rm, "category") != "room" or E.m(rm, "level") != level_id:
            continue
        d = R.data(project, rid)
        ids = {w["id"] for w in d["walls"]} | {x["id"] for x in d["doors"] + d["windows"] + d["columns"]}
        if oid in ids:
            out.append(rid)
    return out


# ------------------------------------------------------------------------------------------------------------ properties panel
def augment(project: Any, oid: str, out: dict[str, Any], relations: bool = False) -> dict[str, Any]:
    """Add what every element has to its Properties: identification, custom properties and (when asked) relationships."""
    meta = E.meta_of(project, oid)
    cat = str(E.m(meta, "category", "generic"))
    identity = next((g for g in out["groups"] if g["title"] == "Identity"), None)
    if identity is None:
        identity = {"title": "Identity", "rows": []}
        out["groups"].append(identity)
    identity["rows"] += [{"label": "Category", "type": "readonly", "value": cat.title()}, {"label": "Element ID", "type": "readonly", "value": oid}]
    from . import arrays as AY
    item = AY.item_rows(project, oid)
    if item:
        out["groups"].append({"title": "Array", "rows": item})
    out["groups"].append(SF.rows_for(meta, cat))
    out["groups"].extend(CL.groups_for(meta, cat))
    from . import bimdata as BD
    out["groups"].extend(BD.groups_for(project, oid))
    custom = custom_of(meta)
    out["groups"].append({"title": "Custom properties (not buildingSMART)", "rows": [
        {"key": f"custom:{n}", "label": n, "type": "choice" if isinstance(v, bool) else "number" if isinstance(v, (int, float)) else "text",
         "value": ("true" if v else "false") if isinstance(v, bool) else v, "removable": True,
         **({"choices": [{"value": "true", "label": "Yes"}, {"value": "false", "label": "No"}]} if isinstance(v, bool) else {})} for n, v in sorted(custom.items())],
        "add": "custom"})
    links = related(project, oid) if relations else []
    if links:
        out["groups"].append({"title": "Relationships", "rows": links})
    return out
