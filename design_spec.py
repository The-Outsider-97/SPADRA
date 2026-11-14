"""Constraint-aware design specifications: typed parameters, constrained sketches and a typed operation graph, executed deterministically through SPADRA.

A *design specification* is data. It names typed parameters (the SGEO :class:`ParameterGraph` is the only place formulas, units, ranges and parameter cycles are
evaluated), constrained sketches (solved by the shared constraint engine), a typed operation graph (``opgraph.py``) whose operations run through the commands and
the SGEO modifier kernel the project already has, and the checks the result must satisfy. Running a specification needs no AI model: the same input gives the same
elements. A model may *propose* a specification or a repair; the host validates it on a throw-away copy first and only a command (one transaction, with Undo)
commits.

    {"id": "slab", "name": "Slab", "parameters": {"width": {"type": "length", "default": 4.0, "min": 0.5, "max": 20}, "t": {"type": "length", "default": 0.2}},
     "operations": [{"id": "outline", "type": "sketch", "params": {"kind": "rectangle", "width": "=width", "height": 3.0}},
                    {"id": "body", "type": "extrude", "inputs": {"profile": "outline"}, "params": {"depth": "=t"}}],
     "expect": [{"object": "body", "check": "size", "value": [4.0, 3.0, 0.2]}], "outputs": ["body"]}

State lives in ``project.manifest["design_specs"]`` (so Undo, save and load keep it) with a portable copy of the definitions in the SGEO document metadata
(``spadra:design_specs``); every element a specification creates carries ``spadra:spec`` provenance. Lengths are metres and angles radians throughout.
"""
from __future__ import annotations

import copy
import importlib.util
import math
import time
from typing import Any, Mapping, Sequence

from ..tools.errors import BadRequest, Conflict, NotFound
from ..tools.validation import new_id
from src.utils.spatial_formats.common import constraint_engine as CE
from src.utils.spatial_formats.common.errors import DependencyUnavailableError, SpatialFormatError
from src.utils.spatial_formats.sgeo.parameters import ParameterGraph

from . import elements as E
from . import forms as FX
from . import impact as IM
from . import opgraph as OG
from .opgraph import CONTRACTS, diag
from .utils.spadra_helpers import MM_PER

KEY = "design_specs"
MIRROR = "spadra:design_specs"
MAX_SPECS, MAX_OPERATIONS, MAX_PARAMETERS, MAX_EXPECT = 50, 300, 400, 300
DEFAULT_TOL = 1e-6                                       # m: the absolute tolerance the project's own modifier evaluation already uses (see model.modifier)
CIRCLE_SIDES = 48
SPEC_FIELDS = {"id", "name", "description", "parameters", "operations", "expect", "outputs", "values", "on_drift"}


# ----------------------------------------------------------------------------------------------------------------------- store
def store(project: Any) -> dict[str, Any]:
    return project.manifest.setdefault(KEY, {})


def entry(project: Any, sid: Any) -> dict[str, Any]:
    e = store(project).get(sid)
    if e is None:
        raise NotFound(f"There is no design specification '{sid}'")
    return e


def list_specs(project: Any) -> list[dict[str, Any]]:
    return [{"id": sid, "name": e["spec"].get("name", sid), "revision": e["revision"], "operations": len(e["spec"].get("operations", [])),
             "parameters": len(e["spec"].get("parameters", {})), "status": e.get("status", "never run"), "ok": e.get("ok")} for sid, e in sorted(store(project).items())]


def capabilities(project: Any | None = None) -> dict[str, Any]:
    """What this installation can run, probed rather than assumed."""
    # Older SLAI checkouts expose KINDS and solve() but not the optional
    # capabilities() convenience function. Report only what that solver declares.
    constraint_probe = getattr(CE, "capabilities", None)
    constraints = constraint_probe() if callable(constraint_probe) else {
        "supported": [{"kind": kind} for kind in getattr(CE, "KINDS", ())],
        "unsupported": [{"kind": "curvature_continuous", "reason": "Not supported by this SLAI constraint solver"}],
    }
    out: dict[str, Any] = {"constraints": constraints, "operations": OG.contract_table()}
    caps: dict[str, Any] = {}
    try:
        import numpy  # noqa: F401
        caps["constraint_solver"] = {"available": True}
    except ImportError:
        caps["constraint_solver"] = {"available": False, "reason": "numpy is not installed"}
    try:
        from src.utils.spatial_formats.sgeo import modifier_ops  # noqa: F401
        caps["modifier_kernel"] = {"available": True}
    except Exception as exc:
        caps["modifier_kernel"] = {"available": False, "reason": str(exc)}
    if importlib.util.find_spec("trimesh") is not None:         # probed, not imported: SPADRA declares no trimesh dependency of its own, the SGEO kernel uses it when present
        caps["mesh_backend"] = {"available": True, "note": "needed for Booleans on non-box meshes, mesh splits and bends"}
    else:
        caps["mesh_backend"] = {"available": False, "reason": "trimesh is not installed: Booleans, splits and bends on meshes (extrusions, walls) cannot run; analytic boxes still can"}
    caps["solid_offset"] = {"available": False, "reason": "SGEO offers a face offset for analytic boxes only, as a separate object; the graph does not wrap it"}
    out["capabilities"] = caps
    return out


# ----------------------------------------------------------------------------------------------------------------------- parameters
def _dim_of(op_type: str, key: str, spec: OG.Param) -> str:
    if spec.dim == "vector":
        return "number" if key == "normal" else "length"
    return spec.dim


def _is_formula(v: Any) -> bool:
    return isinstance(v, str) and v.startswith("=")


def _hidden(op_id: str, key: str, *idx: int) -> str:
    return "_".join(["_" + op_id, key, *map(str, idx)])


def _constraint_dim(kind: str) -> str:
    return "angle" if kind == "angle" else "length"


def _formula_slots(op: Mapping[str, Any]) -> list[tuple[str, Any, str, str]]:
    """``(hidden name, location, dimension, expression)`` for every formula inside an operation (parameters, vector components, point lists, constraint values)."""
    contract = CONTRACTS.get(op.get("type"))
    out: list[tuple[str, Any, str, str]] = []
    if contract is None:
        return out
    for key, v in (op.get("params") or {}).items():
        spec = contract.params.get(key)
        if spec is None:
            continue
        if _is_formula(v):
            out.append((_hidden(op["id"], key), (key,), _dim_of(op["type"], key, spec), v[1:]))
        elif key == "points" and isinstance(v, list):
            for i, pt in enumerate(v):
                for j, c in enumerate(pt if isinstance(pt, (list, tuple)) else []):
                    if _is_formula(c):
                        out.append((_hidden(op["id"], key, i, j), (key, i, j), "length", c[1:]))
        elif isinstance(v, (list, tuple)):
            for i, c in enumerate(v):
                if _is_formula(c):
                    out.append((_hidden(op["id"], key, i), (key, i), _dim_of(op["type"], key, spec), c[1:]))
    for n, c in enumerate(op.get("constraints") or []):
        v = c.get("value") if isinstance(c, Mapping) else None
        if _is_formula(v):
            out.append((_hidden(op["id"], "c", n), ("constraints", n), _constraint_dim(c.get("kind")), v[1:]))
        elif isinstance(v, list):
            for i, comp in enumerate(v):
                if _is_formula(comp):
                    out.append((_hidden(op["id"], "c", n, i), ("constraints", n, i), "length", comp[1:]))
    return out


def build_graph(spec: Mapping[str, Any]) -> tuple[ParameterGraph | None, list[dict[str, Any]]]:
    """The parameter graph: the spec's own parameters plus one hidden derived parameter per formula an operation uses (so formulas share one evaluator)."""
    table: dict[str, Any] = {}
    out: list[dict[str, Any]] = []
    for name, p in (spec.get("parameters") or {}).items():
        table[name] = copy.deepcopy(p)
    for op in spec.get("operations") or []:
        if isinstance(op, Mapping) and isinstance(op.get("id"), str):
            for name, _, dim, expr in _formula_slots(op):
                if dim not in ("length", "angle", "number", "integer", "boolean"):
                    continue
                table[name] = {"type": dim, "expression": expr}
    try:
        return ParameterGraph(table), out
    except SpatialFormatError as exc:
        ctx = getattr(exc, "context", None) or {}
        text = str(exc)
        code = "param.cycle" if "cycl" in text.lower() else "param.invalid"
        out.append(diag(code, "error", text, parameter=ctx.get("parameter"), hint=ctx.get("hint")))
        return None, out


