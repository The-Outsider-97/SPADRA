"""Gutters and rainwater drainage against the real SGEO engine: the four gutter types come out with the volume their section implies, they hang on
and follow the eaves of their roof, downpipes slide along them, and the rain each one drains is the plan area it really serves."""
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


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project.create("Gutters")
        self.l1, self.l2, self.roof_level = (l["id"] for l in self.p.manifest["levels"])

    def run_(self, op, **params):
        return self.p.execute(op, params)["result"]

    def roof(self, kind="gable", points=RECT, **extra):
        return self.run_("model.add_roof", points=points, level=self.roof_level, roof=kind, pitch=30, thickness=200, **extra)["id"]

    def meta(self, oid):
        return self.p.model.get(oid)["metadata"]

    def vol(self, oid):
        return self.p.model.volume(oid, unit="m")

    def bounds(self, oid):
        return self.p.model.world_bounds(oid)

    def gutters(self, roof, **extra):
        return self.run_("model.add_gutters", roof=roof, **extra)

    def cat(self, oid):
        return E.category(self.p, oid)


class Types(Case):
    def test_a_gable_verge_trim_alone_displaces_an_existing_eave_gutter_at_the_corner(self):
        roof = self.roof("gable")
        gutter = self.gutters(roof, edges=[0], downpipe=False)["gutters"][0]
        original = self.bounds(gutter)
        verge = self.run_("model.add_roof_accessory", roof=roof, kind="roof_edge", edge=1)["id"]
        self.assertTrue(E.alive(self.p, verge))
        self.assertGreater(GU.frame(self.p, gutter)["gap"], 0.02)
        self.assertLess(self.bounds(gutter)[0][1], original[0][1] - 0.02)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "roof-edges.spadra"
            self.p.save(path)
            restored = Project.open(path)
            self.assertAlmostEqual(GU.frame(restored, gutter)["gap"], GU.frame(self.p, gutter)["gap"])
        self.run_("model.delete", id=verge)
        self.assertAlmostEqual(GU.frame(self.p, gutter)["gap"], 0.0)

    def test_adding_and_removing_roof_edge_displaces_existing_gutter_without_overwriting_its_gap(self):
        roof = self.roof("gable")
        gutter = self.gutters(roof, edges=[0], downpipe=False)["gutters"][0]
        self.assertAlmostEqual(GU.frame(self.p, gutter)["gap"], 0.0)
        old_bounds = self.bounds(gutter)
        trim = self.run_("model.add_roof_accessory", roof=roof, kind="roof_edge", edge="all")["ids"][0]
        shifted = GU.frame(self.p, gutter)["gap"]
        self.assertGreaterEqual(shifted, 0.035 - 1e-6)
        self.assertAlmostEqual(float(E.m(self.meta(gutter), "gap", 0.0)), 0.0)
        self.assertLess(self.bounds(gutter)[0][1], old_bounds[0][1] - 0.02)
        self.run_("model.set_property", id=trim, key="lateral_offset", value=100)
        self.assertGreater(GU.frame(self.p, gutter)["gap"], shifted + 0.05)
        self.p.undo()
        self.assertAlmostEqual(GU.frame(self.p, gutter)["gap"], shifted, places=6)
        self.run_("model.delete", id=trim)
        self.assertLess(GU.frame(self.p, gutter)["gap"], shifted + 0.001)

    def test_each_type_is_a_closed_channel_with_the_volume_of_its_section(self):
        roof = self.roof("gable")
        for kind in GU.GUTTER_TYPES:
            r = self.gutters(roof, edges=[0], gutter=kind, downpipe=False, end_start="open", end_end="open")          # open ends: the plain swept section
            g = r["gutters"][0]
            sec = GU.section(kind, float(self.meta(g)[f"{NS}:width"]), float(self.meta(g)[f"{NS}:depth"]), float(self.meta(g)[f"{NS}:thickness"]))
            self.assertAlmostEqual(self.vol(g), abs(G.signed_area(sec)) * 10.0, places=9)             # section area x the 10 m eave
            self.assertGreater(self.vol(g), 0)
            self.run_("model.delete", id=g)

    def test_defaults_are_nominal_sizes_and_everything_can_be_edited_afterwards(self):
        roof = self.roof("gable")
        g = self.gutters(roof, edges=[0], downpipe=False, end_start="open", end_end="open")["gutters"][0]
        self.assertEqual((self.meta(g)[f"{NS}:gutter"], self.meta(g)[f"{NS}:width"], self.meta(g)[f"{NS}:depth"]), ("half_round", 0.125, 0.0625))
        before = self.vol(g)
        for key, value in (("gutter", "box"), ("width", 150), ("depth", 120), ("thickness", 1.5), ("material", "Aluminium"), ("fall", 5), ("fall_to", "start")):
            self.run_("model.set_property", id=g, key=key, value=value)
        m = self.meta(g)
        self.assertEqual((m[f"{NS}:gutter"], m[f"{NS}:type"], m[f"{NS}:material"], m[f"{NS}:fall_to"]), ("box", "Box gutter", "Aluminium", "start"))
        self.assertAlmostEqual(m[f"{NS}:fall"], 0.005, places=9)
        sec = GU.section("box", 0.15, 0.12, 0.0015)
        self.assertAlmostEqual(self.vol(g), abs(G.signed_area(sec)) * 10.0, places=9)
        self.assertNotAlmostEqual(self.vol(g), before, places=6)
        for key, bad in (("gutter", "dome"), ("width", 5000), ("thickness", 80), ("fall", 500), ("material", "Gold"), ("nonsense", 1)):
            with self.assertRaises(BadRequest):
                self.run_("model.set_property", id=g, key=key, value=bad)
        self.assertEqual(self.meta(g)[f"{NS}:gutter"], "box")

    def test_the_gutter_falls_evenly_towards_the_outlet_end_and_hangs_outside_the_eave(self):
        roof = self.roof("gable")
        g = self.gutters(roof, edges=[0], gutter="box", fall=10, fall_to="end", downpipe=False)["gutters"][0]
        (x0, y0, z0), (x1, y1, z1) = self.bounds(g)
        self.assertAlmostEqual(z1, 6.4 - GU.RIM_DROP, places=9)                                    # the high rim, just under the roof's edge
        self.assertAlmostEqual(z1 - z0, 0.1 + 0.010 * 10.0, places=9)                              # its depth plus the fall over 10 m
        self.assertAlmostEqual(y0, -0.100, places=9)                                               # edge 0 runs along y = 0; outwards is -y
        self.assertAlmostEqual(y1, 0.0, places=9)
        self.assertEqual((round(x0, 9), round(x1, 9)), (0.0, 10.0))

    def test_a_sheet_too_thick_for_the_section_is_refused_before_anything_is_made(self):
        roof = self.roof("gable")
        count = len(E.element_ids(self.p))
        with self.assertRaises(BadRequest):
            self.gutters(roof, width=30, thickness=15)
        self.assertEqual(len(E.element_ids(self.p)), count)


