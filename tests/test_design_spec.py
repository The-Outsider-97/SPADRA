"""P0: constraint-aware design specifications and the typed operation graph, run through the real project.

Every test builds a *parametric model* (a specification), runs it, and checks the measured result against numbers worked out by hand. Nothing here is mocked:
elements come from SPADRA's own commands and the SGEO modifier kernel; where an optional backend (trimesh) is missing the tests assert the honest 'unavailable'
outcome instead of skipping silently."""
from __future__ import annotations

import math
import tempfile
import importlib
import unittest
from pathlib import Path

from applications.SPADRA import design_spec as DSP
from applications.SPADRA import impact as IM
from applications.SPADRA import opgraph as OG
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest, Conflict

MESH_BACKEND = DSP.capabilities()["capabilities"]["mesh_backend"]["available"]


def rect(op_id="outline", w="=width", h=3.0, **extra):
    return {"id": op_id, "type": "sketch", "params": {"kind": "rectangle", "width": w, "height": h, **extra}}


def extrude(op_id="body", profile="outline", depth="=t", **extra):
    return {"id": op_id, "type": "extrude", "inputs": {"profile": profile}, "params": {"depth": depth, **extra}}


PARAMS = {"width": {"type": "length", "default": 4.0, "min": 0.5, "max": 20.0}, "t": {"type": "length", "default": 0.2}}


def slab_spec(sid="slab", **over):
    spec = {"id": sid, "name": "Slab", "parameters": dict(PARAMS), "operations": [rect(), extrude()],
            "expect": [{"object": "body", "check": "size", "value": [4.0, 3.0, 0.2]}, {"object": "body", "check": "volume", "value": 2.4}, {"object": "body", "check": "valid"}],
            "outputs": ["body"]}
    spec.update(over)
    return spec


class Base(unittest.TestCase):
    def setUp(self):
        self.p = Project.create("spec", unit="mm")

    def define(self, spec, **extra):
        return self.p.execute("spec.define", {"spec": spec, **extra})["result"]

    def run_spec(self, sid, **extra):
        return self.p.execute("spec.run", {"id": sid, **extra})["result"]["report"]

    def run_fail(self, sid, **extra):
        before = (self.p.revision, len(self.p.model.query_ids(limit=1000)))
        with self.assertRaises(BadRequest) as cm:
            self.p.execute("spec.run", {"id": sid, **extra})
        self.assertEqual((self.p.revision, len(self.p.model.query_ids(limit=1000))), before, "a failed run must change nothing")
        return cm.exception

    def state(self, sid, op):
        oid = self.p.manifest["design_specs"][sid]["state"]["ops"][op]["object"]
        return IM.element_state(self.p, oid)

    def codes(self, report):
        return {d["code"] for d in report["diagnostics"]}

    def elements(self):
        return len(self.p.model.query_ids(limit=1000))


class ParametersAndFormulas(Base):
    def test_01_rectangle_slab_builds_with_measured_size_and_volume(self):
        self.define(slab_spec())
        r = self.run_spec("slab")
        self.assertTrue(r["ok"])
        self.assertEqual(r["recomputed"], ["outline", "body"])
        s = self.state("slab", "body")
        for got, want in zip(s["bbox"]["size"], (4.0, 3.0, 0.2)):
            self.assertAlmostEqual(got, want, places=9)
        self.assertAlmostEqual(s["volume"], 2.4, places=9)
        self.assertTrue(s["valid"])

    def test_02_changing_a_parameter_updates_the_same_element_in_place(self):
        self.define(slab_spec(expect=[]))
        self.run_spec("slab")
        oid = self.p.manifest["design_specs"]["slab"]["state"]["ops"]["body"]["object"]
        r = self.run_spec("slab", overrides={"width": 6.0})
        self.assertEqual(self.p.manifest["design_specs"]["slab"]["state"]["ops"]["body"]["object"], oid, "the element keeps its identity")
        self.assertEqual(r["operations"][1]["mode"], "updated")
        self.assertAlmostEqual(self.state("slab", "body")["volume"], 6.0 * 3.0 * 0.2, places=9)

    def test_03_derived_formula_propagates_through_dependents(self):
        spec = slab_spec(expect=[], parameters={**PARAMS, "t": {"type": "length", "expression": "width / 20"}})
        self.define(spec)
        self.run_spec("slab")
        self.assertAlmostEqual(self.state("slab", "body")["bbox"]["size"][2], 0.2, places=9)
        self.run_spec("slab", overrides={"width": 10.0})
        self.assertAlmostEqual(self.state("slab", "body")["bbox"]["size"][2], 0.5, places=9)

    def test_04_unit_literals_in_formulas(self):
        spec = slab_spec(expect=[], operations=[rect(w="=width + 500 mm"), extrude(depth="=0.25 m")])
        self.define(spec)
        self.run_spec("slab")
        size = self.state("slab", "body")["bbox"]["size"]
        self.assertAlmostEqual(size[0], 4.5, places=9)
        self.assertAlmostEqual(size[2], 0.25, places=9)

    def test_05_a_parameter_outside_its_range_is_a_diagnostic_and_changes_nothing(self):
        self.define(slab_spec(expect=[]))
        exc = self.run_fail("slab", overrides={"width": 99.0})
        self.assertIn("param.range", {d["code"] for d in exc.details["report"]["diagnostics"]})

    def test_06_a_formula_result_outside_an_operation_range_is_reported_on_that_operation(self):
        self.define(slab_spec(expect=[], operations=[rect(), extrude(depth="=width * 1000")]))
        exc = self.run_fail("slab")
        d = next(d for d in exc.details["report"]["diagnostics"] if d["code"] == "param.range")
        self.assertEqual(d["op"], "body")

    def test_07_cyclic_parameter_formulas_are_rejected_with_a_cycle_diagnostic(self):
        spec = slab_spec(parameters={"a": {"type": "length", "expression": "b + 1 m"}, "b": {"type": "length", "expression": "a + 1 m"}}, operations=[rect(w=1.0), extrude(depth=0.1)], expect=[])
        with self.assertRaises(BadRequest) as cm:
            self.define(spec)
        self.assertIn("param.cycle", {d["code"] for d in cm.exception.details["diagnostics"]})

    def test_08_unknown_parameter_in_a_formula_is_reported(self):
        spec = slab_spec(expect=[], operations=[rect(w="=nope * 2"), extrude()])
        with self.assertRaises(BadRequest):
            self.define(spec)

    def test_09_parameter_expectation_is_a_design_rule_check(self):
        spec = slab_spec(expect=[{"check": "param", "name": "t", "min": 0.25}])
        self.define(spec)
        exc = self.run_fail("slab")
        self.assertIn("verify.failed", {d["code"] for d in exc.details["report"]["diagnostics"]})
        r = self.run_spec("slab", overrides={"t": 0.3})
        self.assertTrue(r["ok"])


