"""Stair design in SPADRA (P1-03): the riser interval, deterministic choice, flights and landings made from the ordinary stair element, infeasible
requests refused with the reason, and a headroom check against the real triangles above the stair."""
from __future__ import annotations

import math
import unittest

from applications.SPADRA import stairs as SR
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest


class Design(unittest.TestCase):
    def test_interval_is_ceil_and_floor(self):
        self.assertEqual(SR.riser_interval(3.2, SR.RULES), (16, 32))
        self.assertEqual(SR.riser_interval(3.0, {**SR.RULES, "riser_min": 0.17, "riser_max": 0.18}), (17, 17))

    def test_every_candidate_keeps_its_riser_inside_the_limits_and_the_choice_is_deterministic(self):
        d = SR.design(3.2)
        self.assertEqual(d["status"], "feasible")
        for c in d["candidates"]:
            if c["feasible"]:
                self.assertTrue(SR.RULES["riser_min"] - 1e-9 <= c["riser"] <= SR.RULES["riser_max"] + 1e-9)
                self.assertAlmostEqual(c["riser"] * c["risers"], 3.2)
        self.assertEqual(SR.design(3.2)["chosen"], d["chosen"])
        self.assertEqual(d["chosen"]["risers"], 18)                           # the first count whose 2R+T hits the middle of the comfort band
        self.assertEqual(d["chosen"]["flights"], [9, 9])                      # more than 16 risers: two equal flights and a landing
        self.assertAlmostEqual(d["chosen"]["comfort"], 0.625)

    def test_infeasible_says_which_limit_makes_it_so(self):
        d = SR.design(0.5, rules={**SR.RULES, "riser_min": 0.17, "riser_max": 0.18})
        self.assertEqual(d["status"], "infeasible")
        self.assertIsNone(d["chosen"])
        self.assertIn("No whole number of risers", d["reasons"][0])
        d = SR.design(3.2, available=2.0)                                      # too little room for the treads
        self.assertEqual(d["status"], "infeasible")
        self.assertTrue(d["failing_checks"])

    def test_bad_rules_are_refused(self):
        with self.assertRaises(BadRequest):
            SR.rules_from({"riser_min": 250, "riser_max": 200})
        with self.assertRaises(BadRequest):
            SR.rules_from({"nonsense": 1})
        with self.assertRaises(BadRequest):
            SR.design(0.01)


class InTheModel(unittest.TestCase):
    def setUp(self):
        self.p = Project.create("Stairs")
        self.l0, self.l1 = (self.p.manifest["levels"][i]["id"] for i in (0, 1))

    def x(self, op, **kw):
        return self.p.execute(op, kw)["result"]

    def test_two_flights_and_a_landing_meet_exactly_and_undo_is_one_step(self):
        before = len(self.p.undo_stack)
        r = self.x("model.design_stair", level=self.l0, start=[0, 0], end=[9000, 0], width=1000)
        self.assertEqual((len(r["flights"]), len(r["landings"])), (2, 1))
        self.assertEqual(len(self.p.undo_stack), before + 1)
        a, b = (SR.frame(self.p, i) for i in r["flights"])
        n, per = r["design"]["risers"], r["design"]["flights"]
        self.assertEqual(sum(per), n)                                          # 9 m of room: the design uses it, with more, shallower risers
        riser = 3.2 / n
        self.assertAlmostEqual(a["riser"], riser)
        self.assertAlmostEqual(b["riser"], riser)
        self.assertAlmostEqual(a["top"], per[0] * riser, places=9)                  # flight one ends at the landing...
        self.assertAlmostEqual(b["base"], a["top"], places=9)                  # ...where flight two begins...
        self.assertAlmostEqual(b["top"], 3.2, places=9)                        # ...and reaches the level above
        land = self.p.model.get(r["landings"][0])["metadata"]
        self.assertAlmostEqual(land["spadra:offset"], per[0] * riser)
        self.p.undo()
        self.assertEqual([i for i in (r["flights"] + r["landings"]) if i in set(__import__("applications.SPADRA.elements", fromlist=["x"]).element_ids(self.p))], [])

    def test_a_short_site_refuses_and_creates_nothing(self):
        rev = self.p.revision
        n = len(self.p.model.query_ids(limit=1000))
        with self.assertRaises(BadRequest) as cm:
            self.x("model.design_stair", level=self.l0, start=[0, 0], end=[1500, 0])
        self.assertIn("No stair fits", str(cm.exception))
        self.assertEqual((self.p.revision, len(self.p.model.query_ids(limit=1000))), (rev, n))

    def test_one_flight_when_the_rule_allows_it(self):
        r = self.x("model.design_stair", level=self.l0, start=[0, 0], end=[7000, 0], rules={"max_risers_per_flight": 30})
        self.assertEqual((len(r["flights"]), len(r["landings"])), (1, 0))
        f = SR.frame(self.p, r["flights"][0])
        self.assertAlmostEqual(f["height"], 3.2)
        self.assertEqual(f["risers"], r["design"]["risers"])

    def test_headroom_sees_a_slab_above_the_stair_and_not_one_beside_it(self):
        r = self.x("model.design_stair", level=self.l0, start=[0, 0], end=[9000, 0], rules={"max_risers_per_flight": 30})
        sid = r["flights"][0]
        clear = SR.headroom(self.p, sid)
        self.assertTrue(clear["ok"] and clear["minimum_clearance"] is None, clear)
        self.x("model.add_floor", level=self.l1, points=[[0, 3000], [9000, 3000], [9000, 6000], [0, 6000]])            # beside the stair
        self.assertTrue(SR.headroom(self.p, sid)["ok"])
        slab = self.x("model.add_floor", level=self.l1, points=[[0, -500], [9000, -500], [9000, 500], [0, 500]], thickness=200)["id"]   # over it
        h = SR.headroom(self.p, sid)
        self.assertFalse(h["ok"])
        self.assertEqual(h["worst"]["element"], slab)
        f = SR.frame(self.p, sid)
        k = h["worst"]["tread"]
        self.assertAlmostEqual(h["minimum_clearance"], (3.2 - 0.2) - (k * f["riser"]), places=6)                       # underside of the slab minus the tread's height


if __name__ == "__main__":
    unittest.main()
