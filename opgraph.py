"""The typed parametric operation graph: contracts, deterministic ordering, cycle detection, incremental planning, change propagation.

This module is the *pure* half of the feature; it knows nothing about a project, an AI model, or the kernel. ``design_spec.py`` supplies the project-bound half (the
adapters that run an operation through SPADRA's own commands and the SGEO modifier kernel). Parameters are not duplicated here: an operation's numeric
inputs are either literals or ``"=expression"`` formulas that ``design_spec`` compiles into the SGEO :class:`ParameterGraph`, which stays the single authority
for formulas, units, ranges and parameter cycles. What this module owns is the *operation* dependency graph.

An operation is a plain, JSON-serialisable mapping::

    {"id": "slab", "type": "extrude", "inputs": {"profile": "outline"}, "params": {"depth": "=thickness", "base": 0.0}}

``inputs`` maps a *role* to the id of another operation (the producer); the contract of the operation's type says which roles exist, what each must produce
and which parameters are accepted (type, unit, bounds). The same contract table drives validation, the capability report the UI shows, and the AI-facing schema,
so there is one definition of what an operation means.
"""
from __future__ import annotations

import hashlib
import heapq
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

SEVERITIES = ("error", "warning", "info")
STATUSES = ("pending", "ok", "failed", "skipped", "unavailable", "drifted")


def diag(code: str, severity: str, message: str, *, op: str | None = None, hint: str | None = None, **extra: Any) -> dict[str, Any]:
    """A diagnostic: a stable ``code`` for programs, a ``message`` for people, the operation it is about, and a hint at what to do."""
    out: dict[str, Any] = {"code": code, "severity": severity, "message": message}
    if op is not None:
        out["op"] = op
    if hint:
        out["hint"] = hint
    out.update(extra)
    return out


@dataclass(frozen=True)
class Param:
    """One typed operation parameter. ``dim`` is an SGEO parameter type (``length``, ``angle``, ``number``, ``integer``, ``boolean``) or ``text``/``choice``/``vector``/
    ``points``; numbers are SI (metres, radians) so that one formula table serves every operation."""
    dim: str
    required: bool = False
    default: Any = None
    choices: tuple = ()
    min: float | None = None
    max: float | None = None
    size: int | None = None
    doc: str = ""

    def describe(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.dim, "required": self.required}
        for key in ("default", "min", "max", "size"):
            if getattr(self, key) is not None:
                out[key] = getattr(self, key)
        if self.choices:
            out["choices"] = list(self.choices)
        if self.doc:
            out["doc"] = self.doc
        return out


@dataclass(frozen=True)
class Contract:
    """What an operation type promises. ``inputs`` maps a role to the kind of result it consumes (``sketch`` or ``solid``); ``produces`` is what it yields (``sketch``,
    ``solid``, ``edit`` for an operation that changes its target in place, or ``none``)."""
    type: str
    label: str
    inputs: Mapping[str, str]
    params: Mapping[str, Param]
    produces: str
    pre: tuple[str, ...] = ()
    post: tuple[str, ...] = ()
    exactness: str = "exact"
    needs: tuple[str, ...] = ()                               # capability names (see design_spec.capabilities) the operation depends on
    optional_inputs: tuple[str, ...] = ()
    doc: str = ""

    def describe(self) -> dict[str, Any]:
        return {"type": self.type, "label": self.label, "inputs": dict(self.inputs), "optional_inputs": list(self.optional_inputs), "produces": self.produces,
                "params": {k: v.describe() for k, v in self.params.items()}, "preconditions": list(self.pre), "postconditions": list(self.post),
                "exactness": self.exactness, "needs": list(self.needs), "doc": self.doc}


P = Param
LEN, ANG, NUM, INT, BOOL = "length", "angle", "number", "integer", "boolean"