class Placement(Case):
    def test_every_eave_gets_a_gutter_and_a_downpipe_by_default(self):
        counts = {}
        for kind in ("flat", "shed", "gable", "hip"):
            roof = self.roof(kind)
            made = self.gutters(roof)["ids"]
            counts[kind] = (len([i for i in made if self.cat(i) == "gutter"]), len([i for i in made if self.cat(i) == "downpipe"]))
        self.assertEqual(counts, {"flat": (4, 4), "shed": (1, 1), "gable": (2, 2), "hip": (4, 4)})

    def test_only_an_eave_can_take_a_gutter_and_only_once(self):
        roof = self.roof("gable")                                                                   # edges 0 and 2 are the long eaves, 1 and 3 the gables
        for bad in ([1], [7], ["0"], []):
            with self.assertRaises(BadRequest):
                self.gutters(roof, edges=bad)
        self.gutters(roof, edges=[0])
        with self.assertRaises(BadRequest):
            self.gutters(roof, edges=[0])
        with self.assertRaises(BadRequest):
            self.gutters(roof, edges=[0, 2])                                                        # one eave is taken: nothing is made
        self.assertEqual(len([i for i in E.element_ids(self.p) if self.cat(i) == "gutter"]), 1)

    def test_gutters_follow_a_moved_or_turned_roof_and_a_re_pitched_one(self):
        roof = self.roof("gable")
        g = self.gutters(roof, edges=[0])["gutters"][0]
        self.run_("model.move", id=roof, dx=2.0, dy=1.0)
        (x0, y0, _), (x1, y1, _) = self.bounds(g)
        self.assertEqual((round(x0, 6), round(x1, 6), round(y1, 6)), (2.0, 12.0, 1.0))
        self.run_("model.rotate", ids=[roof], centre=[0, 0], angle=90)
        (x0, y0, _), (x1, y1, _) = self.bounds(g)
        self.assertEqual((round(x1 - x0, 6), round(y1 - y0, 6)), (0.125, 10.0))                      # now runs the other way
        before = self.bounds(g)
        self.run_("model.set_property", id=roof, key="pitch", value=45)
        self.assertEqual(self.bounds(g), before)                                                      # the eave did not change
        self.run_("model.set_property", id=roof, key="roof", value="hip")                             # still an eave
        self.assertEqual(self.bounds(g), before)

    def test_a_roof_change_that_takes_an_eave_away_is_refused_whole(self):
        roof = self.roof("gable")
        self.gutters(roof)
        with self.assertRaises(BadRequest) as ctx:
            self.run_("model.set_property", id=roof, key="roof", value="shed")                        # a shed roof drains to one edge only
        self.assertIn("gutter", ctx.exception.message.lower())
        self.assertEqual(self.meta(roof)[f"{NS}:roof"], "gable")
        self.assertEqual(len([i for i in E.element_ids(self.p) if self.cat(i) == "gutter"]), 2)

    def test_a_gutter_alone_is_not_moved_or_turned_but_goes_with_its_roof(self):
        roof = self.roof("gable")
        g = self.gutters(roof, edges=[0])["gutters"][0]
        for op, kw in (("model.move", {"id": g, "dx": 1.0, "dy": 0.0}), ("model.rotate", {"ids": [g], "centre": [0, 0], "angle": 10})):
            with self.assertRaises(BadRequest):
                self.run_(op, **kw)
        self.run_("model.rotate", ids=[roof, g], centre=[0, 0], angle=90)                            # selected together: the gutter turns with the roof, once
        (x0, y0, _), (x1, y1, _) = self.bounds(g)
        self.assertEqual((round(x1 - x0, 6), round(y1 - y0, 6)), (0.125, 10.0))

    def test_deleting_removes_what_hangs_on_it_and_one_undo_brings_it_back(self):
        roof = self.roof("gable")
        made = self.gutters(roof)["ids"]
        self.assertEqual(len(made), 4)
        g = next(i for i in made if self.cat(i) == "gutter")
        pipes = [i for i in made if self.cat(i) == "downpipe" and self.meta(i)[f"{NS}:gutter"] == g]
        self.run_("model.delete", id=g)
        self.assertFalse(E.alive(self.p, g) or E.alive(self.p, pipes[0]))
        self.p.undo()
        self.assertTrue(E.alive(self.p, g) and E.alive(self.p, pipes[0]))
        self.run_("model.delete", id=roof)
        self.assertEqual([i for i in E.element_ids(self.p) if self.cat(i) in ("roof", "gutter", "downpipe")], [])
        self.p.undo()
        self.assertEqual(len([i for i in E.element_ids(self.p) if self.cat(i) in ("roof", "gutter", "downpipe")]), 5)


