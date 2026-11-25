"""A profile's parameters on the element made from it: shown and edited in Properties, and usable in SPADRA formulas.

The parameter list is kept with the element's profile link when it is made (``params_info``). Editing one rebuilds the element from its profile with that value
changed (the change is remembered in the link). Formulas see every parameter as its own name (``w``) when nothing else of the element has that name, and always
as ``profile_<name>``: lengths in metres, angles in radians, like every other quantity in SPADRA.
"""
from __future__ import annotations

import math
import re
from typing import Any, Mapping

from src.utils.spatial_formats.sgeo import parameters as SP
from src.utils.spatial_formats.common import units as U

from ..tools.errors import BadRequest
from . import elements as E

LINK_KEY = "profiler"
PREFIX = "profile:"
COLOUR = "colour"


def link_of(project: Any, oid: str) -> Mapping[str, Any] | None:
    link = E.m(E.meta_of(project, oid), LINK_KEY)
    return link if isinstance(link, Mapping) and link.get("tool") == "profiler" else None


def info(link: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The parameters recorded with the link; objects made before they were recorded fall back to the bare values (taken as lengths)."""
    rows = link.get("params_info")
    if isinstance(rows, list):
        return [dict(r) for r in rows if isinstance(r, Mapping) and r.get("name")]
    unit = str(link.get("source_unit") or "mm")
    return [{"name": k.split(".", 1)[-1], "type": "length", "unit": unit, "value": v, "derived": False, "read_only": False}
            for k, v in (link.get("params") or {}).items() if isinstance(v, (int, float)) and not isinstance(v, bool)]


def variables(project: Any, oid: str) -> dict[str, SP.Quantity]:
    """The numeric parameters as SGEO quantities, under ``profile_<name>``."""
    link = link_of(project, oid)
    if link is None:
        return {}
    out: dict[str, SP.Quantity] = {}
    for row in info(link):
        value, kind = row.get("value"), row.get("type")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not str(row["name"]).isidentifier():
            continue
        if kind == "length":
            out["profile_" + row["name"]] = SP.Quantity(float(value) * U.METERS_PER_UNIT.get(str(row.get("unit") or "mm"), 0.001), SP.PARAMETER_DIMENSIONS["length"])
        elif kind == "angle":
            out["profile_" + row["name"]] = SP.Quantity(math.radians(float(value)), SP.PARAMETER_DIMENSIONS["angle"])
        elif kind in ("number", "integer"):
            out["profile_" + row["name"]] = SP.Quantity(float(value), SP.PARAMETER_DIMENSIONS["number"])
    return out


def group(project: Any, oid: str) -> dict[str, Any] | None:
    """The "Profile parameters" block of the Properties panel, for an element made from a profile."""
    link = link_of(project, oid)
    if link is None:
        return None
    editable = link.get("mode") in ("embedded", "linked")
    rows: list[dict[str, Any]] = [{"label": "Profile", "type": "readonly", "value": "%s (%s)" % (link.get("sketch_name") or "profile", link.get("mode"))}]
    names = []
    if editable and link.get("kind") != "void" and not any(p["type"] == COLOUR for p in info(link)):
        rows.append({"key": PREFIX + COLOUR, "label": COLOUR, "type": "color", "value": "", "text": "Colour of the whole part. Empty: the colours of the profile's own lines"})
    for p in info(link):
        if p["type"] == COLOUR:
            rows.append({"key": PREFIX + COLOUR, "label": COLOUR, "type": "color", "value": p["value"], "text": "Colour of the part (a profile parameter)"})
            continue
        names.append(p["name"])
        unit = p.get("unit") or ""
        if p["type"] == "boolean":
            rows.append({"label": p["name"], "type": "readonly", "value": "yes" if p["value"] else "no"})
        elif p.get("derived") or p.get("read_only") or not editable:
            rows.append({"label": p["name"], "type": "readonly", "value": "%s %s%s" % (round(float(p["value"]), 6), unit, " (set by a formula in the profile)" if p.get("derived") else "")})
        else:
            rows.append({"key": PREFIX + p["name"], "label": p["name"], "type": "number", "unit": unit, "value": float(p["value"])})
    if names:
        rows.append({"label": "Use in formulas", "type": "readonly", "value": ", ".join("profile_" + n for n in names)})
    if not editable:
        rows.append({"label": "Editing", "type": "readonly", "value": "Static: upload again as Embedded or Linked to change these here"})
    return {"title": "Profile parameters", "rows": rows}


def set_value(project: Any, oid: str, key: str, value: Any) -> None:
    from .profiler import transfer as T
    name = key[len(PREFIX):]
    if link_of(project, oid) is None:
        raise BadRequest("This element was not made from a profile")
    if name == COLOUR:
        T.resync_element(project, oid, options={"part_color": E.color(value) or ""})
        return
    if isinstance(value, str):
        v = value.strip()
        if not re.fullmatch(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?", v):
            raise BadRequest("A profile parameter takes a number. To drive something else from it, write a formula there, for example =profile_%s" % name)
        value = float(v)
    T.resync_element(project, oid, overrides={name: float(value)})
