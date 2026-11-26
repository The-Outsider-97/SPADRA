"""Regulation datasets: the versioned, hashed, verifiable data the engine reads. Nothing about a regulation is written in code.

Layout (under ``applications/SPADRA/data/regulations``)::

    registry.json                                  the source register: every source considered, its licence class, how it is reached, what is allowed
    <JURISDICTION>/<dataset id>/<version>/dataset.json    the rules and tables (no source text beyond a short pinpoint, see ``MAX_TEXT``)
    <JURISDICTION>/<dataset id>/<version>/manifest.json   the SHA-256 of dataset.json, and what was checked against the official source and when

A project *pins* the dataset it uses by id, version and hash, so a later edition never changes an old result silently. A dataset whose file no longer matches its
manifest is refused (``tampered``); one that was never checked against the official source is usable but graded ``unverified`` and says so in every finding.
Datasets for sources whose licence forbids storing their content hold no rules at all (``licence class restricted``): they only describe how a licensed import works.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping

from . import expr as X

DATA_ROOT = Path(__file__).resolve().parents[1] / "data" / "regulations"
DATASET_SCHEMA = "spadra.regulation.dataset.v1"
MANIFEST_SCHEMA = "spadra.regulation.manifest.v1"
JURISDICTIONS = ("NL", "AW", "US")
LICENCE_CLASSES = ("public_law", "open_licence", "licence_required", "restricted", "unknown")
VERIFICATION = ("unverified", "verified_official", "failed")
REVIEW = ("draft", "transcribed", "verified", "approved")
CHECK_KINDS = ("minimum", "maximum")
UNITS = ("m", "mm", "m2", "m3", "deg", "ratio", "count")
MAX_TEXT = 400                                         # no string in a dataset is longer: a dataset is data and pinpoints, never a copy of the instrument
FORBIDDEN_KEYS = {"source_text", "full_text", "licensed_text", "restricted_text", "article_text"}
_ID = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,79}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def canonical_bytes(data: Any) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def dataset_sha256(data: Any) -> str:
    return hashlib.sha256(canonical_bytes(data)).hexdigest()


def _iso(value: Any) -> date | None:
    if not isinstance(value, str) or not _DATE.match(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _walk_strings(value: Any, path: str = "") -> Iterable[tuple[str, str, bool]]:
    """(path, text, is_key_name) for every string in a structure."""
    if isinstance(value, str):
        yield path, value, False
    elif isinstance(value, Mapping):
        for k, v in value.items():
            if isinstance(k, str) and k in FORBIDDEN_KEYS:
                yield f"{path}/{k}", k, True
            yield from _walk_strings(v, f"{path}/{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _walk_strings(v, f"{path}/{i}")


def validate_dataset(ds: Any) -> list[str]:
    """Every reason a dataset cannot be used, as sentences. An empty list means the structure is sound (not that the rules are right: that is ``verification``)."""
    if not isinstance(ds, Mapping):
        return ["a dataset must be a JSON object"]
    out: list[str] = []
    if ds.get("schema") != DATASET_SCHEMA:
        out.append(f"schema must be {DATASET_SCHEMA}")
    for key in ("id", "jurisdiction", "title", "version", "valid_from", "source", "rules"):
        if key not in ds:
            out.append(f"missing '{key}'")
    if out:
        return out
    if not isinstance(ds["id"], str) or not _ID.match(ds["id"]):
        out.append("id must be lower-case letters, digits, '.', '_' or '-'")
    if ds["jurisdiction"] not in JURISDICTIONS:
        out.append(f"jurisdiction must be one of {', '.join(JURISDICTIONS)}")
    if not isinstance(ds["version"], str) or not ds["version"] or len(ds["version"]) > 60:
        out.append("version must be a short string")
    vf, vt = _iso(ds["valid_from"]), _iso(ds.get("valid_to")) if ds.get("valid_to") else None
    if vf is None:
        out.append("valid_from must be a date (YYYY-MM-DD)")
    if ds.get("valid_to") and vt is None:
        out.append("valid_to must be a date (YYYY-MM-DD) or null")
    if vf and vt and vt < vf:
        out.append("valid_to is before valid_from")
    src = ds["source"]
    if not isinstance(src, Mapping) or not all(isinstance(src.get(k), str) and src.get(k) for k in ("authority", "citation", "retrieved")):
        out.append("source needs authority, citation and retrieved")
    else:
        lic = src.get("licence")
        if not isinstance(lic, Mapping) or lic.get("class") not in LICENCE_CLASSES or not isinstance(lic.get("basis"), str):
            out.append("source.licence needs a class (" + ", ".join(LICENCE_CLASSES) + ") and a basis")
        elif lic["class"] in ("restricted", "licence_required") and ds["rules"]:
            out.append(f"the source licence class is '{lic['class']}': a dataset may not hold rules or values from it (licensed import only)")
    for path, s, is_key in _walk_strings(ds):
        if is_key:
            out.append(f"{path}: a dataset may not carry source text")
        elif len(s) > MAX_TEXT:
            out.append(f"{path}: a string longer than {MAX_TEXT} characters (a dataset holds pinpoints and data, not the instrument's text)")
    tables = ds.get("tables") or {}
    if not isinstance(tables, Mapping):
        out.append("tables must be an object")
        tables = {}
    for tid, t in tables.items():
        if not isinstance(t, Mapping) or not isinstance(t.get("columns"), Mapping) or not isinstance(t.get("rows"), Mapping):
            out.append(f"table {tid}: needs columns and rows")
            continue
        for rid, row in t["rows"].items():
            vals = row.get("values") if isinstance(row, Mapping) else None
            if not isinstance(vals, Mapping) or set(vals) - set(t["columns"]):
                out.append(f"table {tid} row {rid}: values must use the table's columns")
            elif any(v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))) for v in vals.values()):
                out.append(f"table {tid} row {rid}: values are numbers or null (null = the instrument designates none)")
    funcs = ds.get("building_functions") or []
    if not isinstance(funcs, list) or any(not isinstance(f, Mapping) or not isinstance(f.get("id"), str) or not isinstance(f.get("label"), str) for f in funcs):
        out.append("building_functions must be a list of {id, label}")
    seen: set[str] = set()
    rules = ds["rules"]
    if not isinstance(rules, list):
        return out + ["rules must be a list"]
    for i, r in enumerate(rules):
        where = f"rule {i + 1}"
        if not isinstance(r, Mapping):
            out.append(f"{where}: must be an object")
            continue
        rid = r.get("id")
        where = f"rule {rid}" if isinstance(rid, str) else where
        if not isinstance(rid, str) or not _ID.match(rid) or rid in seen:
            out.append(f"{where}: id missing, malformed or duplicated")
        seen.add(str(rid))
        cit = r.get("citation")
        if not isinstance(cit, Mapping) or not cit.get("instrument") or not cit.get("article"):
            out.append(f"{where}: needs a citation (instrument and article)")
        if not isinstance(r.get("title"), str) or not r["title"]:
            out.append(f"{where}: needs a title")
        app = r.get("applies_to")
        if not isinstance(app, Mapping) or not isinstance(app.get("category"), str):
            out.append(f"{where}: applies_to needs a category")
        if (r.get("review") or {}).get("status") not in REVIEW:
            out.append(f"{where}: review.status must be one of {', '.join(REVIEW)}")
        chk = r.get("check")
        if not isinstance(chk, Mapping) or chk.get("kind") not in CHECK_KINDS or chk.get("unit") not in UNITS:
            out.append(f"{where}: check needs kind ({', '.join(CHECK_KINDS)}) and unit")
        else:
            for key in ("value", "limit"):
                out += [f"{where}: check.{key}: {p}" for p in X.validate(chk.get(key), tables=tables)]
        if r.get("applies_when") is not None:
            out += [f"{where}: applies_when: {p}" for p in X.validate(r["applies_when"], tables=tables)]
        for c in r.get("corrections") or []:
            if not isinstance(c, Mapping) or not isinstance(c.get("id"), str) or not isinstance(c.get("key"), str):
                out.append(f"{where}: a correction needs an id and the parameter key it changes")
                continue
            out += [f"{where}: correction {c['id']}: {p}" for p in X.validate(c.get("new_value"), tables=tables)]
        if not isinstance(r.get("assumptions", []), list):
            out.append(f"{where}: assumptions must be a list")
    return out


@dataclass
class Dataset:
    path: Path
    data: dict[str, Any]
    manifest: dict[str, Any]
    sha256: str
    problems: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return str(self.data.get("id"))

    @property
    def version(self) -> str:
        return str(self.data.get("version"))

    @property
    def jurisdiction(self) -> str:
        return str(self.data.get("jurisdiction"))

    @property
    def usable(self) -> bool:
        return not self.problems

    @property
    def verification(self) -> str:
        status = (self.manifest.get("verification") or {}).get("status")
        return status if status in VERIFICATION else "unverified"

    @property
    def tables(self) -> dict[str, Any]:
        return self.data.get("tables") or {}

    def functions(self) -> list[dict[str, Any]]:
        return list(self.data.get("building_functions") or [])

    def pin(self) -> dict[str, str]:
        return {"id": self.id, "version": self.version, "sha256": self.sha256}

    def describe(self) -> dict[str, Any]:
        src = self.data.get("source") or {}
        return {"id": self.id, "version": self.version, "jurisdiction": self.jurisdiction, "title": self.data.get("title"), "valid_from": self.data.get("valid_from"),
                "valid_to": self.data.get("valid_to"), "sha256": self.sha256, "verification": self.verification, "usable": self.usable, "problems": self.problems[:20],
                "licence": (src.get("licence") or {}).get("class"), "citation": src.get("citation"), "authority": src.get("authority"), "rules": len(self.data.get("rules") or []),
                "import_required": bool(self.data.get("import_required")), "checked": (self.manifest.get("verification") or {}).get("checked_at")}


def load_dataset(folder: Path) -> Dataset:
    """Read one dataset folder, check the manifest hash and the structure. Never raises for a bad dataset: the problems are carried on it."""
    problems: list[str] = []
    data: dict[str, Any] = {}
    manifest: dict[str, Any] = {}
    try:
        data = json.loads((folder / "dataset.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return Dataset(folder, {}, {}, "", [f"dataset.json cannot be read ({type(exc).__name__})"])
    sha = dataset_sha256(data)
    try:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        problems.append("manifest.json is missing or unreadable")
    if manifest:
        if manifest.get("schema") != MANIFEST_SCHEMA:
            problems.append(f"manifest schema must be {MANIFEST_SCHEMA}")
        if manifest.get("dataset_sha256") != sha:
            problems.append("tampered: dataset.json does not match the hash in its manifest")
    problems += validate_dataset(data)
    return Dataset(folder, data if isinstance(data, dict) else {}, manifest, sha, problems)


def load_all(root: Path | None = None) -> list[Dataset]:
    base = Path(root) if root is not None else DATA_ROOT
    found: list[Dataset] = []
    if not base.is_dir():
        return found
    for jur in sorted(p for p in base.iterdir() if p.is_dir() and p.name in JURISDICTIONS):
        for did in sorted(p for p in jur.iterdir() if p.is_dir()):
            for ver in sorted(p for p in did.iterdir() if p.is_dir() and (p / "dataset.json").exists()):
                found.append(load_dataset(ver))
    return found


def select(jurisdiction: str, on: str | None, *, pin: Mapping[str, Any] | None = None, root: Path | None = None) -> tuple[Dataset | None, str]:
    """The dataset to use: the one the project pinned (by hash), else the newest in force on ``on``. Returns (dataset or None, reason when None)."""
    candidates = [d for d in load_all(root) if d.jurisdiction == jurisdiction]
    if not candidates:
        return None, f"No regulation dataset is installed for {jurisdiction}"
    if pin and pin.get("sha256"):
        for d in candidates:
            if d.sha256 == pin["sha256"]:
                return d, ""
        return None, (f"The dataset this project pinned ({pin.get('id')} {pin.get('version')}, hash {str(pin['sha256'])[:12]}…) is not installed, or its content has changed; "
                      "choose a dataset again to re-pin")
    when = _iso(on) if on else None
    if when is None:
        return None, "The project has no reference date for the regulation"
    in_force = [d for d in candidates if d.usable and _iso(d.data["valid_from"]) <= when and (not d.data.get("valid_to") or when <= _iso(d.data["valid_to"]))]
    if not in_force:
        usable = [d for d in candidates if d.usable]
        if not usable:
            return None, f"The installed {jurisdiction} dataset(s) cannot be used: " + "; ".join(sorted({p for d in candidates for p in d.problems[:1]}))
        return None, f"No installed {jurisdiction} dataset is in force on {on}"
    in_force.sort(key=lambda d: (d.data["valid_from"], d.version))
    return in_force[-1], ""


def source_register(root: Path | None = None) -> dict[str, Any]:
    path = (Path(root) if root is not None else DATA_ROOT) / "registry.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema": "spadra.regulation.registry.v1", "sources": [], "error": "registry.json is missing or unreadable"}