CONTRACTS: dict[str, Contract] = {c.type: c for c in (
    Contract("sketch", "Constrained sketch", {}, {"kind": P("choice", True, choices=("polygon", "rectangle", "circle")), "points": P("points"), "center": P("vector", size=2),
                                                  "width": P(LEN, min=1e-6), "height": P(LEN, min=1e-6), "radius": P(LEN, min=1e-6)},
             "sketch", pre=("every constraint reference resolves", "constraints are of kinds the engine supports"),
             post=("the solved outline is simple and non-degenerate",), doc="A 2D outline. Its dimensions are parameters; its constraints are solved by the shared engine."),
    Contract("extrude", "Extrusion", {"profile": "sketch"}, {"depth": P(LEN, True, min=0.001, max=500), "base": P(LEN, default=0.0, min=-500, max=500),
                                                             "void": P(BOOL, default=False), "material": P("text", default="Concrete"), "level": P("text")},
             "solid", pre=("the profile is a simple polygon",), post=("the solid is closed and has positive volume",),
             doc="A prism of the profile: an SPADRA extrusion (parametric, with its profile/base/depth kept on the element), or a void when `void` is true."),
    Contract("box", "Analytic box", {}, {"width": P(LEN, True, min=1e-6), "depth": P(LEN, True, min=1e-6), "height": P(LEN, True, min=1e-6),
                                         "center": P("vector", default=(0.0, 0.0, 0.0), size=3), "name": P("text")},
             "solid", post=("the solid is closed and has positive volume",), exactness="exact (analytic)",
             doc="An axis-aligned analytic box. Exact Booleans, splits and chamfers need analytic operands, so a design that uses them starts from boxes."),
    Contract("wall", "Wall", {}, {"start": P("vector", True, size=2), "end": P("vector", True, size=2), "height": P(LEN, default=3.0, min=0.01, max=500),
                                  "thickness": P(LEN, default=0.2, min=0.001, max=5), "level": P("text"), "material": P("text", default="Concrete")},
             "solid", pre=("start and end differ",), post=("the wall is closed and has positive volume",), doc="A BIM wall (SPADRA's own wall command); start/end in metres."),
    Contract("sweep", "Sweep", {"path": "sketch"}, {"width": P(LEN, True, min=0.001, max=50), "height": P(LEN, True, min=0.001, max=50),
                                                    "shape": P("choice", default="rectangle", choices=("rectangle", "ellipse")), "level": P("text")},
             "solid", pre=("the path is an open polyline",), exactness="exact for polygonal paths; ellipse sections are polygonal approximations", doc="A rectangular or elliptical section swept along the plan path."),
    Contract("boolean", "Boolean", {"target": "solid", "operand": "solid"}, {"operation": P("choice", True, choices=("union", "subtract", "intersect"))},
             "edit", pre=("target and operand are distinct closed solids",), post=("the result is non-empty",), exactness="exact for aligned analytic boxes; numerical (Manifold3D) otherwise",
             needs=("modifier_kernel",), doc="A non-destructive SGEO Boolean on the target; the operand is read-only."),
    Contract("split", "Split / trim by plane", {"target": "solid"}, {"origin": P("vector", size=3), "normal": P("vector", True, size=3), "side": P("choice", default="positive", choices=("positive", "negative"))},
             "edit", pre=("the normal is not zero",), post=("the result is non-empty",), exactness="exact for axis-aligned analytic boxes; numerical for meshes",
             needs=("modifier_kernel",), optional_inputs=("face",), doc="Keeps one side of a plane (a trim). The plane may be given by a semantic face reference instead of origin/normal."),
    Contract("chamfer", "Chamfer", {"target": "solid"}, {"amount": P(LEN, True, min=1e-6), "depth": P(LEN, min=1e-6), "segments": P(INT, default=1, min=1, max=64)},
             "edit", pre=("the target is an analytic box",), post=("the result is non-empty",), exactness="exact for one segment on an analytic box", needs=("modifier_kernel",),
             doc="Chamfers all edges of an analytic box."),
    Contract("bend", "Bend", {"target": "solid"}, {"axis": P("choice", True, choices=("x", "y", "z")), "angle": P(ANG, True), "start": P(LEN, True), "end": P(LEN, True),
                                                   "segments": P(INT, default=16, min=2, max=256)},
             "edit", post=("the result is non-empty",), exactness="display-only numerical approximation", needs=("modifier_kernel", "mesh_backend"), doc="Bends the target along an axis."),
    Contract("offset", "Offset", {"target": "solid"}, {"distance": P(LEN, True)}, "none", exactness="unsupported for solids",
             needs=("solid_offset",), doc="Face offset of a solid. SGEO offers it for analytic boxes only (a separate object); sketches use the `offset` constraint instead."),
    Contract("void", "Void (cut)", {"profile": "sketch"}, {"depth": P(LEN, True, min=0.001, max=500), "base": P(LEN, default=0.0, min=-500, max=500), "level": P("text")},
             "solid", pre=("the profile is a simple polygon",), post=("the void is closed and has positive volume",), optional_inputs=("hosts",),
             doc="An extrusion that cuts the solids it overlaps (SPADRA's void). It is cut into its `hosts` (other operations) when they are given."),
)}

# Types the vocabulary names that the graph cannot run here, with the reason: they are reported as unsupported, never approximated by something else.
UNSUPPORTED_TYPES = {
    "bend_edges": "edge-selective bends need persistent edge names, which SGEO does not provide",
    "fillet3d": "SGEO has no 3D fillet kernel",
    "loft": "SGEO has no loft kernel",
}