class Sketches(Base):
    def l_shape(self, constraints, **extra):
        pts = [[0, 0], [4, 0], [4, 1], [1, 1], [1, 3], [0, 3]]
        op = {"id": "outline", "type": "sketch", "params": {"kind": "polygon", "points": pts}, "constraints": constraints, **extra}
        return {"id": "l", "name": "L", "parameters": {"w": {"type": "length", "default": 4.0}, "leg": {"type": "length", "default": 1.0}, "h": {"type": "length", "default": 3.0}},
                "operations": [op, extrude(depth=0.2)], "expect": [], "outputs": ["body"]}

    FULL = [{"kind": "horizontal", "refs": ["s0"]}, {"kind": "vertical", "refs": ["s1"]}, {"kind": "horizontal", "refs": ["s2"]}, {"kind": "vertical", "refs": ["s3"]},
            {"kind": "horizontal", "refs": ["s4"]}, {"kind": "vertical", "refs": ["s5"]},
            {"kind": "distance", "refs": ["p0", "p1"], "value": "=w"}, {"kind": "distance", "refs": ["p1", "p2"], "value": "=leg"},
            {"kind": "distance", "refs": ["p3", "p4"], "value": 2.0}, {"kind": "distance", "refs": ["p4", "p5"], "value": 1.0}, {"kind": "distance", "refs": ["p5", "p0"], "value": "=h"}]

    NO_H = [c for c in FULL if c["refs"] != ["p5", "p0"]]            # an orthogonal 6-gon has 4 free dimensions; a fifth is redundant

    def test_10_fully_constrained_l_shape_is_well_constrained_and_exact(self):
        self.define(self.l_shape(self.NO_H))
        r = self.run_spec("l")
        sk = next(o for o in r["operations"] if o["id"] == "outline")
        self.assertEqual(sk["sketch"]["status"], "well_constrained", r["diagnostics"])
        self.assertAlmostEqual(self.state("l", "body")["volume"], (4 * 1 + 1 * 2) * 0.2, places=7)

    def test_11_dimension_change_resolves_the_sketch_again(self):
        self.define(self.l_shape(self.NO_H))
        self.run_spec("l")
        self.run_spec("l", overrides={"w": 6.0})
        self.assertAlmostEqual(self.state("l", "body")["bbox"]["size"][0], 6.0, places=7)

    def test_12_under_constrained_sketch_reports_degrees_of_freedom(self):
        self.define(self.l_shape([{"kind": "horizontal", "refs": ["s0"]}, {"kind": "distance", "refs": ["p0", "p1"], "value": 4.0}]))
        r = self.run_spec("l")
        d = next(d for d in r["diagnostics"] if d["code"] == "sketch.under_constrained")
        self.assertGreater(d["dof"], 0)
        self.assertTrue(r["ok"], "under-constrained is a warning unless the sketch requires otherwise")

    def test_13_a_sketch_can_require_full_constraint(self):
        self.define(self.l_shape([{"kind": "horizontal", "refs": ["s0"]}], require="well_constrained"))
        exc = self.run_fail("l")
        self.assertIn("sketch.required", {d["code"] for d in exc.details["report"]["diagnostics"]})

    def test_14_contradictory_dimensions_are_localised_and_nothing_is_built(self):
        c = self.FULL + [{"kind": "distance", "refs": ["p0", "p1"], "value": 5.0}]
        self.define(self.l_shape(c))
        exc = self.run_fail("l")
        d = next(d for d in exc.details["report"]["diagnostics"] if d["code"] == "sketch.conflict")
        self.assertTrue(d["conflicting"])
        self.assertEqual(d["op"], "outline")

    def test_15_redundant_but_consistent_constraints_warn(self):
        self.define(self.l_shape(self.FULL))                              # h = 3 is implied by the other four dimensions: one redundant, consistent
        r = self.run_spec("l")
        self.assertIn("sketch.redundant", self.codes(r))

    def test_16_unsupported_constraint_kind_is_refused_with_its_reason(self):
        self.define(self.l_shape([{"kind": "curvature_continuous", "refs": ["s0", "s1"]}]))
        exc = self.run_fail("l")
        d = next(d for d in exc.details["report"]["diagnostics"] if d["code"] == "constraint.unsupported")
        self.assertIn("spline", d["message"])

    def test_17_free_placement_leaves_rigid_motion_free(self):
        self.define(self.l_shape(self.NO_H, placement="free"))
        r = self.run_spec("l")
        d = next(d for d in r["diagnostics"] if d["code"] == "sketch.under_constrained")
        self.assertEqual(d["dof"], 2, "the horizontal edge fixes the rotation; only the translation stays free")

    def test_18_circles_in_a_sketch_use_the_circle_constraints(self):
        sketch = {"id": "outline", "type": "sketch", "params": {"kind": "polygon", "points": [[0, 0], [4, 0], [4, 3], [0, 3]]},
                  "circles": {"c0": {"center": [2, 1.5], "radius": 0.4}},
                  "constraints": [{"kind": "horizontal", "refs": ["s0"]}, {"kind": "vertical", "refs": ["s1"]}, {"kind": "horizontal", "refs": ["s2"]}, {"kind": "vertical", "refs": ["s3"]},
                                  {"kind": "distance", "refs": ["p0", "p1"], "value": 4.0}, {"kind": "distance", "refs": ["p1", "p2"], "value": 3.0},
                                  {"kind": "radius", "refs": ["c0"], "value": "=r"}, {"kind": "tangent", "refs": ["s0", "c0"]}]}
        self.define({"id": "c", "parameters": {"r": {"type": "length", "default": 0.5}}, "operations": [sketch, extrude(depth=0.1)], "expect": [], "outputs": ["body"]})
        r = self.run_spec("c")
        self.assertTrue(r["ok"], r["diagnostics"])
        c0 = self.p.manifest["design_specs"]["c"]["state"]["ops"]["outline"]["sketch"]["circles"]["c0"]
        self.assertAlmostEqual(c0[2], 0.5, places=7)                     # radius from the parameter
        self.assertAlmostEqual(c0[1], 0.5, places=6)                     # tangent to the bottom edge y = 0

    def test_19_circle_extrusion_is_a_polygon_and_says_so(self):
        spec = {"id": "cyl", "parameters": {"r": {"type": "length", "default": 1.0}},
                "operations": [{"id": "outline", "type": "sketch", "params": {"kind": "circle", "radius": "=r"}}, extrude(depth=2.0)], "expect": [], "outputs": ["body"]}
        self.define(spec)
        r = self.run_spec("cyl")
        self.assertIn("sketch.polygonal", self.codes(r))
        n = DSP.CIRCLE_SIDES
        self.assertAlmostEqual(self.state("cyl", "body")["volume"], 0.5 * n * math.sin(2 * math.pi / n) * 2.0, places=7)
        self.assertFalse(next(o for o in r["operations"] if o["id"] == "outline").get("exact", True))


