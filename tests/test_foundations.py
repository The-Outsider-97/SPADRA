"""Foundations against the real SGEO engine: the five kinds, how they follow the walls and columns they stand on, strips that meet without overlapping, the
build-up of an insulated slab, the declared reinforcement and its indicative take-off, the model check, the schedule, and that every refused edit leaves
the project exactly as it was."""
from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from applications.SPADRA import elements as E
from applications.SPADRA import foundations as FD
from applications.SPADRA import quantities as Q
from applications.SPADRA.project import NS, Project
from applications.tools.errors import BadRequest

RECT = [[0, 0], [6000, 0], [6000, 5000], [0, 5000]]


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project.create("Foundations")
        self.l1 = self.p.manifest["levels"][0]["id"]

    def run_(self, op, **params):
        return self.p.execute(op, params)["result"]

    def meta(self, oid):
        return self.p.model.get(oid)["metadata"]

    def vol(self, oid):
        return self.p.model.volume(oid, unit="m")

    def wall(self, a, b, **extra):
        return self.run_("model.add_wall", level=self.l1, start=a, end=b, height=3, thickness=200, **extra)["id"]

    def column(self, at, **extra):
        return self.run_("model.add_column", level=self.l1, point=at, base=0, top=3, width=300, depth=300, **extra)["id"]

    def found(self, **params):
        return self.run_("model.add_foundation", **params)["id"]

    def slab(self, **extra):
        return self.found(kind="vloer", level=self.l1, points=RECT, thickness=150, top=0, **extra)

    def problems(self):
        return [i["message"] for i in E.check(self.p)]


class Kinds(Case):
    def test_a_rectangular_pad_is_a_box_on_its_point_and_its_volume_is_exact(self):
        pad = self.found(kind="poer", level=self.l1, point=[1000, 2000], width=1200, length=800, height=500, top=-0.3, rotation=30)
        m = self.meta(pad)
        self.assertEqual((m[f"{NS}:category"], m[f"{NS}:kind"], m[f"{NS}:type"]), ("foundation", "poer", "Pad Foundation"))
        self.assertAlmostEqual(self.vol(pad), 1.2 * 0.8 * 0.5, places=9)
        lo, hi = self.p.model.world_bounds(pad)
        self.assertAlmostEqual(hi[2], -0.3, places=9)
        self.assertAlmostEqual(lo[2], -0.8, places=9)
        self.assertEqual(m[f"{NS}:sfb"], "(16.13)")

    def test_a_round_pad_has_one_size_and_is_a_48_sided_prism(self):
        pad = self.found(kind="poer", level=self.l1, point=[0, 0], shape="round", width=1000, height=400, top=0)
        expected = 0.5 * 48 * 0.5 ** 2 * math.sin(2 * math.pi / 48) * 0.4
        self.assertAlmostEqual(self.vol(pad), expected, places=9)
        self.assertLess(self.vol(pad), math.pi * 0.25 * 0.4)
        with self.assertRaises(BadRequest):
            self.run_("model.set_property", id=pad, key="length", value=900)
        self.run_("model.set_property", id=pad, key="width", value=1200)
        self.assertEqual(self.meta(pad)[f"{NS}:length"], 1.2)

    def test_a_strip_between_two_points_is_a_prism_the_size_asked_for(self):
        s = self.found(kind="strook", level=self.l1, start=[0, 0], end=[4000, 0], width=600, height=500, top=0)
        self.assertAlmostEqual(self.vol(s), 4.0 * 0.6 * 0.5, places=9)
        self.assertEqual(self.meta(s)[f"{NS}:sfb"], "(16.12)")

    def test_eccentricity_shifts_the_strip_sideways(self):
        s = self.found(kind="strook", level=self.l1, start=[0, 0], end=[4000, 0], width=600, height=500, top=0, offset=200)
        lo, hi = self.p.model.world_bounds(s)
        self.assertAlmostEqual(lo[1], -0.1, places=9)
        self.assertAlmostEqual(hi[1], 0.5, places=9)

    def test_a_raft_and_a_slab_take_any_simple_outline(self):
        l_shape = [[0, 0], [6000, 0], [6000, 2000], [3000, 2000], [3000, 5000], [0, 5000]]
        raft = self.found(kind="plaat", level=self.l1, points=l_shape, thickness=400, top=0)
        self.assertAlmostEqual(self.vol(raft), (6 * 2 + 3 * 3) * 0.4, places=9)
        self.assertEqual(self.meta(raft)[f"{NS}:sfb"], "(16.10)")
        with self.assertRaises(BadRequest):
            self.found(kind="vloer", level=self.l1, points=[[0, 0], [1000, 1000], [1000, 0], [0, 1000]], thickness=100)

    def test_an_unknown_kind_is_refused(self):
        with self.assertRaises(BadRequest):
            self.found(kind="pile", level=self.l1, point=[0, 0])

    def test_a_foundation_must_be_thick_enough(self):
        with self.assertRaises(BadRequest):
            self.found(kind="poer", level=self.l1, point=[0, 0], width=1000, length=1000, height=5)


