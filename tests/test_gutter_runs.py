"""Connected gutters against the real SGEO engine: pieces on consecutive eaves are one run with mitred corners that meet exactly, free ends are capped or
open and can be extended, the run falls continuously to chosen low points, rain is traced to the downpipe it leaves by, and every change that would break a
run is refused whole and undone as one step."""
from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from applications.SPADRA import elements as E
from applications.SPADRA import geometry as G
from applications.SPADRA import gutters as GU
from applications.SPADRA.project import NS, Project
from applications.tools.errors import BadRequest

RECT = [[0, 0], [10000, 0], [10000, 6000], [0, 6000]]
L_SHAPE = [[0, 0], [10000, 0], [10000, 4000], [5000, 4000], [5000, 8000], [0, 8000]]          # counter-clockwise, with one reflex corner at (5, 4)


def area(poly):
    return abs(G.signed_area([tuple(q) for q in poly]))


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project.create("Runs")
        self.l1, self.l2, self.roof_level = (l["id"] for l in self.p.manifest["levels"])

    def run_(self, op, **params):
        return self.p.execute(op, params)["result"]

    def roof(self, kind="hip", points=RECT):
        return self.run_("model.add_roof", points=points, level=self.roof_level, roof=kind, pitch=30, thickness=200)["id"]

    def meta(self, oid):
        return self.p.model.get(oid)["metadata"]

    def gutters(self, roof, **extra):
        return self.run_("model.add_gutters", roof=roof, **extra)

    def ring(self, **extra):
        roof = self.roof("hip")
        return roof, self.gutters(roof, **extra)["gutters"]

    def mesh(self, g):
        f = GU.frame(self.p, g)
        return GU.segment_mesh(f, self.meta(g)[f"{NS}:gutter"])

    def vol(self, oid):
        return self.p.model.volume(oid, unit="m")


