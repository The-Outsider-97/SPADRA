"""Measured geometry state and geometric impact analysis.

Two things live here, and they are kept apart on purpose:

* **State** (this section): what an element's geometry *is*, measured from the meshes the project already evaluates (``documentation.world_mesh``, which includes
  the non-destructive modifiers): bounding box and size, surface area, volume, topology counts (vertices, edges, faces, boundary and non-manifold edges, shells,
  Euler characteristic), whether it is a closed solid, and a fingerprint of the shape. The same function serves the design-specification verifier (does the result match what
  was asked?) and the modifier impact analysis (what changed?). Measurements are on the tessellation the viewer shows: exact for planar solids, converging for curved ones.
* **Impact** (further down): what a modification *will* change, predicted from its parameters before the kernel runs, compared afterwards with what the kernel really did.

Lengths are metres, areas m², volumes m³ (the model's own units); nothing here changes the project."""
from __future__ import annotations

import hashlib
import math
from typing import Any, Mapping, Sequence

WELD = 1e-7                                    # m: vertices closer than this are one vertex when topology is counted
EMPTY_BOX = {"min": None, "max": None, "size": None}


def _np() -> Any:
    import numpy as np
    return np


def _round(value: Any, digits: int = 9) -> Any:
    if isinstance(value, (list, tuple)):
        return [_round(v, digits) for v in value]
    if isinstance(value, float):
        return round(value, digits)
    return value


# ----------------------------------------------------------------------------------------------------------------------- meshes
def mesh_state(vertices: Sequence[Sequence[float]], faces: Sequence[Sequence[int]]) -> dict[str, Any]:
    """The measured state of one triangle mesh (see the module docs). An empty or degenerate mesh is reported, not raised."""
    np = _np()
    if not len(vertices) or not len(faces):
        return {"empty": True, "bbox": dict(EMPTY_BOX), "area": 0.0, "volume": None, "closed": False, "valid": False, "fingerprint": "empty",
                "topology": {"vertices": 0, "edges": 0, "faces": 0, "boundary_edges": 0, "nonmanifold_edges": 0, "shells": 0, "euler": 0, "degenerate_faces": 0, "oriented": False},
                "issues": ["no geometry"]}
    V = np.asarray(vertices, dtype=float)
    F = np.asarray(faces, dtype=int)
    issues: list[str] = []
    if not np.isfinite(V).all():
        return {"empty": False, "bbox": dict(EMPTY_BOX), "area": 0.0, "volume": None, "closed": False, "valid": False, "fingerprint": "invalid",
                "topology": {"vertices": int(len(V)), "edges": 0, "faces": int(len(F)), "boundary_edges": 0, "nonmanifold_edges": 0, "shells": 0, "euler": 0, "degenerate_faces": 0, "oriented": False},
                "issues": ["a vertex is not a finite number"]}
    used = np.unique(F)
    lo, hi = V[used].min(axis=0), V[used].max(axis=0)
    A, B, C = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    cross = np.cross(B - A, C - A)
    area = float(0.5 * np.linalg.norm(cross, axis=1).sum())
    signed_volume = float(np.einsum("ij,ij->i", A, np.cross(B, C)).sum() / 6.0)
    keys = np.round(V[used] / WELD).astype(np.int64)
    _, first, inverse = np.unique(keys, axis=0, return_index=True, return_inverse=True)
    inverse = np.asarray(inverse).reshape(-1)
    welded = np.empty(len(V), dtype=int)
    welded[used] = inverse
    W = welded[F]
    keep = (W[:, 0] != W[:, 1]) & (W[:, 1] != W[:, 2]) & (W[:, 0] != W[:, 2])
    degenerate = int((~keep).sum())
    W = W[keep]
    nv = int(inverse.max()) + 1
    directed = np.concatenate([W[:, [0, 1]], W[:, [1, 2]], W[:, [2, 0]]]) if len(W) else np.zeros((0, 2), dtype=int)
    undirected = np.sort(directed, axis=1)
    if len(undirected):
        edge_keys, counts = np.unique(undirected, axis=0, return_counts=True)
        dkeys, dcounts = np.unique(directed, axis=0, return_counts=True)
    else:
        edge_keys, counts, dcounts = np.zeros((0, 2), dtype=int), np.zeros(0, dtype=int), np.zeros(0, dtype=int)
    boundary = int((counts == 1).sum())
    nonmanifold = int((counts > 2).sum())
    oriented = bool(len(dcounts) and (dcounts == 1).all() and boundary == 0 and nonmanifold == 0)     # every directed edge once, so every edge is met in opposite senses
    parent = list(range(nv))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for a, b in edge_keys.tolist():
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    present = np.unique(W) if len(W) else np.zeros(0, dtype=int)
    shells = len({find(int(v)) for v in present})
    edges = int(len(edge_keys))
    euler = int(len(present) - edges + len(W))
    closed = boundary == 0 and nonmanifold == 0 and len(W) > 0
    volume = signed_volume if closed and oriented else None
    if degenerate:
        issues.append(f"{degenerate} degenerate triangle(s)")
    if boundary:
        issues.append(f"open surface: {boundary} boundary edge(s)")
    if nonmanifold:
        issues.append(f"{nonmanifold} edge(s) shared by more than two faces")
    if closed and not oriented:
        issues.append("faces are not consistently oriented")
    if volume is not None and volume <= 1e-12:
        issues.append("the solid is inside out or has no volume")
    valid = closed and oriented and volume is not None and volume > 1e-12
    digest = hashlib.sha1()
    digest.update(np.round(V[used], 7).tobytes())
    digest.update(F.astype(np.int64).tobytes())
    return {"empty": False, "bbox": {"min": _round(lo.tolist()), "max": _round(hi.tolist()), "size": _round((hi - lo).tolist())}, "area": round(area, 9),
            "volume": None if volume is None else round(volume, 9), "closed": closed, "valid": bool(valid), "fingerprint": digest.hexdigest()[:16],
            "topology": {"vertices": int(len(present)), "edges": edges, "faces": int(len(W)), "boundary_edges": boundary, "nonmanifold_edges": nonmanifold, "shells": int(shells),
                         "euler": euler, "degenerate_faces": degenerate, "oriented": oriented},
            "issues": issues}


