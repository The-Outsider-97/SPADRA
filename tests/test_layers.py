"""Layered walls, floors and roofs: a stack of layers in one element, each with its own openings, edges and place in a section."""
from __future__ import annotations

import math

from applications.SPADRA import documentation as D
from applications.SPADRA import layers as LY
from applications.tools.errors import BadRequest

from .test_bim import Case
from .test_documentation import area


class Walls(Case):
    def setUp(self) -> None:
        super().setUp()
        self.w = self.wall(a=(0, 0), b=(6000, 0))                              # 250 thick, 3.2 m high, along x
        self.door = self.run_("model.add_door", wall=self.w, offset=2.0, width=900, height=2100)["id"]
        self.win = self.run_("model.add_window", wall=self.w, offset=4.5, width=1200, height=1200, sill=900)["id"]

    def add(self, **kw):
        return self.run_("model.add_layer", id=self.w, **kw)["layer"]

    def layers(self, oid=None):
        return LY.stack(self.p, oid or self.w)

    def test_a_wall_is_a_stack_of_one_until_a_layer_is_added(self):
        self.assertEqual([l["id"] for l in self.layers()], ["main"])
        self.assertIsNone(self.meta(self.w).get("spadra:layers"))
        lid = self.add(material="Plasterboard", thickness=12.5)
        self.assertEqual(len(self.layers()), 2)
        self.assertEqual(self.layers()[1]["id"], lid)

    def test_contextual_left_and_right_insertion_and_cut_inheritance(self):
        left = self.add(material="Plasterboard", side="left", openings="none", thickness=25)
        right = self.add(material="Mineral wool", side="right", openings="all", thickness=40)
        self.assertEqual([r["id"] for r in self.layers()], [left, "main", right])
        self.assertEqual(LY.inherited(self.layers()[0], [self.door, self.win]), [])
        self.assertEqual(LY.inherited(self.layers()[-1], [self.door, self.win]), [self.door, self.win])
        self.assertGreater(self.vol(self.w), 0)
        self.p.undo()
        self.assertEqual([r["id"] for r in self.layers()], [left, "main"])

    def test_3d_wall_face_controls_are_anchored_to_host_orientation(self):
        obj = next(o for o in self.p.scene_data()["objects"] if o["id"] == self.w)
        points = {row["side"]: row["point"] for row in obj["layer_points"]}
        self.assertEqual(set(points), {"left", "right"})
        self.assertGreater(points["left"][1], points["right"][1])

    def test_the_thickness_is_the_sum_of_the_layers_and_the_volume_agrees(self):
        before = self.vol(self.w)
        self.add(material="Plasterboard", thickness=12.5)
        self.add(material="Mineral wool", thickness=100)
        self.assertAlmostEqual(self.meta(self.w)["spadra:thickness"], 0.25 + 0.0125 + 0.1, places=9)
        self.assertAlmostEqual(self.vol(self.w), (0.3625) * 3.2 * 6.0 - 0.9 * 2.1 * 0.3625 - 1.2 * 1.2 * 0.3625, places=5)
        self.assertGreater(self.vol(self.w), before)

    def test_editing_a_total_of_several_layers_is_refused(self):
        self.add(thickness=50)
        with self.assertRaises(BadRequest) as c:
            self.run_("model.set_property", id=self.w, key="thickness", value=300)
        self.assertIn("layers", c.exception.message)
        rows = self.rows(self.w)
        self.assertEqual(rows["Thickness"]["type"] if "Thickness" in rows else "readonly", "readonly")

    def test_a_layer_that_ignores_the_openings_runs_straight_through_them(self):
        lid = self.add(material="Mineral wool", thickness=100)
        full = self.vol(self.w)
        self.run_("model.set_property", id=self.w, key=f"layer:{lid}:openings", value="none")
        # the insulation is a solid slab 6 x 3.2 x 0.1; the main layer keeps both openings
        main = 0.25 * (6 * 3.2 - 0.9 * 2.1 - 1.2 * 1.2)
        self.assertAlmostEqual(self.vol(self.w), main + 0.1 * 6 * 3.2, places=5)
        self.assertGreater(self.vol(self.w), full)
        self.run_("model.set_property", id=self.w, key=f"layer:{lid}:openings", value="all")
        self.assertAlmostEqual(self.vol(self.w), full, places=9)

    def test_a_layer_can_be_cut_by_chosen_openings_only(self):
        lid = self.add(material="Plasterboard", thickness=50)
        self.run_("model.set_property", id=self.w, key=f"layer:{lid}:openings", value="selected")
        self.assertEqual(sorted(self.layers()[1]["opening_ids"]), sorted([self.door, self.win]))      # it starts from what it inherited
        self.run_("model.set_property", id=self.w, key=f"layer:{lid}:cut:{self.win}", value="no")
        main = 0.25 * (6 * 3.2 - 0.9 * 2.1 - 1.2 * 1.2)
        self.assertAlmostEqual(self.vol(self.w), main + 0.05 * (6 * 3.2 - 0.9 * 2.1), places=5)       # the window is closed by the plasterboard

    def test_the_relationship_survives_a_rebuild_and_forgets_a_deleted_opening(self):
        lid = self.add(thickness=50)
        self.run_("model.set_property", id=self.w, key=f"layer:{lid}:openings", value="selected")
        self.run_("model.set_property", id=self.w, key=f"layer:{lid}:cut:{self.win}", value="no")
        self.run_("model.set_property", id=self.w, key="height", value=3.5)
        self.assertEqual(self.layers()[1]["opening_ids"], [self.door])
        self.run_("model.delete", id=self.door)
        self.run_("model.set_property", id=self.w, key="height", value=3.4)
        self.assertEqual(self.layers()[1]["opening_ids"], [])

    def test_a_new_opening_is_inherited_by_all_layers_but_not_by_a_selection(self):
        a = self.add(thickness=50)
        b = self.add(thickness=50)
        self.run_("model.set_property", id=self.w, key=f"layer:{b}:openings", value="selected")
        extra = self.run_("model.add_window", wall=self.w, offset=1.0, width=500, height=500, sill=1500)["id"]
        self.assertNotIn(extra, self.layers()[2]["opening_ids"])
        self.assertEqual(self.layers()[1]["openings"], "all")

    def test_a_preview_changes_nothing(self):
        lid = self.add(thickness=50)
        before = self.vol(self.w)
        out = self.p.layer_preview(self.w, lid, "none")
        self.assertEqual(out["cut_by"], [])
        self.assertEqual(len(out["not_cut_by"]), 2)
        out = self.p.layer_preview(self.w, lid, "selected", [self.door])
        self.assertEqual([o["id"] for o in out["cut_by"]], [self.door])
        self.assertAlmostEqual(out["removed_area_m2"], 0.9 * 2.1, places=9)
        self.assertEqual(self.vol(self.w), before)
        self.assertEqual(self.layers()[1]["openings"], "all")

    def test_layers_are_reordered_and_removed(self):
        a, b = self.add(material="Plasterboard", thickness=10), self.add(material="Mineral wool", thickness=100)
        self.run_("model.move_layer", id=self.w, layer=b, position=0)
        self.assertEqual([l["id"] for l in self.layers()][0], b)
        self.run_("model.remove_layer", id=self.w, layer=a)
        self.assertEqual(len(self.layers()), 2)
        self.assertAlmostEqual(self.meta(self.w)["spadra:thickness"], 0.35, places=9)
        self.p.undo()
        self.assertEqual(len(self.layers()), 3)

    def test_the_last_layer_cannot_be_removed(self):
        with self.assertRaises(BadRequest):
            self.run_("model.remove_layer", id=self.w, layer="main")

    def test_a_trimmed_layer_is_shorter_and_an_extended_one_longer(self):
        lid = self.add(material="Plasterboard", thickness=50)
        base = self.vol(self.w)
        self.run_("model.set_property", id=self.w, key=f"layer:{lid}:end", value=-500)         # mm
        self.assertAlmostEqual(self.vol(self.w), base - 0.05 * 0.5 * 3.2, places=5)
        self.run_("model.set_property", id=self.w, key=f"layer:{lid}:end", value=0)
        self.run_("model.set_property", id=self.w, key=f"layer:{lid}:top", value=300)
        self.assertAlmostEqual(self.vol(self.w), base + 0.05 * 6.0 * 0.3, places=5)

    def test_an_edge_that_runs_into_a_joint_is_refused_with_the_layer_named(self):
        lid = self.add(material="Plasterboard", thickness=50, name="Inner lining")
        self.wall(a=(6000, 0), b=(6000, 4000))                                                # a corner at the wall's end
        with self.assertRaises(BadRequest) as c:
            self.run_("model.set_property", id=self.w, key=f"layer:{lid}:end", value=300)
        self.assertIn("Inner lining", c.exception.message)
        self.assertIn("joined", c.exception.message)
        self.assertEqual(self.layers()[1]["end"], 0.0)                                         # nothing was changed

    def test_a_layer_trimmed_to_nothing_is_refused(self):
        lid = self.add(thickness=50)
        with self.assertRaises(BadRequest):
            self.run_("model.set_property", id=self.w, key=f"layer:{lid}:end", value=-7000)

    def test_edges_close_to_the_wall_snap_to_it(self):
        self.assertEqual(LY.snapped(0.003, []), 0.0)
        self.assertEqual(LY.snapped(0.02, []), 0.02)

    def test_a_corner_joint_follows_the_layers_across_the_thickness(self):
        self.add(material="Plasterboard", thickness=50)
        other = self.wall(a=(6000, 0), b=(6000, 4000))
        total = self.vol(self.w) + self.vol(other)
        self.assertGreater(total, 0)
        self.assertTrue(all(self.p.model.volume(i, unit="m") > 0 for i in (self.w, other)))

    def test_the_properties_panel_edits_existing_layers_without_an_add_button(self):
        lid = self.add(material="Plasterboard", thickness=50)
        out = self.p.properties({"model": self.w})
        group = next(g for g in out["groups"] if g["title"].startswith("Layers"))
        self.assertNotIn("add", group)  # creation is contextual on the selected faces
        keys = [r.get("key") for r in group["rows"]]
        self.assertIn(f"layer:{lid}:thickness", keys)
        self.assertIn(f"layer:{lid}:openings", keys)
        self.assertEqual(group["title"], "Layers (2)")


