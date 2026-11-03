"""Annotations that belong to model elements: tags.

A tag is an ordinary SDRW ``text`` entity on the drawing's ``Tags`` layer whose metadata names the element it tags (``spadra:tag_of``) and the
field it shows (``spadra:tag_field``). Its text is *derived*: whenever a command changes the model, the tags are brought up to date inside that
same command, so the drawing, the model and the schedules always agree about a door's mark, a room's name or a window's size, and a single
Undo puts the element and its tags back together.

* Only entities that carry the tag metadata are ever rewritten. Every other note, dimension and line in a drawing is the person's own, and
  is never touched by a model change (manual annotations are preserved).
* If the tagged element is deleted the tag keeps its last text, is marked detached, and is reported by the model check, so a broken
  association is visible rather than silently wrong. Undo of the deletion re-attaches it.
"""
from __future__ import annotations

from typing import Any, Mapping

from ..tools.errors import BadRequest, NotFound
from ..tools.validation import text
from . import elements as E
from . import sfb as SF

LAYER = "Tags"
KEY_OF, KEY_FIELD, KEY_DETACHED = "spadra:tag_of", "spadra:tag_field", "spadra:detached"
FIELDS = {"mark": "Mark (schedule number)", "name": "Name", "type": "Type", "size": "Size", "room": "Room name and number", "sfb": "NL-SfB code"}
DEFAULT_TEXT_MM = 250.0                      # text height in model millimetres: 2.5 mm on paper at 1:100


def fields_for(category: str) -> list[str]:
    from . import documentation as D
    out = ["name", "type"]
    if category in D.SCHEDULES:
        out.insert(0, "mark")
    if category in E.OPENINGS:
        out.append("size")
    if category == "room":
        out.append("room")
    return out + ["sfb"]


def default_field(category: str) -> str:
    return "room" if category == "room" else "mark" if category in E.OPENINGS or category in ("wall", "floor", "roof", "structural") else "name"


def _marks(project: Any, category: str, cache: dict[str, dict[str, str]]) -> dict[str, str]:
    if category not in cache:
        from . import documentation as D
        cache[category] = {r["id"]: r["no"] for r in D.schedule_data(project, category)["rows"]} if category in D.SCHEDULES else {}
    return cache[category]


def tag_text(project: Any, oid: str, field: str, cache: dict[str, dict[str, str]] | None = None) -> str:
    """The text a tag of ``oid`` shows for ``field``, from the element's current data."""
    cache = {} if cache is None else cache
    obj = project.model.get(oid)
    meta = obj["metadata"]
    cat = str(E.m(meta, "category", "generic"))
    if field not in fields_for(cat):
        raise BadRequest(f"A {cat} tag can show: {', '.join(fields_for(cat))}")
    if field == "mark":
        return _marks(project, cat, cache).get(oid) or obj["name"]
    if field == "name":
        return obj["name"] or cat.title()
    if field == "type":
        if "family_id" in obj:
            from . import families as FM
            return FM.family(project, obj["family_id"])["name"]
        return str(E.m(meta, "type", cat.title()))
    if field == "size":
        return f"{float(E.m(meta, 'width')) * 1000:.0f} × {float(E.m(meta, 'height')) * 1000:.0f}"
    if field == "sfb":
        return SF.read(meta, "sfb") or "-"
    from . import rooms as R
    return f"{obj['name']}\n{R.data(project, oid)['number']}".strip()                    # field == "room"


def add_tag(project: Any, view_id: str, p: Mapping[str, Any], to_mm: float) -> dict[str, Any]:
    """Tag an element in a drawing. ``point`` is in the drawing's unit; the text is the element's data, not typed by the person."""
    oid = text(p.get("element"), "element", 80)
    if not E.alive(project, oid):
        raise NotFound(f"Element {oid} does not exist")
    cat = str(E.category(project, oid))
    if cat == "generic" and p.get("field") is None:
        field = "type"
    else:
        field = p.get("field") or default_field(cat)
    q = p.get("point")
    if not isinstance(q, (list, tuple)) or len(q) != 2:
        raise BadRequest("point must be [x, y]")
    doc = project.drawing(view_id)
    label = tag_text(project, oid, field)
    height = DEFAULT_TEXT_MM / to_mm
    eid = doc.add_text((float(q[0]), float(q[1])), label, height, layer=LAYER, create_layer=True)
    doc.attach_semantics(eid, {"tag_of": oid, "tag_field": field, "detached": False}, namespace="spadra")
    return {"id": eid, "text": label, "field": field}


def set_field(project: Any, view_id: str, eid: str, field: str) -> None:
    doc = project.drawing(view_id)
    md = doc.get(eid).get("metadata", {})
    oid = md.get(KEY_OF)
    if not oid:
        raise BadRequest("That text is not a tag")
    if not E.alive(project, oid):
        raise BadRequest("The element this tag points at no longer exists")
    doc.set_text(eid, tag_text(project, oid, field))
    doc.attach_semantics(eid, {"tag_field": field}, namespace="spadra")


def _tag_ids(doc: Any) -> list[str]:
    return E.all_ids(doc, kind="text", layer=LAYER)                   # a drawing without a Tags layer has no tags: the query is simply empty


def sync_tags(project: Any) -> int:
    """Bring every tag up to date with its element; returns how many tags changed. Called inside the command that changed the model."""
    changed, cache = 0, {}
    live = {v["id"] for v in project.manifest["views"]}
    for vid, doc in project.drawings.items():
        if vid not in live:
            continue
        for eid in _tag_ids(doc):
            ent = doc.get(eid)
            md = ent.get("metadata", {})
            oid = md.get(KEY_OF)
            if not oid:
                continue
            if E.alive(project, oid):
                try:
                    new = tag_text(project, oid, md.get(KEY_FIELD, "name"), cache)
                except BadRequest:
                    new = ent["geometry"]["text"]
                if new != ent["geometry"]["text"] or md.get(KEY_DETACHED):
                    project.touch(f"drawing:{vid}")
                    if new != ent["geometry"]["text"]:
                        doc.set_text(eid, new)
                    if md.get(KEY_DETACHED):
                        doc.attach_semantics(eid, {"detached": False}, namespace="spadra")
                    changed += 1
            elif not md.get(KEY_DETACHED):
                project.touch(f"drawing:{vid}")
                doc.attach_semantics(eid, {"detached": True}, namespace="spadra")
                changed += 1
    return changed


def tags_for(project: Any, oid: str) -> list[dict[str, Any]]:
    out = []
    for v in project.manifest["views"]:
        doc = project.drawings.get(v["id"])
        if doc is None:
            continue
        for eid in doc.query_ids(kind="text", layer=LAYER, metadata={KEY_OF: oid}, limit=1000) if LAYER in {l["name"] for l in doc.layers()} else []:
            out.append({"view": v["id"], "view_name": v["name"], "entity": eid})
    return out


def all_tags(project: Any) -> list[dict[str, Any]]:
    """Every tag in the project, with whether its element still exists."""
    out = []
    for v in project.manifest["views"]:
        doc = project.drawings.get(v["id"])
        if doc is None:
            continue
        for eid in _tag_ids(doc):
            ent = doc.get(eid)
            md = ent.get("metadata", {})
            if md.get(KEY_OF):
                out.append({"view": v["id"], "view_name": v["name"], "entity": eid, "element": md[KEY_OF], "field": md.get(KEY_FIELD),
                            "text": ent["geometry"]["text"], "detached": not E.alive(project, md[KEY_OF])})
    return out
