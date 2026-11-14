"""BIM data on elements: IFC property sets, IFC quantity sets and bSDD references.

* **Property sets** come from ``data/ifc_psets.json`` (buildingSMART definitions, each with its source page and edition; see
  ``data/build_ifc_psets.py``). Values are stored in the element's SGEO metadata (``spadra:psets``), or for a whole type of element in the project
  (``bim.type_psets``); an occurrence value overrides its type's, as in IFC. Only properties the official set defines can be given in an official set:
  anything else is a *custom* property (``relations.py``) and is shown apart, never as buildingSMART data.
* **Quantity sets** are *calculated* from the element's real geometry each time they are asked for (so they cannot go stale), and may be *imported*
  (from another source, with its name) or *overridden* by a person; those two are stored (``spadra:qto``) next to the calculated value they replace,
  and the difference is reported. A quantity that cannot honestly be derived is left out with the reason, never estimated.
* **bSDD** references are looked up through a fixed-origin client with a local cache and an explicit offline mode, stored with provenance (dictionary,
  version, status, when it was retrieved), one class per dictionary per element. A suggestion is never a reference until a person confirms it.

Nothing here writes IFC: the SPADRA IFC adapter reads IFC only (see ``interop.availability``). ``exchange`` gives the data in an IFC-shaped JSON.
"""
from __future__ import annotations

import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..tools.errors import BadRequest, DependencyMissing, NotFound
from ..tools.validation import text
from . import elements as E

DATA = Path(__file__).with_name("data") / "ifc_psets.json"
SCHEMA = "spadra.ifc_psets.v1"
PSET_KEY, QTO_KEY, BSDD_KEY = "spadra:psets", "spadra:qto", "spadra:bsdd"
ENTITY = {"curtainwall": "IfcCurtainWall", "wall": "IfcWall", "floor": "IfcSlab", "roof": "IfcRoof", "door": "IfcDoor", "window": "IfcWindow", "room": "IfcSpace", "stair": "IfcStair",
          "generic": "IfcBuildingElementProxy"}
MAX_TEXT = 255
QTO_TOLERANCE = 1e-4                       # relative difference between an imported/overridden quantity and the calculated one that is reported