class Sections(Case):
    def setUp(self) -> None:
        super().setUp()
        self.w = self.wall(a=(0, 0), b=(6000, 0), material="Masonry")           # 250 thick, 3.2 m high
        self.main = LY.stack(self.p, self.w)[0]["id"]
        self.ins = self.run_("model.add_layer", id=self.w, material="Mineral wool", thickness=100)["layer"]
        self.lining = self.run_("model.add_layer", id=self.w, material="Plasterboard", thickness=12.5)["layer"]
        self.sec = self.run_("view.add_section", a=[3000, -2000], b=[3000, 2000])["id"]

    def item(self):
        pr = D.project_view(self.p, self.p.view(self.sec), 1000.0)
        return next(i for i in pr["items"] if i["id"] == self.w)

    def test_a_section_cuts_each_layer_as_its_own_loop_with_its_own_area(self):
        it = self.item()
        self.assertEqual(len(it["cut"]), 3)
        by = {row["layer"]: area(loop) for row, loop in zip(it["cut_layers"], it["cut"])}
        self.assertAlmostEqual(by[self.main], 250 * 3200, places=3)              # mm²
        self.assertAlmostEqual(by[self.ins], 100 * 3200, places=3)
        self.assertAlmostEqual(by[self.lining], 12.5 * 3200, places=3)

    def test_each_loop_is_hatched_by_its_own_material(self):
        it = self.item()
        patterns = {row["layer"]: h["pattern"] for row, h in zip(it["cut_layers"], it["cut_hatches"])}
        self.assertEqual(patterns[self.main], "lines-135")                       # masonry
        self.assertEqual(patterns[self.ins], "cross-45")                         # insulation
        self.assertEqual(patterns[self.lining], "none")                          # plasterboard
        self.assertNotIn("cut_hatch", it)

    def test_a_view_that_hides_hatching_names_the_layers_but_hatches_none(self):
        self.run_("view.set_option", id=self.sec, key="material_hatching", value="hidden")
        it = self.item()
        self.assertEqual(len(it["cut_layers"]), 3)
        self.assertNotIn("cut_hatches", it)

    def test_a_cut_through_an_opening_leaves_each_layer_with_its_own_lintel(self):
        self.run_("model.add_window", wall=self.w, offset=3.0, width=1000, height=1200, sill=900)
        none = self.run_("model.set_property", id=self.w, key=f"layer:{self.lining}:openings", value="none")
        it = self.item()
        by = {}
        for row, loop in zip(it["cut_layers"], it["cut"]):
            by[row["layer"]] = by.get(row["layer"], 0) + area(loop)
        self.assertAlmostEqual(by[self.main], 250 * (3200 - 1200), places=3)       # cut by the window
        self.assertAlmostEqual(by[self.lining], 12.5 * 3200, places=3)             # not cut: the lining runs across the opening


