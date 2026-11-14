"""Measured evaluation of the dimension-aware reconstruction on the dimensioned fixtures of ``recon_fixtures`` (known ground truth).

    python -m applications.SPADRA.tests.evaluate_reconstruction [--json out.json] [--no-ocr-skip]

Per fixture and in total it reports: dimension recall / precision and the error of the realised distances against what is printed; wall endpoint and thickness error after the one
free translation (the plan has no absolute origin); opening host, kind, offset and width error; room count and area error; the IFC class of every element built into a project;
and how the confidence scores relate to the measured errors (rank correlation, error per confidence bin). Then the effect of one manual correction. Nothing here is a pass/fail
threshold: the numbers are the result, and the fixtures are synthetic drawings (see the report for what that means for real plans).
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from typing import Any

from applications.SPADRA import reconstruct as R
from applications.SPADRA.tests import recon_fixtures as F


def _wall_rect_corners(w: dict[str, Any]) -> list[tuple[float, float]]:
    (x0, y0), (x1, y1) = w["start"], w["end"]
    return [(x0, y0), (x1, y1)]


def truth_walls(fx: F.Fixture) -> list[dict[str, Any]]:
    return [{"start": list(w.p0), "end": list(w.p1), "t": w.t} for w in fx.walls]


def _bbox_min(walls: list[dict[str, Any]], thick: str) -> tuple[float, float]:
    xs, ys = [], []
    for w in walls:
        t = w[thick] / 2
        for p in (w["start"], w["end"]):
            xs.append(p[0] - t)
            ys.append(p[1] - t)
    return min(xs), min(ys)


def spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3:
        return None

    def ranks(v: list[float]) -> list[float]:
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(v):
            j = i
            while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return r
    ra, rb = ranks(a), ranks(b)
    ma, mb = statistics.mean(ra), statistics.mean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))
    return None if den == 0 else num / den


def evaluate_one(fx: F.Fixture, ifc: bool = True) -> dict[str, Any]:
    data, ext, meta = F.render(fx)
    kind = "pdf" if fx.kind == "pdf" else "raster"
    t0 = time.perf_counter()
    res = R.run({"kind": kind, "data": data})
    elapsed = time.perf_counter() - t0
    out: dict[str, Any] = {"name": fx.name, "kind": fx.kind, "seconds": round(elapsed, 2), "summary": res["summary"], "ok": res["ok"], "scale_mm_per_unit": (res["scale"] or {}).get("mm_per_unit")}
    # ---- dimensions: recall / precision against the drawn ones, error of what is printed vs what the solved geometry realises
    truth_dims = [{"axis": d.axis, "value": abs(d.b - d.a)} for d in fx.dims]
    found = [d for d in res["dimensions"] if d["status"] in ("used", "thickness", "dropped", "outlier", "ignored", "candidate")]
    matched, errs_len = 0, []
    pool = list(found)
    for td in truth_dims:
        best = None
        for d in pool:
            if d["axis"] != td["axis"]:
                continue
            e = abs(d["length_px"] * (res["scale"]["mm_per_unit"] or 0) - td["value"])
            if best is None or e < best[0]:
                best = (e, d)
        if best and best[0] <= 0.03 * td["value"]:
            matched += 1
            errs_len.append(best[0])
            pool.remove(best[1])
    out["dimensions"] = {"truth": len(truth_dims), "found": len(found), "matched": matched, "recall": matched / max(len(truth_dims), 1), "precision": matched / max(len(found), 1),
                         "used": sum(1 for d in found if d["status"] in ("used", "thickness")), "dropped": [d["text"] for d in found if d["status"] == "dropped"],
                         "line_length_error_mm": {"mean": statistics.mean(errs_len) if errs_len else None, "max": max(errs_len) if errs_len else None}}
    # realised distance vs printed value for the used dimensions (the constraint residual after solving): measured on the output walls
    realised = []
    cons = res.get("constraints") or {}
    for ax, info in (cons.get("axes") or {}).items():
        if info.get("max_residual_mm") is not None:
            realised.append(info["max_residual_mm"])
    out["dimensions"]["constraint_residual_mm_max"] = max(realised) if realised else None
    out["constraint_status"] = {ax: info.get("status") for ax, info in (cons.get("axes") or {}).items()}
    # ---- walls: match by orientation and nearest midpoint after aligning the lower-left corners
    tw = truth_walls(fx)
    rw = res["walls"]
    if rw:
        tx, ty = _bbox_min([{**w, "t": w["t"]} for w in tw], "t")
        rx, ry = _bbox_min([{**w, "t": w["thickness_mm"]} for w in rw], "t")
        # truth uses the same y-up frame as the output plan
        shift = (rx - tx, ry - ty)
        pool = list(rw)
        wall_err, thick_err, missed, matches = [], [], 0, []
        for i, w in enumerate(tw):
            mid = ((w["start"][0] + w["end"][0]) / 2 + shift[0], (w["start"][1] + w["end"][1]) / 2 + shift[1])
            best = None
            for r in pool:
                rm = ((r["start"][0] + r["end"][0]) / 2, (r["start"][1] + r["end"][1]) / 2)
                d = math.dist(mid, rm)
                if best is None or d < best[0]:
                    best = (d, r)
            if best is None or best[0] > max(w["t"] * 2.0, 400):
                missed += 1
                continue
            r = best[1]
            pool.remove(r)
            ts, te = (w["start"][0] + shift[0], w["start"][1] + shift[1]), (w["end"][0] + shift[0], w["end"][1] + shift[1])
            e_fwd = max(math.dist(ts, r["start"]), math.dist(te, r["end"]))
            e_rev = max(math.dist(ts, r["end"]), math.dist(te, r["start"]))
            rev = e_rev < e_fwd
            wall_err.append(min(e_fwd, e_rev))
            thick_err.append(abs(r["thickness_mm"] - w["t"]))
            matches.append((i, r, rev, shift))
        out["walls"] = {"truth": len(tw), "found": len(rw), "matched": len(matches), "missed": missed, "extra": len(pool),
                        "endpoint_error_mm": {"mean": statistics.mean(wall_err) if wall_err else None, "max": max(wall_err) if wall_err else None},
                        "thickness_error_mm": {"mean": statistics.mean(thick_err) if thick_err else None, "max": max(thick_err) if thick_err else None}}
        # ---- openings: host wall, kind, offset (from the truth wall's own start) and width
        ops = []
        for i, r, rev, _sh in matches:
            t_op = [o for o in fx.openings if o.wall == i]
            r_ops = list(r["openings"])
            for to in t_op:
                best = None
                for ro in r_ops:
                    off = (r["length_mm"] - ro["offset_mm"]) if rev else ro["offset_mm"]
                    d = abs(off - to.center)
                    if best is None or d < best[0]:
                        best = (d, ro, off)
                if best and best[0] <= 0.5 * to.width + 100:
                    r_ops.remove(best[1])
                    ops.append({"offset_error": best[0], "width_error": abs(best[1]["width_mm"] - to.width), "kind_ok": best[1]["kind"] == to.kind, "confidence": best[1]["confidence"],
                                "assumed_door": "assumed" in best[1]["evidence"], "kind": to.kind})
                else:
                    ops.append(None)
        got = [o for o in ops if o]
        out["openings"] = {"truth": len(fx.openings), "matched": len(got), "found": sum(len(w["openings"]) for w in rw),
                           "host_and_position_recall": len(got) / max(len(fx.openings), 1),
                           "kind_accuracy": (sum(o["kind_ok"] for o in got) / len(got)) if got else None,
                           "door_kind_accuracy": (lambda ds: sum(o["kind_ok"] for o in ds) / len(ds) if ds else None)([o for o in got if o["kind"] == "door"]),
                           "window_kind_accuracy": (lambda ds: sum(o["kind_ok"] for o in ds) / len(ds) if ds else None)([o for o in got if o["kind"] == "window"]),
                           "offset_error_mm": {"mean": statistics.mean(o["offset_error"] for o in got) if got else None, "max": max(o["offset_error"] for o in got) if got else None},
                           "width_error_mm": {"mean": statistics.mean(o["width_error"] for o in got) if got else None, "max": max(o["width_error"] for o in got) if got else None}}
        out["_wall_conf_err"] = [(r["confidence"], e) for (i, r, rev, sh), e in zip(matches, wall_err)]
        out["_open_conf_err"] = [(o["confidence"], o["offset_error"]) for o in got]
        # associations: the host of every matched opening is right by construction of the match; dimension anchors counted here
        anchored = [d for d in res["dimensions"] if d["status"] == "used"]
        out["associations"] = {"dimensions_with_both_ends_on_geometry": sum(1 for d in anchored if len(d["anchors"]) == 2), "dimensions_used": len(anchored)}
    else:
        out["walls"] = {"truth": len(tw), "found": 0, "matched": 0, "missed": len(tw), "extra": 0}
        out["openings"] = {"truth": len(fx.openings), "matched": 0, "found": 0, "host_and_position_recall": 0.0}
        out["_wall_conf_err"], out["_open_conf_err"] = [], []
    # ---- rooms
    truth_areas = sorted(fx.rooms)
    found_areas = sorted(s["area_m2"] for s in res["spaces"])
    pair = []
    pool = list(found_areas)
    for a in truth_areas:
        if not pool:
            break
        b = min(pool, key=lambda v: abs(v - a))
        pool.remove(b)
        pair.append((a, b))
    out["rooms"] = {"truth": truth_areas, "found": found_areas, "area_error_m2": [round(b - a, 4) for a, b in pair], "area_error_pct": [round(100 * (b - a) / a, 3) for a, b in pair]}
    out["_room_conf_err"] = [(s["confidence"], abs(s["area_m2"] - min(truth_areas, key=lambda a: abs(a - s["area_m2"]))) / max(min(truth_areas, key=lambda a: abs(a - s["area_m2"])), 1e-9))
                             for s in res["spaces"]] if truth_areas else []
    out["_dim_conf_err"] = []
    for td in truth_dims:
        pass
    for d in res["dimensions"]:
        if d["status"] in ("used", "thickness", "dropped"):
            err = min((abs(d["length_px"] * res["scale"]["mm_per_unit"] - t["value"]) / t["value"] for t in truth_dims if t["axis"] == d["axis"]), default=None)
            if err is not None:
                out["_dim_conf_err"].append((d["confidence"], err))
    # ---- into a project: every element must map to the right IFC class
    if ifc and rw:
        from applications.SPADRA.project import Project
        p = Project.create("ev", unit="mm")
        built = p.execute("reconstruct.build", {"model": res})["result"]
        want = {"walls": "IfcWall", "openings": None, "spaces": "IfcSpace"}
        classes = sorted(set(built["ifc"].values()))
        wrong = [oid for rid, oid in built["walls"].items() if built["ifc"][oid] != "IfcWall"] + [oid for rid, oid in built["spaces"].items() if built["ifc"][oid] != "IfcSpace"]
        door_ok = all(built["ifc"][oid] == {"door": "IfcDoor", "window": "IfcWindow"}[o["kind"]] for w in rw for o in w["openings"] for oid in [built["openings"].get(o["id"])] if oid)
        out["ifc"] = {"elements": len(built["ifc"]), "classes": classes, "wrong_wall_or_space": len(wrong), "openings_map_to_kind": door_ok, "skipped": len(built["skipped"])}
        del want
    return out


def correction_effect() -> dict[str, Any]:
    """One manual correction each on the fixtures where an automatic step is known to fail or to be assumed: a dimension the OCR missed is added by hand, a wrong door/window kind is set."""
    by = {f.name: f for f in F.fixtures()}
    out: dict[str, Any] = {}
    # missed dimension: l-shape has a 2700 y dimension the OCR does not read; adding it by hand (two image points) should give the extra constraint
    fx = by["l-shape"]
    data, _ext, meta = F.render(fx)
    before = R.run({"kind": "raster", "data": data})
    out["l-shape: dimensions used before/after adding the unread 2700 by hand"] = {"before": before["summary"]["dimensions_used"]}
    # find image points of the missing dimension from the truth: reference line (6100, 0) ... at 7000: ends y=-100 and 2600 at x=7000
    ox, oy = meta["origin_mm"]
    k = meta["px_per_mm"]
    to_px = lambda x, y: [(x - ox) * k, (oy - y) * k]
    after = R.run({"kind": "raster", "data": data}, corrections={"add_dimensions": [{"p_px": to_px(7000, -100), "q_px": to_px(7000, 2600), "value_mm": 2700}]})
    out["l-shape: dimensions used before/after adding the unread 2700 by hand"]["after"] = after["summary"]["dimensions_used"]
    # wrong scale: a user scale replaces the calibrated one
    fx = by["single-room"]
    data, _e, meta = F.render(fx)
    base = R.run({"kind": "raster", "data": data})
    forced = R.run({"kind": "raster", "data": data}, corrections={"scale_mm_per_px": 1.0 / meta["px_per_mm"] * 1.05})
    out["single-room: scale forced 5 % high"] = {"calibrated_mm_per_px": base["scale"]["mm_per_unit"], "forced": forced["scale"]["mm_per_unit"],
                                                   "wall_length_mm_before": base["walls"][0]["length_mm"], "wall_length_mm_after": forced["walls"][0]["length_mm"],
                                                   "note": "the dimensions are still imposed: with a forced scale they must be consistent, and the deviation of each shows in dimensions[].scale_deviation",
                                                   "deviations": [d["scale_deviation"] for d in forced["dimensions"]]}
    # opening kind
    door = next((o for w in base["walls"] for o in w["openings"] if o["kind"] == "door"), None)
    if door:
        fixed = R.run({"kind": "raster", "data": data}, corrections={"openings": {door["id"]: {"kind": "window"}}})
        changed = next(o for w in fixed["walls"] for o in w["openings"] if o["id"] == door["id"])
        out["single-room: opening kind set by hand"] = {"before": {"kind": door["kind"], "confidence": door["confidence"]}, "after": {"kind": changed["kind"], "confidence": changed["confidence"]}}
    return out


def stress() -> list[dict[str, Any]]:
    """The two-room plan rendered at other resolutions and noise levels, to show where reading degrades (it is a sweep, not a pass/fail gate)."""
    import dataclasses
    base = {f.name: f for f in F.fixtures()}["two-rooms"]
    out = []
    for ppm, blur, noise, jpeg in ((0.05, 0.0, 0.0, None), (0.08, 0.0, 0.0, None), (0.125, 0.0, 0.0, None), (0.25, 0.0, 0.0, None), (0.125, 1.2, 25.0, None), (0.125, 0.8, 14.0, 25), (0.1, 1.5, 30.0, 20)):
        fx = dataclasses.replace(base, px_per_mm=ppm, blur=blur, noise=noise, jpeg=jpeg)
        row = {"px_per_mm": ppm, "blur": blur, "noise": noise, "jpeg_quality": jpeg}
        try:
            r = evaluate_one(fx, ifc=False)
            row.update({"dimensions_matched": f"{r['dimensions']['matched']}/{r['dimensions']['truth']}", "walls_matched": f"{r['walls']['matched']}/{r['walls']['truth']}", "extra_walls": r["walls"]["extra"],
                        "openings_matched": f"{r['openings']['matched']}/{r['openings']['truth']}", "rooms_found": len(r["rooms"]["found"]), "rooms_truth": len(r["rooms"]["truth"]),
                        "endpoint_error_mm_max": (r["walls"].get("endpoint_error_mm") or {}).get("max"), "max_abs_area_error_pct": max((abs(v) for v in r["rooms"]["area_error_pct"]), default=None)})
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        out.append(row)
    return out


def calibration(rows: list[tuple[float, float]]) -> dict[str, Any]:
    if len(rows) < 3:
        return {"n": len(rows), "spearman_confidence_vs_error": None}
    conf, err = [c for c, _ in rows], [e for _, e in rows]
    bins = {}
    for lo, hi in ((0.0, 0.7), (0.7, 0.85), (0.85, 1.01)):
        sel = [e for c, e in rows if lo <= c < hi]
        bins[f"{lo:.2f}-{min(hi, 1.0):.2f}"] = {"n": len(sel), "mean_error": statistics.mean(sel) if sel else None, "max_error": max(sel) if sel else None}
    return {"n": len(rows), "spearman_confidence_vs_error": spearman(conf, err), "error_by_confidence_bin": bins,
            "note": "a useful score has a negative correlation with the error; fixtures with identical confidence have tied ranks"}


def evaluate(names: list[str] | None = None) -> dict[str, Any]:
    caps = R.capabilities()
    per = []
    for fx in F.fixtures():
        if names and fx.name not in names:
            continue
        try:
            per.append(evaluate_one(fx))
        except Exception as exc:                                                    # a failing fixture is a result, not a reason to stop the run
            per.append({"name": fx.name, "kind": fx.kind, "error": f"{type(exc).__name__}: {exc}"})
    good = [p for p in per if "error" not in p]
    tot: dict[str, Any] = {"fixtures": len(per), "failed": [p["name"] for p in per if "error" in p]}
    tot["dimensions"] = {"truth": sum(p["dimensions"]["truth"] for p in good), "matched": sum(p["dimensions"]["matched"] for p in good), "found": sum(p["dimensions"]["found"] for p in good)}
    tot["dimensions"]["recall"] = tot["dimensions"]["matched"] / max(tot["dimensions"]["truth"], 1)
    tot["dimensions"]["precision"] = tot["dimensions"]["matched"] / max(tot["dimensions"]["found"], 1)
    tot["walls"] = {"truth": sum(p["walls"]["truth"] for p in good), "matched": sum(p["walls"]["matched"] for p in good), "extra": sum(p["walls"]["extra"] for p in good)}
    allw = [e for p in good for _c, e in p["_wall_conf_err"]]
    tot["walls"]["endpoint_error_mm"] = {"mean": statistics.mean(allw) if allw else None, "max": max(allw) if allw else None}
    tot["openings"] = {"truth": sum(p["openings"]["truth"] for p in good), "matched": sum(p["openings"]["matched"] for p in good), "found": sum(p["openings"]["found"] for p in good)}
    ow = [e for p in good for _c, e in p["_open_conf_err"]]
    tot["openings"]["offset_error_mm"] = {"mean": statistics.mean(ow) if ow else None, "max": max(ow) if ow else None}
    tot["rooms"] = {"truth": sum(len(p["rooms"]["truth"]) for p in good), "found": sum(len(p["rooms"]["found"]) for p in good),
                    "max_abs_area_error_pct": max((abs(v) for p in good for v in p["rooms"]["area_error_pct"]), default=None)}
    tot["calibration"] = {"walls (error = endpoint mm)": calibration([r for p in good for r in p["_wall_conf_err"]]),
                          "openings (error = offset mm)": calibration([r for p in good for r in p["_open_conf_err"]]),
                          "dimensions (error = relative length)": calibration([r for p in good for r in p["_dim_conf_err"]]),
                          "rooms (error = relative area)": calibration([r for p in good for r in p["_room_conf_err"]])}
    for p in per:
        for k in [k for k in p if k.startswith("_")]:
            del p[k]
    return {"capabilities": caps, "fixtures": per, "total": tot, "corrections": correction_effect() if caps["ocr"]["available"] else "skipped: OCR unavailable",
            "stress": stress() if caps["ocr"]["available"] else "skipped: OCR unavailable"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="write the full result here")
    ap.add_argument("--only", nargs="*", help="fixture names")
    args = ap.parse_args(argv)
    if not R.capabilities()["sources"]["raster"]["needs_ocr"]["available"]:
        print("Tesseract OCR is not available: raster and image-PDF fixtures cannot be read here (this is reported, not skipped silently).", file=sys.stderr)
    result = evaluate(args.only)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2, default=str)
    t = result["total"]
    print(f"fixtures {t['fixtures']} (failed: {t['failed'] or 'none'})")
    d, w, o, r = t["dimensions"], t["walls"], t["openings"], t["rooms"]
    print(f"dimensions  recall {d['matched']}/{d['truth']}  precision {d['matched']}/{d['found']}")
    print(f"walls       matched {w['matched']}/{w['truth']}  extra {w['extra']}  endpoint error mean {w['endpoint_error_mm']['mean']} max {w['endpoint_error_mm']['max']} mm")
    print(f"openings    matched {o['matched']}/{o['truth']}  found {o['found']}  offset error mean {o['offset_error_mm']['mean']} max {o['offset_error_mm']['max']} mm")
    print(f"rooms       truth {r['truth']}  found {r['found']}  max |area error| {r['max_abs_area_error_pct']} %")
    for k, v in t["calibration"].items():
        print(f"calibration {k}: n={v['n']} spearman={v.get('spearman_confidence_vs_error')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
