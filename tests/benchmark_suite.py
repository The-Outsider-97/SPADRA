"""SPADRA Parametric Benchmark Suite: 60 reproducible tasks that score what SPADRA builds against numbers it did not compute.

    python -m applications.SPADRA.tests.benchmark_suite [--json docs/parametric_benchmark_suite.json] [--only A01,B07] [--list]

Three categories, adapted from the task families of Parametric CAD Bench v3 (arXiv 2611.02659) to what SPADRA really offers (no CAD-kernel scripting interface is simulated):

  A  create    22 tasks  an element or a family is made from an explicit specification
  B  edit      26 tasks  an existing model is edited (repeatedly) and must keep its design intent; invalid edits must change nothing
  C  rebuild   12 tasks  editable BIM elements are reconstructed from a dimensioned 2D drawing (needs scipy, Pillow, pypdfium2; reported ``blocked`` where they are missing)

**The oracle is independent of the evaluator it scores.** Expected volumes and sizes are closed-form (length x thickness x height minus opening volumes, shoelace area x thickness, a family formula
evaluated by hand); the measured ones come from the triangles of the scene (numpy, summed relative to a vertex), not from the SGEO B-rep evaluator. The kernel's own volume is compared as a *second*
opinion, never as the reference. Every created or edited model is also saved, reopened and re-measured (native round trip), and every task is run twice and must give identical measurements.

A task is ``pass`` (every check held), ``fail`` (a check did not hold: the failing check and its numbers are in the result), or ``blocked`` (a dependency of the task itself is missing; nothing is claimed).
Nothing is a threshold except the stated tolerances (``VOLUME_TOL``, ``SIZE_TOL``). Results are written with the environment they were measured in; they describe this build, on this machine, and the reconstruction
tasks use synthetic drawings (see docs/RECONSTRUCTION.md), so nothing here is a claim about real drawings.
"""
from __future__ import annotations

import argparse
import json
import math
import platform
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

from applications.SPADRA import elements as E
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest

VOLUME_TOL = 1e-6                       # relative: the figures are closed-form, so this is arithmetic noise, not a modelling tolerance
SIZE_TOL = 1e-6                         # metres


# ---------------------------------------------------------------------------------------------------------------------------------- oracle
def measure(project: Project, oid: str) -> dict[str, Any]:
    """Volume and world bounding-box size of one element from its scene triangles (not from the B-rep evaluator), plus the kernel's own volume as a second opinion."""
    obj = next(o for o in project.scene_data()["objects"] if o["id"] == oid)
    v = np.asarray(obj["vertices"], dtype=float)
    f = np.asarray(obj["faces"], dtype=int)
    r = v - v[0]
    volume = float(np.einsum("ij,ij->i", r[f[:, 0]], np.cross(r[f[:, 1]], r[f[:, 2]])).sum() / 6.0)
    return {"volume": volume, "size": [float(x) for x in (v.max(axis=0) - v.min(axis=0))], "kernel_volume": float(project.model.volume(oid, unit="m")), "triangles": int(len(f))}


def shoelace(points: list[list[float]]) -> float:
    n = len(points)
    return abs(sum(points[i][0] * points[(i + 1) % n][1] - points[(i + 1) % n][0] * points[i][1] for i in range(n))) / 2.0


@dataclass
class Check:
    name: str
    ok: bool
    detail: dict[str, Any] = field(default_factory=dict)