class PlanBands(Case):
    def test_a_layered_wall_is_drawn_layer_by_layer_in_plan_around_the_openings_each_inherits(self):
        w = self.wall(a=(0, 0), b=(6000, 0), material="Masonry")
        self.run_("model.add_door", wall=w, offset=3.0, width=1000, height=2100)
        lid = self.run_("model.add_layer", id=w, material="Plasterboard", thickness=12.5)["layer"]
        self.run_("model.set_property", id=w, key=f"layer:{lid}:openings", value="none")
        plan = next(v["id"] for v in self.p.manifest["views"] if v.get("level_id") == self.l1)
        pieces = [i for i in self.p.plan_overlay(plan) if i["id"] == w]
        bands = next(i["bands"] for i in pieces if i.get("bands"))
        self.assertEqual([b["material"] for b in bands], ["Masonry", "Plasterboard"])
        self.assertEqual(len(bands[0]["polygons"]), 2)                 # the door splits the main layer in two ...
        self.assertEqual(len(bands[1]["polygons"]), 1)                 # ... and the lining runs across it
        self.assertEqual(bands[0]["hatch"]["pattern"], "lines-135")
        self.assertEqual(sum(1 for i in pieces if i.get("bands")), 1)


class Floors(Case):
    def setUp(self) -> None:
        super().setUp()
        self.f = self.run_("model.add_floor", level=self.l1, points=[[0, 0], [6000, 0], [6000, 4000], [0, 4000]], thickness=200, material="Concrete")["id"]
        self.top = 0.0

    def add(self, **kw):
        return self.run_("model.add_layer", id=self.f, **kw)["layer"]

    def test_top_and_bottom_contextual_layers_preserve_the_existing_slab_location(self):
        before = self.p.model.world_bounds(self.f)
        above = self.add(material="Screed", thickness=50, side="top")
        after_top = self.p.model.world_bounds(self.f)
        self.assertAlmostEqual(after_top[0][2], before[0][2], places=7)
        self.assertAlmostEqual(after_top[1][2], before[1][2] + 0.05, places=7)
        below = self.add(material="EPS", thickness=80, side="bottom")
        after_both = self.p.model.world_bounds(self.f)
        self.assertAlmostEqual(after_both[0][2], before[0][2] - 0.08, places=7)
        self.assertAlmostEqual(after_both[1][2], before[1][2] + 0.05, places=7)
        self.assertEqual([row["id"] for row in LY.stack(self.p, self.f)], [above, "main", below])

    def test_3d_floor_face_controls_are_on_opposing_surface_normals(self):
        obj = next(o for o in self.p.scene_data()["objects"] if o["id"] == self.f)
        points = {row["side"]: row["point"] for row in obj["layer_points"]}
        self.assertEqual(set(points), {"top", "bottom"})
        self.assertGreater(points["top"][2], points["bottom"][2])

    def test_floor_layers_stack_downward_from_the_top_and_add_up(self):
        a = self.add(material="Screed", thickness=60, position=0)                 # on top
        self.add(material="EPS", thickness=100)                                  # under the slab
        self.assertAlmostEqual(self.meta(self.f)["spadra:thickness"], 0.36, places=9)
        self.assertAlmostEqual(self.vol(self.f), 24 * 0.36, places=6)
        lo, hi = self.p.model.world_bounds(self.f)
        self.assertAlmostEqual(hi[2], 0.0, places=9)                              # the top surface stays at the level
        self.assertAlmostEqual(lo[2], -0.36, places=9)

    def test_a_void_cuts_only_the_layers_that_inherit_it(self):
        lid = self.add(material="EPS", thickness=100)
        full = self.vol(self.f)
        void = self.run_("model.add_extrusion", void=True, level=self.l1, points=[[1000, 1000], [2000, 1000], [2000, 2000], [1000, 2000]], base=-0.5, depth=0.7)["id"]
        self.assertAlmostEqual(self.vol(self.f), full - 1.0 * 0.3, places=5)      # through both layers (0.2 + 0.1)
        self.run_("model.set_property", id=self.f, key=f"layer:{lid}:openings", value="none")
        self.assertAlmostEqual(self.vol(self.f), full - 1.0 * 0.2, places=5)      # the insulation now runs across the hole

    def test_a_void_that_reaches_only_the_upper_layer_is_a_partial_depth_cut(self):
        self.add(material="EPS", thickness=100)
        full = self.vol(self.f)
        self.run_("model.add_extrusion", void=True, level=self.l1, points=[[1000, 1000], [2000, 1000], [2000, 2000], [1000, 2000]], base=-0.25, depth=0.5)
        self.assertAlmostEqual(self.vol(self.f), full - 1.0 * 0.2, places=5)      # it spans the slab's 0.2 but not the insulation below it

    def test_the_stored_cuts_follow_the_layers_so_nothing_rebuilds_forever(self):
        self.add(material="EPS", thickness=100)
        self.run_("model.add_extrusion", void=True, level=self.l1, points=[[1000, 1000], [2000, 1000], [2000, 2000], [1000, 2000]], base=-0.5, depth=0.7)
        from applications.SPADRA import forms as FX
        self.assertEqual(FX.reconcile(self.p), 0)

    def test_floor_section_loops_are_told_apart_by_height(self):
        a = self.add(material="EPS", thickness=100)
        sec = self.run_("view.add_section", a=[3000, -1000], b=[3000, 5000])["id"]
        pr = D.project_view(self.p, self.p.view(sec), 1000.0)
        it = next(i for i in pr["items"] if i["id"] == self.f)
        by = {row["material"]: area(loop) for row, loop in zip(it["cut_layers"], it["cut"])}
        self.assertAlmostEqual(by["Concrete"], 4000 * 200, places=3)
        self.assertAlmostEqual(by["EPS"], 4000 * 100, places=3)


