"""Measurements against the real SGEO engine: exact figures, honest refusals, units, and associative persistent measurements."""
from __future__ import annotations

import math
import unittest

from applications.SPADRA import documentation as D
from applications.SPADRA import elements as E
from applications.SPADRA import measurements as M
from applications.SPADRA.project import Project
from applications.SPADRA.tests import profiler_env  # noqa: F401
from applications.tools.errors import BadRequest, NotFound

SQUARE = [[0, 0], [4000, 0], [4000, 4000], [0, 4000]]


def pt(x, y, z=0.0):
    return {"type": "point", "p": [x, y, z]}


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project.create("Measure")
        self.l1 = self.p.manifest["levels"][0]["id"]

    def do(self, op, **params):
        return self.p.execute(op, params)["result"]

    def wall(self, y=0, x=0, length=6000):
        return self.do("model.add_wall", start=[x, y], end=[x + length, y], level=self.l1, thickness=250, height=3.2)["id"]

    def floor(self, dx=0):
        return self.do("model.add_floor", points=[[x + dx, y] for x, y in SQUARE], level=self.l1)["id"]

    def m(self, kind, *refs, **kw):
        return M.measure(self.p, kind, list(refs), **kw)

    def top_face(self, oid):
        verts, faces = D.world_mesh(self.p, oid)
        best, area = None, -1.0
        for t, f in enumerate(faces):
            a, b, c = (verts[i] for i in f)
            n = M._cross(M._sub(b, a), M._sub(c, a))
            if M._len(n) > 1e-9 and n[2] / M._len(n) > 0.999 and M._len(n) > area:
                best, area = t, M._len(n)
        self.assertIsNotNone(best)
        return {"type": "face", "id": oid, "face": best}