class Assembly(Case):
    def test_every_eave_of_a_hip_roof_is_one_closed_run_of_four_named_pieces(self):
        roof, gs = self.ring()
        self.assertEqual(len(gs), 4)
        self.assertEqual(len({self.meta(g)[f"{NS}:run"] for g in gs}), 1)
        self.assertEqual([self.p.model.get(g)["name"] for g in gs], [f"Half-round gutter · eave {i}" for i in (1, 2, 3, 4)])
        f = GU.frame(self.p, gs[2])
        self.assertEqual((f["ids"], f["chain"]["closed"], f["chain"]["fall_to"], f["chain"]["lows"]), (gs, True, "corners", [0.0, 10.0, 16.0, 26.0]))
        self.assertAlmostEqual(f["chain"]["total"], 32.0, places=9)

    def test_neighbouring_pieces_are_cut_on_the_bisector_and_their_outlines_tile_the_ring_exactly(self):
        roof, gs = self.ring()
        tiles = [GU.plan_outline(GU.frame(self.p, g)["chain"], GU.frame(self.p, g)["seg"]) for g in gs]
        self.assertAlmostEqual(sum(area(t) for t in tiles), (10.25 * 6.25) - 60.0, places=9)           # 125 mm of gutter all round, mitres leave no gap or overlap
        self.assertIn((10.125, -0.125), [tuple(round(c, 9) for c in p) for p in tiles[0]])               # the outer corner is shared by two pieces
        self.assertIn((10.125, -0.125), [tuple(round(c, 9) for c in p) for p in tiles[1]])

    def test_the_channel_is_continuous_across_every_corner_in_plan_and_in_height(self):
        roof, gs = self.ring()
        for i, g in enumerate(gs):
            nxt = gs[(i + 1) % 4]
            f = GU.frame(self.p, g)
            corner = f["b"]
            normal = (f["u"][0] + GU.frame(self.p, nxt)["u"][0], f["u"][1] + GU.frame(self.p, nxt)["u"][1])      # the mitre plane's normal bisects the two directions
            on_plane = lambda gid: {tuple(round(c, 9) for c in v) for v in self.mesh(gid)[0]                      # noqa: E731
                                    if abs((v[0] - corner[0]) * normal[0] + (v[1] - corner[1]) * normal[1]) < 1e-9}
            ours, theirs = on_plane(g), on_plane(nxt)
            self.assertGreaterEqual(len(ours), 8)                                                           # the whole section stands on the plane
            self.assertEqual(ours, theirs)                                                                  # the same points at the same heights: no step, no gap

    def test_the_run_falls_continuously_to_a_downpipe_at_every_corner(self):
        roof, gs = self.ring()
        pipes = [i for i in E.element_ids(self.p) if E.category(self.p, i) == "downpipe"]
        self.assertEqual(len(pipes), 4)                                                                  # one at each low point
        f = GU.frame(self.p, gs[0])
        self.assertAlmostEqual(f["high"] - 0.003 * f["chain"]["farthest"], GU.rim(f["chain"], 0.0, f["high"], f["fall"]), places=9)      # the corner is the low point
        self.assertAlmostEqual(GU.rim(f["chain"], 5.0, f["high"], f["fall"]), f["high"], places=9)                                      # mid-piece is the high point
        (_, _, z0), (_, _, z1) = self.p.model.world_bounds(gs[0])
        self.assertAlmostEqual(z1, f["high"], places=9)                                                  # the peak is a real vertex, not lost between the ends
        self.assertAlmostEqual(z1 - z0, 0.0625 + 0.003 * 5.0, places=9)
        self.assertEqual(E.check(self.p), [])

    def test_a_reflex_corner_is_mitred_the_other_way_and_the_pieces_still_tile(self):
        roof = self.roof("flat", L_SHAPE)
        gs = self.gutters(roof)["gutters"]
        self.assertEqual(len(gs), 6)
        tiles = [GU.plan_outline(GU.frame(self.p, g)["chain"], GU.frame(self.p, g)["seg"]) for g in gs]
        # a mitred band is perimeter x width plus w^2 for each right-angle convex corner and minus w^2 for each reflex one; the pieces overlap nowhere
        self.assertAlmostEqual(sum(area(t) for t in tiles), 0.125 * 36.0 + 0.125 ** 2 * (5 - 1), places=9)
        outlines = [GU.plan_outline(GU.frame(self.p, g)["chain"], GU.frame(self.p, g)["seg"]) for g in gs]
        reflex = [o for o in outlines if any(abs(c[0] - 5.0) < 1e-9 and abs(c[1] - 4.0) < 1e-9 for c in o)]
        self.assertEqual(len(reflex), 2)                                                                 # the two pieces that meet at (5, 4)
        for o in reflex:
            pts = [tuple(round(c, 9) for c in p) for p in o]
            self.assertIn((5.0, 4.0), pts)
            self.assertIn((5.125, 4.125), pts)                                                           # both are cut back to the inside of the bend

    def test_edges_that_do_not_touch_are_separate_runs_and_connect_false_keeps_even_neighbours_apart(self):
        roof = self.roof("gable")                                                                        # the two long eaves, 0 and 2
        a = self.gutters(roof)["gutters"]
        self.assertEqual(len({self.meta(g)[f"{NS}:run"] for g in a}), 2)
        roof2 = self.roof("hip")
        b = self.gutters(roof2, connect=False)["gutters"]
        self.assertEqual(len({self.meta(g)[f"{NS}:run"] for g in b}), 4)
        for g in b:
            f = GU.frame(self.p, g)
            self.assertEqual((f["seg"]["k0"], f["seg"]["k1"]), (None, None))                             # square ends, each capped
            self.assertEqual(f["ids"], [g])

    def test_a_partial_chain_has_two_free_ends_and_falls_to_its_end(self):
        roof = self.roof("hip")
        gs = self.gutters(roof, edges=[0, 1, 2])["gutters"]
        f = GU.frame(self.p, gs[1])
        self.assertEqual((f["chain"]["closed"], f["chain"]["fall_to"], f["chain"]["lows"], f["seg"]["k0"] is not None, f["seg"]["k1"] is not None), (False, "end", [26.0], True, True))
        first, last = GU.frame(self.p, gs[0])["seg"], GU.frame(self.p, gs[2])["seg"]
        self.assertEqual((first["k0"], last["k1"]), (None, None))                                       # the two free ends of the chain


