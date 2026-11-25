"""View range on a real project: what a floor plan draws as items (selectable), what it draws as halftone context (reference only), what the view
depth hides, how a range change is validated and undone, and that it survives saving."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project.create("Range")
        self.l1, self.l2, self.l3 = (l["id"] for l in self.p.manifest["levels"])
        self.plan1 = self.plan(self.l1)
        self.plan2 = self.plan(self.l2)

    def plan(self, level):
        return next(v["id"] for v in self.p.manifest["views"] if v.get("level_id") == level)

    def run_(self, op, **params):
        return self.p.execute(op, params)["result"]

    def wall(self, level, a=(0, 0), b=(6000, 0), height=3.2):
        return self.run_("model.add_wall", start=list(a), end=list(b), level=level, thickness=250, height=height)["id"]

    def data(self, plan):
        return self.p.drawing_data(plan)

    def ids(self, plan, layer):
        return [i["id"] for i in self.data(plan)[layer]]


class ContextVersusItems(Case):
    def test_a_lower_levels_wall_is_halftone_context_and_never_a_model_item(self):
        wall = self.wall(self.l1)
        self.assertNotIn(wall, self.ids(self.plan2, "model"))
        context = {c["id"]: c for c in self.data(self.plan2)["context"]}
        self.assertIn(wall, context)
        entry = context[wall]
        self.assertTrue(entry["rings"] or entry["tris"], "the wall must have a drawable footprint")
        self.assertEqual(entry["category"], "wall")
        self.assertEqual(entry["level"], self.l1)

    def test_the_plans_own_wall_is_an_item_and_not_context(self):
        wall = self.wall(self.l1)
        self.assertIn(wall, self.ids(self.plan1, "model"))
        self.assertNotIn(wall, self.ids(self.plan1, "context"))

    def test_view_depth_hides_a_lower_level_that_is_deeper_than_the_depth(self):
        wall = self.wall(self.l1)                                     # 0 .. 3.2 m; Level 02 is at 3.2 m. Values are in project units (mm).
        self.assertIn(wall, self.ids(self.plan2, "context"))          # the default depth (1 m below the level) reaches the wall's top part
        self.run_("view.set_cut_plane", id=self.plan2, key="range_bottom", value=500)
        with self.assertRaises(BadRequest):
            self.run_("view.set_cut_plane", id=self.plan2, key="range_depth", value=600)   # the depth cannot be above the bottom
        self.run_("view.set_cut_plane", id=self.plan2, key="range_depth", value=500)       # depth 0.5 m above the level: below it is hidden
        self.assertNotIn(wall, self.ids(self.plan2, "context"))
        self.assertNotIn(wall, self.ids(self.plan2, "model"))
        self.p.undo()
        self.assertIn(wall, self.ids(self.plan2, "context"))

    def test_a_tall_wall_that_crosses_the_cut_is_cut_not_context(self):
        tall = self.wall(self.l1, height=6.0)                         # 0 .. 6 m crosses Level 02's cut at 4.7 m
        shown = {i["id"]: i for i in self.data(self.plan2)["model"]}
        self.assertIn(tall, shown)
        self.assertIn("hatch", shown[tall])
        self.assertTrue(shown[tall].get("cut", True))
        self.assertNotIn(tall, self.ids(self.plan2, "context"))

    def test_a_low_wall_on_the_plans_level_is_a_projected_item(self):
        low = self.wall(self.l1, height=1.0)                          # 0 .. 1 m, below the 1.5 m cut
        shown = {i["id"]: i for i in self.data(self.plan1)["model"]}
        self.assertIn(low, shown)
        self.assertFalse(shown[low]["cut"])

    def test_moving_a_level_updates_the_plan_without_stored_state(self):
        wall = self.wall(self.l1)
        self.assertIn(wall, self.ids(self.plan2, "context"))
        self.run_("level.set", id=self.l2, elevation=20.0)
        self.assertNotIn(wall, self.ids(self.plan2, "context"))


class Payload(Case):
    def test_view_range_payload_is_in_metres_and_consistent(self):
        rng = self.data(self.plan2)["view_range"]
        self.assertAlmostEqual(rng["cut_z"] - rng["level_z"], rng["height"])
        self.assertAlmostEqual(rng["level_z"] - rng["depth_z"], rng["depth"])
        self.assertTrue(rng["valid"])
        self.assertLessEqual(rng["depth_z"], rng["bottom_z"])
        self.assertLessEqual(rng["bottom_z"], rng["cut_z"])
        self.assertLessEqual(rng["cut_z"], rng["top_z"])

    def test_a_non_plan_view_has_no_view_range(self):
        three_d = next(v["id"] for v in self.p.manifest["views"] if v["kind"] == "3d")
        self.assertIsNone(self.p.view_range(three_d))


class Editing(Case):
    def test_an_invalid_change_is_refused_with_a_sentence_and_changes_nothing(self):
        before = dict(self.p.view(self.plan1))
        with self.assertRaises(BadRequest) as caught:
            self.run_("view.set_cut_plane", id=self.plan1, key="range_depth", value=5000)
        self.assertTrue(str(caught.exception))
        for key in ("cut_offset", "range_top", "range_bottom", "range_depth"):
            self.assertEqual(self.p.view(self.plan1).get(key), before.get(key))

    def test_non_numbers_and_non_finite_values_are_refused(self):
        for bad in ("deep", True, float("inf"), float("nan")):
            with self.assertRaises(BadRequest):
                self.run_("view.set_cut_plane", id=self.plan1, key="range_depth", value=bad)

    def test_a_change_is_one_undoable_step(self):
        before = self.p.view(self.plan1).get("range_depth")
        self.run_("view.set_cut_plane", id=self.plan1, key="range_depth", value=-2000)
        self.assertEqual(self.p.view(self.plan1)["range_depth"], -2.0)
        self.p.undo()
        self.assertEqual(self.p.view(self.plan1).get("range_depth"), before)

    def test_each_plan_keeps_its_own_range(self):
        self.run_("view.set_cut_plane", id=self.plan1, key="range_depth", value=-2000)
        self.assertNotEqual(self.p.view(self.plan2).get("range_depth"), -2.0)

    def test_the_range_survives_saving_and_reopening(self):
        self.run_("view.set_cut_plane", id=self.plan1, key="range_depth", value=-2000)
        self.run_("view.set_cut_plane", id=self.plan1, key="cut_offset", value=1200)
        with tempfile.TemporaryDirectory() as tmp:
            self.p.save(Path(tmp) / "x.spadra")
            q = Project.open(Path(tmp) / "x.spadra")
            rng = q.view_range(self.plan1)
            self.assertAlmostEqual(rng["depth"], 2.0)
            self.assertAlmostEqual(rng["height"], 1.2)

    def test_an_old_project_without_stored_range_numbers_gets_the_defaults(self):
        for key in ("cut_offset", "range_top", "range_bottom", "range_depth"):
            self.p.view(self.plan1).pop(key, None)
        rng = self.p.view_range(self.plan1)
        self.assertTrue(rng["valid"])
        self.assertAlmostEqual(rng["height"], 1.5)
        self.assertAlmostEqual(rng["depth"], 1.0)


class SnapToContext(Case):
    def test_snapping_to_lower_levels_is_off_by_default_and_a_view_option(self):
        self.assertFalse(self.p.view_range(self.plan2)["snap_context"])
        self.run_("view.set_option", id=self.plan2, key="snap_context", value="shown")
        self.assertTrue(self.p.view_range(self.plan2)["snap_context"])
        self.assertFalse(self.p.view_range(self.plan1)["snap_context"])


if __name__ == "__main__":
    unittest.main()
