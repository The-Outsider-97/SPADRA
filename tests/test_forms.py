"""Extrusions, voids, sweeps and arrays: the shapes are exact (volumes are the ones the dimensions give), a void cuts only what can be cut exactly and
says so about the rest, and every one of them is one undoable change that follows later edits."""
from __future__ import annotations

import math
import unittest

from .. import elements as E
from .. import forms as FX
from .. import geometry as G
from ..project import Project
from ...tools.errors import BadRequest
from .test_wall_joins import Joints

SQUARE = [[1000, 1000], [2000, 1000], [2000, 2000], [1000, 2000]]


class Base(Joints):
    def run_(self, op, **p):
        return self.p.execute(op, {"level": self.level, **p})["result"]

    def vol(self, oid):
        return self.p.model.volume(oid, unit="m")

    def floor(self, w=6000, d=5000):
        return self.run_("model.add_floor", points=[[0, 0], [w, 0], [w, d], [0, d]], thickness=200)["id"]

    def void(self, points=SQUARE, **extra):
        return self.run_("model.add_extrusion", points=points, void=True, **extra)["id"]

    def issues(self):
        return [i["message"] for i in E.check(self.p)]


class Extrusion(Base):
    def test_volume_is_area_times_depth(self):
        e = self.run_("model.add_extrusion", points=[[0, 0], [2000, 0], [2000, 1500], [0, 1500]], depth=0.8, base=0.3)["id"]
        self.assertAlmostEqual(self.vol(e), 2 * 1.5 * 0.8, places=9)
        lo = min(v[2] for v in self.p.model.tessellate(e)["vertices"])
        z = self.p.model.world_transform(e)[11]
        self.assertAlmostEqual(lo + z, 0.3, places=9)                           # it stands on its base

    def test_a_concave_outline_is_exact(self):
        l_shape = [[0, 0], [3000, 0], [3000, 1000], [1000, 1000], [1000, 3000], [0, 3000]]
        e = self.run_("model.add_extrusion", points=l_shape, depth=1.0)["id"]
        self.assertAlmostEqual(self.vol(e), 5.0, places=9)

    def test_edits_move_and_turn_rebuild_from_parameters(self):
        e = self.run_("model.add_extrusion", points=SQUARE, depth=1.0)["id"]
        self.p.execute("model.set_property", {"id": e, "key": "depth", "value": 2.5})
        self.assertAlmostEqual(self.vol(e), 2.5, places=9)
        self.p.execute("model.move", {"id": e, "dx": 1.0, "dy": 0.0, "dz": 0.5})
        self.assertEqual(E.meta_of(self.p, e)["spadra:profile"][0], [2.0, 1.0])
        self.assertEqual(E.meta_of(self.p, e)["spadra:base"], 0.5)
        self.p.execute("model.rotate", {"ids": [e], "centre": [0, 0], "angle": 90})
        self.assertAlmostEqual(E.meta_of(self.p, e)["spadra:profile"][0][0], -1.0, places=9)
        self.assertAlmostEqual(self.vol(e), 2.5, places=9)

    def test_bad_outlines_and_depths_are_refused_whole(self):
        before = self.p.revision
        with self.assertRaises(BadRequest):
            self.run_("model.add_extrusion", points=[[0, 0], [1000, 1000], [1000, 0], [0, 1000]], depth=1.0)       # bow-tie
        with self.assertRaises(BadRequest):
            self.run_("model.add_extrusion", points=SQUARE, depth=0.0)
        self.assertEqual(self.p.revision, before)

    def test_is_one_undo_and_appears_in_quantities_with_its_material(self):
        e = self.run_("model.add_extrusion", points=SQUARE, depth=1.0, material="Timber")["id"]
        self.assertEqual(E.properties(self.p, e)["top"][-1]["value"], "Timber")
        rows = {r["id"]: r for r in __import__("applications.SPADRA.quantities", fromlist=["x"]).take_off(self.p, None)["rows"]}
        self.assertAlmostEqual(rows[e]["volume_m3"], 1.0, places=9)
        self.p.undo()
        self.assertFalse(E.alive(self.p, e))