class Ends(Case):
    def test_free_ends_are_closed_with_a_stop_end_plate_and_can_be_left_open(self):
        roof = self.roof("gable")
        capped = self.gutters(roof, edges=[0], downpipe=False)["gutters"][0]
        opened = self.gutters(roof, edges=[2], downpipe=False, end_start="open", end_end="open")["gutters"][0]
        for g, plates in ((capped, 2), (opened, 0)):
            f = GU.frame(self.p, g)
            th = f["thickness"]
            sec = abs(G.signed_area(GU.section("half_round", f["width"], f["depth"], th)))
            solid = abs(G.signed_area(GU._outside("half_round", f["width"], f["depth"])))
            self.assertAlmostEqual(self.vol(g), sec * (10.0 - plates * th) + plates * solid * th, places=9)
        self.assertGreater(self.vol(capped), self.vol(opened))

    def test_an_end_can_be_extended_or_shortened_but_not_a_mitred_one(self):
        roof = self.roof("hip")
        gs = self.gutters(roof, edges=[0, 1], downpipe=False)["gutters"]
        self.run_("model.set_property", id=gs[0], key="ext_start", value=300)
        (x0, _, _), (x1, _, _) = self.p.model.world_bounds(gs[0])
        self.assertAlmostEqual(x0, -0.3 - 0.0, places=9)
        self.run_("model.set_property", id=gs[0], key="ext_start", value=-200)
        self.assertAlmostEqual(self.p.model.world_bounds(gs[0])[0][0], 0.2, places=9)
        with self.assertRaises(BadRequest) as ctx:
            self.run_("model.set_property", id=gs[0], key="ext_end", value=100)                         # that end is mitred to eave 2
        self.assertIn("mitred", ctx.exception.message)
        with self.assertRaises(BadRequest):
            self.run_("model.set_property", id=gs[0], key="ext_start", value=-6000)
        self.assertAlmostEqual(self.meta(gs[0])[f"{NS}:ext_start"], -0.2, places=9)
        self.run_("model.set_property", id=gs[1], key="end_end", value="open")
        self.assertEqual(self.meta(gs[1])[f"{NS}:end_end"], "open")

    def test_offsets_move_the_whole_run_out_and_down(self):
        roof, gs = self.ring()
        before = self.p.model.world_bounds(gs[0])
        self.run_("model.set_property", id=gs[0], key="gap", value=50)
        self.run_("model.set_property", id=gs[0], key="drop", value=60)
        self.assertEqual({self.meta(g)[f"{NS}:gap"] for g in gs}, {0.05})                               # the run's, so every piece took it
        after = self.p.model.world_bounds(gs[0])
        self.assertAlmostEqual(after[0][1], before[0][1] - 0.05, places=9)
        self.assertAlmostEqual(after[1][2], before[1][2] - 0.04, places=9)
        areas = [area(GU.plan_outline(GU.frame(self.p, g)["chain"], GU.frame(self.p, g)["seg"])) for g in gs]
        self.assertAlmostEqual(sum(areas), (10 + 2 * 0.175) * (6 + 2 * 0.175) - (10 + 2 * 0.05) * (6 + 2 * 0.05), places=9)
        self.assertEqual(E.check(self.p), [])
        with self.assertRaises(BadRequest):
            self.run_("model.set_property", id=gs[0], key="gap", value=900)