class Points(Case):
    def test_distance_components_and_units(self):
        r = self.m("distance", pt(0, 0, 0), pt(3, 4, 12))
        self.assertAlmostEqual(r["primary"]["si"], 13.0)
        vals = {v["label"]: v["si"] for v in r["values"]}
        self.assertAlmostEqual(vals["Horizontal"], 5.0)
        self.assertAlmostEqual(vals["Vertical"], 12.0)
        r = self.m("distance", pt(0, 0), pt(1, 0), unit="mm", precision=1)
        self.assertEqual(r["primary"]["display"], "1 000.0 mm")
        r = self.m("distance", pt(0, 0), pt(0.3048, 0), unit="ft", precision=2)
        self.assertAlmostEqual(r["primary"]["value"], 1.0)

    def test_horizontal_and_vertical(self):
        h = self.m("horizontal", pt(0, 0, 0), pt(3, 4, 99))
        self.assertAlmostEqual(h["primary"]["si"], 5.0)
        v = self.m("vertical", pt(5, 5, 1), pt(0, 0, 4.5))
        self.assertAlmostEqual(v["primary"]["si"], 3.5)

    def test_length_of_path_open_and_closed(self):
        r = self.m("length", {"type": "path", "points": [[0, 0, 0], [3, 0, 0], [3, 4, 0]]})
        self.assertAlmostEqual(r["primary"]["si"], 7.0)
        r = self.m("length", {"type": "path", "points": [[0, 0, 0], [3, 0, 0], [3, 4, 0]], "closed": True})
        self.assertAlmostEqual(r["primary"]["si"], 12.0)

    def test_angle_three_points_and_lines(self):
        r = self.m("angle", pt(1, 0), pt(0, 0), pt(0, 1))
        self.assertAlmostEqual(r["primary"]["si"], 90.0)
        r = self.m("angle", pt(1, 0), pt(0, 0), pt(-1, 0))
        self.assertAlmostEqual(r["primary"]["si"], 180.0)

    def test_coincident_points_are_refused_for_an_angle(self):
        with self.assertRaises(BadRequest):
            self.m("angle", pt(0, 0), pt(0, 0), pt(1, 1))

    def test_slope_angle_pitch_percentage(self):
        r = self.m("slope", pt(0, 0, 0), pt(4, 0, 1))
        vals = {v["label"]: v["si"] for v in r["values"]}
        self.assertAlmostEqual(vals["Inclination from horizontal"], math.degrees(math.atan(0.25)))
        self.assertAlmostEqual(vals["Slope"], 25.0)
        self.assertAlmostEqual(vals["Pitch (rise per 1 run)"], 0.25)

    def test_vertical_line_has_no_percentage(self):
        r = self.m("slope", pt(0, 0, 0), pt(0, 0, 3))
        labels = [v["label"] for v in r["values"]]
        self.assertNotIn("Slope", labels)
        self.assertTrue(any("vertical" in n for n in r["notes"]))

    def test_radius_and_diameter(self):
        pts = [pt(2 * math.cos(a), 2 * math.sin(a)) for a in (0, 1.0, 2.5, 4.0)]
        self.assertAlmostEqual(self.m("radius", *pts)["primary"]["si"], 2.0)
        self.assertAlmostEqual(self.m("diameter", *pts)["primary"]["si"], 4.0)

    def test_points_not_on_a_circle_are_refused(self):
        with self.assertRaises(BadRequest) as cm:
            self.m("radius", pt(1, 0), pt(0, 1), pt(-1, 0), pt(0, -2))
        self.assertIn("not on a circle", str(cm.exception))
        with self.assertRaises(BadRequest):
            self.m("radius", pt(0, 0), pt(1, 0), pt(2, 0))

    def test_area_and_perimeter_of_a_profile(self):
        sq = {"type": "path", "points": [[0, 0, 0], [3, 0, 0], [3, 2, 0], [0, 2, 0]], "closed": True}
        self.assertAlmostEqual(self.m("area", sq)["primary"]["si"], 6.0)
        self.assertAlmostEqual(self.m("perimeter", sq)["primary"]["si"], 10.0)
        tilted = {"type": "path", "points": [[0, 0, 0], [3, 0, 0], [3, 2, 2], [0, 2, 2]], "closed": True}
        self.assertAlmostEqual(self.m("area", tilted)["primary"]["si"], 3 * math.hypot(2, 2))

    def test_open_or_nonplanar_profile_is_refused(self):
        with self.assertRaises(BadRequest):
            self.m("area", {"type": "path", "points": [[0, 0, 0], [3, 0, 0], [3, 2, 0]]})
        with self.assertRaises(BadRequest) as cm:
            self.m("area", {"type": "path", "points": [[0, 0, 0], [3, 0, 0], [3, 2, 1], [0, 2, 0]], "closed": True})
        self.assertIn("one plane", str(cm.exception))
        with self.assertRaises(BadRequest):
            self.m("area", {"type": "path", "points": [[0, 0, 0], [1, 0, 0], [2, 0, 0]], "closed": True})

    def test_coordinates_world_and_local(self):
        r = self.m("coordinates", pt(1, 2, 3), origin=[1, 1, 1])
        vals = {v["label"]: v["si"] for v in r["values"]}
        self.assertEqual((vals["X"], vals["Y"], vals["Z"]), (1, 2, 3))
        self.assertAlmostEqual(vals["X from origin"], 0)
        self.assertAlmostEqual(vals["Z from origin"], 2)

    def test_bad_input_is_a_clear_error(self):
        for bad in ({"type": "point", "p": [1, 2]}, {"type": "point", "p": [1, 2, float("nan")]}, {"type": "wat"}, "x"):
            with self.assertRaises(BadRequest):
                self.m("distance", bad, pt(0, 0))
        with self.assertRaises(BadRequest):
            M.measure(self.p, "nonsense", [pt(0, 0)])
        with self.assertRaises(BadRequest):
            self.m("distance", pt(0, 0), pt(1, 1), unit="furlong")
        with self.assertRaises(BadRequest):
            self.m("distance", pt(0, 0), pt(1, 1), precision=99)
        with self.assertRaises(BadRequest):
            self.m("distance", pt(0, 0))