def element_state(project: Any, oid: str) -> dict[str, Any]:
    """The measured state of a model element as the viewer shows it (modifiers included, world coordinates)."""
    from . import documentation as D
    try:
        vertices, faces = D.world_mesh(project, oid)
    except Exception as exc:                                                    # an element with no geometry, or one the kernel cannot tessellate
        return {**mesh_state([], []), "issues": [f"no measurable geometry: {exc}"]}
    state = mesh_state(vertices, faces)
    meta = project.model.get(oid).get("metadata", {})
    state["id"] = oid
    state["category"] = meta.get("spadra:category")
    return state


def drawing_state(doc: Any, eid: str) -> dict[str, Any]:
    """The measured state of one 2D entity: bounding box and size, length (open) or area (closed), vertex count."""
    e = doc.get(eid)
    g, kind = e["geometry"], e["kind"]
    pts: list[list[float]] = []
    length = 0.0
    area = None
    closed = False
    if kind == "line":
        pts = [list(g["start"]), list(g["end"])]
        length = math.dist(*pts)
    elif kind == "polyline":
        pts = [list(p) for p in g["points"]]
        closed = bool(g.get("closed"))
        loop = pts + ([pts[0]] if closed and pts else [])
        length = sum(math.dist(a, b) for a, b in zip(loop, loop[1:]))
        if closed and len(pts) >= 3:
            area = abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1])) / 2.0)
    elif kind == "circle":
        c, r = g["center"], g["radius"]
        pts = [[c[0] - r, c[1] - r], [c[0] + r, c[1] + r]]
        length, area, closed = 2 * math.pi * r, math.pi * r * r, True
    elif kind == "hatch":
        total = 0.0
        for k, loop in enumerate(g.get("boundary", [])):
            lp = [list(p) for p in loop.get("points", [])] if loop.get("kind") == "polygon" else []
            pts += lp
            if len(lp) >= 3:
                a = abs(sum(p[0] * q[1] - q[0] * p[1] for p, q in zip(lp, lp[1:] + lp[:1])) / 2.0)
                total += a if k == 0 else -a
        area, closed = total, True
    if not pts:
        return {"id": eid, "kind": kind, "empty": True, "bbox": dict(EMPTY_BOX), "length": 0.0, "area": None, "closed": closed, "vertices": 0, "fingerprint": "empty"}
    lo = [min(p[i] for p in pts) for i in (0, 1)]
    hi = [max(p[i] for p in pts) for i in (0, 1)]
    return {"id": eid, "kind": kind, "empty": False, "bbox": {"min": _round(lo), "max": _round(hi), "size": _round([hi[0] - lo[0], hi[1] - lo[1]])}, "length": round(length, 9),
            "area": None if area is None else round(area, 9), "closed": closed, "vertices": len(pts),
            "fingerprint": hashlib.sha1(repr(_round(pts)).encode()).hexdigest()[:16]}