class Supports(Case):
    def test_a_strip_under_a_wall_takes_the_wall_axis_and_a_width_from_the_wall_thickness(self):
        w = self.wall([0, 0], [5000, 0])
        s = self.found(kind="strook", support=[w], height=500)
        self.assertEqual(self.meta(s)[f"{NS}:start"], [0.0, 0.0])
        self.assertEqual(self.meta(s)[f"{NS}:end"], [5.0, 0.0])
        self.assertAlmostEqual(self.meta(s)[f"{NS}:width"], 0.6, places=9)
        self.assertEqual(self.problems(), [])

    def test_the_strip_follows_the_wall_when_it_moves_stretches_or_turns(self):
        w = self.wall([0, 0], [5000, 0])
        s = self.found(kind="strook", support=[w], height=500)
        before = self.vol(s)
        self.run_("model.move", id=w, dx=1.0, dy=0.5, dz=0)
        self.assertEqual(self.meta(s)[f"{NS}:start"], [1.0, 0.5])
        self.run_("model.set_wall_ends", id=w, end=[8000, 500])
        self.assertAlmostEqual(self.vol(s), 7.0 * 0.6 * 0.5, places=9)
        self.assertNotAlmostEqual(self.vol(s), before)
        self.run_("model.rotate", ids=[w], centre=[1000, 500], angle=90)
        self.assertAlmostEqual(self.meta(s)[f"{NS}:end"][0], 1.0, places=6)
        self.assertAlmostEqual(self.meta(s)[f"{NS}:end"][1], 7.5, places=6)

    def test_a_pad_follows_its_column_in_place_and_in_turn(self):
        c = self.column([2000, 3000])
        pad = self.found(kind="poer", support=[c], height=600)
        self.assertAlmostEqual(self.meta(pad)[f"{NS}:width"], 0.7, places=9)
        self.run_("model.move", id=c, dx=0.5, dy=0, dz=0)
        self.assertEqual(self.meta(pad)[f"{NS}:point"], [2.5, 3.0])
        self.run_("model.rotate", ids=[c], centre=[2500, 3000], angle=45)
        self.assertAlmostEqual(self.meta(pad)[f"{NS}:rotation"], math.radians(45), places=9)
        self.assertEqual(self.problems(), [])

    def test_a_foundation_beam_runs_between_two_columns_and_follows_both(self):
        a, b = self.column([0, 0]), self.column([4000, 0])
        beam = self.found(kind="funderingsbalk", support=[a, b], width=300, height=500)
        self.assertAlmostEqual(self.vol(beam), 4.0 * 0.3 * 0.5, places=9)
        self.run_("model.move", id=b, dx=1.0, dy=0, dz=0)
        self.assertAlmostEqual(self.vol(beam), 5.0 * 0.3 * 0.5, places=9)
        with self.assertRaises(BadRequest):
            self.found(kind="funderingsbalk", support=[a], width=300, height=500)

    def test_the_top_starts_at_the_base_of_what_it_carries_and_a_later_mismatch_is_reported(self):
        w = self.wall([0, 0], [5000, 0])
        self.run_("model.set_property", id=w, key="base_offset", value=-300)
        s = self.found(kind="strook", support=[w], height=500)
        self.assertAlmostEqual(self.meta(s)[f"{NS}:top"], -0.3, places=9)
        self.assertEqual(self.problems(), [])
        self.run_("model.set_property", id=w, key="base_offset", value=0)
        msgs = self.problems()
        self.assertEqual(len(msgs), 1)
        self.assertIn("300 mm below the underside", msgs[0])
        self.assertAlmostEqual(self.meta(s)[f"{NS}:top"], -0.3, places=9)              # reported, never silently moved

    def test_it_stays_independent_of_the_walls_hosted_openings_and_survives_the_wall_going(self):
        w = self.wall([0, 0], [5000, 0])
        s = self.found(kind="strook", support=[w], height=500)
        self.run_("model.delete", id=w)
        self.assertTrue(E.alive(self.p, s))                                           # a foundation is not hosted by its wall
        self.assertIn("has lost what it stood on", self.problems()[0])
        self.assertAlmostEqual(self.vol(s), 5.0 * 0.6 * 0.5, places=9)

    def test_a_supported_foundation_will_not_be_moved_or_turned_on_its_own(self):
        c = self.column([0, 0])
        pad = self.found(kind="poer", support=[c], height=500)
        with self.assertRaises(BadRequest):
            self.run_("model.move", id=pad, dx=0.1, dy=0, dz=0)
        with self.assertRaises(BadRequest):
            self.run_("model.rotate", ids=[pad], centre=[0, 0], angle=10)
        loose = self.found(kind="poer", level=self.l1, point=[5000, 0], width=800, length=800, height=500)
        self.run_("model.move", id=loose, dx=0.1, dy=0.2, dz=-0.1)
        self.assertEqual(self.meta(loose)[f"{NS}:point"], [5.1, 0.2])

    def test_a_support_of_the_wrong_kind_is_refused(self):
        w = self.wall([0, 0], [5000, 0])
        with self.assertRaises(BadRequest):
            self.found(kind="poer", support=[w], height=500)
        c = self.column([0, 0])
        with self.assertRaises(BadRequest):
            self.found(kind="strook", support=[c], height=500)

    def test_under_gives_strips_to_walls_and_pads_to_columns_all_or_none(self):
        w, c = self.wall([0, 0], [5000, 0]), self.column([0, 4000])
        made = self.run_("model.add_foundations_under", supports=[w, c], height=500)["ids"]
        self.assertEqual([self.meta(i)[f"{NS}:kind"] for i in made], ["strook", "poer"])
        count = len(E.element_ids(self.p))
        with self.assertRaises(BadRequest):
            self.run_("model.add_foundations_under", supports=[w, made[0]], height=500)          # a foundation is neither a wall nor a column
        self.assertEqual(len(E.element_ids(self.p)), count)


