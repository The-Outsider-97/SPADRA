"""Predicted versus measured modifier impact over a deterministic suite of operations.

Run it (Windows PowerShell, from the repository root):  ``python -m applications.SPADRA.tests.evaluate_impact [--json out.json]``

Every operation is built from scratch in a real SPADRA project and executed by the real kernel on a throw-away copy (exactly what the preview does), and the
deterministic prediction (``impact.predict``) is compared with the measured result: bounding box, volume (area), topology validity, dependents, candidate collisions and
timing. Operations the kernel cannot run *here* (no trimesh, for meshes) are reported as ``unavailable`` - they are neither counted as passes nor dropped - and, for the
mesh splits, the predictor is additionally checked against an independent oracle (an exact 2D polygon clip times the height) that does not use the kernel.

There are no invented thresholds: an exact prediction must match to 1e-6 of the geometry's scale (the project's own modifier tolerance is 1e-6 m), a bound must contain
the measurement, and both are stated in the report."""
from __future__ import annotations

import json
import math
import sys
import time
from typing import Any

from applications.SPADRA import impact as IM
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest

LEVEL = None


def _project() -> tuple[Project, str]:
    p = Project.create("impact", unit="mm")
    return p, p.manifest["levels"][0]["id"]


def _case_boolean_boxes() -> list[dict[str, Any]]:
    out = []
    for mode in ("union", "subtract", "intersect"):
        for dx in (-1.5, -0.5, 0.0, 0.5, 1.0, 1.5, 3.0):
            for dy in (0.0, 0.75):
                out.append({"group": "boolean/aligned boxes", "operation": "boolean", "mode": mode, "dx": dx, "dy": dy})
    return out


def _case_split_axis() -> list[dict[str, Any]]:
    out = []
    for axis in range(3):
        for origin in (-0.8, -0.25, 0.0, 0.4, 0.9):
            for side in ("positive", "negative"):
                out.append({"group": "split/axis-aligned box", "operation": "split", "axis": axis, "origin": origin, "side": side})
    return out


def _case_split_oblique() -> list[dict[str, Any]]:
    out = []
    for n in ([1, 1, 0], [1, 2, 0], [1, 1, 1], [2, -1, 1], [0, 1, 1]):
        for origin in (-0.3, 0.2, 0.6):
            for side in ("positive", "negative"):
                out.append({"group": "split/oblique plane on a box", "operation": "split", "normal": n, "origin_t": origin, "side": side})
    return out


def _case_chamfer() -> list[dict[str, Any]]:
    return [{"group": "chamfer/analytic box", "operation": "chamfer", "amount": a, "segments": 1} for a in (0.02, 0.05, 0.1, 0.2, 0.3, 0.45, 0.6, 0.8)]


def _case_boolean_mesh() -> list[dict[str, Any]]:
    out = []
    for mode in ("union", "subtract", "intersect"):
        for dx in (0.5, 1.5, 3.0):
            out.append({"group": "boolean/extrusions (meshes)", "operation": "boolean", "mode": mode, "dx": dx})
    return out


def _case_split_mesh() -> list[dict[str, Any]]:
    out = []
    for x in (0.3, 0.8, 1.5, 2.2, 2.7):
        for side in ("positive", "negative"):
            out.append({"group": "split/non-convex L extrusion", "operation": "split", "x": x, "side": side})
    return out


L_SHAPE = [[0, 0], [3000, 0], [3000, 1000], [1000, 1000], [1000, 3000], [0, 3000]]          # mm; area 5 m^2, depth 2 m -> 10 m^3


def _clip_area(poly: list[list[float]], x: float, keep_greater: bool) -> float:
    """Independent oracle: Sutherland-Hodgman clip of a (possibly concave) polygon by the line X = x, then the shoelace area. Exact for a single half-plane."""
    inside = (lambda p: p[0] >= x) if keep_greater else (lambda p: p[0] <= x)
    out: list[list[float]] = []
    for a, b in zip(poly, poly[1:] + poly[:1]):
        ia, ib = inside(a), inside(b)
        if ia:
            out.append(a)
        if ia != ib:
            t = (x - a[0]) / (b[0] - a[0])
            out.append([x, a[1] + t * (b[1] - a[1])])
    return abs(sum(p[0] * q[1] - q[0] * p[1] for p, q in zip(out, out[1:] + out[:1])) / 2.0) if len(out) >= 3 else 0.0


