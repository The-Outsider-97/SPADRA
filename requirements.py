"""Information requirements checked against the model: buildingSMART IDS files and the BIM basis ILS (NL).

These are *information* checks (is the data there, in the right form), kept apart from the model's own consistency and clash checks:
their findings carry the categories ``ids`` and ``ils`` and say which requirement they come from. They are not code-compliance checks.

* **IDS** (Information Delivery Specification): a ``.ids`` file is stored in the project, evaluated element by element, and the result is kept as
  evidence (which element, which requirement, what was expected, what was found). Requirements SPADRA cannot evaluate (an attribute it does not
  have, a relation it does not model) are reported as *not evaluable*, never as passed or failed. Revalidation is a command; a result made for an
  older revision of the model says it is out of date.
* **BIM basis ILS**: a small set of rules, each transcribed from one chapter of the published ILS and carrying that chapter's link and its own wording
  of how binding it is. The ILS is off until a project turns it on; the edition is a label the project sets; a project can disable or re-grade a rule
  and add rules of its own, which must cite where they come from.
"""
from __future__ import annotations

import hashlib
import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from ..tools.errors import BadRequest, NotFound
from ..tools.validation import text
from . import bimdata as BD
from . import elements as E

MAX_IDS_BYTES = 2_000_000
MAX_IDS_DOCS = 8
MAX_EVIDENCE = 400                                  # failing elements kept per specification
MAX_REPORTED = 25                                   # findings per rule or specification put into the project check
SEVERITIES = ("error", "warning", "info")
BUILT_SUPERCLASSES = {"IFCELEMENT", "IFCBUILTELEMENT", "IFCBUILDINGELEMENT"}
GENERAL_SUPERCLASSES = {"IFCPRODUCT", "IFCOBJECT", "IFCOBJECTDEFINITION", "IFCROOT"}
SYSTEMS = {"sfb": ("nl-sfb", "nlsfb", "nl/sfb"), "omniclass": ("omniclass",), "uniformat": ("uniformat",)}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _kids(node: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in node if _local(c.tag) == name]


def _kid(node: ET.Element, name: str) -> ET.Element | None:
    return next(iter(_kids(node, name)), None)


# ---------------------------------------------------------------------------------------------------------------------- IDS parsing
def _value(node: ET.Element | None) -> dict[str, Any] | None:
    if node is None:
        return None
    simple = _kid(node, "simpleValue")
    if simple is not None:
        return {"simple": (simple.text or "").strip()}
    res = _kid(node, "restriction")
    if res is None:
        raise BadRequest("An IDS value is a simpleValue or a restriction")
    out: dict[str, Any] = {"restriction": {"base": res.get("base", ""), "enumeration": [], "pattern": []}}
    for c in res:
        n, v = _local(c.tag), c.get("value")
        if v is None:
            continue
        if n == "enumeration":
            out["restriction"]["enumeration"].append(v)
        elif n == "pattern":
            out["restriction"]["pattern"].append(v)
        elif n in ("minInclusive", "maxInclusive", "minExclusive", "maxExclusive", "length", "minLength", "maxLength"):
            out["restriction"][n] = v
    return out


def parse_ids(raw: bytes) -> dict[str, Any]:
    """An IDS document as plain data, or BadRequest saying what is wrong. DTDs and entities are refused before parsing."""
    if len(raw) > MAX_IDS_BYTES:
        raise BadRequest(f"An IDS file can be at most {MAX_IDS_BYTES // 1_000_000} MB")
    head = raw[:4096].lower()
    if b"<!doctype" in raw.lower() or b"<!entity" in raw.lower():
        raise BadRequest("An IDS file with a DTD or entity declaration is refused")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise BadRequest(f"This is not well-formed XML: {exc}") from exc
    if _local(root.tag) != "ids":
        raise BadRequest("This is not an IDS file (the root element is not <ids>)", hint="IDS files are XML documents whose root is <ids>")
    info = _kid(root, "info")
    title = (_kid(info, "title").text or "").strip() if info is not None and _kid(info, "title") is not None else ""
    specs_node = _kid(root, "specifications")
    if specs_node is None or not _kids(specs_node, "specification"):
        raise BadRequest("The IDS file has no specifications")
    specs = []
    for i, sp in enumerate(_kids(specs_node, "specification"), 1):
        app = _kid(sp, "applicability")
        req = _kid(sp, "requirements")
        if app is None:
            raise BadRequest(f"Specification {i} has no applicability")
        specs.append({"index": i, "name": sp.get("name") or f"Specification {i}", "ifc_version": sp.get("ifcVersion", ""), "identifier": sp.get("identifier", ""),
                      "description": sp.get("description", ""), "instructions": sp.get("instructions", ""),
                      "min": app.get("minOccurs", "0"), "max": app.get("maxOccurs", "unbounded"),
                      "applicability": [_facet(f, False) for f in app], "requirements": [_facet(f, True) for f in (req if req is not None else [])]})
    return {"title": title or "Untitled IDS", "specifications": specs}