class Voids(Base):
    def test_a_void_inside_a_floor_makes_a_hole_in_it(self):
        f = self.floor()
        self.assertAlmostEqual(self.vol(f), 6.0, places=9)
        v = self.void()
        self.assertAlmostEqual(self.vol(f), 5.8, places=9)                       # 1 m² × 200 mm gone
        item = next(i for i in E.plan_items(self.p, self.level, 1000.0) if i["id"] == f)
        self.assertEqual(len(item["holes"]), 1)
        self.assertEqual(sorted(map(tuple, item["holes"][0])), sorted(map(tuple, [[float(a), float(b)] for a, b in SQUARE])))
        self.assertEqual(FX.report(self.p, v)["cuts"], ["Floor"])

    def test_moving_resizing_or_deleting_the_void_updates_the_floor(self):
        f = self.floor()
        v = self.void()
        self.p.execute("model.move", {"id": v, "dx": 1.0, "dy": 0.0, "dz": 0.0})
        self.assertAlmostEqual(self.vol(f), 5.8, places=9)
        self.p.execute("model.move", {"id": v, "dx": 10.0, "dy": 0.0, "dz": 0.0})            # now clear of the floor
        self.assertAlmostEqual(self.vol(f), 6.0, places=9)
        self.assertTrue(any("does not touch anything" in m for m in self.issues()))
        self.p.execute("model.move", {"id": v, "dx": -10.0, "dy": 0.0, "dz": 0.0})
        self.assertAlmostEqual(self.vol(f), 5.8, places=9)
        self.p.execute("model.delete", {"id": v})
        self.assertAlmostEqual(self.vol(f), 6.0, places=9)
        self.p.undo()
        self.assertAlmostEqual(self.vol(f), 5.8, places=9)

    def solid(self, oid):
        st = E.solid_status(self.p, oid)
        self.assertEqual(st.state, "solid", st.message)                              # a cut that left an open or invalid shell would show here, not as a volume
        return st

    def test_moving_the_floor_under_a_void_follows(self):
        f = self.floor()
        self.void()
        self.p.execute("model.move", {"id": f, "dx": 1.5, "dy": 0.0, "dz": 0.0})              # the void now crosses the floor's edge: it takes a notch out of it
        self.assertAlmostEqual(self.vol(f), 6.0 - 0.1, places=9)                              # the 0.5 m x 1 m of the void that is still over the floor, 200 mm thick
        self.solid(f)
        self.assertFalse(any("crosses the edge" in m for m in self.issues()))
        self.p.execute("model.move", {"id": f, "dx": -1.5, "dy": 0.0, "dz": 0.0})             # and back: a hole again
        self.assertAlmostEqual(self.vol(f), 5.8, places=9)
        self.assertEqual(E.meta_of(self.p, f)["spadra:cut_outline"], [])

    def test_a_void_across_the_edge_takes_a_notch_out_of_it(self):
        f = self.floor()
        v = self.void([[-500, 1000], [500, 1000], [500, 2000], [-500, 2000]])
        self.assertAlmostEqual(self.vol(f), 6.0 - 0.1, places=9)
        self.solid(f)
        self.assertEqual(FX.report(self.p, v)["cuts"], ["Floor"])
        item = next(i for i in E.plan_items(self.p, self.level, 1000.0) if i["id"] == f)
        self.assertNotIn("holes", item)
        self.assertEqual(len(item["polygon"]), 8)                                       # the rectangle with the notch in its left edge
        self.assertAlmostEqual(abs(G.signed_area([tuple(q) for q in item["polygon"]])), 29.5e6, places=3)
        self.p.execute("model.delete", {"id": v})
        self.assertAlmostEqual(self.vol(f), 6.0, places=9)                                  # the floor is whole again
        self.p.undo()
        self.assertAlmostEqual(self.vol(f), 5.9, places=9)

    def test_a_void_lying_along_the_edge_is_a_clean_notch_with_no_sliver(self):
        f = self.floor()
        self.void([[0, 1000], [1000, 1000], [1000, 2000], [0, 2000]])                      # its left side is the floor's left edge
        self.assertAlmostEqual(self.vol(f), 5.8, places=9)
        item = next(i for i in E.plan_items(self.p, self.level, 1000.0) if i["id"] == f)
        self.assertEqual(len(item["polygon"]), 8)
        self.solid(f)

    def test_a_void_is_cut_from_a_solid_extrusion_at_its_edge_too(self):
        e = self.run_("model.add_extrusion", points=[[0, 0], [3000, 0], [3000, 2000], [0, 2000]], depth=1.0)["id"]
        self.assertAlmostEqual(self.vol(e), 6.0, places=9)
        v = self.void([[-500, 500], [1000, 500], [1000, 1500], [-500, 1500]], base=-0.5, depth=3.0)
        self.assertAlmostEqual(self.vol(e), 6.0 - 1.0, places=9)                             # 1 m wide x 1 m high x 1 m deep
        self.solid(e)
        self.assertEqual(len(E.meta_of(self.p, e)["spadra:cut_outline"]), 8)
        self.assertEqual(FX.report(self.p, v)["cuts"], ["Extrusion"])

    def test_a_void_that_would_split_the_floor_in_two_cuts_nothing_and_says_why(self):
        f = self.floor()
        v = self.void([[2000, -100], [3000, -100], [3000, 5100], [2000, 5100]])
        self.assertAlmostEqual(self.vol(f), 6.0, places=9)
        self.assertEqual(FX.report(self.p, v)["cuts"], [])
        self.assertTrue(any("separate pieces" in n for n in FX.report(self.p, v)["notes"]))
        self.assertTrue(any("separate pieces" in m for m in self.issues()))

    def test_a_second_void_that_would_split_it_is_left_out_and_the_first_still_cuts(self):
        f = self.floor()
        first = self.void()
        self.void([[3000, -100], [4000, -100], [4000, 5100], [3000, 5100]])
        self.assertAlmostEqual(self.vol(f), 5.8, places=9)
        self.assertEqual(FX.report(self.p, first)["cuts"], ["Floor"])
        self.solid(f)

    def test_voids_that_together_remove_the_whole_floor_leave_the_second_out_and_say_so(self):
        f = self.floor(w=2000, d=2000)
        a = self.void([[-100, -100], [1100, -100], [1100, 2100], [-100, 2100]])
        b = self.void([[900, -100], [2100, -100], [2100, 2100], [900, 2100]])
        self.assertAlmostEqual(self.vol(f), 0.9 * 2.0 * 0.2, places=9)                    # only the first is cut: it leaves a strip 0.9 m wide
        self.assertEqual(FX.report(self.p, a)["cuts"], ["Floor"])
        self.assertEqual(FX.report(self.p, b)["cuts"], [])
        self.assertTrue(any("leaves nothing of Floor" in n for n in FX.report(self.p, b)["notes"]))
        self.solid(f)

    def test_a_void_covering_the_whole_floor_is_not_cut_away(self):
        f = self.floor(w=2000, d=2000)
        v = self.void([[-100, -100], [2100, -100], [2100, 2100], [-100, 2100]])
        self.assertAlmostEqual(self.vol(f), 4.0 * 0.2, places=9)
        self.assertTrue(any("covers all" in n for n in FX.report(self.p, v)["notes"]))

    def test_two_holes_that_touch_at_a_corner_are_not_both_cut_and_the_second_says_why(self):
        f = self.floor()
        self.void()
        v = self.void([[2000, 2000], [3000, 2000], [3000, 3000], [2000, 3000]])             # meets the first at the point (2, 2)
        self.assertAlmostEqual(self.vol(f), 5.8, places=9)
        self.assertTrue(any("single point" in n for n in FX.report(self.p, v)["notes"]))

    def test_a_void_that_stops_inside_the_slab_cuts_nothing(self):
        f = self.floor()
        v = self.void(base=-0.1, depth=1.0)                                   # the slab is -0.2 to 0: this starts half way through
        self.assertAlmostEqual(self.vol(f), 6.0, places=9)
        self.assertTrue(any("full thickness" in n for n in FX.report(self.p, v)["notes"]))

    def test_overlapping_voids_in_one_floor_act_as_their_union(self):
        f = self.floor()
        a = self.void()
        b = self.void([[1500, 1500], [2500, 1500], [2500, 2500], [1500, 2500]])
        self.assertAlmostEqual(self.vol(f), 6.0 - 1.75 * 0.2, places=9)                    # 1 + 1 - 0.25 m² gone, 200 mm thick
        self.solid(f)
        self.assertEqual(len(E.meta_of(self.p, f)["spadra:cut"]), 1)                       # one hole, the union's outline
        self.assertEqual(FX.report(self.p, a)["cuts"], ["Floor"])
        self.assertEqual(FX.report(self.p, b)["cuts"], ["Floor"])
        self.p.execute("model.delete", {"id": b})
        self.assertAlmostEqual(self.vol(f), 5.8, places=9)

    def test_overlapping_voids_that_reach_the_edge_notch_it_once(self):
        f = self.floor()
        self.void([[-500, 1000], [1500, 1000], [1500, 2000], [-500, 2000]])
        self.void([[1000, 1500], [2000, 1500], [2000, 3000], [1000, 3000]])
        # first: 1.5 x 1 = 1.5 m² over the floor; second: 1 x 1.5 = 1.5 m²; they share 0.5 x 0.5 = 0.25 m²
        self.assertAlmostEqual(self.vol(f), 6.0 - (1.5 + 1.5 - 0.25) * 0.2, places=9)
        self.solid(f)

    def test_two_separate_voids_make_two_holes(self):
        f = self.floor()
        self.void()
        self.void([[3000, 1000], [4000, 1000], [4000, 2000], [3000, 2000]])
        self.assertAlmostEqual(self.vol(f), 5.6, places=9)

    def test_a_rectangular_void_across_a_wall_makes_an_opening(self):
        w = self.wall((0, 0), (6000, 0))
        full = self.vol(w)
        self.assertAlmostEqual(full, 6 * 3 * 0.2, places=9)
        v = self.void([[1000, -300], [2000, -300], [2000, 300], [1000, 300]], base=0.0, depth=2.1)
        self.assertAlmostEqual(self.vol(w), full - 1 * 2.1 * 0.2, places=9)
        self.assertEqual(FX.report(self.p, v)["cuts"], ["Basic Wall"])
        self.p.execute("model.make_void", {"ids": [v], "void": False})                # as a solid it no longer cuts
        self.assertAlmostEqual(self.vol(w), full, places=9)

    def test_a_void_can_take_a_notch_out_of_the_top_or_cut_a_wall_in_two(self):
        w = self.wall((0, 0), (6000, 0))
        self.void([[1000, -300], [2000, -300], [2000, 300], [1000, 300]], base=2.5, depth=2.0)
        self.assertAlmostEqual(self.vol(w), 3.6 - 1 * 0.5 * 0.2, places=9)

    def test_a_void_in_a_wall_also_follows_the_wall_and_hosted_openings_may_not_overlap_it(self):
        w = self.wall((0, 0), (6000, 0))
        self.void([[1000, -300], [2000, -300], [2000, 300], [1000, 300]], base=0.0, depth=2.1)
        self.p.execute("model.move", {"id": w, "dx": 0.0, "dy": 1.0, "dz": 0.0})              # the wall leaves the void behind
        self.assertAlmostEqual(self.vol(w), 3.6, places=9)
        self.p.execute("model.move", {"id": w, "dx": 0.0, "dy": -1.0, "dz": 0.0})
        self.assertAlmostEqual(self.vol(w), 3.6 - 0.42, places=9)
        before = self.p.revision
        with self.assertRaises(BadRequest):
            self.p.execute("model.add_door", {"wall": w, "offset": 1.5, "width": 900})
        self.assertEqual(self.p.revision, before)

    def test_a_void_that_is_not_through_the_whole_wall_or_not_square_to_it_cuts_nothing(self):
        w = self.wall((0, 0), (6000, 0))
        niche = self.void([[1000, -300], [2000, -300], [2000, 50], [1000, 50]], base=0.0, depth=2.1)
        slanted = self.void([[3000, -300], [4000, -300], [4300, 300], [3300, 300]], base=0.0, depth=2.1)
        self.assertAlmostEqual(self.vol(w), 3.6, places=9)
        self.assertTrue(any("niche" in n for n in FX.report(self.p, niche)["notes"]))
        self.assertTrue(any("not a rectangle" in n for n in FX.report(self.p, slanted)["notes"]))

    def test_an_l_shaped_void_whose_arm_runs_square_through_the_wall_cuts_the_opening_the_arm_makes(self):
        w = self.wall((0, 0), (6000, 0))
        v = self.void([[1000, -300], [2000, -300], [2000, 300], [3000, 300], [3000, 800], [1000, 800]], base=0.0, depth=2.1)       # the foot lies clear of the wall
        self.assertAlmostEqual(self.vol(w), 3.6 - 1.0 * 0.2 * 2.1, places=9)
        self.assertEqual(FX.report(self.p, v)["cuts"], ["Basic Wall"])
        self.assertEqual(E.solid_status(self.p, w).state, "solid")

    def test_a_round_void_through_a_wall_is_not_cut_because_its_opening_is_not_a_rectangle(self):
        w = self.wall((0, 0), (6000, 0))
        octagon = [[1500 + 400 * math.cos(math.pi / 4 * i), 400 * math.sin(math.pi / 4 * i)] for i in range(8)]
        v = self.void(octagon, base=0.0, depth=2.1)
        self.assertAlmostEqual(self.vol(w), 3.6, places=9)
        self.assertTrue(any("not a rectangle" in n for n in FX.report(self.p, v)["notes"]))

    def test_a_void_cuts_a_solid_extrusion_too(self):
        e = self.run_("model.add_extrusion", points=[[0, 0], [4000, 0], [4000, 4000], [0, 4000]], depth=1.0)["id"]
        self.void(base=-0.5, depth=3.0)
        self.assertAlmostEqual(self.vol(e), 15.0, places=9)
        self.assertEqual(len(E.meta_of(self.p, e)["spadra:cut"]), 1)

    def test_make_void_switches_both_ways_and_drops_the_material(self):
        e = self.run_("model.add_extrusion", points=SQUARE, depth=1.0)["id"]
        self.p.execute("model.make_void", {"ids": [e]})
        self.assertEqual(E.category(self.p, e), "void")
        self.assertNotIn("spadra:material", E.meta_of(self.p, e))
        self.p.execute("model.set_property", {"id": e, "key": "form", "value": "Solid"})
        self.assertEqual(E.category(self.p, e), "extrusion")
        with self.assertRaises(BadRequest):
            self.p.execute("model.make_void", {"ids": [e], "void": False})
        w = self.wall((0, 0), (1000, 0))
        with self.assertRaises(BadRequest):
            self.p.execute("model.make_void", {"ids": [w]})

    def test_a_void_has_no_quantities_and_is_left_out_of_exports(self):
        from .. import interop as IO
        from .. import quantities as Q
        w = self.wall((0, 0), (6000, 0))
        v = self.void([[1000, -300], [2000, -300], [2000, 300], [1000, 300]], base=0.0, depth=2.1)
        self.assertNotIn(v, {r["id"] for r in Q.take_off(self.p, None)["rows"]})
        with self.assertRaises(BadRequest):
            Q.take_off(self.p, [v])
        data, report = IO.export_model(self.p, "obj")
        self.assertNotIn(b"Void", data)
        self.assertEqual(report["converted"]["voids_left_out"], 1)
        self.assertIn(w, {r["id"] for r in Q.take_off(self.p, None)["rows"]})                  # the wall itself is still measured


