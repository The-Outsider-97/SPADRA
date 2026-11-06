"""The planar boolean engine behind the cuts a void makes in a floor or an extrusion. Beyond the hand-worked cases, every random pair of polygons is held to the
definition of the operation: a point is in the result exactly when it is in the operands the way the operation says, checked at thousands of points, and the areas
add up (A = A-B + A&B, A+B = A|B + A&B)."""
from __future__ import annotations

import math
import random
import unittest

from .. import planar as P


def square(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def rotated(cx, cy, r, turn, sides=4):
    return [(cx + r * math.cos(turn + 2 * math.pi * i / sides), cy + r * math.sin(turn + 2 * math.pi * i / sides)) for i in range(sides)]


def in_regions(regions, p):
    return any(P.inside(outer, p) and not any(P.inside(h, p) for h in holes) for outer, holes in regions)


def total(regions):
    return sum(P.region_area(r) for r in regions)


class HandWorked(unittest.TestCase):
    def test_a_corner_bitten_off_a_square(self):
        (outer, holes), = P.difference(square(0, 0, 4, 4), [square(3, 3, 6, 6)])
        self.assertEqual(holes, [])
        self.assertAlmostEqual(P.signed_area(outer), 15.0, places=9)
        self.assertEqual(len(outer), 6)                                                  # an L: the bite adds no vertices of its own beyond the two it makes
        self.assertGreater(P.signed_area(outer), 0)

    def test_a_notch_across_an_edge_is_one_outline(self):
        (outer, holes), = P.difference(square(0, 0, 6, 5), [square(-1, 1, 1, 2)])
        self.assertAlmostEqual(P.signed_area(outer), 29.0, places=9)
        self.assertEqual(holes, [])

    def test_a_void_wholly_inside_is_a_hole(self):
        (outer, holes), = P.difference(square(0, 0, 6, 5), [square(1, 1, 2, 2)])
        self.assertEqual(len(holes), 1)
        self.assertLess(P.signed_area(holes[0]), 0)
        self.assertAlmostEqual(P.region_area((outer, holes)), 29.0, places=9)

    def test_overlapping_voids_are_one_hole_by_their_union(self):
        (outer, holes), = P.difference(square(0, 0, 6, 5), [square(1, 1, 3, 3), square(2, 2, 4, 4)])
        self.assertEqual(len(holes), 1)
        self.assertAlmostEqual(P.region_area((outer, holes)), 30.0 - 7.0, places=9)

    def test_a_void_sharing_an_edge_flush_with_the_outline_makes_a_notch_not_a_sliver(self):
        (outer, holes), = P.difference(square(0, 0, 6, 5), [square(0, 0, 2, 5)])
        self.assertEqual(holes, [])
        self.assertEqual(len(outer), 4)
        self.assertAlmostEqual(P.signed_area(outer), 20.0, places=9)

    def test_a_void_sharing_an_edge_inside_makes_a_notch_in_the_far_side(self):
        (outer, holes), = P.difference(square(0, 0, 6, 5), [square(0, 1, 2, 2)])
        self.assertEqual(holes, [])
        self.assertAlmostEqual(P.signed_area(outer), 28.0, places=9)

    def test_a_void_across_the_middle_leaves_two_regions(self):
        regions = P.difference(square(0, 0, 6, 5), [square(2, -1, 3, 6)])
        self.assertEqual(len(regions), 2)
        self.assertAlmostEqual(total(regions), 25.0, places=9)

    def test_a_void_that_covers_everything_leaves_nothing(self):
        self.assertEqual(P.difference(square(0, 0, 6, 5), [square(-1, -1, 7, 6)]), [])

    def test_a_void_apart_changes_nothing(self):
        (outer, holes), = P.difference(square(0, 0, 6, 5), [square(10, 10, 11, 11)])
        self.assertEqual(holes, [])
        self.assertAlmostEqual(P.signed_area(outer), 30.0, places=9)

    def test_voids_that_touch_only_at_a_corner_have_no_single_answer_and_say_so(self):
        with self.assertRaises(P.PlanarError):
            P.difference(square(0, 0, 6, 6), [square(1, 1, 2, 2), square(2, 2, 3, 3)])          # the two holes meet at (2,2)
        with self.assertRaises(P.PlanarError):
            P.difference(square(0, 0, 6, 6), [[(3, 0), (5, 2), (3, 4), (1, 2)]])                 # a diamond whose lowest corner rests on the bottom edge

    def test_a_degenerate_outline_is_refused_by_name(self):
        with self.assertRaises(P.PlanarError):
            P.difference(square(0, 0, 6, 5), [[(1, 1), (2, 2), (3, 3)]])

    def test_union_of_two_squares_and_their_intersection(self):
        (outer, _), = P.union([square(0, 0, 2, 2), square(1, 1, 3, 3)])
        self.assertAlmostEqual(P.signed_area(outer), 7.0, places=9)
        (inner, _), = P.intersection(square(0, 0, 2, 2), square(1, 1, 3, 3))
        self.assertAlmostEqual(P.signed_area(inner), 1.0, places=9)

    def test_vertices_off_the_snap_grid_do_not_disturb_the_result(self):
        region, = P.difference(square(0, 0, 6, 5), [square(0.30000000001, 1.00000000002, 2.9999999999, 2.0)])
        self.assertAlmostEqual(P.region_area(region), 30.0 - 2.7, places=6)

    def test_the_orientation_of_the_inputs_does_not_matter(self):
        a, b = square(0, 0, 4, 4), square(3, 3, 6, 6)
        for aa in (a, a[::-1]):
            for bb in (b, b[::-1]):
                (outer, _), = P.difference(aa, [bb])
                self.assertAlmostEqual(P.signed_area(outer), 15.0, places=9)


class AgainstTheDefinition(unittest.TestCase):
    """Random polygons: squares, turned squares, triangles and hexagons of random size, so edges meet at every angle."""

    @staticmethod
    def shape(rng):
        kind = rng.choice(("box", "turned", "tri", "hex"))
        cx, cy = rng.uniform(1, 9), rng.uniform(1, 9)
        if kind == "box":
            w, h = rng.uniform(0.5, 5), rng.uniform(0.5, 5)
            return square(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
        return rotated(cx, cy, rng.uniform(0.7, 3.5), rng.uniform(0, 6.28), {"turned": 4, "tri": 3, "hex": 6}[kind])

    def check(self, seed, count):
        rng = random.Random(seed)
        a = self.shape(rng)
        clips = [self.shape(rng) for _ in range(count)]
        probes = [(rng.uniform(-3, 13), rng.uniform(-3, 13)) for _ in range(1500)]
        try:
            diff = P.difference(a, clips)
        except P.PlanarError:
            return "touch"                                                              # random shapes never touch exactly, but the engine is allowed to say so
        for p in probes:
            want = P.inside(a, p) and not any(P.inside(c, p) for c in clips)
            self.assertEqual(in_regions(diff, p), want, f"seed {seed}: point {p} for A minus {count} clips")
        union_clips = P.union(clips)
        for p in probes:
            self.assertEqual(in_regions(union_clips, p), any(P.inside(c, p) for c in clips), f"seed {seed}: point {p} in the union")
        inter = [P.intersection(a, c) for c in clips[:1]][0]
        self.assertAlmostEqual(total(diff), abs(P.signed_area(a)) - sum(P.region_area(r) for r in P.combine([a, *clips], lambda f: f[0] and any(f[1:]))), places=5)
        self.assertAlmostEqual(total(P.union([a, clips[0]])), abs(P.signed_area(a)) + abs(P.signed_area(clips[0])) - total(inter), places=5)    # operands are snapped to 0.1 µm, so areas agree to about a perimeter times 5e-8
        return "ok"

    def test_one_clip(self):
        results = [self.check(seed, 1) for seed in range(60)]
        self.assertGreater(results.count("ok"), 55)

    def test_several_overlapping_clips(self):
        results = [self.check(1000 + seed, 4) for seed in range(40)]
        self.assertGreater(results.count("ok"), 35)


if __name__ == "__main__":
    unittest.main()