def _param_diag(exc: Exception, op_for: Mapping[str, str]) -> dict[str, Any]:
    ctx = getattr(exc, "context", None) or {}
    name = ctx.get("parameter")
    code = "param.range" if "bound" in str(exc).lower() or "range" in str(exc).lower() or "outside" in str(exc).lower() else "param.value"
    return diag(code, "error", str(exc), parameter=name, op=op_for.get(name or ""))


# ----------------------------------------------------------------------------------------------------------------------- resolving an operation
def _get(d: Any, path: Sequence[Any]) -> Any:
    for k in path:
        d = d[k]
    return d


def resolve(op: Mapping[str, Any], values: Mapping[str, Any]) -> dict[str, Any]:
    """The operation's parameters with every formula replaced by its value, and defaults applied."""
    contract = CONTRACTS[op["type"]]
    params = copy.deepcopy(op.get("params") or {})
    for name, path, _, _ in _formula_slots(op):
        if path[0] == "constraints":
            continue
        cur: Any = params
        for k in path[:-1]:
            cur = cur[k]
        cur[path[-1]] = values[name]
    for key, spec in contract.params.items():
        if key not in params and spec.default is not None:
            params[key] = list(spec.default) if isinstance(spec.default, tuple) else spec.default
    for key, v in list(params.items()):
        spec = contract.params.get(key)
        if spec is not None and spec.dim == INT and isinstance(v, float):
            params[key] = int(round(v))
    return params


INT = "integer"


def resolve_constraints(op: Mapping[str, Any], values: Mapping[str, Any]) -> list[dict[str, Any]]:
    out = []
    for n, c in enumerate(op.get("constraints") or []):
        c = copy.deepcopy(c)
        v = c.get("value")
        if _is_formula(v):
            c["value"] = values[_hidden(op["id"], "c", n)]
        elif isinstance(v, list):
            c["value"] = [values[_hidden(op["id"], "c", n, i)] if _is_formula(x) else x for i, x in enumerate(v)]
        out.append(c)
    return out