class Sweeps(Base):
    def test_volume_is_section_area_times_path_length_on_a_straight_run(self):
        s = self.run_("model.add_sweep", points=[[0, 0], [4000, 0]], shape="rectangle", width=200, height=300)["id"]
        self.assertAlmostEqual(self.vol(s), 0.2 * 0.3 * 4.0, places=9)
        z = [v[2] + self.p.model.world_transform(s)[11] for v in self.p.model.tessellate(s)["vertices"]]
        self.assertAlmostEqual(min(z), 0.0, places=9)
        self.assertAlmostEqual(max(z), 0.3, places=9)

    def test_a_mitred_corner_keeps_the_centre_line_volume_and_a_closed_ring_is_a_ring(self):
        s = self.run_("model.add_sweep", points=[[0, 0], [3000, 0], [3000, 3000]], shape="rectangle", width=200, height=300)["id"]
        self.assertAlmostEqual(self.vol(s), 0.06 * 6.0, places=9)                 # a centred section round a mitre keeps the centre-line length
        ring = self.run_("model.add_sweep", points=[[0, 0], [3000, 0], [3000, 3000], [0, 3000]], closed=True, shape="rectangle", width=200, height=300)["id"]
        self.assertAlmostEqual(self.vol(ring), 0.06 * 12.0, places=9)
        item = next(i for i in E.plan_items(self.p, self.level, 1000.0) if i["id"] == ring)
        self.assertEqual(len(item["holes"]), 1)
        self.assertTrue(item["closed"])

    def test_a_custom_section_and_edits(self):
        s = self.run_("model.add_sweep", points=[[0, 0], [2000, 0]], section=[[0, 0], [0.1, 0], [0.1, 0.2], [0.05, 0.2], [0.05, 0.05], [0, 0.05]])["id"]
        self.assertAlmostEqual(self.vol(s), (0.1 * 0.05 + 0.05 * 0.15) * 2.0, places=9)
        r = self.run_("model.add_sweep", points=[[0, 0], [2000, 0]], shape="rectangle", width=100, height=100)["id"]
        self.p.execute("model.set_property", {"id": r, "key": "height", "value": 400})
        self.assertAlmostEqual(self.vol(r), 0.1 * 0.4 * 2.0, places=9)
        self.p.execute("model.set_property", {"id": r, "key": "shape", "value": "round"})
        self.assertLess(self.vol(r), 0.1 * 0.4 * 2.0)
        self.p.execute("model.move", {"id": r, "dx": 0.0, "dy": 1.0, "dz": 1.0})
        self.assertEqual(E.meta_of(self.p, r)["spadra:path"][0], [0.0, 1.0])

    def test_what_a_profile_cannot_get_round_is_refused(self):
        before = self.p.revision
        with self.assertRaises(BadRequest):
            self.run_("model.add_sweep", points=[[0, 0], [3000, 0], [0, 100]], shape="rectangle", width=100, height=100)              # a hairpin
        with self.assertRaises(BadRequest):
            self.run_("model.add_sweep", points=[[0, 0], [3000, 0], [3000, 300], [3000, 3000]][:3], shape="rectangle", width=2000, height=100)   # wider than the leg
        with self.assertRaises(BadRequest):
            self.run_("model.add_sweep", points=[[0, 0], [3000, 3000], [3000, 0], [0, 3000]], shape="rectangle", width=100, height=100)  # crosses itself
        with self.assertRaises(BadRequest):
            self.run_("model.add_sweep", points=[[0, 0]], shape="rectangle")
        self.assertEqual(self.p.revision, before)