# ---------------------------------------------------------------------------------------------------------------------- catalogue
@lru_cache(maxsize=1)
def catalog() -> dict[str, Any]:
    try:
        raw = json.loads(DATA.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DependencyMissing(f"The IFC property-set catalogue could not be read: {exc}") from exc
    if raw.get("schema") != SCHEMA:
        raise DependencyMissing("The IFC property-set catalogue has an unknown schema")
    return raw


def info() -> dict[str, Any]:
    c = catalog()
    return {"schema": c["schema"], "origin": c["origin"], "note": c["note"],
            "sets": [{"name": n, "kind": s["kind"], "entities": s["entities"], "edition": s["edition"], "source": s["source"],
                      "count": len(s.get("properties") or s.get("quantities") or {})} for n, s in sorted(c["sets"].items())]}


def definition(name: str) -> dict[str, Any]:
    s = catalog()["sets"].get(name)
    if s is None:
        raise BadRequest(f"'{name}' is not a buildingSMART set in SPADRA's catalogue", hint="Use a Pset_ or Qto_ name from the list; other properties are custom properties")
    return s


def entity_of(meta: Mapping[str, Any]) -> str | None:
    """The IFC occurrence class SPADRA gives an element: an IFC type the person chose wins (IfcWallType -> IfcWall), otherwise its category's class."""
    chosen = str(meta.get("spadra:ifc_type") or "")
    if chosen.endswith("Type") and len(chosen) > 7:
        return chosen[:-4]
    cat = str(E.m(meta, "category", "generic"))
    if cat == "structural":
        return "IfcColumn" if E.is_column(meta) else "IfcBeam"
    return ENTITY.get(cat)


def sets_for(meta: Mapping[str, Any], kind: str) -> list[str]:
    ent = entity_of(meta)
    return sorted(n for n, s in catalog()["sets"].items() if s["kind"] == kind and ent in s["entities"])


def search(q: str = "", *, entity: str | None = None, limit: int = 40) -> list[dict[str, Any]]:
    """Find property or quantity definitions by (part of) a set name, property name or description."""
    term = q.strip().casefold()
    out = []
    for sname, s in sorted(catalog()["sets"].items()):
        if entity and entity not in s["entities"]:
            continue
        for pname, d in (s.get("properties") or s.get("quantities") or {}).items():
            hay = f"{sname} {pname} {d.get('description', '')}".casefold()
            if term in hay:
                out.append({"set": sname, "name": pname, "kind": s["kind"], "type": d["ifc_type"], "unit": d.get("unit", ""), "edition": s["edition"], "source": s["source"],
                            "entities": s["entities"]})
    out.sort(key=lambda r: (not r["name"].casefold().startswith(term), r["set"], r["name"]))
    return out[:max(1, min(int(limit), 200))]


# ---------------------------------------------------------------------------------------------------------------------- values
def coerce(set_name: str, prop: str, value: Any) -> Any:
    """The value as the property's data type holds it, or BadRequest saying what is wrong. ``None`` (or empty text) clears."""
    s = definition(set_name)
    if s["kind"] != "pset":
        raise BadRequest(f"{set_name} is a quantity set: its values are quantities, not properties")
    d = (s["properties"]).get(prop)
    if d is None:
        raise BadRequest(f"{set_name} has no property '{prop}'", hint="A property the set does not define is a custom property, not part of the official set")
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    kind = d["kind"]
    if kind == "text":
        v = str(value).strip()
        if len(v) > MAX_TEXT:
            raise BadRequest(f"{prop} is limited to {MAX_TEXT} characters")
        return v
    if kind == "bool":
        if isinstance(value, bool):
            return value
        v = str(value).strip().lower()
        if v in ("true", "yes", "1"):
            return True
        if v in ("false", "no", "0"):
            return False
        raise BadRequest(f"{prop} is yes/no (IfcBoolean)")
    if kind == "enum":
        hit = next((e for e in d["enum"] if e.casefold() == str(value).strip().casefold()), None)
        if hit is None:
            raise BadRequest(f"{prop} must be one of: {', '.join(d['enum'])}")
        return hit
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise BadRequest(f"{prop} is a number ({d['ifc_type']}{', ' + d['unit'] if d.get('unit') else ''})") from None
    if not math.isfinite(v):
        raise BadRequest(f"{prop} must be a finite number")
    if d.get("rule") == "positive" and v <= 0:
        raise BadRequest(f"{prop} must be greater than zero ({d['ifc_type']})")
    if d.get("rule") == "nonneg" and v < 0:
        raise BadRequest(f"{prop} cannot be negative ({d['ifc_type']})")
    return v


def _bim(project: Any, create: bool = False) -> dict[str, Any]:
    if create:
        return project.manifest.setdefault("bim", {})
    return project.manifest.get("bim") or {}


def type_key(meta: Mapping[str, Any]) -> str:
    return f"{E.m(meta, 'category', 'generic')}:{E.m(meta, 'type', '')}"


def type_members(project: Any, key: str) -> list[str]:
    return [i for i in E.element_ids(project) if type_key(E.meta_of(project, i)) == key]


def effective(project: Any, oid: str) -> dict[str, dict[str, dict[str, Any]]]:
    """Every property value that applies to an element: {set: {property: {value, level}}}, the occurrence's own over its type's."""
    meta = E.meta_of(project, oid)
    own = meta.get(PSET_KEY) or {}
    typed = (_bim(project).get("type_psets") or {}).get(type_key(meta), {})
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for sname in set(own) | set(typed):
        for src, level in ((typed, "type"), (own, "occurrence")):
            for prop, v in (src.get(sname) or {}).items():
                out.setdefault(sname, {})[prop] = {"value": v, "level": level}
    return out


def set_value(project: Any, oid: str, set_name: str, prop: str, value: Any, level: str = "occurrence") -> None:
    """Set (or, with an empty value, clear) one official property of an element or of its type. Runs inside a command."""
    meta = E.meta_of(project, oid)
    s = definition(set_name)
    if entity_of(meta) not in s["entities"]:
        raise BadRequest(f"{set_name} applies to {', '.join(s['entities'])}, not to this element ({entity_of(meta) or 'no IFC class'})")
    v = coerce(set_name, prop, value)
    if level == "type":
        store = _bim(project, True).setdefault("type_psets", {})
        bucket = store.setdefault(type_key(meta), {}).setdefault(set_name, {})
        if v is None:
            bucket.pop(prop, None)
        else:
            bucket[prop] = v
        for sn in [k for k, b in store[type_key(meta)].items() if not b]:
            del store[type_key(meta)][sn]
        if not store[type_key(meta)]:
            del store[type_key(meta)]
        return
    if level != "occurrence":
        raise BadRequest("level must be occurrence or type")
    own = {k: dict(b) for k, b in (meta.get(PSET_KEY) or {}).items()}
    if v is None:
        own.get(set_name, {}).pop(prop, None)
        if not own.get(set_name):
            own.pop(set_name, None)
    else:
        own.setdefault(set_name, {})[prop] = v
    new = {k: x for k, x in meta.items() if k != PSET_KEY}
    if own:
        new[PSET_KEY] = own
    project.model.attach_semantics(oid, new, replace=True)


def set_property(project: Any, oid: str, key: str, value: Any) -> None:
    """The Properties panel's keys: ``pset:<Set>:<Prop>``, ``psett:<Set>:<Prop>`` (the element's type), ``qto:<Set>:<Quantity>``."""
    parts = key.split(":")
    if len(parts) != 3:
        raise BadRequest(f"'{key}' is not a BIM property key")
    head, sname, prop = parts
    if head == "qto":
        set_quantity(project, oid, sname, prop, value, "overridden" if value not in (None, "") else None)
    elif head in ("pset", "psett"):
        set_value(project, oid, sname, prop, value, "type" if head == "psett" else "occurrence")
    else:
        raise BadRequest(f"'{key}' is not a BIM property key")


# ---------------------------------------------------------------------------------------------------------------------- quantities
def _polygon(points: Sequence[Sequence[float]]) -> tuple[float, float]:
    area2 = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, list(points[1:]) + [points[0]]))
    perim = sum(math.dist(a, b) for a, b in zip(points, list(points[1:]) + [points[0]]))
    return abs(area2) / 2.0, perim


