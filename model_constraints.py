"""Geometric constraints between model elements, solved by SLAI's neutral constraint engine and applied back through ordinary element edits.

What is constrained is the *plan geometry* of elements that have one: a wall or a beam is a segment (its start and end, in plan), a column is a point. The constraint
kinds are the engine's: coincident, distance, fixed, parallel, perpendicular, equal_length, angle, horizontal, vertical, point_on_line. Elevations, heights and sections are
not constrained (they are parameters, see the families and the parameter graph). The engine itself accepts 3D coordinates; SPADRA's elements are plan-based with levels, so
what is wired here is plan-level constraining of 3D elements, not constraints between solids in space.

  add / delete     store a constraint in the project (``manifest["model_constraints"]``). A reference names an element and, for a segment, a ``part`` (``start`` / ``end``).
  analyze          read-only: solve a copy and report status, degrees of freedom (overall and per element), redundant and conflicting constraints, unresolved references.
  solve            analyze, refuse unless the system is solvable and consistent, move the elements through their own edit commands (a wall is stretched, so its joins and
                   hosted openings follow), then re-measure the real elements and refuse (rolling everything back) if any constraint is not met to ``tolerance``.

``fixed`` lists elements that must not move. Units at the interface are the project's: distances in mm, angles in degrees. The analysis is local (first-order degrees of
freedom at the solution found) and the solver returns the solution nearest the drawing, not a global one; both are stated in every result."""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from src.utils.spatial_formats.common import constraint_engine as CE
from src.utils.spatial_formats.common.errors import ValidationError as EngineError

from ..tools.errors import BadRequest, Conflict, NotFound
from ..tools.validation import new_id, number, text
from . import elements as E

KEY = "model_constraints"
KINDS = ("coincident", "distance", "fixed", "parallel", "perpendicular", "equal_length", "angle", "horizontal", "vertical", "point_on_line")
MAX_CONSTRAINTS = 400
MOVE_EPS = 1e-9                                           # m: a coordinate that moved less than this is left alone
DEFAULT_TOLERANCE = 1e-7                                  # m (or rad): what "satisfied" means when the real elements are measured again


def constraints(project: Any) -> list[dict[str, Any]]:
    return project.manifest.setdefault(KEY, [])


def _kind_of(project: Any, oid: str) -> str | None:
    """``segment`` for a wall or beam, ``point`` for a column, ``None`` for anything else (including a missing element)."""
    if not E.alive(project, oid):
        return None
    meta = E.meta_of(project, oid)
    cat = E.m(meta, "category")
    if cat == "wall":
        return "segment"
    if cat == "structural":
        return "point" if E.is_column(meta) else "segment"
    return None


def entity_of(project: Any, oid: str) -> dict[str, Any]:
    kind = _kind_of(project, oid)
    meta = E.meta_of(project, oid)
    if kind == "segment":
        s, e = E.m(meta, "start"), E.m(meta, "end")
        return {"kind": "segment", "coords": [float(s[0]), float(s[1]), float(e[0]), float(e[1])], "name": project.model.get(oid)["name"]}
    x, y = E.m(meta, "point")
    return {"kind": "point", "coords": [float(x), float(y)], "name": project.model.get(oid)["name"]}


