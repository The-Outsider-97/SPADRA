"""The view range engine (viewrange.py): absolute elevations from offsets, validation, classification and the geometric clip."""
from __future__ import annotations

import math
import unittest

from .. import viewrange as VR


def box(x0: float, y0: float, z0: float, x1: float, y1: float, z1: float, base: int = 0):
    """A closed box as an indexed triangle mesh, outward-facing, like the tessellated walls and slabs."""
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    faces = []
    for a, b, c, d in quads:
        faces += [(a + base, b + base, c + base), (a + base, c + base, d + base)]
    return v, faces


def merge(*meshes):
    verts, faces = [], []
    for v, f in meshes:
        offset = len(verts)
        verts += v
        faces += [tuple(i + offset for i in tri) for tri in f]
    return verts, faces


def area(loop):
    return abs(sum(loop[i][0] * loop[(i + 1) % len(loop)][1] - loop[(i + 1) % len(loop)][0] * loop[i][1] for i in range(len(loop))) / 2)


class Elevations(unittest.TestCase):
    def test_defaults_give_the_documented_absolute_elevations(self):
        r = VR.from_view({}, level_z=3.2)
        self.assertAlmostEqual(r.cut_z, 4.7)
        self.assertAlmostEqual(r.top_z, 6.2)
        self.assertAlmostEqual(r.bottom_z, 3.0)
        self.assertAlmostEqual(r.depth_z, 2.2)
        self.assertIsNone(r.problem())

    def test_the_cut_offset_is_measured_from_its_own_reference_level_and_the_rest_from_the_plans_level(self):
        r = VR.from_view({"cut_offset": 0.5, "range_top": 1.0, "range_bottom": -0.1, "range_depth": -3.0}, level_z=6.4, reference_z=3.2)
        self.assertAlmostEqual(r.cut_z, 3.7)          # reference + offset
        self.assertAlmostEqual(r.top_z, 4.7)          # cut + top
        self.assertAlmostEqual(r.bottom_z, 6.3)       # the plan's own level + bottom
        self.assertAlmostEqual(r.depth_z, 3.4)
        self.assertIn("below the bottom", r.problem() or "")           # a reference level that far down puts the cut below the bottom

    def test_negative_elevations_and_a_ground_level_below_zero(self):
        r = VR.from_view({"range_depth": -2.0}, level_z=-0.5)
        self.assertAlmostEqual(r.cut_z, 1.0)
        self.assertAlmostEqual(r.depth_z, -2.5)
        self.assertAlmostEqual(r.bottom_z, -0.7)
        self.assertIsNone(r.problem())

    def test_a_view_that_stores_nothing_or_junk_falls_back_to_the_defaults(self):
        r = VR.from_view({"cut_offset": "high", "range_top": None, "range_bottom": float("nan"), "range_depth": []}, level_z=0.0)
        self.assertEqual((r.cut_offset, r.top_offset, r.bottom_offset, r.depth_offset), (1.5, 1.5, -0.2, -1.0))

    def test_describe_distinguishes_offsets_absolute_elevations_and_depth_distance(self):
        d = VR.from_view({"cut_offset": 1.2, "range_depth": -3.0}, level_z=3.2).describe(level="L1")
        self.assertAlmostEqual(d["height"], 1.2)            # offset from the reference level
        self.assertAlmostEqual(d["cut_z"], 4.4)             # absolute
        self.assertAlmostEqual(d["depth"], 3.0)             # a positive distance below the level
        self.assertAlmostEqual(d["depth_z"], 0.2)           # absolute
        self.assertTrue(d["valid"])
        self.assertEqual(d["reference_level"], "L1")