def _rect_sides(points: Sequence[Sequence[float]]) -> tuple[float, float] | None:
    if len(points) != 4:
        return None
    sides = [math.dist(a, b) for a, b in zip(points, list(points[1:]) + [points[0]])]
    d02, d13 = math.dist(points[0], points[2]), math.dist(points[1], points[3])
    if abs(sides[0] - sides[2]) > 1e-6 or abs(sides[1] - sides[3]) > 1e-6 or abs(d02 - d13) > 1e-6:
        return None
    return sides[0], sides[1]


def _profile_area(profile: Sequence[Sequence[float]], length: float) -> float:
    pts = sorted((float(s), float(h)) for s, h in profile)
    return sum((b[0] - a[0]) * (a[1] + b[1]) / 2 for a, b in zip(pts, pts[1:])) if len(pts) > 1 else 0.0


def calculated(project: Any, ids: Sequence[str] | None = None) -> dict[str, dict[str, Any]]:
    """Quantities of the elements' real geometry, by Qto set: {id: {"set": name, "values": {quantity: {value, unit, basis}}, "omitted": {quantity: reason}}}.
    Elements with no Qto set in the catalogue are not in the result."""
    from . import quantities as Q
    from . import rooms as R
    wanted = [i for i in (ids if ids is not None else E.element_ids(project)) if E.alive(project, i)]
    measurable = [i for i in wanted if E.category(project, i) not in ("room", "void", "grid") and project.model.get(i).get("geometry") is not None
                  and sets_for(E.meta_of(project, i), "qto")]
    rows: dict[str, Any] = {}
    if measurable:
        rows = {r["id"]: r for r in Q.take_off(project, measurable)["rows"]}
    out: dict[str, dict[str, Any]] = {}
    for oid in wanted:
        meta = E.meta_of(project, oid)
        ent = entity_of(meta)
        names = [n for n in sets_for(meta, "qto")]
        if not names:
            continue
        name = names[0]
        vals: dict[str, dict[str, Any]] = {}
        why: dict[str, str] = {}
        row = rows.get(oid)

        def put(q: str, v: float | None, basis: str) -> None:
            if v is None or not math.isfinite(v):
                why[q] = basis
            else:
                d = catalog()["sets"][name]["quantities"][q]
                vals[q] = {"value": v, "unit": d["unit"], "basis": basis}
        net_vol = row["volume_m3"] if row else None
        mass = row.get("mass_kg") if row else None
        weights = lambda gross, net: [put("GrossWeight", gross * mass / net if mass and net else None, "mass of the net volume scaled to the gross volume; needs a material density"),    # noqa: E731
                                      put("NetWeight", mass, "volume x density of its material")]
        if ent == "IfcWall":
            f = E.wall_frame(project, oid)
            length, t = f["length"], f["thickness"]
            op_side = op_vol = 0.0
            for hid in E.hosted(project, oid):
                x0, x1, z0, z1 = E.opening_span(E.meta_of(project, hid))
                w = max(0.0, min(x1, length) - max(x0, 0.0))
                op_side += w * max(0.0, z1 - z0)
            op_vol = op_side * t
            put("Length", length, "distance between the wall's end points, along its centre line")
            put("Width", t, "wall thickness (all layers)")
            if f["top"]:
                why["Height"] = "the top is sloped (attached to a roof): there is no single height"
                gross_side = _profile_area(f["top"], length)
            else:
                put("Height", f["height"], "wall height")
                gross_side = length * f["height"]
            put("GrossFootPrintArea", length * t, "length x thickness; end joints are not subtracted")
            why["NetFootPrintArea"] = "not derived: the footprint under door openings is not modelled separately"
            put("GrossSideArea", gross_side, "elevation area of the centre plane, openings ignored")
            put("NetSideArea", max(0.0, gross_side - op_side), f"gross side area minus {op_side:.3f} m² of hosted door and window openings")
            put("NetVolume", net_vol, "volume of the solid as SGEO measures it, openings and joints included")
            put("GrossVolume", net_vol + op_vol if net_vol is not None else None, "net volume plus the volume of the hosted openings; cut-outs by voids are not added back")
            weights(net_vol + op_vol if net_vol is not None else None, net_vol)
        elif ent == "IfcSlab":
            poly = [tuple(map(float, p)) for p in E.m(meta, "boundary")]
            area, perim = _polygon(poly)
            t = float(E.m(meta, "thickness"))
            rect = _rect_sides(poly)
            put("Width", t, "slab thickness (all layers)")
            if rect:
                put("Length", max(rect), "longer side of the rectangular outline")
                put("Depth", min(rect), "shorter side of the rectangular outline")
            else:
                why["Length"] = why["Depth"] = "the slab is not a rectangle"
            put("Perimeter", perim, "perimeter of the outline")
            put("GrossArea", area, "area of the outline")
            put("NetArea", net_vol / t if net_vol is not None and t > 0 else None, "net volume / thickness (prismatic slab, so cut-outs are accounted)")
            put("NetVolume", net_vol, "volume of the solid as SGEO measures it")
            put("GrossVolume", area * t, "outline area x thickness")
            weights(area * t, net_vol)
        elif ent == "IfcRoof":
            poly = [tuple(map(float, p)) for p in E.m(meta, "boundary")]
            footprint, _ = _polygon(poly)
            gross = 0.0
            for outline, (a, b, _c) in E.roof_facets(project, meta):
                gross += _polygon(outline)[0] * math.sqrt(1 + a * a + b * b)
            put("ProjectedArea", footprint, "area of the outline in plan")
            put("GrossArea", gross, "sum of the sloping facets' areas (slope measured from the facet planes)")
            put("NetArea", gross, "no roof openings are modelled, so equal to the gross area")
        elif ent in ("IfcDoor", "IfcWindow"):
            w, h = float(E.m(meta, "width")), float(E.m(meta, "height"))
            put("Width", w, "overall width of the opening's frame")
            put("Height", h, "overall height of the opening's frame")
            put("Perimeter", 2 * (w + h), "perimeter of the rectangular outline")
            put("Area", w * h, "width x height of the rectangular outline")
        elif ent in ("IfcBeam", "IfcColumn"):
            if ent == "IfcBeam":
                length = math.dist(E.m(meta, "start"), E.m(meta, "end"))
            else:
                length = float(E.m(meta, "top")) - float(E.m(meta, "base"))
            surface = row["surface_area_m2"] if row else None
            put("Length", length, "axis length")
            cross = net_vol / length if net_vol is not None and length > 0 else None
            put("CrossSectionArea", cross, "volume / length (prismatic member)")
            put("OuterSurfaceArea", surface - 2 * cross if surface is not None and cross is not None else None, "total surface minus both end faces")
            put("GrossSurfaceArea", surface, "surface area as SGEO measures it, end faces included")
            put("NetSurfaceArea", surface, "no cut-outs are modelled: equal to the gross surface")
            put("GrossVolume", net_vol, "volume of the solid; no cut-outs are modelled")
            put("NetVolume", net_vol, "volume of the solid as SGEO measures it")
            weights(net_vol, net_vol)
        elif ent == "IfcSpace":
            enclosure = R.enclosure_status(project, oid) if E.m(meta, "auto") else {"valid": True}
            if not enclosure["valid"]:
                # A stale outline may span two formerly independent rooms.
                # Emitting its area/volume as valid BIM quantities would be a
                # safety-critical misrepresentation, not just a UI problem.
                for q in ("Height", "GrossFloorArea", "GrossPerimeter", "GrossVolume"):
                    why[q] = str(enclosure["message"])
            else:
                d = R.data(project, oid)
                put("Height", d["height"], "height of the enclosing geometry")
                put("GrossFloorArea", d["area"], "area of the room outline")
                put("GrossPerimeter", d["perimeter"], "perimeter of the room outline")
                put("GrossVolume", d["volume"], f"room volume, {d['volume_basis']}")
        out[oid] = {"set": name, "values": vals, "omitted": why}
    return out