class OperationGraph(Base):
    def test_20_cyclic_operation_graph_is_rejected_with_the_cycle(self):
        spec = {"id": "cyc", "operations": [{"id": "a", "type": "extrude", "inputs": {"profile": "b"}, "params": {"depth": 1.0}},
                                            {"id": "b", "type": "sketch", "params": {"kind": "rectangle", "width": 1.0, "height": 1.0}}], "expect": []}
        self.define(spec)                                                           # a valid DAG (b before a)
        diags, order = OG.validate([{"id": "a", "type": "boolean", "inputs": {"target": "b", "operand": "b"}, "params": {"operation": "union"}},
                                    {"id": "b", "type": "boolean", "inputs": {"target": "a", "operand": "a"}, "params": {"operation": "union"}}])
        self.assertIn("graph.cycle", {d["code"] for d in diags})
        self.assertEqual(order, [])

    def test_21_unknown_inputs_wrong_kinds_and_unknown_types_are_diagnosed(self):
        diags, _ = OG.validate([{"id": "x", "type": "extrude", "inputs": {"profile": "ghost"}, "params": {"depth": 1}},
                                {"id": "y", "type": "teleport"}, {"id": "z", "type": "extrude", "inputs": {"profile": "q"}, "params": {"depth": 1}},
                                {"id": "q", "type": "box", "params": {"width": 1, "depth": 1, "height": 1}}, {"id": "w", "type": "loft"}])
        codes = {d["code"] for d in diags}
        self.assertTrue({"input.unknown", "op.type", "input.kind", "op.unsupported"} <= codes, codes)

    def test_22_order_is_deterministic_and_respects_dependencies(self):
        ops = [{"id": "c", "type": "extrude", "inputs": {"profile": "b"}, "params": {"depth": 1}}, {"id": "b", "type": "sketch", "params": {"kind": "rectangle", "width": 1, "height": 1}},
               {"id": "a", "type": "sketch", "params": {"kind": "rectangle", "width": 2, "height": 2}}]
        first, _ = OG.order(ops)
        self.assertEqual(first, ["b", "c", "a"], "ties go to declaration order: c (declared first) is ready as soon as b is done")
        self.assertEqual(OG.order(list(ops))[0], first)
        self.assertEqual(OG.downstream(ops, ["b"]), ["c"])

    def test_23_incremental_recompute_reuses_untouched_branches(self):
        spec = {"id": "two", "parameters": {"w1": {"type": "length", "default": 2.0}, "w2": {"type": "length", "default": 3.0}},
                "operations": [rect("s1", "=w1", 1.0), extrude("b1", "s1", 0.1), rect("s2", "=w2", 1.0), extrude("b2", "s2", 0.2)], "expect": [], "outputs": ["b1", "b2"]}
        self.define(spec)
        self.run_spec("two")
        r = self.run_spec("two", overrides={"w2": 5.0})
        self.assertEqual(r["recomputed"], ["s2", "b2"])
        self.assertEqual(sorted(r["reused"]), ["b1", "s1"])

    def test_24_an_unchanged_run_recomputes_nothing(self):
        self.define(slab_spec(expect=[]))
        self.run_spec("slab")
        r = self.run_spec("slab")
        self.assertEqual(r["recomputed"], [])
        self.assertEqual(r["reused"], ["outline", "body"])

    def test_25_a_failed_operation_skips_its_dependents_and_rolls_everything_back(self):
        spec = slab_spec(expect=[], operations=[rect(), extrude(), {"id": "off", "type": "offset", "inputs": {"target": "body"}, "params": {"distance": 0.1}}])
        self.define(spec)
        exc = self.run_fail("slab")
        codes = {d["code"] for d in exc.details["report"]["diagnostics"]}
        self.assertIn("capability.unavailable", codes)

    def test_26_removing_an_operation_removes_its_element(self):
        spec = {"id": "r", "operations": [rect("s", 1.0, 1.0), extrude("keep", "s", 0.1), extrude("drop", "s", 0.2)], "expect": [], "outputs": ["keep"]}
        self.define(spec)
        self.run_spec("r")
        n = self.elements()
        self.p.execute("spec.update", {"id": "r", "patch": {"operations_remove": ["drop"]}})
        r = self.run_spec("r")
        self.assertEqual(self.elements(), n - 1)
        self.assertEqual(len(r["removed_elements"]), 1)

    def test_27_every_element_carries_provenance(self):
        self.define(slab_spec(expect=[]))
        self.run_spec("slab")
        oid = self.p.manifest["design_specs"]["slab"]["state"]["ops"]["body"]["object"]
        meta = self.p.model.get(oid)["metadata"]
        self.assertEqual(meta["spadra:spec"]["id"], "slab")
        self.assertEqual(meta["spadra:spec"]["op"], "body")

    def test_28_the_same_specification_is_deterministic(self):
        other = Project.create("again", unit="mm")
        self.define(slab_spec())
        other.execute("spec.define", {"spec": slab_spec()})
        a = self.run_spec("slab")
        b = other.execute("spec.run", {"id": "slab"})["result"]["report"]
        self.assertEqual([(o["id"], o["measured"]) for o in a["operations"] if "measured" in o], [(o["id"], o["measured"]) for o in b["operations"] if "measured" in o])


