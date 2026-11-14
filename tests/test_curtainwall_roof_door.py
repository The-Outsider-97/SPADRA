"""A roof coming down into a door field of a curtain wall (follow-up to the issue sheet of 11-11-2026): the error says which field, where the roof is over
it and by how much the door would be cut, and offers corrections that were checked to leave every door whole; applying one and attaching again works."""
from __future__ import annotations

import unittest

from applications.SPADRA import curtainwall as CW
from applications.SPADRA import elements as E
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest


class RoofDoor(unittest.TestCase):
    free, rows, door = 3500, 0, "panel:0:0:type"

    def setUp(self) -> None:
        self.p = Project.create("Roof door")
        lv = [l["id"] for l in self.p.manifest["levels"]]
        self.l0, self.l1 = lv[0], lv[1]
        self.roof = self.cmd("model.add_roof", points=[[-500, -2000], [6500, -2000], [6500, 2000], [-500, 2000]], level=self.l1, roof="gable", pitch=30, thickness=200)["id"]
        # a wall across the gable: its top rises to the ridge, and the field next to the end stands where the roof is low
        self.cw = self.cmd("model.add_curtainwall", start=[3000, -1800], end=[3000, 1800], level=self.l0, height=self.free, v_mode="count", v_value=2, h_mode="count", h_value=self.rows)["id"]
        self.cmd("model.set_property", id=self.cw, key=self.door, value="Door")

    def cmd(self, op, **params):
        return self.p.execute(op, params)["result"]

    def refused(self):
        with self.assertRaises(BadRequest) as ctx:
            self.cmd("model.attach_wall", ids=[self.cw, self.roof], to="roof")
        return ctx.exception

    def test_the_message_names_the_field_and_the_numbers(self):
        err = self.refused()
        d = err.details
        self.assertEqual((d["element"], d["part"], d["cell"]), (self.cw, "cell:0,0", "0,0"))
        self.assertGreater(d["cut_mm"], 0)
        self.assertIn("field 1.1", str(err))
        self.assertIn("1st field from the start", str(err))
        self.assertIn(f"cut off by {d['cut_mm']:,.0f} mm", str(err))
        self.assertNotIn("whole field", str(err))

    def test_every_correction_offered_works_and_none_is_offered_that_does_not(self):
        fixes = self.refused().details["fixes"]
        self.assertGreaterEqual(len(fixes), 2)
        # apply each in turn on a fresh copy of the same wall, then attach: the roof must not cut a door
        for n in range(len(fixes)):
            with self.subTest(fix=fixes[n]["label"]):
                self.setUp()
                fx = self.refused().details["fixes"][n]
                for st in fx["steps"]:
                    self.p.execute(st["op"], st["params"])
                self.cmd("model.attach_wall", ids=[self.cw, self.roof], to="roof")
                sp = CW.spec(E.meta_of(self.p, self.cw))
                self.assertEqual(CW.door_conflicts(sp), [])
                if not fixes[n]["label"].startswith("Make field"):
                    self.assertTrue(any(CW.cell_spec(sp, c)["type"] == "Door" for c in CW.cells(sp)), "a door is still in the wall")

    def test_the_transom_correction_keeps_the_door_as_wide_as_it_was(self):
        fixes = self.refused().details["fixes"]
        fx = next(f for f in fixes if f["label"].startswith("Add a transom"))
        before = next(c for c in CW.cells(CW.spec(E.meta_of(self.p, self.cw))) if c["key"] == "0,0")
        for st in fx["steps"]:
            self.p.execute(st["op"], st["params"])
        after = next(c for c in CW.cells(CW.spec(E.meta_of(self.p, self.cw))) if c["key"] == "0,0")
        self.assertAlmostEqual(after["u1"] - after["u0"], before["u1"] - before["u0"], places=9)
        self.assertLess(after["z1"], before["z1"])
        self.assertEqual(CW.cell_spec(CW.spec(E.meta_of(self.p, self.cw)), after)["type"], "Door")

    def test_the_correction_runs_on_the_wall_as_stored_and_the_refused_change_then_goes_through(self):
        fx = next(f for f in self.refused().details["fixes"] if f["label"].startswith("Add a transom"))
        self.assertLess(fx["steps"][0]["params"]["value"], self.free * 1000)                     # a position the stored (not yet raised) wall has
        self.assertEqual(CW.spec(E.meta_of(self.p, self.cw))["height"], self.free / 1000)       # the refused change left nothing behind

    def test_a_wall_with_no_roof_on_it_is_never_refused(self):
        self.assertEqual(CW.door_conflicts(CW.spec(E.meta_of(self.p, self.cw))), [])


class DoorWhereTheRoofIsLevel(unittest.TestCase):
    """The roof is level over the first and last fields and slopes up across the middle one: a door is whole where the roof is level and cut where it slopes."""

    def setUp(self) -> None:
        self.p = Project.create("Level")
        self.l0 = self.p.manifest["levels"][0]["id"]
        self.cw = self.p.execute("model.add_curtainwall", {"start": [0, 0], "end": [6000, 0], "level": self.l0, "height": 3200, "v_mode": "count", "v_value": 2,
                                                          "h_mode": "count", "h_value": 0})["result"]["id"]
        self.p.execute("model.set_property", {"id": self.cw, "key": "panel:1:0:type", "value": "Door"})
        E._attach(self.p, self.cw, {"top_profile": [[0.0, 2.2], [2.1, 2.2], [3.0, 3.2], [6.0, 3.2]]})          # as an attachment would leave it

    def test_the_door_is_whole_where_the_roof_is_level_and_cut_where_it_slopes(self):
        sp = CW.spec(E.meta_of(self.p, self.cw))
        self.assertEqual([h["cell"] for h in CW.door_conflicts(sp)], ["1,0"])
        self.p.execute("model.set_property", {"id": self.cw, "key": "panel:1:0:type", "value": "Window"})
        self.p.execute("model.set_property", {"id": self.cw, "key": "panel:0:0:type", "value": "Door"})          # a door under the level part: no refusal
        self.assertEqual(CW.door_conflicts(CW.spec(E.meta_of(self.p, self.cw))), [])
        leaf = [r for r in self.p.scene_data()["objects"] if r["id"] == self.cw][0]["components"]
        self.assertTrue(any(r["part"] == "cell:0,0" for r in leaf))

    def test_the_door_can_be_moved_to_a_field_the_roof_does_not_cut(self):
        with self.assertRaises(BadRequest) as ctx:
            CW.check_spec(self.p, E.meta_of(self.p, self.cw), self.cw)
        move = next(f for f in ctx.exception.details["fixes"] if f["label"].startswith("Move the door to field"))
        self.assertEqual([st["params"]["key"] for st in move["steps"][:2]], ["panel:1:0:type", move["steps"][1]["params"]["key"]])      # the cut door becomes a window first
        self.assertEqual((move["steps"][0]["params"]["value"], move["steps"][1]["params"]["value"]), ("Window", "Door"))
        for st in move["steps"]:
            self.p.execute(st["op"], st["params"])
        self.assertEqual(CW.door_conflicts(CW.spec(E.meta_of(self.p, self.cw))), [])


if __name__ == "__main__":
    unittest.main()