class Ctx:
    """What one task is allowed to do: build a project, record checks and read measurements. Every check is kept, so a result says *what* held."""

    def __init__(self) -> None:
        self.checks: list[Check] = []
        self.p = Project.create("Bench")
        self.l1, self.l2, self.roof = (l["id"] for l in self.p.manifest["levels"])
        self.measures: dict[str, Any] = {}
        self._lane = 0

    def lane(self) -> int:
        self._lane += 1
        return self._lane * 20_000                                    # mm: elements never touch, so no join or clash changes what is measured

    def run(self, op: str, **params: Any) -> dict[str, Any]:
        return self.p.execute(op, params)["result"]

    def wall(self, length: float, thickness: float, height: float) -> tuple[str, int]:
        y = self.lane()
        return self.run("model.add_wall", start=[0, y], end=[length, y], level=self.l1, thickness=thickness, height=height)["id"], y

    def expect(self, name: str, got: float, want: float, *, tol: float = VOLUME_TOL, relative: bool = True) -> None:
        err = abs(got - want) / (abs(want) if relative and want else 1.0)
        self.checks.append(Check(name, err <= tol, {"got": got, "want": want, "error": err, "tolerance": tol}))

    def volume(self, name: str, oid: str, want: float) -> dict[str, Any]:
        m = measure(self.p, oid)
        self.expect(f"{name}: volume from the scene triangles", m["volume"], want)
        self.expect(f"{name}: kernel volume agrees with the independent one", m["kernel_volume"], m["volume"])
        self.measures[name] = m
        return m

    def size(self, name: str, oid: str, want: list[float]) -> None:
        got = measure(self.p, oid)["size"]
        ok = all(abs(a - b) <= SIZE_TOL for a, b in zip(sorted(got), sorted(want)))
        self.checks.append(Check(f"{name}: bounding-box size", ok, {"got": got, "want": want, "tolerance": SIZE_TOL}))

    def holds(self, name: str, ok: bool, **detail: Any) -> None:
        self.checks.append(Check(name, bool(ok), detail))

    def refused(self, name: str, op: str, **params: Any) -> None:
        """An invalid edit must be refused with a message and leave the model exactly as it was (revision and every element's measures)."""
        before = (self.p.revision, {o["id"]: round(float(measure(self.p, o["id"])["volume"]), 12) for o in self.p.scene_data()["objects"]})
        try:
            self.p.execute(op, params)
            self.checks.append(Check(name, False, {"error": "accepted"}))
            return
        except BadRequest as exc:
            message = str(exc)
        after = (self.p.revision, {o["id"]: round(float(measure(self.p, o["id"])["volume"]), 12) for o in self.p.scene_data()["objects"]})
        self.checks.append(Check(name, before == after and bool(message), {"message": message, "revision_unchanged": before[0] == after[0], "model_unchanged": before[1] == after[1]}))

    def roundtrip(self, oids: list[str]) -> None:
        """Save, reopen, and compare every listed element's volume and size with the live ones."""
        live = {oid: measure(self.p, oid) for oid in oids}
        with tempfile.TemporaryDirectory() as tmp:
            self.p.save(Path(tmp) / "bench.spadra")
            reopened = Project.open(Path(tmp) / "bench.spadra")
            for oid in oids:
                back = measure(reopened, oid)
                same = abs(back["volume"] - live[oid]["volume"]) <= 1e-9 * max(1.0, abs(live[oid]["volume"])) and all(abs(a - b) <= 1e-9 for a, b in zip(back["size"], live[oid]["size"]))
                self.checks.append(Check(f"save/reopen keeps {oid[:8]}", same, {"live": live[oid]["volume"], "reopened": back["volume"]}))


@dataclass
class Task:
    id: str
    category: str
    title: str
    spec: dict[str, Any]
    run: Callable[[Ctx, dict[str, Any]], None]
    needs: tuple[str, ...] = ()


TASKS: list[Task] = []


def task(id: str, category: str, title: str, spec: dict[str, Any], needs: tuple[str, ...] = ()):
    def register(fn):
        TASKS.append(Task(id, category, title, spec, fn, needs))
        return fn
    return register


# ------------------------------------------------------------------------------------------------------------------------------ A: create
def _wall_task(i: int, length: float, thickness: float, height: float) -> None:
    spec = {"length_mm": length, "thickness_mm": thickness, "height_m": height}

    @task(f"A{i:02d}", "create", f"Wall {length:g} x {thickness:g} mm, {height:g} m high", spec)
    def run(c: Ctx, s: dict[str, Any]) -> None:
        w, _ = c.wall(s["length_mm"], s["thickness_mm"], s["height_m"])
        L, t = s["length_mm"] / 1000, s["thickness_mm"] / 1000
        c.volume("wall", w, L * t * s["height_m"])
        c.size("wall", w, [L, t, s["height_m"]])
        c.holds("wall carries its BIM parameters", abs(E.m(c.p.model.get(w)["metadata"], "thickness") - t) < 1e-12 and abs(E.m(c.p.model.get(w)["metadata"], "height") - s["height_m"]) < 1e-12)
        c.roundtrip([w])


