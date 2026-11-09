"""Door panels: a door is a list of panels, each with its own width, hinge, swing side and operation, edited separately."""
from __future__ import annotations

import tempfile
from pathlib import Path

from applications.SPADRA import documentation as D
from applications.SPADRA import elements as E
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest

from .test_bim import Case


class OpeningProfiles(Case):
    def setUp(self) -> None:
        super().setUp()
        self.host = self.wall(a=(0, 0), b=(6000, 0))

    def frame(self, opening, **kwargs):
        return self.run_("model.add_opening_profile", opening=opening, **kwargs)["id"]

    def test_door_has_three_closed_profile_members_and_reduced_panel_without_changing_nominal_size(self):
        door = self.run_("model.add_door", wall=self.host, offset=3.0, width=900, height=2100)["id"]
        initial = self.vol(door)
        frame = self.frame(door, width=50)
        self.assertEqual(E.category(self.p, frame), "opening_profile")
        self.assertEqual(len(self.p.model.tessellate(frame)["faces"]), 36)
        expected_volume = 0.05 * E.wall_frame(self.p, self.host)["thickness"] * (2 * (2.1 - 0.05) + 0.9)
        self.assertAlmostEqual(self.vol(frame), expected_volume, places=6)
        self.assertLess(self.vol(door), initial)
        self.assertAlmostEqual(E.m(self.meta(door), "width"), 0.9)
        self.assertAlmostEqual(E.m(self.meta(door), "height"), 2.1)
        self.assertEqual(E.opening_profile(self.p, door), frame)
        self.assertFalse([x for x in E.check(self.p) if x["severity"] == "error"])

    def test_window_has_four_closed_members_and_follows_host_thickness_and_size(self):
        window = self.run_("model.add_window", wall=self.host, offset=3.0, width=1200, height=1200, sill=900)["id"]
        frame = self.frame(window, width=50)
        self.assertEqual(len(self.p.model.tessellate(frame)["faces"]), 48)
        v0 = self.vol(frame)
        thickness0 = E.wall_frame(self.p, self.host)["thickness"]
        self.run_("model.set_property", id=self.host, key="thickness", value=300)
        self.assertAlmostEqual(self.vol(frame), v0 * (0.3 / thickness0), places=6)
        self.run_("model.set_property", id=window, key="width", value=1500)
        self.assertGreater(self.vol(frame), v0 * (0.3 / thickness0))
        self.assertAlmostEqual(E.m(self.meta(window), "width"), 1.5)

    def test_frame_constraints_rollback_and_removal_restores_panel(self):
        door = self.run_("model.add_door", wall=self.host, offset=3.0, width=550, height=1200)["id"]
        initial = self.vol(door)
        with self.assertRaises(BadRequest):
            self.frame(door, width=250)
        self.assertIsNone(E.opening_profile(self.p, door))
        frame = self.frame(door, width=50)
        with self.assertRaises(BadRequest):
            self.run_("model.set_property", id=frame, key="face_width", value=250)
        self.assertAlmostEqual(E.m(self.meta(frame), "face_width"), 0.05)
        self.run_("model.remove_opening_profile", opening=door)
        self.assertIsNone(E.opening_profile(self.p, door))
        self.assertAlmostEqual(self.vol(door), initial, places=6)

    def test_existing_sdrw_section_is_real_extruded_geometry_and_persists(self):
        door = self.run_("model.add_door", wall=self.host, offset=3.0, width=900, height=2100)["id"]
        drawing = next(iter(self.p.drawings))
        outline = self.run_("draw.polyline", drawing=drawing, points=[[0, 0], [50, 0], [45, 200], [0, 200]], closed=True)["id"]
        frame = self.frame(door, profile_drawing=drawing, profile_entity=outline)
        self.assertEqual(E.m(self.meta(frame), "section_source"), {"drawing": drawing, "entity": outline})
        self.assertGreater(self.vol(frame), 0)
        self.assertNotAlmostEqual(self.vol(frame), 0.0625, places=5)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "profiles.spadra"
            self.p.save(target)
            reopened = Project.open(target)
            self.assertEqual(E.opening_profile(reopened, door), frame)
            self.assertAlmostEqual(reopened.model.volume(frame, unit="m"), self.vol(frame), places=6)
        self.run_("model.add_opening_profile", opening=door, width=60, replace=True)
        self.assertEqual(E.opening_profile(self.p, door), frame)
        self.assertIsNone(E.m(self.meta(frame), "section"))

    def test_a_polyline_drawn_back_to_its_first_point_is_closed_and_usable_as_a_frame_profile(self):
        door = self.run_("model.add_door", wall=self.host, offset=3.0, width=900, height=2100)["id"]
        drawing = next(iter(self.p.drawings))
        corners = [[0, 0], [50, 0], [50, 100], [30, 100], [30, 200], [0, 200]]
        # drawn through the command with the first point repeated: it is stored closed, without the repeated point
        looped = self.run_("draw.polyline", drawing=drawing, points=corners + [corners[0]], closed=False)["id"]
        shape = self.p.drawing(drawing).get(looped)
        self.assertTrue(shape["geometry"]["closed"])
        self.assertEqual(len(shape["geometry"]["points"]), 6)
        frame = self.frame(door, profile_drawing=drawing, profile_entity=looped)
        self.assertEqual(E.m(self.meta(frame), "section_source"), {"drawing": drawing, "entity": looped})
        self.assertGreater(self.vol(frame), 0)

    def test_an_older_open_polyline_that_returns_to_its_start_is_still_accepted_as_a_profile(self):
        window = self.run_("model.add_window", wall=self.host, offset=3.0, width=1200, height=1200, sill=900)["id"]
        drawing = next(iter(self.p.drawings))
        doc = self.p.drawing(drawing)
        legacy = doc.add_polyline([(0, 0), (50, 0), (50, 200), (0, 200), (0, 0)], closed=False)      # as earlier versions stored it
        self.assertFalse(doc.get(legacy)["geometry"]["closed"])
        self.assertGreater(self.vol(self.frame(window, profile_drawing=drawing, profile_entity=legacy)), 0)

    def test_profile_rejects_open_or_self_intersecting_sdrw_contours(self):
        window = self.run_("model.add_window", wall=self.host, offset=3.0, width=1200, height=1200, sill=900)["id"]
        drawing = next(iter(self.p.drawings))
        open_line = self.run_("draw.polyline", drawing=drawing, points=[[0, 0], [50, 0], [50, 200]], closed=False)["id"]
        with self.assertRaises(BadRequest):
            self.frame(window, profile_drawing=drawing, profile_entity=open_line)
        with self.assertRaises(BadRequest):
            self.frame(window, section=[[0, 0], [50, 200], [0, 200], [50, 0]])
        self.assertIsNone(E.opening_profile(self.p, window))

    def test_sdrw_rectangle_source_edit_refits_profile_with_one_undo_transaction(self):
        door = self.run_("model.add_door", wall=self.host, offset=3.0, width=900, height=2100)["id"]
        drawing = next(iter(self.p.drawings))
        rectangle = self.run_("draw.rectangle", drawing=drawing, corner=[0, 0], width=50, height=200)["id"]
        frame = self.frame(door, profile_drawing=drawing, profile_entity=rectangle)
        original_volume = self.vol(frame)
        original_leaf = self.vol(door)
        self.run_("draw.scale", drawing=drawing, ids=[rectangle], factor=1.2, about=[0, 0])
        self.assertAlmostEqual(E.m(self.meta(frame), "face_width"), 0.06)
        self.assertNotAlmostEqual(self.vol(frame), original_volume)
        self.assertNotAlmostEqual(self.vol(door), original_leaf)
        with self.assertRaises(BadRequest):
            self.run_("draw.delete", drawing=drawing, ids=[rectangle])
        self.assertIsNotNone(self.p.drawing(drawing).get(rectangle))
        self.p.undo()
        self.assertAlmostEqual(E.m(self.meta(frame), "face_width"), 0.05)
        self.assertAlmostEqual(self.vol(frame), original_volume, places=7)
        self.assertAlmostEqual(self.vol(door), original_leaf, places=7)
        self.p.redo()
        self.assertAlmostEqual(E.m(self.meta(frame), "face_width"), 0.06)


