"""Doors and windows: vertical position, depth reference and offsets, the leaf in plan, and sliding doors (schuifdeuren)."""
from __future__ import annotations

from applications.SPADRA import elements as E
from applications.tools.errors import BadRequest

from .test_bim import Case

NS = "spadra"
WALL_T, LEAF = 0.25, E.DOOR_LEAF


class Offsets(Case):
    def setUp(self) -> None:
        super().setUp()
        self.w = self.wall(a=(0, 0), b=(6000, 0))
        self.d = self.run_("model.add_door", wall=self.w, offset=3.0, width=900, height=2100)["id"]
        self.plan = next(v for v in self.p.manifest["views"] if v.get("level_id") == self.l1 and v["kind"] == "plan")

    def box(self, oid):
        lo, hi = self.p.model.bounds(oid)
        return lo, hi

    def item(self, oid):
        return next(i for i in self.p.plan_overlay(self.plan["id"]) if i["id"] == oid)

    # ---------------------------------------------------------------- vertical position
    def test_a_door_can_be_raised_and_its_size_and_the_cut_do_not_change(self):
        v0 = self.vol(self.w)
        self.run_("model.set_property", id=self.d, key="sill", value=300)
        lo, hi = self.box(self.d)
        self.assertAlmostEqual(lo[2], 0.3, places=6)
        self.assertAlmostEqual(hi[2], 2.4, places=6)                                   # still 2.1 m tall
        self.assertAlmostEqual(self.vol(self.w), v0, places=6)                          # the same area is cut out of the wall, higher up
        self.assertAlmostEqual(self.meta(self.d)[f"{NS}:width"], 0.9)

    def test_base_elevation_is_measured_from_the_level_and_the_head_is_calculated(self):
        self.run_("model.set_property", id=self.w, key="base_offset", value=200)
        self.run_("model.set_property", id=self.d, key="elevation", value=500)         # 500 mm above the level, the wall starts 200 mm above it
        self.assertAlmostEqual(self.meta(self.d)[f"{NS}:sill"], 0.3)
        rows = self.rows(self.d)
        self.assertEqual(rows["elevation"]["value"], 500)
        self.assertEqual(rows["Head Elevation (above level)"]["value"], "2600 mm")
        lo, hi = self.box(self.d)
        self.assertAlmostEqual(lo[2], 0.5, places=6)
        self.run_("model.set_property", id=self.w, key="base_offset", value=0)          # the wall moves down: the door goes with it
        self.assertAlmostEqual(self.box(self.d)[0][2], 0.3, places=6)

    def test_a_head_above_the_wall_or_a_base_below_it_is_refused_and_changes_nothing(self):
        for key, value in (("sill", 1200), ("elevation", -50)):
            with self.assertRaises(BadRequest):
                self.run_("model.set_property", id=self.d, key=key, value=value)
        self.assertAlmostEqual(self.meta(self.d)[f"{NS}:sill"], 0.0)

    def test_a_window_and_a_door_both_move_vertically(self):
        win = self.run_("model.add_window", wall=self.w, offset=1.0, width=1000, height=900, sill=900)["id"]
        self.run_("model.move", id=win, dx=0, dy=0, dz=0.3)
        self.run_("model.move", id=self.d, dx=0, dy=0, dz=0.2)
        self.assertAlmostEqual(self.meta(win)[f"{NS}:sill"], 1.2)
        self.assertAlmostEqual(self.meta(self.d)[f"{NS}:sill"], 0.2)

    def test_the_offsets_survive_save_reopen_and_duplication(self):
        self.run_("model.set_property", id=self.d, key="sill", value=150)
        self.run_("model.set_property", id=self.d, key="depth_ref", value="far")
        self.run_("model.set_property", id=self.d, key="depth_offset", value=-10)
        import tempfile
        from pathlib import Path

        from applications.SPADRA.project import Project
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.spadra"
            self.p.save(path)
            q = Project.open(path)
        meta = q.model.get(self.d)["metadata"]
        self.assertEqual((meta[f"{NS}:sill"], meta[f"{NS}:depth_ref"], meta[f"{NS}:depth_offset"]), (0.15, "far", -0.01))

    # ---------------------------------------------------------------- depth
    def leaf_y(self, oid=None):
        lo, hi = self.box(oid or self.d)
        return round(lo[1], 6), round(hi[1], 6)

    def test_the_leaf_stands_on_the_centre_line_flush_with_either_face_or_at_a_custom_offset(self):
        self.assertEqual(self.leaf_y(), (-0.025, 0.025))
        self.run_("model.set_property", id=self.d, key="depth_ref", value="near")      # the near face is the right-hand face of the wall's path (start to end)
        self.assertEqual(self.leaf_y(), (-0.125, -0.075))
        self.run_("model.set_property", id=self.d, key="depth_ref", value="far")
        self.assertEqual(self.leaf_y(), (0.075, 0.125))
        self.run_("model.set_property", id=self.d, key="depth_ref", value="custom")
        self.run_("model.set_property", id=self.d, key="depth_offset", value=50)
        self.assertEqual(self.leaf_y(), (0.025, 0.075))
        self.run_("model.set_property", id=self.d, key="depth_ref", value="near")      # the offset is added to the reference in every case
        self.assertEqual(self.leaf_y(), (-0.075, -0.025))

    def test_the_depth_reference_follows_a_change_of_wall_thickness(self):
        self.run_("model.set_property", id=self.d, key="depth_ref", value="near")
        self.run_("model.set_property", id=self.w, key="thickness", value=400)
        self.assertEqual(self.leaf_y(), (-0.2, -0.15))

    def test_a_frame_moves_with_it_and_the_leaf_stays_in_its_frame(self):
        frame = self.run_("model.add_opening_profile", opening=self.d, width=50, depth=100)["id"]
        self.run_("model.set_property", id=self.d, key="depth_ref", value="near")
        flo, fhi = self.box(frame)
        self.assertAlmostEqual(flo[1], -0.125, places=6)                                # flush with the near face
        self.assertAlmostEqual(fhi[1], -0.025, places=6)
        self.assertEqual(self.leaf_y(), (-0.1, -0.05))                                  # the leaf is centred in the frame's depth
        self.assertAlmostEqual((flo[1] + fhi[1]) / 2, -0.075, places=6)
        self.run_("model.set_property", id=self.d, key="depth_offset", value=20)
        self.assertAlmostEqual(self.box(frame)[0][1], -0.105, places=6)
        self.assertEqual(self.leaf_y(), (-0.08, -0.03))
        self.assertAlmostEqual(self.vol(self.w), 6 * WALL_T * 3.2 - 0.9 * 2.1 * WALL_T, places=6)   # the cut in the wall is unchanged

    def test_a_leaf_that_sticks_out_of_the_wall_is_a_warning_and_an_absurd_offset_is_refused(self):
        self.run_("model.set_property", id=self.d, key="depth_offset", value=200)
        warnings = [i for i in E.check(self.p) if i["severity"] == "warning"]
        self.assertTrue(any("outside the faces of its wall" in i["message"] for i in warnings))
        with self.assertRaises(BadRequest):
            self.run_("model.set_property", id=self.d, key="depth_offset", value=9000)
        self.assertEqual([i for i in E.check(self.p) if i["severity"] == "error"], [])

    def test_a_window_takes_the_same_references(self):
        win = self.run_("model.add_window", wall=self.w, offset=1.0, width=1000, height=900, sill=900, depth_ref="far")["id"]
        self.assertEqual(self.leaf_y(win), (0.095, 0.125))                              # 30 mm of glass against the far face

    # ---------------------------------------------------------------- the leaf in plan
    def test_a_hinged_door_shows_its_leaf_in_the_opening_and_open_as_a_rectangle(self):
        sym = self.item(self.d)["symbol"]
        leaves = [s for s in sym["shapes"] if s["role"] == "leaf"]
        self.assertEqual(len(leaves), 1)
        xs = sorted({round(q[0], 6) for q in leaves[0]["p"]})
        ys = sorted({round(q[1], 6) for q in leaves[0]["p"]})
        self.assertEqual((xs, ys), ([2550.0, 3450.0], [-25.0, 25.0]))                    # the closed leaf: the opening's width, the leaf's thickness
        self.assertEqual(len(sym["poly"]), 4)                                           # the open leaf is a rectangle too, not a line
        self.assertIn("radius", sym)

    def test_the_closed_leaf_is_drawn_even_where_the_view_hides_the_swing(self):
        self.p.view(self.plan["id"])["show_opening_direction"] = False
        sym = self.item(self.d)["symbol"]
        self.assertTrue(any(s["role"] == "leaf" for s in sym["shapes"]))
        self.assertNotIn("hinge", sym)

    def test_a_fixed_door_panel_is_drawn_too(self):
        self.run_("model.add_panel", id=self.d)
        self.run_("model.set_property", id=self.d, key="panel:p1:operation", value="fixed")
        roles = [s["role"] for s in self.item(self.d)["symbol"]["shapes"]]
        self.assertEqual(roles.count("leaf"), 2)

    def test_a_door_raised_above_the_cut_plane_is_not_cut_in_plan(self):
        self.run_("model.set_property", id=self.d, key="height", value=1200)
        self.run_("model.set_property", id=self.d, key="sill", value=1600)             # starts above the default 1.5 m cut plane
        self.assertFalse(self.item(self.d)["cut"])


