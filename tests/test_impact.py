"""P1: geometric impact analysis and the modifier preview contract, measured against the real kernel (and an independent oracle where the kernel is absent)."""
from __future__ import annotations

import math
import unittest

from applications.SPADRA import impact as IM
from applications.SPADRA.project import Project
from applications.SPADRA.tests import evaluate_impact as EV
from applications.tools.errors import BadRequest

from .harness import ServerCase

BACKEND = EV.IM_ENV()["mesh_backend"]


class MeasuredState(unittest.TestCase):
    def test_box_state_is_exact_and_closed(self):
        p = Project.create("s", unit="mm")
        oid = p.model.add_box(width=2, depth=3, height=4, name="b")
        s = IM.element_state(p, oid)
        self.assertEqual(s["bbox"]["size"], [2.0, 3.0, 4.0])
        self.assertAlmostEqual(s["volume"], 24.0, places=9)
        self.assertAlmostEqual(s["area"], 2 * (6 + 8 + 12), places=9)
        self.assertEqual((s["topology"]["euler"], s["topology"]["shells"], s["topology"]["boundary_edges"]), (2, 1, 0))
        self.assertTrue(s["valid"] and s["closed"])

    def test_open_surface_and_inside_out_solid_are_reported_not_hidden(self):
        p0 = Project.create("s", unit="mm")
        v = [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
        s = IM.mesh_state(v, [[0, 1, 2]])
        self.assertFalse(s["closed"])
        self.assertIsNone(s["volume"])
        self.assertTrue(any("open surface" in i for i in s["issues"]))
        from applications.SPADRA import documentation as D
        cube, faces = D.world_mesh(p0, p0.model.add_box(width=1, depth=1, height=1, name="cube"))
        ok = IM.mesh_state(cube, faces)
        flipped = IM.mesh_state(cube, [f[::-1] for f in faces])
        self.assertTrue(ok["valid"])
        self.assertFalse(flipped["valid"])
        self.assertTrue(any("inside out" in i for i in flipped["issues"]))

    def test_diff_reports_changes_and_a_drawing_entity_is_measured(self):
        p = Project.create("s", unit="mm")
        a = IM.element_state(p, p.model.add_box(width=1, depth=1, height=1, name="a"))
        b = IM.element_state(p, p.model.add_box(width=2, depth=1, height=1, name="b"))
        d = IM.diff_states(a, b)
        self.assertEqual(d["size"], [1.0, 0.0, 0.0])
        self.assertAlmostEqual(d["volume"], 1.0, places=9)
        self.assertTrue(d["geometry_changed"])
        view = next(v["id"] for v in p.manifest["views"] if v["kind"] == "plan")
        e = p.drawing(view).add_polyline([[0, 0], [2000, 0], [2000, 1000], [0, 1000]], closed=True)
        s = IM.drawing_state(p.drawing(view), e)
        self.assertAlmostEqual(s["area"] / 1.0, s["area"])
        self.assertEqual(s["vertices"], 4)


class Prediction(unittest.TestCase):
    def test_split_plane_clip_matches_hand_calculation_for_oblique_planes(self):
        p = Project.create("s", unit="mm")
        oid = p.model.add_box(width=2, depth=2, height=2, name="b")
        from applications.SPADRA import documentation as D
        v, f = D.world_mesh(p, oid)
        pos = IM.split_prediction(v, f, [0, 0, 0], [1, 1, 0], "positive")["volume"]
        neg = IM.split_prediction(v, f, [0, 0, 0], [1, 1, 0], "negative")["volume"]
        self.assertAlmostEqual(pos, 4.0, places=7)
        self.assertAlmostEqual(pos + neg, 8.0, places=7)
        corner = IM.split_prediction(v, f, [2.5, 0, 0], [1, 1, 1], "positive")["volume"]               # plane x+y+z = 2.5 cuts a corner tetrahedron with legs 0.5
        self.assertAlmostEqual(corner, 0.5 ** 3 / 6.0, places=9)
        self.assertAlmostEqual(IM.split_prediction(v, f, [1.0, 1.0, 1.0], [1, 1, 1], "positive")["volume"], 0.0, places=9)

    def test_prediction_of_aligned_box_booleans_is_exact(self):
        p = Project.create("s", unit="mm")
        a = p.model.add_box(width=2, depth=2, height=2, name="a")
        b = p.model.add_box(width=2, depth=2, height=2, center=(1, 0, 0), name="b")
        for mode, vol in (("union", 12.0), ("subtract", 4.0), ("intersect", 4.0)):
            r = IM.predict(p, {"id": a, "operation": "boolean", "operand": b, "parameters": {"operation": mode}})
            self.assertEqual(r["exact"]["volume"], vol, mode)

    def test_non_box_operands_get_bounds_not_a_guess(self):
        p = Project.create("s", unit="mm")
        lvl = p.manifest["levels"][0]["id"]
        a = p.execute("model.add_extrusion", {"level": lvl, "points": EV.L_SHAPE, "depth": 2.0})["result"]["id"]
        b = p.model.add_box(width=1, depth=1, height=1, center=(0.5, 0.5, 1.0), name="b")
        r = IM.predict(p, {"id": a, "operation": "boolean", "operand": b, "parameters": {"operation": "subtract"}})
        self.assertNotIn("exact", r)
        lo, hi = r["bounds"]["volume"]
        self.assertTrue(lo <= 10.0 - 1.0 + 1e-9 and hi == 10.0)

    def test_bend_has_no_predictor_and_says_so(self):
        p = Project.create("s", unit="mm")
        a = p.model.add_box(width=1, depth=1, height=1, name="a")
        r = IM.predict(p, {"id": a, "operation": "bend", "parameters": {}})
        self.assertFalse(r["available"])
        self.assertIn("numerical", r["reason"])

    def test_learned_prediction_is_reported_unavailable_with_its_probes(self):
        learned = IM.learned_prediction()
        self.assertFalse(learned["available"])
        self.assertIn("not a reproduction of CAD-WAM", learned["reason"])
        self.assertIn("torch", learned["probes"])


class Analysis(unittest.TestCase):
    def preview(self, p, request):
        draft = p.scratch()
        try:
            result, err = draft.execute("model.modifier", request)["result"], None
        except BadRequest as exc:
            result, err = None, exc
        return IM.analyze_model_modifier(p, draft, request, result, err)

    def setUp(self):
        self.p = Project.create("a", unit="mm")
        self.a = self.p.model.add_box(width=2, depth=2, height=2, name="a")
        self.b = self.p.model.add_box(width=2, depth=2, height=2, center=(1, 0, 0), name="b")
        self.c = self.p.model.add_box(width=1, depth=1, height=1, center=(-0.5, 0, 0), name="c")

    def test_boolean_analysis_has_before_after_diff_audit_and_candidate_collisions(self):
        req = {"id": self.a, "operation": "boolean", "operand": self.b, "parameters": {"operation": "subtract"}, "policy": "tolerance_controlled"}
        before_rev = self.p.revision
        out = self.preview(self.p, req)
        self.assertEqual(out["contract"], "spadra.preview.v1")
        self.assertTrue(out["valid"], out["diagnostics"])
        self.assertAlmostEqual(out["before"]["volume"], 8.0, places=9)
        self.assertAlmostEqual(out["after"]["volume"], 4.0, places=7)
        self.assertAlmostEqual(out["diff"]["volume"], -4.0, places=7)
        self.assertTrue(all(a["within"] for a in out["audit"]) and out["audit"])
        self.assertEqual(out["impact"]["modified"], [self.a])
        self.assertTrue(out["collisions"]["approximate"])
        self.assertIn(self.c, {c["id"] for c in out["collisions"]["overlaps"]})
        self.assertEqual(out["apply"]["expected_revision"], before_rev)
        self.assertEqual(self.p.revision, before_rev, "an analysis never changes the project")
        self.assertFalse(self.p.model.modifiers())

    def test_dependents_include_boolean_operand_users_and_spec_operations(self):
        self.p.model.add_modifier(self.a, kind="sgeo.boolean", operands=[self.b], parameters={"operation": "subtract"})
        deps = IM.dependents(self.p, self.b)
        self.assertTrue(any(d["id"] == self.a and "boolean operand" in d["relation"] for d in deps))
        spec = {"id": "s", "operations": [{"id": "x", "type": "box", "params": {"width": 1.0, "depth": 1.0, "height": 1.0}}], "expect": [], "outputs": ["x"]}
        self.p.execute("spec.define", {"spec": spec})
        self.p.execute("spec.run", {"id": "s"})
        oid = self.p.manifest["design_specs"]["s"]["state"]["ops"]["x"]["object"]
        self.assertTrue(any(d["id"] == "spec:s:x" for d in IM.dependents(self.p, oid)))

    def test_an_empty_result_is_an_error_with_the_matching_prediction(self):
        req = {"id": self.a, "operation": "boolean", "operand": self.b, "parameters": {"operation": "intersect"}, "policy": "tolerance_controlled"}
        far = self.p.model.add_box(width=1, depth=1, height=1, center=(20, 0, 0), name="far")
        req["operand"] = far
        out = self.preview(self.p, req)
        self.assertFalse(out["valid"])
        codes = {d["code"] for d in out["diagnostics"]}
        self.assertIn("kernel.refused", codes)
        self.assertIn("prediction.empty", codes)

    def test_failures_and_warnings_are_distinguished(self):
        req = {"id": self.a, "operation": "split", "parameters": {"origin": [0, 0, 0], "normal": [1, 0, 0], "side": "positive"}, "policy": "exact"}
        out = self.preview(self.p, req)
        self.assertTrue(out["valid"])
        self.assertTrue(all(d["severity"] != "error" for d in out["diagnostics"]))
        self.assertIn("mode.non_destructive", {d["code"] for d in out["diagnostics"]})

    def test_a_parametric_form_is_no_longer_rejected_as_a_bim_host(self):
        lvl = self.p.manifest["levels"][0]["id"]
        ex = self.p.execute("model.add_extrusion", {"level": lvl, "points": EV.L_SHAPE, "depth": 2.0})["result"]["id"]
        req = {"id": ex, "operation": "split", "parameters": {"origin": [1, 0, 0], "normal": [1, 0, 0], "side": "positive"}, "policy": "tolerance_controlled"}
        try:
            self.p.scratch().execute("model.modifier", req)
        except BadRequest as exc:
            self.assertNotIn("BIM host", str(exc))                       # either it ran, or the honest reason is the missing mesh backend
            self.assertFalse(BACKEND)
            self.assertIn("trimesh", str(exc))
        wall = self.p.execute("model.add_wall", {"level": lvl, "start": [0, 0], "end": [4000, 0]})["result"]["id"]
        with self.assertRaises(BadRequest) as cm:
            self.p.scratch().execute("model.modifier", {**req, "id": wall})
        self.assertIn("BIM host", str(cm.exception))                    # walls keep their dedicated editing commands

    def test_modifier_on_an_extrusion_survives_a_parameter_change(self):
        if not BACKEND:
            self.skipTest("a mesh split needs trimesh, which is not installed here (see the evaluation report for the 'unavailable' count)")
        lvl = self.p.manifest["levels"][0]["id"]
        ex = self.p.execute("model.add_extrusion", {"level": lvl, "points": EV.L_SHAPE, "depth": 2.0})["result"]["id"]
        self.p.execute("model.modifier", {"id": ex, "operation": "split", "parameters": {"origin": [1, 0, 0], "normal": [1, 0, 0], "side": "positive"}, "policy": "tolerance_controlled"})
        before = IM.element_state(self.p, ex)["volume"]
        self.p.execute("model.set_property", {"id": ex, "key": "depth", "value": 4.0})
        self.assertAlmostEqual(IM.element_state(self.p, ex)["volume"], before * 2, places=6)


class Evaluation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = EV.evaluate()
        cls.s = cls.report["summary"]

    def test_the_suite_has_at_least_one_hundred_modifier_operations(self):
        self.assertGreaterEqual(self.s["operations"], 100)

    def test_every_operation_the_kernel_could_run_matches_its_prediction(self):
        self.assertGreaterEqual(self.s["measured_by_kernel"], 60)
        self.assertEqual(self.s["predictions_outside"], 0, [r for r in self.report["rows"] if r["status"] == "measured" and not r["within"]][:3])

    def test_every_refusal_is_predicted_empty_or_a_named_kernel_limit(self):
        """A refusal is either a case the predictor called empty, or one of the kernel's own named failures on a case whose exact answer exists (reported separately)."""
        self.assertEqual(self.s["refusals_unexplained"], 0, [r for r in self.report["rows"] if r["status"] == "refused" and not r.get("refusal_predicted") and not r.get("kernel_limit")][:3])
        self.assertEqual(self.s["refused_by_kernel"], self.s["refusals_predicted_empty"] + self.s["refusals_kernel_limit"])

    def test_the_non_convex_mesh_prediction_agrees_with_an_independent_oracle(self):
        self.assertGreaterEqual(self.s["oracle_checked"], 40)
        self.assertEqual(self.s["oracle_agree"], self.s["oracle_checked"])

    def test_unavailable_operations_are_counted_not_dropped(self):
        total = self.s["measured_by_kernel"] + self.s["unavailable_here"] + self.s["refused_by_kernel"]
        self.assertEqual(total, self.s["operations"])
        if BACKEND:
            self.assertEqual(self.s["unavailable_here"], 0)


class PreviewRoutes(ServerCase):
    def test_predict_and_preview_return_the_shared_contract_and_change_nothing(self):
        a = self.api
        a.post("/api/project/new", {"name": "P", "unit": "mm"})
        project = self.app.require()
        box = project.model.add_box(width=2, depth=2, height=2, name="a")
        other = project.model.add_box(width=2, depth=2, height=2, center=(1, 0, 0), name="b")
        rev = project.revision
        body = {"kind": "model", "id": box, "operation": "boolean", "operand": other, "parameters": {"operation": "subtract"}, "policy": "tolerance_controlled"}
        pred = a.post("/api/modifier/predict", body)
        self.assertEqual(pred["predicted"]["exact"]["volume"], 4.0)
        self.assertFalse(pred["learned"]["available"])
        prev = a.post("/api/modifier/preview", body)
        an = prev["analysis"]
        self.assertEqual((an["contract"], an["kind"], an["valid"]), ("spadra.preview.v1", "model", True))
        self.assertAlmostEqual(an["after"]["volume"], 4.0, places=7)
        self.assertTrue(prev["before"]["faces"] and prev["after"]["faces"])
        self.assertEqual(project.revision, rev)

    def test_a_refused_preview_is_still_a_400_but_carries_the_analysis(self):
        a = self.api
        a.post("/api/project/new", {"name": "P", "unit": "mm"})
        project = self.app.require()
        box = project.model.add_box(width=2, depth=2, height=2, name="a")
        far = project.model.add_box(width=1, depth=1, height=1, center=(30, 0, 0), name="far")
        status, out = a.call("POST", "/api/modifier/preview", {"kind": "model", "id": box, "operation": "boolean", "operand": far, "parameters": {"operation": "intersect"}})
        self.assertEqual(status, 400)
        analysis = out["error"]["details"]["analysis"]
        self.assertFalse(analysis["valid"])
        self.assertIn("prediction.empty", {d["code"] for d in analysis["diagnostics"]})

    def test_2d_preview_uses_the_same_contract(self):
        a = self.api
        a.post("/api/project/new", {"name": "P", "unit": "mm"})
        project = self.app.require()
        view = next(v["id"] for v in project.manifest["views"] if v["kind"] == "plan")
        line = project.drawing(view).add_line([0, 0], [3000, 0])
        prev = a.post("/api/modifier/preview", {"kind": "drawing", "drawing": view, "id": line, "operation": "bend", "policy": "display_only",
                                               "parameters": {"axis": "x", "start": 0, "end": 2000, "angle": math.pi / 4, "segments": 16}})
        an = prev["analysis"]
        self.assertEqual((an["contract"], an["kind"]), ("spadra.preview.v1", "drawing"))
        self.assertGreater(an["after"]["vertices"], an["before"]["vertices"])
        self.assertFalse(an["predicted"]["available"])


if __name__ == "__main__":
    unittest.main()
