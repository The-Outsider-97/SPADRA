"""NL/SfB classification: the official tables shipped with SPADRA, and the codes an element can carry.

The tables are data (``data/nl_sfb.json``, built from the official spreadsheet by ``data/build_nl_sfb.py``); nothing here invents a code.
An element carries up to three codes, stored in its own SGEO metadata so they travel with the element through save, copy, GLB / SGEO export and import:

* ``spadra:sfb``          the element code: **Table 1** (functional building elements) for building elements, **Table 0** (spatial provisions) for rooms;
* ``spadra:sfb_method``   the construction method, Table 2 (one letter);
* ``spadra:sfb_material`` the material, Table 3 (a letter and digits).

A code is only stored when it exists in its table. Nothing is assigned on its own: ``suggestions`` names the codes people usually use for a category,
and the person picks.
"""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..tools.errors import BadRequest, DependencyMissing

DATA = Path(__file__).with_name("data") / "nl_sfb.json"
FIELDS = {"sfb": None, "sfb_method": 2, "sfb_material": 3}              # metadata key -> its table (None: Table 1, or 0 for a room)
LABELS = {"sfb": "NL-SfB code", "sfb_method": "Construction method (NL-SfB table 2)", "sfb_material": "Material (NL-SfB table 3)"}
BRACKETED = (1, 4)                                                     # the tables whose official notation puts the code in brackets, as in (21.1)

# The codes people usually give each category in Table 1, offered when the picker is empty. Each is checked against the data by the tests.
SUGGESTED: dict[str, tuple[str, ...]] = {
    "wall": ("(21.1)", "(21.2)", "(22.1)", "(22.2)"),
    "door": ("(31.3)", "(32.3)"),
    "window": ("(31.2)", "(32.2)"),
    "floor": ("(23.1)", "(23.2)"),
    "roof": ("(27.1)", "(27.2)"),
    "gutter": ("(27.16)", "(27.26)"),
    "downpipe": ("(52.12)", "(52.11)"),
    "structural": ("(28.1)", "(28.2)"),
    "stair": ("(24.1)",),
}


def _fold(text: str) -> str:
    """Lower case without accents, so ``crèche`` is found by ``creche``."""
    return "".join(c for c in unicodedata.normalize("NFD", text.casefold()) if unicodedata.category(c) != "Mn")


@lru_cache(maxsize=1)
def _load() -> dict[str, Any]:
    try:
        raw = json.loads(DATA.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DependencyMissing("The NL-SfB tables (data/nl_sfb.json) are missing from this installation.", hint="Reinstall SPADRA, or run data/build_nl_sfb.py on the official spreadsheet.",
                                details={"error": str(exc)}) from exc
    rows = [{"table": r[0], "code": r[1], "nl": r[2], "en": r[3], "_f": _fold(f"{r[1]} {r[2]} {r[3]}"), "_k": _key(r[1])} for r in raw["rows"]]
    by_key: dict[tuple[int, str], dict[str, Any]] = {}
    for row in rows:
        by_key.setdefault((row["table"], row["_k"]), row)            # a code that the source lists twice answers with its first text
    return {"meta": {k: raw[k] for k in ("source", "version", "tables")}, "rows": rows, "by_key": by_key}


def _key(code: str) -> str:
    return code.strip().strip("()").strip().casefold()


def info() -> dict[str, Any]:
    d = _load()
    counts = {t: sum(1 for r in d["rows"] if r["table"] == int(t)) for t in d["meta"]["tables"]}
    return {**d["meta"], "counts": counts}


def public(row: dict[str, Any]) -> dict[str, Any]:
    return {"table": row["table"], "code": row["code"], "nl": row["nl"], "en": row["en"]}


def _head(value: str) -> str:
    """The code at the start of a text such as ``(21.1) buitenwanden``: the bracketed part, or the first word."""
    value = value.strip()
    if value.startswith("(") and ")" in value:
        return value[: value.index(")") + 1]
    return value.split(None, 1)[0] if value else ""


def lookup(table: int, code: Any) -> dict[str, Any] | None:
    """The entry for a code in a table (with or without the brackets), or None."""
    if not isinstance(code, str) or not code.strip():
        return None
    row = _load()["by_key"].get((table, _key(_head(code))))
    return public(row) if row else None


def search(query: str, table: int | None = None, limit: int = 30) -> list[dict[str, Any]]:
    """Codes whose number starts with the query, or whose Dutch or English text contains every word of it. Code matches come first."""
    if table is not None and table not in range(5):
        raise BadRequest("table must be 0 to 4")
    limit = max(1, min(int(limit), 200))
    q = _fold(query.strip())
    rows = [r for r in _load()["rows"] if table is None or r["table"] == table]
    if not q:
        return [public(r) for r in rows[:limit]]
    key, words = _key(q), q.replace("(", " ").replace(")", " ").split()
    exact: list[dict[str, Any]] = []
    prefix: list[dict[str, Any]] = []
    text: list[dict[str, Any]] = []
    for r in rows:
        if r["_k"] == key:
            exact.append(r)
        elif r["_k"].startswith(key):
            prefix.append(r)
        elif all(w in r["_f"] for w in words):
            text.append(r)
    return [public(r) for r in (exact + prefix + text)[:limit]]


def suggestions(category: str) -> list[dict[str, Any]]:
    out = []
    for code in SUGGESTED.get(category, ()):
        entry = lookup(1, code)
        if entry:
            out.append(entry)
    return out


def table_for(field: str, category: str) -> int:
    t = FIELDS.get(field, "missing")
    if t == "missing":
        raise BadRequest(f"'{field}' is not an NL-SfB field")
    return t if t is not None else (0 if category == "room" else 1)


def normalise(table: int, value: Any) -> str | None:
    """The code as the table writes it, or None to clear it. A code the table does not have is refused with the table's name."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str):
        raise BadRequest("An NL-SfB code is text")
    head = _head(value)
    hit = lookup(table, head)
    if hit is None:
        names = _load()["meta"]["tables"][str(table)]
        raise BadRequest(f"'{head}' is not a code in NL-SfB table {table} ({names['en']}).", hint="Type part of the code or of its name and pick one from the list")
    return hit["code"]


def read(meta: Any, field: str) -> str:
    return str(meta.get(f"spadra:{field}", "") or "")


def rows_for(meta: Any, category: str) -> dict[str, Any]:
    """The Properties group for an element: one row per field, with the name of the code that is set and the table the picker searches."""
    rows = []
    for field in FIELDS:
        table = table_for(field, category)
        code = read(meta, field)
        entry = lookup(table, code) if code else None
        rows.append({"key": field, "label": LABELS[field] if field != "sfb" else f"NL-SfB code (table {table})", "type": "sfb", "table": table, "value": code,
                     "text": (entry["nl"] if entry else ""), **({"suggest": suggestions(category)} if field == "sfb" and table == 1 else {})})
    return {"title": "Classification (NL-SfB)", "rows": rows}


def set_code(project: Any, oid: str, field: str, value: Any) -> None:
    """Store, or clear (empty value), one NL-SfB field of an element."""
    from . import elements as E
    meta = E.meta_of(project, oid)
    table = table_for(field, str(E.m(meta, "category", "generic")))
    code = normalise(table, value)
    key = f"spadra:{field}"
    if code is None:
        if key in meta:
            project.model.attach_semantics(oid, {k: v for k, v in meta.items() if k != key}, replace=True)
        return
    project.model.attach_semantics(oid, {field: code}, namespace="spadra")


def is_field(key: str) -> bool:
    return key in FIELDS