def check_ranges(op: Mapping[str, Any], resolved: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Range and choice checks on resolved values (literals were checked statically; formulas can only be checked once evaluated)."""
    out = []
    contract = CONTRACTS[op["type"]]
    for key, spec in contract.params.items():
        v = resolved.get(key)
        if v is None or spec.dim not in ("length", "angle", "number", "integer") or isinstance(v, (list, tuple)):
            continue
        if spec.min is not None and v < spec.min - 1e-12 or spec.max is not None and v > spec.max + 1e-12:
            out.append(diag("param.range", "error", f"{op['id']}.{key} = {v:g} is outside {spec.min}..{spec.max}", op=op["id"],
                            hint="change the parameter it is computed from, or its bounds"))
    return out


# ----------------------------------------------------------------------------------------------------------------------- sketches
def _entity_ref(ref: Any, segs: int = 0, closed: bool = True) -> Any:
    if isinstance(ref, str):
        if ref.startswith("p") and ref[1:].isdigit():
            if segs and int(ref[1:]) == segs and not closed:
                return {"entity": f"s{segs - 1}", "part": "end"}          # the last vertex of an open path ends the last segment
            return {"entity": "s" + ref[1:], "part": "start"}
        if "." in ref:
            ent, part = ref.split(".", 1)
            return {"entity": ent, "part": part}
    return ref


def solve_sketch(op: Mapping[str, Any], r: Mapping[str, Any], constraints: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Solve one sketch: ``{"profile": points (m), "closed", "status", "dof", "diagnostics", ...}``. Rectangles and circles are fully dimensioned shorthand."""
    kind = r["kind"]
    diags: list[dict[str, Any]] = []
    oid = op["id"]
    if kind == "rectangle":
        w, h = r.get("width"), r.get("height")
        if w is None or h is None:
            return {"profile": None, "status": "invalid", "diagnostics": [diag("sketch.missing", "error", "a rectangle needs width and height", op=oid)]}
        cx, cy = r.get("center") or (0.0, 0.0)
        return {"profile": [[cx - w / 2, cy - h / 2], [cx + w / 2, cy - h / 2], [cx + w / 2, cy + h / 2], [cx - w / 2, cy + h / 2]], "closed": True, "status": "fully_dimensioned",
                "dof": 0, "diagnostics": [], "exact": True}
    if kind == "circle":
        rad = r.get("radius")
        if rad is None:
            return {"profile": None, "status": "invalid", "diagnostics": [diag("sketch.missing", "error", "a circle needs a radius", op=oid)]}
        cx, cy = r.get("center") or (0.0, 0.0)
        return {"profile": [[cx + rad * math.cos(2 * math.pi * i / CIRCLE_SIDES), cy + rad * math.sin(2 * math.pi * i / CIRCLE_SIDES)] for i in range(CIRCLE_SIDES)], "closed": True,
                "status": "fully_dimensioned", "dof": 0, "exact": False, "circle": {"center": [cx, cy], "radius": rad},
                "diagnostics": [diag("sketch.polygonal", "info", f"a circle is extruded as a {CIRCLE_SIDES}-sided polygon (an approximation of the true circle)", op=oid)]}
    pts = r.get("points")
    if not isinstance(pts, list) or len(pts) < 2:
        return {"profile": None, "status": "invalid", "diagnostics": [diag("sketch.missing", "error", "a polygon sketch needs at least 2 points (its initial positions)", op=oid)]}
    closed = op.get("closed", True) is not False
    if closed and len(pts) < 3:
        return {"profile": None, "status": "invalid", "diagnostics": [diag("sketch.missing", "error", "a closed polygon needs at least 3 points", op=oid)]}
    n = len(pts)
    segs = n if closed else n - 1
    entities: dict[str, Any] = {}
    for i in range(segs):
        a, b = pts[i], pts[(i + 1) % n]
        entities[f"s{i}"] = {"kind": "segment", "coords": [float(a[0]), float(a[1]), float(b[0]), float(b[1])]}
    rows: list[dict[str, Any]] = []
    for i in range(segs - 1 if not closed else segs):
        j = (i + 1) % segs
        if closed or j < segs:
            rows.append({"kind": "coincident", "refs": [{"entity": f"s{i}", "part": "end"}, {"entity": f"s{j}", "part": "start"}]})
    joints = len(rows)
    fixed: list[str] = []
    placement = op.get("placement", "anchored")
    if placement == "anchored":
        entities["axis"] = {"kind": "segment", "coords": [0.0, 0.0, 1.0, 0.0]}
        fixed.append("axis")
        x0, y0 = pts[0]
        rows.append({"kind": "fixed", "refs": [{"entity": "s0", "part": "start"}], "value": [float(x0), float(y0)]})
        ang = math.atan2(pts[1][1] - pts[0][1], pts[1][0] - pts[0][0])
        a = abs(ang) % math.pi if abs(ang) <= math.pi else math.pi
        rows.append({"kind": "angle", "refs": ["s0", "axis"], "value": a if ang >= 0 else math.pi - a if ang != 0 else 0.0})
        diags.append(diag("sketch.anchor", "info", "the first point and the direction of the first edge are anchored where they are drawn (placement 'anchored')", op=oid))
    user_at = len(rows)
    for c in constraints:
        if not isinstance(c, Mapping) or not isinstance(c.get("kind"), str):
            diags.append(diag("constraint.shape", "error", "a constraint needs a kind", op=oid))
            continue
        cap = CE.capability(c["kind"])
        if not cap["supported"]:
            diags.append(diag("constraint.unsupported", "error", f"constraint '{c['kind']}' is not supported: {cap['reason']}", op=oid))
            continue
        row = {"kind": c["kind"], "refs": [_entity_ref(x, segs, closed) for x in c.get("refs", [])]}
        if c.get("value") is not None:
            row["value"] = c["value"]
        rows.append(row)
    for name, circle in (op.get("circles") or {}).items():
        cc = circle.get("center") or [0.0, 0.0]
        entities[name] = {"kind": "circle", "coords": [float(cc[0]), float(cc[1]), float(circle.get("radius", 1.0))]}
    if any(d["severity"] == "error" for d in diags):
        return {"profile": None, "status": "invalid", "diagnostics": diags}
    try:
        res = CE.analyze(entities, rows, fixed=fixed)
    except SpatialFormatError as exc:
        return {"profile": None, "status": "invalid", "diagnostics": diags + [diag("constraint.invalid", "error", str(exc), op=oid)]}
    status = res["status"]
    if status == "over_constrained_consistent" and set(res["redundant"]) <= set(range(joints, user_at)):
        status = "well_constrained"                                           # only the anchor repeated something the drawing already fixed (for instance a horizontal edge)
    names = {i + user_at: f"constraint {i} ({c.get('kind')})" for i, c in enumerate(constraints)}
    if res["unresolved"]:
        diags.append(diag("constraint.unresolved", "error", "constraint references that do not resolve: " + "; ".join(map(str, res["unresolved"][:6])), op=oid))
    if status == "conflicting":
        bad = [names.get(i, f"built-in constraint {i}") for i in res["conflicting"]]
        extra = []
        if res["degenerate"]:
            extra.append("the only solution found collapses " + ", ".join(res["degenerate"]))
        if not res["converged"]:
            extra.append(f"the solver did not converge (residual {res['max_residual']:.3g})")
        diags.append(diag("sketch.conflict", "error", "contradictory constraints: " + ("; ".join(bad) if bad else "see details") + (" — " + "; ".join(extra) if extra else ""),
                          op=oid, conflicting=[names.get(i, f"built-in {i}") for i in res["conflicting"]],
                          conflicting_indices=[i - user_at for i in res["conflicting"] if i >= user_at], hint="remove or relax one of the listed constraints"))
    elif status == "under_constrained":
        diags.append(diag("sketch.under_constrained", "warning", f"the sketch is under-constrained: {res['dof']} degree(s) of freedom remain (first-order, local analysis)", op=oid,
                          dof=res["dof"], free=sorted(res["free_entities"])[:12], hint="add dimensions or relations on the free entities, or accept the drawn position"))
    elif status == "over_constrained_consistent":
        red = [names.get(i, f"built-in constraint {i}") for i in res["redundant"] if i >= joints]
        diags.append(diag("sketch.redundant", "warning", "over-constrained but consistent: " + (", ".join(red) if red else "a built-in relation repeats one given"), op=oid, redundant=red))
    if status == "conflicting":
        return {"profile": None, "status": status, "dof": res["dof"], "diagnostics": diags, "analysis": {k: res[k] for k in ("iterations", "max_residual", "rank", "variables", "equations")}}
    sol = res["solution"]
    profile = [[sol[f"s{i}"][0], sol[f"s{i}"][1]] for i in range(segs)]
    if not closed:
        profile.append([sol[f"s{segs - 1}"][2], sol[f"s{segs - 1}"][3]])
    clean = {"profile": [[round(x, 12), round(y, 12)] for x, y in profile], "closed": closed, "status": status, "dof": res["dof"], "exact": True,
             "diagnostics": diags, "analysis": {k: res[k] for k in ("iterations", "max_residual", "rank", "variables", "equations")},
             "circles": {k: [round(v, 12) for v in sol[k]] for k in (op.get("circles") or {})}}
    if (op.get("require") == "well_constrained") and status != "well_constrained":
        clean["diagnostics"] = diags + [diag("sketch.required", "error", f"this sketch must be well constrained but is {status}", op=oid)]
    return clean


# ----------------------------------------------------------------------------------------------------------------------- semantic face references
def faces_of(project: Any, oid: str) -> list[dict[str, Any]]:
    """Planar face groups of the element as the viewer shows it: ``{"normal", "offset", "area", "centroid"}`` (descriptors, not persistent face ids)."""
    import numpy as np
    from . import documentation as D
    vertices, faces = D.world_mesh(project, oid)
    V, F = np.asarray(vertices, float), np.asarray(faces, int)
    cross = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    area = np.linalg.norm(cross, axis=1)
    keep = area > 1e-14
    normals = cross[keep] / area[keep, None]
    cent = V[F[keep]].mean(axis=1)
    groups: dict[tuple, list[int]] = {}
    for k, (n, c) in enumerate(zip(normals, cent)):
        groups.setdefault((*np.round(n, 6).tolist(), round(float(n @ c), 6)), []).append(k)
    out = []
    for key, idx in groups.items():
        a = area[keep][idx]
        c = (cent[idx] * a[:, None]).sum(axis=0) / a.sum()
        out.append({"normal": list(key[:3]), "offset": key[3], "area": float(a.sum() / 2), "centroid": [float(x) for x in c]})
    return out


def resolve_face(project: Any, oid: str, ref: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve ``{"op", "normal": [...], "pick": "max"|"min"}`` on the element: the planar face with that normal, the outermost (``max``) or innermost along it."""
    want = [float(v) for v in ref.get("normal", [])]
    mag = math.sqrt(sum(v * v for v in want)) or 1.0
    want = [v / mag for v in want]
    groups = [g for g in faces_of(project, oid) if sum(a * b for a, b in zip(g["normal"], want)) > 1 - 1e-6]
    if not groups:
        raise BadRequest("No face with that normal exists on the element", hint="face normals: " + ", ".join(str(g["normal"]) for g in faces_of(project, oid)[:8]))
    pick = ref.get("pick", "max")
    g = max(groups, key=lambda g: g["offset"]) if pick == "max" else min(groups, key=lambda g: g["offset"])
    return {"origin": g["centroid"], "normal": want, "area": g["area"], "offset": g["offset"]}


# ----------------------------------------------------------------------------------------------------------------------- execution context and adapters
class OpFailure(Exception):
    def __init__(self, status: str, diagnostic: dict[str, Any]) -> None:
        super().__init__(diagnostic["message"])
        self.status, self.diagnostic = status, diagnostic


class Ctx:
    def __init__(self, project: Any, sid: str, revision: int, prior: Mapping[str, Any]) -> None:
        self.project, self.sid, self.revision = project, sid, revision
        self.prior = prior                                           # op id -> state recorded by the last successful run
        self.to_m = MM_PER[project.manifest["unit"]] / 1000.0
        self.objects: dict[str, str] = {}                            # op id -> element id (for ops that make or edit one)
        self.sketches: dict[str, dict[str, Any]] = {}                # op id -> solved sketch
        self.now: dict[str, dict[str, Any]] = {}                     # op id -> state being built

    def alive(self, oid: Any) -> bool:
        return bool(oid) and E.alive(self.project, oid)

    def level(self, ref: Any) -> dict[str, Any]:
        levels = self.project.manifest["levels"]
        if ref in (None, ""):
            return levels[0]
        for lv in levels:
            if ref in (lv["id"], lv["name"]):
                return lv
        raise OpFailure("failed", diag("level.unknown", "error", f"there is no level '{ref}'", hint="levels: " + ", ".join(l["name"] for l in levels)))

    def provenance(self, oid: str, op: Mapping[str, Any]) -> None:
        E._attach(self.project, oid, {"spec": {"id": self.sid, "op": op["id"], "type": op["type"], "revision": self.revision}})

    def units(self, pts: Sequence[Sequence[float]]) -> list[list[float]]:
        return [[x / self.to_m, y / self.to_m] for x, y in pts]


def _rethrow(exc: Exception) -> OpFailure:
    if isinstance(exc, DependencyUnavailableError):
        return OpFailure("unavailable", diag("capability.unavailable", "error", str(exc), hint="this operation needs a backend that is not installed here"))
    return OpFailure("failed", diag("op.failed", "error", str(exc), hint=getattr(exc, "hint", None)))


def _a_sketch(ctx: Ctx, op: Mapping[str, Any], r: Mapping[str, Any], constraints: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    s = solve_sketch(op, r, constraints)
    ctx.sketches[op["id"]] = s
    errors = [d for d in s["diagnostics"] if d["severity"] == "error"]
    if errors or s.get("profile") is None:
        raise OpFailure("failed", errors[0] if errors else diag("sketch.failed", "error", "the sketch has no solution", op=op["id"]))
    return {"object": None, "mode": "solved", "sketch": {"status": s["status"], "dof": s.get("dof"), "points": len(s["profile"]), "circles": s.get("circles", {})}, "exact": s.get("exact", True),
            "diagnostics": [d for d in s["diagnostics"] if d["severity"] != "error"]}


def _a_extrude(ctx: Ctx, op: Mapping[str, Any], r: Mapping[str, Any], void: bool | None = None) -> dict[str, Any]:
    project = ctx.project
    sk = ctx.sketches[op["inputs"]["profile"]]
    if not sk.get("closed", True):
        raise OpFailure("failed", diag("extrude.open", "error", "the profile is an open path; an extrusion needs a closed outline", op=op["id"]))
    void = bool(r.get("void")) if void is None else void
    level = ctx.level(r.get("level"))
    points = ctx.units(sk["profile"])
    base, depth = float(r.get("base", 0.0)), float(r["depth"])
    prev = ctx.prior.get(op["id"], {}).get("object")
    try:
        profile = E._boundary(points, ctx.to_m, "void" if void else "extrusion")
        if ctx.alive(prev) and E.category(project, prev) in ("extrusion", "void"):
            oid = prev
            if (E.category(project, oid) == "void") != void:
                FX.make_void(project, oid, void)
            values = {"profile": profile, "base": base, "depth": depth, "level": level["id"]}
            if not void:
                values["material"] = E._choice(r.get("material", "Concrete"), E.MATERIALS, "material")
            E._attach(project, oid, values)
            FX.regenerate_extrusion(project, oid)
            mode = "updated"
        else:
            oid = FX.add_extrusion(project, {"level": level["id"], "points": points, "base": base, "depth": depth, "void": void, "material": r.get("material", "Concrete"),
                                             "name": op.get("name") or op["id"]}, ctx.to_m)
            mode = "created"
        if void:
            from . import cutting as CU
            hosts = [ctx.objects[h] for h in op.get("hosts") or [] if h in ctx.objects]
            if hosts:
                E._attach(project, oid, {"cut_targets": hosts})
        ctx.provenance(oid, op)
    except (BadRequest, SpatialFormatError) as exc:
        raise _rethrow(exc) from exc
    ctx.objects[op["id"]] = oid
    return {"object": oid, "mode": mode, "exact": sk.get("exact", True)}


def _a_void(ctx: Ctx, op: Mapping[str, Any], r: Mapping[str, Any]) -> dict[str, Any]:
    return _a_extrude(ctx, op, r, void=True)


def _a_box(ctx: Ctx, op: Mapping[str, Any], r: Mapping[str, Any]) -> dict[str, Any]:
    project = ctx.project
    size, center = [float(r["width"]), float(r["depth"]), float(r["height"])], [float(v) for v in r.get("center", (0.0, 0.0, 0.0))]
    prev = ctx.prior.get(op["id"], {}).get("object")
    try:
        if ctx.alive(prev):
            oid = prev
            project.model.set_geometry(oid, {"kind": "box", "size": size, "center": center})
            mode = "updated"
        else:
            oid = project.model.add_box(size=size, center=center, name=r.get("name") or op.get("name") or op["id"])
            mode = "created"
        ctx.provenance(oid, op)
    except (BadRequest, SpatialFormatError) as exc:
        raise _rethrow(exc) from exc
    ctx.objects[op["id"]] = oid
    return {"object": oid, "mode": mode, "exact": True}


def _a_wall(ctx: Ctx, op: Mapping[str, Any], r: Mapping[str, Any]) -> dict[str, Any]:
    from .project import _Commands
    project = ctx.project
    prev = ctx.prior.get(op["id"], {}).get("object")
    s, e = [float(v) for v in r["start"]], [float(v) for v in r["end"]]
    if math.dist(s, e) < 1e-6:
        raise OpFailure("failed", diag("wall.degenerate", "error", "a wall's start and end coincide", op=op["id"]))
    try:
        if ctx.alive(prev) and E.category(project, prev) == "wall":
            oid = prev
            meta = E.meta_of(project, oid)
            k = 0.0
            E._attach(project, oid, {"start": s, "end": e, "thickness": float(r["thickness"]), "ref_thickness": float(r["thickness"]), "height": float(r["height"]),
                                     "material": r.get("material", "Concrete")})
            E.regenerate_wall(project, oid)
            mode = "updated"
        else:
            u = 1.0 / ctx.to_m
            oid = _Commands.model__add_wall(project, {"level": ctx.level(r.get("level"))["id"], "start": [s[0] * u, s[1] * u], "end": [e[0] * u, e[1] * u],
                                                      "thickness": float(r["thickness"]) * 1000.0, "height": float(r["height"]), "material": r.get("material", "Concrete"),
                                                      "name": op.get("name") or op["id"]})["id"]
            mode = "created"
        ctx.provenance(oid, op)
    except (BadRequest, SpatialFormatError) as exc:
        raise _rethrow(exc) from exc
    ctx.objects[op["id"]] = oid
    return {"object": oid, "mode": mode, "exact": True}


def _a_sweep(ctx: Ctx, op: Mapping[str, Any], r: Mapping[str, Any]) -> dict[str, Any]:
    project = ctx.project
    sk = ctx.sketches[op["inputs"]["path"]]
    if sk.get("closed", True):
        raise OpFailure("failed", diag("sweep.closed_path", "error", "a sweep path must be an open polyline (set closed: false on its sketch)", op=op["id"]))
    level = ctx.level(r.get("level"))
    path = ctx.units(sk["profile"])
    prev = ctx.prior.get(op["id"], {}).get("object")
    try:
        if ctx.alive(prev) and E.category(project, prev) == "sweep":
            oid = prev
            shape = r.get("shape", "rectangle")
            E._attach(project, oid, {"path": FX._path(path, ctx.to_m, False), "level": level["id"], "shape": shape, "width": float(r["width"]), "height": float(r["height"]),
                                     "section": FX.section_polygon(shape, float(r["width"]), float(r["height"]))})
            FX.regenerate_sweep(project, oid)
            mode = "updated"
        else:
            oid = FX.add_sweep(project, {"level": level["id"], "points": path, "shape": r.get("shape", "rectangle"), "width": float(r["width"]) * 1000.0,
                                         "height": float(r["height"]) * 1000.0, "name": op.get("name") or op["id"]}, ctx.to_m)
            mode = "created"
        ctx.provenance(oid, op)
    except (BadRequest, SpatialFormatError) as exc:
        raise _rethrow(exc) from exc
    ctx.objects[op["id"]] = oid
    return {"object": oid, "mode": mode, "exact": sk.get("exact", True) and r.get("shape", "rectangle") == "rectangle"}


_MODIFIER_KIND = {"boolean": "sgeo.boolean", "split": "sgeo.split", "chamfer": "sgeo.chamfer", "bend": "sgeo.bend"}


def _modifier_params(ctx: Ctx, op: Mapping[str, Any], r: Mapping[str, Any], target: str) -> dict[str, Any]:
    t = op["type"]
    if t == "boolean":
        return {"operation": r["operation"]}
    if t == "split":
        if op.get("face"):
            f = resolve_face(ctx.project, ctx.objects[op["face"]["op"]] if op["face"].get("op") in ctx.objects else target, op["face"])
            return {"origin": f["origin"], "normal": f["normal"], "side": r.get("side", "positive")}
        return {"origin": [float(v) for v in r.get("origin", (0.0, 0.0, 0.0))], "normal": [float(v) for v in r["normal"]], "side": r.get("side", "positive")}
    if t == "chamfer":
        return {"amount": float(r["amount"]), "depth": float(r.get("depth", r["amount"])), "segments": int(r.get("segments", 1))}
    return {"axis": r["axis"], "angle": float(r["angle"]), "start": float(r["start"]), "end": float(r["end"]), "segments": int(r.get("segments", 16))}


def _a_modifier(ctx: Ctx, op: Mapping[str, Any], r: Mapping[str, Any]) -> dict[str, Any]:
    project = ctx.project
    target = ctx.objects.get(op["inputs"]["target"])
    if not ctx.alive(target):
        raise OpFailure("skipped", diag("op.target_missing", "error", f"the target '{op['inputs']['target']}' has no element", op=op["id"]))
    if project.model.get(target).get("geometry") is None:
        raise OpFailure("failed", diag("modifier.no_geometry", "error", "the target has no geometry to modify", op=op["id"]))
    operands: list[str] = []
    if op["type"] == "boolean":
        o = ctx.objects.get(op["inputs"]["operand"])
        if not ctx.alive(o) or o == target:
            raise OpFailure("failed", diag("boolean.operand", "error", "a Boolean needs a distinct operand element", op=op["id"]))
        operands = [o]
    params = _modifier_params(ctx, op, r, target)
    policy = {"mode": "display_only"} if op["type"] == "bend" else {"mode": "tolerance_controlled", "absolute": DEFAULT_TOL}
    prior = ctx.prior.get(op["id"], {})
    mid = prior.get("modifier")
    live = {m["id"]: m for m in project.model.modifiers()}
    try:
        if mid in live and live[mid]["target"]["item_id"] == target and [x["item_id"] for x in live[mid]["operands"]] == operands:
            project.model.update_modifier(mid, parameters=params, evaluation_policy=policy)
            mode = "updated"
        else:
            if mid in live:
                project.model.remove_modifier(mid)
            mid = project.model.add_modifier(target, kind=_MODIFIER_KIND[op["type"]], operands=operands, parameters=params, evaluation_policy=policy)
            mode = "created"
        report = next(i for i in project.model.evaluate_modifiers() if i["modifier_id"] == mid)
    except (BadRequest, SpatialFormatError) as exc:
        if mid in {m["id"] for m in project.model.modifiers()}:
            project.model.remove_modifier(mid)
        raise _rethrow(exc) from exc
    if report["status"] != "ok" or not report.get("result") or report["result"].get("geometry") is None:
        project.model.remove_modifier(mid)
        raise OpFailure("failed", diag("modifier.empty", "error", f"the {op['type']} produced no geometry ({report.get('status')})", op=op["id"], details=report.get("diagnostics")))
    ctx.objects[op["id"]] = target
    details = report.get("diagnostics", {}) or {}
    return {"object": target, "modifier": mid, "mode": mode, "exact": not details.get("approximate", False) and op["type"] != "bend", "kernel": details}


def _a_offset(ctx: Ctx, op: Mapping[str, Any], r: Mapping[str, Any]) -> dict[str, Any]:
    raise OpFailure("unavailable", diag("capability.unavailable", "error", "solid offset is not supported by the operation graph", op=op["id"],
                                        hint="use an 'offset' constraint between two sketch segments, or a second extrusion with the offset dimensions"))


ADAPTERS = {"extrude": _a_extrude, "void": _a_void, "box": _a_box, "wall": _a_wall, "sweep": _a_sweep, "boolean": _a_modifier, "split": _a_modifier, "chamfer": _a_modifier,
            "bend": _a_modifier, "offset": _a_offset}


# ----------------------------------------------------------------------------------------------------------------------- verification
def _check_one(project: Any, exp: Mapping[str, Any], states: Mapping[str, Any], values: Mapping[str, Any], op_object: Mapping[str, Any]) -> dict[str, Any]:
    check, tol = exp.get("check"), float(exp.get("tol", DEFAULT_TOL))
    rec: dict[str, Any] = {"id": exp.get("id"), "check": check, "object": exp.get("object"), "tolerance": tol, "tolerance_source": "spec" if "tol" in exp else f"default {DEFAULT_TOL} m (the project's modifier tolerance)"}
    if check == "param":
        v = values.get(exp.get("name"))
        lo, hi = exp.get("min"), exp.get("max")
        rec.update(actual=v, expected={"min": lo, "max": hi}, passed=v is not None and (lo is None or v >= lo - 1e-12) and (hi is None or v <= hi + 1e-12))
        return rec
    if check == "no_overlap":
        a, b = (states.get(x) for x in exp.get("objects", [None, None])[:2])
        if not a or not b or a["bbox"]["min"] is None or b["bbox"]["min"] is None:
            rec.update(passed=False, actual=None, note="an object has no measurable geometry")
            return rec
        gap = max(max(b["bbox"]["min"][i] - a["bbox"]["max"][i], a["bbox"]["min"][i] - b["bbox"]["max"][i]) for i in range(3))
        rec.update(actual=round(gap, 9), expected=f">= {exp.get('clearance', 0.0)}", passed=gap >= float(exp.get("clearance", 0.0)) - tol, approximate=True,
                   note="axis-aligned bounding boxes only: a positive gap proves no overlap, a negative one only means the boxes overlap")
        return rec
    st = states.get(exp.get("object"))
    if st is None:
        rec.update(passed=False, actual=None, note=f"'{exp.get('object')}' is not an element of this specification")
        return rec
    if check == "valid":
        rec.update(actual=bool(st["valid"]), expected=True, passed=bool(st["valid"]), note="; ".join(st.get("issues", [])) or None)
    elif check in ("size", "bbox_min", "bbox_max"):
        key = "size" if check == "size" else "min" if check == "bbox_min" else "max"
        actual = st["bbox"][key]
        want = exp.get("value")
        if actual is None or not isinstance(want, list) or len(want) != 3:
            rec.update(passed=False, actual=actual, expected=want, note="needs a measurable element and a 3-component value")
        else:
            errs = [None if w is None else abs(a - w) for a, w in zip(actual, want)]
            rec.update(actual=actual, expected=want, error=errs, passed=all(e is None or e <= tol for e in errs))
    elif check in ("volume", "area"):
        actual = st.get(check)
        want = exp.get("value")
        rel = exp.get("rel")
        allowed = tol if rel is None else abs(float(want)) * float(rel)
        rec.update(actual=actual, expected=want, error=None if actual is None else abs(actual - want), passed=actual is not None and abs(actual - want) <= allowed,
                   tolerance=allowed)
    elif check == "topology":
        actual = st["topology"].get(exp.get("key"))
        rec.update(actual=actual, expected=exp.get("value"), passed=actual == exp.get("value"), tolerance=0)
    else:
        rec.update(passed=False, note=f"unknown check '{check}'", actual=None)
    return rec


def verify_state(project: Any, spec: Mapping[str, Any], values: Mapping[str, Any], objects: Mapping[str, str], ops: Sequence[Mapping[str, Any]],
                 op_states: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """``(verification records, diagnostics, measured states by op id)``: the built-in postconditions plus the spec's ``expect`` checks, with measured evidence."""
    states: dict[str, Any] = {}
    diags: list[dict[str, Any]] = []
    for op in ops:
        oid = objects.get(op["id"])
        if oid and E.alive(project, oid):
            st = IM.element_state(project, oid)
            states[op["id"]] = st
            if CONTRACTS[op["type"]].produces == "solid" and not st["valid"] and op["type"] != "void":
                diags.append(diag("post.not_solid", "error", f"'{op['id']}' is not a closed solid with positive volume: " + "; ".join(st["issues"] or ["no geometry"]), op=op["id"]))
            elif op["type"] == "void" and st["empty"]:
                diags.append(diag("post.not_solid", "error", f"void '{op['id']}' has no geometry", op=op["id"]))
    records = []
    for n, exp in enumerate(spec.get("expect") or []):
        rec = _check_one(project, {**exp, "id": exp.get("id", f"expect{n}")}, states, values, objects)
        records.append(rec)
        if not rec["passed"]:
            diags.append(diag("verify.failed", "error", _fail_text(rec), op=exp.get("object"), expectation=rec["id"], actual=rec.get("actual"), expected=rec.get("expected")))
    for out in spec.get("outputs") or []:
        st = op_states.get(out, {})
        if st.get("status") != "ok":
            diags.append(diag("output.missing", "error", f"required output '{out}' was not produced ({st.get('status', 'not run')})", op=out))
    return records, diags, states


def _fail_text(rec: Mapping[str, Any]) -> str:
    where = f" on '{rec['object']}'" if rec.get("object") else ""
    return f"{rec['check']}{where}: expected {rec.get('expected')}, measured {rec.get('actual')}" + (f" (error {rec['error']})" if rec.get("error") is not None else "") + (f" — {rec['note']}" if rec.get("note") else "")


# ----------------------------------------------------------------------------------------------------------------------- run
def normalise(spec: Any) -> dict[str, Any]:
    if not isinstance(spec, Mapping):
        raise BadRequest("A design specification must be an object")
    extra = set(spec) - SPEC_FIELDS
    if extra:
        raise BadRequest("Unknown specification field(s): " + ", ".join(sorted(extra)), hint="fields: " + ", ".join(sorted(SPEC_FIELDS)))
    out = copy.deepcopy(dict(spec))
    out.setdefault("parameters", {})
    out.setdefault("operations", [])
    out.setdefault("expect", [])
    out.setdefault("outputs", [])
    if not isinstance(out["parameters"], dict) or len(out["parameters"]) > MAX_PARAMETERS:
        raise BadRequest("parameters must be an object of at most %d entries" % MAX_PARAMETERS)
    if not isinstance(out["operations"], list) or len(out["operations"]) > MAX_OPERATIONS:
        raise BadRequest("operations must be a list of at most %d" % MAX_OPERATIONS)
    if not isinstance(out["expect"], list) or len(out["expect"]) > MAX_EXPECT:
        raise BadRequest("expect must be a list of at most %d" % MAX_EXPECT)
    sid = out.get("id")
    if sid is None:
        out["id"] = new_id()
    elif not isinstance(sid, str) or not sid or len(sid) > 64:
        raise BadRequest("The specification id must be text of 1 to 64 characters")
    out.setdefault("name", out["id"])
    return out


def static_check(spec: Mapping[str, Any]) -> tuple[list[dict[str, Any]], ParameterGraph | None, list[str]]:
    diags, order = OG.validate(spec.get("operations"))
    for op in spec.get("operations") or []:
        if isinstance(op, Mapping) and isinstance(op.get("id"), str) and len(op["id"]) > 30:
            diags.append(diag("op.id", "error", f"operation id '{op['id']}' is longer than 30 characters", op=op["id"]))
    graph, gd = build_graph(spec)
    diags += gd
    ids = {op["id"] for op in spec.get("operations") or [] if isinstance(op, Mapping) and isinstance(op.get("id"), str)}
    for n, exp in enumerate(spec.get("expect") or []):
        if isinstance(exp, Mapping) and exp.get("check") != "param" and exp.get("check") != "no_overlap" and exp.get("object") not in ids:
            diags.append(diag("expect.unknown", "error", f"expectation {n} refers to the unknown operation '{exp.get('object')}'"))
    for out in spec.get("outputs") or []:
        if out not in ids:
            diags.append(diag("output.unknown", "error", f"output '{out}' is not an operation"))
    return diags, graph, order


def evaluate_parameters(graph: ParameterGraph, overrides: Mapping[str, Any], spec: Mapping[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    op_for = {n: op["id"] for op in spec.get("operations", []) for n, *_ in _formula_slots(op)}
    try:
        return graph.evaluate(dict(overrides)), []
    except SpatialFormatError as exc:
        return None, [_param_diag(exc, op_for)]


def run(project: Any, sid: str, *, overrides: Mapping[str, Any] | None = None, on_drift: str | None = None, dry_run: bool = False,
        expected_revision: int | None = None) -> dict[str, Any]:
    """Run the specification inside the running command. Returns the report; the caller decides what a failed report means (the command raises, rolling everything back)."""
    t0 = time.perf_counter()
    ent = entry(project, sid)
    spec = ent["spec"]
    if expected_revision is not None and int(expected_revision) != ent["revision"]:
        raise Conflict(f"The specification changed (now revision {ent['revision']}; this run was made for {expected_revision})", hint="Review it and run again")
    policy = on_drift or spec.get("on_drift", "stop")
    if policy not in ("stop", "overwrite"):
        raise BadRequest("on_drift must be 'stop' or 'overwrite'")
    report: dict[str, Any] = {"spec": sid, "revision": ent["revision"], "ok": False, "dry_run": dry_run, "diagnostics": [], "operations": [], "recomputed": [], "reused": [],
                              "verification": [], "outputs": {}}
    diags: list[dict[str, Any]] = report["diagnostics"]
    sdiags, graph, order = static_check(spec)
    diags += sdiags
    if any(d["severity"] == "error" for d in diags) or graph is None:
        report["status"] = "invalid"
        return report
    merged = {**(ent.get("values") or {}), **(overrides or {})}
    values, pd = evaluate_parameters(graph, merged, spec)
    diags += pd
    if values is None:
        report["status"] = "invalid"
        return report
    report["parameters"] = {k: v for k, v in values.items() if not k.startswith("_")}
    by_id = {op["id"]: op for op in spec["operations"]}
    prior = (ent.get("state") or {}).get("ops", {})
    ctx = Ctx(project, sid, ent["revision"], prior)
    resolved: dict[str, Any] = {}
    cons: dict[str, Any] = {}
    fps: dict[str, str] = {}
    for oid in order:
        op = by_id[oid]
        resolved[oid], cons[oid] = resolve(op, values), resolve_constraints(op, values)
        rd = check_ranges(op, resolved[oid])
        diags += rd
        fps[oid] = OG.digest(op["type"], resolved[oid], cons[oid], op.get("face"), op.get("placement"), op.get("closed"), op.get("circles"), op.get("require"),
                             [fps.get(p, "?") for p in OG.producers(op)], op.get("hosts"))
    if any(d["severity"] == "error" for d in diags):
        report["status"] = "invalid"
        return report
    # drift: elements changed outside the specification since its last successful run
    drift: dict[str, str] = {}
    for oid in order:
        p = prior.get(oid, {})
        obj = p.get("object")
        if obj and ctx.alive(obj) and p.get("result_fp") and CONTRACTS[by_id[oid]["type"]].produces in ("solid", "edit"):
            if IM.element_state(project, obj)["fingerprint"] != p["result_fp"]:
                drift[oid] = obj
    # incremental plan
    dirty: set[str] = set()
    for oid in order:
        p = prior.get(oid)
        missing = bool(p) and CONTRACTS[by_id[oid]["type"]].produces in ("solid", "edit") and not ctx.alive(p.get("object"))
        if (not p or p.get("fingerprint") != fps[oid] or p.get("status") != "ok" or missing or any(q in dirty for q in OG.producers(by_id[oid]))):
            dirty.add(oid)
    conflicts = sorted(dirty & set(drift))
    if drift and policy == "stop" and (conflicts or any(by_id[o]["type"] in ("boolean", "split", "chamfer", "bend") for o in dirty)):
        for oid in (conflicts or sorted(drift)):
            diags.append(diag("conflict.drift", "error", f"'{oid}' (element {drift[oid]}) was changed outside the specification since it last ran", op=oid,
                              hint="run with on_drift='overwrite' to reassert the specification, or update the specification to match"))
        report["status"] = "conflict"
        return report
    for oid, obj in drift.items():
        if oid not in conflicts:
            diags.append(diag("drift.noted", "warning", f"'{oid}' (element {obj}) differs from what the specification last produced; it is left as it is because nothing it depends on changed", op=oid))
        elif policy == "overwrite":
            diags.append(diag("drift.overwritten", "warning", f"'{oid}' (element {obj}) was changed outside the specification; its specified geometry was restored", op=oid))
    # execute
    failed: set[str] = set()
    op_states: dict[str, Any] = {}
    for oid in order:
        op, p = by_id[oid], prior.get(oid, {})
        record: dict[str, Any] = {"id": oid, "type": op["type"], "fingerprint": fps[oid]}
        if oid not in dirty:
            if p.get("object"):
                ctx.objects[oid] = p["object"]
            if op["type"] == "sketch":
                ctx.sketches[oid] = solve_sketch(op, resolved[oid], cons[oid])
            op_states[oid] = {**p, "status": "ok", "mode": "reused"}
            report["reused"].append(oid)
            continue
        if any(q in failed for q in OG.producers(op)):
            op_states[oid] = {**record, "status": "skipped", "object": p.get("object"), "modifier": p.get("modifier")}
            diags.append(diag("op.skipped", "error", f"'{oid}' was not run because an operation it depends on did not succeed", op=oid))
            failed.add(oid)
            continue
        try:
            out = _a_sketch(ctx, op, resolved[oid], cons[oid]) if op["type"] == "sketch" else ADAPTERS[op["type"]](ctx, op, resolved[oid])
            diags += out.pop("diagnostics", [])
            op_states[oid] = {**record, "status": "ok", **out}
            report["recomputed"].append(oid)
        except OpFailure as exc:
            op_states[oid] = {**record, "status": exc.status, "object": p.get("object"), "modifier": p.get("modifier")}
            d = dict(exc.diagnostic)
            d.setdefault("op", oid)
            diags.append(d)
            failed.add(oid)
        except (BadRequest, SpatialFormatError) as exc:
            op_states[oid] = {**record, "status": "failed", "object": p.get("object"), "modifier": p.get("modifier")}
            diags.append(diag("op.failed", "error", str(exc), op=oid, hint=getattr(exc, "hint", None)))
            failed.add(oid)
    # operations that left the specification take their elements with them (unless someone else changed them)
    removed = []
    for oid, p in prior.items():
        if oid in by_id:
            continue
        if p.get("modifier") and p["modifier"] in {m["id"] for m in project.model.modifiers()}:
            project.model.remove_modifier(p["modifier"])
        obj = p.get("object")
        if obj and ctx.alive(obj) and p.get("mode") != "reused" and E.m(E.meta_of(project, obj), "spec", {}).get("id") == sid and p.get("type") not in ("boolean", "split", "chamfer", "bend"):
            E.remove(project, obj)
            removed.append(obj)
    report["removed_elements"] = removed
    try:
        FX.reconcile(project)
    except (BadRequest, SpatialFormatError) as exc:
        diags.append(diag("reconcile.failed", "error", str(exc)))
    ordered_ops = [by_id[o] for o in order]
    records, vdiags, measured = verify_state(project, spec, values, ctx.objects, ordered_ops, op_states)
    report["verification"], report["diagnostics"] = records, diags + vdiags
    for oid in order:
        st = op_states[oid]
        m = measured.get(oid)
        if m is not None:
            keep = oid in drift and oid not in dirty                       # an outside edit stays flagged until the specification is run over it
            st["result_fp"] = prior[oid]["result_fp"] if keep else m["fingerprint"]
            st["measured"] = {"bbox": m["bbox"], "area": m["area"], "volume": m["volume"], "valid": m["valid"], "topology": m["topology"]}
        if oid in ctx.objects:
            st["object"] = ctx.objects[oid]
        st["type"] = by_id[oid]["type"]
        st["exact"] = st.get("exact", True)
        report["operations"].append({k: st.get(k) for k in ("id", "type", "status", "mode", "object", "modifier", "exact", "measured", "sketch", "kernel") if st.get(k) is not None})
    report["outputs"] = {o: ctx.objects.get(o) for o in spec.get("outputs", [])}
    report["ok"] = not any(d["severity"] == "error" for d in report["diagnostics"])
    report["status"] = "ok" if report["ok"] else "failed"
    report["timing_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    ent["_pending"] = {"ops": op_states, "params": values, "overrides": merged}
    return report


def commit_run(project: Any, sid: str, report: Mapping[str, Any]) -> None:
    ent = entry(project, sid)
    pending = ent.pop("_pending", None)
    if pending is None:
        return
    ent["state"] = {"ops": pending["ops"], "params": {k: v for k, v in pending["params"].items() if not k.startswith("_")}}
    ent["values"] = pending["overrides"]
    ent["status"], ent["ok"] = report["status"], report["ok"]
    mirror(project)


def mirror(project: Any) -> None:
    """A portable copy of the definitions in the SGEO document, so a bare ``.sgeo`` still says how it was made (the manifest stays authoritative)."""
    specs = {sid: {"spec": e["spec"], "revision": e["revision"]} for sid, e in store(project).items()}
    try:
        if specs:
            project.model.set_metadata({MIRROR: specs})
    except SpatialFormatError:
        pass


# ----------------------------------------------------------------------------------------------------------------------- commands (called by project.py)
def define(project: Any, p: Mapping[str, Any]) -> dict[str, Any]:
    spec = normalise(p.get("spec"))
    specs = store(project)
    if spec["id"] in specs and not p.get("replace"):
        raise Conflict(f"A specification '{spec['id']}' already exists", hint="use spec.update, or pass replace")
    if len(specs) >= MAX_SPECS and spec["id"] not in specs:
        raise BadRequest("A project holds at most %d design specifications" % MAX_SPECS)
    diags, _, _ = static_check(spec)
    errors = [d for d in diags if d["severity"] == "error"]
    if errors and not p.get("allow_invalid"):
        raise BadRequest(errors[0]["message"], details={"diagnostics": diags}, hint=errors[0].get("hint") or "fix the listed diagnostics")
    revision = specs[spec["id"]]["revision"] + 1 if spec["id"] in specs else 1
    prev = specs.get(spec["id"], {})
    specs[spec["id"]] = {"spec": spec, "revision": revision, "state": prev.get("state"), "values": spec.get("values") or prev.get("values") or {}, "status": "defined", "ok": None}
    mirror(project)
    return {"id": spec["id"], "revision": revision, "diagnostics": diags}


def update(project: Any, p: Mapping[str, Any]) -> dict[str, Any]:
    sid = p.get("id")
    ent = entry(project, sid)
    if p.get("expected_revision") is not None and int(p["expected_revision"]) != ent["revision"]:
        raise Conflict(f"The specification changed (now revision {ent['revision']})", hint="Review it and try again")
    spec = copy.deepcopy(ent["spec"])
    patch = p.get("patch")
    if not isinstance(patch, Mapping):
        raise BadRequest("patch must be an object")
    for key, v in patch.items():
        if key in ("parameters",):
            spec["parameters"] = {**spec.get("parameters", {}), **v}
            for k in [k for k, x in v.items() if x is None]:
                spec["parameters"].pop(k, None)
        elif key == "operations_set":
            ops = {o["id"]: o for o in spec["operations"]}
            for o in v:
                ops[o["id"]] = o
            spec["operations"] = list(ops.values())
        elif key == "operations_remove":
            spec["operations"] = [o for o in spec["operations"] if o["id"] not in v]
        elif key in ("operations", "expect", "outputs", "name", "description", "on_drift"):
            spec[key] = v
        else:
            raise BadRequest(f"Cannot patch '{key}'", hint="patch keys: parameters, operations, operations_set, operations_remove, expect, outputs, name, description, on_drift")
    spec = normalise(spec)
    diags, _, _ = static_check(spec)
    errors = [d for d in diags if d["severity"] == "error"]
    if errors and not p.get("allow_invalid"):
        raise BadRequest(errors[0]["message"], details={"diagnostics": diags})
    ent["spec"], ent["revision"] = spec, ent["revision"] + 1
    ent["status"], ent["ok"] = "edited", None
    mirror(project)
    return {"id": sid, "revision": ent["revision"], "diagnostics": diags}


def delete(project: Any, p: Mapping[str, Any]) -> dict[str, Any]:
    sid = p.get("id")
    ent = entry(project, sid)
    removed: list[str] = []
    if p.get("remove_elements"):
        for st in reversed(list((ent.get("state") or {}).get("ops", {}).values())):
            if st.get("modifier") and st["modifier"] in {m["id"] for m in project.model.modifiers()}:
                project.model.remove_modifier(st["modifier"])
        for st in (ent.get("state") or {}).get("ops", {}).values():
            if st.get("type") not in ("boolean", "split", "chamfer", "bend") and st.get("object") and E.alive(project, st["object"]):
                E.remove(project, st["object"])
                removed.append(st["object"])
    del store(project)[sid]
    if not store(project):
        project.manifest.pop(KEY, None)
        try:
            project.model.set_metadata({MIRROR: None}) if MIRROR in project.model.metadata else None
        except Exception:
            pass
    else:
        mirror(project)
    return {"id": sid, "removed_elements": removed}


def run_command(project: Any, p: Mapping[str, Any]) -> dict[str, Any]:
    sid = p.get("id")
    overrides = p.get("overrides") or {}
    if not isinstance(overrides, Mapping):
        raise BadRequest("overrides must be an object of parameter values")
    report = run(project, sid, overrides=overrides, on_drift=p.get("on_drift"), dry_run=bool(p.get("dry_run")), expected_revision=p.get("expected_revision"))
    if not report["ok"] and not p.get("dry_run"):
        first = next((d for d in report["diagnostics"] if d["severity"] == "error"), None)
        raise BadRequest(first["message"] if first else "The specification did not verify", hint=(first or {}).get("hint") or "nothing was changed", details={"report": report})
    commit_run(project, sid, report)
    return {"id": sid, "report": report}


# ----------------------------------------------------------------------------------------------------------------------- verification -> feedback -> repair
def verify(project: Any, sid: str, overrides: Mapping[str, Any] | None = None, on_drift: str | None = None) -> dict[str, Any]:
    """Run the specification on a throw-away copy of the project and report (the project is not touched). The report carries the measured evidence."""
    scratch = project.scratch()
    return scratch.execute("spec.run", {"id": sid, "overrides": dict(overrides or {}), "dry_run": True, "on_drift": on_drift or "overwrite"})["result"]["report"]


def _upstream(spec: Mapping[str, Any], op_id: str) -> list[Mapping[str, Any]]:
    by_id = {o["id"]: o for o in spec["operations"]}
    seen, stack, out = set(), [op_id], []
    while stack:
        i = stack.pop()
        if i in seen or i not in by_id:
            continue
        seen.add(i)
        out.append(by_id[i])
        stack.extend(OG.producers(by_id[i]))
    return out


def _influences(spec: Mapping[str, Any], graph: ParameterGraph, op_id: str) -> list[str]:
    """Editable parameters (defaults, not derived, not read-only) that the operation's result depends on."""
    names: set[str] = set()
    for op in _upstream(spec, op_id):
        for hidden, *_ in _formula_slots(op):
            names |= set(graph.transitive_dependencies([hidden])) | {hidden}
    return sorted(n for n in names if n in (spec.get("parameters") or {}) and "expression" not in spec["parameters"][n] and not spec["parameters"][n].get("read_only")
                  and spec["parameters"][n].get("type") in ("length", "angle", "number"))


def _target_of(rec: Mapping[str, Any]) -> float | None:
    if rec["check"] in ("volume", "area"):
        return float(rec["expected"])
    errs = rec.get("error") or []
    axis = next((i for i, e in enumerate(errs) if e is not None and e > rec["tolerance"]), None)
    return None if axis is None else float(rec["expected"][axis])


def propose_repairs(project: Any, sid: str, overrides: Mapping[str, Any] | None = None, *, max_iterations: int = 10) -> dict[str, Any]:
    """Localised, feasible repair proposals for a specification that does not verify. Each proposal was *re-validated on a throw-away copy* before it is offered, and none is
    applied: committing one is a normal ``spec.run`` (or ``spec.update``) command, which verifies again and rolls back on failure. Design intent is never altered silently:
    a proposal says which parameter or constraint it would change, from what to what, and what it leaves failing."""
    ent = entry(project, sid)
    spec = ent["spec"]
    base = dict(overrides or {})
    report = verify(project, sid, base)
    out: dict[str, Any] = {"spec": sid, "spec_revision": ent["revision"], "ok": report["ok"], "report": report, "proposals": [], "unrepairable": []}
    if report["ok"]:
        return out
    graph, _ = build_graph(spec)
    current = report.get("parameters", {})
    failures = [r for r in report["verification"] if not r["passed"]]
    for rec in failures:
        if rec["check"] == "param":
            p = spec["parameters"].get(rec["name"], {})
            want = (rec["expected"]["min"] if rec["actual"] is not None and rec["expected"]["min"] is not None and rec["actual"] < rec["expected"]["min"] else rec["expected"]["max"])
            if "expression" in p or want is None:
                out["unrepairable"].append({"expectation": rec["id"], "reason": "a derived parameter is set by its formula; change the parameters it reads"})
                continue
            _offer(project, sid, spec, out, rec, rec["name"], current.get(rec["name"]), want, base)
            continue
        target = _target_of(rec)
        cands = _influences(spec, graph, rec.get("object")) if graph is not None and rec.get("object") else []
        if target is None or not cands:
            out["unrepairable"].append({"expectation": rec["id"], "reason": "no editable parameter influences this measurement" if not cands else "the check has no scalar target"})
            continue
        for name in cands:
            solved = _solve_for(project, sid, spec, base, rec, name, current[name], target, max_iterations)
            if solved is None:
                continue
            _offer(project, sid, spec, out, rec, name, current[name], solved, base)
    for d in report["diagnostics"]:
        if d["code"] == "sketch.conflict":
            for idx in d.get("conflicting_indices", []):
                op = next(o for o in spec["operations"] if o["id"] == d["op"])
                trial = copy.deepcopy(op)
                removed = trial["constraints"].pop(idx)
                patch = {"operations_set": [trial]}
                try:
                    trial_project = project.scratch()
                    trial_project.execute("spec.update", {"id": sid, "patch": patch})
                    rep2 = trial_project.execute("spec.run", {"id": sid, "overrides": base, "dry_run": True, "on_drift": "overwrite"})["result"]["report"]
                except (BadRequest, SpatialFormatError):
                    continue
                gone = "sketch.conflict" not in {x["code"] for x in rep2["diagnostics"]}
                out["proposals"].append({"kind": "remove_constraint", "op": d["op"], "constraint_index": idx, "constraint": removed, "revalidated": rep2["ok"], "resolves_conflict": gone,
                                         "remaining_failures": [x["message"] for x in rep2["diagnostics"] if x["severity"] == "error"],
                                         "commands": [{"op": "spec.update", "params": {"id": sid, "expected_revision": ent["revision"], "patch": patch}},
                                                      {"op": "spec.run", "params": {"id": sid, "overrides": base}}],
                                         "note": "removing a constraint changes design intent: confirm it is the redundant or wrong one"})
    out["proposals"].sort(key=lambda p: (not p.get("revalidated"), abs(p.get("relative_change", 0.0))))
    return out


def _measure(project: Any, sid: str, base: Mapping[str, Any], name: str, value: float, rec: Mapping[str, Any]) -> float | None:
    rep = verify(project, sid, {**base, name: value})
    op = next((o for o in rep["operations"] if o["id"] == rec.get("object")), None)
    m = (op or {}).get("measured")
    if not m:
        return None
    if rec["check"] == "volume":
        return m["volume"]
    if rec["check"] == "area":
        return m["area"]
    key = {"size": "size", "bbox_min": "min", "bbox_max": "max"}[rec["check"]]
    axis = next((i for i, e in enumerate(rec.get("error") or []) if e is not None and e > rec["tolerance"]), None)
    return None if axis is None else m["bbox"][key][axis]


def _actual_scalar(rec: Mapping[str, Any]) -> float:
    a = rec["actual"]
    if isinstance(a, list):
        a = a[next(i for i, e in enumerate(rec["error"]) if e is not None and e > rec["tolerance"])]
    return float(a)


def _solve_for(project: Any, sid: str, spec: Mapping[str, Any], base: Mapping[str, Any], rec: Mapping[str, Any], name: str, v0: float, target: float, iterations: int) -> float | None:
    p = spec["parameters"][name]
    lo, hi = float(p.get("min", -1e12)), float(p.get("max", 1e12))
    tol = rec["tolerance"] if rec["check"] not in ("volume", "area") or rec.get("tolerance") is None else rec["tolerance"]
    x0 = float(v0)
    f0 = _actual_scalar(rec) - target
    step = max(abs(x0) * 0.1, 0.05)
    x1 = min(max(x0 + step, lo), hi)
    if x1 == x0:
        x1 = min(max(x0 - step, lo), hi)
    if x1 == x0:
        return None
    for _ in range(iterations):
        m1 = _measure(project, sid, base, name, x1, rec)
        if m1 is None:
            return None
        f1 = m1 - target
        if abs(f1) <= max(tol, 1e-9):
            return x1
        if abs(f1 - f0) < 1e-14:
            return None                                                         # the measurement does not respond to this parameter
        x2 = x1 - f1 * (x1 - x0) / (f1 - f0)
        x2 = min(max(x2, lo), hi)
        if abs(x2 - x1) < 1e-14:
            return None                                                         # pinned at a bound without reaching the target
        x0, f0, x1 = x1, f1, x2
    return None


def _offer(project: Any, sid: str, spec: Mapping[str, Any], out: dict[str, Any], rec: Mapping[str, Any], name: str, was: Any, to: float, base: Mapping[str, Any]) -> None:
    try:
        rep = verify(project, sid, {**base, name: to})
    except (BadRequest, SpatialFormatError, Conflict) as exc:
        out["unrepairable"].append({"expectation": rec["id"], "parameter": name, "reason": str(exc)})
        return
    remaining = [d["message"] for d in rep["diagnostics"] if d["severity"] == "error"]
    out["proposals"].append({"kind": "parameter", "parameter": name, "from": was, "to": to, "relative_change": 0.0 if not was else (to - was) / abs(was), "fixes": rec["id"],
                             "revalidated": rep["ok"], "remaining_failures": remaining, "evidence": [{"id": r["id"], "actual": r.get("actual"), "passed": r["passed"]} for r in rep["verification"]],
                             "commands": [{"op": "spec.run", "params": {"id": sid, "overrides": {**base, name: to}, "expected_revision": out["spec_revision"]}}],
                             "note": "changes the value of one parameter; the specification itself is unchanged"})