for n, (L, t, h) in enumerate([(6000, 250, 3.2), (3000, 100, 2.4), (12000, 300, 4.0), (900, 50, 2.1), (25000, 200, 3.0), (4500, 450, 6.0), (7300, 140, 2.75), (1500, 75, 1.2)], 1):
    _wall_task(n, L, t, h)


@task("A09", "create", "Wall at 36.87 degrees (3-4-5 triangle), 200 mm, 3 m", {"start": [0, 0], "end": [3000, 4000], "thickness_mm": 200, "height_m": 3.0})
def a09(c: Ctx, s: dict[str, Any]) -> None:
    w = c.run("model.add_wall", start=s["start"], end=s["end"], level=c.l1, thickness=s["thickness_mm"], height=s["height_m"])["id"]
    c.volume("wall", w, 5.0 * 0.2 * 3.0)
    c.size("wall", w, [3.0 + 0.2 * 0.8, 4.0 + 0.2 * 0.6, 3.0])           # a rotated rectangle's extent: length component + thickness component on each axis
    c.roundtrip([w])


def _floor_task(i: int, name: str, pts: list[list[float]], thickness: float) -> None:
    @task(f"A{i:02d}", "create", f"Floor: {name}, {thickness:g} mm", {"points_mm": pts, "thickness_mm": thickness})
    def run(c: Ctx, s: dict[str, Any]) -> None:
        f = c.run("model.add_floor", level=c.l1, points=s["points_mm"], thickness=s["thickness_mm"])["id"]
        area = shoelace([[x / 1000, y / 1000] for x, y in s["points_mm"]])
        c.volume("floor", f, area * s["thickness_mm"] / 1000)
        xs, ys = [p[0] / 1000 for p in s["points_mm"]], [p[1] / 1000 for p in s["points_mm"]]
        c.size("floor", f, [max(xs) - min(xs), max(ys) - min(ys), s["thickness_mm"] / 1000])
        c.roundtrip([f])


_floor_task(10, "rectangle 6 x 4 m", [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], 200)
_floor_task(11, "L shape", [[0, 0], [6000, 0], [6000, 2000], [3000, 2000], [3000, 4000], [0, 4000]], 200)
_floor_task(12, "triangle", [[0, 0], [5000, 0], [1500, 3500]], 150)
_floor_task(13, "irregular pentagon", [[0, 0], [5200, 300], [6100, 3300], [2900, 5000], [-700, 2800]], 250)


def _roof_task(i: int, name: str, pts: list[list[float]], thickness: float) -> None:
    @task(f"A{i:02d}", "create", f"Flat roof: {name}, {thickness:g} mm", {"points_mm": pts, "thickness_mm": thickness})
    def run(c: Ctx, s: dict[str, Any]) -> None:
        r = c.run("model.add_roof", level=c.roof, roof="flat", points=s["points_mm"], thickness=s["thickness_mm"])["id"]
        c.volume("roof", r, shoelace([[x / 1000, y / 1000] for x, y in s["points_mm"]]) * s["thickness_mm"] / 1000)
        c.roundtrip([r])


_roof_task(14, "rectangle 6 x 4 m", [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], 100)
_roof_task(15, "L shape", [[0, 0], [6000, 0], [6000, 2000], [3000, 2000], [3000, 4000], [0, 4000]], 120)
_roof_task(16, "trapezoid", [[0, 0], [8000, 0], [6000, 3000], [2000, 3000]], 90)