class Downpipes(Case):
    def setUp(self) -> None:
        super().setUp()
        self.roof_id = self.roof("gable")
        self.g = self.gutters(self.roof_id, edges=[0], downpipe=False)["gutters"][0]

    def pipe(self, **extra):
        return self.run_("model.add_downpipe", gutter=self.g, **extra)["id"]

    def test_a_downpipe_is_a_tube_from_under_the_gutter_to_its_discharge(self):
        d = self.pipe(offset=9.9, diameter=80)
        n = GU.CIRCLE
        ring = 0.5 * n * math.sin(2 * math.pi / n)
        h = (6.4 - GU.RIM_DROP - 0.003 * 9.9 - 0.0625) - 0.3
        self.assertAlmostEqual(self.vol(d), ring * (0.04 ** 2 - 0.038 ** 2) * h, places=9)
        (x0, y0, z0), (x1, y1, z1) = self.bounds(d)
        self.assertAlmostEqual(z0, 0.3, places=9)
        self.assertAlmostEqual(z1, 6.4 - GU.RIM_DROP - 0.003 * 9.9 - 0.0625, places=9)
        self.assertAlmostEqual(y0, -0.08, places=2)                                                  # against the wall line, under the channel
        self.assertGreaterEqual(y0, -0.125 - 1e-9)

    def test_moving_a_downpipe_slides_it_along_its_gutter_only(self):
        d = self.pipe(offset=5.0)
        self.run_("model.move", id=d, dx=1.5, dy=4.0)
        self.assertAlmostEqual(self.meta(d)[f"{NS}:offset"], 6.5, places=9)
        with self.assertRaises(BadRequest):                                                          # off the end of the gutter
            self.run_("model.move", id=d, dx=100.0, dy=0.0)
        self.assertAlmostEqual(self.meta(d)[f"{NS}:offset"], 6.5, places=9)

    def test_what_a_downpipe_cannot_do_is_refused_and_leaves_things_as_they_were(self):
        d = self.pipe(offset=5.0)
        for kw in ({"gutter": self.g, "offset": 0.01}, {"gutter": self.g, "offset": 10.5}, {"gutter": self.g, "diameter": 200},
                   {"gutter": self.g, "offset": 5.0, "bottom": 6.3}, {"gutter": "nope"}):
            with self.assertRaises(Exception):
                self.run_("model.add_downpipe", **kw)
        for key, bad in (("diameter", 200), ("offset", 12), ("bottom", 6.25), ("wall", 40), ("material", "Gold")):
            with self.assertRaises(BadRequest):
                self.run_("model.set_property", id=d, key=key, value=bad)
        self.assertEqual([i for i in E.element_ids(self.p) if self.cat(i) == "downpipe"], [d])
        self.run_("model.set_property", id=d, key="bottom", value=0.5)
        self.assertAlmostEqual(self.bounds(d)[0][2], 0.5, places=9)

    def test_a_downpipe_follows_its_gutter_when_the_gutter_is_edited(self):
        d = self.pipe(offset=5.0)
        top = self.bounds(d)[1][2]
        self.run_("model.set_property", id=self.g, key="depth", value=100)
        self.assertAlmostEqual(self.bounds(d)[1][2], top - (0.1 - 0.0625), places=9)

    def test_a_gutter_too_narrow_for_its_downpipe_is_refused(self):
        self.pipe(offset=5.0, diameter=80)
        with self.assertRaises(BadRequest):
            self.run_("model.set_property", id=self.g, key="width", value=60)
        self.assertEqual(self.meta(self.g)[f"{NS}:width"], 0.125)

    def test_downpipes_are_drawn_on_every_level_they_pass_and_the_gutter_on_its_own(self):
        self.pipe(offset=5.0)
        names = lambda level: sorted(i["category"] for i in E.plan_items(self.p, level, 1000.0))             # noqa: E731
        self.assertEqual(names(self.roof_level), ["downpipe", "gutter", "roof"])
        self.assertEqual(names(self.l2), ["downpipe"])
        self.assertEqual(names(self.l1), ["downpipe"])


