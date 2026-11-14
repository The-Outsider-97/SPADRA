"""What an element is connected to, and the properties every element shares.

Everything here is read from data the project already holds: the hosting and level parameters in the SGEO metadata, the rooms' geometric
relations, the tags in the drawings, the viewports on the sheets, and the animation. Nothing is stored twice and nothing is inferred; a link is
shown only when the data to follow it exists.

Also here: **custom properties**, the free-form name/value pairs a person can attach to any element. They are stored in the element's SGEO
metadata under the ``custom:`` namespace, so they travel with the element through save, copy, GLB/SGEO export and import."""
from __future__ import annotations

import re
from uuid import uuid4
from typing import Any, Mapping

from ..tools.errors import BadRequest, NotFound
from ..tools.validation import text
from . import elements as E
from . import sfb as SF
from . import classifications as CL

CUSTOM_NS = "custom"
_CUSTOM_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
CUSTOM_TYPES = ("text", "integer", "number", "boolean", "length", "area", "volume", "angle", "color")
CUSTOM_UNITS = {"length": "mm", "area": "m²", "volume": "m³", "angle": "°"}
MAX_CUSTOM = 40
MAX_PROJECTION_WORK = 600                # elements × section/elevation views above which "appears in" is not evaluated for sections


# --------------------------------------------------------------------------------------------------------------- custom properties
def custom_of(meta: Mapping[str, Any]) -> dict[str, Any]:
    prefix = f"{CUSTOM_NS}:"
    return {k[len(prefix):]: v for k, v in meta.items() if k.startswith(prefix)}


def _scope_owner(project: Any, oid: str, scope: str) -> str:
    meta = E.meta_of(project, oid)
    if scope == "project":
        return "*"
    if scope == "type":
        from .bimdata import type_key
        return type_key(meta)
    if scope == "family":
        obj = project.model.get(oid)
        return str(obj.get("family_id") or f"category:{E.m(meta, 'category', 'generic')}")
    raise BadRequest("Custom property scope must be instance, type, family or project")


def _definitions(project: Any, create: bool = False) -> list[dict[str, Any]]:
    if create:
        return project.manifest.setdefault("bim", {}).setdefault("custom_definitions", [])
    return (project.manifest.get("bim") or {}).get("custom_definitions") or []


def effective_custom(project: Any, oid: str) -> dict[str, dict[str, Any]]:
    """One deterministic, non-duplicating custom property lookup: instance > type > family > project."""
    meta = E.meta_of(project, oid)
    types = E.m(meta, "custom_types") or {}
    inherited: dict[str, dict[str, Any]] = {}
    for scope in ("project", "family", "type"):
        owner = _scope_owner(project, oid, scope)
        for d in _definitions(project):
            if d.get("scope") == scope and d.get("owner") == owner:
                inherited[d["name"]] = {"value": d["value"], "type": d["type"], "scope": scope, "id": d["id"]}
    for name, value in custom_of(meta).items():
        inherited[name] = {"value": value, "type": types.get(name) or "text", "scope": "instance", "id": None}
    return inherited


def custom_group(project: Any, oid: str) -> dict[str, Any]:
    """One shared presentation contract for element and selected assembly-part editors."""
    meta = E.meta_of(project, oid)
    custom = custom_of(meta)
    types = E.m(meta, "custom_types") or {}
    rows = [
        {"key": f"custom:{n}", "label": n, "type": "color" if types.get(n) == "color" else "choice" if isinstance(v, bool) else "number" if isinstance(v, (int, float)) else "text",
         "value": ("true" if v else "false") if isinstance(v, bool) else v, "removable": True,
         **({"unit": CUSTOM_UNITS[types[n]]} if types.get(n) in CUSTOM_UNITS else {}),
         **({"choices": [{"value": "true", "label": "Yes"}, {"value": "false", "label": "No"}]} if isinstance(v, bool) else {})} for n, v in sorted(custom.items())]
    for name, d in sorted(effective_custom(project, oid).items()):
        if d["scope"] == "instance":
            continue
        v, kind = d["value"], d["type"]
        rows.append({"key": f"customdef:{d['id']}", "label": f"{name} ({d['scope']})",
                     "type": "color" if kind == "color" else "choice" if isinstance(v, bool) else "number" if isinstance(v, (int, float)) else "text",
                     "value": "true" if v is True else "false" if v is False else v, "removable": True,
                     **({"unit": CUSTOM_UNITS[kind]} if kind in CUSTOM_UNITS else {}),
                     **({"choices": [{"value": "true", "label": "Yes"}, {"value": "false", "label": "No"}]} if isinstance(v, bool) else {})})
    return {"title": "Custom properties (not buildingSMART)", "rows": rows, "add": "custom"}