def _opening_task(i: int, title: str, openings: list[dict[str, Any]]) -> None:
    @task(f"A{i:02d}", "create", title, {"wall": {"length_mm": 8000, "thickness_mm": 250, "height_m": 3.2}, "openings": openings})
    def run(c: Ctx, s: dict[str, Any]) -> None:
        w, _ = c.wall(s["wall"]["length_mm"], s["wall"]["thickness_mm"], s["wall"]["height_m"])
        t = s["wall"]["thickness_mm"] / 1000
        cut, ids = 0.0, []
        for o in s["openings"]:
            op = "model.add_door" if o["kind"] == "door" else "model.add_window"
            extra = {"sill": o["sill_mm"]} if o["kind"] == "window" else {}
            ids.append(c.run(op, wall=w, offset=o["offset_m"], width=o["width_mm"], height=o["height_mm"], **extra)["id"])
            cut += o["width_mm"] / 1000 * o["height_mm"] / 1000 * t
        c.volume("wall with openings", w, 8.0 * t * 3.2 - cut)
        c.holds("every opening is hosted by the wall", all(E.m(c.p.model.get(i)["metadata"], "host") == w for i in ids))
        c.roundtrip([w, *ids])


_opening_task(17, "Wall with one door", [{"kind": "door", "offset_m": 4.0, "width_mm": 900, "height_mm": 2100}])
_opening_task(18, "Wall with one window", [{"kind": "window", "offset_m": 4.0, "width_mm": 1200, "height_mm": 1200, "sill_mm": 900}])
_opening_task(19, "Wall with two doors", [{"kind": "door", "offset_m": 2.0, "width_mm": 900, "height_mm": 2100}, {"kind": "door", "offset_m": 6.0, "width_mm": 1000, "height_mm": 2200}])
_opening_task(20, "Wall with a door and two windows", [{"kind": "door", "offset_m": 1.5, "width_mm": 900, "height_mm": 2100}, {"kind": "window", "offset_m": 4.0, "width_mm": 1500, "height_mm": 1200, "sill_mm": 900},
                                                       {"kind": "window", "offset_m": 6.5, "width_mm": 800, "height_mm": 1000, "sill_mm": 1000}])


FRAME = {"opening_width": {"default": 900, "min": 300, "max": 3000}, "clearance": {"default": 20, "min": 0, "max": 100},
         "width": {"expression": "opening_width - 2 * clearance"}, "depth": {"default": 100}, "height": {"default": 2100}}


@task("A21", "create", "Family: FrameWidth = OpeningWidth - 2 x Clearance", {"parameters": FRAME})
def a21(c: Ctx, s: dict[str, Any]) -> None:
    fid = c.run("family.define", name="Frame", shape="box", parameters=s["parameters"])["id"]
    i = c.run("family.place", family=fid, level=c.l1, point=[0, 0])["id"]
    c.volume("frame", i, (0.9 - 2 * 0.02) * 0.1 * 2.1)
    c.size("frame", i, [0.86, 0.1, 2.1])
    c.roundtrip([i])


@task("A22", "create", "Family: chained formulas (area = width x depth, height = area / 2)", {"parameters": {"width": {"default": 2000, "min": 500, "max": 6000}, "depth": {"expression": "width / 2"},
      "height": {"expression": "depth / 4"}}})
def a22(c: Ctx, s: dict[str, Any]) -> None:
    fid = c.run("family.define", name="Chain", shape="box", parameters=s["parameters"])["id"]
    i = c.run("family.place", family=fid, level=c.l1, point=[0, 0])["id"]
    c.volume("chain", i, 2.0 * 1.0 * 0.25)
    c.size("chain", i, [2.0, 1.0, 0.25])
    c.refused("a value above the family's maximum is refused", "model.set_property", id=i, key="param:width", value=9000)
    c.roundtrip([i])


