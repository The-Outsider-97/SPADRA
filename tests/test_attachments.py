"""Walls attached to a roof or extended to a level: their height is derived, follows what they follow, and goes back when freed."""
from __future__ import annotations

import math

from applications.SPADRA import attachments as AT
from applications.tools.errors import BadRequest

from .test_bim import Case

RECT = [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]


class Attachments(Case):
    def test_offset_ridge_preserves_peak_elevation_and_updates_attached_gable_wall(self):
        w, r = self.gable_wall(), self.roof()
        self.run_("model.attach_wall", ids=[w, r], to="roof")
        before = self.top(w)
        self.run_("model.set_property", id=r, key="ridge_offset", value=600)
        self.assertAlmostEqual(self.top(w), before, places=6)
        prof = self.meta(w)["spadra:top_profile"]
        self.assertEqual([round(s, 6) for s, _ in prof], [0.0, 2.6, 4.0])
        self.assertNotAlmostEqual(prof[1][0] - prof[0][0], prof[2][0] - prof[1][0])
        plan = next(v["id"] for v in self.p.manifest["views"] if v.get("level_id") == self.roof_level and v["kind"] == "plan")
        ridge = next(q["ridge"] for q in self.p.plan_overlay(plan) if q["id"] == r)
        self.assertAlmostEqual((ridge[0][0] + ridge[1][0]) / 2, 3000.0)
        self.assertAlmostEqual((ridge[0][1] + ridge[1][1]) / 2, 2600.0)
        handle = next(q["ridge_handle"] for q in self.p.scene_data()["objects"] if q["id"] == r)
        self.assertAlmostEqual(handle["offset_mm"], 600)
        self.assertAlmostEqual(handle["point"][2], self.elevation(self.roof_level) + 2 * math.tan(math.radians(30)))
        self.assertAlmostEqual(self.vol(w), .25 * (4 * (self.elevation(self.roof_level) - .1) + 2 * math.tan(math.radians(30)) * 2), places=5)
        self.p.undo()
        self.assertEqual([round(s, 6) for s, _ in self.meta(w)["spadra:top_profile"]], [0.0, 2.0, 4.0])

    def elevation(self, level):
        return self.p.level(level)["elevation"]

    def roof(self, kind="gable", **extra):
        return self.run_("model.add_roof", level=self.roof_level, roof=kind, points=RECT, thickness=100, pitch=30, **extra)["id"]

    def gable_wall(self):
        """The wall along the short edge, x = 0: under the roof's gable end, so the roof line rises to the ridge and falls again."""
        return self.run_("model.add_wall", start=[0, 0], end=[0, 4000], level=self.l1, thickness=250, height=3.2)["id"]

    def top(self, oid):
        return self.p.model.world_bounds(oid)[1][2]

    # ------------------------------------------------------------------------------------------------------------- roof
    def test_a_wall_under_a_gable_roof_follows_the_underside_up_to_the_ridge(self):
        w, r = self.gable_wall(), self.roof()
        out = self.run_("model.attach_wall", ids=[w, r], to="roof")
        self.assertEqual(out["walls"], [w])
        eaves = self.elevation(self.roof_level) - 0.1
        rise = math.tan(math.radians(30)) * 2.0
        prof = self.meta(w)["spadra:top_profile"]
        self.assertEqual([round(s, 6) for s, _ in prof], [0.0, 2.0, 4.0])
        self.assertAlmostEqual(prof[0][1], eaves - self.elevation(self.l1), places=6)
        self.assertAlmostEqual(prof[1][1], eaves + rise - self.elevation(self.l1), places=6)
        self.assertAlmostEqual(self.meta(w)["spadra:height"], eaves + rise, places=6)
        self.assertAlmostEqual(self.top(w), eaves + rise, places=6)
        # a gable: two trapezoids under the roof line, 0.25 thick
        self.assertAlmostEqual(self.vol(w), 0.25 * (4.0 * eaves + 0.5 * 4.0 * rise), places=5)

    def test_the_wall_follows_the_roof_when_the_roof_is_edited(self):
        w, r = self.gable_wall(), self.roof()
        self.run_("model.attach_wall", ids=[w, r], to="roof")
        before = self.vol(w)
        self.run_("model.set_property", id=r, key="pitch", value=45)
        rise = math.tan(math.radians(45)) * 2.0
        self.assertAlmostEqual(self.top(w), self.elevation(self.roof_level) - 0.1 + rise, places=6)
        self.assertGreater(self.vol(w), before)
        self.p.undo()
        self.assertAlmostEqual(self.top(w), self.elevation(self.roof_level) - 0.1 + math.tan(math.radians(30)) * 2.0, places=6)

    def test_a_flat_roof_gives_a_level_top_and_no_profile(self):
        w = self.wall(a=(0, 0), b=(6000, 0))
        r = self.roof("flat")
        self.run_("model.attach_wall", ids=[w, r], to="roof", offset=-100)
        self.assertIsNone(self.meta(w).get("spadra:top_profile"))
        self.assertAlmostEqual(self.meta(w)["spadra:height"], self.elevation(self.roof_level) - 0.1 - self.elevation(self.l1), places=6)

    def test_a_wall_that_sticks_out_from_under_the_roof_is_refused_with_how_much(self):
        w = self.wall(a=(0, 0), b=(8000, 0))
        r = self.roof("flat")
        with self.assertRaises(BadRequest) as c:
            self.run_("model.attach_wall", ids=[w, r], to="roof")
        self.assertIn("2.00 m", c.exception.message)
        self.assertIsNone(self.meta(w).get("spadra:top_to"))
        self.assertAlmostEqual(self.meta(w)["spadra:height"], 3.2)

    def test_a_height_cannot_be_typed_on_an_attached_wall_but_detaching_restores_it(self):
        w, r = self.gable_wall(), self.roof()
        self.run_("model.attach_wall", ids=[w, r], to="roof")
        with self.assertRaises(BadRequest) as c:
            self.run_("model.set_property", id=w, key="height", value=2.0)
        self.assertIn("attached", c.exception.message)
        self.run_("model.detach_wall", ids=[w], **{"from": "roof"})
        self.assertAlmostEqual(self.meta(w)["spadra:height"], 3.2)
        self.assertIsNone(self.meta(w).get("spadra:top_profile"))
        self.assertAlmostEqual(self.top(w), 3.2, places=6)
        self.assertAlmostEqual(self.vol(w), 0.25 * 4.0 * 3.2, places=5)

    def test_deleting_the_roof_frees_the_wall_at_its_old_height(self):
        w, r = self.gable_wall(), self.roof()
        self.run_("model.attach_wall", ids=[w, r], to="roof")
        self.run_("model.delete", id=r)
        self.assertIsNone(self.meta(w).get("spadra:top_to"))
        self.assertAlmostEqual(self.top(w), 3.2, places=6)
        self.p.undo()
        self.assertIsNotNone(self.meta(w).get("spadra:top_to"))

    def test_doors_are_kept_and_one_that_reaches_the_roof_is_refused(self):
        w, r = self.gable_wall(), self.roof()
        d = self.run_("model.add_door", wall=w, offset=2.0, width=900, height=2100)["id"]
        self.run_("model.attach_wall", ids=[w, r], to="roof")
        self.assertTrue(self.p.model.get(d))
        low = self.run_("model.add_wall", start=[0, 0], end=[0, 4000], level=self.l2, thickness=250, height=3.2)["id"]
        self.run_("model.add_door", wall=low, offset=2.0, width=900, height=2100)
        with self.assertRaises(BadRequest) as c:                                # a roof line that comes down below the head of the door
            self.run_("model.attach_wall", ids=[low, r], to="roof", offset=-1500)
        self.assertIsNone(self.meta(low).get("spadra:top_to"))

    def test_needs_a_roof_and_a_wall(self):
        w = self.gable_wall()
        with self.assertRaises(BadRequest):
            self.run_("model.attach_wall", ids=[w], to="roof")
        r = self.roof()
        with self.assertRaises(BadRequest):
            self.run_("model.attach_wall", ids=[r], to="roof")

    def test_a_layered_wall_follows_the_roof_in_every_layer(self):
        w, r = self.gable_wall(), self.roof()
        self.run_("model.add_layer", id=w, material="Mineral wool", thickness=100)
        self.run_("model.attach_wall", ids=[w, r], to="roof")
        eaves = self.elevation(self.roof_level) - 0.1
        rise = math.tan(math.radians(30)) * 2.0
        self.assertAlmostEqual(self.vol(w), 0.35 * (4.0 * eaves + 0.5 * 4.0 * rise), places=5)
        self.assertAlmostEqual(self.top(w), eaves + rise, places=6)

    # ------------------------------------------------------------------------------------------------------------ level
    def test_a_wall_extends_to_the_next_level_and_follows_it(self):
        w = self.wall()
        self.run_("model.attach_wall", ids=[w], to="level")
        self.assertAlmostEqual(self.meta(w)["spadra:height"], self.elevation(self.l2) - self.elevation(self.l1), places=9)
        self.run_("level.set", id=self.l2, elevation=self.elevation(self.l2) + 0.5)
        self.assertAlmostEqual(self.top(w), self.elevation(self.l2), places=6)
        self.p.undo()
        self.assertAlmostEqual(self.top(w), self.elevation(self.l2), places=6)

    def test_extending_to_a_level_with_an_offset_and_disconnecting(self):
        w = self.wall()
        self.run_("model.attach_wall", ids=[w], to="level", level=self.roof_level, offset=-250)
        self.assertAlmostEqual(self.top(w), self.elevation(self.roof_level) - 0.25, places=6)
        self.run_("model.detach_wall", ids=[w], **{"from": "level"})
        self.assertAlmostEqual(self.top(w), 3.2, places=6)
        with self.assertRaises(BadRequest):
            self.run_("model.detach_wall", ids=[w])

    def test_no_level_above_means_nothing_to_extend_to(self):
        w = self.run_("model.add_wall", start=[0, 0], end=[3000, 0], level=self.roof_level, thickness=250, height=1.0)["id"]
        with self.assertRaises(BadRequest) as c:
            self.run_("model.attach_wall", ids=[w], to="level")
        self.assertIn("no level above", c.exception.message)

    def test_properties_say_what_the_wall_follows(self):
        w, r = self.gable_wall(), self.roof()
        self.assertEqual(self.rows(w)["Top"]["value"], "Free (its own height)")
        self.run_("model.attach_wall", ids=[w, r], to="roof")
        rows = self.rows(w)
        self.assertIn("Attached to roof", rows["Top"]["value"])
        self.assertEqual(rows["Height"]["type"], "readonly")
        self.assertIn("Lowest / highest", rows)
