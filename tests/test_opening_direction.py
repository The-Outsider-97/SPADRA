"""Doors and windows show which way they open in every 2D drawing: in a section or elevation as two lines from the hinge edge (solid when the
leaf opens toward the viewer, dashed when it opens away), on a plan as the swing arc, and the person can turn it off per view."""
from __future__ import annotations

import math

from applications.SPADRA import documentation as D
from applications.tools.errors import BadRequest

from .test_documentation import Room


class OpeningDirection(Room):
    """The room's south wall runs along y = 0; a door opens to the wall's left (+y, into the room) unless told otherwise."""

    def item(self, projection, oid):
        return next((i for i in projection["items"] if i["id"] == oid), None)

    def opening(self, side, oid):
        it = self.item(self.elevation(side), oid)
        return it["opening"] if it else None

    def plan(self):
        view = next(v for v in self.p.manifest["views"] if v["kind"] == "plan")
        return {w["id"]: w for w in self.p.plan_overlay(view["id"])}, view

    def test_a_door_opening_away_from_the_viewer_is_dashed_and_its_apex_is_at_the_hinge(self):
        o = self.opening("south", self.door)                                   # the south elevation looks +y; the door opens +y: away
        self.assertFalse(o["toward"])
        a, b = o["lines"]
        self.assertEqual(a[:2], b[:2])                                          # both lines start at the apex
        self.assertAlmostEqual(a[0], 1050.0, places=3)                          # hinge "start": the jamb at x = 1500 − 450
        self.assertAlmostEqual(a[1], 1050.0, places=3)                          # at mid height of a 2100 door
        self.assertAlmostEqual(a[2], 1950.0, places=3)                          # to the free jamb ...
        self.assertAlmostEqual(a[3], 2100.0, places=3)                          # ... at the head
        self.assertAlmostEqual(b[2], 1950.0, places=3)
        self.assertAlmostEqual(b[3], 0.0, places=3)                             # ... and at the floor

    def test_turning_the_swing_side_turns_dashed_into_solid(self):
        self.cmd("model.set_property", id=self.door, key="swing", value="right")
        self.assertTrue(self.opening("south", self.door)["toward"])
        self.p.undo()
        self.assertFalse(self.opening("south", self.door)["toward"])

    def test_seen_from_the_other_side_the_same_door_flips(self):
        self.assertTrue(self.opening("north", self.door)["toward"])             # looking -y at a door that opens +y: toward the viewer

    def test_the_hinge_decides_which_edge_the_apex_is_on(self):
        self.cmd("model.set_property", id=self.door, key="hinge", value="end")
        a, b = self.opening("south", self.door)["lines"]
        self.assertAlmostEqual(a[0], 1950.0, places=3)
        self.assertAlmostEqual(a[2], 1050.0, places=3)

    def test_a_fixed_window_has_no_symbol_and_a_casement_has_one(self):
        self.assertIsNone(self.opening("south", self.window))
        self.cmd("model.set_property", id=self.window, key="operation", value="casement")
        o = self.opening("south", self.window)
        self.assertEqual(o["kind"], "window")
        a, b = o["lines"]
        self.assertAlmostEqual(a[1], 900 + 600, places=3)                       # mid height of the sash
        self.assertAlmostEqual(a[0], 4500 - 600, places=3)
        self.cmd("model.set_property", id=self.window, key="swing", value="right")
        self.assertTrue(self.opening("south", self.window)["toward"])

    def test_a_wall_seen_edge_on_has_no_symbol(self):
        self.assertIsNone(self.opening("east", self.door))                       # the east elevation looks along the south wall

    def test_a_section_that_cuts_the_symbol_in_two_draws_none_of_it(self):
        _, pr = self.section((1500, -2000), (1500, 6000))                       # the cut plane passes through the middle of the door
        self.assertIsNone(self.item(pr, self.door)["opening"])

    def test_a_section_that_looks_at_the_door_draws_it(self):
        _, pr = self.section((0, -1000), (6000, -1000))                         # looks +y at the south wall from outside
        o = self.item(pr, self.door)["opening"]
        self.assertFalse(o["toward"])
        self.assertAlmostEqual(o["lines"][0][0], 1050.0, places=3)

    def test_the_plan_shows_the_swing_of_doors_and_casements_and_not_of_fixed_windows(self):
        plan, _ = self.plan()
        self.assertIn("hinge", plan[self.door]["symbol"])
        self.assertNotIn("hinge", plan[self.window]["symbol"])
        self.cmd("model.set_property", id=self.window, key="operation", value="casement")
        sym = self.plan()[0][self.window]["symbol"]
        self.assertIn("hinge", sym)
        self.assertIn("a", sym)                                                  # the glass line is still there
        self.assertAlmostEqual(sym["radius"], 1200.0, places=3)

    def test_the_view_can_hide_the_direction_in_plan_and_in_elevation(self):
        _, plan_view = self.plan()
        elevation = next(v for v in self.p.manifest["views"] if v.get("side") == "south")
        self.cmd("view.set_option", id=plan_view["id"], key="opening_direction", value="hidden")
        plan, _ = self.plan()
        self.assertNotIn("hinge", plan[self.door]["symbol"])                     # no leaf and no arc ...
        self.assertEqual(len(plan[self.door]["polygon"]), 4)                     # ... but the door is still there
        self.assertIsNotNone(self.opening("south", self.door))                  # other views keep theirs
        self.cmd("view.set_option", id=elevation["id"], key="opening_direction", value="hidden")
        self.assertNotIn("opening", self.item(self.elevation("south"), self.door))
        self.p.undo()
        self.assertIsNotNone(self.opening("south", self.door))

    def test_the_option_is_for_drawing_views_and_for_this_one_setting(self):
        schedule = self.cmd("view.add_schedule", category="door")["id"]
        with self.assertRaises(BadRequest):
            self.cmd("view.set_option", id=schedule, key="opening_direction", value="hidden")
        with self.assertRaises(BadRequest):
            self.cmd("view.set_option", id=self.p.manifest["views"][0]["id"], key="colour", value="hidden")

    def test_the_properties_panel_offers_the_option_and_the_window_operation(self):
        elevation = next(v for v in self.p.manifest["views"] if v.get("side") == "south")
        rows = {r["key"]: r for g in self.p.properties({"view": elevation["id"]})["groups"] for r in g["rows"] if "key" in r}
        self.assertEqual(rows["opening_direction"]["value"], "shown")
        w = {r.get("key") or r["label"]: r for g in self.p.properties({"model": self.window})["groups"] for r in g["rows"]}
        self.assertEqual(w["operation"]["value"], "fixed")
        self.assertNotIn("swing", w)                                             # a fixed window has no swing side
        self.cmd("model.set_property", id=self.window, key="operation", value="casement")
        w = {r.get("key") or r["label"]: r for g in self.p.properties({"model": self.window})["groups"] for r in g["rows"]}
        self.assertIn("swing", w)
        with self.assertRaises(BadRequest):
            self.cmd("model.set_property", id=self.window, key="operation", value="sliding")

    def test_the_symbol_follows_the_model_when_the_door_moves(self):
        self.cmd("model.set_property", id=self.door, key="offset", value=2.5)
        a = self.opening("south", self.door)["lines"][0]
        self.assertAlmostEqual(a[0], 2050.0, places=3)