def effective_quantities(project: Any, oid: str, calc: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """One element's quantities, calculated and stored, with where each came from: {set, quantities: [{name, value, unit, mode, calculated, differs, basis}]}."""
    calc = calc if calc is not None else calculated(project, [oid])
    c = calc.get(oid)
    if c is None:
        return {"set": None, "quantities": [], "omitted": {}}
    stored = (E.meta_of(project, oid).get(QTO_KEY) or {}).get(c["set"], {})
    rows = []
    for qn in catalog()["sets"][c["set"]]["quantities"]:
        have, got = c["values"].get(qn), stored.get(qn)
        if got is None and have is None:
            continue
        calc_v = have["value"] if have else None
        value = got["value"] if got else calc_v
        differs = bool(got and calc_v is not None and abs(got["value"] - calc_v) > QTO_TOLERANCE * max(1.0, abs(calc_v)))
        rows.append({"name": qn, "value": value, "unit": catalog()["sets"][c["set"]]["quantities"][qn]["unit"], "mode": got["mode"] if got else "calculated",
                     "source": got.get("source", "") if got else "", "calculated": calc_v, "differs": differs, "basis": have["basis"] if have else "no calculation for this quantity"})
    return {"set": c["set"], "quantities": rows, "omitted": c["omitted"]}


def set_quantity(project: Any, oid: str, set_name: str, name: str, value: Any, mode: str | None, source: str = "") -> None:
    """Store an imported or overridden quantity, or (``mode`` None) go back to the calculated one."""
    meta = E.meta_of(project, oid)
    s = definition(set_name)
    if s["kind"] != "qto" or entity_of(meta) not in s["entities"]:
        raise BadRequest(f"{set_name} is not a quantity set for this element")
    if name not in s["quantities"]:
        raise BadRequest(f"{set_name} has no quantity '{name}'")
    store = {k: dict(b) for k, b in (meta.get(QTO_KEY) or {}).items()}
    if mode is None:
        store.get(set_name, {}).pop(name, None)
        if not store.get(set_name):
            store.pop(set_name, None)
    else:
        if mode not in ("imported", "overridden"):
            raise BadRequest("mode must be imported or overridden")
        try:
            v = float(value)
        except (TypeError, ValueError):
            raise BadRequest(f"{name} is a number ({s['quantities'][name]['unit']})") from None
        if not math.isfinite(v) or v < 0:
            raise BadRequest(f"{name} must be a finite number that is not negative")
        if mode == "imported" and not source.strip():
            raise BadRequest("An imported quantity needs the name of its source")
        calc = calculated(project, [oid]).get(oid, {"values": {}})["values"].get(name)
        store.setdefault(set_name, {})[name] = {"value": v, "mode": mode, "source": text(source, "source", 120, empty=True),
                                                "calculated_when_set": calc["value"] if calc else None}
    new = {k: x for k, x in meta.items() if k != QTO_KEY}
    if store:
        new[QTO_KEY] = store
    project.model.attach_semantics(oid, new, replace=True)


def schedule(project: Any, ids: Sequence[str] | None = None, set_name: str | None = None) -> dict[str, Any]:
    """A quantity schedule (one row per element) for the Qto sets: calculated values with imported/overridden ones marked."""
    calc = calculated(project, ids)
    rows = []
    for oid, c in calc.items():
        if set_name and c["set"] != set_name:
            continue
        q = effective_quantities(project, oid, calc)
        rows.append({"id": oid, "name": project.model.get(oid)["name"], "category": E.category(project, oid), "set": q["set"],
                     "quantities": {r["name"]: {"value": r["value"], "unit": r["unit"], "mode": r["mode"], "differs": r["differs"]} for r in q["quantities"]}})
    return {"revision": project.revision, "rows": rows, "note": "Calculated from the modelled geometry; imported and overridden values are marked and keep the calculated figure beside them."}


# ---------------------------------------------------------------------------------------------------------------------- Properties panel
def _row(set_name: str, prop: str, d: Mapping[str, Any], got: Mapping[str, Any] | None, head: str = "pset") -> dict[str, Any]:
    key = f"{head}:{set_name}:{prop}"
    value = got["value"] if got else None
    label = prop + (" · from type" if got and got["level"] == "type" and head == "pset" else "")
    if d["kind"] == "bool":
        return {"key": key, "label": label, "type": "choice", "value": "" if value is None else "true" if value else "false",
                "choices": [{"value": "", "label": "— Unassigned —"}, {"value": "true", "label": "Yes"}, {"value": "false", "label": "No"}]}
    if d["kind"] == "enum":
        return {"key": key, "label": label, "type": "choice", "value": value or "", "choices": [{"value": "", "label": "— Unassigned —"}] + [{"value": e, "label": e} for e in d["enum"]]}
    if d["kind"] == "real":
        return {"key": key, "label": label, "type": "number", "value": "" if value is None else value, **({"unit": d["unit"]} if d.get("unit") else {})}
    return {"key": key, "label": label, "type": "text", "value": value or ""}


def groups_for(project: Any, oid: str) -> list[dict[str, Any]]:
    """The Properties groups: one per applicable official property set, a collapsed one for the type's values, and the quantity set (read from geometry)."""
    meta = E.meta_of(project, oid)
    eff = effective(project, oid)
    cat = catalog()["sets"]
    out: list[dict[str, Any]] = []
    for sname in sets_for(meta, "pset"):
        s = cat[sname]
        rows = [_row(sname, p, d, eff.get(sname, {}).get(p)) for p, d in s["properties"].items()]
        out.append({"title": f"{sname} (buildingSMART, {s['edition'].split(' (')[0]})", "rows": rows, "collapsed": not eff.get(sname)})
    typed = (_bim(project).get("type_psets") or {}).get(type_key(meta), {})
    members = len(type_members(project, type_key(meta)))
    if sets_for(meta, "pset"):
        rows = []
        for sname in sets_for(meta, "pset"):
            rows += [dict(_row(sname, p, d, {"value": typed[sname][p], "level": "type"} if p in typed.get(sname, {}) else None, "psett"), label=f"{sname.removeprefix('Pset_')} · {p}")
                     for p, d in cat[sname]["properties"].items()]
        out.append({"title": f"Type-level values — {E.m(meta, 'type', 'type')} ({members} element{'s' if members != 1 else ''})", "rows": rows, "collapsed": True})
    q = effective_quantities(project, oid)
    if q["set"]:
        d = cat[q["set"]]
        rows = [{"key": f"qto:{q['set']}:{r['name']}", "label": f"{r['name']} · {r['mode']}" + (" · differs from geometry" if r["differs"] else ""), "type": "number",
                 "value": round(r["value"], 6), "unit": r["unit"]} for r in q["quantities"]]
        rows += [{"label": k, "type": "readonly", "value": f"not derived: {v}"} for k, v in sorted(q["omitted"].items())]
        out.append({"title": f"{q['set']} (calculated from geometry)", "rows": rows, "collapsed": True})
    return out


# ---------------------------------------------------------------------------------------------------------------------- exchange
def exchange(project: Any, ids: Sequence[str] | None = None) -> dict[str, Any]:
    """The BIM data of the elements in an IFC-shaped JSON (entity, property sets with data types, quantity sets, classifications, bSDD references)."""
    from .relations import effective_custom
    cat = catalog()["sets"]
    calc = calculated(project, ids)
    items = []
    for oid in (ids if ids is not None else E.element_ids(project)):
        if not E.alive(project, oid):
            continue
        meta = E.meta_of(project, oid)
        psets = {s: {p: {"value": v["value"], "type": cat[s]["properties"][p]["ifc_type"], "level": v["level"]} for p, v in props.items() if s in cat and p in cat[s]["properties"]}
                 for s, props in effective(project, oid).items()}
        q = effective_quantities(project, oid, calc)
        items.append({"id": oid, "name": project.model.get(oid)["name"], "ifc_entity": entity_of(meta), "predefined_type": meta.get("spadra:ifc_predefined_type") or None,
                      "property_sets": {k: v for k, v in psets.items() if v},
                      "quantity_sets": {q["set"]: {r["name"]: {"value": r["value"], "unit": r["unit"], "origin": r["mode"], "source": r["source"]} for r in q["quantities"]}} if q["set"] else {},
                      "classifications": {k: v for k, v in {"NL-SfB": str(meta.get("spadra:sfb") or "").strip("()") or None, "UniFormat": meta.get("spadra:assembly_code"),
                                                             "OmniClass Table 21": meta.get("spadra:omniclass_21"), "OmniClass Table 23": meta.get("spadra:omniclass_23")}.items() if v},
                      "classification_edition": meta.get("spadra:classification_edition"),
                      "custom_properties": {k[len("custom:"):]: v for k, v in meta.items() if k.startswith("custom:")},
                      "effective_custom_properties": effective_custom(project, oid),
                      "custom_property_types": meta.get("spadra:custom_types") or {},
                      "parametric_formulas": meta.get("spadra:formulas") or {},
                      "conditional_material": meta.get("spadra:material_rule") or None,
                      "bsdd": meta.get(BSDD_KEY) or []})
    return {"schema": "spadra.bim_exchange.v1", "revision": project.revision, "catalogue": {"origin": catalog()["origin"], "sets": {k: cat[k]["edition"] for k in cat}},
            "note": "Not an IFC file: SPADRA's IFC adapter has no writer. Plane angles are in degrees, lengths in metres.", "elements": items}


# ---------------------------------------------------------------------------------------------------------------------- bSDD
BSDD_ORIGIN = "https://api.bsdd.buildingsmart.org"
# Paths of the public bSDD read endpoints, kept together so they can be corrected in one place. They follow the published API (v1) but have NOT been
# exercised against the live service from where SPADRA was built (no network): the client's behaviour is tested with a stand-in transport only.
BSDD_PATHS = {"dictionaries": "/api/Dictionary/v1", "search": "/api/TextSearch/v1", "class": "/api/Class/v1"}
CACHE_FILE = Path(__file__).with_name("data") / "bsdd_cache.local.json"
CACHE_TTL = 7 * 24 * 3600
CACHE_MAX = 300
MAX_RESPONSE = 2_000_000
Transport = Callable[[str], bytes]
_URI = re.compile(r"^https://identifier\.buildingsmart\.org/uri/([^/]+)/([^/]+)/([^/]+)/class/([^/?#]+)$|^https://[^\s]+$")


def _fetch(url: str) -> bytes:
    if not url.startswith(BSDD_ORIGIN + "/"):
        raise BadRequest("bSDD requests go to the buildingSMART service only")
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "SPADRA"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:                       # nosec: fixed https origin checked above
            data = resp.read(MAX_RESPONSE + 1)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise ConnectionError(str(exc)) from exc
    if len(data) > MAX_RESPONSE:
        raise ConnectionError("the bSDD answer is too large")
    return data


class Bsdd:
    """bSDD read client: cache first when asked offline, network first otherwise, a stale cache entry (marked) when the network fails."""

    def __init__(self, transport: Transport | None = None, cache_file: Path | None = None, now: Callable[[], float] = time.time) -> None:
        self.transport, self.cache_file, self.now = transport or _fetch, cache_file or CACHE_FILE, now

    def _load(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.cache_file.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, cache: dict[str, Any]) -> None:
        if len(cache) > CACHE_MAX:
            for k in sorted(cache, key=lambda x: cache[x]["at"])[:len(cache) - CACHE_MAX]:
                del cache[k]
        try:
            tmp = self.cache_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(cache), encoding="utf-8")
            tmp.replace(self.cache_file)
        except OSError:
            pass                                                                    # the cache is a convenience; failing to write it never fails a lookup

    def get(self, path: str, params: Mapping[str, Any], *, offline: bool = False) -> dict[str, Any]:
        query = urllib.parse.urlencode({k: v for k, v in sorted(params.items()) if v not in (None, "")})
        url = f"{BSDD_ORIGIN}{path}" + (f"?{query}" if query else "")
        cache = self._load()
        hit = cache.get(url)
        fresh = hit is not None and self.now() - hit["at"] < CACHE_TTL
        if hit is not None and (offline or fresh):
            return {"data": hit["data"], "source": "cache", "retrieved": hit["when"], "stale": not fresh, "url": url}
        if offline:
            raise BadRequest("bSDD is in offline mode and this is not in the cache", hint="Go online once to look it up; the answer is then kept")
        try:
            data = json.loads(self.transport(url).decode("utf-8"))
        except (ConnectionError, OSError, TimeoutError, ValueError, UnicodeDecodeError) as exc:
            if hit is not None:
                return {"data": hit["data"], "source": "cache", "retrieved": hit["when"], "stale": True, "url": url, "warning": f"bSDD could not be reached ({exc}); showing the cached answer"}
            raise DependencyMissing(f"bSDD could not be reached ({exc}), and nothing is cached for this", hint="Check the connection, or use offline mode for cached lookups") from exc
        when = datetime.fromtimestamp(self.now(), timezone.utc).isoformat(timespec="seconds")
        cache[url] = {"at": self.now(), "when": when, "data": data}
        self._save(cache)
        return {"data": data, "source": "network", "retrieved": when, "stale": False, "url": url}

    def dictionaries(self, *, offline: bool = False) -> dict[str, Any]:
        r = self.get(BSDD_PATHS["dictionaries"], {"Limit": 200}, offline=offline)
        raw = r["data"].get("dictionaries", []) if isinstance(r["data"], dict) else r["data"]
        return {**{k: v for k, v in r.items() if k != "data"},
                "results": [{"uri": d.get("uri", ""), "name": d.get("name", ""), "code": d.get("code", ""), "version": d.get("version", ""), "status": d.get("status", ""),
                             "organization": d.get("organizationNameOwner", "")} for d in raw if isinstance(d, dict)]}

    def search(self, q: str, *, dictionary: str = "", offline: bool = False, limit: int = 30) -> dict[str, Any]:
        q = text(q, "q", 100)
        r = self.get(BSDD_PATHS["search"], {"SearchText": q, "DictionaryUri": dictionary, "Limit": max(1, min(int(limit), 100))}, offline=offline)
        raw = r["data"].get("classes", []) if isinstance(r["data"], dict) else r["data"]
        return {**{k: v for k, v in r.items() if k != "data"}, "results": [_class_row(c) for c in raw if isinstance(c, dict)][:limit]}

    def class_detail(self, uri: str, *, offline: bool = False) -> dict[str, Any]:
        if not isinstance(uri, str) or not uri.startswith("https://") or len(uri) > 400:
            raise BadRequest("A bSDD class is identified by its https URI")
        r = self.get(BSDD_PATHS["class"], {"Uri": uri}, offline=offline)
        if not isinstance(r["data"], dict) or not (r["data"].get("code") or r["data"].get("referenceCode")):
            raise NotFound("bSDD has no class with that URI")
        return {**{k: v for k, v in r.items() if k != "data"}, "class": _class_row(r["data"], uri)}