def _facet(node: ET.Element, requirement: bool) -> dict[str, Any]:
    kind = _local(node.tag)
    f: dict[str, Any] = {"type": kind, "cardinality": node.get("cardinality", "required") if requirement else "required", "instructions": node.get("instructions", "")}
    if kind == "entity":
        f.update(name=_value(_kid(node, "name")), predefined=_value(_kid(node, "predefinedType")))
    elif kind == "attribute":
        f.update(name=_value(_kid(node, "name")), value=_value(_kid(node, "value")))
    elif kind == "property":
        f.update(pset=_value(_kid(node, "propertySet")), name=_value(_kid(node, "baseName")), value=_value(_kid(node, "value")), data_type=node.get("dataType", ""))
    elif kind == "classification":
        f.update(value=_value(_kid(node, "value")), system=_value(_kid(node, "system")))
    elif kind == "material":
        f.update(value=_value(_kid(node, "value")))
    elif kind == "partOf":
        ent = _kid(node, "entity")
        f.update(relation=node.get("relation", ""), entity=_facet(ent, False) if ent is not None else None)
    else:
        raise BadRequest(f"IDS facet '{kind}' is not part of IDS")
    return f


# ---------------------------------------------------------------------------------------------------------------------- matching
def _number(s: Any) -> float | None:
    try:
        v = float(s)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _as_text(v: Any) -> str:
    return ("TRUE" if v else "FALSE") if isinstance(v, bool) else (f"{v:g}" if isinstance(v, float) else str(v))


def matches(spec: Mapping[str, Any] | None, actual: Any, *, fold: bool = False) -> bool:
    """Does ``actual`` satisfy an IDS value (a simple value or a restriction)? ``None`` for the spec means 'anything'."""
    if spec is None:
        return True
    if actual is None:
        return False
    a = _as_text(actual)
    if "simple" in spec:
        want = spec["simple"]
        if fold:
            return a.casefold() == want.casefold()
        if a == want:
            return True
        x, y = _number(actual), _number(want)
        return x is not None and y is not None and math.isclose(x, y, rel_tol=1e-9, abs_tol=1e-9)
    r = spec["restriction"]
    if r["enumeration"] and not any(matches({"simple": e}, actual, fold=fold) for e in r["enumeration"]):
        return False
    if r["pattern"] and not any(re.fullmatch(p, a) for p in r["pattern"]):
        return False
    n = _number(actual)
    for key, test in (("minInclusive", lambda x, b: x >= b), ("maxInclusive", lambda x, b: x <= b), ("minExclusive", lambda x, b: x > b), ("maxExclusive", lambda x, b: x < b)):
        if key in r:
            b = _number(r[key])
            if n is None or b is None or not test(n, b):
                return False
    if "length" in r and len(a) != int(r["length"]):
        return False
    if "minLength" in r and len(a) < int(r["minLength"]):
        return False
    if "maxLength" in r and len(a) > int(r["maxLength"]):
        return False
    return True


def describe(spec: Mapping[str, Any] | None) -> str:
    if spec is None:
        return "any"
    if "simple" in spec:
        return spec["simple"]
    r = spec["restriction"]
    bits = [f"one of {r['enumeration']}"] if r["enumeration"] else []
    bits += [f"pattern {p}" for p in r["pattern"]]
    bits += [f"{k} {v}" for k, v in r.items() if k not in ("base", "enumeration", "pattern")]
    return ", ".join(bits) or "any"