class Strips(Case):
    def test_strips_under_walls_that_meet_are_mitred_so_the_corner_is_solid_once(self):
        w1, w2 = self.wall([0, 0], [6000, 0]), self.wall([6000, 0], [6000, 5000])
        a, b = self.found(kind="strook", support=[w1], width=600, height=500), self.found(kind="strook", support=[w2], width=600, height=500)
        self.assertAlmostEqual(self.vol(a) + self.vol(b), (6.0 + 5.0) * 0.6 * 0.5, places=9)       # no square counted twice, no square missing
        self.assertEqual(self.meta(a)[f"{NS}:joins"], [b])
        self.assertNotIn("overlaps", " ".join(self.problems()))

    def test_a_closed_ring_of_strips_has_the_volume_of_the_ring_between_its_outer_and_inner_outline(self):
        pts = [[0, 0], [6000, 0], [6000, 5000], [0, 5000]]
        ws = [self.wall(pts[i], pts[(i + 1) % 4]) for i in range(4)]
        ss = [self.found(kind="strook", support=[w], width=600, height=500) for w in ws]
        ring = (6.0 + 0.6) * (5.0 + 0.6) - (6.0 - 0.6) * (5.0 - 0.6)
        self.assertAlmostEqual(sum(self.vol(s) for s in ss), ring * 0.5, places=9)

    def test_a_strip_ending_on_the_side_of_another_is_cut_square_to_its_face(self):
        w1, w2 = self.wall([0, 0], [6000, 0]), self.wall([3000, 0], [3000, 4000])
        a, b = self.found(kind="strook", support=[w1], width=600, height=500), self.found(kind="strook", support=[w2], width=600, height=500)
        self.assertAlmostEqual(self.vol(a), 6.0 * 0.6 * 0.5, places=9)                              # the through strip is untouched
        self.assertAlmostEqual(self.vol(b), (4.0 - 0.3) * 0.6 * 0.5, places=9)                      # the stem stops at its face

    def test_moving_a_wall_re_cuts_the_neighbouring_strips_in_the_same_step(self):
        w1, w2 = self.wall([0, 0], [6000, 0]), self.wall([6000, 0], [6000, 5000])
        a, b = self.found(kind="strook", support=[w1], width=600, height=500), self.found(kind="strook", support=[w2], width=600, height=500)
        self.run_("model.move", id=w2, dx=0, dy=3.0, dz=0)                                         # now only touches nothing: the corner is open
        self.assertEqual(self.meta(a)[f"{NS}:joins"], [])
        self.assertAlmostEqual(self.vol(a), 6.0 * 0.6 * 0.5, places=9)
        self.p.undo()
        self.assertEqual(self.meta(a)[f"{NS}:joins"], [b])

    def test_strips_on_other_levels_do_not_join(self):
        l2 = self.p.manifest["levels"][1]["id"]
        w1 = self.wall([0, 0], [6000, 0])
        w2 = self.run_("model.add_wall", level=l2, start=[6000, 0], end=[6000, 5000], height=3, thickness=200)["id"]
        a = self.found(kind="strook", support=[w1], width=600, height=500)
        b = self.found(kind="strook", support=[w2], width=600, height=500)
        self.assertEqual(self.meta(a)[f"{NS}:joins"], [])
        self.assertEqual(self.meta(b)[f"{NS}:joins"], [])


