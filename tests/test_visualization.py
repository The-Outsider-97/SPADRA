"""3D visualization settings: the shared defaults and validation (visualization.py) and how a project stores them per 3D view."""
from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

from applications.SPADRA import visualization as VZ
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest


CASES = json.loads((Path(__file__).parent / "visual_cases.json").read_text(encoding="utf-8"))   # the table tests_ts/visualization.test.ts reads too


class SharedTable(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(VZ.normalise(None), CASES["defaults"])

    def test_normalise_cases(self):
        for case in CASES["normalise"]:
            with self.subTest(case["name"]):
                self.assertEqual(VZ.normalise(case["raw"]), case["expect"])

    def test_refused_cases(self):
        for case in CASES["refused"]:
            with self.subTest(case["name"]):
                with self.assertRaises(VZ.VisualError):
                    VZ.updated(None, case["patch"])

    def test_a_refused_change_leaves_the_setting_as_it_was(self):
        base = VZ.updated(None, {"mode": "lined", "edge_boost": 150})
        for case in CASES["refused"]:
            with self.subTest(case["name"]):
                with self.assertRaises(VZ.VisualError):
                    VZ.updated(base, case["patch"])
                self.assertEqual(base["edge_boost"], 150.0)

    def test_shadows_on(self):
        for case in CASES["shadows_on"]:
            with self.subTest(str(case["visual"])):
                visual = VZ.normalise(case["visual"])
                self.assertEqual(VZ.shadows_on(visual), case["expect"])

    def test_sun_directions(self):
        for case in CASES["sun"]:
            with self.subTest(str(case)):
                for got, want in zip(VZ.sun_direction(case["azimuth"], case["elevation"]), case["expect"]):
                    self.assertAlmostEqual(got, want, places=9)


class Module(unittest.TestCase):
    def test_defaults_are_realistic_with_a_sun_and_no_optional_shadows(self):
        v = VZ.normalise(None)
        self.assertEqual(v["mode"], "realistic")
        self.assertEqual(v["sun"], {"azimuth": 225.0, "elevation": 45.0})
        self.assertTrue(VZ.shadows_on(v))                             # realistic shadows are automatic

    def test_garbage_falls_back_field_by_field(self):
        v = VZ.normalise({"mode": "toon", "edge_boost": "x", "sun": {"azimuth": 90, "elevation": 999}})
        self.assertEqual(v["mode"], "realistic")
        self.assertEqual(v["edge_boost"], 100.0)
        self.assertEqual(v["sun"]["azimuth"], 90.0)
        self.assertEqual(v["sun"]["elevation"], 45.0)

    def test_edge_factor_maps_the_percentage_to_a_thickness_multiplier(self):
        self.assertEqual(VZ.edge_factor(50), 1.5)
        self.assertEqual(VZ.edge_factor(100), 2.0)
        self.assertEqual(VZ.edge_factor(200), 3.0)

    def test_updates_are_strict(self):
        base = VZ.normalise(None)
        self.assertEqual(VZ.updated(base, {"mode": "lined"})["mode"], "lined")
        for bad in ({"mode": "toon"}, {"edge_boost": 20}, {"edge_boost": 250}, {"sun": {"elevation": 0}},
                    {"sun": {"elevation": 91}}, {"nope": 1}, {"shadows": {"wireframe": True}}, {"shadows": {"realistic": False}}):
            with self.assertRaises(VZ.VisualError, msg=str(bad)):
                VZ.updated(base, bad)

    def test_shadows_are_kept_per_mode_when_switching(self):
        v = VZ.updated(VZ.normalise(None), {"mode": "colored", "shadows": {"colored": True}})
        v = VZ.updated(v, {"mode": "gray"})
        v = VZ.updated(v, {"mode": "colored"})
        self.assertTrue(VZ.shadows_on(v))
        self.assertFalse(VZ.shadows_on(VZ.updated(v, {"mode": "wireframe"})))

    def test_sun_direction_is_a_unit_vector_pointing_where_the_angles_say(self):
        x, y, z = VZ.sun_direction(0, 90)
        self.assertAlmostEqual(z, 1.0)
        x, y, z = VZ.sun_direction(90, 0.0001)
        self.assertAlmostEqual(x, 1.0, places=3)                      # east
        for az in range(0, 360, 45):
            for el in (1, 30, 90):
                self.assertAlmostEqual(math.sqrt(sum(c * c for c in VZ.sun_direction(az, el))), 1.0)


class InAProject(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project.create("Visual")
        self.view = next(v["id"] for v in self.p.manifest["views"] if v["kind"] == "3d")

    def run_(self, op, **params):
        return self.p.execute(op, params)["result"]

    def node(self, vid):
        def walk(nodes):
            for n in nodes:
                if n.get("id") == vid:
                    return n
                found = walk(n.get("children", []))
                if found:
                    return found
        return walk(self.p.tree()["sections"])

    def test_the_tree_carries_the_normalised_settings(self):
        self.assertEqual(self.node(self.view)["visual"]["mode"], "realistic")

    def test_set_visual_is_validated_and_undoable(self):
        self.run_("view.set_visual", id=self.view, patch={"mode": "lined", "edge_boost": 150})
        self.assertEqual(self.node(self.view)["visual"]["edge_boost"], 150.0)
        with self.assertRaises(BadRequest):
            self.run_("view.set_visual", id=self.view, patch={"mode": "toon"})
        self.assertEqual(self.node(self.view)["visual"]["mode"], "lined")
        with self.assertRaises(BadRequest):
            self.run_("view.set_visual", id=self.view, patch={})
        self.p.undo()
        self.assertEqual(self.node(self.view)["visual"]["mode"], "realistic")

    def test_only_3d_views_have_visual_settings(self):
        plan = next(v["id"] for v in self.p.manifest["views"] if v["kind"] == "plan")
        with self.assertRaises(BadRequest):
            self.run_("view.set_visual", id=plan, patch={"mode": "gray"})

    def test_each_3d_view_has_its_own_settings_and_a_new_one_starts_from_its_source(self):
        self.run_("view.set_visual", id=self.view, patch={"mode": "gray", "shadows": {"gray": True}})
        other = self.run_("view.add_3d")["id"]
        self.assertEqual(self.node(other)["visual"]["mode"], "gray")
        self.run_("view.set_visual", id=other, patch={"mode": "wireframe"})
        self.assertEqual(self.node(self.view)["visual"]["mode"], "gray")
        self.assertEqual(self.node(other)["visual"]["mode"], "wireframe")
        self.run_("view.set_visual", id=self.view, patch={"sun": {"azimuth": 10}})
        self.assertEqual(self.node(other)["visual"]["sun"]["azimuth"], 225.0)

    def test_a_new_3d_view_gets_a_unique_name_and_can_be_renamed_and_deleted(self):
        other = self.run_("view.add_3d")
        self.assertEqual(other["name"], "3D View 2")
        self.assertEqual(self.run_("view.add_3d")["name"], "3D View 3")
        self.run_("view.rename", id=other["id"], name="Street side")
        with self.assertRaises(Exception):
            self.run_("view.add_3d", name="street side")              # names are unique, ignoring case
        self.run_("view.delete", id=other["id"])
        self.assertNotIn(other["id"], [v["id"] for v in self.p.manifest["views"]])

    def test_the_only_3d_view_cannot_be_renamed_or_deleted(self):
        with self.assertRaises(Exception):
            self.run_("view.delete", id=self.view)

    def test_settings_survive_saving_and_reopening(self):
        self.run_("view.set_visual", id=self.view, patch={"mode": "lined", "edge_boost": 200, "shadows": {"lined": True}, "sun": {"azimuth": 100, "elevation": 20}})
        with tempfile.TemporaryDirectory() as tmp:
            self.p.save(Path(tmp) / "x.spadra")
            q = Project.open(Path(tmp) / "x.spadra")
            node = next(v for v in q.manifest["views"] if v["id"] == self.view)
        self.assertEqual(VZ.normalise(node["visual"])["edge_boost"], 200.0)
        self.assertEqual(VZ.normalise(node["visual"])["sun"], {"azimuth": 100.0, "elevation": 20.0})
        self.assertTrue(VZ.shadows_on(VZ.normalise(node["visual"])))

    def test_an_old_project_without_settings_opens_with_the_defaults(self):
        for v in self.p.manifest["views"]:
            v.pop("visual", None)
        self.assertEqual(self.node(self.view)["visual"], VZ.normalise(None))


if __name__ == "__main__":
    unittest.main()