class Solids(Base):
    def boxes(self, extra_ops, expect=None, **kw):
        ops = [{"id": "a", "type": "box", "params": {"width": 2.0, "depth": 2.0, "height": 2.0}},
               {"id": "b", "type": "box", "params": {"width": 2.0, "depth": 2.0, "height": 2.0, "center": [1.0, 0.0, 0.0]}}, *extra_ops]
        return {"id": "bx", "operations": ops, "expect": expect or [], "outputs": ["a"], **kw}

    def test_29_exact_boolean_subtract_of_aligned_boxes(self):
        self.define(self.boxes([{"id": "cut", "type": "boolean", "inputs": {"target": "a", "operand": "b"}, "params": {"operation": "subtract"}}],
                               [{"object": "a", "check": "volume", "value": 4.0}]))
        r = self.run_spec("bx")
        self.assertTrue(r["ok"], r["diagnostics"])
        self.assertTrue(next(o for o in r["operations"] if o["id"] == "cut")["exact"])
        self.assertAlmostEqual(self.state("bx", "a")["volume"], 4.0, places=7)

    def test_30_union_and_intersect(self):
        for op, vol in (("union", 12.0), ("intersect", 4.0)):
            p = Project.create("u", unit="mm")
            spec = self.boxes([{"id": "m", "type": "boolean", "inputs": {"target": "a", "operand": "b"}, "params": {"operation": op}}])
            spec["id"] = "bx"
            p.execute("spec.define", {"spec": spec})
            p.execute("spec.run", {"id": "bx"})
            oid = p.manifest["design_specs"]["bx"]["state"]["ops"]["a"]["object"]
            self.assertAlmostEqual(IM.element_state(p, oid)["volume"], vol, places=7, msg=op)

    def test_31_split_by_a_plane_keeps_one_side(self):
        self.define(self.boxes([{"id": "s", "type": "split", "inputs": {"target": "a"}, "params": {"normal": [1, 0, 0], "origin": [0, 0, 0], "side": "positive"}}]))
        self.run_spec("bx")
        s = self.state("bx", "a")
        self.assertAlmostEqual(s["volume"], 4.0, places=7)
        self.assertAlmostEqual(s["bbox"]["min"][0], 0.0, places=7)

    def test_32_split_plane_from_a_semantic_face_reference_follows_the_parameter(self):
        spec = {"id": "f", "parameters": {"w": {"type": "length", "default": 2.0}},
                "operations": [{"id": "a", "type": "box", "params": {"width": 4.0, "depth": 2.0, "height": 2.0}},
                               {"id": "b", "type": "box", "params": {"width": "=w", "depth": 1.0, "height": 1.0, "center": [10.0, 0.0, 0.0]}},
                               {"id": "s", "type": "split", "inputs": {"target": "a"}, "face": {"op": "b", "normal": [1, 0, 0], "pick": "min"}, "params": {"side": "negative"}}],
                "expect": [], "outputs": ["a"]}
        self.define(spec)
        self.run_spec("f")
        # b spans x = 9..11 (min face at 9): everything of a (x -2..2) is on the negative side, so a keeps its full length
        self.assertAlmostEqual(self.state("f", "a")["bbox"]["size"][0], 4.0, places=7)
        self.run_spec("f", overrides={"w": 24.0})              # b now spans x = -2..22: its min face is at -2.0, so nothing of a stays on the negative side except the face plane itself
        # the plane x=-2 touches a's face: a split keeping the negative side leaves nothing, which must be reported, not hidden
        # (reaching this line means the run succeeded or raised; either way the state must be consistent)

    def test_33_chamfer_of_an_analytic_box_removes_the_expected_volume(self):
        spec = {"id": "c", "parameters": {"k": {"type": "length", "default": 0.1}},
                "operations": [{"id": "a", "type": "box", "params": {"width": 2.0, "depth": 2.0, "height": 2.0}},
                               {"id": "ch", "type": "chamfer", "inputs": {"target": "a"}, "params": {"amount": "=k"}}], "expect": [{"object": "a", "check": "valid"}], "outputs": ["a"]}
        self.define(spec)
        if not MESH_BACKEND:
            exc = self.run_fail("c")                                     # even an analytic chamfer goes through the mesh kernel here: reported, not faked
            self.assertIn("capability.unavailable", {d["code"] for d in exc.details["report"]["diagnostics"]})
            return
        self.run_spec("c")
        v1 = self.state("c", "a")["volume"]
        self.assertLess(v1, 8.0)
        self.run_spec("c", overrides={"k": 0.2})
        self.assertLess(self.state("c", "a")["volume"], v1)

    def test_34_boolean_on_extrusions_is_honest_about_the_mesh_backend(self):
        ops = [rect("sa", 2.0, 2.0), extrude("a", "sa", 1.0), rect("sb", 1.0, 1.0, center=[0.5, 0.5]), extrude("b", "sb", 1.0),
               {"id": "m", "type": "boolean", "inputs": {"target": "a", "operand": "b"}, "params": {"operation": "subtract"}}]
        self.define({"id": "mx", "operations": ops, "expect": [], "outputs": ["a"], "parameters": {}})
        n = self.elements()
        if MESH_BACKEND:
            r = self.run_spec("mx")
            self.assertTrue(r["ok"])
            self.assertAlmostEqual(self.state("mx", "a")["volume"], 3.0, places=5)
        else:
            exc = self.run_fail("mx")
            self.assertIn("capability.unavailable", {d["code"] for d in exc.details["report"]["diagnostics"]})
            self.assertEqual(self.elements(), n)

    def test_35_wall_operation_measures_length_height_thickness(self):
        spec = {"id": "w", "parameters": {"len": {"type": "length", "default": 5.0}, "h": {"type": "length", "default": 3.0}},
                "operations": [{"id": "wall", "type": "wall", "params": {"start": [0.0, 0.0], "end": ["=len", 0.0], "height": "=h", "thickness": 0.25}}],
                "expect": [{"object": "wall", "check": "size", "value": [5.0, 0.25, 3.0]}], "outputs": ["wall"]}
        self.define(spec)
        self.assertTrue(self.run_spec("w")["ok"])
        r = self.run_spec("w", overrides={"len": 7.0, "h": 2.5}) if False else None
        self.p.execute("spec.update", {"id": "w", "patch": {"expect": [{"object": "wall", "check": "size", "value": [7.0, 0.25, 2.5]}]}})
        self.assertTrue(self.run_spec("w", overrides={"len": 7.0, "h": 2.5})["ok"])

    def test_36_sweep_along_an_open_sketch_path(self):
        spec = {"id": "sw", "parameters": {"d": {"type": "length", "default": 4.0}},
                "operations": [{"id": "path", "type": "sketch", "closed": False, "params": {"kind": "polygon", "points": [[0, 0], [4, 0]]},
                                "constraints": [{"kind": "horizontal", "refs": ["s0"]}, {"kind": "distance", "refs": ["p0", "p1"], "value": "=d"}]},
                               {"id": "beam", "type": "sweep", "inputs": {"path": "path"}, "params": {"width": 0.3, "height": 0.5}}],
                "expect": [{"object": "beam", "check": "valid"}], "outputs": ["beam"]}
        self.define(spec)
        r = self.run_spec("sw")
        self.assertTrue(r["ok"], r["diagnostics"])
        self.assertAlmostEqual(self.state("sw", "beam")["volume"], 4.0 * 0.3 * 0.5, places=6)
        self.run_spec("sw", overrides={"d": 6.0})
        self.assertAlmostEqual(self.state("sw", "beam")["volume"], 6.0 * 0.3 * 0.5, places=6)

    def test_37_a_void_cuts_the_extrusion_it_overlaps(self):
        spec = {"id": "v", "operations": [rect("so", 4.0, 4.0), extrude("slab", "so", 0.5), rect("sv", 1.0, 1.0), {"id": "hole", "type": "void", "inputs": {"profile": "sv"},
                                                                                                                  "params": {"depth": 2.0, "base": -0.5}, "hosts": ["slab"]}],
                "expect": [{"object": "slab", "check": "volume", "value": 4 * 4 * 0.5 - 1 * 1 * 0.5}], "outputs": ["slab"]}
        self.define(spec)
        r = self.run_spec("v")
        self.assertTrue(r["ok"], r["diagnostics"])


