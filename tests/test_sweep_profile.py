"""Sweep with a user-defined profile: a drawn closed polygon, pivot alignment, angle, offsets, mirror, corner radius, fixed to an element."""
from __future__ import annotations

import math

from applications.SPADRA import forms as FX
from applications.tools.errors import BadRequest

from .test_bim import Case

PATH = [[0, 0], [4000, 0]]
L_SHAPE = [[0, 0], [100, 0], [100, 20], [20, 20], [20, 100], [0, 100]]            # mm, drawn as a closed loop: an angle iron 100 x 100
RECT = [[0, 0], [200, 0], [200, 100], [0, 100]]


class UserProfile(Case):
    def sweep(self, polygon=RECT, **extra):
        return self.run_("model.add_sweep", points=PATH, level=self.l1, profile="user", polygon=polygon, **extra)["id"]

    def bounds(self, oid):
        lo, hi = self.p.model.world_bounds(oid)
        return [round(v, 6) for v in lo], [round(v, 6) for v in hi]

    def test_the_polygon_becomes_the_section_and_the_pivot_defaults_to_the_centre(self):
        s = self.sweep()
        lo, hi = self.bounds(s)
        self.assertEqual((lo[1], hi[1]), (-0.1, 0.1))                           # 200 mm across the path, centred on it
        self.assertEqual((lo[2], hi[2]), (-0.05, 0.05))                         # 100 mm high, centred on the path height
        self.assertAlmostEqual(self.vol(s), 0.2 * 0.1 * 4.0, places=6)
        self.assertEqual(self.meta(s)["spadra:profile_source"]["pivot"], "center")

    def test_every_one_of_the_nine_pivots_puts_that_point_of_the_polygon_on_the_path(self):
        expect = {"bottom-left": (0.0, 0.2, 0.0, 0.1), "top-right": (-0.2, 0.0, -0.1, 0.0), "bottom": (-0.1, 0.1, 0.0, 0.1), "left": (0.0, 0.2, -0.05, 0.05)}
        for pivot, (u0, u1, v0, v1) in expect.items():
            s = self.sweep(pivot=pivot)
            lo, hi = self.bounds(s)
            self.assertEqual((lo[1], hi[1], lo[2], hi[2]), (u0, u1, v0, v1), pivot)

    def test_changing_the_pivot_in_properties_moves_the_solid(self):
        s = self.sweep()
        self.run_("model.set_property", id=s, key="pivot", value="bottom-left")
        lo, hi = self.bounds(s)
        self.assertEqual((lo[2], hi[2]), (0.0, 0.1))
        with self.assertRaises(BadRequest):
            self.run_("model.set_property", id=s, key="pivot", value="middle")

    def test_the_angle_turns_the_section_about_the_pivot(self):
        s = self.sweep(angle=90)
        lo, hi = self.bounds(s)
        self.assertEqual((lo[1], hi[1]), (-0.05, 0.05))
        self.assertEqual((lo[2], hi[2]), (-0.1, 0.1))
        self.assertAlmostEqual(self.vol(s), 0.2 * 0.1 * 4.0, places=6)

    def test_mirror_flips_an_asymmetric_profile_across_the_path(self):
        s = self.sweep(polygon=L_SHAPE, pivot="bottom-left")
        a = self.bounds(s)
        self.run_("model.set_property", id=s, key="mirror", value="Yes")
        b = self.bounds(s)
        self.assertEqual((a[0][1], a[1][1]), (0.0, 0.1))
        self.assertEqual((b[0][1], b[1][1]), (-0.1, 0.0))
        self.assertAlmostEqual(self.vol(s), (100 * 20 + 80 * 20) / 1e6 * 4.0, places=6)         # still a valid solid, same volume

    def test_offsets_move_the_solid_in_x_y_and_z(self):
        s = self.sweep()
        self.run_("model.set_property", id=s, key="offset_x", value=500)
        self.run_("model.set_property", id=s, key="offset_y", value=-300)
        self.run_("model.set_property", id=s, key="offset_z", value=1000)
        lo, hi = self.bounds(s)
        self.assertEqual((lo[0], hi[0]), (0.5, 4.5))
        self.assertEqual((lo[1], hi[1]), (-0.4, -0.2))
        self.assertAlmostEqual(lo[2], 0.95, places=6)

    def test_rounded_corners_take_off_the_expected_area(self):
        s = self.sweep(radius=20)
        r = 0.02
        self.assertAlmostEqual(self.vol(s), (0.2 * 0.1 - 4 * (1 - math.pi / 4) * r * r) * 4.0, delta=1e-4)       # arcs are polygons: a hair under the circle
        self.assertLess(self.vol(s), 0.2 * 0.1 * 4.0)

    def test_a_radius_too_big_for_the_edges_is_limited_not_broken(self):
        s = self.sweep(radius=5000)                                             # far more than the shape can take: it is rounded as far as fits
        self.assertGreater(self.vol(s), 0.0)
        lo, hi = self.bounds(s)
        self.assertEqual((lo[1], hi[1]), (-0.1, 0.1))

    def test_a_polygon_that_crosses_itself_is_refused(self):
        with self.assertRaises(BadRequest):
            self.sweep(polygon=[[0, 0], [100, 100], [100, 0], [0, 100]])

    def test_fixed_to_an_element_the_sweep_moves_with_it_and_is_released_where_it_stands(self):
        w = self.wall(a=(0, 5000), b=(4000, 5000))
        s = self.sweep(fixed_to=w)
        before = self.bounds(s)
        self.run_("model.move", id=w, dx=0, dy=1.0, dz=0)
        after = self.bounds(s)
        self.assertAlmostEqual(after[0][1] - before[0][1], 1.0, places=6)
        self.run_("model.set_property", id=s, key="fixed_to", value="")
        self.assertIsNone(self.meta(s).get("spadra:fixed_to"))
        self.assertEqual(self.bounds(s), after)                                 # released: it stays where it stands
        self.run_("model.move", id=w, dx=0, dy=1.0, dz=0)
        self.assertEqual(self.bounds(s), after)

    def test_deleting_the_element_releases_the_sweep_in_place(self):
        w = self.wall(a=(0, 5000), b=(4000, 5000))
        s = self.sweep(fixed_to=w)
        self.run_("model.move", id=w, dx=0, dy=0.5, dz=0)
        placed = self.bounds(s)
        self.run_("model.delete", id=w)
        self.assertIsNone(self.meta(s).get("spadra:fixed_to"))
        self.assertEqual(self.bounds(s), placed)
        self.p.undo()
        self.assertEqual(self.meta(s).get("spadra:fixed_to"), w)

    def test_properties_show_pivot_angle_mirror_radius_and_offsets_instead_of_width_and_height(self):
        s = self.sweep()
        rows = self.rows(s)
        for key in ("pivot", "angle", "mirror", "radius", "offset_x", "offset_y", "offset_z", "fixed_to"):
            self.assertIn(key, rows)
        self.assertNotIn("width", rows)
        self.assertNotIn("height", rows)
        self.assertEqual(len(rows["pivot"]["choices"]), 9)

    def test_the_other_profiles_still_work_as_before(self):
        s = self.run_("model.add_sweep", points=PATH, level=self.l1, shape="rectangle", width=200, height=100)["id"]
        self.assertIn("width", self.rows(s))
        self.assertAlmostEqual(self.vol(s), 0.2 * 0.1 * 4.0, places=6)