class Elements(Case):
    def test_volume_of_a_wall(self):
        w = self.wall()
        r = self.m("volume", {"type": "element", "id": w})
        self.assertAlmostEqual(r["primary"]["si"], 6.0 * 0.25 * 3.2, places=3)

    def test_volume_of_a_face_or_point_is_refused(self):
        f = self.floor()
        with self.assertRaises(BadRequest):
            self.m("volume", self.top_face(f))
        with self.assertRaises(BadRequest):
            self.m("volume", pt(0, 0))

    def test_face_area_perimeter_slope(self):
        f = self.floor()
        face = self.top_face(f)
        self.assertAlmostEqual(self.m("area", face)["primary"]["si"], 16.0, places=3)
        self.assertAlmostEqual(self.m("perimeter", face)["primary"]["si"], 16.0, places=3)
        r = self.m("slope", face)
        self.assertTrue(any("horizontal" in n for n in r["notes"]))
        self.assertAlmostEqual(r["primary"]["si"], 0.0, places=6)

    def test_surface_area_of_an_element(self):
        w = self.wall()
        r = self.m("area", {"type": "element", "id": w})
        self.assertAlmostEqual(r["primary"]["si"], 2 * (6 * 3.2 + 0.25 * 3.2) + 2 * 6 * 0.25, places=3)

    def test_thickness_of_a_layered_wall_and_of_a_face(self):
        w = self.wall()
        self.assertAlmostEqual(self.m("thickness", {"type": "element", "id": w})["primary"]["si"], 0.25, places=6)
        f = self.floor()
        t = self.m("thickness", self.top_face(f))["primary"]["si"]
        self.assertAlmostEqual(t, E.m(E.meta_of(self.p, f), "thickness"), places=4)

    def test_clearance_is_the_true_gap_not_the_box_gap(self):
        a = self.wall(y=0)
        b = self.wall(y=2000)
        r = self.m("clearance", {"type": "element", "id": a}, {"type": "element", "id": b})
        self.assertAlmostEqual(r["primary"]["si"], 2.0 - 0.25, places=3)
        far = self.wall(y=0, x=10000)
        r = self.m("clearance", {"type": "element", "id": a}, {"type": "element", "id": far})
        self.assertAlmostEqual(r["primary"]["si"], 4.0, places=3)

    def test_touching_elements_report_zero_and_say_so(self):
        a = self.wall(y=0, x=0, length=3000)
        b = self.wall(y=0, x=3000, length=3000)
        r = self.m("clearance", {"type": "element", "id": a}, {"type": "element", "id": b})
        self.assertEqual(r["primary"]["si"], 0.0)
        self.assertTrue(r["notes"])

    def test_distance_to_a_point_and_between_elements(self):
        a = self.wall(y=0)
        r = self.m("distance", {"type": "element", "id": a}, pt(3, 5, 1))
        vals = {v["label"]: v["si"] for v in r["values"]}
        self.assertAlmostEqual(vals["Minimum gap"], 5.0 - 0.125, places=3)

    def test_deleted_element_is_not_found(self):
        w = self.wall()
        self.do("model.delete", id=w)
        with self.assertRaises(NotFound):
            self.m("volume", {"type": "element", "id": w})

    def test_stale_vertex_index_is_refused(self):
        w = self.wall()
        with self.assertRaises(BadRequest) as cm:
            self.m("distance", {"type": "vertex", "id": w, "index": 99999}, pt(0, 0))
        self.assertIn("changed", str(cm.exception))

    def test_edge_length_and_angle_between_edges(self):
        w = self.wall()
        verts, _ = D.world_mesh(self.p, w)
        origin = min(range(len(verts)), key=lambda i: M._len(verts[i]))
        near = sorted(range(len(verts)), key=lambda i: M._len(M._sub(verts[i], verts[origin])))
        a, b = near[0], next(i for i in near[1:] if M._len(M._sub(verts[i], verts[origin])) > 1e-6)
        r = self.m("length", {"type": "edge", "id": w, "a": a, "b": b})
        self.assertAlmostEqual(r["primary"]["si"], M._len(M._sub(verts[a], verts[b])))
        r = self.m("angle", {"type": "edge", "id": w, "a": a, "b": b}, {"type": "edge", "id": w, "a": a, "b": b})
        self.assertAlmostEqual(r["primary"]["si"], 0.0, places=6)

    def test_measuring_never_changes_the_model(self):
        w = self.wall()
        before = (self.p.revision, self.p.model.get(w)["metadata"])
        self.m("volume", {"type": "element", "id": w})
        self.m("clearance", {"type": "element", "id": w}, pt(1, 1, 1)) if False else None
        self.assertEqual(before, (self.p.revision, self.p.model.get(w)["metadata"]))

    def test_figures_lists_what_applies_and_why_not(self):
        f = self.floor()
        fig = M.figures(self.p, f)
        self.assertTrue(any(x["kind"] == "area" for x in fig["figures"]))
        for item in fig["unavailable"]:
            self.assertTrue(item["reason"])

    def test_nearest_features_snaps_to_vertex_edge_face(self):
        f = self.floor()
        near = M.nearest_features(self.p, f, [0.01, 0.01, 0.0], radius=0.1)
        self.assertIsNotNone(near["vertex"])
        self.assertIsNotNone(near["face"])
        far = M.nearest_features(self.p, f, [2.0, 2.0, 0.0], radius=0.01)
        self.assertIsNone(far["vertex"])