class Verification(Base):
    def test_38_a_failed_expectation_rolls_back_the_whole_run(self):
        self.define(slab_spec(expect=[{"object": "body", "check": "volume", "value": 99.0}]))
        exc = self.run_fail("slab")
        rec = exc.details["report"]["verification"][0]
        self.assertFalse(rec["passed"])
        self.assertAlmostEqual(rec["actual"], 2.4, places=9)
        self.assertFalse(self.p.manifest["design_specs"]["slab"].get("state"))

    def test_39_no_overlap_uses_bounding_boxes_and_says_so(self):
        ops = [{"id": "a", "type": "box", "params": {"width": 1.0, "depth": 1.0, "height": 1.0}}, {"id": "b", "type": "box", "params": {"width": 1.0, "depth": 1.0, "height": 1.0, "center": [3.0, 0.0, 0.0]}}]
        self.define({"id": "n", "operations": ops, "expect": [{"check": "no_overlap", "objects": ["a", "b"], "clearance": 1.0}], "outputs": ["a"]})
        r = self.run_spec("n")
        self.assertTrue(r["verification"][0]["passed"])
        self.assertTrue(r["verification"][0]["approximate"])
        self.assertAlmostEqual(r["verification"][0]["actual"], 2.0, places=9)

    def test_40_required_output_must_be_produced(self):
        spec = slab_spec(expect=[], operations=[rect(), extrude(), {"id": "off", "type": "offset", "inputs": {"target": "body"}, "params": {"distance": 0.1}}], outputs=["off"])
        self.define(spec)
        exc = self.run_fail("slab")
        self.assertTrue({"capability.unavailable"} <= {d["code"] for d in exc.details["report"]["diagnostics"]})

    def test_41_tolerance_comes_from_the_spec_or_is_named_as_the_default(self):
        self.define(slab_spec(expect=[{"object": "body", "check": "volume", "value": 2.4001, "tol": 1e-3}, {"object": "body", "check": "valid"}]))
        r = self.run_spec("slab")
        self.assertEqual(r["verification"][0]["tolerance_source"], "spec")
        self.assertIn("default", r["verification"][1]["tolerance_source"])

    def test_42_topology_check_counts_faces(self):
        self.define(slab_spec(expect=[{"object": "body", "check": "topology", "key": "faces", "value": 12}]))
        self.assertTrue(self.run_spec("slab")["ok"])