class Roofs(Case):
    def test_inside_flat_roof_layer_grows_below_without_moving_existing_roof(self):
        roof = self.run_("model.add_roof", level=self.roof_level, roof="flat", points=[[0, 0], [6000, 0], [6000, 4000], [0, 4000]], thickness=100)["id"]
        before = self.p.model.world_bounds(roof)
        self.run_("model.add_layer", id=roof, side="inside", thickness=80)
        after = self.p.model.world_bounds(roof)
        self.assertAlmostEqual(after[0][2], before[0][2] - 0.08, places=7)
        self.assertAlmostEqual(after[1][2], before[1][2], places=7)

    def test_3d_roof_face_controls_follow_a_pitched_surface(self):
        roof = self.run_("model.add_roof", level=self.roof_level, roof="gable", points=[[0, 0], [6000, 0], [6000, 4000], [0, 4000]], thickness=100, pitch=30)["id"]
        obj = next(o for o in self.p.scene_data()["objects"] if o["id"] == roof)
        points = {row["side"]: row["point"] for row in obj["layer_points"]}
        self.assertEqual(set(points), {"outside", "inside"})
        self.assertNotEqual(points["outside"][2], points["inside"][2])

    def test_a_layered_flat_roof_keeps_standing_on_its_level(self):
        r = self.run_("model.add_roof", level=self.roof_level, roof="flat", points=[[0, 0], [6000, 0], [6000, 4000], [0, 4000]], thickness=100)["id"]
        base = next(l["elevation"] for l in self.p.manifest["levels"] if l["id"] == self.roof_level)
        self.run_("model.add_layer", id=r, material="EPS", thickness=150)
        lo, hi = self.p.model.world_bounds(r)
        self.assertAlmostEqual(lo[2], base, places=9)                              # a flat roof stands on its level, as before
        self.assertAlmostEqual(hi[2], base + 0.25, places=9)
        self.assertAlmostEqual(self.vol(r), 24 * 0.25, places=6)

    def test_a_layered_gable_roof_keeps_its_slope_and_its_volume(self):
        r = self.run_("model.add_roof", level=self.roof_level, roof="gable", points=[[0, 0], [6000, 0], [6000, 4000], [0, 4000]], thickness=100, pitch=30)["id"]
        single = self.vol(r)
        self.run_("model.add_layer", id=r, material="EPS", thickness=100)
        self.assertAlmostEqual(self.vol(r), 2 * single, places=6)                 # two equal slabs, the second lowered straight down
        from applications.SPADRA import elements as E
        self.assertAlmostEqual(E.m(self.meta(r) and self.p.model.get(r)["metadata"], "thickness"), 0.2, places=9)

    def test_a_roof_has_no_openings_rows(self):
        r = self.run_("model.add_roof", level=self.roof_level, roof="flat", points=[[0, 0], [6000, 0], [6000, 4000], [0, 4000]], thickness=100)["id"]
        lid = self.run_("model.add_layer", id=r, thickness=50)["layer"]
        keys = [x.get("key") for g in self.p.properties({"model": r})["groups"] for x in g["rows"]]
        self.assertIn(f"layer:{lid}:thickness", keys)
        self.assertNotIn(f"layer:{lid}:openings", keys)

    def test_room_volume_uses_the_total_thickness_of_a_layered_roof(self):
        from applications.SPADRA import rooms as R
        rid = self.run_("model.add_room", level=self.l1, points=[[0, 0], [5000, 0], [5000, 4000], [0, 4000]])["id"]
        r = self.run_("model.add_roof", level=self.roof_level, roof="flat", points=[[0, 0], [6000, 0], [6000, 4000], [0, 4000]], thickness=100)["id"]
        base = next(l["elevation"] for l in self.p.manifest["levels"] if l["id"] == self.roof_level)
        self.run_("model.add_layer", id=r, material="EPS", thickness=150)
        self.assertAlmostEqual(R.data(self.p, rid)["volume"], 20 * base, places=6)       # a flat roof's underside is at its eaves' base, layers hang above... below?


