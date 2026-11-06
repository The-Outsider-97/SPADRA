"""Door panels: a door is a list of panels, each with its own width, hinge, swing side and operation, edited separately."""
from __future__ import annotations

from applications.SPADRA import documentation as D
from applications.tools.errors import BadRequest

from .test_bim import Case


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