def _build(case: dict[str, Any]) -> tuple[Project, dict[str, Any], dict[str, Any] | None]:
    """The project, the request, and (for the oracle cases) the independent expected volume."""
    p, level = _project()
    g = case["group"]
    oracle = None
    if g.startswith("boolean/aligned"):
        a = p.model.add_box(width=2.0, depth=2.0, height=2.0, name="A")
        b = p.model.add_box(width=2.0, depth=2.0, height=2.0, center=(case["dx"], case["dy"], 0.0), name="B")
        req = {"id": a, "operation": "boolean", "operand": b, "parameters": {"operation": case["mode"]}}
    elif g.startswith("split/axis"):
        a = p.model.add_box(width=2.0, depth=2.0, height=2.0, name="A")
        n = [0, 0, 0]
        n[case["axis"]] = 1
        o = [0.0, 0.0, 0.0]
        o[case["axis"]] = case["origin"]
        req = {"id": a, "operation": "split", "parameters": {"origin": o, "normal": n, "side": case["side"]}}
        oracle = (2.0 * 2.0 * (1.0 - case["origin"]) if case["side"] == "positive" else 2.0 * 2.0 * (case["origin"] + 1.0))
    elif g.startswith("split/oblique"):
        a = p.model.add_box(width=2.0, depth=2.0, height=2.0, name="A")
        n = case["normal"]
        req = {"id": a, "operation": "split", "parameters": {"origin": [case["origin_t"], 0.0, 0.0], "normal": n, "side": case["side"]}}
    elif g.startswith("chamfer"):
        a = p.model.add_box(width=2.0, depth=2.0, height=2.0, name="A")
        req = {"id": a, "operation": "chamfer", "parameters": {"amount": case["amount"], "depth": case["amount"], "segments": 1}}
    elif g.startswith("boolean/extrusions"):
        sq = [[0, 0], [2000, 0], [2000, 2000], [0, 2000]]
        a = p.execute("model.add_extrusion", {"level": level, "points": sq, "depth": 2.0})["result"]["id"]
        b = p.execute("model.add_extrusion", {"level": level, "points": [[x + case["dx"] * 1000 - 1000 + 1000, y] for x, y in sq], "depth": 2.0, "base": 0.5})["result"]["id"]
        req = {"id": a, "operation": "boolean", "operand": b, "parameters": {"operation": case["mode"]}}
    else:                                                                       # L extrusion split by a vertical plane x = case["x"] in its own frame (world x = x + 1.5)
        a = p.execute("model.add_extrusion", {"level": level, "points": L_SHAPE, "depth": 2.0})["result"]["id"]
        req = {"id": a, "operation": "split", "parameters": {"origin": [case["x"], 0.0, 0.0], "normal": [1, 0, 0], "side": case["side"]}}
        tx = p.model.world_transform(a)[3]                                      # the plane is in the extrusion's own frame, whose origin is the middle of its bounding box
        area = _clip_area([[x / 1000, y / 1000] for x, y in L_SHAPE], case["x"] + tx, case["side"] == "positive")
        oracle = area * 2.0
    if "policy" not in req:
        req["policy"] = "tolerance_controlled"
    return p, req, ({"volume": oracle} if oracle is not None else None)


def suite() -> list[dict[str, Any]]:
    return _case_boolean_boxes() + _case_split_axis() + _case_split_oblique() + _case_chamfer() + _case_boolean_mesh() + _case_split_mesh()


