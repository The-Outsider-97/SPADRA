"""Measure (not claim) how long the parametric operations take.

    python -m applications.SPADRA.tests.benchmark_parametric [--runs 5] [--json docs/parametric_benchmarks.json]
        [--only dependents --scales 1,10,100,1000 --budget-s 120]

Each operation is timed ``runs`` times (minimum, median, maximum in milliseconds). Project commands are called directly (not through HTTP), so these are the cost
of the operation itself. The numbers describe the machine they were taken on and nothing else; nothing here is a threshold, and a different machine will differ.
"Cold" is the first call in the process (imports, caches empty); "warm" is the median of the later ones.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from src.utils.spatial_formats.sgeo import parameters as SP
from applications.SPADRA import optimization as OPZ
from applications.SPADRA import scripting as SC
from applications.SPADRA import stairs as SR
from applications.SPADRA import door_swing as DS
from applications.SPADRA.project import Project


def timed(runs: int, fn, *, setup=None) -> dict:
    samples = []
    for _ in range(runs):
        ctx = setup() if setup else None
        t = time.perf_counter()
        fn(ctx) if setup else fn()
        samples.append((time.perf_counter() - t) * 1000.0)
    return {"min_ms": round(min(samples), 3), "median_ms": round(statistics.median(samples), 3), "max_ms": round(max(samples), 3), "runs": runs}


def quantiles(samples: list[float]) -> dict:
    """The report's measurement protocol asks for wall-clock quantiles, not a single number: p50, p95 (nearest rank), minimum and maximum."""
    ordered = sorted(samples)
    rank = lambda q: ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))]       # noqa: E731
    return {"p50_ms": round(rank(0.5), 3), "p95_ms": round(rank(0.95), 3), "min_ms": round(ordered[0], 3), "max_ms": round(ordered[-1], 3), "runs": len(ordered)}


def peak_rss_mb() -> float | None:
    """The process's peak resident set so far (a high-water mark: it never goes down inside one process), or None where the platform cannot say."""
    try:
        import resource
    except ImportError:
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)


def cold_warm(runs: int, fn) -> dict:
    t = time.perf_counter()
    fn()
    cold = (time.perf_counter() - t) * 1000.0
    warm = timed(runs, fn)
    return {"cold_ms": round(cold, 3), "warm": warm}


# ----------------------------------------------------------------------------------------------------------------------- parameters
def chains(n_chains: int, length: int) -> dict:
    specs = {}
    for c in range(n_chains):
        specs[f"b{c}"] = {"type": "number", "default": 1.0}
        prev = f"b{c}"
        for i in range(length):
            name = f"c{c}_{i}"
            specs[name] = {"type": "number", "expression": f"{prev} * 1.0001 + 1"}
            prev = name
    return specs