class Drainage(Case):
    def test_a_gable_roof_sends_half_its_plan_to_each_eave(self):
        roof = self.roof("gable")
        self.gutters(roof)
        d = self.p.drainage(roof)["roofs"][0]
        self.assertEqual((d["plan_area_m2"], d["drained_m2"], d["undrained_m2"]), (60.0, 60.0, 0.0))
        for g in d["gutters"]:
            self.assertEqual((g["catchment_m2"], g["flow_l_s"]), (30.0, 0.9))                       # 30 m² x 0.03 l/s·m²
            self.assertEqual([p["flow_l_s"] for p in g["downpipes"]], [0.9])

    def test_each_hip_slope_drains_its_own_area_and_they_add_up_to_the_plan(self):
        roof = self.roof("hip")
        made = self.gutters(roof)["gutters"]
        areas = sorted(round(GU.catchment(self.p, g), 6) for g in made)
        self.assertEqual(areas, [9.0, 9.0, 21.0, 21.0])                                              # two trapezoids and two triangles
        self.assertAlmostEqual(sum(areas), 60.0, places=9)

    def test_a_shed_roof_drains_to_its_one_eave_and_a_flat_roof_is_shared_by_its_gutters(self):
        shed = self.roof("shed")
        g = self.gutters(shed)["gutters"][0]
        self.assertAlmostEqual(GU.catchment(self.p, g), 60.0, places=9)
        flat = self.roof("flat")
        self.gutters(flat, edges=[0, 2])
        self.assertEqual(sorted(round(GU.catchment(self.p, i), 6) for i in GU.gutters_of(self.p, flat)), [30.0, 30.0])

    def test_unguttered_slopes_are_reported_as_undrained_and_downpipes_share_the_flow(self):
        roof = self.roof("gable")
        g = self.gutters(roof, edges=[0], downpipe=False)["gutters"][0]
        d = self.p.drainage(roof)["roofs"][0]
        self.assertEqual((d["drained_m2"], d["undrained_m2"]), (30.0, 30.0))
        self.run_("model.add_downpipe", gutter=g, offset=9.8)
        self.run_("model.add_downpipe", gutter=g, offset=9.9)
        d = self.p.drainage(roof)["roofs"][0]
        self.assertEqual([p["flow_l_s"] for p in d["gutters"][0]["downpipes"]], [0.45, 0.45])
        self.run_("model.set_property", id=g, key="rainfall", value=0.06)
        self.assertEqual(self.p.drainage(roof)["roofs"][0]["gutters"][0]["flow_l_s"], 1.8)
        self.assertIn("not sized", self.p.drainage()["note"])

    def test_a_gutter_without_a_downpipe_is_flagged_and_a_complete_one_is_clean(self):
        roof = self.roof("gable")
        g = self.gutters(roof, edges=[0], downpipe=False)["gutters"][0]
        issues = E.check(self.p)
        self.assertEqual([(i["severity"], i["ref"]) for i in issues], [("warning", g)])
        self.run_("model.add_downpipe", gutter=g, offset=9.9)
        self.assertEqual(E.check(self.p), [])

    def test_properties_show_the_drainage_and_everything_editable(self):
        roof = self.roof("gable")
        made = self.gutters(roof, edges=[0])["ids"]
        g = next(i for i in made if self.cat(i) == "gutter")
        d = next(i for i in made if self.cat(i) == "downpipe")
        top = {r["key"]: r for r in E.properties(self.p, g)["top"] if "key" in r}
        self.assertEqual(sorted(top), ["depth", "gutter", "material", "thickness", "width"])
        self.assertEqual([c["value"] for c in top["gutter"]["choices"]], ["half_round", "ogee", "box", "fascia"])
        rows = [r for grp in E.properties(self.p, g)["groups"] for r in grp["rows"]]
        self.assertTrue({"fall", "fall_to", "rainfall"} <= {r.get("key") for r in rows})
        self.assertIn("30.00 m²", next(r["value"] for r in rows if r["label"] == "Roof area draining here"))
        pipe_rows = {r["key"] for r in E.properties(self.p, d)["top"] if "key" in r}
        self.assertEqual(pipe_rows, {"diameter", "offset", "bottom", "wall", "material"})

    def test_gutter_downpipe_and_roof_schedules_read_the_elements(self):
        from applications.SPADRA import documentation as D
        roof = self.roof("gable")
        self.gutters(roof, edges=[0], fall=4)
        g = D.schedule_data(self.p, "gutter")
        row = g["rows"][0]
        self.assertEqual((row["no"], row["type"], row["length"], row["width"], row["depth"], row["fall"], row["catchment"], row["flow"], row["downpipes"], row["material"]),
                         ("GT01", "Half-round gutter", 10.0, 125.0, 62.5, 4.0, 30.0, 0.9, 1, "Zinc"))
        self.assertEqual(g["totals"]["catchment"], 30.0)
        self.assertTrue(row["sfb"] == "-")
        d = D.schedule_data(self.p, "downpipe")["rows"][0]
        self.assertEqual((d["no"], d["diameter"], d["flow"], d["bottom"]), ("DP01", 80.0, 0.9, 0.3))
        self.assertAlmostEqual(d["length"], 6.4 - GU.RIM_DROP - 0.004 * 9.955 - 0.0625 - 0.3, places=3)
        r = D.schedule_data(self.p, "roof")["rows"][0]
        self.assertEqual((r["pitch"], r["slope"]), (30.0, 57.7))
        self.assertEqual(D.schedule_data(self.p, "count")["rows"][0]["category"] in ("Downpipes", "Gutters", "Roofs"), True)

    def test_a_gutter_and_a_downpipe_take_an_nl_sfb_code(self):
        roof = self.roof("gable")
        made = self.gutters(roof, edges=[0])["ids"]
        g, d = (next(i for i in made if self.cat(i) == c) for c in ("gutter", "downpipe"))
        self.run_("model.set_property", id=g, key="sfb", value="(27.16)")
        self.run_("model.set_property", id=d, key="sfb", value="(52.12)")
        self.assertEqual((self.meta(g)[f"{NS}:sfb"], self.meta(d)[f"{NS}:sfb"]), ("(27.16)", "(52.12)"))

    def test_it_survives_save_and_reopen_and_quantities_count_the_sheet(self):
        roof = self.roof("hip")
        self.gutters(roof)
        with tempfile.TemporaryDirectory() as tmp:
            self.p.save(Path(tmp) / "g.spadra")
            q = Project.open(Path(tmp) / "g.spadra")
            self.assertEqual(q.check()["issues"], [])
            self.assertEqual(len(q.drainage()["roofs"][0]["gutters"]), 4)
        rows = {r["id"]: r for r in self.p.quantities()["rows"]}
        gutter = next(i for i in rows if rows[i]["category"] == "gutter")
        self.assertGreater(rows[gutter]["volume_m3"], 0)
        self.assertEqual(rows[gutter]["material"], "Zinc")