def set_shared_custom(project: Any, oid: str, name: str, value: Any, kind: str, scope: str) -> None:
    if scope not in ("type", "family", "project"):
        raise BadRequest("Shared property scope must be type, family or project")
    name = text(name, "name", 40)
    if not _CUSTOM_NAME.fullmatch(name):
        raise BadRequest("Property names start with a letter, followed by letters, digits or _")
    if kind not in CUSTOM_TYPES:
        raise BadRequest(f"Unsupported custom property type '{kind}'")
    owner = _scope_owner(project, oid, scope)
    definitions = _definitions(project, True)
    previous = next((d for d in definitions if d["scope"] == scope and d["owner"] == owner and d["name"] == name), None)
    if previous is None and len(definitions) >= 200:
        raise BadRequest("A project supports up to 200 shared property definitions")
    cast = _coerce_custom(name, value, kind)
    if previous is None:
        definitions.append({"id": str(uuid4()), "name": name, "scope": scope, "owner": owner, "type": kind, "value": cast})
    else:
        previous.update(type=kind, value=cast)


def update_shared_custom(project: Any, oid: str, identifier: str, payload: Any) -> None:
    d = next((d for d in _definitions(project) if d["id"] == identifier), None)
    if d is None or d["owner"] != _scope_owner(project, oid, d["scope"]):
        raise BadRequest("The selected element does not own that shared property")
    definitions = _definitions(project, True)
    if payload is None:
        definitions.remove(d)
    elif isinstance(payload, Mapping) and "rename" in payload:
        new = text(payload["rename"], "new name", 40)
        if not _CUSTOM_NAME.fullmatch(new) or any(other is not d and other["scope"] == d["scope"] and other["owner"] == d["owner"] and other["name"] == new for other in definitions):
            raise BadRequest("Invalid or duplicate shared property name")
        d["name"] = new
    else:
        d["value"] = _coerce_custom(d["name"], payload, d["type"])


def _coerce_custom(name: str, value: Any, kind: str | None) -> Any:
    if kind == "text":
        value = text(str(value), name, 200, empty=True)
    elif kind == "boolean":
        if isinstance(value, str) and value.lower() in ("true", "false"):
            value = value.lower() == "true"
        if not isinstance(value, bool):
            raise BadRequest(f"{name} must be true or false")
    elif kind in ("number", "integer", "length", "area", "volume", "angle"):
        from ..tools.validation import number
        value = number(value, name, -1e12, 1e12)
        if kind == "integer":
            if not float(value).is_integer():
                raise BadRequest(f"{name} must be a whole number")
            value = int(value)
    elif kind == "color":
        value = E.color(value)
        if value is None:
            raise BadRequest(f"{name} requires a six-digit HEX color")
    if isinstance(value, bool) or isinstance(value, (int, float)):
        if isinstance(value, float) and (value != value or abs(value) == float("inf")):
            raise BadRequest("A numeric property must be a finite number")
    elif isinstance(value, str):
        value = text(value, name, 200, empty=True)
    else:
        raise BadRequest("A custom property is text, a number or yes/no")
    return value