class BuildUp(Case):
    def test_layers_stack_up_and_down_from_the_slab_and_the_volumes_add_up(self):
        s = self.slab(below=[{"name": "Insulation", "material": "EPS", "thickness": 100, "conductivity": 0.036}, {"material": "Sand", "thickness": 150}],
                      above=[{"name": "Screed", "material": "Screed", "thickness": 70}])
        area = 30.0
        self.assertAlmostEqual(self.vol(s), area * (0.15 + 0.10 + 0.15 + 0.07), places=9)
        lo, hi = self.p.model.world_bounds(s)
        self.assertAlmostEqual(hi[2], 0.07, places=9)
        self.assertAlmostEqual(lo[2], -0.40, places=9)
        self.assertEqual(E.solid_status(self.p, s).state, "solid")
        q = FD.quantities(self.p, s)
        self.assertAlmostEqual(q["concrete_m3"], area * 0.15, places=9)
        self.assertAlmostEqual(q["volume_m3"], self.vol(s), places=9)
        self.assertEqual([l["material"] for l in q["layers"]], ["Screed", "Concrete", "EPS", "Sand"])

    def test_thermal_resistance_is_only_the_sum_of_what_was_given_and_says_when_it_is_incomplete(self):
        s = self.slab(below=[{"name": "Insulation", "material": "PIR", "thickness": 120, "conductivity": 0.022}])
        th = FD.quantities(self.p, s)["thermal"]
        self.assertFalse(th["complete"])
        self.assertEqual(th["missing"], ["Concrete"])
        self.assertAlmostEqual(th["r_m2k_w"], 0.12 / 0.022, places=9)
        self.assertIsNone(th["u_w_m2k"])
        self.run_("model.set_property", id=s, key="conductivity", value=1.8)
        th = FD.quantities(self.p, s)["thermal"]
        self.assertTrue(th["complete"])
        self.assertAlmostEqual(th["r_m2k_w"], 0.12 / 0.022 + 0.15 / 1.8, places=9)
        self.assertAlmostEqual(th["u_w_m2k"], 1 / th["r_m2k_w"], places=9)

    def test_the_material_quantities_count_each_layer_under_its_own_material_and_give_the_layered_element_no_single_mass(self):
        s = self.slab(below=[{"material": "EPS", "thickness": 100}])
        t = Q.take_off(self.p, None)
        by = {b["material"]: b for b in t["by_material"]}
        self.assertAlmostEqual(by["EPS"]["volume_m3"], 3.0, places=9)
        self.assertAlmostEqual(by["Concrete"]["volume_m3"], 4.5, places=9)
        row = next(r for r in t["rows"] if r["id"] == s)
        self.assertIsNone(row["mass_kg"])
        self.assertTrue(any("layers of different materials" in n for n in row["notes"]))

    def test_layers_are_edited_as_a_whole_and_only_on_a_raft_or_slab(self):
        s = self.slab()
        self.run_("model.set_foundation_data", id=s, below=[{"material": "XPS", "thickness": 80, "conductivity": 0.034}])
        self.assertAlmostEqual(self.vol(s), 30.0 * 0.23, places=9)
        self.run_("model.set_foundation_data", id=s, below=[])
        self.assertAlmostEqual(self.vol(s), 30.0 * 0.15, places=9)
        pad = self.found(kind="poer", level=self.l1, point=[0, 0], width=800, length=800, height=500)
        with self.assertRaises(BadRequest):
            self.run_("model.set_foundation_data", id=pad, below=[{"material": "EPS", "thickness": 50}])
        before = self.vol(s)
        with self.assertRaises(BadRequest):
            self.run_("model.set_foundation_data", id=s, below=[{"material": "EPS", "thickness": 0}])
        self.assertEqual(self.vol(s), before)

    def test_bad_layers_are_refused_whole(self):
        for bad in ([{"material": "EPS"}], [{"thickness": 50}], "EPS", [{"material": "EPS", "thickness": 50, "conductivity": -1}], [{"material": "EPS", "thickness": 50}] * 13):
            with self.subTest(bad=bad), self.assertRaises(BadRequest):
                self.slab(below=bad)
        self.assertEqual(E.element_ids(self.p), [])