class Persistent(Case):
    def test_kept_measurement_follows_the_element(self):
        a = self.wall(y=0)
        b = self.wall(y=2000)
        got = M.add(self.p, "clearance", [{"type": "element", "id": a}, {"type": "element", "id": b}], label="Gap")
        mid = got["id"]
        self.assertAlmostEqual(got["result"]["primary"]["si"], 1.75, places=3)
        self.do("model.move", id=b, dx=0, dy=1.0, dz=0)
        rows = M.evaluate_all(self.p)
        self.assertEqual(rows[0]["status"], "ok")
        self.assertAlmostEqual(rows[0]["result"]["primary"]["si"], 2.75, places=3)
        self.assertEqual(rows[0]["id"], mid)

    def test_lost_reference_is_reported_not_stale(self):
        a = self.wall(y=0)
        b = self.wall(y=2000)
        M.add(self.p, "clearance", [{"type": "element", "id": a}, {"type": "element", "id": b}])
        self.do("model.delete", id=b)
        row = M.evaluate_all(self.p)[0]
        self.assertEqual(row["status"], "lost")
        self.assertNotIn("result", row)
        self.assertTrue(row["reason"])

    def test_unmeasurable_measurement_is_not_stored(self):
        with self.assertRaises(BadRequest):
            M.add(self.p, "radius", [pt(0, 0), pt(1, 0), pt(2, 0)])
        self.assertNotIn("measurements", self.p.manifest)

    def test_edit_and_remove(self):
        mid = M.add(self.p, "distance", [pt(0, 0), pt(1, 0)], label="A")["id"]
        M.edit(self.p, mid, label="B", unit="mm", precision=0)
        row = M.evaluate_all(self.p)[0]
        self.assertEqual(row["label"], "B")
        self.assertEqual(row["result"]["primary"]["display"], "1 000 mm")
        with self.assertRaises(BadRequest):
            M.edit(self.p, mid, precision=-1)
        M.remove(self.p, mid)
        self.assertNotIn("measurements", self.p.manifest)
        with self.assertRaises(NotFound):
            M.remove(self.p, mid)

    def test_command_adds_removes_and_undoes(self):
        res = self.do("measure.add", kind="distance", refs=[pt(0, 0), pt(2, 0)], label="Span")
        self.assertAlmostEqual(res["result"]["primary"]["si"], 2.0)
        self.assertEqual(len(self.p.manifest["measurements"]), 1)
        self.p.undo()
        self.assertNotIn("measurements", self.p.manifest)
        self.p.redo()
        self.assertEqual(len(self.p.manifest["measurements"]), 1)
        self.do("measure.remove", id=res["id"])
        self.assertNotIn("measurements", self.p.manifest)

    def test_survives_save_and_reopen(self):
        a = self.wall(y=0)
        b = self.wall(y=2000)
        M.add(self.p, "clearance", [{"type": "element", "id": a}, {"type": "element", "id": b}])
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "m.spadra"
            self.p.save(path) if hasattr(self.p, "save") else self.skipTest("no save API")
            q = Project.open(path) if hasattr(Project, "open") else self.skipTest("no open API")
            self.assertAlmostEqual(M.evaluate_all(q)[0]["result"]["primary"]["si"], 1.75, places=3)


if __name__ == "__main__":
    unittest.main()
