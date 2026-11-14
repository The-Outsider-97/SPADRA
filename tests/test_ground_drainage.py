"""Ground rainwater drainage (issue sheet 11-11-2026, item 1): three parametric drains - a point drain (yard gully), a linear channel drain and a
downpipe ground inlet - each cutting its opening in the floor it stands in (and in no other floor), layer by layer in a multilayer floor, and
following every edit of itself, its floor or its downpipe."""
from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from applications.SPADRA import elements as E
from applications.SPADRA import forms as FX
from applications.SPADRA import geometry as G
from applications.SPADRA import gutters as GU
from applications.SPADRA.project import NS, Project
from applications.tools.errors import BadRequest

SLAB = [[0, 0], [8000, 0], [8000, 6000], [0, 6000]]


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project.create("Drains")
        self.l1, self.l2, self.roof_level = (l["id"] for l in self.p.manifest["levels"])
        self.floor = self.run_("model.add_floor", points=SLAB, level=self.l1, thickness=200)["id"]

    def run_(self, op, **params):
        return self.p.execute(op, params)["result"]

    def vol(self, oid):
        return self.p.model.volume(oid, unit="m")

    def holes(self, fid):
        return E.meta_of(self.p, fid).get(f"{NS}:cut") or []

    def drain(self, **kw):
        return self.run_("model.add_drain", **{"level": self.l1, **kw})["id"]