class Editing(Case):
    def test_a_setting_of_the_run_is_the_whole_runs_and_one_undo_takes_it_all_back(self):
        roof, gs = self.ring()
        vols = [self.vol(g) for g in gs]
        self.run_("model.set_property", id=gs[1], key="gutter", value="box")
        self.assertEqual({self.meta(g)[f"{NS}:gutter"] for g in gs}, {"box"})
        self.assertEqual({self.meta(g)[f"{NS}:type"] for g in gs}, {"Box gutter"})
        self.assertNotEqual(vols[0], self.vol(gs[0]))
        self.p.undo()
        self.assertEqual({self.meta(g)[f"{NS}:gutter"] for g in gs}, {"half_round"})
        self.assertEqual([self.vol(g) for g in gs], vols)

    def test_a_closed_loop_cannot_fall_to_its_start_or_end_and_is_refused_whole(self):
        roof, gs = self.ring()
        with self.assertRaises(BadRequest) as ctx:
            self.run_("model.set_property", id=gs[0], key="fall_to", value="end")
        self.assertIn("closed loop", ctx.exception.message)
        self.assertEqual({self.meta(g)[f"{NS}:fall_to"] for g in gs}, {"corners"})
        self.run_("model.set_property", id=gs[0], key="fall_to", value="middle")
        self.assertEqual({self.meta(g)[f"{NS}:fall_to"] for g in gs}, {"middle"})
        self.assertEqual(GU.frame(self.p, gs[0])["chain"]["lows"], [16.0])

    def test_deleting_a_piece_caps_its_neighbours_and_resets_a_fall_that_no_longer_fits(self):
        roof, gs = self.ring()
        self.run_("model.delete", id=gs[1])
        a = GU.frame(self.p, gs[0])
        self.assertEqual(a["ids"], [gs[2], gs[3], gs[0]])                                               # the chain now runs 3, 4, 1
        self.assertEqual((a["chain"]["closed"], a["seg"]["k1"]), (False, None))
        self.assertEqual(self.meta(gs[0])[f"{NS}:end_end"], "cap")
        self.run_("model.delete", id=gs[3])
        self.run_("model.delete", id=gs[2])
        self.assertEqual(self.meta(gs[0])[f"{NS}:fall_to"], "end")                                      # one lone piece cannot fall to corners
        self.assertEqual(E.check(self.p), [])                                                            # it still has a downpipe at the end it now falls to
        self.p.undo()
        self.p.undo()
        self.p.undo()
        self.assertEqual(GU.frame(self.p, gs[0])["chain"]["closed"], True)
        self.assertEqual({self.meta(g)[f"{NS}:fall_to"] for g in gs}, {"corners"})

    def test_a_piece_can_be_added_to_a_run_and_takes_the_runs_settings(self):
        roof = self.roof("hip")
        first = self.gutters(roof, edges=[0, 1], gutter="box", fall=5)["gutters"]
        added = self.gutters(roof, edges=[2], run=first[0])["gutters"]
        f = GU.frame(self.p, added[0])
        self.assertEqual(f["ids"], [first[0], first[1], added[0]])
        self.assertEqual((self.meta(added[0])[f"{NS}:gutter"], self.meta(added[0])[f"{NS}:fall"], f["seg"]["k0"] is not None), ("box", 0.005, True))
        self.assertEqual(GU.frame(self.p, first[0])["seg"]["k1"] is not None, True)
        with self.assertRaises(BadRequest):
            self.gutters(roof, edges=[3], run=first[0], width=200)                                      # the run's settings are not given again
        far = self.roof("gable")
        self.gutters(far, edges=[0])
        with self.assertRaises(BadRequest):
            self.gutters(roof, edges=[3], run=first[0], connect=False)

    def test_a_roof_change_that_breaks_a_run_is_refused_whole(self):
        roof, gs = self.ring()
        before = [self.vol(g) for g in gs]
        with self.assertRaises(BadRequest) as ctx:
            self.run_("model.set_property", id=roof, key="roof", value="gable")                          # the gable ends lose their eaves
        self.assertIn("gutter", ctx.exception.message.lower())
        self.assertEqual(self.meta(roof)[f"{NS}:roof"], "hip")
        self.assertEqual([self.vol(g) for g in gs], before)
        self.assertEqual(len([i for i in E.element_ids(self.p) if E.category(self.p, i) == "downpipe"]), 4)

    def test_a_failed_add_makes_nothing(self):
        roof = self.roof("hip")
        count = len(E.element_ids(self.p))
        for bad in ({"diameter": 400}, {"width": 60, "diameter": 80}, {"gap": 900}, {"fall_to": "end"}, {"ext_start": 50}, {"gutter": "dome"}):
            with self.assertRaises(BadRequest):
                self.gutters(roof, **bad)
            self.assertEqual(len(E.element_ids(self.p)), count, bad)

    def test_a_corner_too_sharp_to_mitre_is_refused(self):
        roof = self.roof("flat", [[0, 0], [10000, 0], [200, 100], [0, 6000]])
        with self.assertRaises(BadRequest) as ctx:
            self.gutters(roof, edges=[0, 1])
        self.assertIn("too sharp", ctx.exception.message)


class Preview(Case):
    def test_the_preview_is_what_is_built(self):
        roof = self.roof("hip")
        params = {"edges": [0, 1, 2], "gutter": "box", "gap": 20, "ext_start": 150}
        pv = self.p.preview_gutters({"roof": roof, **params})
        self.assertEqual((pv["problems"], len(pv["runs"]), pv["runs"][0]["edges"], pv["runs"][0]["closed"]), ([], 1, [0, 1, 2], False))
        self.assertEqual(len(E.element_ids(self.p)), 1)                                                 # nothing was made
        made = self.gutters(roof, **params)["gutters"]
        plan = {i["id"]: i["polygon"] for i in E.plan_items(self.p, self.roof_level, 1000.0) if i["category"] == "gutter"}
        for g, seg in zip(made, pv["runs"][0]["segments"]):
            for a, b in zip(plan[g], seg["polygon"]):
                self.assertAlmostEqual(a[0], b[0], places=6)
                self.assertAlmostEqual(a[1], b[1], places=6)
        self.assertEqual([s["start"] for s in pv["runs"][0]["segments"]], ["cap", "mitre", "mitre"])

    def test_a_request_that_cannot_be_made_is_reported_as_a_problem_not_an_error(self):
        roof = self.roof("gable")
        pv = self.p.preview_gutters({"roof": roof, "edges": [1]})
        self.assertEqual(pv["runs"], [])
        self.assertIn("not an eave", pv["problems"][0])