class Sliding(Case):
    def setUp(self) -> None:
        super().setUp()
        self.w = self.wall(a=(0, 0), b=(6000, 0))
        self.plan = next(v for v in self.p.manifest["views"] if v.get("level_id") == self.l1 and v["kind"] == "plan")

    def door(self, **sliding):
        return self.run_("model.add_door", wall=self.w, offset=3.0, width=1800, height=2100, sliding=sliding)["id"]

    def shapes(self, oid, role=None):
        sym = next(i for i in self.p.plan_overlay(self.plan["id"]) if i["id"] == oid)["symbol"]
        return [s for s in sym["shapes"] if role is None or s["role"] == role]

    def xs(self, shape):
        return sorted({round(q[0], 6) for q in shape["p"]})

    def ys(self, shape):
        return sorted({round(q[1], 6) for q in shape["p"]})

    def test_a_single_surface_leaf_runs_on_a_track_in_front_of_the_face_and_slides_the_way_it_says(self):
        d = self.door(leaves=1, direction="right", mount="surface", face=1)
        self.assertEqual(self.meta(d)[f"{NS}:panels"][0]["operation"], "slide")
        leaf, = self.shapes(d, "leaf")
        self.assertEqual(self.xs(leaf), [2100.0, 3900.0])
        self.assertEqual(self.ys(leaf), [145.0, 195.0])                                 # on the far face: 125 + 20 gap, then the leaf's 50
        open_leaf, = self.shapes(d, "open")
        self.assertEqual(self.xs(open_leaf), [300.0, 2100.0])                          # "right" seen from the far face is toward the wall's start
        self.assertTrue(open_leaf["dash"])
        track, = self.shapes(d, "track")
        self.assertEqual([round(q[0], 6) for q in track["p"]], [300.0, 3900.0])
        arrow, = self.shapes(d, "direction")
        self.assertLess(arrow["p"][1][0], arrow["p"][0][0])
        lo, hi = self.p.model.bounds(d)
        self.assertAlmostEqual(hi[2], 2.1 + 0.01 + E.RAIL, places=6)                    # the track stands above the head

    def test_left_and_right_swap_with_the_face_the_leaf_runs_on(self):
        far = self.run_("model.add_door", wall=self.w, offset=1.5, width=1800, height=2100, sliding={"leaves": 1, "direction": "left", "face": 1})["id"]
        near = self.run_("model.add_door", wall=self.w, offset=4.5, width=1800, height=2100, sliding={"leaves": 1, "direction": "left", "face": -1})["id"]
        self.assertEqual(self.xs(self.shapes(far, "open")[0]), [2400.0, 4200.0])        # "left" seen from the far face is toward the wall's end
        self.assertEqual(self.xs(self.shapes(near, "open")[0]), [1800.0, 3600.0])       # and from the near face, toward its start
        self.assertEqual(self.ys(self.shapes(near, "leaf")[0]), [-195.0, -145.0])

    def test_a_pocket_door_slides_into_the_wall_and_has_no_track_in_3d(self):
        d = self.door(leaves=1, direction="right", mount="pocket")
        leaf, = self.shapes(d, "leaf")
        self.assertEqual(self.ys(leaf), [-25.0, 25.0])                                  # inside the wall's thickness
        cavity, = self.shapes(d, "pocket")
        self.assertTrue(cavity["dash"])
        self.assertEqual(self.shapes(d, "track"), [])
        self.assertAlmostEqual(self.p.model.bounds(d)[1][2], 2.1, places=6)
        self.assertAlmostEqual(self.vol(self.w), 6 * WALL_T * 3.2 - 1.8 * 2.1 * WALL_T, places=6)

    def test_a_bi_parting_pair_has_two_half_leaves_that_part_toward_each_end(self):
        d = self.door(leaves=2, direction="both", mount="surface")
        leaves = self.shapes(d, "leaf")
        self.assertEqual([self.xs(l) for l in leaves], [[2100.0, 3000.0], [3000.0, 3900.0]])
        opens = self.shapes(d, "open")
        self.assertEqual([self.xs(o) for o in opens], [[1200.0, 2100.0], [3900.0, 4800.0]])
        self.assertEqual(len({tuple(self.ys(l)) for l in leaves}), 1)                  # on one rail

    def test_two_leaves_going_one_way_run_on_two_rails(self):
        d = self.door(leaves=2, direction="left", mount="surface", face=1)
        leaves = self.shapes(d, "leaf")
        self.assertEqual(len({tuple(self.ys(l)) for l in leaves}), 2)
        self.assertEqual(len(self.meta(d)[f"{NS}:sliding"].keys()), 5)
        self.assertEqual(self.meta(d)[f"{NS}:sliding"]["layers"], 2)

    def test_the_door_can_be_switched_between_hinged_and_sliding_and_edited_afterwards(self):
        d = self.run_("model.add_door", wall=self.w, offset=3.0, width=1800, height=2100)["id"]
        self.run_("model.set_property", id=d, key="door_type", value="sliding")
        self.assertEqual(self.meta(d)[f"{NS}:type"], "Sliding Door")
        self.run_("model.set_property", id=d, key="sliding_leaves", value="2")
        self.run_("model.set_property", id=d, key="sliding_direction", value="both")
        self.assertEqual(len(self.meta(d)[f"{NS}:panels"]), 2)
        self.run_("model.set_property", id=d, key="sliding_mount", value="pocket")
        self.assertEqual(self.meta(d)[f"{NS}:sliding"]["mount"], "pocket")
        self.run_("model.set_property", id=d, key="width", value=2000)                  # the leaves keep their proportions
        self.assertAlmostEqual(sum(q["width"] for q in self.meta(d)[f"{NS}:panels"]), 2.0)
        self.run_("model.set_property", id=d, key="door_type", value="hinged")
        self.assertIsNone(self.meta(d).get(f"{NS}:panels"))
        self.assertEqual(self.meta(d)[f"{NS}:type"], "Single Flush Door")

    def test_a_bad_configuration_is_refused(self):
        with self.assertRaises(BadRequest):
            self.door(leaves=1, direction="both")
        with self.assertRaises(BadRequest):
            self.door(leaves=3, direction="left")
        with self.assertRaises(BadRequest):
            self.run_("model.add_window", wall=self.w, offset=3.0, width=1800, height=1200, sliding={"leaves": 1})

    def test_a_leaf_with_no_room_to_slide_is_a_warning_not_an_error(self):
        d = self.run_("model.add_door", wall=self.w, offset=1.0, width=1800, height=2100, sliding={"leaves": 1, "direction": "right", "face": 1})["id"]
        issues = E.check(self.p)
        self.assertTrue(any(i["severity"] == "warning" and "sliding leaf needs" in i["message"] for i in issues))
        self.assertEqual([i for i in issues if i["severity"] == "error"], [])

    def test_a_sliding_door_in_a_section_cuts_the_leaf_and_the_track(self):
        d = self.door(leaves=1, direction="right", mount="surface")
        self.assertTrue(self.p.model.tessellate(d)["faces"])
        self.assertGreater(self.p.model.volume(d, unit="m"), 0.0)