class Reinforcement(Case):
    def test_bars_in_a_slab_are_counted_from_the_declared_spacing_and_cover(self):
        s = self.slab(cover=50, bars=[{"mark": "A", "diameter": 10, "spacing": 200, "role": "x"}, {"mark": "B", "diameter": 12, "count": 10, "role": "y", "face": "top"}])
        rf = FD.quantities(self.p, s)["reinforcement"]
        a, b = rf["groups"]
        self.assertEqual(a["n"], int((5.0 - 0.1) / 0.2) + 1)                                      # across the 5 m side at 200 mm
        self.assertAlmostEqual(a["each_m"], 6.0 - 0.1, places=9)
        self.assertEqual(b["n"], 10)
        self.assertAlmostEqual(b["each_m"], 5.0 - 0.1, places=9)
        self.assertEqual(rf["bars"], a["n"] + 10)
        self.assertAlmostEqual(rf["length_m"], a["n"] * a["each_m"] + 10 * b["each_m"], places=9)
        if rf["steel_density"]:
            self.assertAlmostEqual(a["mass_kg"], a["n"] * a["each_m"] * math.pi * 0.005 ** 2 * rf["steel_density"], places=6)
        self.assertIn("not a design", FD.quantities(self.p, s)["reinforcement"]["note"].replace("not designed", "not a design"))

    def test_a_strip_has_long_bars_and_links(self):
        s = self.found(kind="strook", level=self.l1, start=[0, 0], end=[4000, 0], width=600, height=500, top=0, cover=50,
                       bars=[{"mark": "L", "diameter": 16, "count": 4, "role": "long"}, {"mark": "S", "diameter": 8, "spacing": 200, "role": "link"}])
        rf = FD.quantities(self.p, s)["reinforcement"]
        long_, link = rf["groups"]
        self.assertEqual(long_["n"], 4)
        self.assertAlmostEqual(long_["each_m"], 3.9, places=9)
        self.assertEqual(link["n"], int(3.9 / 0.2) + 1)
        self.assertAlmostEqual(link["each_m"], 2 * 0.5 + 2 * 0.4, places=9)

    def test_declarations_that_cannot_be_right_are_refused_before_anything_is_made(self):
        base = dict(kind="poer", level=self.l1, point=[0, 0], width=800, length=800, height=500)
        for bad in ([{"diameter": 11, "spacing": 150, "role": "x"}],            # not a bar size
                    [{"diameter": 12, "role": "x"}],                              # neither spacing nor count
                    [{"diameter": 12, "spacing": 150, "count": 5, "role": "x"}],  # both
                    [{"diameter": 12, "spacing": 150, "role": "long"}],           # a pad has x and y bars
                    [{"diameter": 12, "spacing": 150, "role": "x", "face": "side"}]):
            with self.subTest(bad=bad), self.assertRaises(BadRequest):
                self.found(**base, bars=bad)
        with self.assertRaises(BadRequest):
            self.found(kind="strook", level=self.l1, start=[0, 0], end=[4000, 0], width=600, height=500, bars=[{"diameter": 8, "count": 5, "role": "link"}])
        self.assertEqual(E.element_ids(self.p), [])

    def test_a_cover_that_leaves_no_room_for_a_bar_is_an_error_in_the_check(self):
        pad = self.found(kind="poer", level=self.l1, point=[0, 0], width=200, length=800, height=500, cover=100, bars=[{"diameter": 12, "spacing": 150, "role": "x"}])
        self.assertTrue(any("cover leaves no room" in m for m in self.problems()))
        self.assertTrue(E.alive(self.p, pad))

    def test_the_concrete_class_steel_and_cover_are_declared_text_and_numbers_never_checked(self):
        pad = self.found(kind="poer", level=self.l1, point=[0, 0], width=800, length=800, height=500, concrete_class="C25/30", steel="B500B")
        q = FD.quantities(self.p, pad)
        self.assertEqual((q["concrete_class"], q["steel"]), ("C25/30", "B500B"))
        self.run_("model.set_foundation_data", id=pad, concrete_class="C30/37", cover=40)
        self.assertEqual(FD.quantities(self.p, pad)["concrete_class"], "C30/37")
        self.assertAlmostEqual(FD.quantities(self.p, pad)["cover_m"], 0.04, places=9)
        with self.assertRaises(BadRequest):
            self.run_("model.set_foundation_data", id=pad)