class Validation(unittest.TestCase):
    base = VR.from_view({}, level_z=0.0)

    def test_every_relation_is_refused_with_its_own_sentence(self):
        for key, value, phrase in (("range_top", -0.1, "below the cut plane"), ("cut_offset", -0.5, "below the bottom"), ("range_depth", -0.1, "shallower")):
            with self.subTest(key):
                with self.assertRaises(VR.ViewRangeError) as caught:
                    VR.with_change(self.base, key, value)
                self.assertIn(phrase, str(caught.exception))

    def test_a_valid_change_returns_the_new_range_and_leaves_the_old_one_alone(self):
        new = VR.with_change(self.base, "range_depth", -3.0)
        self.assertAlmostEqual(new.depth_z, -3.0)
        self.assertAlmostEqual(self.base.depth_z, -1.0)
        self.assertAlmostEqual(VR.with_change(self.base, "cut_offset", 1.2).cut_z, 1.2)

    def test_not_a_number_and_absurd_values_are_refused(self):
        for bad in (float("nan"), float("inf"), "1.2", None, True):
            with self.subTest(bad):
                with self.assertRaises(VR.ViewRangeError):
                    VR.with_change(self.base, "cut_offset", bad)   # type: ignore[arg-type]
        with self.assertRaises(VR.ViewRangeError):
            VR.with_change(self.base, "range_depth", -5000.0)
        with self.assertRaises(VR.ViewRangeError):
            VR.with_change(self.base, "elevation", 1.0)

    def test_the_boundaries_may_coincide(self):
        r = VR.ViewRange(0.0, 0.0, cut_offset=0.0, top_offset=0.0, bottom_offset=0.0, depth_offset=0.0)
        self.assertIsNone(r.problem())


class Classification(unittest.TestCase):
    r = VR.from_view({}, level_z=3.2)         # depth 2.2, bottom 3.0, cut 4.7, top 6.2

    def test_the_table_of_the_specification(self):
        c = self.r.classify
        self.assertEqual(c(3.2, 6.4, on_level=True), VR.CUT)               # a wall of the level through the cut plane
        self.assertEqual(c(3.2, 6.4, on_level=False), VR.CUT)              # a tall wall of the storey below, also through it: geometry decides, not the level
        self.assertEqual(c(3.2, 4.0, on_level=True), VR.PROJECTED)         # a low wall of the level, wholly below the cut
        self.assertEqual(c(5.0, 6.0, on_level=True), VR.PROJECTED)         # an overhead beam in range
        self.assertEqual(c(0.0, 3.2, on_level=False), VR.CONTEXT)          # the storey below, down to the depth: halftone
        self.assertEqual(c(0.0, 2.0, on_level=False), VR.HIDDEN)           # below the view depth
        self.assertEqual(c(6.3, 9.0, on_level=True), VR.HIDDEN)            # above the top
        self.assertEqual(c(6.3, 9.0, on_level=False), VR.HIDDEN)

    def test_partial_overlap_counts_and_touching_does_not_cross(self):
        c = self.r.classify
        self.assertEqual(c(0.0, 2.3, on_level=False), VR.CONTEXT)          # its top 100 mm are inside the depth
        self.assertEqual(c(4.7, 5.0, on_level=True), VR.PROJECTED)         # sits on the cut plane: not cut
        self.assertEqual(c(4.0, 4.7, on_level=True), VR.PROJECTED)
        self.assertEqual(c(2.2, 2.2, on_level=False), VR.CONTEXT)          # a zero-thickness face exactly on the depth is within it

    def test_a_deeper_view_depth_brings_the_lower_storey_in_and_a_shallower_one_removes_it(self):
        deep = VR.with_change(self.r, "range_depth", -3.0)
        self.assertEqual(deep.classify(0.0, 3.2, on_level=False), VR.CONTEXT)
        shallow = VR.from_view({"range_depth": -0.2}, level_z=3.2)
        self.assertEqual(shallow.classify(0.0, 2.9, on_level=False), VR.HIDDEN)