class Hinges(Base):
    """A door or window turns about its hinge edge (what the 2D drawing shows), so its object origin is the hinge axis, not the centre of the leaf."""

    def hinge_and_leaf(self, oid):
        t = self.p.model.world_transform(oid)
        g = self.p.model.get(oid)["geometry"]
        return (t[3], t[7]), g

    def test_the_origin_is_the_hinge_edge_for_doors_and_windows_either_way(self):
        w = self.wall((0, 0), (4000, 0))
        d = self.p.execute("model.add_door", {"wall": w, "offset": 2.0, "width": 900})["result"]["id"]
        win = self.p.execute("model.add_window", {"wall": w, "offset": 3.2, "width": 1200})["result"]["id"]
        for oid, x0, x1 in ((d, 1.55, 2.45), (win, 2.6, 3.8)):
            for hinge, edge in (("start", x0), ("end", x1)):
                self.run_("model.set_property", id=oid, key="hinge", value=hinge)
                (ox, oy), g = self.hinge_and_leaf(oid)
                self.assertAlmostEqual(ox, edge, places=6)                           # on the wall's line, at the hinge edge
                cx = g["center"][0]
                self.assertAlmostEqual(ox + cx, (x0 + x1) / 2, places=6)             # the leaf itself did not move
                self.assertAlmostEqual(abs(cx), (x1 - x0) / 2, places=6)             # it lies to one side of the hinge, the full width away
        self.assertAlmostEqual(self.hinge_and_leaf(win)[0][1], 0.0, places=6)         # a window pivots on the wall's centre line
        self.assertAlmostEqual(abs(self.hinge_and_leaf(d)[0][1]), 0.1, places=6)      # a door on the wall face (200 mm wall)

    def test_turning_the_door_leaves_the_hinge_edge_in_place(self):
        w = self.wall((0, 0), (4000, 0))
        d = self.p.execute("model.add_door", {"wall": w, "offset": 2.0, "width": 900})["result"]["id"]
        self.run_("model.set_property", id=d, key="hinge", value="start")
        t = self.p.model.world_transform(d)
        g = self.p.model.get(d)["geometry"]
        hinge_edge = (t[3] + g["center"][0] - g["size"][0] / 2, t[7])
        self.assertAlmostEqual(hinge_edge[0], t[3], places=6)                        # the leaf's hinge-side edge is exactly at the origin
        self.assertAlmostEqual(g["center"][0] - g["size"][0] / 2, 0.0, places=6)

    def test_moving_the_wall_or_the_opening_keeps_the_origin_on_the_hinge(self):
        w = self.wall((0, 0), (4000, 0))
        d = self.p.execute("model.add_door", {"wall": w, "offset": 2.0, "width": 900})["result"]["id"]
        self.run_("model.set_property", id=d, key="hinge", value="end")
        self.run_("model.move", id=w, dx=0.0, dy=1.0)
        (ox, oy), g = self.hinge_and_leaf(d)
        self.assertAlmostEqual(ox, 2.45, places=6)
        self.assertAlmostEqual(oy, 1.0 + 0.1, places=6)