class Panels(Case):
    def setUp(self) -> None:
        super().setUp()
        self.w = self.wall(a=(0, 0), b=(6000, 0))
        self.d = self.run_("model.add_door", wall=self.w, offset=3.0, width=1800, height=2100)["id"]

    def panels(self):
        return self.meta(self.d).get("spadra:panels")

    def plan_symbol(self):
        plan = next(v for v in self.p.manifest["views"] if v.get("level_id") == self.l1 and v["kind"] == "plan")
        return next(i for i in self.p.plan_overlay(plan["id"]) if i["id"] == self.d)["symbol"]

    def test_a_door_is_one_panel_until_one_is_added(self):
        self.assertIsNone(self.panels())
        rows = self.rows(self.d)
        self.assertIn("hinge", rows)
        self.assertNotIn("panel:p1:width", rows)
        vol = self.vol(self.d)
        self.run_("model.add_panel", id=self.d)
        self.assertEqual(len(self.panels()), 2)
        self.assertAlmostEqual(sum(q["width"] for q in self.panels()), 1.8, places=9)
        self.assertAlmostEqual(self.meta(self.d)["spadra:width"], 1.8, places=9)
        self.assertAlmostEqual(self.vol(self.d), vol, places=6)                         # same leaf area, now as two leaves

    def test_the_new_panel_is_hinged_on_the_opposite_jamb(self):
        self.run_("model.add_panel", id=self.d)
        a, b = self.panels()
        self.assertEqual((a["hinge"], b["hinge"]), ("start", "end"))
        self.assertAlmostEqual(a["width"], 0.9)

    def test_changing_one_panel_does_not_change_the_other_hinge_or_the_door_width(self):
        self.run_("model.add_panel", id=self.d)
        a, b = self.panels()
        self.run_("model.set_property", id=self.d, key=f"panel:{a['id']}:swing", value="right")
        a2, b2 = self.panels()
        self.assertEqual((a2["swing"], b2["swing"]), (-1, 1))
        self.assertAlmostEqual(self.meta(self.d)["spadra:width"], 1.8)

    def test_a_panel_width_is_taken_from_its_neighbour_and_the_door_stays_as_wide(self):
        self.run_("model.add_panel", id=self.d)
        a, b = self.panels()
        self.run_("model.set_property", id=self.d, key=f"panel:{a['id']}:width", value=1200)
        a2, b2 = self.panels()
        self.assertAlmostEqual(a2["width"], 1.2)
        self.assertAlmostEqual(b2["width"], 0.6)
        with self.assertRaises(BadRequest) as c:
            self.run_("model.set_property", id=self.d, key=f"panel:{a['id']}:width", value=1780)
        self.assertIn("narrower", c.exception.message)
        self.assertAlmostEqual(self.panels()[0]["width"], 1.2)

    def test_a_fixed_panel_has_no_swing_symbol(self):
        self.run_("model.add_panel", id=self.d)
        sym = self.plan_symbol()
        self.assertIn("more", sym)                                                       # two swinging leaves: the first and one more
        a, b = self.panels()
        self.run_("model.set_property", id=self.d, key=f"panel:{b['id']}:operation", value="fixed")
        sym = self.plan_symbol()
        self.assertNotIn("more", sym)
        self.assertAlmostEqual(sym["radius"], 900.0, places=3)                           # 900 mm leaf, drawn in mm
        self.run_("model.set_property", id=self.d, key=f"panel:{a['id']}:operation", value="fixed")
        self.assertNotIn("hinge", self.plan_symbol())

    def test_each_swinging_panel_gets_its_own_symbol_in_elevation(self):
        self.run_("model.add_panel", id=self.d)
        a, b = self.panels()
        self.run_("model.set_property", id=self.d, key=f"panel:{b['id']}:swing", value="right")      # the two leaves open to opposite sides of the wall
        from applications.SPADRA import elements as E
        sym = E.elevation_symbol(self.p, self.d, (0.0, 1.0))
        self.assertEqual(len(sym["more"]), 1)
        self.assertNotEqual(sym["toward"], sym["more"][0]["toward"])

    def test_removing_a_panel_gives_its_width_to_the_neighbour(self):
        self.run_("model.add_panel", id=self.d)
        a, b = self.panels()
        self.run_("model.remove_panel", id=self.d, panel=b["id"])
        self.assertEqual(len(self.panels()), 1)
        self.assertAlmostEqual(self.panels()[0]["width"], 1.8)
        with self.assertRaises(BadRequest):
            self.run_("model.remove_panel", id=self.d, panel=a["id"])

    def test_door_hinge_and_swing_belong_to_the_panels_once_there_are_several(self):
        self.run_("model.add_panel", id=self.d)
        with self.assertRaises(BadRequest):
            self.run_("model.set_property", id=self.d, key="hinge", value="end")
        rows = self.rows(self.d)
        self.assertNotIn("hinge", rows)
        self.assertIn(f"panel:{self.panels()[0]['id']}:hinge", rows)

    def test_the_door_width_scales_its_panels(self):
        self.run_("model.add_panel", id=self.d)
        self.run_("model.set_property", id=self.d, key="width", value=1400)
        self.assertAlmostEqual(sum(q["width"] for q in self.panels()), 1.4, places=9)
        self.assertAlmostEqual(self.panels()[0]["width"], 0.7)

    def test_undo_takes_the_panel_away(self):
        self.run_("model.add_panel", id=self.d)
        self.p.undo()
        self.assertIsNone(self.panels())

    def test_the_door_still_fits_its_opening_and_the_wall_keeps_it(self):
        self.run_("model.add_panel", id=self.d)
        self.run_("model.add_panel", id=self.d)
        self.assertEqual(len(self.panels()), 3)
        self.assertAlmostEqual(sum(q["width"] for q in self.panels()), 1.8, places=9)
        lo, hi = self.p.model.world_bounds(self.d)
        self.assertAlmostEqual(hi[0] - lo[0], 1.8, places=6)
        self.assertAlmostEqual(lo[0], 2.1, places=6)