# ---------------------------------------------------------------------------------------------------------------------- element context
class Context:
    """What the requirements can ask about one element, read from the model once."""

    def __init__(self, project: Any, oid: str, qto: Callable[[], Mapping[str, Any]]) -> None:
        meta = E.meta_of(project, oid)
        self.id, self.name = oid, project.model.get(oid)["name"] or oid
        self.category = E.category(project, oid)
        ent = BD.entity_of(meta)
        self.entity = (ent or "").upper()
        self.predefined = str(meta.get("spadra:ifc_predefined_type") or "").upper()
        self.object_type = str(E.m(meta, "type", "") or "")
        self.meta, self.project, self._qto = meta, project, qto
        self._psets: dict[str, dict[str, Any]] | None = None
        level = E.m(meta, "level")
        if self.category in E.OPENINGS and E.m(meta, "host") and E.alive(project, E.m(meta, "host")):
            level = E.m(E.meta_of(project, E.m(meta, "host")), "level")
        self.level = next((l["name"] for l in project.manifest["levels"] if l["id"] == level), None)
        host = E.m(meta, "host") if self.category in E.OPENINGS else None
        self.host_entity = (BD.entity_of(E.meta_of(project, host)) or "").upper() if host and E.alive(project, host) else None

    @property
    def psets(self) -> dict[str, dict[str, Any]]:
        if self._psets is None:
            sets: dict[str, dict[str, Any]] = {s: {p: v["value"] for p, v in props.items()} for s, props in BD.effective(self.project, self.id).items()}
            for k, v in self.meta.items():
                if k.startswith("custom:"):
                    sets.setdefault(custom_set_name(self.project), {})[k[len("custom:"):]] = v
            self._psets = sets
        return self._psets

    def quantities(self) -> dict[str, Any]:
        return self._qto().get(self.id, {})

    def classifications(self) -> list[tuple[str, str, str]]:
        """(system alias, system name, code) for every classification on the element."""
        out = []
        for field, alias, label in (("sfb", "sfb", "NL-SfB"), ("assembly_code", "uniformat", "UniFormat"), ("omniclass_21", "omniclass", "OmniClass Table 21"), ("omniclass_23", "omniclass", "OmniClass Table 23")):
            v = self.meta.get(f"spadra:{field}")
            if v:
                out.append((alias, label, str(v).strip("()") if alias == "sfb" else str(v)))        # NL-SfB's table writes (21.11); the ILS notation is 21.11
        for r in self.meta.get("spadra:bsdd") or []:
            out.append(("bsdd", r.get("dictionary_name") or r.get("dictionary_uri", ""), str(r.get("code", ""))))
        return out

    def materials(self) -> list[str]:
        layers = E.m(self.meta, "layers")
        names = [str(l.get("material")) for l in layers if l.get("material")] if layers else []
        single = E.m(self.meta, "material")
        return names or ([str(single)] if single else [])


def custom_set_name(project: Any) -> str:
    return str((BD._bim(project).get("ils") or {}).get("custom_set_name") or "SPADRA_Custom")


def _quantity_getter(project: Any) -> Callable[[], Mapping[str, Any]]:
    cache: dict[str, Any] = {}

    def get() -> Mapping[str, Any]:
        if not cache:
            calc = BD.calculated(project)
            for oid, c in calc.items():
                q = BD.effective_quantities(project, oid, calc)
                cache[oid] = {q["set"]: {r["name"]: r["value"] for r in q["quantities"]}} if q["set"] else {}
            cache["__loaded__"] = True
        return cache
    return get


# ---------------------------------------------------------------------------------------------------------------------- facets
PASS, FAIL, UNKNOWN = "pass", "fail", "not_evaluable"
ATTRIBUTES = {"name": lambda c: c.name, "objecttype": lambda c: c.object_type, "tag": lambda c: c.id, "predefinedtype": lambda c: c.predefined}
QTY_TYPES = {"length": "IFCLENGTHMEASURE", "area": "IFCAREAMEASURE", "volume": "IFCVOLUMEMEASURE", "weight": "IFCMASSMEASURE"}


def _entity_match(f: Mapping[str, Any], c: Context) -> bool:
    """The element's class matches by name, or the name is a superclass SPADRA knows the element belongs to (a space is a product, not a built element)."""
    if not c.entity:
        return False
    names = f["name"]
    ok = matches(names, c.entity, fold=True)
    if not ok and names is not None and "simple" in names:
        sup = names["simple"].upper()
        ok = sup in GENERAL_SUPERCLASSES or (sup in BUILT_SUPERCLASSES and c.entity != "IFCSPACE")
    if ok and f.get("predefined"):
        ok = matches(f["predefined"], c.predefined or None, fold=True)
    return ok