class Arrays(Base):
    def test_a_linear_array_of_walls_with_their_door(self):
        w = self.wall((0, 0), (4000, 0))
        d = self.p.execute("model.add_door", {"wall": w, "offset": 2.0})["result"]["id"]
        r = self.p.execute("model.array", {"ids": [w, d], "mode": "linear", "count": 4, "step": [0, 3000]})["result"]
        self.assertEqual(len(r["ids"]), 3)                                          # the door travelled with its wall, it is not a fourth item
        walls = [i for i in E.element_ids(self.p) if E.category(self.p, i) == "wall"]
        doors = [i for i in E.element_ids(self.p) if E.category(self.p, i) == "door"]
        self.assertEqual((len(walls), len(doors)), (4, 4))
        ys = sorted(E.wall_frame(self.p, i)["start"][1] for i in walls)
        self.assertEqual(ys, [0.0, 3.0, 6.0, 9.0])
        for i in doors:
            self.assertIn(E.m(E.meta_of(self.p, i), "host"), walls)
        copy_door = next(i for i in doors if i != d and E.m(E.meta_of(self.p, i), "host") == r["ids"][0])
        self.assertAlmostEqual(self.p.model.world_transform(copy_door)[7], 3.0 + 0.1, places=6)        # a door's origin is its hinge, on the wall face
        self.p.undo()
        self.assertEqual(len([i for i in E.element_ids(self.p) if E.category(self.p, i) == "wall"]), 1)

    def test_a_radial_array_spreads_evenly_round_the_circle_or_over_an_angle(self):
        c = self.p.execute("model.add_column", {"level": self.level, "point": [2000, 0], "width": 300, "depth": 300, "base": 0, "top": 3})["result"]["id"]
        r = self.p.execute("model.array", {"ids": [c], "mode": "radial", "count": 4, "centre": [0, 0], "angle": 360})["result"]
        pts = [E.m(E.meta_of(self.p, i), "point") for i in r["ids"]]
        for got, want in zip(pts, [(0.0, 2.0), (-2.0, 0.0), (0.0, -2.0)]):
            self.assertAlmostEqual(got[0], want[0], places=9)
            self.assertAlmostEqual(got[1], want[1], places=9)
        self.assertAlmostEqual(E.m(E.meta_of(self.p, r["ids"][0]), "rotation"), math.pi / 2, places=9)
        r2 = self.p.execute("model.array", {"ids": [c], "mode": "radial", "count": 3, "centre": [0, 0], "angle": 90})["result"]
        last = E.m(E.meta_of(self.p, r2["ids"][-1]), "point")
        self.assertAlmostEqual(last[0], 0.0, places=9)
        self.assertAlmostEqual(last[1], 2.0, places=9)

    def test_copies_are_independent_and_walls_rejoin_where_copies_meet(self):
        a = self.wall((0, 0), (3000, 0))
        self.p.execute("model.array", {"ids": [a], "mode": "radial", "count": 4, "centre": [0, 0], "angle": 360})
        walls = [i for i in E.element_ids(self.p) if E.category(self.p, i) == "wall"]
        self.assertEqual(len(walls), 4)
        self.assertTrue(all(len(E.m(E.meta_of(self.p, i), "joins")) >= 1 for i in walls))      # four walls meeting at the centre are joined
        self.p.execute("model.set_property", {"id": walls[1], "key": "height", "value": 5})
        self.assertEqual(sorted(E.m(E.meta_of(self.p, i), "height") for i in walls), [3, 3, 3, 5])

    def test_arrays_of_extrusions_voids_and_sweeps_keep_cutting(self):
        f = self.floor(w=12000)
        v = self.void()
        self.p.execute("model.array", {"ids": [v], "mode": "linear", "count": 3, "step": [3000, 0]})
        self.assertAlmostEqual(self.vol(f), 12 * 5 * 0.2 - 3 * 0.2, places=9)
        s = self.run_("model.add_sweep", points=[[0, 0], [1000, 0]], shape="rectangle", width=100, height=100)["id"]
        r = self.p.execute("model.array", {"ids": [s], "mode": "linear", "count": 2, "step": [0, 500, 1000]})["result"]      # the step is in drawing units, z too
        self.assertEqual(E.m(E.meta_of(self.p, r["ids"][0]), "base"), 1.0)

    def test_what_cannot_be_arrayed_or_is_too_much_is_refused_whole(self):
        w = self.wall((0, 0), (3000, 0))
        d = self.p.execute("model.add_door", {"wall": w, "offset": 1.5})["result"]["id"]
        room = self.p.execute("model.add_room", {"level": self.level, "points": [[0, 0], [1000, 0], [1000, 1000], [0, 1000]], "name": "R"})["result"]["id"]
        before = self.p.revision
        n = len(self.p.model.query_ids(limit=1000))
        for params in ({"ids": [d], "mode": "radial", "count": 3, "centre": [0, 0], "angle": 360},
                       {"ids": [room], "mode": "linear", "count": 3, "step": [1000, 0]},
                       {"ids": [w], "mode": "linear", "count": 3, "step": [0, 0]},
                       {"ids": [w], "mode": "linear", "count": 1, "step": [1000, 0]},
                       {"ids": [w], "mode": "linear", "count": 501, "step": [1000, 0]},
                       {"ids": [w], "mode": "radial", "count": 3, "centre": [0, 0], "angle": 0},
                       {"ids": ["nope"], "mode": "linear", "count": 3, "step": [1000, 0]},
                       {"ids": [w], "mode": "spiral", "count": 3}):
            with self.assertRaises(Exception):
                self.p.execute("model.array", params)
        self.assertEqual(self.p.revision, before)
        self.assertEqual(len(self.p.model.query_ids(limit=1000)), n)


