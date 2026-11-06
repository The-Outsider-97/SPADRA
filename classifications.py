"""IFC 4.3 type products and optional, locally licensed OmniClass classifications.

The IFC vocabulary is generated from the official IFC4X3_ADD2 EXPRESS schema.
CSI OmniClass tables/crosswalks are NOT redistributed: load a permitted
catalog from SPADRA_OMNICLASS_CATALOG or data/omniclass.local.json.

A Revit-style Assembly Code is UniFormat, *not* an OmniClass number.
A verified, unique local crosswalk can fill OmniClass Table 21 (Elements);
Table 23 (Products) is intentionally independent and is never inferred.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from urllib import error as urlerror
from urllib import request as urlrequest
from pathlib import Path
from typing import Any, Mapping

from ..tools.errors import BadRequest, DependencyMissing

DIR = Path(__file__).with_name("data")
IFC_DATA = DIR / "ifc_type_products.json"
LOCAL_OMNICLASS = DIR / "omniclass.local.json"
SCHEMA = "spadra.omniclass.v1"
FIELDS = frozenset({
    "assembly_code", "omniclass_21", "omniclass_23",
    "ifc_type", "ifc_predefined_type",
})
IFC_SUGGEST = {
    "wall": ("IfcWallType", "IfcCurtainWallType"),
    "door": ("IfcDoorType",),
    "window": ("IfcWindowType",),
    "floor": ("IfcSlabType", "IfcCoveringType"),
    "roof": ("IfcRoofType",),
    "gutter": ("IfcPipeSegmentType",),
    "downpipe": ("IfcPipeSegmentType",),
    "structural": ("IfcBeamType", "IfcColumnType", "IfcMemberType"),
    "foundation": ("IfcFootingType", "IfcPileType"),
    "stair": ("IfcStairType", "IfcStairFlightType"),
    "room": ("IfcSpaceType",),
    "generic": ("IfcBuildingElementProxyType",),
}
MAX_CATALOG_BYTES = 8_000_000
# CSI publishes the API host, path, and GraphQL query examples. Never accept a
# user-provided upstream URL (no SSRF) or expose credentials to the browser.
CSI_API_URL = "https://crosswalk.azure-api.net/v3/query"

@lru_cache(maxsize=1)
def _ifc() -> dict[str, Any]:
    try:
        data = json.loads(IFC_DATA.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DependencyMissing("IFC type-product catalog is unavailable", hint="Restore data/ifc_type_products.json") from exc
    if data.get("schema") != "IFC4X3_ADD2" or not isinstance(data.get("types"), dict):
        raise DependencyMissing("Invalid IFC 4.3 type-product catalog")
    return data

def ifc_lookup(value: Any) -> tuple[str, dict[str, Any]] | None:
    if not isinstance(value, str):
        return None
    needle = value.strip().casefold()
    for name, entry in _ifc()["types"].items():
        if needle == name.casefold():
            return name, entry
    return None

def ifc_search(q: str = "", limit: int = 30) -> list[dict[str, Any]]:
    term = q.strip().casefold()
    hits = [
        {"code": name, "title": name, "predefined_types": row["predefined_types"],
         **({"occurrence": row["occurrence"]} if "occurrence" in row else {})}
        for name, row in _ifc()["types"].items()
        if term in name.casefold() or term in str(row.get("occurrence", "")).casefold()
    ]
    hits.sort(key=lambda x: (not x["code"].casefold().startswith(term), x["code"]))
    return hits[:limit]

def _catalog_path() -> Path:
    override = os.environ.get("SPADRA_OMNICLASS_CATALOG", "").strip()
    return Path(override).expanduser() if override else LOCAL_OMNICLASS

def _code(value: Any, label: str) -> str:
    if not isinstance(value, str) or not 0 < len(value.strip()) <= 100:
        raise BadRequest(label + " must be non-empty text of at most 100 characters")
    return value.strip()

@lru_cache(maxsize=8)
def _parse_catalog(path: str, mtime_ns: int, size: int) -> dict[str, Any]:
    if size > MAX_CATALOG_BYTES:
        raise BadRequest("OmniClass catalog is larger than the configured safety limit")
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BadRequest("Unable to read OmniClass catalog JSON", details={"reason": str(exc)}) from exc
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        raise BadRequest("OmniClass catalog must declare schema spadra.omniclass.v1")
    if not isinstance(raw.get("edition"), str) or not raw["edition"].strip():
        raise BadRequest("OmniClass catalog must state its edition")
    tables = raw.get("tables")
    if not isinstance(tables, dict) or set(tables) != {"21", "23"}:
        raise BadRequest("OmniClass catalog requires tables 21 and 23")
    codes: dict[str, dict[str, dict[str, str]]] = {"21": {}, "23": {}}
    for table in ("21", "23"):
        if not isinstance(tables[table], list):
            raise BadRequest("OmniClass table " + table + " must be a list")
        for entry in tables[table]:
            if not isinstance(entry, dict):
                raise BadRequest("OmniClass entries must be objects")
            code = _code(entry.get("code"), "OmniClass code")
            title = _code(entry.get("title"), "OmniClass title")
            k = code.casefold()
            if k in codes[table]:
                raise BadRequest("Duplicate OmniClass table " + table + " code: " + code)
            codes[table][k] = {"code": code, "title": title, "table": int(table)}
    mappings = raw.get("assembly_mappings", [])
    if not isinstance(mappings, list):
        raise BadRequest("assembly_mappings must be a list")
    by_assembly: dict[str, list[dict[str, str]]] = {}
    for entry in mappings:
        if not isinstance(entry, dict):
            raise BadRequest("Assembly mappings must be objects")
        assembly = _code(entry.get("assembly_code"), "Assembly code")
        omni = _code(entry.get("omniclass_code"), "Mapped OmniClass code")
        source = _code(entry.get("source"), "Crosswalk evidence")
        if entry.get("table") != 21:
            raise BadRequest("Assembly mapping must target OmniClass Table 21, not Table 23")
        if entry.get("verified") is not True:
            raise BadRequest("Only explicitly verified mappings may be imported")
        resolved = codes["21"].get(omni.casefold())
        if resolved is None:
            raise BadRequest("Mapped OmniClass code is absent from Table 21: " + omni)
        row = {"assembly_code": assembly, "omniclass_code": resolved["code"], "source": source}
        items = by_assembly.setdefault(assembly.casefold(), [])
        if not any(i["omniclass_code"] == row["omniclass_code"] for i in items):
            items.append(row)
    return {"edition": raw["edition"], "codes": codes, "mappings": by_assembly}

def catalog() -> dict[str, Any] | None:
    path = _catalog_path()
    if not path.is_file():
        if os.environ.get("SPADRA_OMNICLASS_CATALOG", "").strip():
            raise DependencyMissing("Configured OmniClass catalog file was not found",
                                    hint="Check SPADRA_OMNICLASS_CATALOG")
        return None
    stat = path.stat()
    return _parse_catalog(str(path.resolve()), stat.st_mtime_ns, stat.st_size)

def _csi_key() -> str:
    """A server-only CSI Crosswalk API key; never serialize it in API responses."""
    return os.environ.get("SPADRA_CSI_CROSSWALK_KEY", "").strip()


def _csi_rows(standard: str, *, table: int | None = None, number: str = "",
              search: str = "", crosswalks: bool = False) -> tuple[list[dict[str, Any]], str]:
    """Query CSI's documented Crosswalk v3 GraphQL endpoint, without copying data into SPADRA.

    Documentation and example queries: github.com/csi-net/crosswalk.
    The upstream API requires a separately licensed key. The normal UI never
    sends the key, and it is not inserted in logging, diagnostics or metadata.
    """
    key = _csi_key()
    if not key:
        raise DependencyMissing("CSI Crosswalk is not configured",
                                hint="Set SPADRA_CSI_CROSSWALK_KEY or load an authorized local catalog")
    if standard not in ("omniclass", "uniformat") or (table is not None and table not in (21, 23)):
        raise BadRequest("Invalid CSI standard or OmniClass table")
    args = ["id: " + json.dumps(standard)]
    if table is not None:
        args.append("table: " + json.dumps(str(table)))
    filters = []
    if number:
        filters.append("number: " + json.dumps(_code(number, "Classification number")))
    elif search:
        filters.append("search: " + json.dumps(_code(search, "Classification query")))
    cls_args = "(" + ", ".join(filters) + ")" if filters else ""
    details = ("crosswalks { number title standards { name version } }"
               if crosswalks else "")
    query = ("{ standards(" + ", ".join(args) + ") { name number version "
             "classifications" + cls_args + " { number title " + details + " } } }")
    request = urlrequest.Request(
        CSI_API_URL, data=json.dumps({"query": query}).encode("utf-8"),
        headers={"Authorization": key, "Content-Type": "application/json",
                 "Accept": "application/json", "User-Agent": "SPADRA/1.0"},
        method="POST")
    try:
        with urlrequest.urlopen(request, timeout=8.0) as response:
            # Guard against unexpectedly large upstream content and HTML error pages.
            data = response.read(2_000_001)
            if len(data) > 2_000_000:
                raise DependencyMissing("CSI Crosswalk response exceeded the size limit")
        result = json.loads(data)
    except urlerror.HTTPError as exc:
        # Do not echo response bodies or request headers (subscription key).
        raise DependencyMissing(
            "CSI Crosswalk request was refused",
            hint="Check the CSI license/API key and connection",
            details={"http_status": exc.code}) from None
    except (urlerror.URLError, TimeoutError, OSError, ValueError) as exc:
        raise DependencyMissing("CSI Crosswalk is unreachable or returned invalid data",
                                hint="Check connection and CSI Crosswalk service") from None
    if not isinstance(result, dict) or result.get("errors"):
        raise DependencyMissing("CSI Crosswalk returned an unsuccessful GraphQL result")
    standards = result.get("data", {}).get("standards")
    if not isinstance(standards, list):
        raise DependencyMissing("Unexpected CSI Crosswalk response structure")
    rows = []
    edition = ""
    for st in standards:
        if not isinstance(st, dict):
            continue
        edition = str(st.get("version") or edition)
        classes = st.get("classifications") or []
        if not isinstance(classes, list):
            raise DependencyMissing("Invalid CSI Crosswalk classification payload")
        rows.extend(x for x in classes if isinstance(x, dict))
    return rows, edition


def _csi_lookup(code: str, table: int) -> dict[str, Any] | None:
    matches, edition = _csi_rows("omniclass", table=table, number=code)
    normalized = re.sub(r"\s+", "", code).casefold()
    for row in matches:
        number = row.get("number")
        if isinstance(number, str) and re.sub(r"\s+", "", number).casefold() == normalized:
            return {"code": number, "title": str(row.get("title") or number),
                    "table": table, "edition": edition}
    return None


def _csi_search(kind: str, q: str, table: int, limit: int) -> list[dict[str, Any]]:
    # Without a search term the API returns only root classifications;
    # with a term it searches the licensed catalog throughout the hierarchy.
    standard = "uniformat" if kind == "assembly" else "omniclass"
    rows, edition = _csi_rows(standard, table=table if kind == "omniclass" else None,
                             search=q.strip()[:100])
    out = []
    seen = set()
    for item in rows:
        number = item.get("number")
        title = item.get("title")
        if not isinstance(number, str) or not isinstance(title, str) or len(number) > 100:
            continue
        k = number.casefold()
        if k in seen:
            continue
        seen.add(k)
        out.append({"code": number, "title": title,
                    **({"table": table} if kind == "omniclass" else {}),
                    "edition": edition})
    return out[:limit]


def _csi_crosswalk(assembly_code: str) -> list[dict[str, str]]:
    """Only authoritative UniFormat -> OmniClass Table 21 direct crosswalks.

    Return every distinct code/edition candidate; the caller autofills only
    when there is a single unambiguous candidate. No word-similarity guessing.
    """
    classes, _ = _csi_rows("uniformat", number=assembly_code, crosswalks=True)
    candidates = {}
    for cls in classes:
        code = cls.get("number")
        if not isinstance(code, str) or code.casefold() != assembly_code.casefold():
            continue
        for other in cls.get("crosswalks") or []:
            if not isinstance(other, dict):
                continue
            number = other.get("number")
            if not isinstance(number, str) or not re.match(r"^21[-\s]", number):
                continue
            for standard in other.get("standards") or []:
                if not isinstance(standard, dict):
                    continue
                if str(standard.get("name", "")).casefold().replace(" ", "") != "omniclass":
                    continue
                edition = str(standard.get("version") or "")
                candidate = {"omniclass_code": number,
                             "source": "CSI Crosswalk v3 UniFormat → OmniClass Table 21"
                                       + (", version " + edition if edition else "")}
                candidates[(number.casefold(), edition)] = candidate
    return list(candidates.values())


def info() -> dict[str, Any]:
    cs = catalog()
    return {
        "ifc": {"schema": _ifc()["schema"], "version": _ifc()["version"],
                "types": len(_ifc()["types"]), "source": _ifc()["source_url"]},
        "omniclass": {"available": cs is not None or bool(_csi_key()),
                      "provider": "local_catalog" if cs is not None else "csi_crosswalk" if _csi_key() else "unconfigured",
                      "edition": cs["edition"] if cs else None,
                      "tables": {t: len(cs["codes"][t]) if cs else None if _csi_key() else 0 for t in ("21", "23")},
                      "verified_assembly_codes": len(cs["mappings"]) if cs else None if _csi_key() else 0,
                      "notice": "CSI-licensed table content requires appropriate access; no official mapping is bundled."},
    }

def search(kind: str, q: str = "", *, table: int = 21, limit: int = 30) -> list[dict[str, Any]]:
    if kind == "ifc":
        return ifc_search(q, limit)
    if kind not in ("omniclass", "assembly"):
        raise BadRequest("Classification kind must be ifc, omniclass, or assembly")
    if table not in (21, 23):
        raise BadRequest("OmniClass table must be 21 or 23")
    cs = catalog()
    if cs is None:
        return _csi_search(kind, q, table, limit) if _csi_key() else []
    term = q.strip().casefold()
    if kind == "assembly":
        return [{"code": rows[0]["assembly_code"], "title": "Assembly / UniFormat",
                 "mapped": [r["omniclass_code"] for r in rows]}
                for rows in sorted(cs["mappings"].values(), key=lambda items: items[0]["assembly_code"])
                if term in rows[0]["assembly_code"].casefold()][:limit]
    vals = [x for x in cs["codes"][str(table)].values()
            if term in x["code"].casefold() or term in x["title"].casefold()]
    vals.sort(key=lambda x: (not x["code"].casefold().startswith(term), x["code"]))
    return vals[:limit]

def read(meta: Mapping[str, Any], field: str) -> str:
    return str(meta.get("spadra:" + field, "") or "")

def is_field(key: str) -> bool:
    return key in FIELDS

def _put(project: Any, oid: str, changes: Mapping[str, Any]) -> None:
    """One atomic SGEO metadata change; undo/redo is owned by Project.execute."""
    from . import elements as E
    meta = dict(E.meta_of(project, oid))
    for key, value in changes.items():
        full = "spadra:" + key
        if value is None or value == "":
            meta.pop(full, None)
        else:
            meta[full] = value
    project.model.attach_semantics(oid, meta, replace=True)

def _manual_omni(value: Any, table: int) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    c = catalog()
    code = _code(value, "OmniClass code")
    if c is None and _csi_key():
        entry = _csi_lookup(code, table)
    elif c is not None:
        entry = c["codes"][str(table)].get(code.casefold())
    else:
        raise BadRequest("Configure a licensed OmniClass catalog or CSI Crosswalk key first")
    if entry is None:
        raise BadRequest("Code is not in the configured OmniClass Table " + str(table))
    return entry["code"]

def set_field(project: Any, oid: str, field: str, value: Any) -> None:
    """Only explicitly verified local or CSI-provided crosswalks can auto-fill OmniClass."""
    from . import elements as E
    if field not in FIELDS:
        raise BadRequest("Unknown classification field " + field)
    meta = E.meta_of(project, oid)
    if field == "assembly_code":
        new = None if value is None or (isinstance(value, str) and not value.strip()) else _code(value, "Assembly code")
        changes: dict[str, Any] = {"assembly_code": new}
        if read(meta, "omniclass_21_origin") == "auto":
            changes.update({"omniclass_21": None, "omniclass_21_origin": None, "omniclass_21_source": None})
        if new and (not read(meta, "omniclass_21") or read(meta, "omniclass_21_origin") == "auto"):
            cs = catalog()
            hits = (cs["mappings"].get(new.casefold(), []) if cs is not None
                    else _csi_crosswalk(new) if _csi_key() else [])
            if len(hits) == 1:
                changes.update({"omniclass_21": hits[0]["omniclass_code"],
                                "omniclass_21_origin": "auto", "omniclass_21_source": hits[0]["source"]})
        _put(project, oid, changes)
        return
    if field in ("omniclass_21", "omniclass_23"):
        table = int(field[-2:])
        code = _manual_omni(value, table)
        changes = {field: code}
        if table == 21:
            changes.update({"omniclass_21_origin": "manual" if code else None,
                            "omniclass_21_source": None})
        _put(project, oid, changes)
        return
    if field == "ifc_type":
        if value is None or (isinstance(value, str) and not value.strip()):
            _put(project, oid, {"ifc_type": None, "ifc_predefined_type": None})
            return
        entry = ifc_lookup(value)
        if entry is None:
            raise BadRequest("Unknown IFC 4.3 type product; select one from the official IFC catalog")
        old = read(meta, "ifc_predefined_type")
        _put(project, oid, {"ifc_type": entry[0],
                            "ifc_predefined_type": old if old in entry[1]["predefined_types"] else None})
        return
    selected = ifc_lookup(read(meta, "ifc_type"))
    if value is None or (isinstance(value, str) and not value.strip()):
        _put(project, oid, {"ifc_predefined_type": None})
        return
    if not isinstance(value, str) or selected is None:
        raise BadRequest("Choose an IFC type before setting its predefined type")
    possible = selected[1]["predefined_types"]
    predefined = value.strip().upper()
    if predefined not in possible:
        raise BadRequest("Predefined type is not valid for " + selected[0])
    _put(project, oid, {"ifc_predefined_type": predefined})

def groups_for(meta: Mapping[str, Any], category: str) -> list[dict[str, Any]]:
    """Use catalog-backed pickers, not copied app-side classifications."""
    cs = catalog()
    a = read(meta, "assembly_code")
    code21 = read(meta, "omniclass_21")
    code23 = read(meta, "omniclass_23")
    def title(table: str, code: str) -> str:
        if cs is None:
            return ("CSI Crosswalk connected: search the licensed table." if _csi_key()
                    else "OmniClass catalog not configured (licensed access required).") if not code else code
        entry = cs["codes"][table].get(code.casefold()) if code else None
        return entry["title"] if entry else ("This code is not in the current catalog." if code else "")
    if a and not code21 and cs is not None and len(cs["mappings"].get(a.casefold(), [])) > 1:
        status = "Ambiguous assembly crosswalk: choose the OmniClass element manually."
    elif a and not code21:
        status = "No verified one-to-one mapping for this Assembly Code."
    elif code21 and read(meta, "omniclass_21_origin") == "auto":
        status = "Automatically mapped from Assembly Code; " + read(meta, "omniclass_21_source")
    else:
        status = ""
    omniclass = {"title": "Classification (OmniClass)", "rows": [
        {"key": "assembly_code", "label": "Assembly Code (UniFormat)", "type": "catalog",
         "catalog": "assembly", "value": a, "text": "UniFormat assembly; source for the verified Table 21 mapping."},
        {"key": "omniclass_21", "label": "OmniClass Table 21 (Elements)", "type": "catalog",
         "catalog": "omniclass", "table": 21, "value": code21,
         "text": " ".join(x for x in (title("21", code21), status) if x)},
        {"key": "omniclass_23", "label": "OmniClass Table 23 (Products)", "type": "catalog",
         "catalog": "omniclass", "table": 23, "value": code23, "text": title("23", code23)},
    ]}
    type_name = read(meta, "ifc_type")
    lookup = ifc_lookup(type_name)
    predefined = lookup[1]["predefined_types"] if lookup else []
    ifc = {"title": "Classification (IFC 4.3)", "rows": [
        {"key": "ifc_type", "label": "IFC Type Product", "type": "catalog",
         "catalog": "ifc", "value": type_name,
         "catalog_suggest": [{"code": n, "title": n} for n in IFC_SUGGEST.get(category, ())
                             if ifc_lookup(n)]},
        {"key": "ifc_predefined_type", "label": "IFC PredefinedType", "type": "choice",
         "value": read(meta, "ifc_predefined_type"),
         "choices": [{"value": "", "label": "— Unassigned —"}] +
                    [{"value": v, "label": v} for v in predefined]},
    ]}
    return [omniclass, ifc]