def _class_row(c: Mapping[str, Any], uri: str = "") -> dict[str, Any]:
    u = str(c.get("uri") or uri)
    m = re.match(r"^https://[^/]+/uri/([^/]+)/([^/]+)/([^/]+)/class/([^/?#]+)$", u)
    return {"uri": u, "code": str(c.get("code") or c.get("referenceCode") or (m.group(4) if m else "")), "name": str(c.get("name", "")),
            "dictionary_uri": str(c.get("dictionaryUri") or (f"https://identifier.buildingsmart.org/uri/{m.group(1)}/{m.group(2)}/{m.group(3)}" if m else "")),
            "dictionary_name": str(c.get("dictionaryName", "")), "version": str(c.get("dictionaryVersion") or c.get("version") or (m.group(3) if m else "")),
            "status": str(c.get("status", "")), "definition": str(c.get("definition") or c.get("description") or "")[:500]}


CLIENT = Bsdd()


def _dictionary_key(dictionary_uri: str, version: str) -> str:
    return dictionary_uri.removesuffix("/" + version) if version and dictionary_uri.endswith("/" + version) else dictionary_uri


def references(project: Any, oid: str) -> list[dict[str, Any]]:
    return list(E.meta_of(project, oid).get(BSDD_KEY) or [])


def assign(project: Any, oid: str, uri: str, *, origin: str = "manual", confirm: bool = False, offline: bool = False, client: Bsdd | None = None) -> dict[str, Any]:
    """Attach a bSDD class to an element after reading it from bSDD (or the cache). One class per dictionary; an inactive class or a suggestion needs ``confirm``."""
    if origin not in ("manual", "suggested"):
        raise BadRequest("origin must be manual or suggested")
    if origin == "suggested" and not confirm:
        raise BadRequest("A suggestion is not a classification until a person confirms it", hint="Review the class and confirm it")
    got = (client or CLIENT).class_detail(uri, offline=offline)
    c = got["class"]
    if c["status"].lower() == "inactive" and not confirm:
        raise BadRequest(f"{c['name'] or c['code']} is inactive in bSDD", hint="Confirm to use it anyway")
    key = _dictionary_key(c["dictionary_uri"], c["version"])
    refs = references(project, oid)
    clash = next((r for r in refs if _dictionary_key(r["dictionary_uri"], r["version"]) == key), None)
    if clash is not None:
        if clash["uri"] == c["uri"]:
            return clash
        raise BadRequest(f"This element already has {clash['code']} from {clash['dictionary_name'] or key}", hint="Remove it first: an element holds one class per dictionary")
    ref = {**c, "retrieved": got["retrieved"], "retrieved_from": got["source"] + (" (stale)" if got.get("stale") else ""), "origin": origin, "service": BSDD_ORIGIN}
    meta = E.meta_of(project, oid)
    project.model.attach_semantics(oid, {**{k: v for k, v in meta.items()}, BSDD_KEY: refs + [ref]}, replace=True)
    return ref


