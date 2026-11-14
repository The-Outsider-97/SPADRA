"""Door and frame depth offsets are independent, and the plan's swing is the 3D leaf's swing: the same hinge axis, the same effective leaf width, the same side."""
from __future__ import annotations

import itertools
import math
import tempfile
from pathlib import Path

from applications.SPADRA import elements as E
from applications.SPADRA.project import Project

from .test_bim import Case

NS = "spadra"
F = 1000.0                                                                         # the test project draws in millimetres


class FrameAndLeaf(Case):
    def setUp(self) -> None:
        super().setUp()
        self.w = self.wall(a=(0, 0), b=(6000, 0))
        self.d = self.run_("model.add_door", wall=self.w, offset=3.0, width=900, height=2100)["id"]
        self.plan = next(v for v in self.p.manifest["views"] if v.get("level_id") == self.l1 and v["kind"] == "plan")

    def box(self, oid):
        lo, hi = self.p.model.bounds(oid)
        return lo, hi

    def y_of(self, oid):
        lo, hi = self.box(oid)
        return round(lo[1], 6), round(hi[1], 6)

    def symbol(self, oid):
        return next(i for i in self.p.plan_overlay(self.plan["id"]) if i["id"] == oid)["symbol"]

    def hinge_3d(self, oid):
        t = self.p.model.world_transform(oid)
        return t[3], t[7]

    def free_point_3d(self, oid):
        """The leaf's free edge on the hinge face line, from the solid itself: local (2 * centre x, 0), the leaf's own frame, through the object's transform."""
        g = self.p.model.get(oid)["geometry"]
        t = self.p.model.world_transform(oid)
        x, y = 2 * g["center"][0], g["center"][1] + (1 if self.meta(oid)[f"{NS}:swing"] > 0 else -1) * g["size"][1] / 2
        return t[0] * x + t[1] * y + t[3], t[4] * x + t[5] * y + t[7]

    # ------------------------------------------------------------------------------------------------ independent offsets
    def test_the_leaf_offset_moves_the_leaf_and_not_the_frame(self):
        frame = self.run_("model.add_opening_profile", opening=self.d, width=50, depth=100)["id"]
        f0, l0 = self.y_of(frame), self.y_of(self.d)
        self.run_("model.set_property", id=self.d, key="leaf_depth_offset", value=30)
        self.assertEqual(self.y_of(frame), f0)
        self.assertEqual(self.y_of(self.d), (round(l0[0] + 0.03, 6), round(l0[1] + 0.03, 6)))

    def test_the_frame_offset_moves_the_frame_and_not_the_leaf(self):
        frame = self.run_("model.add_opening_profile", opening=self.d, width=50, depth=100)["id"]
        f0, l0 = self.y_of(frame), self.y_of(self.d)
        self.run_("model.set_property", id=self.d, key="frame_depth_offset", value=-40)
        self.assertEqual(self.y_of(self.d), l0)
        self.assertEqual(self.y_of(frame), (round(f0[0] - 0.04, 6), round(f0[1] - 0.04, 6)))

    def test_positive_and_negative_offsets_are_independent_in_every_combination(self):
        frame = self.run_("model.add_opening_profile", opening=self.d, width=50, depth=100)["id"]
        base_f, base_l = self.y_of(frame), self.y_of(self.d)
        for fo, lo in itertools.product((-60, 0, 45), (-30, 0, 70)):
            self.run_("model.set_property", id=self.d, key="frame_depth_offset", value=fo)
            self.run_("model.set_property", id=self.d, key="leaf_depth_offset", value=lo)
            self.assertEqual(self.y_of(frame), (round(base_f[0] + fo / 1000, 6), round(base_f[1] + fo / 1000, 6)), (fo, lo))
            self.assertEqual(self.y_of(self.d), (round(base_l[0] + lo / 1000, 6), round(base_l[1] + lo / 1000, 6)), (fo, lo))

    def test_the_legacy_single_offset_still_moves_both_and_old_projects_look_the_same(self):
        frame = self.run_("model.add_opening_profile", opening=self.d, width=50, depth=100)["id"]
        self.run_("model.set_property", id=self.d, key="depth_ref", value="near")
        self.run_("model.set_property", id=self.d, key="depth_offset", value=20)
        self.assertEqual(self.y_of(self.d), (-0.08, -0.03))                         # as before the split
        self.assertAlmostEqual(self.box(frame)[0][1], -0.105, places=6)
        meta = self.meta(self.d)
        del meta[f"{NS}:frame_depth_offset"], meta[f"{NS}:leaf_depth_offset"]       # a project saved before the split has only depth_offset
        self.assertEqual((E.frame_depth_offset(meta), E.leaf_depth_offset(meta)), (0.02, 0.02))
        E.regenerate_wall(self.p, self.w)
        self.assertEqual(self.y_of(self.d), (-0.08, -0.03))

    def test_the_properties_panel_offers_both_offsets_and_applies_them(self):
        rows = self.rows(self.d)
        self.assertIn("frame_depth_offset", rows)
        self.assertIn("leaf_depth_offset", rows)
        self.run_("model.set_property", id=self.d, key="leaf_depth_offset", value=12.5)
        self.assertEqual(self.rows(self.d)["leaf_depth_offset"]["value"], 12.5)
        self.assertEqual(self.rows(self.d)["frame_depth_offset"]["value"], 0)

    def test_the_offsets_follow_a_wall_thickness_change_and_survive_save_and_reopen(self):
        frame = self.run_("model.add_opening_profile", opening=self.d, width=50, depth=100)["id"]
        self.run_("model.set_property", id=self.d, key="depth_ref", value="near")
        self.run_("model.set_property", id=self.d, key="frame_depth_offset", value=10)
        self.run_("model.set_property", id=self.d, key="leaf_depth_offset", value=-15)
        self.run_("model.set_property", id=self.w, key="thickness", value=400)
        self.assertAlmostEqual(self.box(frame)[0][1], -0.2 + 0.01, places=6)         # near face flush, then +10 mm
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.spadra"
            self.p.save(path)
            q = Project.open(path)
        meta = q.model.get(self.d)["metadata"]
        self.assertEqual((meta[f"{NS}:frame_depth_offset"], meta[f"{NS}:leaf_depth_offset"]), (0.01, -0.015))

    # ------------------------------------------------------------------------------------------------ the swing in 2D is the leaf in 3D
    def check_plan_matches_3d(self, oid):
        sym = self.symbol(oid)
        hx, hy = self.hinge_3d(oid)
        self.assertAlmostEqual(sym["hinge"][0] / F, hx, places=6)                    # the arc starts at the object's origin: the hinge axis
        self.assertAlmostEqual(sym["hinge"][1] / F, hy, places=6)
        fx, fy = self.free_point_3d(oid)
        self.assertAlmostEqual(math.hypot(fx - hx, fy - hy) * F, sym["radius"], places=4)      # the arc's radius is the 3D leaf's width
        closed = math.atan2(fy - hy, fx - hx)
        ends = [sym["start_angle"], sym["end_angle"]]
        self.assertTrue(any(abs(((a - closed + math.pi) % (2 * math.pi)) - math.pi) < 1e-6 for a in ends), (closed, ends))   # the arc runs from the closed leaf

    def test_the_arc_starts_at_the_hinge_axis_of_the_leaf_not_the_wall_face(self):
        sym = self.symbol(self.d)
        self.assertEqual([round(v, 6) for v in sym["hinge"]], [2550.0, 25.0])        # the leaf's own swing-side face, 25 mm off the centre line, not the face at 125
        self.check_plan_matches_3d(self.d)

    def test_plan_and_3d_agree_for_every_hinge_swing_wall_angle_and_offset(self):
        for k, (hinge, swing, ang, off) in enumerate(itertools.product(("start", "end"), ("left", "right"), (0, 30, 135, -90), (0, 40, -40))):
            a = math.radians(ang)
            wall = self.wall(a=(0, 10000 * (k + 1)), b=(6000 * math.cos(a), 10000 * (k + 1) + 6000 * math.sin(a)))
            door = self.run_("model.add_door", wall=wall, offset=3.0, width=900, height=2100, hinge=hinge, swing=swing)["id"]
            self.run_("model.set_property", id=door, key="leaf_depth_offset", value=off)
            self.run_("model.set_property", id=door, key="frame_depth_offset", value=-off)
            with self.subTest(hinge=hinge, swing=swing, angle=ang, offset=off):
                self.check_plan_matches_3d(door)

    def test_the_effective_leaf_width_is_the_same_in_plan_elevation_and_3d_when_there_is_a_frame(self):
        self.run_("model.add_opening_profile", opening=self.d, width=60, depth=100)
        g = self.p.model.get(self.d)["geometry"]
        self.assertAlmostEqual(g["size"][0], 0.9 - 2 * (0.06 + 0.003), places=6)     # 3D: the opening less the frame and its clearance
        sym = self.symbol(self.d)
        self.assertAlmostEqual(sym["radius"], g["size"][0] * F, places=6)            # 2D radius = the real leaf
        self.assertAlmostEqual(sym["leaf"]["width"], g["size"][0] * F, places=6)
        leaf = next(s for s in sym["shapes"] if s["role"] == "leaf")
        xs = sorted({round(q[0], 6) for q in leaf["p"]})
        self.assertEqual(xs, [round((2.55 + 0.063) * F, 6), round((3.45 - 0.063) * F, 6)])    # the leaf drawn in the opening is the leaf, not the nominal opening
        self.check_plan_matches_3d(self.d)
        elev = E.elevation_symbol(self.p, self.d, (0.0, 1.0))
        self.assertAlmostEqual(abs(elev["head"][0] - elev["apex"][0]), g["size"][0], places=6)   # the elevation's swing symbol spans the same leaf

    def test_frame_and_leaf_depth_show_in_the_symbol_and_the_leaf_stands_in_the_frame(self):
        self.run_("model.add_opening_profile", opening=self.d, width=50, depth=100)
        self.run_("model.set_property", id=self.d, key="frame_depth_offset", value=20)
        self.run_("model.set_property", id=self.d, key="leaf_depth_offset", value=-10)
        sym = self.symbol(self.d)
        self.assertAlmostEqual(sym["leaf"]["frame_y"], 20.0, places=6)
        self.assertAlmostEqual(sym["leaf"]["y"], -10.0, places=6)
        leaf = next(s for s in sym["shapes"] if s["role"] == "leaf")
        self.assertEqual(sorted({round(q[1], 6) for q in leaf["p"]}), [-35.0, 15.0])

    def test_a_mirrored_wall_keeps_door_frame_and_leaf_where_they_were(self):
        frame = self.run_("model.add_opening_profile", opening=self.d, width=50, depth=100)["id"]
        self.run_("model.set_property", id=self.d, key="leaf_depth_offset", value=30)
        self.run_("model.set_property", id=self.d, key="frame_depth_offset", value=-20)
        before = (self.box(self.d), self.box(frame), [round(v, 6) for v in self.symbol(self.d)["hinge"]])
        self.run_("model.flip_wall", id=self.w)
        after = (self.box(self.d), self.box(frame), [round(v, 6) for v in self.symbol(self.d)["hinge"]])
        for a, b in zip(before[0] + before[1], after[0] + after[1]):
            for u, v in zip(a, b):
                self.assertAlmostEqual(u, v, places=6)
        self.assertEqual(before[2], after[2])                                         # the arc still starts at the same place in the room
        self.check_plan_matches_3d(self.d)

    def test_the_swing_direction_and_hinge_edit_the_same_model_for_plan_and_3d(self):
        self.run_("model.set_property", id=self.d, key="swing", value="right")
        self.run_("model.set_property", id=self.d, key="hinge", value="end")
        sym = self.symbol(self.d)
        self.assertEqual([round(v, 6) for v in sym["hinge"]], [3450.0, -25.0])
        self.check_plan_matches_3d(self.d)
        self.assertLess(self.free_point_3d(self.d)[0], self.hinge_3d(self.d)[0])      # an end hinge: the leaf lies toward the wall's start

    # ------------------------------------------------------------------------------------------------ the opening angle
    def test_the_plan_draws_the_leaf_open_by_the_configured_angle_about_the_same_hinge(self):
        for deg in (45, 90, 135):
            self.run_("model.set_property", id=self.d, key="opening_angle", value=deg)
            sym = self.symbol(self.d)
            self.assertAlmostEqual(math.degrees(sym["end_angle"] - sym["start_angle"]), deg, places=6)
            dx, dy = sym["leaf_end"][0] - sym["hinge"][0], sym["leaf_end"][1] - sym["hinge"][1]
            self.assertAlmostEqual(math.hypot(dx, dy), 900.0, places=6)
            self.assertAlmostEqual(math.degrees(math.atan2(dy, dx)), deg, places=6)    # start hinge, swing left: counter-clockwise from the closed leaf along +x
            self.assertEqual([round(v, 6) for v in sym["hinge"]], [2550.0, 25.0])
        for bad in (0, 4, 171, 360):
            with self.assertRaises(Exception):
                self.run_("model.set_property", id=self.d, key="opening_angle", value=bad)

    def test_the_swing_plan_uses_the_doors_own_opening_angle_by_default(self):
        from applications.SPADRA import door_swing as DS
        self.run_("model.set_property", id=self.d, key="opening_angle", value=60)
        self.assertEqual(DS.plan(self.p, self.d, check=False)["requested_degrees"], 60.0)
        self.assertEqual(DS.plan(self.p, self.d, 90, check=False)["requested_degrees"], 90.0)

    # ------------------------------------------------------------------------------------------------ warnings
    def test_a_frame_that_sticks_out_of_the_wall_is_reported_like_a_leaf(self):
        self.run_("model.add_opening_profile", opening=self.d, width=50, depth=100)
        self.run_("model.set_property", id=self.d, key="frame_depth_offset", value=200)
        msgs = [i["message"] for i in E.check(self.p) if i["severity"] == "warning"]
        self.assertTrue(any("frame stands" in m for m in msgs), msgs)
