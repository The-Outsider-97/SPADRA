"""Measure (not claim) how long the main operations take on a real project through the real HTTP server (master prompt 22.4).

    python -m applications.SPADRA.tests.measure_performance [--walls 60] [--runs 5] [--json out.json]

Each operation is timed ``runs`` times and the minimum, median and maximum are reported in milliseconds, with the project size and the machine
(Python version, platform). The numbers describe the machine they were taken on and nothing else. Nothing here is a pass/fail threshold: it reports.
"""
from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

from applications.SPADRA.app import Settings, build_server

from .harness import TOKEN, Api


def timed(runs: int, fn) -> dict[str, float]:
    samples = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    return {"min_ms": round(min(samples), 1), "median_ms": round(statistics.median(samples), 1), "max_ms": round(max(samples), 1)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--walls", type=int, default=60)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--json")
    args = ap.parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        client = root / "dist"
        client.mkdir()
        (client / "index.html").write_text("<!doctype html><title>SPADRA</title>")
        settings = Settings(port=0, max_request_bytes=1 << 20, undo_depth=50, max_scene_vertices=2_000_000, recent_projects=5,
                            data_dir=root / "data", config_path=root / "none.yaml")
        server, _ = build_server(settings, TOKEN, root=None, client_dir=client)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        try:
            a = Api(server.port)
            a.post("/api/project/new", {"name": "Perf", "unit": "mm"})
            tree = a.get("/api/project/tree")
            level = tree["levels"][0]["id"]
            plan = tree["tree"]["sections"][0]["children"][1]["children"][0]["id"]
            walls = []
            side = max(2, int(args.walls ** 0.5))
            for i in range(args.walls):                                     # a grid of 4 m walls, every third with a door
                x, y = (i % side) * 5000, (i // side) * 5000
                wid = a.cmd("model.add_wall", {"start": [x, y], "end": [x + 4000, y], "level": level, "thickness": 200, "height": 3})["result"]["id"]
                walls.append(wid)
                if i % 3 == 0:
                    a.cmd("model.add_door", {"wall": wid, "offset": 1500, "width": 900, "height": 2100})
                a.cmd("draw.line", {"drawing": plan, "start": [x, y], "end": [x + 4000, y]})
            section = a.cmd("view.add_section", {"a": [0, 500], "b": [side * 5000, 500]})["result"]["id"]
            animated = False
            try:
                a.cmd("anim.key", {"id": walls[0], "property": "translation", "time": 5, "value": [0, 0, 2]})
                animated = True
            except AssertionError:
                pass
            path = a.post("/api/project/save", {"name": "Perf"})["path"]
            results: dict[str, dict[str, float]] = {}
            results["open project (read files, verify hashes)"] = timed(args.runs, lambda: a.post("/api/project/open", {"path": path, "discard": True}))
            results["open a drawing (floor plan: one line and one wall per step)"] = timed(args.runs, lambda: a.get(f"/api/drawing/{plan}"))
            results["load the 3D scene"] = timed(args.runs, lambda: a.get("/api/scene"))
            results["select an element (read its properties)"] = timed(args.runs, lambda: a.post("/api/properties", {"model": walls[0]}))
            n = [0]

            def update() -> None:
                n[0] += 1
                a.cmd("model.set_property", {"id": walls[0], "key": "thickness", "value": 200 + n[0] % 2 * 50})
            results["update a parameter (regenerates the wall, one undo step)"] = timed(args.runs, update)
            results["generate a section drawing (cut through the model)"] = timed(args.runs, lambda: a.get(f"/api/drawing/{section}"))
            results["quantities of every element (volume, area, centre)"] = timed(args.runs, lambda: a.post("/api/quantities", {}))
            results["save (second save: writes files and a backup)"] = timed(args.runs, lambda: a.post("/api/project/save", {}))
            if animated:
                results["animation playback: one frame (scene at a time)"] = timed(args.runs, lambda: a.get("/api/scene?time=2.5"))
            report = {"python": sys.version.split()[0], "platform": platform.platform(), "walls": args.walls, "runs": args.runs,
                      "model_elements": sum(a.get("/api/project/properties")["counts"]["elements"].values()), "animation_measured": animated, "results": results}
        finally:
            server.shutdown(); server.server_close()
    for name, r in report["results"].items():
        print(f"{name:<66} min {r['min_ms']:>8} ms   median {r['median_ms']:>8} ms   max {r['max_ms']:>8} ms")
    print(f"\n{report['model_elements']} model elements; {args.runs} runs each; Python {report['python']} on {report['platform']}")
    if not animated:
        print("The animation frame was not measured: the SLAI Animation Agent is not importable here.")
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