def bench_parameters(runs: int) -> list[dict]:
    out = []
    # a parameter table is capped at MAX_PARAMETERS (128) by design, so the sizes stop there
    for total in (16, 64, SP.MAX_PARAMETERS):
        specs = chains(4, total // 4 - 1)
        graph = SP.ParameterGraph(specs)
        full_values = graph.evaluate({})
        row = {"parameters": len(specs), "chains": 4, "chain_length": total // 4}
        row["full"] = timed(runs, lambda: graph.evaluate({}))
        row["incremental_one_chain"] = timed(runs, lambda: graph.evaluate({"b0": 2.0}, previous=full_values, changed={"b0"}))
        _, recomputed = graph.evaluate_with_report({"b0": 2.0}, previous=full_values, changed={"b0"})
        row["recomputed_in_incremental"] = len(recomputed)
        row["build_graph"] = timed(runs, lambda: SP.ParameterGraph(specs))
        out.append(row)
    return out


# -------------------------------------------------------------------------------------------------------------------------- families
def door_family(p: Project) -> str:
    return p.execute("family.define", {"name": "Door", "shape": "box", "parameters": {
        "width": {"default": 900, "min": 600, "max": 1800, "scope": "type"}, "height": {"default": 2100, "min": 1800, "max": 2600, "scope": "type"},
        "depth": {"default": 40}, "frame": {"default": 35, "min": 20, "max": 80},
        "clear_width": {"type": "length", "expression": "width - 2 * frame"}, "leaf_area": {"type": "area", "expression": "width * height"}}})["result"]["id"]


def bench_type_update(runs: int) -> list[dict]:
    out = []
    for n in (1, 10, 50, 100):
        p = Project.create("B")
        level = p.manifest["levels"][0]["id"]
        fid = door_family(p)
        p.execute("family.type_define", {"id": fid, "name": "D", "values": {"width": 900}})
        t = time.perf_counter()
        for i in range(n):
            p.execute("family.place", {"family": fid, "level": level, "point": [i * 1000, 0], "type": "D"})
        placed_ms = (time.perf_counter() - t) * 1000.0
        width = [900]

        def update():
            width[0] = 1000 if width[0] == 900 else 900
            p.execute("family.type_update", {"id": fid, "name": "D", "values": {"width": width[0]}})
        t = time.perf_counter()
        update()
        first = (time.perf_counter() - t) * 1000.0
        out.append({"instances": n, "placing_all_ms": round(placed_ms, 1), "type_update": {**timed(max(1, min(runs, 3)), update), "first_ms": round(first, 3)}})
    return out


def bench_dependents(runs: int, scales: tuple[int, ...] = (1, 10, 100), budget_s: float = 120.0, seed: int = 0) -> list[dict]:
    """The report's scale protocol (10.5): edit one type that 1, 10, 100 or 1,000 placed instances depend on, and compare with rebuilding the whole model.

    Per scale: setup time, the first (cold) edit, ``runs`` warm edits as quantiles, ``runs`` full rebuilds as quantiles, failure counts, and the process's peak RSS
    so far. Instances sit on a fixed grid (the ``seed`` is recorded; nothing here is random). A scale whose setup alone is projected to exceed ``budget_s``
    is *not measured* and says so; it is never filled in with an extrapolated number."""
    out, last = [], None
    for n in scales:
        row: dict = {"dependent_instances": n, "seed": seed, "failures": 0}
        if last is not None and last["setup_ms"] / 1000.0 * (n / last["dependent_instances"]) ** 2 > budget_s:
            projected = last["setup_ms"] / 1000.0 * (n / last["dependent_instances"]) ** 2
            out.append({**row, "measured": False, "reason": f"not measured: setup at the previous scale grew quadratically would need about {projected:.0f} s, above the {budget_s:g} s budget "
                                                            "(a projection from the measured scales, not a result)"})
            continue
        p = Project.create("Scale")
        level = p.manifest["levels"][0]["id"]
        fid = door_family(p)
        p.execute("family.type_define", {"id": fid, "name": "D", "values": {"width": 900}})
        started = time.perf_counter()
        for i in range(n):
            try:
                p.execute("family.place", {"family": fid, "level": level, "point": [(i % 40) * 1000, (i // 40) * 1000], "type": "D"})
            except Exception:
                row["failures"] += 1
        row["setup_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
        width = [900]

        def edit() -> float:
            width[0] = 1000 if width[0] == 900 else 900
            t = time.perf_counter()
            try:
                p.execute("family.type_update", {"id": fid, "name": "D", "values": {"width": width[0]}})
            except Exception:
                row["failures"] += 1
            return (time.perf_counter() - t) * 1000.0

        def rebuild() -> float:
            t = time.perf_counter()
            try:
                p.execute("model.rebuild", {})
            except Exception:
                row["failures"] += 1
            return (time.perf_counter() - t) * 1000.0

        row["cache_state"] = {"first_edit": "cold for this project (the parameter memo is warm from setup)", "later_edits": "warm"}
        row["first_edit_ms"] = round(edit(), 3)
        row["edit"] = quantiles([edit() for _ in range(max(1, runs))])
        row["full_rebuild"] = quantiles([rebuild() for _ in range(max(1, min(runs, 3)))])
        row["edit_vs_full_rebuild_p50"] = round(row["edit"]["p50_ms"] / row["full_rebuild"]["p50_ms"], 3) if row["full_rebuild"]["p50_ms"] else None
        row["peak_rss_mb_so_far"] = peak_rss_mb()
        row["measured"] = True
        out.append(row)
        last = row
    return out


# ------------------------------------------------------------------------------------------------------------------------ computation
def bench_optimization(runs: int) -> dict:
    p = Project.create("B")
    fid = door_family(p)
    p.execute("family.type_define", {"id": fid, "name": "D", "values": {"width": 900}})
    prob = {"family": fid, "type": "D", "variables": {"width": {"min": 700, "max": 1400}, "frame": {"min": 25, "max": 60}}, "objectives": [{"expression": "leaf_area", "sense": "min"}],
            "constants": {"m": {"type": "length", "value": 800}}, "constraints": ["clear_width >= m"], "seed": 1}
    res = OPZ.optimize_family(p, {**prob, "budget": 400})
    alts = OPZ.alternatives_family(p, {**prob, "count": 4, "candidates": 300})
    return {"optimize_budget_400": {**timed(runs, lambda: OPZ.optimize_family(p, {**prob, "budget": 400})), "evaluations": res["evaluations"]},
            "alternatives_300_candidates": {**timed(runs, lambda: OPZ.alternatives_family(p, {**prob, "count": 4, "candidates": 300})), "evaluations": alts["evaluations"]}}


def bench_constraints(runs: int) -> list[dict]:
    out = []
    for k in (10, 40, 100):
        p = Project.create("B")
        view = next(iter(p.drawings))
        ids = [p.execute("draw.line", {"drawing": view, "start": [i * 1000, (i % 3) * 40], "end": [i * 1000 + 1000, ((i + 1) % 3) * 40 + 10]})["result"]["id"] for i in range(k)]
        cons = [{"kind": "horizontal", "entities": [i]} for i in ids]
        p.execute("draw.constraints_set", {"drawing": view, "constraints": cons})
        doc = p.drawing(view)
        out.append({"lines": k, "constraints": len(cons), "analyze": timed(runs, lambda: doc.analyze_constraints())})
    return out


def bench_script(runs: int) -> dict:
    p = Project.create("B")
    src = "def run(model, params):\n    return {'n': len(model.elements())}\n"
    sid = p.execute("script.add", {"name": "s", "source": src, "inputs": {}, "outputs": {"n": "integer"}})["result"]["id"]
    iso = SC.isolation()
    if not iso["untrusted_execution"]:
        p.execute("script.approve", {"id": sid, "version": 1, "sha256": SC.sha256(src)})
    return {"isolation": iso["level"], "empty_model_run": cold_warm(runs, lambda: SC.run(p, {"script": sid}))}


# -------------------------------------------------------------------------------------------------------------------------- geometry
def bench_geometry(runs: int) -> dict:
    out = {}
    out["stair_design_pure_3.2m"] = timed(runs * 4, lambda: SR.design(3.2))
    p = Project.create("B")
    level = p.manifest["levels"][0]["id"]
    wall = p.execute("model.add_wall", {"start": [0, 0], "end": [20000, 0], "level": level, "thickness": 200, "height": 3})["result"]["id"]
    door = p.execute("model.add_door", {"wall": wall, "offset": 5.0, "width": 900})["result"]["id"]
    t = time.perf_counter()
    g = p.execute("procedural.facade", {"wall": wall, "panel_width": 600, "panel_height": 600})["result"]
    out["facade_generate"] = {"panels": len(g["members"]), "ms": round((time.perf_counter() - t) * 1000, 3)}
    flip = [1.0]

    def move_wall():
        flip[0] = -flip[0]
        p.execute("model.move", {"id": wall, "dx": 0.0, "dy": 0.5 * flip[0], "dz": 0.0})
    out["move_host_wall_with_facade_regenerating"] = {**timed(runs, move_wall), "panels": len(g["members"])}
    p2 = Project.create("B2")
    l2 = p2.manifest["levels"][0]["id"]
    w2 = p2.execute("model.add_wall", {"start": [0, 0], "end": [20000, 0], "level": l2, "thickness": 200, "height": 3})["result"]["id"]
    flip2 = [1.0]

    def move_plain():
        flip2[0] = -flip2[0]
        p2.execute("model.move", {"id": w2, "dx": 0.0, "dy": 0.5 * flip2[0], "dz": 0.0})
    out["move_same_wall_without_facade"] = timed(runs, move_plain)
    out["door_swing_plan_with_facade"] = {**timed(runs, lambda: DS.plan(p, door, 90, tolerance_mm=12)), "elements_checked_against": len(g["members"])}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--json", default=None)
    ap.add_argument("--scales", default="1,10,100", help="dependent-instance counts for the 'dependents' section, e.g. 1,10,100,1000")
    ap.add_argument("--budget-s", type=float, default=120.0, help="a scale whose setup is projected to take longer than this is reported as not measured")
    ap.add_argument("--only", default=None, help="comma list of sections to run (for slow machines): parameters,types,dependents,optimization,constraints,script,geometry")
    a = ap.parse_args()
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        commit = ""
    import numpy
    report = {"environment": {"python": sys.version.split()[0], "platform": platform.platform(), "cpu_count": os.cpu_count(), "numpy": numpy.__version__,
                              "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "spadra_commit": commit,
                              "note": "numbers describe this machine only; project commands are called directly, not through HTTP"},
              "runs": a.runs}
    sections = {"parameters": ("parameters_full_vs_incremental", bench_parameters), "types": ("family_type_update_rebuilding_instances", bench_type_update),
                "dependents": ("dependent_scales_vs_full_rebuild", lambda runs: bench_dependents(runs, tuple(int(x) for x in a.scales.split(",")), a.budget_s)),
                "optimization": ("optimization", bench_optimization), "constraints": ("constraint_analysis_2d", bench_constraints),
                "script": ("script", bench_script), "geometry": ("geometry", bench_geometry)}
    report["environment"].update(machine=platform.machine(), os=f"{platform.system()} {platform.release()}", python_implementation=platform.python_implementation())
    wanted = a.only.split(",") if a.only else list(sections)
    for key in wanted:
        name, fn = sections[key]
        report[name] = fn(a.runs)
    text = json.dumps(report, indent=1)
    if a.json:
        Path(a.json).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