# ----------------------------------------------------------------------------------------------------------------------- comparison
def _delta(before: Any, after: Any) -> Any:
    if before is None or after is None:
        return None
    if isinstance(before, (list, tuple)):
        return [round(b - a, 9) for a, b in zip(before, after)]
    return round(after - before, 9)


def diff_states(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """What changed between two measured states (``after - before``), for the bounding box, size, area, volume and every topology count."""
    out: dict[str, Any] = {"geometry_changed": before.get("fingerprint") != after.get("fingerprint"),
                           "bbox_min": _delta(before["bbox"]["min"], after["bbox"]["min"]), "bbox_max": _delta(before["bbox"]["max"], after["bbox"]["max"]),
                           "size": _delta(before["bbox"]["size"], after["bbox"]["size"]), "area": _delta(before.get("area"), after.get("area")),
                           "volume": _delta(before.get("volume"), after.get("volume"))}
    bt, at = before.get("topology") or {}, after.get("topology") or {}
    out["topology"] = {k: at[k] - bt[k] for k in at if k in bt and isinstance(at[k], int) and not isinstance(at[k], bool)}
    out["became_valid"] = bool(after.get("valid")) and not before.get("valid")
    out["became_invalid"] = bool(before.get("valid")) and not after.get("valid")
    return out


# ======================================================================================================================= prediction
# What a modifier *will* do, computed from its parameters and the current geometry before the kernel runs. Every number says how it was obtained: ``exact`` (a
# closed-form result for the geometry given), ``bounds`` (a guaranteed interval), or nothing (the modifier has no analytic predictor; that is said, not guessed).

def _box_like(state: Mapping[str, Any]) -> bool:
    size = state["bbox"]["size"]
    if size is None or state.get("volume") is None:
        return False
    full = size[0] * size[1] * size[2]
    return full > 0 and abs(state["volume"] - full) <= 1e-9 * max(1.0, full)


def _box_volume(lo: Sequence[float], hi: Sequence[float]) -> float:
    return math.prod(max(0.0, h - l) for l, h in zip(lo, hi))


def _box_difference(a: tuple[list[float], list[float]], b: tuple[list[float], list[float]]) -> list[tuple[list[float], list[float]]]:
    """``a - b`` for axis-aligned boxes as up to six disjoint boxes (empty list: nothing left)."""
    alo, ahi = [list(a[0]), list(a[1])]
    blo, bhi = b
    if any(min(ahi[i], bhi[i]) - max(alo[i], blo[i]) <= 0 for i in range(3)):
        return [(alo, ahi)]
    pieces = []
    lo, hi = list(alo), list(ahi)
    for i in range(3):
        if blo[i] > lo[i]:
            pieces.append((list(lo), [*hi[:i], blo[i], *hi[i + 1:]]))
            lo[i] = blo[i]
        if bhi[i] < hi[i]:
            pieces.append(([*lo[:i], bhi[i], *lo[i + 1:]], list(hi)))
            hi[i] = bhi[i]
    return [p for p in pieces if _box_volume(*p) > 0]


def split_prediction(vertices: Sequence[Sequence[float]], faces: Sequence[Sequence[int]], origin: Sequence[float], normal: Sequence[float], side: str,
                     matrix: Sequence[float] | None = None) -> dict[str, Any]:
    """Exact volume and bounding box of a closed triangle mesh clipped by a plane (the kept side is ``side``). The cap is accounted for through the divergence theorem, so
    no section loop has to be assembled: the cap's area vector is minus the sum of the clipped faces' area vectors.

    The plane is given in the frame of ``vertices``, which is the frame the modifier kernel uses (the object's own geometry frame). ``matrix`` (the row-major local-to-world 3x4 or 4x4
    transform) maps the clipped result into world space, where the bounding box is reported and the volume is scaled by the determinant of its linear part."""
    np = _np()
    V, F = np.asarray(vertices, float), np.asarray(faces, int)
    n = np.asarray(normal, float)
    n = n / np.linalg.norm(n)
    if side == "negative":
        n = -n
    o = np.asarray(origin, float)
    s = (V - o) @ n
    polys: list[Any] = []
    for tri in F:
        poly = []
        for k in range(3):
            a, b = tri[k], tri[(k + 1) % 3]
            sa, sb = s[a], s[b]
            if sa >= 0:
                poly.append(V[a])
            if (sa >= 0) != (sb >= 0) and sa != sb:
                t = sa / (sa - sb)
                poly.append(V[a] + t * (V[b] - V[a]))
        if len(poly) >= 3:
            polys.append(np.array(poly))
    if not polys:
        return {"empty": True, "volume": 0.0, "bbox": dict(EMPTY_BOX), "method": "plane clip of the closed mesh; nothing remains on the kept side"}
    vol6 = 0.0
    area_vec = np.zeros(3)
    pts = []
    for poly in polys:
        for k in range(1, len(poly) - 1):
            a, b, c = poly[0], poly[k], poly[k + 1]
            vol6 += float(a @ np.cross(b, c))
            area_vec += 0.5 * np.cross(b - a, c - a)
        pts.append(poly)
    cap_area = float(n @ area_vec)
    volume = vol6 / 6.0 + (1.0 / 3.0) * (-float(n @ o)) * cap_area
    allp = np.vstack(pts)
    if matrix is not None:
        M = np.asarray(matrix, float).reshape(-1)[:12].reshape(3, 4)             # a 4x4 row-major matrix is accepted: its last row is not used
        allp = allp @ M[:, :3].T + M[:, 3]
        volume *= abs(float(np.linalg.det(M[:, :3])))
        cap_area = float("nan") if not np.allclose(M[:, :3], np.eye(3)) else cap_area
    lo, hi = allp.min(axis=0), allp.max(axis=0)
    return {"empty": volume <= 1e-12, "volume": round(volume, 9), "bbox": {"min": _round(lo.tolist()), "max": _round(hi.tolist()), "size": _round((hi - lo).tolist())},
            "cap_area": round(cap_area, 9), "method": "exact plane clip of the closed mesh (divergence theorem)"}


def predict(project: Any, params: Mapping[str, Any]) -> dict[str, Any]:
    """The deterministic prediction for a model modifier request (``{"id", "operation", "parameters", "operand"?}``); ``{"available": False, "reason"}`` when there is none."""
    from . import documentation as D
    op = params.get("operation")
    oid = params.get("id")
    p = params.get("parameters") or {}
    try:
        verts, faces = D.world_mesh(project, oid)
    except Exception as exc:
        return {"available": False, "reason": f"the target has no measurable geometry: {exc}"}
    state = mesh_state(verts, faces)
    if state["empty"] or state["volume"] is None:
        return {"available": False, "reason": "the target is not a closed solid, so a volume cannot be predicted"}
    if op == "split":
        try:
            origin = p.get("origin", [0.0, 0.0, 0.0])
            mesh = D.evaluated_meshes(project).get(oid) or project.model.tessellate(oid)           # the kernel cuts in the object's own frame, not in world space
            r = split_prediction(mesh["vertices"], mesh["faces"], origin, p["normal"], p.get("side", "positive"), project.model.world_transform(oid))
        except (KeyError, ValueError, ZeroDivisionError) as exc:
            return {"available": False, "reason": f"the split parameters are incomplete: {exc}"}
        return {"available": True, "operation": op, "exact": {"volume": r["volume"], "bbox": r["bbox"]}, "empty": r["empty"], "method": r["method"]}
    if op == "boolean":
        try:
            ov, of = D.world_mesh(project, params["operand"])
        except Exception as exc:
            return {"available": False, "reason": f"the operand has no measurable geometry: {exc}"}
        other = mesh_state(ov, of)
        lo_a, hi_a, lo_b, hi_b = state["bbox"]["min"], state["bbox"]["max"], other["bbox"]["min"], other["bbox"]["max"]
        ilo = [max(lo_a[i], lo_b[i]) for i in range(3)]
        ihi = [min(hi_a[i], hi_b[i]) for i in range(3)]
        vi_box = _box_volume(ilo, ihi)
        va, vb = state["volume"], other["volume"] or 0.0
        mode = p.get("operation")
        ulo, uhi = [min(lo_a[i], lo_b[i]) for i in range(3)], [max(hi_a[i], hi_b[i]) for i in range(3)]
        if _box_like(state) and _box_like(other):
            if mode == "union":
                vol, box = va + vb - vi_box, (ulo, uhi)
            elif mode == "intersect":
                vol, box = vi_box, (ilo, ihi) if vi_box > 0 else None
            else:
                pieces = _box_difference((lo_a, hi_a), (lo_b, hi_b))
                vol = sum(_box_volume(*q) for q in pieces)
                box = ([min(q[0][i] for q in pieces) for i in range(3)], [max(q[1][i] for q in pieces) for i in range(3)]) if pieces else None
            bb = None if box is None else {"min": _round(box[0]), "max": _round(box[1]), "size": _round([h - l for l, h in zip(*box)])}
            return {"available": True, "operation": op, "exact": {"volume": round(vol, 9), "bbox": bb or dict(EMPTY_BOX)}, "empty": vol <= 1e-12,
                    "method": "exact: both operands are axis-aligned boxes (volume equals bounding-box volume)"}
        if mode == "union":
            bounds = {"volume": [max(va, vb), va + vb], "bbox": {"min": _round(ulo), "max": _round(uhi)}}
        elif mode == "intersect":
            cap = min(va, vb, vi_box)
            bounds = {"volume": [0.0, cap], "bbox_within": {"min": _round(ilo), "max": _round(ihi)}}
            if vi_box <= 1e-12:                         # bounding boxes that only touch or do not meet cannot share volume: the intersection is provably empty
                return {"available": True, "operation": op, "bounds": bounds, "empty": True, "method": "the operands' bounding boxes share no volume, so the intersection is empty"}
        else:
            cap = min(va, vb, vi_box)
            bounds = {"volume": [max(0.0, va - cap), va], "bbox_within": {"min": state["bbox"]["min"], "max": state["bbox"]["max"]}}
        return {"available": True, "operation": op, "bounds": bounds, "method": "interval bounds from bounding boxes and volumes (the operands are not both boxes)"}
    if op == "chamfer":
        sx, sy, sz = state["bbox"]["size"]
        a = float(p.get("amount", 0.0))
        d = float(p.get("depth", a))
        edges = 4 * (sx + sy + sz)
        removed_upper = a * d / 2.0 * edges
        return {"available": True, "operation": op, "exact": {"bbox": {"min": state["bbox"]["min"], "max": state["bbox"]["max"], "size": state["bbox"]["size"]}},
                "bounds": {"volume": [max(0.0, state["volume"] - removed_upper), state["volume"]]},
                "method": "a chamfer never changes the bounding box; its volume lies between the box minus every edge prism (corner overlaps counted twice) and the box"}
    if op == "bend":
        return {"available": False, "reason": "a bend is a numerical deformation with no analytic predictor; only the measured result is reported"}
    return {"available": False, "reason": f"no analytic predictor for '{op}'"}


def audit_prediction(pred: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Predicted versus measured, metric by metric. ``within`` is true when the measurement matches an exact prediction to 1e-6 (relative to the geometry's scale) or lies in a bound."""
    out: list[dict[str, Any]] = []
    if not pred.get("available"):
        return out
    scale = max(1.0, *(after["bbox"]["size"] or [1.0]))
    exact = pred.get("exact") or {}
    if "volume" in exact:
        actual = after.get("volume") or 0.0
        err = actual - exact["volume"]
        out.append({"metric": "volume", "kind": "exact", "predicted": exact["volume"], "actual": actual, "error": round(err, 9), "within": abs(err) <= 1e-6 * scale ** 3})
    box = exact.get("bbox")
    if box and box.get("size") is not None and after["bbox"]["size"] is not None:
        errs = [round(a - p, 9) for a, p in zip(after["bbox"]["size"], box["size"])]
        out.append({"metric": "bbox_size", "kind": "exact", "predicted": box["size"], "actual": after["bbox"]["size"], "error": errs, "within": all(abs(e) <= 1e-6 * scale for e in errs)})
        errs = [round(a - p, 9) for a, p in zip(after["bbox"]["min"], box["min"])]
        out.append({"metric": "bbox_min", "kind": "exact", "predicted": box["min"], "actual": after["bbox"]["min"], "error": errs, "within": all(abs(e) <= 1e-6 * scale for e in errs)})
    b = pred.get("bounds") or {}
    if "volume" in b:
        lo, hi = b["volume"]
        actual = after.get("volume") or 0.0
        tol = 1e-6 * scale ** 3
        out.append({"metric": "volume", "kind": "bounds", "predicted": [lo, hi], "actual": actual, "within": lo - tol <= actual <= hi + tol})
    if "bbox_within" in b and after["bbox"]["min"] is not None:
        w = b["bbox_within"]
        tol = 1e-6 * scale
        out.append({"metric": "bbox_within", "kind": "bounds", "predicted": w, "actual": {"min": after["bbox"]["min"], "max": after["bbox"]["max"]},
                    "within": all(after["bbox"]["min"][i] >= w["min"][i] - tol and after["bbox"]["max"][i] <= w["max"][i] + tol for i in range(3))})
    if "bbox" in b and after["bbox"]["min"] is not None:
        w = b["bbox"]
        tol = 1e-6 * scale
        out.append({"metric": "bbox_union", "kind": "exact", "predicted": w, "actual": {"min": after["bbox"]["min"], "max": after["bbox"]["max"]},
                    "within": all(abs(after["bbox"]["min"][i] - w["min"][i]) <= tol and abs(after["bbox"]["max"][i] - w["max"][i]) <= tol for i in range(3))})
    return out


def learned_prediction() -> dict[str, Any]:
    """Whether a *trained* impact model exists here. None ships with SPADRA: this reports the probes it ran, and never invents a score."""
    import importlib.util
    from pathlib import Path
    probes = {"torch": importlib.util.find_spec("torch") is not None}
    weights = [str(p) for p in Path(__file__).parent.rglob("*.pt")][:3] + [str(p) for p in Path(__file__).parent.rglob("*.safetensors")][:3]
    probes["checkpoint_files"] = bool(weights)
    return {"available": False, "probes": probes,
            "reason": "no trained geometric-impact model is installed (" + ("PyTorch is missing and " if not probes["torch"] else "") + "no checkpoint file ships with SPADRA); the deterministic analysis below is the only prediction. "
                      "This is not a reproduction of CAD-WAM or any published predictor."}


# ======================================================================================================================= impact on the rest of the model
def dependents(project: Any, oid: str) -> list[dict[str, Any]]:
    """Everything that reads this element: modifiers that use it as an operand, elements whose metadata refers to it (hosts, cut targets, fixed-to...), design-specification operations."""
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(item: str, relation: str, name: str | None = None) -> None:
        if (item, relation) not in seen and item != oid:
            seen.add((item, relation))
            out.append({"id": item, "relation": relation, "name": name})

    for m in project.model.modifiers():
        if any(o.get("item_id") == oid for o in m.get("operands", [])):
            add(m["target"]["item_id"], f"{m['kind']} operand")
        if m["target"].get("item_id") == oid:
            add(oid + ":" + m["id"], f"modifier {m['kind']} on it")
    raw = getattr(project.model, "_model", None)
    for obj in (raw.objects if raw is not None else []):
        meta = obj.get("metadata") or {}
        for key, value in meta.items():
            if not key.startswith("spadra:") or key in ("spadra:spec",):
                continue
            if value == oid or (isinstance(value, list) and oid in value):
                add(obj["id"], key.split(":", 1)[1], obj.get("name"))
    for sid, ent in (project.manifest.get("design_specs") or {}).items():
        for op, st in ((ent.get("state") or {}).get("ops") or {}).items():
            if st.get("object") == oid:
                add(f"spec:{sid}:{op}", "design specification operation")
    return out


def collisions(project: Any, oid: str, state: Mapping[str, Any], limit: int = 300) -> dict[str, Any]:
    """Elements whose bounding box overlaps this one's. Bounding boxes only: an overlap is a *candidate* collision, and the absence of one is a guarantee of none."""
    found, checked = [], 0
    if state.get("bbox", {}).get("min") is None:
        return {"approximate": True, "checked": 0, "overlaps": [], "truncated": False}
    lo, hi = state["bbox"]["min"], state["bbox"]["max"]
    truncated = False
    for other in project.model.query_ids(limit=limit + 1):
        if other == oid:
            continue
        if checked >= limit:
            truncated = True
            break
        checked += 1
        s = element_state(project, other)
        if s.get("bbox", {}).get("min") is None:
            continue
        ov = [min(hi[i], s["bbox"]["max"][i]) - max(lo[i], s["bbox"]["min"][i]) for i in range(3)]
        if all(v > 1e-9 for v in ov):
            found.append({"id": other, "name": project.model.get(other).get("name"), "overlap_box_volume": round(math.prod(ov), 9)})
    return {"approximate": True, "checked": checked, "overlaps": found, "truncated": truncated,
            "note": "axis-aligned bounding boxes: an overlap is a candidate, not a proven interference"}


# ======================================================================================================================= the analysis a preview returns
def _contract(kind: str, project: Any, params: Mapping[str, Any]) -> dict[str, Any]:
    return {"contract": "spadra.preview.v1", "kind": kind, "source_revision": project.revision, "operation": params.get("operation"), "target": params.get("id")}


def analyze_model_modifier(project: Any, draft: Any, params: Mapping[str, Any], result: Mapping[str, Any] | None, error: Exception | None = None) -> dict[str, Any]:
    """The geometric impact of a model modifier: measured before/after state, the deterministic prediction and how it compares, dependents, candidate collisions, diagnostics.

    ``draft`` is the throw-away copy the request was executed on (``result`` is None and ``error`` set when the kernel refused it): the user's project is read, never written."""
    oid = params["id"]
    out = _contract("model", project, params)
    before = element_state(project, oid)
    pred = predict(project, {"id": oid, "operation": params.get("operation"), "parameters": params.get("parameters"), "operand": params.get("operand")})
    out.update(before=before, predicted=pred, learned=learned_prediction(), dependents=dependents(project, oid), diagnostics=[])
    diags = out["diagnostics"]
    if not before["valid"]:
        diags.append({"code": "before.invalid", "severity": "warning", "message": "the target is not a closed solid with positive volume before the change: " + "; ".join(before["issues"])})
    if error is not None or result is None:
        diags.append({"code": "kernel.refused", "severity": "error", "message": str(error) if error else "the modifier produced no result",
                      "hint": getattr(error, "hint", None)})
        if pred.get("available") and pred.get("empty"):
            diags.append({"code": "prediction.empty", "severity": "info", "message": "the analytic prediction also finds nothing left (" + pred["method"] + "), so this is a genuine empty result, not a kernel failure"})
        out.update(valid=False, after=None, diff=None, audit=[], impact={"added": [], "removed": [], "modified": []})
        return out
    after_id = result["id"]
    after = element_state(draft, after_id)
    out["after"] = after
    out["diff"] = diff_states(before, after)
    out["audit"] = audit_prediction(pred, after)
    wrong = [a for a in out["audit"] if not a["within"]]
    if wrong:
        diags.append({"code": "prediction.mismatch", "severity": "warning", "message": "the kernel result differs from the analytic prediction for: " + ", ".join(a["metric"] for a in wrong),
                      "hint": "the measured result is authoritative; the prediction is advisory"})
    if after["empty"] or (after["volume"] is not None and after["volume"] <= 1e-12):
        diags.append({"code": "result.empty", "severity": "error", "message": "the result has no volume"})
    elif not after["valid"]:
        diags.append({"code": "after.invalid", "severity": "warning", "message": "the result is not a closed oriented solid: " + "; ".join(after["issues"]),
                      "hint": "it is a display result; check it before relying on its volume"})
    if result.get("mode") == "non_destructive":
        diags.append({"code": "mode.non_destructive", "severity": "info", "message": "the source geometry is kept; the modifier can be edited, reordered or removed"})
    approx = (result.get("diagnostics") or {}).get("approximate")
    if approx:
        diags.append({"code": "result.approximate", "severity": "info", "message": "the kernel reports an approximate (numerical) result"})
    new_overlaps = collisions(draft, after_id, after)
    old_ids = {c["id"] for c in collisions(project, oid, before)["overlaps"]}
    new_overlaps["introduced"] = [c for c in new_overlaps["overlaps"] if c["id"] not in old_ids]
    out["collisions"] = new_overlaps
    copy_made = result.get("derived_from") is not None
    out["impact"] = {"added": [after_id] if copy_made else [], "removed": [], "modified": [] if copy_made else [oid],
                     "modifiers_added": [result["modifier_id"]] if result.get("modifier_id") else []}
    out["valid"] = not any(d["severity"] == "error" for d in diags)
    out["apply"] = {"op": "model.modifier", "params": {k: v for k, v in params.items() if k != "kind"}, "expected_revision": project.revision}
    return out


def analyze_drawing_modifier(project: Any, draft: Any, params: Mapping[str, Any], result: Mapping[str, Any] | None, error: Exception | None = None) -> dict[str, Any]:
    """The same contract for a 2D modifier: measured before/after state of the drawing entity (no analytic predictor exists for 2D modifiers, and the analysis says so)."""
    out = _contract("drawing", project, params)
    view, source = params["drawing"], params["id"]
    before = drawing_state(project.drawing(view), source)
    out.update(before=before, predicted={"available": False, "reason": "no analytic predictor exists for 2D modifiers; the measured result is reported"}, learned=learned_prediction(), diagnostics=[])
    if error is not None or result is None:
        out["diagnostics"].append({"code": "kernel.refused", "severity": "error", "message": str(error) if error else "the modifier produced no result", "hint": getattr(error, "hint", None)})
        out.update(valid=False, after=None, diff=None, audit=[], impact={"added": [], "removed": [], "modified": []})
        return out
    after = drawing_state(draft.drawing(view), result["id"])
    out["after"] = after
    out["diff"] = {"geometry_changed": before["fingerprint"] != after["fingerprint"], "length": _delta(before["length"], after["length"]), "area": _delta(before["area"], after["area"]),
                   "bbox_size": _delta(before["bbox"]["size"], after["bbox"]["size"]), "vertices": after["vertices"] - before["vertices"]}
    out["audit"] = []
    out["impact"] = {"added": [result["id"]] if result["id"] != source else [], "removed": [], "modified": [source] if result["id"] == source else []}
    out["dependents"] = []
    out["valid"] = True
    out["apply"] = {"op": "draw.modifier", "params": {k: v for k, v in params.items() if k != "kind"}, "expected_revision": project.revision}
    return out