def unassign(project: Any, oid: str, uri: str) -> None:
    meta = E.meta_of(project, oid)
    refs = references(project, oid)
    left = [r for r in refs if r["uri"] != uri]
    if len(left) == len(refs):
        raise NotFound("That bSDD class is not on this element")
    new = {k: v for k, v in meta.items() if k != BSDD_KEY}
    if left:
        new[BSDD_KEY] = left
    project.model.attach_semantics(oid, new, replace=True)


def suggest(project: Any, oid: str, *, dictionary: str = "", offline: bool = False, client: Bsdd | None = None) -> dict[str, Any]:
    """Candidate bSDD classes found by text search on the element's name and type. These are suggestions only: nothing is stored."""
    meta = E.meta_of(project, oid)
    term = str(E.m(meta, "type", "") or project.model.get(oid)["name"] or E.category(project, oid))
    found = (client or CLIENT).search(term, dictionary=dictionary, offline=offline)
    return {**{k: v for k, v in found.items() if k != "results"}, "authoritative": False, "query": term,
            "notice": "Suggestions from a text search, not verified matches. Confirm one to attach it.",
            "results": [{**r, "origin": "suggested"} for r in found["results"]]}


def issues(project: Any) -> list[dict[str, str]]:
    """Findings about stored bSDD references: two classes of one dictionary on an element (older projects, imports), or a reference with no version."""
    out = []
    for oid in E.element_ids(project):
        refs = references(project, oid)
        seen: dict[str, str] = {}
        for r in refs:
            k = _dictionary_key(r.get("dictionary_uri", ""), r.get("version", ""))
            if k in seen and seen[k] != r.get("uri"):
                out.append(E.issue("warning", oid, f"{project.model.get(oid)['name']} has two bSDD classes from the same dictionary ({r.get('dictionary_name') or k})", "bsdd"))
            seen[k] = r.get("uri", "")
            if not r.get("version"):
                out.append(E.issue("info", oid, f"The bSDD reference {r.get('code')} on {project.model.get(oid)['name']} names no dictionary version", "bsdd"))
    return out