def covers(fp, x, y):
    """Whether the footprint paints the point: inside a filled triangle, or inside an odd number of filled rings."""
    for a, b, c in fp.triangles:
        d1 = (b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0])
        d2 = (c[0] - b[0]) * (y - b[1]) - (c[1] - b[1]) * (x - b[0])
        d3 = (a[0] - c[0]) * (y - c[1]) - (a[1] - c[1]) * (x - c[0])
        if d1 >= -1e-12 and d2 >= -1e-12 and d3 >= -1e-12:
            return True
    inside = 0
    for ring in fp.rings:
        hit = False
        for i in range(len(ring)):
            (x0, y0), (x1, y1) = ring[i], ring[(i + 1) % len(ring)]
            if (y0 > y) != (y1 > y) and x < (x1 - x0) * (y - y0) / (y1 - y0) + x0:
                hit = not hit
        inside += hit
    return inside % 2 == 1


class Clipping(unittest.TestCase):
    def test_a_box_clipped_to_a_slab_keeps_only_that_slab(self):
        v, f = box(0, 0, 0, 2, 2, 4)
        pieces = VR.clip_triangles(v, f, 1.0, 3.0)
        zs = [p[2] for poly in pieces for p in poly]
        self.assertAlmostEqual(min(zs), 1.0)
        self.assertAlmostEqual(max(zs), 3.0)
        self.assertEqual(VR.clip_triangles(v, f, 5.0, 6.0), [])


class Sections(unittest.TestCase):
    def test_a_closed_solid_cuts_to_one_ring_and_nothing_loose(self):
        rings, loose = VR.section(*box(0, 0, 0, 3, 2, 4), 2.0)
        self.assertEqual((len(rings), loose), (1, []))
        self.assertAlmostEqual(area(rings[0]), 6.0)
        self.assertEqual(len(rings[0]), 4)

    def test_a_single_sheet_cuts_to_loose_lines_and_a_plane_that_misses_cuts_nothing(self):
        sheet = ([(0, 0, 0), (4, 0, 4), (4, 2, 4), (0, 2, 0)], [(0, 1, 2), (0, 2, 3)])
        rings, loose = VR.section(*sheet, 2.0)
        self.assertEqual(rings, [])
        self.assertEqual(len(loose), 1)
        (a, b) = loose[0]
        self.assertAlmostEqual(a[0], 2.0)
        self.assertAlmostEqual(b[0], 2.0)
        self.assertEqual(VR.section(*sheet, 9.0), ([], []))


