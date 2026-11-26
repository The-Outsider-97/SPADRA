"""The small expression language the regulation datasets use for limits, applicability and corrections.

A rule never contains code. Its numbers and conditions are a JSON tree of a fixed set of operations, validated when a dataset is loaded and evaluated here with no
``eval``, no attribute access and no names but the facts the engine hands in. A tree is bounded (depth, node count), so a dataset cannot hang or exhaust the engine.

Values are numbers (metres, unless a fact says otherwise), strings (ids such as a building function) or booleans. A fact that is not known raises ``Missing``: the
engine turns that into "insufficient data" for the finding. It is never replaced by a default.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

MAX_DEPTH = 12
MAX_NODES = 120
EPS = 1e-9                                           # float noise only; never a tolerance on a legal limit (a margin is a separate, visible setting)


class ExprError(ValueError):
    """The tree itself is wrong (unknown operation, bad shape, too large). A dataset with such a tree is refused."""


class Missing(Exception):
    """A fact (or table cell) the expression needs is not known."""

    def __init__(self, name: str, why: str = "") -> None:
        super().__init__(name)
        self.name, self.why = name, why


@dataclass
class Trace:
    facts: set[str] = field(default_factory=set)
    tables: set[str] = field(default_factory=set)


_NUMERIC_NARY = {"add", "mul", "min", "max"}
_NUMERIC_BINARY = {"sub", "div"}
_COMPARE = {"eq", "ne", "lt", "le", "gt", "ge"}
_LOGIC = {"and", "or"}
OPS = {"const", "fact", "table", "table_has", "abs", "neg", "ceil_to", "floor_to", "round_to", "not", "in", "if", *_NUMERIC_NARY, *_NUMERIC_BINARY, *_COMPARE, *_LOGIC}


def validate(node: Any, *, tables: Mapping[str, Any] | None = None, _depth: int = 0, _count: list[int] | None = None) -> list[str]:
    """Every problem with a tree, as sentences (an empty list means it is acceptable). Checks shape, operations, sizes and table references."""
    count = _count if _count is not None else [0]
    count[0] += 1
    if count[0] > MAX_NODES:
        return [f"the expression has more than {MAX_NODES} nodes"]
    if _depth > MAX_DEPTH:
        return [f"the expression is nested deeper than {MAX_DEPTH}"]
    if not isinstance(node, Mapping) or "op" not in node:
        return [f"an expression node must be an object with an 'op' (got {type(node).__name__})"]
    op = node["op"]
    if op not in OPS:
        return [f"unknown operation '{op}'"]
    problems: list[str] = []

    def sub(child: Any) -> None:
        problems.extend(validate(child, tables=tables, _depth=_depth + 1, _count=count))

    if op == "const":
        v = node.get("value")
        if not isinstance(v, (int, float, str, list)) and v is not None:
            problems.append("const needs a number, string, boolean or list")
        if isinstance(v, float) and not math.isfinite(v):
            problems.append("const must be finite")
        if isinstance(v, list) and (len(v) > 64 or not all(isinstance(x, (int, float, str)) and not isinstance(x, bool) for x in v)):
            problems.append("a const list holds at most 64 numbers or strings")
    elif op == "fact":
        if not isinstance(node.get("name"), str) or not node["name"] or len(node["name"]) > 80:
            problems.append("fact needs a name")
    elif op in ("table", "table_has"):
        tid = node.get("table")
        if not isinstance(tid, str):
            problems.append(f"{op} needs a table id")
        elif tables is not None and tid not in tables:
            problems.append(f"{op} refers to table '{tid}', which the dataset does not define")
        if op == "table" and not isinstance(node.get("column"), str):
            problems.append("table needs a column")
        elif op == "table" and tables is not None and isinstance(tid, str) and tid in tables and node["column"] not in (tables[tid].get("columns") or {}):
            problems.append(f"table '{tid}' has no column '{node['column']}'")
        if "row" not in node:
            problems.append(f"{op} needs a row expression")
        else:
            sub(node["row"])
    elif op in _NUMERIC_NARY | _LOGIC:
        args = node.get("args")
        if not isinstance(args, list) or not 1 <= len(args) <= 16:
            problems.append(f"{op} needs 1 to 16 'args'")
        else:
            for a in args:
                sub(a)
    elif op in _NUMERIC_BINARY | _COMPARE | {"in"}:
        for key in ("a", "b"):
            if key not in node:
                problems.append(f"{op} needs '{key}'")
            else:
                sub(node[key])
    elif op in ("abs", "neg", "not"):
        if "value" not in node:
            problems.append(f"{op} needs 'value'")
        else:
            sub(node["value"])
    elif op in ("ceil_to", "floor_to", "round_to"):
        if "value" not in node:
            problems.append(f"{op} needs 'value'")
        else:
            sub(node["value"])
        step = node.get("step")
        if isinstance(step, bool) or not isinstance(step, (int, float)) or not step > 0:
            problems.append(f"{op} needs a positive 'step'")
    elif op == "if":
        for key in ("cond", "then", "else"):
            if key not in node:
                problems.append(f"if needs '{key}'")
            else:
                sub(node[key])
    return problems


Fact = Callable[[str], Any]


def evaluate(node: Mapping[str, Any], facts: Mapping[str, Any], tables: Mapping[str, Any] | None = None, trace: Trace | None = None) -> Any:
    """Evaluate a validated tree. ``facts`` maps a fact name to its value (``None`` or absent means unknown)."""
    tables = tables or {}
    trace = trace if trace is not None else Trace()

    def num(v: Any, what: str) -> float:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ExprError(f"{what} is not a number")
        return float(v)

    def go(n: Mapping[str, Any]) -> Any:
        op = n["op"]
        if op == "const":
            return n["value"]
        if op == "fact":
            name = n["name"]
            trace.facts.add(name)
            value = facts.get(name)
            if value is None:
                raise Missing(name)
            return value
        if op in ("table", "table_has"):
            tid = n["table"]
            trace.tables.add(tid)
            row = go(n["row"])
            cells = ((tables.get(tid) or {}).get("rows") or {}).get(str(row))
            if op == "table_has":
                return cells is not None and any(v is not None for v in (cells.get("values") or {}).values())
            if cells is None:
                raise Missing(f"table {tid} row {row}", "the table has no row for this value")
            value = (cells.get("values") or {}).get(n["column"])
            if value is None:
                raise Missing(f"table {tid} row {row}", "the table designates no value here")
            return value
        if op == "add":
            return sum(num(go(a), "add operand") for a in n["args"])
        if op == "mul":
            out = 1.0
            for a in n["args"]:
                out *= num(go(a), "mul operand")
            return out
        if op == "min":
            return min(num(go(a), "min operand") for a in n["args"])
        if op == "max":
            return max(num(go(a), "max operand") for a in n["args"])
        if op == "sub":
            return num(go(n["a"]), "sub operand") - num(go(n["b"]), "sub operand")
        if op == "div":
            d = num(go(n["b"]), "div divisor")
            if abs(d) < 1e-15:
                raise ExprError("division by zero")
            return num(go(n["a"]), "div operand") / d
        if op == "abs":
            return abs(num(go(n["value"]), "abs operand"))
        if op == "neg":
            return -num(go(n["value"]), "neg operand")
        if op in ("ceil_to", "floor_to", "round_to"):
            v, step = num(go(n["value"]), f"{op} operand"), float(n["step"])
            q = v / step
            q = math.ceil(q - EPS) if op == "ceil_to" else math.floor(q + EPS) if op == "floor_to" else round(q)
            return round(q * step, 12)
        if op == "not":
            return not bool(go(n["value"]))
        if op == "and":
            return all(bool(go(a)) for a in n["args"])
        if op == "or":
            return any(bool(go(a)) for a in n["args"])
        if op == "in":
            return go(n["a"]) in (go(n["b"]) or [])
        if op == "if":
            return go(n["then"]) if bool(go(n["cond"])) else go(n["else"])
        a, b = go(n["a"]), go(n["b"])                                       # comparisons
        if op == "eq":
            return a == b
        if op == "ne":
            return a != b
        x, y = num(a, "comparison operand"), num(b, "comparison operand")
        return {"lt": x < y - EPS, "le": x <= y + EPS, "gt": x > y + EPS, "ge": x >= y - EPS}[op]

    return go(node)


def facts_in(node: Any) -> set[str]:
    """The fact names a tree mentions (for documentation and for "what is missing")."""
    out: set[str] = set()
    if isinstance(node, Mapping):
        if node.get("op") == "fact" and isinstance(node.get("name"), str):
            out.add(node["name"])
        for v in node.values():
            out |= facts_in(v)
    elif isinstance(node, list):
        for v in node:
            out |= facts_in(v)
    return out