# -------------------------------------------------------------------------------------------------------------------------------- B: edit
def _edit_wall_task(i: int, prop: str, values: list[float]) -> None:
    @task(f"B{i:02d}", "edit", f"Edit a wall with a door and a window: {prop} {' -> '.join(f'{v:g}' for v in values)}", {"property": prop, "values": values})
    def run(c: Ctx, s: dict[str, Any]) -> None:
        w, _ = c.wall(6000, 250, 3.2)
        d = c.run("model.add_door", wall=w, offset=2.0, width=900, height=2100)["id"]
        win = c.run("model.add_window", wall=w, offset=4.0, width=1200, height=1200, sill=900)["id"]
        t, h = 0.25, 3.2
        for v in s["values"]:
            c.run("model.set_property", id=w, key=s["property"], value=v)
            if s["property"] == "thickness":
                t = v / 1000
            else:
                h = v
            c.volume(f"after {s['property']} = {v:g}", w, 6.0 * t * h - 0.9 * 2.1 * t - 1.2 * 1.2 * t)      # the openings keep their size and follow the host's thickness
            c.holds("openings are still hosted", all(E.m(c.p.model.get(i)["metadata"], "host") == w for i in (d, win)))
        for _ in s["values"]:
            c.p.undo()
        c.volume("full undo returns the original", w, 6.0 * 0.25 * 3.2 - 0.9 * 2.1 * 0.25 - 1.2 * 1.2 * 0.25)
        for v in s["values"]:
            c.run("model.set_property", id=w, key=s["property"], value=v)
        c.roundtrip([w, d, win])


for n, (prop, vals) in enumerate([("thickness", [300]), ("thickness", [150, 400]), ("thickness", [100, 200, 350, 500]), ("height", [3.6]), ("height", [2.8, 4.5]), ("height", [3.0, 3.4, 5.0, 2.6])], 1):
    _edit_wall_task(n, prop, vals)


def _edit_door_task(i: int, widths: list[float]) -> None:
    @task(f"B{i:02d}", "edit", f"Edit a door's width {' -> '.join(f'{w:g}' for w in widths)} mm: the host's opening follows", {"widths_mm": widths})
    def run(c: Ctx, s: dict[str, Any]) -> None:
        w, _ = c.wall(6000, 250, 3.2)
        d = c.run("model.add_door", wall=w, offset=3.0, width=900, height=2100)["id"]
        for wd in s["widths_mm"]:
            c.run("model.set_property", id=d, key="width", value=wd)
            c.volume(f"door {wd:g} mm", w, 6.0 * 0.25 * 3.2 - wd / 1000 * 2.1 * 0.25)
            c.expect("door's own size follows", E.m(c.p.model.get(d)["metadata"], "width"), wd / 1000)
        c.roundtrip([w, d])


_edit_door_task(7, [1000])
_edit_door_task(8, [700, 1200])
_edit_door_task(9, [800, 1500, 600, 1100, 900])


def _edit_frame_task(i: int, edits: list[tuple[str, float]]) -> None:
    @task(f"B{i:02d}", "edit", f"Design intent: FrameWidth = OpeningWidth - 2 x Clearance, edits {edits}", {"edits": edits, "parameters": FRAME})
    def run(c: Ctx, s: dict[str, Any]) -> None:
        fid = c.run("family.define", name="Frame", shape="box", parameters=s["parameters"])["id"]
        i = c.run("family.place", family=fid, level=c.l1, point=[0, 0])["id"]
        other = c.run("family.place", family=fid, level=c.l1, point=[3000, 0])["id"]
        ow, cl = 900.0, 20.0
        baseline = measure(c.p, other)
        revisions = [c.p.revision]
        for key, value in s["edits"]:
            c.run("model.set_property", id=i, key=f"param:{key}", value=value)
            revisions.append(c.p.revision)
            if key == "opening_width":
                ow = value
            else:
                cl = value
            c.volume(f"after {key} = {value:g}", i, (ow - 2 * cl) / 1000 * 0.1 * 2.1)
            c.size(f"after {key} = {value:g}", i, [(ow - 2 * cl) / 1000, 0.1, 2.1])
        c.holds("an unrelated instance of the family is untouched", abs(measure(c.p, other)["volume"] - baseline["volume"]) < 1e-12)
        c.holds("depth and height (not formula-driven) kept their values", abs(c.p.model.get(i)["geometry"]["size"][1] - 0.1) < 1e-12 and abs(c.p.model.get(i)["geometry"]["size"][2] - 2.1) < 1e-12)
        for _ in s["edits"]:
            c.p.undo()
        c.volume("full undo returns the original frame", i, 0.86 * 0.1 * 2.1)
        for key, value in s["edits"]:
            c.run("model.set_property", id=i, key=f"param:{key}", value=value)
        c.roundtrip([i, other])