def evaluate(f: Mapping[str, Any], c: Context) -> tuple[str, str, str]:
    """One facet against one element: (status, expected, found). Status is pass, fail or not_evaluable."""
    t = f["type"]
    if t == "entity":
        want = describe(f["name"]) + (f" / {describe(f['predefined'])}" if f.get("predefined") else "")
        return (PASS if _entity_match(f, c) else FAIL), want, f"{c.entity or 'no IFC class'}" + (f" / {c.predefined}" if c.predefined else "")
    if t == "attribute":
        names = f["name"]
        keys = [k for k in ATTRIBUTES if matches(names, k, fold=True)] if names else []
        if not keys:
            return UNKNOWN, describe(names), "SPADRA keeps no such IFC attribute"
        vals = [ATTRIBUTES[k](c) for k in keys]
        ok = any(v and matches(f.get("value"), v) for v in vals)
        return (PASS if ok else FAIL), f"{describe(names)} = {describe(f.get('value'))}", ", ".join(str(v) for v in vals) or "empty"
    if t == "property":
        found = [(s, p, v) for s, props in c.psets.items() for p, v in props.items() if matches(f["pset"], s) and matches(f["name"], p)]
        qty = c.quantities()
        found += [(s, p, v) for s, props in qty.items() for p, v in props.items() if matches(f["pset"], s) and matches(f["name"], p)]
        want = f"{describe(f['pset'])}.{describe(f['name'])}" + (f" = {describe(f.get('value'))}" if f.get("value") else "")
        if not found:
            return (FAIL if f["cardinality"] == "required" else PASS), want, "property absent"
        if f["cardinality"] == "prohibited":
            return FAIL, "absent: " + want, ", ".join(f"{s}.{p}" for s, p, _ in found)
        good = [x for x in found if _typed(f, x) and (f.get("value") is None and x[2] not in (None, "") or f.get("value") is not None and matches(f["value"], x[2]))]
        return (PASS if good else FAIL), want, "; ".join(f"{s}.{p} = {_as_text(v)}" for s, p, v in found)
    if t == "classification":
        found = [(a, n, code) for a, n, code in c.classifications() if f.get("system") is None or matches(f["system"], n) or any(matches(f["system"], al) or n.casefold().startswith(al) for al in SYSTEMS.get(a, ()))]
        want = f"{describe(f.get('system'))} {describe(f.get('value'))}"
        if f["cardinality"] == "prohibited":
            return (FAIL if found else PASS), "none: " + want, ", ".join(x[2] for x in found) or "none"
        ok = any(matches(f.get("value"), code) for _, _, code in found) if found else False
        return (PASS if ok else FAIL), want, ", ".join(f"{n} {code}" for _, n, code in found) or "not classified"
    if t == "material":
        mats = c.materials()
        ok = bool(mats) and (f.get("value") is None or any(matches(f["value"], m) for m in mats))
        return (PASS if ok else FAIL), f"material {describe(f.get('value'))}", ", ".join(mats) or "no material"
    if t == "partOf":
        rel, ent = f["relation"].upper(), f.get("entity")
        if rel == "IFCRELCONTAINEDINSPATIALSTRUCTURE":
            ok = c.level is not None and (ent is None or matches(ent["name"], "IFCBUILDINGSTOREY", fold=True))
            return (PASS if ok else FAIL), f"contained in {describe(ent['name']) if ent else 'a spatial element'}", f"level {c.level}" if c.level else "no level"
        if rel in ("IFCRELFILLSELEMENT", "IFCRELVOIDSELEMENT") and ent is not None and c.host_entity:
            return (PASS if matches(ent["name"], c.host_entity, fold=True) else FAIL), f"part of {describe(ent['name'])}", c.host_entity
        return UNKNOWN, f"{rel or 'relation'} {describe(ent['name']) if ent else ''}", "SPADRA does not model this relation"
    return UNKNOWN, t, "unsupported facet"


def _typed(f: Mapping[str, Any], found: tuple[str, str, Any]) -> bool:
    if not f.get("data_type"):
        return True
    s, p, _ = found
    cat = BD.catalog()["sets"].get(s)
    if cat is None:
        return True                                                                  # a custom property has no declared type to compare with
    d = (cat.get("properties") or {}).get(p)
    if d is not None:
        return d["ifc_type"].upper() == f["data_type"].upper() or d["kind"] == "enum"
    q = (cat.get("quantities") or {}).get(p)
    return q is None or QTY_TYPES[q["kind"]] == f["data_type"].upper()


# ---------------------------------------------------------------------------------------------------------------------- IDS store and validation
def _docs(project: Any) -> list[dict[str, Any]]:
    return BD._bim(project).get("ids") or []


def add_ids(project: Any, raw: bytes, filename: str) -> dict[str, Any]:
    doc = parse_ids(raw)
    store = BD._bim(project, True)
    if len(store.get("ids") or []) >= MAX_IDS_DOCS:
        raise BadRequest(f"A project holds at most {MAX_IDS_DOCS} IDS files")
    digest = hashlib.sha256(raw).hexdigest()
    if any(d["sha256"] == digest for d in store.get("ids") or []):
        raise BadRequest("This IDS file is already in the project")
    entry = {"id": digest[:12], "title": doc["title"], "filename": text(filename or "requirements.ids", "filename", 120), "sha256": digest,
             "added": datetime.now(timezone.utc).isoformat(timespec="seconds"), "specifications": len(doc["specifications"]), "xml": raw.decode("utf-8", "replace")}
    store.setdefault("ids", []).append(entry)
    return {k: v for k, v in entry.items() if k != "xml"}