class ThreeTypes(Case):
    def test_each_type_stands_in_its_floor_cuts_its_footprint_and_is_a_closed_solid(self):
        whole = self.vol(self.floor)
        cases = {"gully": {"point": [2000, 2000]}, "channel": {"start": [3000, 1000], "end": [6000, 1000]}, "inlet": {"point": [6000, 4000]}}
        removed = 0.0
        for kind, where in cases.items():
            with self.subTest(kind=kind):
                d = self.drain(kind=kind, **where)
                f = GU.drain_frame(self.p, d)
                self.assertEqual(f["host"], self.floor)
                self.assertAlmostEqual(f["top"], 0.0, places=9)                                  # flush with the floor's top
                removed += abs(G.signed_area(f["footprint"])) * 0.2
                self.assertAlmostEqual(whole - self.vol(self.floor), removed, places=9)          # the opening is exactly the body's footprint
                scene = {o["id"]: o for o in self.p.scene_data()["objects"]}
                self.assertTrue(G.is_closed((scene[d]["vertices"], scene[d]["faces"])))
                self.assertGreater(self.vol(d), 0)
        self.assertEqual(len(self.holes(self.floor)), 3)

    def test_the_cut_follows_the_drain_and_goes_when_the_drain_goes(self):
        whole = self.vol(self.floor)
        d = self.drain(kind="gully", point=[2000, 2000])
        cut = self.vol(self.floor)
        self.run_("model.move", id=d, dx=1.0, dy=0.5)
        self.assertAlmostEqual(self.vol(self.floor), cut, places=9)
        hole = self.holes(self.floor)[0]
        self.assertAlmostEqual(sum(q[0] for q in hole) / len(hole), 3.0, places=6)
        self.run_("model.set_property", id=d, key="width", value=400)
        self.assertAlmostEqual(whole - self.vol(self.floor), 0.4 * 0.4 * 0.2, places=9)
        self.run_("model.delete", id=d)
        self.assertAlmostEqual(self.vol(self.floor), whole, places=9)
        self.p.undo()
        self.assertAlmostEqual(whole - self.vol(self.floor), 0.4 * 0.4 * 0.2, places=9)

    def test_an_unrelated_floor_is_never_cut(self):
        other = self.run_("model.add_floor", points=SLAB, level=self.l2, thickness=200)["id"]
        before = self.vol(other)
        self.drain(kind="gully", point=[2000, 2000])
        self.assertAlmostEqual(self.vol(other), before, places=9)
        outside = self.drain(kind="gully", point=[20000, 20000])                                # beside the floor: it stands on its level and cuts nothing
        self.assertIsNone(GU.drain_frame(self.p, outside)["host"])

    def test_a_drain_follows_its_floor_top_and_the_floor_keeps_its_layers(self):
        d = self.drain(kind="channel", start=[1000, 1000], end=[4000, 1000])
        self.run_("model.set_property", id=self.floor, key="offset", value=200)
        f = GU.drain_frame(self.p, d)
        self.assertAlmostEqual(f["top"], 0.2, places=9)
        self.assertAlmostEqual(self.p.model.world_bounds(d)[1][2], 0.2, places=9)
        self.assertEqual(len(self.holes(self.floor)), 1)

    def test_a_multilayer_floor_is_cut_through_the_layers_the_body_passes_and_a_shallow_body_is_flagged(self):
        self.run_("model.add_layer", id=self.floor, material="Concrete", thickness=300)
        d = self.drain(kind="gully", point=[2000, 2000], depth=300)                              # deeper than the top layer, shallower than the floor
        cuts = E.meta_of(self.p, self.floor).get(f"{NS}:cut_layers")
        self.assertEqual([bool(h) for h, _ in cuts], [True, False])                              # the top 200 mm cut, the 300 mm layer below not
        found = [i for i in E.check(self.p) if i["ref"] == d]
        self.assertTrue(any("shallower" in i["message"] for i in found))

    def test_a_ground_inlet_stands_under_its_downpipe_and_the_downpipe_ends_in_its_socket(self):
        roof = self.run_("model.add_roof", points=[[0, 1000], [8000, 1000], [8000, 6000], [0, 6000]], level=self.roof_level, roof="gable", pitch=30, thickness=200)["id"]
        g = self.run_("model.add_gutters", roof=roof, edges=[0], downpipe=False)["gutters"][0]
        pipe = self.run_("model.add_downpipe", gutter=g, offset=4.0)["id"]
        inlet = self.drain(kind="inlet", downpipe=pipe)
        f = GU.drain_frame(self.p, inlet)
        end = GU.assembly(self.p, pipe)["end"]
        self.assertAlmostEqual(math.dist(f["centre"], end[:2]), 0.0, places=9)
        self.assertAlmostEqual(end[2], f["top"] - f["p"]["socket"], places=9)
        self.run_("model.move", id=pipe, dx=1.0, dy=0.0)                                        # the downpipe slides: the inlet and the floor's cut follow
        f = GU.drain_frame(self.p, inlet)
        self.assertAlmostEqual(math.dist(f["centre"], GU.assembly(self.p, pipe)["end"][:2]), 0.0, places=9)
        hole = self.holes(self.floor)[0]
        self.assertAlmostEqual(sum(q[0] for q in hole) / len(hole), f["centre"][0], places=6)
        made = self.run_("model.edit_downpipe", id=pipe, action="add", kind="offset_bend", index=0)["created"]
        self.run_("model.set_property", id=pipe, key=f"comp:{made}:offset", value=200)
        self.assertAlmostEqual(math.dist(GU.drain_frame(self.p, inlet)["centre"], GU.assembly(self.p, pipe)["end"][:2]), 0.0, places=9)
        with self.assertRaises(BadRequest):
            self.run_("model.move", id=inlet, dx=1.0, dy=0.0)

    def test_drains_survive_save_and_reopen(self):
        d = self.drain(kind="channel", start=[1000, 1000], end=[4000, 1000], outlet_at="middle")
        vol, fvol = self.vol(d), self.vol(self.floor)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "drains.spadra"
            self.p.save(path)
            again = Project.open(path)
            self.assertAlmostEqual(again.model.volume(d, unit="m"), vol, places=9)
            self.assertAlmostEqual(again.model.volume(self.floor, unit="m"), fvol, places=9)
            self.assertEqual(E.meta_of(again, d)[f"{NS}:outlet_at"], "middle")

    def test_properties_and_plan(self):
        d = self.drain(kind="gully", point=[2000, 2000])
        props = E.properties(self.p, d)
        keys = {r.get("key") for r in props["top"]} | {r.get("key") for g in props["groups"] for r in g["rows"]}
        self.assertTrue({"width", "depth", "cover", "outlet_diameter", "outlet_direction", "offset"} <= keys)
        items = [i for i in E.plan_items(self.p, self.l1, 1000.0) if i["id"] == d]
        self.assertEqual(len(items), 1)
        self.assertEqual(len(items[0]["polygon"]), 4)
        with self.assertRaises(BadRequest):
            self.run_("model.set_property", id=d, key="width", value=50)


if __name__ == "__main__":
    unittest.main()