class Footprints(unittest.TestCase):
    def test_a_wall_of_the_storey_below_has_no_horizontal_face_in_the_slab_yet_still_has_a_footprint(self):
        fp = VR.footprint(*box(0, 0, 0, 4, 1, 3.2), 1.0, 2.5)
        self.assertEqual(fp.triangles, ())                # its own top and bottom are outside the slab ...
        self.assertTrue(covers(fp, 2.0, 0.5))             # ... and the cross sections at the slab's planes still cover it
        self.assertFalse(covers(fp, 2.0, 1.5))
        self.assertFalse(covers(fp, 5.0, 0.5))

    def test_a_whole_box_is_covered_once_its_diagonals_and_doubled_faces_are_gone(self):
        fp = VR.footprint(*box(0, 0, 0, 3, 2, 3), 0.0, 3.0)
        self.assertTrue(covers(fp, 1.5, 1.0))
        self.assertEqual(len(fp.triangles), 2)            # top and bottom coincide in plan
        self.assertEqual(len(fp.lines), 4)                # the outline: four sides, no diagonal
        self.assertEqual(fp.rings, ())                    # the planes touch its faces: no cross section is added where there is nothing to add

    def test_nothing_in_the_range_is_nothing(self):
        self.assertFalse(VR.footprint(*box(0, 0, 0, 3, 2, 3), 4.0, 5.0))
        self.assertFalse(VR.footprint(*box(0, 0, 0, 3, 2, 3), -5.0, -1.0))

    def test_overlapping_and_touching_solids_are_covered_without_a_courtyard_being_filled(self):
        ring = merge(box(0, 0, 0, 4, 1, 3), box(0, 3, 0, 4, 4, 3), box(0, 1, 0, 1, 3, 3), box(3, 1, 0, 4, 3, 3))
        fp = VR.footprint(*ring, 0.5, 2.0)
        for point in ((2, 0.5), (2, 3.5), (0.5, 2), (3.5, 2)):
            self.assertTrue(covers(fp, *point), point)
        self.assertFalse(covers(fp, 2.0, 2.0))            # the courtyard
        self.assertFalse(covers(fp, 5.0, 2.0))

    def test_an_l_of_two_overlapping_boxes_is_covered_where_either_covers(self):
        fp = VR.footprint(*merge(box(0, 0, 0, 4, 1, 3), box(0, 0.5, 0, 1, 3, 3)), 0.5, 2.0)
        self.assertTrue(covers(fp, 3.0, 0.5))
        self.assertTrue(covers(fp, 0.5, 2.5))
        self.assertFalse(covers(fp, 3.0, 2.0))

    def test_a_ramp_inside_a_slab_is_only_the_part_between_the_planes(self):
        ramp = ([(0, 0, 0), (4, 0, 4), (4, 2, 4), (0, 2, 0)], [(0, 1, 2), (0, 2, 3)])
        fp = VR.footprint(*ramp, 1.0, 3.0)
        self.assertTrue(covers(fp, 2.0, 1.0))
        self.assertFalse(covers(fp, 0.5, 1.0))
        self.assertFalse(covers(fp, 3.5, 1.0))
        contours = sorted(round(a[0], 6) for a, b in fp.lines if abs(a[0] - b[0]) < 1e-9)
        self.assertEqual(contours, [1.0, 3.0])            # where the range's planes cut the sheet

    def test_a_leaning_wall_moves_with_the_slab(self):
        v = [(0, 0, 0), (4, 0, 0), (4, 0.2, 0), (0, 0.2, 0), (1, 0, 2), (5, 0, 2), (5, 0.2, 2), (1, 0.2, 2)]
        quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
        f = [t for a, b, c, d in quads for t in ((a, b, c), (a, c, d))]
        low, high = VR.footprint(v, f, 0.0, 1.0), VR.footprint(v, f, 1.0, 2.0)
        self.assertTrue(covers(low, 0.1, 0.1) and covers(low, 4.4, 0.1) and not covers(low, 4.6, 0.1))
        self.assertFalse(covers(high, 0.4, 0.1))
        self.assertTrue(covers(high, 0.6, 0.1) and covers(high, 4.9, 0.1) and not covers(high, 5.1, 0.1))

    def test_the_payload_is_scaled_into_the_drawing_unit(self):
        fp = VR.footprint(*box(0, 0, 0, 2, 1, 3), 0.0, 3.0)
        payload = fp.payload(1000.0)
        self.assertEqual(len(payload["tris"]), len(fp.triangles))
        self.assertEqual(max(c for t in payload["tris"] for c in t), 2000.0)
        self.assertEqual(sorted({round(c) for l in payload["lines"] for c in l}), [0, 1000, 2000])

    def test_degenerate_input_does_not_raise(self):
        self.assertFalse(VR.footprint([], [], 0, 1))
        self.assertFalse(VR.footprint([(0, 0, 0)], [(0, 5, 9)], 0, 1))                      # indices that do not exist
        self.assertFalse(VR.footprint(*box(0, 0, 0, 1, 1, 1), 2.0, 1.0))                    # an upside-down range
        self.assertFalse(VR.footprint([(0, 0, 0), (1, 0, 0), (2, 0, 0)], [(0, 1, 2)], 0, 1).triangles)   # a zero-area face


class Extents(unittest.TestCase):
    def test_extent_of_a_mesh(self):
        self.assertEqual(VR.extent([(0, 0, -1.5), (1, 1, 2.5), (0, 1, 0)]), (-1.5, 2.5))
        self.assertIsNone(VR.extent([]))


if __name__ == "__main__":
    unittest.main()