def remove_ids(project: Any, ids_id: str) -> None:
    store = BD._bim(project, True)
    left = [d for d in store.get("ids") or [] if d["id"] != ids_id]
    if len(left) == len(store.get("ids") or []):
        raise NotFound("That IDS file is not in the project")
    store["ids"] = left
    (store.get("ids_results") or {}).pop(ids_id, None)


def listing(project: Any) -> list[dict[str, Any]]:
    res = BD._bim(project).get("ids_results") or {}
    return [{**{k: v for k, v in d.items() if k != "xml"}, "validated": res.get(d["id"], {}).get("summary"),
             "stale": bool(res.get(d["id"])) and res[d["id"]]["revision"] != project.revision} for d in _docs(project)]


def validate_ids(project: Any, ids_id: str) -> dict[str, Any]:
    """Evaluate every specification against the model and keep the evidence in the project. Runs inside a command (the result is part of the project)."""
    entry = next((d for d in _docs(project) if d["id"] == ids_id), None)
    if entry is None:
        raise NotFound("That IDS file is not in the project")
    doc = parse_ids(entry["xml"].encode("utf-8"))
    qto = _quantity_getter(project)
    ctxs = [Context(project, oid, qto) for oid in E.element_ids(project)]
    specs = []
    for sp in doc["specifications"]:
        applicable = [c for c in ctxs if all(evaluate(f, c)[0] == PASS for f in sp["applicability"])] if sp["applicability"] else []
        failures: list[dict[str, Any]] = []
        passed = unknown = 0
        for c in applicable:
            bad = False
            unsure = False
            for f in sp["requirements"]:
                status, expected, found = evaluate(f, c)
                if status == FAIL:
                    bad = True
                    if len(failures) < MAX_EVIDENCE:
                        failures.append({"element": c.id, "name": c.name, "facet": f["type"], "cardinality": f["cardinality"], "expected": expected, "found": found,
                                         "instructions": f.get("instructions", "")})
                elif status == UNKNOWN:
                    unsure = True
                    if len(failures) < MAX_EVIDENCE:
                        failures.append({"element": c.id, "name": c.name, "facet": f["type"], "cardinality": f["cardinality"], "expected": expected, "found": found, "not_evaluable": True})
            if bad:
                continue
            unknown += unsure
            passed += not unsure
        required = int(sp["min"] or 0) >= 1
        if sp["max"] == "0":                                                             # a prohibiting specification: nothing may be like this
            failures = [{"element": c.id, "name": c.name, "facet": "applicability", "cardinality": "prohibited", "expected": "no such element", "found": "present"} for c in applicable][:MAX_EVIDENCE]
        if not applicable:
            status = FAIL if required else "not_applicable"
        elif any(not x.get("not_evaluable") for x in failures):
            status = FAIL
        elif unknown:
            status = UNKNOWN
        else:
            status = PASS
        specs.append({"index": sp["index"], "name": sp["name"], "status": status, "applicable": len(applicable), "passed": passed, "failed": len({x["element"] for x in failures if not x.get("not_evaluable")}),
                      "not_evaluable": unknown, "required_but_none_apply": required and not applicable, "failures": failures, "instructions": sp["instructions"]})
    summary = {"specifications": len(specs), **{s: sum(1 for x in specs if x["status"] == s) for s in (PASS, FAIL, UNKNOWN, "not_applicable")}}
    result = {"revision": project.revision + 1, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "summary": summary, "specs": specs,
              "limits": "Entity matching is by the class SPADRA gives an element (and the usual IFC superclasses); attributes and relations SPADRA does not keep are reported as not evaluable."}
    BD._bim(project, True).setdefault("ids_results", {})[ids_id] = result
    return result


def ids_report(project: Any, ids_id: str) -> dict[str, Any]:
    entry = next((d for d in _docs(project) if d["id"] == ids_id), None)
    if entry is None:
        raise NotFound("That IDS file is not in the project")
    res = (BD._bim(project).get("ids_results") or {}).get(ids_id)
    return {"ids": {k: v for k, v in entry.items() if k != "xml"}, "result": res, "stale": bool(res) and res["revision"] != project.revision}


# ---------------------------------------------------------------------------------------------------------------------- BIM basis ILS
ILS_EDITION = "BIM basis ILS (digiGO), second version (v2.0) as published online; chapters 3.6, 3.7, 4.3, 4.4, 4.5 and 4.7 read"
BASE = "https://www.digigo.nu/en/ilsen-en-richtlijnen/bim-base-ids/"
# strength: required = the page says it always applies; directive = it is worded as an instruction without calling it mandatory; recommended = "should preferably".
STRENGTH_SEVERITY = {"required": "error", "directive": "warning", "recommended": "info"}
STRUCT = {"wall", "floor", "roof", "structural"}
OFFICIAL_ENTITIES_WITH_LOADBEARING = {"IfcBeam", "IfcColumn", "IfcSlab", "IfcWall", "IfcRoof"}