if __name__ == "__main__":
    unittest.main()


class RequestedDrainageRegressions(Case):
    """Downpipe length and roof-core references use actual SGEO geometry."""

    def test_pipe_length_is_editable_without_detaching_gutter(self):
        roof = self.roof()
        gutter = self.gutters(roof, edges=[0], downpipe=False)["gutters"][0]
        pipe = self.run_("model.add_downpipe", gutter=gutter, offset=5)["id"]
        before_top = self.bounds(pipe)[1][2]
        self.run_("model.set_property", id=pipe, key="length", value=2.5)
        low, high = self.bounds(pipe)
        self.assertAlmostEqual(high[2], before_top, places=6)
        self.assertAlmostEqual(low[2], before_top - 2.5, places=6)
        props = E.properties(self.p, pipe)
        fields = {row.get("key"): row for g in props["groups"] for row in g["rows"]}
        self.assertAlmostEqual(fields["length"]["value"], 2.5)
        with self.assertRaises(BadRequest):
            self.run_("model.set_property", id=pipe, key="length", value=0.05)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "pipe-length.spadra"
            self.p.save(target)
            after = Project.open(target)
            self.assertAlmostEqual(after.model.world_bounds(pipe)[0][2], before_top - 2.5, places=6)

    def test_pipe_cut_and_depth_visibility_come_from_elevations(self):
        roof = self.roof()
        gutter = self.gutters(roof, edges=[0], downpipe=False)["gutters"][0]
        pipe = self.run_("model.add_downpipe", gutter=gutter, offset=5)["id"]
        view = next(v["id"] for v in self.p.manifest["views"] if v.get("level_id") == self.roof_level)
        shown = {i["id"]: i for i in self.p.plan_overlay(view)}
        self.assertIn(pipe, shown)
        self.assertFalse(shown[pipe]["cut"])
        self.run_("view.set_cut_plane", id=view, key="range_bottom", value=-50)
        self.run_("view.set_cut_plane", id=view, key="range_depth", value=-60)
        self.assertNotIn(pipe, [i["id"] for i in self.p.plan_overlay(view)])

    def test_flat_roof_edge_profile_reference_centres_in_total_thickness(self):
        roof = self.roof(kind="flat")
        meta = E.meta_of(self.p, roof)
        verts, faces = E._roof_edge_mesh(self.p, meta, 0, 0.06, 0.18, 0.0, 0.0)
        self.assertGreater(len(faces), 0)
        roof_top = E.level_elevation(self.p, self.roof_level) + float(E.m(meta, "thickness"))
        zcentre = sum(v[2] for v in verts[:4]) / 4
        self.assertAlmostEqual(zcentre, roof_top - float(E.m(meta, "thickness")) / 2, places=6)

    def test_explicit_core_alignment_ignores_new_outer_finish(self):
        from applications.SPADRA import layers as LY
        roof = self.roof(kind="flat")
        accessory = self.run_("model.add_roof_accessory", roof=roof, kind="roof_edge", edge=1)["id"]
        initial = self.bounds(accessory)
        original = LY.stack(self.p, roof)[0]
        self.run_("model.set_property", id=roof, key=f"layer:{original['id']}:role", value="core")
        self.run_("model.add_layer", id=roof, side="outside", thickness=20,
                  material="Timber", role="non_core")
        now = self.bounds(accessory)
        self.assertAlmostEqual((now[0][2] + now[1][2]) / 2,
                               (initial[0][2] + initial[1][2]) / 2, places=6)
        roles = [row["role"] for row in LY.stack(self.p, roof)]
        self.assertEqual(roles, ["non_core", "core"])
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "structural-core.spadra"
            self.p.save(target)
            restored = Project.open(target)
            self.assertEqual([row["role"] for row in LY.stack(restored, roof)], roles)