class Checks(Case):
    def test_a_pad_that_does_not_cover_its_column_is_reported(self):
        c = self.column([0, 0])
        pad = self.found(kind="poer", support=[c], width=250, length=250, height=500)
        self.assertTrue(any("does not cover the whole foot" in m for m in self.problems()))
        self.run_("model.set_property", id=pad, key="width", value=600)
        self.run_("model.set_property", id=pad, key="length", value=600)
        self.assertEqual(self.problems(), [])

    def test_a_strip_narrower_than_its_wall_is_reported(self):
        w = self.wall([0, 0], [5000, 0])
        self.found(kind="strook", support=[w], width=150, height=500)
        self.assertTrue(any("narrower than the wall" in m for m in self.problems()))

    def test_overlapping_foundations_are_reported_because_their_concrete_is_counted_twice(self):
        a = self.found(kind="poer", level=self.l1, point=[0, 0], width=1000, length=1000, height=500)
        self.found(kind="poer", level=self.l1, point=[600, 0], width=1000, length=1000, height=500)
        self.assertTrue(any("overlaps" in m for m in self.problems()))
        self.run_("model.move", id=a, dx=-0.6, dy=0, dz=0)
        self.assertFalse(any("overlaps" in m for m in self.problems()))
        self.run_("model.move", id=a, dx=0.6, dy=0, dz=-1.0)                                       # same place, but a different depth
        self.assertFalse(any("overlaps" in m for m in self.problems()))

    def test_a_void_that_reaches_a_foundation_reports_it_as_not_cut(self):
        pad = self.found(kind="poer", level=self.l1, point=[1500, 1500], width=1000, length=1000, height=500, top=0)
        v = self.run_("model.add_extrusion", level=self.l1, points=[[1000, 1000], [2000, 1000], [2000, 2000], [1000, 2000]], void=True, base=-1.0, depth=2.0)["id"]
        rows = self.p.cutting_report(v)["elements"]
        row = next(r for r in rows if r["id"] == pad)
        self.assertEqual(row["state"], "unsupported")
        self.assertAlmostEqual(self.vol(pad), 0.5, places=9)                                       # nothing was faked