def _prop(c: Context, name: str) -> Any:
    for s, props in c.psets.items():
        if s in BD.catalog()["sets"] and name in props:
            return props[name]
    return None


def _r_sfb(c: Context) -> str | None:
    code = str(c.meta.get("spadra:sfb") or "").strip("()")                          # SPADRA keeps the table's bracketed form; the ILS notation is without brackets
    if not code:
        return "has no NL-SfB (table 1) code"
    if not re.fullmatch(r"\d{2}\.\d{2}", code):
        return f"NL-SfB code '{code}' is not a 4-digit code (e.g. 21.11); choose a more specific code"
    return None


def _r_loadbearing(c: Context) -> str | None:
    return None if isinstance(_prop(c, "LoadBearing"), bool) else "has no LoadBearing property (yes/no) in an official property set"


def _r_external(c: Context) -> str | None:
    return None if isinstance(_prop(c, "IsExternal"), bool) else "has no IsExternal property (yes/no) in an official property set"


def _r_fire(c: Context) -> str | None:
    v = _prop(c, "FireRating")
    return None if v is None or re.fullmatch(r"\d{1,3}", str(v).strip()) else f"FireRating '{v}' is not just a number of minutes"


def _r_material(c: Context) -> str | None:
    return None if c.materials() else "has no material"


def _r_material_name(c: Context) -> str | None:
    for m in c.materials():
        if re.search(r"\b\d{2}\.\d{2}\b", m) or re.search(r"\b(REI|EI|R)\s?\d{2,3}\b", m):
            return f"material '{m}' carries an element code or a performance requirement in its name"
    return None


def _r_official_dup(c: Context) -> str | None:
    official = {p for s in BD.sets_for(c.meta, "pset") for p in BD.catalog()["sets"][s]["properties"]}
    dup = sorted(k[len("custom:"):] for k in c.meta if k.startswith("custom:") and k[len("custom:"):] in official)
    return f"custom propert{'ies' if len(dup) > 1 else 'y'} {', '.join(dup)} also exist{'s' if len(dup) == 1 else ''} as official buildingSMART propert{'ies' if len(dup) > 1 else 'y'}: use the official set" if dup else None


BASELINE: list[dict[str, Any]] = [
    {"id": "ILS-3.6-SFB", "title": "NL-SfB classification, table 1, four-digit notation", "chapter": "3-6-classification-system", "strength": "directive",
     "text": "Classify the object with the most appropriate NL-SfB (table 1) code, in the 4-digit notation without brackets. The page does not say which IFC property carries it.",
     "applies": lambda c: c.category in {"wall", "floor", "roof", "door", "window", "structural", "stair", "foundation"}, "check": _r_sfb},
    {"id": "ILS-3.7-OFFICIAL", "title": "Official properties belong in the official set", "chapter": "3-7-use-property-sets", "strength": "directive",
     "text": "Properties that buildingSMART assigns to a property set should also be found there; own properties go in an own set.",
     "applies": lambda c: bool(BD.sets_for(c.meta, "pset")), "check": _r_official_dup},
    {"id": "ILS-4.3-LOADBEARING", "title": "LoadBearing on structural components", "chapter": "4-3-load-bearing-non-load-bearing", "strength": "recommended",
     "text": "Primary and secondary structural components (beam, column, slab, wall, and in IFC4 also roof) should preferably have LoadBearing = TRUE or FALSE.",
     "applies": lambda c: BD.entity_of(c.meta) in OFFICIAL_ENTITIES_WITH_LOADBEARING, "check": _r_loadbearing},
    {"id": "ILS-4.4-ISEXTERNAL", "title": "IsExternal on floors, walls, roofs and their openings", "chapter": "4-4-internal-external", "strength": "required",
     "text": "IsExternal always applies to floors, walls and roofs (including openings): TRUE for external, FALSE for internal.",
     "applies": lambda c: c.category in {"wall", "floor", "roof", "door", "window"}, "check": _r_external},
    {"id": "ILS-4.5-FIRERATING", "title": "FireRating is a number of minutes", "chapter": "4-5-fire-safety", "strength": "directive",
     "text": "FireRating holds only the value in number of minutes (for example 30, 60, 90). Whether it is required depends on the project's fire-safety requirements.",
     "applies": lambda c: _prop(c, "FireRating") is not None, "check": _r_fire},
    {"id": "ILS-4.7-MATERIAL", "title": "Every object has a material", "chapter": "4-7-material", "strength": "directive",
     "text": "Each object has a relationship with a material; if several, link them separately, otherwise the dominant one.",
     "applies": lambda c: c.category in {"wall", "floor", "roof", "structural", "foundation"}, "check": _r_material},
    {"id": "ILS-4.7-MATERIALNAME", "title": "No element codes or performance requirements in material names", "chapter": "4-7-material", "strength": "recommended",
     "text": "Quality and performance requirements (e.g. fire rating) and element codes (e.g. NL-SfB) do not belong in the material name. SPADRA checks this by pattern, which is a heuristic.",
     "applies": lambda c: bool(c.materials()), "check": _r_material_name},
]
BY_ID = {r["id"]: r for r in BASELINE}