def evaluate() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for case in suite():
        p, req, oracle = _build(case)
        t0 = time.perf_counter()
        pred = IM.predict(p, req)
        t_pred = (time.perf_counter() - t0) * 1000
        draft = p.scratch()
        t0 = time.perf_counter()
        error = None
        result = None
        try:
            result = draft.execute("model.modifier", req)["result"]
        except Exception as exc:                                               # the kernel's refusal or a missing backend: recorded, not hidden
            error = exc
        t_kernel = (time.perf_counter() - t0) * 1000
        analysis = IM.analyze_model_modifier(p, draft, req, result, error)
        row: dict[str, Any] = {"group": case["group"], "operation": req["operation"], "parameters": {k: v for k, v in case.items() if k not in ("group", "operation")},
                               "predicted_available": pred.get("available", False), "predict_ms": round(t_pred, 3), "kernel_ms": round(t_kernel, 3)}
        if error is not None:
            missing = "trimesh" in str(error) or "numpy" in str(error)
            row.update(status="unavailable" if missing else "refused", reason=str(error), refusal_predicted=bool(pred.get("available") and pred.get("empty")),
                       kernel_limit=bool(not (pred.get("available") and pred.get("empty")) and any(s in str(error) for s in KERNEL_LIMITS)))
        else:
            row.update(status="measured", audit=analysis["audit"], within=all(a["within"] for a in analysis["audit"]),
                       actual_volume=analysis["after"]["volume"], actual_valid=analysis["after"]["valid"], dependents=len(analysis["dependents"]),
                       collisions=len(analysis["collisions"]["overlaps"]), introduced=len(analysis["collisions"]["introduced"]),
                       topology_delta=analysis["diff"]["topology"], max_volume_error=max([abs(a["error"]) for a in analysis["audit"] if a["metric"] == "volume" and "error" in a] or [0.0]))
        if oracle is not None and pred.get("available") and "exact" in pred:
            row["oracle_volume"] = round(oracle["volume"], 9)
            row["oracle_error"] = round(pred["exact"]["volume"] - oracle["volume"], 9)
            row["oracle_ok"] = abs(row["oracle_error"]) <= 1e-6
        rows.append(row)
    measured = [r for r in rows if r["status"] == "measured"]
    oracle_rows = [r for r in rows if "oracle_ok" in r]
    summary = {"operations": len(rows), "measured_by_kernel": len(measured), "unavailable_here": sum(r["status"] == "unavailable" for r in rows),
               "refused_by_kernel": sum(r["status"] == "refused" for r in rows), "refusals_predicted_empty": sum(r["status"] == "refused" and bool(r.get("refusal_predicted")) for r in rows),
               "refusals_kernel_limit": sum(r["status"] == "refused" and bool(r.get("kernel_limit")) for r in rows),
               "refusals_unexplained": sum(r["status"] == "refused" and not r.get("refusal_predicted") and not r.get("kernel_limit") for r in rows), "predictions_within": sum(r["within"] for r in measured),
               "predictions_outside": sum(not r["within"] for r in measured), "oracle_checked": len(oracle_rows), "oracle_agree": sum(r["oracle_ok"] for r in oracle_rows),
               "max_volume_error_m3": max([r["max_volume_error"] for r in measured] or [0.0]),
               "median_predict_ms": sorted(r["predict_ms"] for r in rows)[len(rows) // 2], "median_kernel_ms": sorted(r["kernel_ms"] for r in rows)[len(rows) // 2],
               "by_group": {}}
    for r in rows:
        grp = summary["by_group"].setdefault(r["group"], {"n": 0, "measured": 0, "within": 0, "unavailable": 0, "refused": 0})
        grp["n"] += 1
        grp["measured"] += r["status"] == "measured"
        grp["within"] += bool(r.get("within"))
        grp["unavailable"] += r["status"] == "unavailable"
        grp["refused"] += r["status"] == "refused"
    return {"summary": summary, "rows": rows, "tolerance": "exact predictions: 1e-6 of the geometry scale; bounds: must contain the measurement",
            "environment": IM_ENV()}


# Reasons with which the mesh kernel (trimesh) has been seen to refuse a case for which the exact analytic prediction exists and is non-empty: a limitation of the kernel's
# cap triangulation / Boolean backend, counted and reported on its own so that it can never hide an unexplained refusal.
KERNEL_LIMITS = ("degenerate or overflowing mesh triangle", "mesh split and cap failed", "manifold Boolean could not construct a valid result")


def IM_ENV() -> dict[str, Any]:
    from applications.SPADRA import design_spec as DSP
    return {k: v["available"] for k, v in DSP.capabilities()["capabilities"].items()}


def main(argv: list[str]) -> int:
    report = evaluate()
    if "--json" in argv:
        with open(argv[argv.index("--json") + 1], "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1)
    print(json.dumps(report["summary"], indent=1))
    s = report["summary"]
    return 0 if s["predictions_outside"] == 0 and s["refusals_unexplained"] == 0 and report["summary"]["oracle_checked"] == report["summary"]["oracle_agree"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