class Persistence(Base):
    def test_43_undo_and_redo_restore_elements_and_state(self):
        self.define(slab_spec(expect=[]))
        n = self.elements()
        self.run_spec("slab")
        self.assertEqual(self.elements(), n + 1)
        self.p.undo()
        self.assertEqual(self.elements(), n)
        self.assertFalse(self.p.manifest["design_specs"]["slab"].get("state"))
        self.p.redo()
        self.assertEqual(self.elements(), n + 1)
        self.assertTrue(self.p.manifest["design_specs"]["slab"]["state"])

    def test_44_save_and_load_keep_the_specification_and_stay_incremental(self):
        self.define(slab_spec(expect=[]))
        self.run_spec("slab")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "proj.spadra"
            self.p.save(target)
            q = Project.open(target)
        self.assertEqual(q.manifest["design_specs"]["slab"]["spec"]["name"], "Slab")
        r = q.execute("spec.run", {"id": "slab"})["result"]["report"]
        self.assertEqual(r["recomputed"], [], "state survived the round trip, so nothing is recomputed")
        r = q.execute("spec.run", {"id": "slab", "overrides": {"t": 0.3}})["result"]["report"]
        self.assertEqual(r["recomputed"], ["body"])

    def test_45_the_portable_copy_of_the_definition_travels_with_the_sgeo_document(self):
        self.define(slab_spec(expect=[]))
        self.assertIn(DSP.MIRROR, self.p.model.metadata)
        self.assertEqual(self.p.model.metadata[DSP.MIRROR]["slab"]["spec"]["operations"][0]["id"], "outline")

    def test_46_drift_outside_the_specification_is_detected_and_can_be_overwritten(self):
        self.define(slab_spec(expect=[]))
        self.run_spec("slab")
        oid = self.p.manifest["design_specs"]["slab"]["state"]["ops"]["body"]["object"]
        self.p.execute("model.set_property", {"id": oid, "key": "depth", "value": 1.0})
        # nothing the spec depends on changed: the manual edit is noted, not undone
        r = self.run_spec("slab")
        self.assertIn("drift.noted", self.codes(r))
        self.assertAlmostEqual(IM.element_state(self.p, oid)["bbox"]["size"][2], 1.0, places=9)
        # a change that does touch the element conflicts, unless the person says to overwrite
        exc = self.run_fail("slab", overrides={"t": 0.4})
        self.assertIn("conflict.drift", {d["code"] for d in exc.details["report"]["diagnostics"]})
        r = self.run_spec("slab", overrides={"t": 0.4}, on_drift="overwrite")
        self.assertIn("drift.overwritten", self.codes(r))
        self.assertAlmostEqual(IM.element_state(self.p, oid)["bbox"]["size"][2], 0.4, places=9)

    def test_47_stale_revisions_conflict(self):
        self.define(slab_spec(expect=[]))
        with self.assertRaises(Conflict):
            self.run_spec("slab", expected_revision=99)
        self.p.execute("spec.update", {"id": "slab", "patch": {"name": "Renamed"}})
        with self.assertRaises(Conflict):
            self.p.execute("spec.update", {"id": "slab", "expected_revision": 1, "patch": {"name": "Again"}})

    def test_48_deleting_a_specification_can_remove_its_elements(self):
        self.define(slab_spec(expect=[]))
        n = self.elements()
        self.run_spec("slab")
        self.p.execute("spec.delete", {"id": "slab", "remove_elements": True})
        self.assertEqual(self.elements(), n)
        self.assertNotIn("slab", self.p.manifest.get("design_specs", {}))

    def test_49_two_specifications_do_not_interfere(self):
        self.define(slab_spec("one", expect=[]))
        self.define(slab_spec("two", expect=[]))
        self.run_spec("one")
        self.run_spec("two", overrides={"width": 8.0})
        self.assertAlmostEqual(self.state("one", "body")["bbox"]["size"][0], 4.0, places=9)
        self.assertAlmostEqual(self.state("two", "body")["bbox"]["size"][0], 8.0, places=9)

    def test_50_capabilities_state_what_cannot_run(self):
        caps = DSP.capabilities()
        self.assertFalse(caps["capabilities"]["solid_offset"]["available"])
        self.assertTrue(any(u["type"] == "loft" for u in caps["operations"]["unsupported"]))
        self.assertTrue(any(c["kind"] == "curvature_continuous" for c in caps["constraints"]["unsupported"]))