def ils_config(project: Any) -> dict[str, Any]:
    cfg = BD._bim(project).get("ils") or {}
    return {"enabled": bool(cfg.get("enabled", False)), "edition": cfg.get("edition") or ILS_EDITION, "disabled": list(cfg.get("disabled") or []),
            "severity": dict(cfg.get("severity") or {}), "project_rules": list(cfg.get("project_rules") or []), "custom_set_name": cfg.get("custom_set_name") or "SPADRA_Custom"}


def set_ils(project: Any, p: Mapping[str, Any]) -> dict[str, Any]:
    cfg = dict(BD._bim(project, True).get("ils") or {})
    if "enabled" in p:
        cfg["enabled"] = bool(p["enabled"])
    if "edition" in p:
        cfg["edition"] = text(p["edition"], "edition", 200)
    if "disabled" in p:
        bad = [r for r in p["disabled"] if r not in BY_ID and r not in {x["id"] for x in cfg.get("project_rules") or []}]
        if bad:
            raise BadRequest(f"Unknown rule: {bad[0]}")
        cfg["disabled"] = sorted(set(p["disabled"]))
    if "severity" in p:
        sev = {}
        for k, v in dict(p["severity"]).items():
            if k not in BY_ID and k not in {x["id"] for x in cfg.get("project_rules") or []}:
                raise BadRequest(f"Unknown rule: {k}")
            if v not in SEVERITIES:
                raise BadRequest("severity must be error, warning or info")
            sev[k] = v
        cfg["severity"] = sev
    if "custom_set_name" in p:
        name = text(p["custom_set_name"], "custom_set_name", 60)
        if name.lower().startswith("pset"):
            raise BadRequest("An own property set may not start with 'Pset' (buildingSMART's rule, repeated in the ILS)")
        cfg["custom_set_name"] = name
    BD._bim(project, True)["ils"] = cfg
    return ils_config(project)


def add_project_rule(project: Any, p: Mapping[str, Any]) -> dict[str, Any]:
    """A rule of this project's own: which categories, which official property, what it must hold, and where the requirement is written."""
    cfg = dict(BD._bim(project, True).get("ils") or {})
    rules = list(cfg.get("project_rules") or [])
    rid = text(p.get("id"), "id", 40)
    if not re.fullmatch(r"[A-Za-z0-9._-]+", rid) or rid in BY_ID or any(r["id"] == rid for r in rules):
        raise BadRequest("Rule ids are letters, digits and . _ - and must be new")
    source = text(p.get("source"), "source", 300)                                       # a requirement with no citation is an opinion
    cats = [str(c) for c in p.get("categories") or []]
    if not cats:
        raise BadRequest("Name the categories the rule applies to")
    sname, prop = text(p.get("set"), "set", 80), text(p.get("property"), "property", 80)
    if sname in BD.catalog()["sets"]:
        BD.coerce(sname, prop, None)                                                     # an official set: the property must be one it defines
    if sname.lower().startswith("pset") and sname not in BD.catalog()["sets"]:
        raise BadRequest(f"{sname} is not a buildingSMART set in the catalogue, and own sets may not start with 'Pset'")
    rule = {"id": rid, "title": text(p.get("title") or rid, "title", 120), "source": source, "categories": cats, "set": sname, "property": prop,
            "required": bool(p.get("required", True)), "allowed": [str(a) for a in p.get("allowed") or []], "pattern": str(p.get("pattern") or ""),
            "severity": p.get("severity") if p.get("severity") in SEVERITIES else "warning"}
    if rule["pattern"]:
        try:
            re.compile(rule["pattern"])
        except re.error as exc:
            raise BadRequest(f"The pattern is not a valid regular expression: {exc}") from exc
    rules.append(rule)
    cfg["project_rules"] = rules
    BD._bim(project, True)["ils"] = cfg
    return rule


