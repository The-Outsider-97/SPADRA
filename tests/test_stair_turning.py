"""L and U stairs (the straight-flights-only limit of the stair design): two ordinary flights and one ordinary landing, turned a quarter or a half turn either way.
The checks are made on the plan polygons and on the real meshes, not on the numbers the design reported."""
from __future__ import annotations

import math
import unittest

from applications.SPADRA import elements as E
from applications.SPADRA import stairs as SR
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest

LAYOUTS = (("L", "left"), ("L", "right"), ("U", "left"), ("U", "right"))


def interior_overlap(a, b):
    """Area of the intersection of two axis-aligned rectangles given as polygons (stairs here are rectangles at 0 or 90 degrees; checked by the bounding box)."""
    ax, ay = [q[0] for q in a], [q[1] for q in a]
    bx, by = [q[0] for q in b], [q[1] for q in b]
    w = min(max(ax), max(bx)) - max(min(ax), min(bx))
    h = min(max(ay), max(by)) - max(min(ay), min(by))
    return max(w, 0.0) * max(h, 0.0)


def box(poly):
    xs, ys = [q[0] for q in poly], [q[1] for q in poly]
    return min(xs), min(ys), max(xs), max(ys)


def touches(a, b, tol=1e-6):
    """True when two axis-aligned rectangles share part of an edge (they meet without overlapping)."""
    ax0, ay0, ax1, ay1 = box(a)
    bx0, by0, bx1, by1 = box(b)
    x_gap = max(ax0 - bx1, bx0 - ax1)
    y_gap = max(ay0 - by1, by0 - ay1)
    return (abs(x_gap) <= tol and y_gap < -tol) or (abs(y_gap) <= tol and x_gap < -tol)


class Base(unittest.TestCase):
    def setUp(self):
        self.p = Project.create("Turning stairs")
        self.l0 = self.p.manifest["levels"][0]["id"]

    def make(self, layout, turn="left", **kw):
        params = {"level": self.l0, "start": [0, 0], "end": [4500, 0], "width": 1000, "layout": layout, "turn": turn, **kw}
        return self.p.execute("model.design_stair", params)["result"]

    def polygon(self, oid):
        return SR.plan_item(self.p, oid, 1.0)["polygon"]

    def landing_polygon(self, fid):
        return [tuple(q) for q in self.p.model.get(fid)["metadata"]["spadra:boundary"]]

    def z_range(self, oid):
        zs = [v[2] for tri in SR._world_triangles(self.p, oid) for v in tri]
        return min(zs), max(zs)


class Geometry(Base):
    def test_heights_meet_at_the_landing_and_the_top_level(self):
        for layout, turn in LAYOUTS:
            with self.subTest(layout=layout, turn=turn):
                p = Project.create("T")
                self.p, self.l0 = p, p.manifest["levels"][0]["id"]
                r = self.make(layout, turn)
                a, b = (SR.frame(self.p, i) for i in r["flights"])
                n1, n2 = r["design"]["flights"]
                riser = 3.2 / sum(r["design"]["flights"])
                self.assertEqual((a["risers"], b["risers"]), (n1, n2))
                self.assertAlmostEqual(a["riser"], riser)
                self.assertAlmostEqual(b["riser"], riser)
                self.assertAlmostEqual(a["top"], n1 * riser, places=9)        # flight one rises to the landing ...
                self.assertAlmostEqual(b["base"], a["top"], places=9)          # ... flight two starts at that height ...
                self.assertAlmostEqual(b["top"], 3.2, places=9)                # ... and reaches the level above
                land = self.p.model.get(r["landings"][0])["metadata"]
                self.assertAlmostEqual(land["spadra:offset"], n1 * riser, places=9)
                self.assertAlmostEqual(self.z_range(r["landings"][0])[1], n1 * riser, places=9)        # the landing's top face is the height flight one steps up to
                self.assertAlmostEqual(self.z_range(r["flights"][1])[0], 0.0 + n1 * riser, places=9)   # flight two's solid starts at the landing height
                self.assertAlmostEqual(self.z_range(r["flights"][1])[1], 3.2 - riser, places=9)        # and its last tread is one riser below the floor above

    def test_flights_and_landing_tile_without_overlap_and_touch_each_other(self):
        for layout, turn in LAYOUTS:
            with self.subTest(layout=layout, turn=turn):
                p = Project.create("T")
                self.p, self.l0 = p, p.manifest["levels"][0]["id"]
                r = self.make(layout, turn)
                f1, f2 = (self.polygon(i) for i in r["flights"])
                land = self.landing_polygon(r["landings"][0])
                self.assertAlmostEqual(interior_overlap(f1, f2), 0.0, places=9)
                self.assertAlmostEqual(interior_overlap(f1, land), 0.0, places=9)
                self.assertAlmostEqual(interior_overlap(f2, land), 0.0, places=9)
                self.assertTrue(touches(f1, land), "flight one must meet the landing")
                self.assertTrue(touches(f2, land), "flight two must meet the landing")
                self.assertEqual(self.p.check()["issues"], [])

    def test_an_l_stair_turns_a_quarter_turn_and_a_u_stair_comes_back_alongside(self):
        for turn, sign in (("left", 1), ("right", -1)):
            self.setUp()
            l = self.make("L", turn)
            b1, b2 = box(self.polygon(l["flights"][0])), box(self.polygon(l["flights"][1]))
            self.assertAlmostEqual(b2[2] - b2[0], 1.0)                                            # flight two is one stair-width wide in x: it runs along y
            self.assertGreater(sign * ((b2[1] + b2[3]) / 2 - (b1[1] + b1[3]) / 2), 0)             # on the side of the turn
            self.setUp()
            u = self.make("U", turn)
            c1, c2 = box(self.polygon(u["flights"][0])), box(self.polygon(u["flights"][1]))
            self.assertAlmostEqual(c2[2] - c2[0], c1[2] - c1[0])                                  # same extent along the run: it comes back alongside
            self.assertAlmostEqual(abs((c2[1] + c2[3]) / 2 - (c1[1] + c1[3]) / 2), 1.0 + 0.1)     # one width plus the default 100 mm well
            self.assertGreater(sign * ((c2[1] + c2[3]) / 2 - (c1[1] + c1[3]) / 2), 0)

    def test_left_and_right_are_mirror_images(self):
        left = self.make("L", "left")
        lb = [box(self.polygon(i)) for i in left["flights"]]
        p = Project.create("T")
        self.p, self.l0 = p, p.manifest["levels"][0]["id"]
        right = self.make("L", "right")
        rb = [box(self.polygon(i)) for i in right["flights"]]
        for (x0, y0, x1, y1), (u0, v0, u1, v1) in zip(lb, rb):
            self.assertAlmostEqual(x0, u0, places=9)
            self.assertAlmostEqual(x1, u1, places=9)
            self.assertAlmostEqual(y0, -v1, places=9)
            self.assertAlmostEqual(y1, -v0, places=9)

    def test_the_well_gap_is_a_parameter(self):
        r = self.make("U", "left", gap=300)
        c1, c2 = box(self.polygon(r["flights"][0])), box(self.polygon(r["flights"][1]))
        self.assertAlmostEqual(c2[1] - c1[3], 0.3, places=9)
        self.assertEqual(r["gap_m"], 0.3)
        self.assertAlmostEqual(self.landing_polygon(r["landings"][0])[2][1] - self.landing_polygon(r["landings"][0])[0][1], 1.0 + 0.3 + 1.0, places=9)

    def test_the_first_arm_uses_the_room_it_was_given(self):
        r = self.make("L", "left")
        first = (r["design"]["flights"][0] - 1) * r["design"]["tread"] + r["landing"]["depth"]
        self.assertAlmostEqual(first, 4.5, places=9)                                              # treads + landing fill the 4.5 m drawn line
        wide = self.make("L", "left", width=1400, name="Wide")
        self.assertAlmostEqual(wide["landing"]["depth"], 1.4)                                     # a landing is at least as deep as the stair is wide

    def test_headroom_works_on_both_flights(self):
        r = self.make("U", "left")
        for fid in r["flights"]:
            self.assertTrue(SR.headroom(self.p, fid)["ok"])

    def test_one_undo_step_removes_everything(self):
        before = len(self.p.undo_stack)
        r = self.make("L", "left")
        self.assertEqual(len(self.p.undo_stack), before + 1)
        self.p.undo()
        alive = set(E.element_ids(self.p))
        self.assertEqual([i for i in r["flights"] + r["landings"] if i in alive], [])