class NativeModifierCommands(unittest.TestCase):
    """Regression tests for the existing SLAI SGEO/SDRW modifier integration."""

    def setUp(self):
        self.p = Project.create("Modifier checks")

    def execute(self, op, **params):
        return self.p.execute(op, params)["result"]

    def generic_box(self, x=0.0):
        return self.p.model.add_box(width=2, depth=2, height=2, center=(x, 0, 0), name="Independent solid")

    def scene_bounds(self, oid):
        obj = next(obj for obj in self.p.scene_data()["objects"] if obj["id"] == oid)
        return ([min(v[i] for v in obj["vertices"]) for i in range(3)],
                [max(v[i] for v in obj["vertices"]) for i in range(3)])

    def test_exact_analytic_split_changes_view_not_authoritative_sgeo(self):
        source = self.generic_box()
        raw_geometry = self.p.model.get(source)["geometry"]
        result = self.execute("model.modifier", id=source, operation="split",
                              policy="exact", parameters={
                                  "origin": [0, 0, 0], "normal": [1, 0, 0], "side": "positive"})
        self.assertEqual(result["mode"], "non_destructive")
        self.assertEqual(self.p.model.get(source)["geometry"], raw_geometry)
        self.assertEqual(len(self.p.model.modifiers()), 1)
        lo, hi = self.scene_bounds(source)
        self.assertAlmostEqual(lo[0], 0.0, places=5)
        self.assertAlmostEqual(hi[0], 1.0, places=5)
        self.p.undo()
        self.assertFalse(self.p.model.modifiers())
        lo, hi = self.scene_bounds(source)
        self.assertAlmostEqual(lo[0], -1.0, places=5)

    def test_native_modifier_properties_are_editable_and_undoable(self):
        source = self.generic_box()
        response = self.execute("model.modifier", id=source, operation="split",
                                policy="exact", parameters={
                                    "origin": [0, 0, 0], "normal": [1, 0, 0], "side": "positive"})
        mid = response["modifier_id"]
        props = self.p.properties({"model": source})
        group = next(g for g in props["groups"] if g["title"] == "Native modifiers")
        self.assertTrue(any(r.get("key") == f"modifier:{mid}:origin:0" for r in group["rows"]))
        self.execute("model.set_modifier", id=source, modifier=mid, key="origin:0", value=0.4)
        self.assertAlmostEqual(self.scene_bounds(source)[0][0], 0.4, places=5)
        self.assertEqual(self.p.model.modifiers()[0]["parameters"]["origin"][0], 0.4)
        with self.assertRaises(BadRequest):
            self.execute("model.set_modifier", id=source, modifier=mid, key="side", value="invalid")
        self.assertAlmostEqual(self.scene_bounds(source)[0][0], 0.4, places=5)
        self.p.undo()
        self.assertAlmostEqual(self.scene_bounds(source)[0][0], 0.0, places=5)

    def test_exact_box_boolean_and_save_reopen(self):
        left, right = self.generic_box(0), self.generic_box(1)
        result = self.execute("model.modifier", id=left, operation="boolean",
                              operand=right, policy="exact",
                              parameters={"operation": "union"})
        self.assertEqual(result["diagnostics"]["approximate"], False)
        self.assertGreater(self.scene_bounds(left)[1][0], 1)
        with __import__("tempfile").TemporaryDirectory() as tmp:
            target = __import__("pathlib").Path(tmp) / "modifiers.spadra"
            self.p.save(target)
            reopened = Project.open(target)
            self.assertEqual(len(reopened.model.modifiers()), 1)
            coordinates = next(o for o in reopened.scene_data()["objects"] if o["id"] == left)["vertices"]
            self.assertAlmostEqual(max(v[0] for v in coordinates), 2.0, places=4)

    def test_a_generic_family_instance_can_be_modified_without_losing_family_parameters(self):
        definition = self.execute("family.define", name="Adjustable Block", shape="box",
                                  parameters={"width": {"default": 2000},
                                              "depth": {"default": 2000},
                                              "height": {"default": 3000}})["id"]
        level = self.p.manifest["levels"][0]["id"]
        oid = self.execute("family.place", family=definition, level=level, point=[0, 0])["id"]
        source = self.p.model.get(oid)
        before_revision = self.p.revision
        response = self.execute("model.modifier", id=oid, operation="split", policy="exact",
                                parameters={"origin": [0, 0, 0], "normal": [1, 0, 0],
                                            "side": "positive"})
        self.assertEqual(response["mode"], "non_destructive")
        self.assertIsNone(response["derived_from"])
        self.assertEqual(self.p.revision, before_revision + 1)
        self.assertEqual(self.p.model.get(oid)["family_id"], definition)
        self.assertEqual(self.p.model.get(oid)["geometry"], source["geometry"])
        self.assertEqual(len(self.p.model.modifiers()), 1)
        self.p.undo()
        self.assertEqual(self.p.model.get(oid)["family_id"], definition)
        self.assertEqual(self.p.model.modifiers(), [])

    def test_detached_copy_modifier_preserves_host_and_original_geometry(self):
        level = self.p.manifest["levels"][0]["id"]
        wall = self.execute("model.add_wall", level=level, start=[0, 0],
                            end=[5000, 0], height=3, thickness=250)["id"]
        before = self.p.model.get(wall)
        answer = self.execute("model.modifier", operation="split", id=wall, policy="exact",
                              independent_copy=True, parameters={
                                  "origin": [0, 0, 1], "normal": [0, 0, 1],
                                  "side": "positive"})
        self.assertNotEqual(wall, answer["id"])
        self.assertEqual(answer["derived_from"], wall)
        self.assertEqual(self.p.model.get(wall), before)
        self.assertEqual(E.category(self.p, answer["id"]), "generic")
        self.assertEqual(len(self.p.model.modifiers()), 1)
        self.p.undo()
        self.assertEqual(self.p.model.get(wall), before)
        self.assertFalse(self.p.model.modifiers())

    def test_semantically_hosted_wall_rejects_freeform_modification_atomically(self):
        level = self.p.manifest["levels"][0]["id"]
        wall = self.execute("model.add_wall", level=level, start=[0, 0],
                            end=[5000, 0], height=3, thickness=250)["id"]
        revision = self.p.revision
        with self.assertRaises(BadRequest):
            self.execute("model.modifier", id=wall, operation="split",
                         parameters={"origin": [0, 0, 1], "normal": [0, 0, 1], "side": "positive"})
        self.assertEqual(self.p.revision, revision)
        self.assertFalse(self.p.model.modifiers())

    def test_subdivision_bakes_derived_mesh_with_undo(self):
        source = self.generic_box()
        original = self.p.model.get(source)["geometry"]
        self.execute("model.modifier", id=source, operation="subdivision",
                     parameters={"max_edge": 0.7})
        output = self.p.model.get(source)["geometry"]
        self.assertEqual(output["kind"], "mesh")
        self.assertGreater(len(output["faces"]), 12)
        self.assertFalse(self.p.model.modifiers())
        self.p.undo()
        self.assertEqual(self.p.model.get(source)["geometry"], original)

    def test_weld_merges_shared_triangle_positions_without_erasing_faces(self):
        oid = self.generic_box()
        existing = self.p.model.tessellate(oid)
        unindexed, faces = [], []
        for triangle in existing["faces"]:
            at = len(unindexed)
            unindexed.extend([existing["vertices"][vertex] for vertex in triangle])
            faces.append([at, at + 1, at + 2])
        self.p.model.set_geometry(oid, {"kind": "mesh", "vertices": unindexed, "faces": faces})
        self.execute("model.modifier", id=oid, operation="weld", parameters={"decimals": None})
        result = self.p.model.get(oid)["geometry"]
        self.assertLess(len(result["vertices"]), len(unindexed))
        self.assertEqual(len(result["faces"]), len(faces))
        self.assertFalse(self.p.model.modifiers())
        self.p.undo()
        self.assertEqual(len(self.p.model.get(oid)["geometry"]["vertices"]), len(unindexed))

    def test_native_sdrw_bend_changes_derived_curve_with_undo(self):
        import math
        view = next(v["id"] for v in self.p.manifest["views"] if v["kind"] == "plan")
        oid = self.p.drawing(view).add_line([0, 0], [3000, 0])
        outcome = self.execute("draw.modifier", drawing=view, id=oid,
                               operation="bend", policy="display_only",
                               parameters={"axis": "x", "start": 0, "end": 2000,
                                           "angle": math.pi / 4, "segments": 16})
        result = self.p.drawing(view).get(outcome["id"])
        self.assertEqual(result["kind"], "polyline")
        self.assertGreater(len(result["geometry"]["points"]), 2)
        self.p.undo()
        self.assertEqual(self.p.drawing(view).get(oid)["kind"], "line")

    def test_native_2d_region_boolean_replaces_source_and_preserves_operand(self):
        try:
            import shapely  # noqa: F401
        except ImportError:
            self.skipTest("Native planar Boolean requires shapely")
        view = next(v["id"] for v in self.p.manifest["views"] if v["kind"] == "plan")
        doc = self.p.drawing(view)
        first = doc.add_polyline([[0, 0], [2000, 0], [2000, 2000], [0, 2000]], closed=True)
        second = doc.add_polyline([[1000, 0], [3000, 0], [3000, 2000], [1000, 2000]], closed=True)
        result = self.execute("draw.modifier", drawing=view, id=first, operand=second,
                              operation="boolean", policy="tolerance_controlled",
                              parameters={"operation": "union"})
        self.assertEqual(self.p.drawing(view).get(result["id"])["kind"], "hatch")
        self.assertNotIn(first, self.p.drawing(view).query_ids(limit=100))
        self.assertIn(second, self.p.drawing(view).query_ids(limit=100))
        self.p.undo()
        self.assertIn(first, self.p.drawing(view).query_ids(limit=100))

    def test_sdrw_chamfer_uses_native_kernel_and_is_undoable(self):
        try:
            import shapely  # noqa: F401
        except ImportError:
            self.skipTest("Native 2D chamfer requires the optional shapely backend")
        view = next(v["id"] for v in self.p.manifest["views"] if v["kind"] == "plan")
        source = self.p.drawing(view).add_polyline([[0, 0], [2000, 0], [2000, 2000]], closed=False)
        original = self.p.drawing(view).get(source)["geometry"]
        result = self.execute("draw.modifier", drawing=view, id=source, operation="chamfer",
                              parameters={"amount": 100, "depth": 100, "segments": 1}, policy="exact")
        self.assertEqual(result["id"], source)
        self.assertGreater(len(self.p.drawing(view).get(source)["geometry"]["points"]), 3)
        self.p.undo()
        self.assertEqual(self.p.drawing(view).get(source)["geometry"], original)


if __name__ == "__main__":
    unittest.main()
