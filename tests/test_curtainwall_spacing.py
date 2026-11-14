"""Curtain-wall spacing that does not get in the way (follow-up to the issue sheet of 11-11-2026): a spacing rule never makes a member it cannot build
(a remainder too small for a panel is folded into its neighbour), and a real conflict says exactly which two members clash, where, how much room is
missing, and offers corrections that have been checked to work, as commands with exact values."""
from __future__ import annotations

import unittest

from applications.SPADRA import curtainwall as CW
from applications.SPADRA import elements as E
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest

from .harness import ServerCase


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project.create("Spacing")
        self.l0 = self.p.manifest["levels"][0]["id"]

    def cmd(self, op, **params):
        return self.p.execute(op, params)["result"]

    def cw(self, **kw):
        return self.cmd("model.add_curtainwall", **{"start": [0, 0], "end": [6000, 0], "level": self.l0, "height": 3000, **kw})["id"]

    def axes(self, oid, d="h"):
        return [round(x * 1000, 3) for x in CW.axes(CW.spec(E.meta_of(self.p, oid)), d)]


class Remainder(Case):
    def test_a_remainder_too_small_for_a_panel_is_folded_into_the_last_bay(self):
        oid = self.cw(height=4291)                                       # 2100 mm transoms would leave 41 mm at the top
        self.assertEqual(self.axes(oid), [25.0, 2125.0, 4266.0])           # one transom; the top bay takes the 41 mm
        self.cmd("model.set_property", id=oid, key="height", value=4500)  # with room for a panel the remainder is a bay again
        self.assertEqual(self.axes(oid), [25.0, 2125.0, 4225.0, 4475.0])

    def test_every_alignment_folds_its_sliver_and_never_makes_one(self):
        w = 0.05
        for align, expect in (("start", [2.1, 2.141]), ("end", [2.141, 2.1]), ("centre", [2.1205, 2.1205])):
            with self.subTest(align=align):
                got = CW.bays(4.241, "centre", 2.1, align, w + CW.MIN_FIELD)
                self.assertEqual([round(b, 6) for b in got], expect)
        self.assertEqual(len(CW.bays(4.241, "centre", 2.1, "start")), 3)       # without a minimum the old behaviour is kept for callers that want it

    def test_making_the_wall_lower_or_the_floor_higher_no_longer_fails(self):
        floor = self.cmd("model.add_floor", points=[[-500, -500], [6500, -500], [6500, 500], [-500, 500]], level=self.l0)
        oid = self.cw(height=4400)
        for h in range(4250, 4400, 7):                                     # every height in between, slivers included
            self.cmd("model.set_property", id=oid, key="height", value=h)
            self.assertTrue(CW._fits(CW.spec(E.meta_of(self.p, oid))))
        self.assertTrue(floor)


class Conflict(Case):
    def conflict(self, oid, key, value):
        with self.assertRaises(BadRequest) as cm:
            self.cmd("model.set_property", id=oid, key=key, value=value)
        return cm.exception

    def test_the_error_names_the_wall_the_two_members_and_the_room_missing(self):
        oid = self.cw()
        self.cmd("model.set_property", id=oid, key="h_mode", value="count")
        e = self.conflict(oid, "h_value", 60)
        self.assertIn("Curtain Wall: the bottom member (25 mm above the base) and transom 1", e.message)
        self.assertEqual((e.details["element"], e.details["direction"], e.details["needed_mm"]), (oid, "h", 50.0))
        self.assertLess(e.details["gap_mm"], 50)
        self.assertEqual(e.details["positions_mm"][0], 25.0)

    def test_every_offered_correction_really_makes_the_change_buildable(self):
        oid = self.cw()
        self.cmd("model.set_property", id=oid, key="h_mode", value="count")
        e = self.conflict(oid, "h_value", 60)
        labels = [f["label"] for f in e.details["fixes"]]
        self.assertEqual(labels, ["Use 28 interior transoms, the most that leave room for panels"])     # removing one of 60 would not be enough: not offered
        rev = self.p.revision
        for st in e.details["fixes"][0]["steps"]:
            self.p.execute(st["op"], st["params"])
        self.assertEqual(self.p.revision, rev + 1)
        self.assertEqual(len(self.axes(oid)), 30)

    def test_a_correction_is_applied_before_the_change_it_unblocks(self):
        oid = self.cw(v_mode="count", v_value=1)                                         # one mullion in the middle (3000 mm)
        self.cmd("model.set_property", id=oid, key="line:v:0", value="removed")          # taken out,
        self.cmd("model.set_property", id=oid, key="add_line:v", value=3060)             # and one added by hand 60 mm from where it was
        e = self.conflict(oid, "line:v:0", "present")                                    # putting the first back clashes with the new one
        self.assertIn("mullion 1 (3,000 mm from the start) and mullion 2 (3,060 mm)", e.message)
        fixes = {f["label"]: f["steps"] for f in e.details["fixes"]}
        self.assertEqual(set(fixes), {"Remove mullion 1 (at 3,000 mm)", "Remove mullion 2 (at 3,060 mm)"})     # the rule is not the cause: no spacing change offered
        for st in fixes["Remove mullion 2 (at 3,060 mm)"]:
            self.p.execute(st["op"], st["params"])
        self.cmd("model.set_property", id=oid, key="line:v:0", value="present")          # the original change now goes through
        self.assertEqual(self.axes(oid, "v"), [25.0, 3000.0, 5975.0])


class OverHttp(ServerCase):
    def test_the_corrections_reach_the_client(self):
        a = self.api
        a.post("/api/project/new", {"name": "Spacing", "unit": "mm"})
        level = a.get("/api/project/tree")["levels"][0]["id"]
        oid = a.cmd("model.add_curtainwall", {"start": [0, 0], "end": [6000, 0], "level": level, "height": 4291})["result"]["id"]
        a.cmd("model.set_property", {"id": oid, "key": "h_mode", "value": "count"})
        status, body = a.call("POST", "/api/command", {"op": "model.set_property", "params": {"id": oid, "key": "h_value", "value": 80}})
        self.assertEqual(status, 400)
        err = body["error"]
        self.assertEqual(err["details"]["element"], oid)
        self.assertTrue(err["details"]["fixes"] and err["details"]["fixes"][0]["steps"][0]["params"]["id"] == oid)


if __name__ == "__main__":
    unittest.main()