def contract_table() -> dict[str, Any]:
    return {"operations": [c.describe() for c in CONTRACTS.values()], "unsupported": [{"type": t, "reason": r} for t, r in UNSUPPORTED_TYPES.items()]}


# ----------------------------------------------------------------------------------------------------------------------- hashing
def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def digest(*parts: Any) -> str:
    h = hashlib.sha1()
    for part in parts:
        h.update(canonical(part).encode())
        h.update(b"\x1f")
    return h.hexdigest()[:16]


# ----------------------------------------------------------------------------------------------------------------------- structure
def producers(op: Mapping[str, Any]) -> list[str]:
    """The ids an operation reads: its inputs, plus the references inside ``face`` and ``hosts``."""
    ids = [v for v in (op.get("inputs") or {}).values() if isinstance(v, str)]
    face = op.get("face")
    if isinstance(face, Mapping) and isinstance(face.get("op"), str):
        ids.append(face["op"])
    for h in op.get("hosts") or []:
        if isinstance(h, str):
            ids.append(h)
    return list(dict.fromkeys(ids))


def order(operations: Sequence[Mapping[str, Any]]) -> tuple[list[str], list[str] | None]:
    """``(order, cycle)``: a deterministic dependency order (ties keep declaration order) and, if the graph is cyclic, the ids on one cycle (the order is then partial)."""
    index = {op["id"]: i for i, op in enumerate(operations)}
    deps = {op["id"]: [p for p in producers(op) if p in index] for op in operations}
    pending = {i: len(set(d)) for i, d in deps.items()}
    users: dict[str, list[str]] = {i: [] for i in index}
    for i, ds in deps.items():
        for d in set(ds):
            users[d].append(i)
    ready = [(index[i], i) for i, c in pending.items() if c == 0]
    heapq.heapify(ready)
    out: list[str] = []
    while ready:
        _, i = heapq.heappop(ready)
        out.append(i)
        for u in users[i]:
            pending[u] -= 1
            if pending[u] == 0:
                heapq.heappush(ready, (index[u], u))
    if len(out) == len(index):
        return out, None
    left = {i for i in index if i not in set(out)}
    start = min(left, key=lambda i: index[i])
    path, seen = [start], {start: 0}
    cur = start
    while True:                                                                 # follow dependencies inside the leftover set until one repeats: that is a cycle
        nxt = next((d for d in deps[cur] if d in left), None)
        if nxt is None:
            return out, sorted(left, key=lambda i: index[i])
        if nxt in seen:
            return out, path[seen[nxt]:]
        seen[nxt] = len(path)
        path.append(nxt)
        cur = nxt


def downstream(operations: Sequence[Mapping[str, Any]], changed: Sequence[str]) -> list[str]:
    """Every operation that (transitively) reads one of ``changed``, in dependency order, not including ``changed`` themselves."""
    ids, _ = order(operations)
    by_id = {op["id"]: op for op in operations}
    dirty = set(changed)
    out = []
    for i in ids:
        if i in changed:
            continue
        if any(p in dirty for p in producers(by_id[i])):
            dirty.add(i)
            out.append(i)
    return out


# ----------------------------------------------------------------------------------------------------------------------- validation
def _kind_of(op: Mapping[str, Any]) -> str | None:
    c = CONTRACTS.get(op.get("type"))
    return None if c is None else ("sketch" if c.produces == "sketch" else "solid")


