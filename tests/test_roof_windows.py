"""Roof windows: placed on the slope of a gable or shed roof, opened through every layer, and following the roof."""
from __future__ import annotations

import math
import tempfile
from pathlib import Path

from applications.SPADRA import elements as E
from applications.tools.errors import BadRequest

from .test_bim import Case

NS = "spadra"
RECT = [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]
T, PITCH = 0.2, 40


class RoofWindows(Case):
    def setUp(self) -> None:
        super().setUp()
        self.roof = self.run_("model.add_roof", points=RECT, level=self.roof_level, roof="gable", pitch=PITCH, thickness=T * 1000)["id"]
        self.solid = self.vol(self.roof)

    def window(self, **extra):
        return self.run_("model.add_roof_window", roof=self.roof, edge=0, **extra)["id"]

    def opening_area(self, w, h):                                                       # plan area of an opening for a w x h frame of the default depth
        a = math.radians(PITCH)
        return (w + 0.004) * (h * math.cos(a) + 0.12 * math.sin(a) + 0.004)

    # ---------------------------------------------------------------- the opening
    def test_the_roof_is_opened_through_its_thickness_by_exactly_the_opening(self):
        self.window()
        self.assertAlmostEqual(self.solid - self.vol(self.roof), self.opening_area(0.78, 0.98) * T, places=6)

    def test_every_layer_is_opened(self):
        self.run_("model.add_layer", id=self.roof, material="Mineral wool", thickness=100)
        full = self.vol(self.roof)
        self.window()
        self.assertAlmostEqual(full - self.vol(self.roof), self.opening_area(0.78, 0.98) * 0.3, places=6)      # the 100 mm layer and the 200 mm roof

    def test_the_opening_is_in_the_plan_outline_the_window_reports(self):
        w = self.window(x=2000, d=1500)
        plan = E.m(self.meta(w), "plan")
        xs, ys = [q[0] for q in plan], [q[1] for q in plan]
        self.assertAlmostEqual(max(xs) - min(xs), 0.784, places=6)
        self.assertAlmostEqual((min(xs) + max(xs)) / 2, 2.0, places=6)
        self.assertAlmostEqual((min(ys) + max(ys)) / 2, 1.5 * math.cos(math.radians(PITCH)), places=6)       # 1.5 m up the slope, in plan

    def test_the_window_lies_in_the_plane_of_the_roof(self):
        w = self.window(d=1500)
        lo, hi = self.p.model.bounds(w)
        a = math.radians(PITCH)
        top = float(self.p.level(self.roof_level)["elevation"])
        # centre of the frame's top surface is on the roof's top surface 1.5 m up the slope
        self.assertAlmostEqual(hi[2] - lo[2], 0.98 * math.sin(a) + 0.12 * math.cos(a), places=6)
        self.assertGreater(lo[2], top)                                                  # above the eave line

    def test_the_kerb_raises_the_window_off_the_roof_surface(self):
        w = self.window(d=1500)
        z0 = self.p.model.bounds(w)[1][2]
        self.run_("model.set_property", id=w, key="kerb", value=100)
        self.assertGreater(self.p.model.bounds(w)[1][2], z0 + 0.07)

    # ---------------------------------------------------------------- it follows the roof
    def test_the_window_and_its_opening_follow_the_roof(self):
        w = self.window(d=1500)
        z = self.p.model.bounds(w)[1][2]
        self.run_("model.set_property", id=self.roof, key="pitch", value=30)
        self.assertLess(self.p.model.bounds(w)[1][2], z)                                # a flatter roof is lower at the same distance up the slope
        self.assertAlmostEqual(self.solid - self.vol(self.roof), (0.784 * (0.98 * math.cos(math.radians(30)) + 0.12 * math.sin(math.radians(30)) + 0.004)) * T, places=6)
        self.run_("model.set_property", id=self.roof, key="offset", value=500)
        self.assertGreater(self.p.model.bounds(w)[1][2], z - 0.5)                      # moved with the roof's level offset
        self.run_("model.set_property", id=self.roof, key="thickness", value=300)
        self.assertAlmostEqual(self.solid * 1.5 - self.vol(self.roof), self.opening_area(0.78, 0.98) * 0 + (0.784 * (0.98 * math.cos(math.radians(30)) + 0.12 * math.sin(math.radians(30)) + 0.004)) * 0.3, places=6)

    def test_an_end_connection_that_would_cut_into_the_window_is_refused_and_a_clear_one_is_followed(self):
        w = self.window(d=700)                                                          # close to the eave
        with self.assertRaises(BadRequest):
            self.run_("model.set_roof_end", id=self.roof, edge=0, type="B1", offset=-900)          # the slope would end 900 mm up: through the window
        self.assertIsNone(E.roof_end_config(self.meta(self.roof), 0))
        self.run_("model.set_roof_end", id=self.roof, edge=0, type="B3", offset=-20)
        self.assertGreater(self.vol(self.roof), 0)
        self.assertEqual(E.check(self.p), [i for i in E.check(self.p) if i["severity"] != "error"])

    def test_the_other_slope_takes_its_own_windows(self):
        a, b = self.window(), self.run_("model.add_roof_window", roof=self.roof, edge=2, x=3000)["id"]
        self.assertAlmostEqual(self.solid - self.vol(self.roof), 2 * self.opening_area(0.78, 0.98) * T, places=6)
        self.assertLess(self.p.model.bounds(a)[1][1], 2.1)
        self.assertGreater(self.p.model.bounds(b)[0][1], 1.9)

    # ---------------------------------------------------------------- limits
    def test_a_window_that_does_not_fit_is_refused_and_nothing_changes(self):
        for extra in ({"d": 100}, {"d": 2600}, {"x": 100}, {"x": 5950}, {"height": 3500}):
            with self.subTest(extra), self.assertRaises(BadRequest):
                self.window(**extra)
            self.assertAlmostEqual(self.vol(self.roof), self.solid, places=9)
        self.assertEqual(len(E.roof_windows(self.p, self.roof)), 0)

    def test_two_windows_may_not_overlap(self):
        self.window(x=3000)
        with self.assertRaises(BadRequest):
            self.window(x=3500)
        self.window(x=4500)

    def test_a_roof_edit_that_would_leave_a_window_off_the_slope_is_refused(self):
        self.window(d=1500)
        with self.assertRaises(BadRequest):
            self.run_("model.set_property", id=self.roof, key="ridge_offset", value=-1500)             # the ridge would cross the window
        self.assertAlmostEqual(self.meta(self.roof)[f"{NS}:ridge_offset"], 0.0)

    def test_without_a_place_it_goes_to_the_middle_or_the_nearest_free_place(self):
        a, b = self.window(), self.window()
        self.assertAlmostEqual(self.meta(a)[f"{NS}:x"], 3.0)
        self.assertAlmostEqual(self.meta(b)[f"{NS}:x"], 3.0 + 0.78 + 0.3)
        self.assertAlmostEqual(self.solid - self.vol(self.roof), 2 * self.opening_area(0.78, 0.98) * T, places=6)

    def test_flat_and_hip_roofs_are_refused(self):
        flat = self.run_("model.add_roof", points=RECT, level=self.roof_level, roof="flat")["id"]
        with self.assertRaises(BadRequest):
            self.run_("model.add_roof_window", roof=flat, edge=0)

    # ---------------------------------------------------------------- editing
    def test_move_and_properties_edit_the_position_and_size(self):
        w = self.window(x=3000, d=1500)
        self.run_("model.move", id=w, dx=0.5, dy=0, dz=0)
        self.assertAlmostEqual(self.meta(w)[f"{NS}:x"], 3.5)
        with self.assertRaises(BadRequest):
            self.run_("model.move", id=w, dx=0, dy=0, dz=0.3)
        self.run_("model.set_property", id=w, key="width", value=1140)
        self.assertAlmostEqual(self.solid - self.vol(self.roof), self.opening_area(1.14, 0.98) * T, places=6)
        rows = self.rows(w)
        for key in ("width", "height", "x", "d", "kerb", "depth"):
            self.assertIn(key, rows)
        self.assertEqual(rows["d"]["value"], 1500)

    def test_deleting_the_window_closes_the_roof_and_deleting_the_roof_takes_the_window(self):
        w = self.window()
        self.run_("model.delete", id=w)
        self.assertAlmostEqual(self.vol(self.roof), self.solid, places=9)
        w = self.window()
        self.run_("model.delete", id=self.roof)
        self.assertFalse(E.alive(self.p, w))

    # ---------------------------------------------------------------- views, persistence
    def test_the_plan_of_the_roof_level_shows_the_window(self):
        w = self.window(x=2000, d=1500)
        plan = next(v for v in self.p.manifest["views"] if v.get("level_id") == self.roof_level and v["kind"] == "plan")
        item = next(i for i in self.p.plan_overlay(plan["id"]) if i["id"] == w)
        self.assertEqual(item["category"], "roof_window")
        self.assertEqual(len(item["polygon"]), 4)

    def test_schedules_exports_and_the_project_browser_cope_with_roof_windows(self):
        self.window()
        self.assertIn("Roof windows", str(self.p.tree()))                                    # the Project Browser lists them under their own category
        self.assertEqual([i for i in E.check(self.p) if i["severity"] == "error"], [])

    def test_undo_redo_and_save_reload(self):
        w = self.window(d=1500)
        removed = self.solid - self.vol(self.roof)
        self.p.undo()
        self.assertAlmostEqual(self.vol(self.roof), self.solid, places=9)
        self.p.redo()
        with tempfile.TemporaryDirectory() as tmp:
            self.p.save(Path(tmp) / "w.spadra")
            self.p = type(self.p).open(Path(tmp) / "w.spadra")
        self.assertAlmostEqual(self.solid - self.vol(self.roof), removed, places=9)
        self.run_("model.set_property", id=self.roof, key="pitch", value=35)                  # still follows after reload
        self.assertLess(self.vol(self.roof), self.solid)
        self.assertTrue(E.alive(self.p, w))