class Drainage(Case):
    def test_each_corner_drains_the_half_pieces_either_side_of_it_and_the_flows_add_up(self):
        roof, gs = self.ring()
        d = self.p.drainage(roof)["roofs"][0]
        run = d["runs"][0]
        self.assertEqual((run["closed"], len(run["lows"]), run["problems"], run["gutters"]), (True, 4, [], gs))
        self.assertAlmostEqual(sum(l["flow_l_s"] for l in run["lows"]), 60.0 * 0.03, places=9)
        self.assertAlmostEqual(sum(l["catchment_m2"] for l in run["lows"]), 60.0, places=9)
        self.assertTrue(all(l["served"] and len(l["downpipes"]) == 1 for l in run["lows"]))
        # the corner at the start of eave 1 takes half of eave 1 (its density: 21/10 m2/m) and half of eave 4 (9/6 m2/m)
        low0 = next(l for l in run["lows"] if l["at_m"] == 0.0)
        self.assertAlmostEqual(low0["catchment_m2"], 21 / 10 * 5 + 9 / 6 * 3, places=9)
        pipe = low0["downpipes"][0]
        self.assertAlmostEqual(GU.pipe_flow(self.p, pipe), low0["flow_l_s"], places=9)

    def test_rain_is_traced_down_the_run_to_the_nearest_low_point_and_out_of_its_downpipe(self):
        roof, gs = self.ring()
        t = self.p.trace_drainage(gs[0], 2.0)
        self.assertEqual((t["distance_m"], t["reaches_downpipe"], len(t["downpipes"])), (2.0, True, 1))
        self.assertEqual([(s["gutter"], s["from_m"], s["to_m"]) for s in t["path"]], [(gs[0], 2.0, 0.0)])
        t = self.p.trace_drainage(gs[0], 8.0)                                                           # nearer eave 1's far end (10 m): 2 m away
        self.assertEqual((t["distance_m"], t["path"][0]["to_m"]), (2.0, 10.0))
        t = self.p.trace_drainage(gs[1], 3.0)                                                           # 3 m along eave 2: the corner at its start is 3 m away
        self.assertEqual((t["distance_m"], t["low"]["gutter"]), (3.0, gs[0]))
        with self.assertRaises(BadRequest):
            self.p.trace_drainage(gs[0], 11.0)

    def test_a_chain_falling_to_its_end_carries_everything_through_one_downpipe(self):
        roof = self.roof("hip")
        gs = self.gutters(roof, edges=[0, 1, 2])["gutters"]
        t = self.p.trace_drainage(gs[0], 0.0)
        self.assertEqual(([s["gutter"] for s in t["path"]], t["distance_m"], t["low"]["gutter"]), (gs, 26.0, gs[2]))
        self.assertAlmostEqual(t["low"]["flow_l_s"], 51.0 * 0.03, places=9)
        self.assertAlmostEqual(t["downpipes"][0]["flow_l_s"], 51.0 * 0.03, places=9)

    def test_a_low_point_without_a_downpipe_is_reported_and_a_downpipe_away_from_one_takes_nothing(self):
        roof, gs = self.ring()
        pipes = [i for i in E.element_ids(self.p) if E.category(self.p, i) == "downpipe"]
        self.run_("model.delete", id=pipes[0])
        issues = E.check(self.p)
        self.assertEqual([i["severity"] for i in issues], ["warning"])
        self.assertIn("low point", issues[0]["message"])
        self.assertIn("m² of roof drains there", issues[0]["message"])
        stray = self.run_("model.add_downpipe", gutter=gs[0], offset=5.0)["id"]                            # mid-piece: the high point, no water reaches it
        self.assertEqual(GU.pipe_flow(self.p, stray), 0.0)
        self.assertEqual(len(E.check(self.p)), 1)

    def test_it_survives_save_and_reopen(self):
        roof, gs = self.ring()
        vols = [self.vol(g) for g in gs]
        with tempfile.TemporaryDirectory() as tmp:
            self.p.save(Path(tmp) / "r.spadra")
            q = Project.open(Path(tmp) / "r.spadra")
            self.assertEqual(q.check()["issues"], [])
            self.assertEqual([q.model.volume(g, unit="m") for g in gs], vols)
            self.assertEqual(GU.frame(q, gs[0])["ids"], gs)
            self.assertEqual(len(q.drainage(roof)["roofs"][0]["runs"][0]["lows"]), 4)


if __name__ == "__main__":
    unittest.main()
