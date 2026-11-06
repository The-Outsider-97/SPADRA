"""Stretching the outline of a floor or roof: one command, everything that depends on it follows, and an outline it cannot have is refused."""
from __future__ import annotations

from applications.tools.errors import BadRequest

from .test_bim import Case

RECT = [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]


class Outline(Case):
    def floor(self):
        return self.run_("model.add_floor", level=self.l1, points=RECT, thickness=200, material="Concrete")["id"]

    def test_moving_an_edge_changes_the_floor_and_its_volume(self):
        f = self.floor()
        self.assertAlmostEqual(self.vol(f), 6 * 4 * 0.2, places=6)
        self.run_("model.set_boundary", id=f, points=[[0, 0], [8000, 0], [8000, 4000], [0, 4000]])
        self.assertAlmostEqual(self.vol(f), 8 * 4 * 0.2, places=6)
        self.p.undo()
        self.assertAlmostEqual(self.vol(f), 6 * 4 * 0.2, places=6)

    def test_a_crossing_outline_is_refused_and_nothing_changes(self):
        f = self.floor()
        with self.assertRaises(BadRequest):
            self.run_("model.set_boundary", id=f, points=[[0, 0], [6000, 4000], [6000, 0], [0, 4000]])
        self.assertAlmostEqual(self.vol(f), 6 * 4 * 0.2, places=6)
        self.assertEqual(self.meta(f)["spadra:boundary"], [[0.0, 0.0], [6.0, 0.0], [6.0, 4.0], [0.0, 4.0]])

    def test_a_wall_is_not_an_outline(self):
        with self.assertRaises(BadRequest):
            self.run_("model.set_boundary", id=self.wall(), points=RECT)

    def test_the_plan_gives_a_floor_its_outline_for_the_grips(self):
        f = self.floor()
        plan = next(v for v in self.p.manifest["views"] if v.get("level_id") == self.l1 and v["kind"] == "plan")
        item = next(i for i in self.p.plan_overlay(plan["id"]) if i["id"] == f)
        self.assertEqual(item["outline"], [[0.0, 0.0], [6000.0, 0.0], [6000.0, 4000.0], [0.0, 4000.0]])

    def test_a_pitched_roof_keeps_to_a_rectangle_and_a_wall_attached_to_it_follows(self):
        r = self.run_("model.add_roof", level=self.roof_level, roof="gable", points=RECT, thickness=100, pitch=30)["id"]
        w = self.run_("model.add_wall", start=[0, 0], end=[0, 4000], level=self.l1, thickness=250, height=3.2)["id"]
        self.run_("model.attach_wall", ids=[w, r], to="roof")
        with self.assertRaises(BadRequest):                                       # a corner dragged out of square: a gable needs a rectangle
            self.run_("model.set_boundary", id=r, points=[[0, 0], [6000, 0], [6500, 4000], [0, 4000]])
        top = self.p.model.world_bounds(w)[1][2]
        self.run_("model.set_boundary", id=r, points=[[0, 0], [6000, 0], [6000, 6000], [0, 6000]])      # the roof is made deeper: the ridge rises
        self.assertGreater(self.p.model.world_bounds(w)[1][2], top)
        self.assertAlmostEqual(self.meta(w)["spadra:top_profile"][-1][0], 4.0, places=6)               # the wall is still 4 m long