_edit_frame_task(10, [("opening_width", 1200.0)])
_edit_frame_task(11, [("opening_width", 700.0), ("clearance", 35.0)])
_edit_frame_task(12, [("clearance", 0.0), ("opening_width", 3000.0), ("clearance", 100.0)])
_edit_frame_task(13, [("opening_width", 800.0), ("opening_width", 1500.0), ("clearance", 50.0), ("opening_width", 400.0), ("clearance", 10.0)])
_edit_frame_task(14, [("opening_width", 1000.0), ("clearance", 25.0), ("opening_width", 1000.0)])
_edit_frame_task(15, [("clearance", 40.0), ("opening_width", 2500.0), ("clearance", 5.0), ("opening_width", 900.0), ("clearance", 20.0), ("opening_width", 1800.0)])


@task("B16", "edit", "Family type edit moves every instance of the type", {"widths_mm": [800, 1000]})
def b16(c: Ctx, s: dict[str, Any]) -> None:
    fid = c.run("family.define", name="Slab", shape="box", parameters={"width": {"default": 900, "min": 300, "max": 3000, "scope": "type"}, "depth": {"default": 200}, "height": {"default": 100}})["id"]
    c.run("family.type_define", id=fid, name="T", values={"width": 900})
    ids = [c.run("family.place", family=fid, level=c.l1, point=[i * 4000, 0], type="T")["id"] for i in range(3)]
    for w in s["widths_mm"]:
        c.run("family.type_update", id=fid, name="T", values={"width": w})
        for i in ids:
            c.volume(f"instance after type width {w}", i, w / 1000 * 0.2 * 0.1)
    c.roundtrip(ids)


@task("B17", "edit", "Moving a wall keeps its hosted door and window attached", {"dx_mm": 2500, "dy_mm": 1000})
def b17(c: Ctx, s: dict[str, Any]) -> None:
    w, _ = c.wall(6000, 250, 3.2)
    d = c.run("model.add_door", wall=w, offset=2.0, width=900, height=2100)["id"]
    before = measure(c.p, w)["volume"]
    c.run("model.move", id=w, dx=s["dx_mm"] / 1000, dy=s["dy_mm"] / 1000, dz=0.0)
    c.expect("moving does not change the wall's volume", measure(c.p, w)["volume"], before)
    c.holds("the door is still hosted by the moved wall", E.m(c.p.model.get(d)["metadata"], "host") == w)
    c.roundtrip([w, d])


def _refusal_task(i: int, title: str, build: Callable[[Ctx], tuple[str, dict[str, Any]]]) -> None:
    @task(f"B{i:02d}", "edit", title, {"kind": "invalid edit"})
    def run(c: Ctx, s: dict[str, Any]) -> None:
        op, params = build(c)
        c.refused("the invalid edit is refused with a message and changes nothing", op, **params)


def _door_in_wall(c: Ctx) -> tuple[str, Any]:
    w, _ = c.wall(6000, 250, 3.2)
    return w, c.run("model.add_door", wall=w, offset=3.0, width=900, height=2100)["id"]


def _b18(c: Ctx):
    w, d = _door_in_wall(c)
    return "model.set_property", {"id": d, "key": "width", "value": 9000}


def _b19(c: Ctx):
    w, _ = c.wall(6000, 250, 3.2)
    return "model.set_property", {"id": w, "key": "thickness", "value": -5}


def _b20(c: Ctx):
    w, _ = c.wall(6000, 250, 3.2)
    return "model.set_property", {"id": w, "key": "height", "value": 0}


def _b21(c: Ctx):
    w, d = _door_in_wall(c)
    return "model.set_property", {"id": d, "key": "height", "value": 9.0}


def _b22(c: Ctx):
    fid = c.run("family.define", name="Frame", shape="box", parameters=FRAME)["id"]
    i = c.run("family.place", family=fid, level=c.l1, point=[0, 0])["id"]
    return "model.set_property", {"id": i, "key": "param:clearance", "value": 500}