def _ref(project: Any, raw: Any, label: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or "element" not in raw:
        raise BadRequest(f"{label} must be {{element, part}}: the element's id and, for a wall or beam end, 'start' or 'end'")
    oid = text(raw["element"], "element", 80)
    kind = _kind_of(project, oid)
    if kind is None:
        raise NotFound(f"{oid} is not a wall, beam or column of this project", hint="Only walls, beams and columns have plan geometry to constrain")
    part = raw.get("part")
    if part not in (None, "start", "end"):
        raise BadRequest("part must be 'start' or 'end' (or left out for the whole element)")
    if part is not None and kind == "point":
        raise BadRequest("A column is a point: it has no start or end")
    return {"element": oid, "part": part}


def _engine_ref(project: Any, ref: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"entity": ref["element"]}
    if ref.get("part"):
        out["part"] = ref["part"]
    if E.alive(project, ref["element"]) and _kind_of(project, ref["element"]):
        out["fingerprint"] = CE.fingerprint(entity_of(project, ref["element"]))
    return out


def _value(kind: str, value: Any) -> Any:
    """The constraint's value from the person's units (mm, degrees) to the engine's (m, radians)."""
    if kind == "distance":
        return number(value, "distance", 0.001, 1e9) / 1000.0
    if kind == "angle":
        return math.radians(number(value, "angle", 0, 180))
    if kind == "fixed":
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise BadRequest("fixed needs the point [x, y] in mm")
        return [number(value[0], "x", -1e9, 1e9) / 1000.0, number(value[1], "y", -1e9, 1e9) / 1000.0]
    if value is not None:
        raise BadRequest(f"A {kind} constraint takes no value")
    return None


def _check_shape(project: Any, kind: str, refs: Sequence[Mapping[str, Any]]) -> None:
    """The engine's own arity and entity-kind rules, applied before anything is stored so a bad constraint is refused at the door."""
    if len(refs) != CE.ARITY[kind]:
        raise BadRequest(f"A {kind} constraint takes {CE.ARITY[kind]} reference(s), not {len(refs)}")
    for i, r in enumerate(refs):
        entity_kind = _kind_of(project, r["element"])
        point_like = i in CE.POINT_LIKE.get(kind, ())
        segment = i in CE.SEGMENTS.get(kind, ())
        if point_like and not (entity_kind == "point" or (entity_kind == "segment" and r["part"])):
            raise BadRequest(f"Reference {i + 1} of a {kind} constraint must be a column, or the 'start' or 'end' of a wall or beam")
        if segment and not point_like and not (entity_kind == "segment" and not r["part"]):
            raise BadRequest(f"Reference {i + 1} of a {kind} constraint must be a whole wall or beam")


def add(project: Any, p: Mapping[str, Any]) -> dict[str, Any]:
    kind = p.get("kind")
    if kind not in KINDS:
        raise BadRequest(f"kind must be one of {', '.join(KINDS)}")
    raw = p.get("refs")
    if not isinstance(raw, (list, tuple)):
        raise BadRequest("refs must be a list of {element, part}")
    refs = [_ref(project, r, f"Reference {i + 1}") for i, r in enumerate(raw)]
    _check_shape(project, kind, refs)
    if kind == "coincident" and refs[0] == refs[1]:
        raise BadRequest("A point cannot be coincident with itself")
    if kind in ("distance", "angle", "fixed") and p.get("value") is None:
        raise BadRequest(f"A {kind} constraint needs a value")
    value = _value(kind, p.get("value"))
    stored = constraints(project)
    if len(stored) >= MAX_CONSTRAINTS:
        raise BadRequest(f"A project holds at most {MAX_CONSTRAINTS} model constraints")
    cid = new_id()
    stored.append({"id": cid, "kind": kind, "refs": refs, "value": value})
    return {"id": cid, "kind": kind}


def delete(project: Any, cid: Any) -> dict[str, Any]:
    cid = text(cid, "id", 80)
    stored = constraints(project)
    keep = [c for c in stored if c["id"] != cid]
    if len(keep) == len(stored):
        raise NotFound(f"There is no model constraint {cid}")
    project.manifest[KEY] = keep
    return {"id": cid}


def describe(project: Any) -> list[dict[str, Any]]:
    """The stored constraints with their values in the person's units, and whether every reference still names a wall, beam or column."""
    out = []
    for c in constraints(project):
        value = c["value"]
        shown = value * 1000.0 if c["kind"] == "distance" else math.degrees(value) if c["kind"] == "angle" else [v * 1000.0 for v in value] if c["kind"] == "fixed" else None
        out.append({"id": c["id"], "kind": c["kind"], "refs": c["refs"], "value": shown, "unit": {"distance": "mm", "angle": "deg", "fixed": "mm"}.get(c["kind"], ""),
                    "valid": all(_kind_of(project, r["element"]) is not None for r in c["refs"])})
    return out


def _system(project: Any, fixed: Sequence[str]) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    stored = constraints(project)
    engine_constraints = [{"kind": c["kind"], "refs": [_engine_ref(project, r) for r in c["refs"]], **({"value": c["value"]} if c["value"] is not None else {})} for c in stored]
    ids = {r["element"] for c in stored for r in c["refs"]}
    entities = {oid: entity_of(project, oid) for oid in sorted(ids) if _kind_of(project, oid)}
    fixed_ids = []
    for f in fixed:
        f = text(f, "fixed", 80)
        if f not in entities:
            if _kind_of(project, f) is None:
                raise NotFound(f"{f} is not a wall, beam or column of this project")
            continue                                                       # a fixed element no constraint mentions has nothing to hold still
        fixed_ids.append(f)
    return entities, engine_constraints, fixed_ids


def analyze(project: Any, fixed: Sequence[str] = (), *, tolerance: float = DEFAULT_TOLERANCE) -> dict[str, Any]:
    """Read-only analysis of the stored constraints: nothing in the project changes."""
    if not constraints(project):
        return {"status": "none", "constraints": 0, "dof": None, "scope": "no model constraints are stored"}
    entities, engine_constraints, fixed_ids = _system(project, fixed)
    try:
        result = CE.analyze(entities, engine_constraints, fixed=fixed_ids, tolerance=max(tolerance, 1e-10))
    except EngineError as exc:
        raise BadRequest(f"The constraints cannot be analysed: {exc}") from exc
    stored = constraints(project)
    result["constraint_ids"] = [c["id"] for c in stored]
    for row in result.get("constraints", []):
        row["id"] = stored[row["index"]]["id"] if row["index"] < len(stored) else None
    result["fixed"] = fixed_ids
    result["moves"] = _moves(entities, result.get("solution") or {})
    return result


def _moves(entities: Mapping[str, Mapping[str, Any]], solution: Mapping[str, Sequence[float]]) -> dict[str, dict[str, Any]]:
    out = {}
    for oid, coords in solution.items():
        before = entities[oid]["coords"]
        if max(abs(a - b) for a, b in zip(before, coords)) > MOVE_EPS:
            out[oid] = {"from": list(before), "to": list(coords), "distance": max(math.dist(before[i:i + 2], coords[i:i + 2]) for i in range(0, len(before), 2))}
    return out


def _apply(project: Any, oid: str, coords: Sequence[float]) -> None:
    kind = _kind_of(project, oid)
    if kind == "point":
        E.set_property(project, oid, "x", coords[0] * 1000.0)
        E.set_property(project, oid, "y", coords[1] * 1000.0)
    elif E.m(E.meta_of(project, oid), "category") == "wall":
        E.stretch_wall(project, oid, (coords[0], coords[1]), (coords[2], coords[3]))
    else:
        for key, v in zip(("start_x", "start_y", "end_x", "end_y"), coords):
            E.set_property(project, oid, key, v * 1000.0)


def solve(project: Any, p: Mapping[str, Any]) -> dict[str, Any]:
    """Move the constrained elements so the stored constraints hold, keeping ``fixed`` elements where they are. All or nothing: refused, with the reason, when the system is
    conflicting, did not converge, collapses a segment, or has an unresolved reference; and rolled back if the real elements, measured again, do not meet the constraints."""
    fixed = p.get("fixed") or []
    if not isinstance(fixed, (list, tuple)):
        raise BadRequest("fixed must be a list of element ids")
    tolerance = number(p.get("tolerance", DEFAULT_TOLERANCE * 1000), "tolerance", 1e-6, 1.0) / 1000.0
    result = analyze(project, fixed, tolerance=tolerance)
    if result["status"] == "none":
        raise BadRequest("There are no model constraints to solve", hint="Add one first")
    problems = []
    if result["unresolved"]:
        problems.append("unresolved references: " + "; ".join(str(u.get("message", u)) for u in result["unresolved"]))
    if result["conflicting"] or result["status"] == "conflicting":
        problems.append("the constraints conflict" + (f" (constraint {', '.join(str(i) for i in result['conflicting'])})" if result["conflicting"] else ""))
    if not result["converged"]:
        problems.append("the solver did not converge")
    if result["degenerate"]:
        problems.append("it can only be satisfied by collapsing " + ", ".join(result["degenerate"]) + " to a point")
    if problems:
        raise Conflict("The constraints cannot be solved: " + "; ".join(problems), hint="Remove or relax a constraint, or free an element", details={"analysis": result})
    moved = result["moves"]
    for oid in fixed:
        if oid in moved:
            raise Conflict(f"The constraints cannot be met without moving {oid}, which was kept fixed", details={"analysis": result})
    for oid, move in moved.items():
        _apply(project, oid, move["to"])
    after = analyze(project, fixed, tolerance=tolerance)                    # measure the real elements again: this is the evidence, not the solver's own report
    worst = max((c["residual"] for c in after.get("constraints", [])), default=0.0)
    if worst > max(tolerance, 1e-6):
        raise Conflict(f"After moving the elements a constraint is still off by {worst * 1000:.6f}", details={"analysis": after})
    return {"status": result["status"], "dof": result["dof"], "moved": {k: {"distance_mm": v["distance"] * 1000.0, "to": v["to"]} for k, v in moved.items()},
            "max_residual_after": worst, "constraints": len(constraints(project)), "free_entities": result["free_entities"], "scope": result["scope"]}