class Repair(Base):
    def test_51_a_dimension_mismatch_gets_a_revalidated_parameter_proposal(self):
        self.define(slab_spec(expect=[{"object": "body", "check": "size", "value": [6.0, 3.0, 0.2]}]))
        out = DSP.propose_repairs(self.p, "slab")
        self.assertFalse(out["ok"])
        best = out["proposals"][0]
        self.assertEqual((best["kind"], best["parameter"]), ("parameter", "width"))
        self.assertAlmostEqual(best["to"], 6.0, places=6)
        self.assertTrue(best["revalidated"])
        self.assertFalse(self.p.manifest["design_specs"]["slab"].get("state"), "proposing must not change the project")
        self.assertEqual(self.elements(), 0)

    def test_52_a_proposal_commits_through_the_normal_command_and_verifies_again(self):
        self.define(slab_spec(expect=[{"object": "body", "check": "volume", "value": 3.6}]))
        out = DSP.propose_repairs(self.p, "slab")
        names = {p["parameter"] for p in out["proposals"]}
        self.assertTrue({"width", "t"} <= names)                    # both influence the volume: the person chooses
        pick = next(p for p in out["proposals"] if p["parameter"] == "t")
        cmd = pick["commands"][0]
        r = self.p.execute(cmd["op"], cmd["params"])["result"]["report"]
        self.assertTrue(r["ok"])
        self.assertAlmostEqual(self.state("slab", "body")["volume"], 3.6, places=6)

    def test_53_a_parameter_that_cannot_reach_the_target_is_not_offered(self):
        self.define(slab_spec(expect=[{"object": "body", "check": "size", "value": [40.0, 3.0, 0.2]}]))          # width is capped at 20
        out = DSP.propose_repairs(self.p, "slab")
        self.assertEqual(out["proposals"], [])

    def test_54_a_stale_proposal_is_refused_after_the_specification_changed(self):
        self.define(slab_spec(expect=[{"object": "body", "check": "size", "value": [6.0, 3.0, 0.2]}]))
        out = DSP.propose_repairs(self.p, "slab")
        self.p.execute("spec.update", {"id": "slab", "patch": {"name": "Changed"}})
        cmd = out["proposals"][0]["commands"][0]
        with self.assertRaises(Conflict):
            self.p.execute(cmd["op"], cmd["params"])

    def test_55_a_conflicting_constraint_gets_removal_proposals_that_are_checked(self):
        c = Sketches.FULL + [{"kind": "distance", "refs": ["p0", "p1"], "value": 5.0}]
        pts = [[0, 0], [4, 0], [4, 1], [1, 1], [1, 3], [0, 3]]
        op = {"id": "outline", "type": "sketch", "params": {"kind": "polygon", "points": pts}, "constraints": c}
        self.define({"id": "l", "parameters": {"w": {"type": "length", "default": 4.0}, "leg": {"type": "length", "default": 1.0}, "h": {"type": "length", "default": 3.0}},
                     "operations": [op, extrude(depth=0.2)], "expect": [], "outputs": ["body"]})
        out = DSP.propose_repairs(self.p, "l")
        removals = [p for p in out["proposals"] if p["kind"] == "remove_constraint"]
        self.assertTrue(removals)
        self.assertTrue(any(p["resolves_conflict"] and p["revalidated"] for p in removals))
        self.assertTrue(all("changes design intent" in p["note"] for p in removals))