class Drawing(Case):
    def test_a_section_through_a_layered_slab_tags_every_cut_region_with_its_material(self):
        from applications.SPADRA import documentation as D
        s = self.slab(below=[{"material": "EPS", "thickness": 100}, {"material": "Sand", "thickness": 150}], above=[{"material": "Screed", "thickness": 70}])
        vid = self.run_("view.add_section", a=[0, 2500], b=[6000, 2500])["id"]
        item = next(i for i in D.project_view(self.p, self.p.view(vid), 1000.0)["items"] if i["id"] == s)
        self.assertEqual(sorted(item["cut_material"]), ["Concrete", "EPS", "Sand", "Screed"])
        self.assertEqual(len(item["cut"]), len(item["cut_material"]))
        heights = {m: (min(p[1] for p in loop), max(p[1] for p in loop)) for loop, m in zip(item["cut"], item["cut_material"])}
        self.assertAlmostEqual(heights["Concrete"][0], -150, places=3)
        self.assertAlmostEqual(heights["EPS"][1], -150, places=3)
        self.assertAlmostEqual(heights["Sand"][0], -400, places=3)
        self.assertAlmostEqual(heights["Screed"][1], 70, places=3)

    def test_a_pad_is_a_dashed_outline_in_plan_and_a_strip_carries_its_axis(self):
        pad = self.found(kind="poer", level=self.l1, point=[1000, 1000], width=800, length=800, height=400)
        strip = self.found(kind="strook", level=self.l1, start=[0, 0], end=[4000, 0], width=600, height=400)
        items = {i["id"]: i for i in E.plan_items(self.p, self.l1, 1000.0)}
        self.assertEqual(items[pad]["category"], "foundation")
        self.assertEqual(items[pad]["kind"], "poer")
        self.assertEqual(len(items[pad]["polygon"]), 4)
        self.assertEqual(items[strip]["axis"]["thickness"], 600.0)
        self.assertNotIn("axis", items[pad])