def remove_project_rule(project: Any, rid: str) -> None:
    cfg = dict(BD._bim(project, True).get("ils") or {})
    left = [r for r in cfg.get("project_rules") or [] if r["id"] != rid]
    if len(left) == len(cfg.get("project_rules") or []):
        raise NotFound("That project rule does not exist")
    cfg["project_rules"] = left
    BD._bim(project, True)["ils"] = cfg


def _project_check(rule: Mapping[str, Any]) -> Callable[[Context], str | None]:
    def check(c: Context) -> str | None:
        v = c.psets.get(rule["set"], {}).get(rule["property"])
        if v is None or v == "":
            return f"has no {rule['set']}.{rule['property']}" if rule["required"] else None
        s = _as_text(v)
        if rule["allowed"] and s not in rule["allowed"]:
            return f"{rule['property']} is '{s}', expected one of {', '.join(rule['allowed'])}"
        if rule["pattern"] and not re.fullmatch(rule["pattern"], s):
            return f"{rule['property']} '{s}' does not match {rule['pattern']}"
        return None
    return check


def ils_report(project: Any) -> dict[str, Any]:
    """Every active rule against every element: which requirement (with its source) is met by how many, and which elements fail and why."""
    cfg = ils_config(project)
    qto = _quantity_getter(project)
    ctxs = [Context(project, oid, qto) for oid in E.element_ids(project) if E.category(project, oid) not in ("grid", "void", "array", "room")]
    rules = []
    allr = [{**r, "scope": "baseline", "source": BASE + r["chapter"]} for r in BASELINE] + [
        {"id": r["id"], "title": r["title"], "scope": "project", "source": r["source"], "strength": None, "text": f"{r['set']}.{r['property']}" + (" required" if r["required"] else ""),
         "applies": (lambda cats: (lambda c: c.category in cats))(r["categories"]), "check": _project_check(r), "severity": r["severity"]} for r in cfg["project_rules"]]
    for r in allr:
        enabled = r["id"] not in cfg["disabled"]
        sev = cfg["severity"].get(r["id"]) or r.get("severity") or STRENGTH_SEVERITY[r["strength"]]
        applicable = [c for c in ctxs if r["applies"](c)] if enabled else []
        fails = []
        for c in applicable:
            msg = r["check"](c)
            if msg:
                fails.append({"element": c.id, "name": c.name, "message": msg})
        rules.append({"id": r["id"], "title": r["title"], "scope": r["scope"], "source": r["source"], "text": r["text"], "strength": r.get("strength"), "severity": sev, "enabled": enabled,
                      "applicable": len(applicable), "passed": len(applicable) - len(fails), "failed": len(fails), "failures": fails})
    return {"enabled": cfg["enabled"], "edition": cfg["edition"], "revision": project.revision, "rules": rules,
            "note": "Baseline rules are transcribed from the chapters named in each source; where a chapter does not say a requirement is mandatory, it is graded as an instruction or a recommendation, "
                    "and the project can change the grade. A rule the ILS leaves open (such as which property carries a classification) is not invented here."}


# ---------------------------------------------------------------------------------------------------------------------- project check
def issues(project: Any) -> list[dict[str, str]]:
    """Findings for ``Project.check``: the ILS (when the project turned it on) and the latest IDS validations. Each says which requirement it comes from."""
    out: list[dict[str, str]] = []
    if ils_config(project)["enabled"]:
        for r in ils_report(project)["rules"]:
            for f in r["failures"][:MAX_REPORTED]:
                out.append(E.issue(r["severity"], f["element"], f"[{r['id']}] {f['name']} {f['message']}", "ils"))
            if len(r["failures"]) > MAX_REPORTED:
                out.append(E.issue("info", r["failures"][0]["element"], f"[{r['id']}] {len(r['failures']) - MAX_REPORTED} more element(s) fail this rule", "ils"))
    results = BD._bim(project).get("ids_results") or {}
    for d in _docs(project):
        res = results.get(d["id"])
        if res is None:
            continue
        if res["revision"] != project.revision:
            out.append(E.issue("info", d["id"], f"The IDS validation of '{d['title']}' was made for an earlier model revision: validate it again", "ids"))
        for sp in res["specs"]:
            if sp["required_but_none_apply"]:
                out.append(E.issue("error", d["id"], f"[IDS {d['title']}] '{sp['name']}' requires at least one applicable element and none matches", "ids"))
            for f in [x for x in sp["failures"] if not x.get("not_evaluable")][:MAX_REPORTED]:
                out.append(E.issue("error", f["element"], f"[IDS {d['title']}: {sp['name']}] {f['name']}: expected {f['expected']}, found {f['found']}", "ids"))
    return out