def _b23(c: Ctx):
    fid = c.run("family.define", name="Frame", shape="box", parameters=FRAME)["id"]
    i = c.run("family.place", family=fid, level=c.l1, point=[0, 0])["id"]
    return "model.set_property", {"id": i, "key": "param:no_such_parameter", "value": 5}


def _b24(c: Ctx):
    w, d = _door_in_wall(c)
    return "model.set_property", {"id": d, "key": "offset", "value": 50.0}


_refusal_task(18, "Refused: a door wider than its wall", _b18)
_refusal_task(19, "Refused: a negative wall thickness", _b19)
_refusal_task(20, "Refused: a zero wall height", _b20)
_refusal_task(21, "Refused: a door taller than any storey", _b21)
_refusal_task(22, "Refused: a family parameter beyond its bounds", _b22)
_refusal_task(23, "Refused: a parameter the family does not have", _b23)
_refusal_task(24, "Refused: a door moved off the end of its wall", _b24)


@task("B25", "edit", "Repeated edits are deterministic: the same edit script on two projects gives identical geometry", {"script": ["thickness 300", "height 3.0", "thickness 180"]})
def b25(c: Ctx, s: dict[str, Any]) -> None:
    def build() -> list[float]:
        k = Ctx()
        w, _ = k.wall(6000, 250, 3.2)
        k.run("model.add_door", wall=w, offset=3.0, width=900, height=2100)
        for step in s["script"]:
            key, value = step.split()
            k.run("model.set_property", id=w, key=key, value=float(value))
        m = measure(k.p, w)
        return [m["volume"], *m["size"]]
    a, b = build(), build()
    c.holds("two builds of the same script are bit-identical", a == b, first=a, second=b)
    c.expect("and equal the closed form", a[0], 6.0 * 0.18 * 3.0 - 0.9 * 2.1 * 0.18)


@task("B26", "edit", "Edits to one wall do not alter its neighbours", {"neighbours": 2})
def b26(c: Ctx, s: dict[str, Any]) -> None:
    ws = [c.wall(6000, 250, 3.2)[0] for _ in range(3)]
    ref = [measure(c.p, w)["volume"] for w in ws]
    c.run("model.set_property", id=ws[1], key="thickness", value=400)
    c.holds("the other walls are unchanged", abs(measure(c.p, ws[0])["volume"] - ref[0]) < 1e-12 and abs(measure(c.p, ws[2])["volume"] - ref[2]) < 1e-12)
    c.volume("the edited wall", ws[1], 6.0 * 0.4 * 3.2)
    c.roundtrip(ws)


# --------------------------------------------------------------------------------------------------------------------------- C: rebuild
def _recon_task(i: int, name: str) -> None:
    @task(f"C{i:02d}", "rebuild", f"Reconstruct editable BIM elements from the dimensioned drawing '{name}'", {"fixture": name}, needs=("scipy", "PIL"))
    def run(c: Ctx, s: dict[str, Any]) -> None:
        from applications.SPADRA.tests import evaluate_reconstruction as EV
        from applications.SPADRA.tests import recon_fixtures as F
        fx = next(f for f in F.fixtures() if f.name == s["fixture"])
        r = EV.evaluate_one(fx)
        c.holds("the drawing was read into walls", r["walls"]["found"] > 0, walls=r["walls"])
        c.holds("the elements built into a project have the right IFC classes", r.get("ifc", {}).get("wrong_wall_or_space", 1) == 0 and r.get("ifc", {}).get("elements", 0) > 0, ifc=r.get("ifc"))
        c.measures["reported_not_gated"] = {k: r[k] for k in ("walls", "openings", "rooms", "dimensions") if k in r}


for n, name in enumerate(["single-room", "two-rooms", "l-shape", "three-rooms-m", "noisy-raster", "two-rooms-pdf", "three-rooms-pdf-raster", "arrow-dims", "mixed-thickness", "jpeg", "four-rooms", "small-room"], 1):
    _recon_task(n, name)