class Refusals(Base):
    def assertRefused(self, **kw):
        rev, ids = self.p.revision, len(self.p.model.query_ids(limit=1000))
        with self.assertRaises(BadRequest) as cm:
            self.make(**kw)
        self.assertEqual((self.p.revision, len(self.p.model.query_ids(limit=1000))), (rev, ids))      # nothing is left behind
        return str(cm.exception)

    def test_too_little_room(self):
        self.assertIn("No stair fits", self.assertRefused(layout="L", end=[1800, 0]))

    def test_too_many_risers_for_two_flights(self):
        self.assertIn("No stair fits", self.assertRefused(layout="U", risers=40))

    def test_too_few_risers_for_two_flights(self):
        self.assertIn("No stair fits", self.assertRefused(layout="L", risers=3))

    def test_bad_layout_turn_and_gap(self):
        self.assertIn("layout must be one of", self.assertRefused(layout="spiral"))
        self.assertIn("turn must be", self.assertRefused(layout="L", turn="up"))
        with self.assertRaises(BadRequest):
            self.make("U", "left", gap=-5)

    def test_the_failing_checks_are_named(self):
        with self.assertRaises(BadRequest) as cm:
            self.make("L", end=[1800, 0])
        names = {c["name"] for cand in cm.exception.details["design"]["candidates"] for c in cand["checks"] if not c["ok"]}
        self.assertIn("tread", names)                                 # the short room squeezes the tread below its limit, as it does for a straight stair


class Design(unittest.TestCase):
    def test_straight_design_is_unchanged(self):
        d = SR.design(3.2)
        self.assertEqual((d["chosen"]["risers"], d["chosen"]["flights"], d["chosen"]["landings"]), (18, [9, 9], 1))
        self.assertEqual(d["chosen"]["layout"], "straight")

    def test_turning_layouts_have_two_flights_and_one_landing(self):
        for layout in ("L", "U"):
            d = SR.design(3.2, layout=layout, available=4.5, width=1.0)
            self.assertEqual(d["status"], "feasible")
            self.assertEqual((len(d["chosen"]["flights"]), d["chosen"]["landings"], d["chosen"]["layout"]), (2, 1, layout))
            self.assertEqual(sum(d["chosen"]["flights"]), d["chosen"]["risers"])

    def test_the_odd_riser_goes_to_the_first_flight(self):
        self.assertEqual(SR._turning_split(21), [11, 10])
        self.assertEqual(SR._turning_split(22), [11, 11])
        self.assertEqual(SR._turning_split(3), [3])


if __name__ == "__main__":
    unittest.main()