class Commands(Case):
    def test_one_undo_removes_a_foundation_and_redo_brings_it_back(self):
        w = self.wall([0, 0], [5000, 0])
        s = self.found(kind="strook", support=[w], height=500)
        v = self.vol(s)
        self.p.undo()
        self.assertFalse(E.alive(self.p, s))
        self.p.redo()
        self.assertAlmostEqual(self.vol(s), v, places=9)
        self.run_("model.move", id=w, dx=0, dy=1.0, dz=0)
        self.p.undo()
        self.assertEqual(self.meta(s)[f"{NS}:start"], [0.0, 0.0])

    def test_a_refused_edit_leaves_everything_as_it_was(self):
        w = self.wall([0, 0], [5000, 0])
        s = self.found(kind="strook", support=[w], height=500)
        rev, snapshot = self.p.revision, dict(self.meta(s))
        with self.assertRaises(BadRequest):
            self.run_("model.set_property", id=s, key="height", value=1)
        with self.assertRaises(BadRequest):
            self.run_("model.set_property", id=s, key="rotation", value=10)
        self.assertEqual((self.p.revision, dict(self.meta(s))), (rev, snapshot))

    def test_an_array_copy_is_independent_and_does_not_snap_back_to_the_support(self):
        c = self.column([0, 0])
        pad = self.found(kind="poer", support=[c], height=500)
        copy = self.p.execute("model.array", {"ids": [pad], "mode": "linear", "count": 2, "step": [3000.0, 0.0, 0.0], "linked": False})["result"]["ids"][0]       # an unlinked array leaves the copy where it was put
        self.assertEqual(self.meta(copy)[f"{NS}:support"], [])
        self.assertEqual(self.meta(copy)[f"{NS}:point"], [3.0, 0.0])
        self.run_("model.move", id=c, dx=1.0, dy=0, dz=0)
        self.assertEqual(self.meta(copy)[f"{NS}:point"], [3.0, 0.0])
        self.assertEqual(self.meta(pad)[f"{NS}:point"], [1.0, 0.0])

    def test_the_browser_schedule_count_and_sfb_know_foundations(self):
        w = self.wall([0, 0], [5000, 0])
        s = self.found(kind="strook", support=[w], height=500, bars=[{"diameter": 12, "count": 4, "role": "long"}], concrete_class="C20/25")
        slab = self.slab(below=[{"material": "EPS", "thickness": 100, "conductivity": 0.036}])
        categories = next(n for n in self.p.tree()["sections"] if n["id"] == "categories")["children"]
        listed = next(c for c in categories if c["label"] == "Foundations")
        self.assertEqual(sorted(x["id"] for x in listed["children"]), sorted([s, slab]))
        from applications.SPADRA import documentation as D
        schedule = D.schedule_data(self.p, "foundation")
        counts = {r["category"]: r["count"] for r in D.schedule_data(self.p, "count")["rows"]}
        self.assertEqual(counts["Foundations"], 2)
        rows = {r["id"]: r for r in schedule["rows"]}
        self.assertEqual(rows[s]["type"], "Strip foundation")
        self.assertEqual(rows[s]["stands_on"], self.p.model.get(w)["name"])
        self.assertAlmostEqual(rows[s]["concrete"], 5.0 * 0.6 * 0.5, places=3)
        self.assertEqual(rows[s]["class"], "C20/25")
        self.assertEqual(rows[s]["bars"], 4)
        self.assertIn("EPS", rows[slab]["layers"])
        self.assertTrue(str(rows[slab]["r_value"]).endswith("+"))                                # incomplete: the slab has no conductivity
        self.assertEqual(schedule["totals"]["concrete"], round(5.0 * 0.6 * 0.5 + 30 * 0.15, 3))
        self.assertTrue(all(r["no"].startswith("FD") for r in schedule["rows"]))
        self.assertEqual(self.meta(slab)[f"{NS}:sfb"], "(13.2)")

    def test_the_properties_panel_shows_the_foundation_and_says_the_reinforcement_is_declared(self):
        c = self.column([0, 0])
        pad = self.found(kind="poer", support=[c], height=500, bars=[{"diameter": 12, "spacing": 150, "role": "x"}])
        panel = self.p.properties({"model": pad})
        titles = [g["title"] for g in panel["groups"]]
        self.assertEqual(titles[:5], ["Geometry", "Constraints", "Concrete and reinforcement", "Quantities", "Identity"][:5])
        rf = next(g for g in panel["groups"] if g["title"] == "Concrete and reinforcement")["rows"]
        self.assertTrue(any("Declared, not designed or verified" in str(r["value"]) for r in rf))
        self.run_("model.set_property", id=pad, key="height", value=700)
        self.assertAlmostEqual(self.meta(pad)[f"{NS}:height"], 0.7, places=9)

    def test_it_survives_save_and_reopen_with_its_follow_behaviour(self):
        w1, w2 = self.wall([0, 0], [6000, 0]), self.wall([6000, 0], [6000, 5000])
        for w in (w1, w2):
            self.run_("model.set_property", id=w, key="base_offset", value=-500)
        a, b = self.found(kind="strook", support=[w1], width=600, height=500), self.found(kind="strook", support=[w2], width=600, height=500)
        slab = self.slab(below=[{"material": "EPS", "thickness": 100, "conductivity": 0.036}], bars=[{"diameter": 10, "spacing": 200, "role": "x"}])
        vols = [self.vol(i) for i in (a, b, slab)]
        with tempfile.TemporaryDirectory() as tmp:
            self.p.save(Path(tmp) / "f.spadra")
            q = Project.open(Path(tmp) / "f.spadra")
            self.assertEqual(q.check()["issues"], [])
            self.assertEqual([q.model.volume(i, unit="m") for i in (a, b, slab)], vols)
            q.execute("model.set_wall_ends", {"id": w2, "end": [6000, 4000]})
            self.assertAlmostEqual(q.model.volume(b, unit="m"), 4.0 * 0.6 * 0.5, places=9)       # the mitred corner is re-cut after reopening, too
            self.assertEqual(FD.quantities(q, slab)["reinforcement"]["bars"], FD.quantities(self.p, slab)["reinforcement"]["bars"])


if __name__ == "__main__":
    unittest.main()