def set_custom(project: Any, oid: str, name: Any, value: Any, kind: str | None = None) -> None:
    """Set a custom property, or remove it when ``value`` is None."""
    name = text(name, "name", 40)
    if not _CUSTOM_NAME.match(name):
        raise BadRequest(f"'{name}' is not a usable property name", hint="Start with a letter; letters, digits and _ only (up to 40)")
    meta = E.meta_of(project, oid)
    current = custom_of(meta)
    schema = dict(E.m(meta, "custom_types") or {})
    if value is None:
        if name not in current:
            raise BadRequest(f"There is no custom property '{name}' to remove")
        schema.pop(name, None)
        updated = {k: v for k, v in meta.items() if k != f"{CUSTOM_NS}:{name}"}
        updated[f"{E.NS}:custom_types"] = schema
        project.model.attach_semantics(oid, updated, replace=True)
        return
    if kind is not None and kind not in CUSTOM_TYPES:
        raise BadRequest(f"Unsupported custom property type '{kind}'")
    kind = kind or schema.get(name)
    value = _coerce_custom(name, value, kind)
    if name not in current and len(current) >= MAX_CUSTOM:
        raise BadRequest(f"An element holds at most {MAX_CUSTOM} custom properties")
    if kind:
        schema[name] = kind
        E._attach(project, oid, {"custom_types": schema})
    project.model.attach_semantics(oid, {name: value}, namespace=CUSTOM_NS)


def rename_custom(project: Any, oid: str, old: Any, new: Any) -> None:
    """Rename one custom property without changing its type, scope, or value."""
    old, new = text(old, "name", 40), text(new, "new name", 40)
    if not _CUSTOM_NAME.fullmatch(new):
        raise BadRequest("Property names start with a letter and contain only letters, digits and _ (up to 40)")
    meta = E.meta_of(project, oid)
    current = custom_of(meta)
    if old not in current:
        raise BadRequest(f"There is no custom property '{old}'")
    if new != old and new in current:
        raise BadRequest(f"A custom property named '{new}' already exists")
    if new == old:
        return
    schema = dict(E.m(meta, "custom_types") or {})
    if old in schema:
        schema[new] = schema.pop(old)
    updated = {k: v for k, v in meta.items() if k != f"custom:{old}"}
    updated[f"custom:{new}"] = current[old]
    updated[f"{E.NS}:custom_types"] = schema
    # An identifier rename is a refactor: update references on this element
    # atomically so dimension formulas and material conditions keep working.
    replace_name = lambda expr: re.sub(rf"\b{re.escape(old)}\b", new, expr)  # noqa: E731
    formulas = E.m(meta, "formulas") or {}
    if isinstance(formulas, dict):
        updated[f"{E.NS}:formulas"] = {k: replace_name(v) if isinstance(v, str) else v for k, v in formulas.items()}
    rule = E.m(meta, "material_rule") or {}
    if isinstance(rule, dict) and isinstance(rule.get("condition"), str):
        updated[f"{E.NS}:material_rule"] = {**rule, "condition": replace_name(rule["condition"])}
    project.model.attach_semantics(oid, updated, replace=True)


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
    identity["rows"] += [
        {"key": "description", "label": "Description", "type": "multiline",
         "value": str(E.m(meta, "description", ""))},
        {"label": "Category", "type": "readonly", "value": cat.title()},
        {"label": "Element ID", "type": "readonly", "value": oid}]
    from . import arrays as AY
    item = AY.item_rows(project, oid)
    if item:
        out["groups"].append({"title": "Array", "rows": item})
    out["groups"].append(SF.rows_for(meta, cat))
    out["groups"].extend(CL.groups_for(meta, cat))
    from . import bimdata as BD
    out["groups"].extend(BD.groups_for(project, oid))
    out["groups"].append(custom_group(project, oid))
    links = related(project, oid) if relations else []
    if links:
        out["groups"].append({"title": "Relationships", "rows": links})
    return out