class Quantities(Case):
    def test_each_layer_counts_under_its_own_material_and_they_add_up(self):
        w = self.wall(a=(0, 0), b=(6000, 0), material="Masonry")
        self.run_("model.add_window", wall=w, offset=3.0, width=1000, height=1200, sill=900)
        lid = self.run_("model.add_layer", id=w, material="Mineral wool", thickness=100)["layer"]
        self.run_("model.add_layer", id=w, material="Plasterboard", thickness=12.5)
        rows = {l["material"]: l["volume_m3"] for l in LY.layer_volumes(self.p, w)}
        net = 6 * 3.2 - 1.0 * 1.2
        self.assertAlmostEqual(rows["Masonry"], 0.25 * net, places=6)
        self.assertAlmostEqual(rows["Mineral wool"], 0.1 * net, places=6)
        self.assertAlmostEqual(rows["Plasterboard"], 0.0125 * net, places=6)
        self.assertAlmostEqual(sum(rows.values()), self.vol(w), places=6)
        q = self.p.quantities([w], None)
        by = {b["material"]: b["volume_m3"] for b in q["by_material"]}
        self.assertAlmostEqual(by["Mineral wool"], 0.1 * net, places=5)
        self.assertAlmostEqual(by["Masonry"], 0.25 * net, places=5)

    def test_a_layer_that_runs_across_an_opening_counts_the_extra_material(self):
        w = self.wall(a=(0, 0), b=(6000, 0), material="Masonry")
        self.run_("model.add_window", wall=w, offset=3.0, width=1000, height=1200, sill=900)
        lid = self.run_("model.add_layer", id=w, material="Plasterboard", thickness=12.5)["layer"]
        self.run_("model.set_property", id=w, key=f"layer:{lid}:openings", value="none")
        rows = {l["material"]: l["volume_m3"] for l in LY.layer_volumes(self.p, w)}
        self.assertAlmostEqual(rows["Plasterboard"], 0.0125 * 6 * 3.2, places=6)