# -------------------------------------------------------------------------------------------------------------------------------- runner
def blocked_reason(needs: tuple[str, ...]) -> str | None:
    import importlib
    for module in needs:
        try:
            importlib.import_module(module)
        except ImportError as exc:
            return f"{module} is not installed here ({exc})"
    return None


def rebuild_blocked_reason(fixture: str) -> str | None:
    """Reconstruction needs more than importable modules: a raster drawing needs OpenCV/scikit-image *and* the Tesseract program to read printed dimensions, a vector PDF needs pdfplumber.
    SPADRA reports what is missing itself (``reconstruct.capabilities``); a task whose source cannot be read here is ``blocked`` with that reason, never ``fail``."""
    from applications.SPADRA import reconstruct as R
    from applications.SPADRA.tests import recon_fixtures as F
    fx = next(f for f in F.fixtures() if f.name == fixture)
    caps = R.capabilities()
    if fx.kind == "pdf":
        src = caps["sources"]["pdf"]
        return None if src["available"] else src["reason"]
    if not caps["sources"]["raster"]["available"]:
        return caps["sources"]["raster"]["reason"]
    if not caps["ocr"]["available"]:
        return caps["ocr"]["reason"]
    return None


def run_one(t: Task) -> dict[str, Any]:
    why = blocked_reason(t.needs) or (rebuild_blocked_reason(t.spec["fixture"]) if t.category == "rebuild" and not blocked_reason(t.needs) else None)
    if why:
        return {"id": t.id, "category": t.category, "title": t.title, "status": "blocked", "reason": why, "spec": t.spec}
    runs = []
    for _ in range(2):                                                                  # twice: the measurements must be identical
        c = Ctx()
        started = time.perf_counter()
        try:
            t.run(c, t.spec)
            error = None
        except Exception as exc:                                                        # a crash is a failed task with its traceback, not a stopped run
            error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=4)}"
        runs.append((c, (time.perf_counter() - started) * 1000, error))
    (c1, ms1, err1), (c2, ms2, err2) = runs
    deterministic = json.dumps(c1.measures, sort_keys=True, default=float) == json.dumps(c2.measures, sort_keys=True, default=float) and [k.ok for k in c1.checks] == [k.ok for k in c2.checks]
    failed = [k for k in c1.checks if not k.ok]
    ok = not err1 and not failed and bool(c1.checks) and deterministic
    return {"id": t.id, "category": t.category, "title": t.title, "status": "pass" if ok else "fail", "checks": len(c1.checks), "failed_checks": [{"name": k.name, "detail": k.detail} for k in failed],
            "deterministic": deterministic, "error": err1, "ms": [round(ms1, 2), round(ms2, 2)], "measures": c1.measures, "spec": t.spec}


def environment() -> dict[str, Any]:
    versions = {}
    for module in ("numpy", "trimesh", "scipy", "shapely", "manifold3d", "jsonschema", "PIL"):
        try:
            versions[module] = getattr(__import__(module), "__version__", "installed")
        except ImportError:
            versions[module] = None
    return {"python": sys.version.split()[0], "platform": platform.platform(), "machine": platform.machine(), "dependencies": versions, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    ap.add_argument("--only", help="comma-separated task ids")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args(argv)
    chosen = [t for t in TASKS if not args.only or t.id in args.only.split(",")]
    if args.list:
        for t in chosen:
            print(f"{t.id}  {t.category:8s} {t.title}")
        return 0
    results = [run_one(t) for t in chosen]
    summary = {cat: {s: sum(1 for r in results if r["category"] == cat and r["status"] == s) for s in ("pass", "fail", "blocked")} for cat in ("create", "edit", "rebuild")}
    out = {"suite": "SPADRA Parametric Benchmark Suite", "tasks": len(results), "summary": summary, "environment": environment(), "results": results}
    for r in results:
        print(f"{r['id']:4s} {r['status']:8s} {r['title']}" + (f"  [{r.get('reason') or [f['name'] for f in r.get('failed_checks', [])][:2] or r.get('error', '')[:80]}]" if r["status"] != "pass" else ""))
    print(json.dumps(summary))
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(out, indent=1, default=float), encoding="utf-8")
    return 0 if not any(r["status"] == "fail" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