def validate(operations: Any, *, parameter_names: Sequence[str] = ()) -> tuple[list[dict[str, Any]], list[str]]:
    """Static checks of an operation list against the contracts. Returns ``(diagnostics, order)``; nothing here touches a project."""
    out: list[dict[str, Any]] = []
    if not isinstance(operations, list):
        return [diag("ops.shape", "error", "operations must be a list")], []
    seen: set[str] = set()
    ok_ops: list[Mapping[str, Any]] = []
    for n, op in enumerate(operations):
        if not isinstance(op, Mapping) or not isinstance(op.get("id"), str) or not op["id"].isidentifier():
            out.append(diag("op.id", "error", f"operation {n} needs an identifier-like id"))
            continue
        if op["id"] in seen:
            out.append(diag("op.duplicate", "error", f"operation id '{op['id']}' is used twice", op=op["id"]))
            continue
        seen.add(op["id"])
        ok_ops.append(op)
        if op.get("type") in UNSUPPORTED_TYPES:
            out.append(diag("op.unsupported", "error", f"'{op['type']}' is not supported: {UNSUPPORTED_TYPES[op['type']]}", op=op["id"]))
            continue
        contract = CONTRACTS.get(op.get("type"))
        if contract is None:
            out.append(diag("op.type", "error", f"unknown operation type '{op.get('type')}'", op=op["id"], hint="types: " + ", ".join(CONTRACTS)))
            continue
        extra = set(op) - {"id", "type", "inputs", "params", "face", "hosts", "constraints", "name", "note", "tolerance", "closed", "placement", "circles", "require"}
        if extra:
            out.append(diag("op.field", "error", "unknown operation field(s): " + ", ".join(sorted(extra)), op=op["id"]))
        params = op.get("params") or {}
        if not isinstance(params, Mapping):
            out.append(diag("op.params", "error", "params must be an object", op=op["id"]))
            continue
        for key in params:
            if key not in contract.params:
                out.append(diag("param.unknown", "error", f"'{key}' is not a parameter of {contract.type}", op=op["id"], hint="parameters: " + ", ".join(contract.params)))
        for key, spec in contract.params.items():
            if spec.required and key not in params and not (op["type"] == "split" and key in ("origin", "normal") and op.get("face")) and not (
                    op["type"] == "sketch" and key in ("points", "center", "width", "height", "radius")):
                out.append(diag("param.missing", "error", f"{contract.type} needs '{key}'", op=op["id"]))
        for key, value in params.items():
            spec = contract.params.get(key)
            if spec is None:
                continue
            if isinstance(value, str) and value.startswith("="):
                if spec.dim not in (LEN, ANG, NUM, INT, BOOL):
                    out.append(diag("param.formula", "error", f"'{key}' ({spec.dim}) cannot be a formula", op=op["id"]))
                continue
            if spec.dim in (LEN, ANG, NUM, INT):
                if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or abs(value) == float("inf"):
                    out.append(diag("param.type", "error", f"'{key}' must be a finite number or a formula", op=op["id"]))
                elif spec.dim == INT and int(value) != value:
                    out.append(diag("param.type", "error", f"'{key}' must be an integer", op=op["id"]))
                elif spec.min is not None and value < spec.min or spec.max is not None and value > spec.max:
                    out.append(diag("param.range", "error", f"'{key}' = {value} is outside {spec.min}..{spec.max}", op=op["id"]))
            elif spec.dim == "choice" and value not in spec.choices:
                out.append(diag("param.choice", "error", f"'{key}' must be one of {', '.join(spec.choices)}", op=op["id"]))
            elif spec.dim == BOOL and type(value) is not bool:
                out.append(diag("param.type", "error", f"'{key}' must be true or false", op=op["id"]))
            elif spec.dim == "vector" and not (isinstance(value, (list, tuple)) and spec.size in (None, len(value)) and all(
                    isinstance(v, (int, float)) and not isinstance(v, bool) or isinstance(v, str) and v.startswith("=") for v in value)):
                out.append(diag("param.type", "error", f"'{key}' must be a list of {spec.size} numbers or formulas", op=op["id"]))
    index = {op["id"]: op for op in ok_ops}
    for op in ok_ops:
        contract = CONTRACTS.get(op.get("type"))
        if contract is None:
            continue
        inputs = op.get("inputs") or {}
        if not isinstance(inputs, Mapping):
            out.append(diag("op.inputs", "error", "inputs must be an object", op=op["id"]))
            continue
        for role in inputs:
            if role not in contract.inputs:
                out.append(diag("input.role", "error", f"'{role}' is not an input of {contract.type}", op=op["id"], hint="inputs: " + (", ".join(contract.inputs) or "none")))
        for role, kind in contract.inputs.items():
            if role not in inputs and role not in contract.optional_inputs:
                out.append(diag("input.missing", "error", f"{contract.type} needs input '{role}'", op=op["id"]))
                continue
            ref = inputs.get(role)
            if ref is None:
                continue
            if ref not in index:
                out.append(diag("input.unknown", "error", f"input '{role}' refers to the unknown operation '{ref}'", op=op["id"]))
            elif ref == op["id"]:
                out.append(diag("input.self", "error", "an operation cannot read itself", op=op["id"]))
            elif _kind_of(index[ref]) != kind and CONTRACTS.get(index[ref].get("type")) is not None:
                out.append(diag("input.kind", "error", f"input '{role}' needs a {kind}, but '{ref}' is a {_kind_of(index[ref])}", op=op["id"]))
        for ref in producers(op):
            if ref not in index:
                out.append(diag("ref.unknown", "error", f"refers to the unknown operation '{ref}'", op=op["id"]))
    ids, cycle = order(ok_ops)
    if cycle:
        out.append(diag("graph.cycle", "error", "the operations depend on each other in a cycle: " + " → ".join(cycle + cycle[:1]), cycle=cycle,
                        hint="remove one of the inputs along the cycle"))
    return out, ids