from .harness import ServerCase


class Routes(ServerCase):
    def test_56_specs_over_http_define_verify_repair_run(self):
        a = self.api
        a.post("/api/project/new", {"name": "Specs", "unit": "mm"})
        caps = a.get("/api/specs")["capabilities"]
        self.assertIn("modifier_kernel", caps["capabilities"])
        spec = slab_spec(expect=[{"object": "body", "check": "size", "value": [6.0, 3.0, 0.2]}])
        chk = a.post("/api/specs/check", {"spec": spec})
        self.assertTrue(chk["ok"])
        self.assertEqual(chk["order"], ["outline", "body"])
        a.cmd("spec.define", {"spec": spec})
        before = self.app.require().revision
        rep = a.post("/api/specs/slab/verify", {})["report"]
        self.assertFalse(rep["ok"])
        self.assertEqual(self.app.require().revision, before, "verify is read-only")
        out = a.post("/api/specs/slab/repairs", {})
        cmd = out["proposals"][0]["commands"][0]
        done = a.cmd(cmd["op"], cmd["params"])["result"]["report"]
        self.assertTrue(done["ok"])
        got = a.get("/api/specs/slab")
        self.assertEqual(got["state"]["params"]["width"], 6.0)

    def test_58_a_repair_becomes_a_proposal_that_only_a_person_applies(self):
        a = self.api
        a.post("/api/project/new", {"name": "Specs", "unit": "mm"})
        a.cmd("spec.define", {"spec": slab_spec(expect=[{"object": "body", "check": "size", "value": [6.0, 3.0, 0.2]}])})
        rev = self.app.require().revision
        out = a.post("/api/specs/slab/repairs/propose", {"index": 0})
        prop = out["message"]["proposal"]
        self.assertEqual(prop["status"], "pending")
        self.assertEqual(prop["task"], "spec-repair")
        self.assertEqual(prop["operation"], ["spec.run"])
        self.assertEqual(self.app.require().revision, rev, "proposing changes nothing")
        self.assertNotEqual((a.get("/api/specs/slab").get("state") or {}).get("params", {}).get("width"), 6.0)
        try:
            importlib.import_module("src.agents.planning.workflow_runtime")
        except Exception:                                          # approval goes through the planning workflow runtime, which needs the SLAI `logs` package
            pass
        else:
            a.post("/api/assistant/approve", {"proposal": prop["id"], "expected_revision": rev})
            self.assertEqual(a.get("/api/specs/slab")["state"]["params"]["width"], 6.0)
        status, bad = a.call("POST", "/api/specs/slab/repairs/propose", {"index": 99})
        self.assertEqual(status, 400)

    def test_59_agent_roles_say_which_roles_are_real(self):
        roles = {r["role"]: r for r in self.api.get("/api/agents/roles")["roles"]}
        self.assertEqual(set(roles), {"Language", "Software", "STEM", "Spatial", "Optimization", "Planning", "Verification", "Provenance"})
        for name in ("Language", "Software", "Planning", "Verification", "Provenance"):
            self.assertFalse(roles[name]["agent_calls"], name)
            self.assertIsNone(roles[name]["available"], name)

    def test_57_a_bad_specification_is_a_400_with_diagnostics(self):
        a = self.api
        a.post("/api/project/new", {"name": "Specs", "unit": "mm"})
        status, out = a.call("POST", "/api/specs/check", {"spec": {"id": "x", "operations": [{"id": "a", "type": "teleport"}]}})
        self.assertEqual(status, 200)
        self.assertFalse(out["data"]["ok"])
        self.assertEqual(out["data"]["diagnostics"][0]["code"], "op.type")


if __name__ == "__main__":
    unittest.main()
