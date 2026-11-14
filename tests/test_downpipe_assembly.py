"""Downpipes as parametric assemblies (issue sheet 11-11-2026, item 1): centred on the gutter, a real outlet hole cut through the gutter's sheet,
components (sections, offset bends, elbows, a shoe, a Y-branch, a hopper head) that can be added, removed, replaced, moved and edited one by one,
connectors made wherever two sections meet, and classification inherited from the downpipe or the gutter."""
from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from applications.SPADRA import elements as E
from applications.SPADRA import geometry as G
from applications.SPADRA import gutters as GU
from applications.SPADRA.project import NS, Project
from applications.SPADRA.stairs import _world_triangles
from applications.tools.errors import BadRequest

RECT = [[0, 0], [10000, 0], [10000, 6000], [0, 6000]]


def ray_hits(tris, x, y):
    """How many triangles a vertical line through (x, y) passes through."""
    n = 0
    for a, b, c in tris:
        d = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        if abs(d) < 1e-15:
            continue
        l1 = ((b[0] - x) * (c[1] - y) - (b[1] - y) * (c[0] - x)) / d
        l2 = ((c[0] - x) * (a[1] - y) - (c[1] - y) * (a[0] - x)) / d
        if l1 >= 0 and l2 >= 0 and l1 + l2 <= 1:
            n += 1
    return n


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project.create("Downpipes")
        self.l1, self.l2, self.roof_level = (l["id"] for l in self.p.manifest["levels"])

    def run_(self, op, **params):
        return self.p.execute(op, params)["result"]

    def roof(self, kind="gable", points=RECT):
        return self.run_("model.add_roof", points=points, level=self.roof_level, roof=kind, pitch=30, thickness=200)["id"]

    def gutter(self, roof=None, **extra):
        roof = roof or self.roof()
        return self.run_("model.add_gutters", roof=roof, edges=[0], downpipe=False, **extra)["gutters"][0]

    def pipe(self, gutter, **extra):
        return self.run_("model.add_downpipe", gutter=gutter, **{"offset": 5.0, **extra})["id"]

    def meta(self, oid):
        return E.meta_of(self.p, oid)

    def vol(self, oid):
        return self.p.model.volume(oid, unit="m")

    def edit(self, pipe, action, **kw):
        return self.run_("model.edit_downpipe", id=pipe, action=action, **kw)

    def comps(self, pipe):
        return GU.components_of(self.meta(pipe))


class Outlet(Case):
    def test_the_downpipe_is_centred_on_the_gutter_for_every_type_and_turn(self):
        for kind in ("half_round", "ogee", "box", "fascia"):
            for turn in (0, 37):
                with self.subTest(kind=kind, turn=turn):
                    a = math.radians(turn)
                    pts = [[5000 + math.cos(a) * (x - 5000) - math.sin(a) * (y - 3000), 3000 + math.sin(a) * (x - 5000) + math.cos(a) * (y - 3000)] for x, y in RECT]
                    g = self.gutter(self.roof(points=pts), gutter=kind)
                    d = self.pipe(g, diameter=60)
                    f = GU.frame(self.p, g)
                    pf = GU.pipe_frame(self.p, d)
                    centre = (f["a"][0] + f["u"][0] * 5.0 + f["n"][0] * (f["gap"] + f["width"] / 2), f["a"][1] + f["u"][1] * 5.0 + f["n"][1] * (f["gap"] + f["width"] / 2))
                    self.assertAlmostEqual(pf["at"][0], centre[0], places=9)
                    self.assertAlmostEqual(pf["at"][1], centre[1], places=9)
                    vs = [v for t in _world_triangles(self.p, d) for v in t]
                    cx, cy = sum(v[0] for v in vs) / len(vs), sum(v[1] for v in vs) / len(vs)
                    self.assertAlmostEqual(cx, centre[0], places=6)                         # the tube itself is centred, not only its parameter
                    self.assertAlmostEqual(cy, centre[1], places=6)

    def test_the_outlet_is_a_real_hole_through_the_sheet_and_the_solid_stays_closed(self):
        g = self.gutter(gutter="box")
        before = self.vol(g)
        d = self.pipe(g, diameter=80)
        th = float(self.meta(g)[f"{NS}:thickness"])
        bore = 0.04 - 0.002
        polygon = 0.5 * GU.CIRCLE * math.sin(2 * math.pi / GU.CIRCLE) * bore ** 2
        self.assertAlmostEqual(before - self.vol(g), polygon * th, places=10)                 # a box gutter's bottom is a flat sheet: exactly area × thickness
        scene = {o["id"]: o for o in self.p.scene_data()["objects"]}
        self.assertTrue(G.is_closed((scene[g]["vertices"], scene[g]["faces"])))
        tris = _world_triangles(self.p, g)
        x, y = GU.pipe_frame(self.p, d)["at"]
        self.assertEqual(ray_hits(tris, x, y), 0)                                              # looking straight down the outlet, nothing of the gutter
        f = GU.frame(self.p, g)
        ox, oy = x + f["u"][0] * (bore + 0.003), y + f["u"][1] * (bore + 0.003)
        self.assertEqual(ray_hits(tris, ox, oy), 2)                                            # just past the rim: the sheet, top and bottom

    def test_the_hole_follows_the_downpipe_and_closes_when_it_is_deleted(self):
        g = self.gutter()
        whole = self.vol(g)
        d = self.pipe(g)
        cut = self.vol(g)
        self.assertLess(cut, whole)
        self.run_("model.move", id=d, dx=1.0, dy=0.0)
        tris = _world_triangles(self.p, g)
        x, y = GU.pipe_frame(self.p, d)["at"]
        self.assertEqual(ray_hits(tris, x, y), 0)
        self.assertEqual(ray_hits(tris, x - 1.0, y), 2)                                        # where it was, the sheet is whole again
        self.assertAlmostEqual(self.vol(g), cut, places=10)
        self.run_("model.delete", id=d)
        self.assertAlmostEqual(self.vol(g), whole, places=10)
        self.p.undo()
        self.assertAlmostEqual(self.vol(g), cut, places=10)

    def test_the_pipe_meets_the_gutter_underside_round_its_whole_rim(self):
        for kind in ("half_round", "ogee"):
            with self.subTest(kind=kind):
                g = self.gutter(gutter=kind)
                d = self.pipe(g, diameter=80)
                f = GU.frame(self.p, g)
                top = GU.pipe_frame(self.p, d)["top"]
                rim = [v for t in _world_triangles(self.p, d) for v in t if v[2] > top - 1e-9]
                self.assertTrue(rim)
                for x, y, z in rim:
                    self.assertAlmostEqual(z, GU.underside(f, x, y), places=7)

    def test_an_outlet_too_wide_for_the_bottom_is_refused(self):
        g = self.gutter(thickness=2)
        with self.assertRaises(BadRequest):
            self.pipe(g, diameter=125)                                                          # its 121 mm bore would reach the gutter's sides
        self.assertEqual(GU.downpipes_of(self.p, g), [])


class Components(Case):
    def setUp(self) -> None:
        super().setUp()
        self.g = self.gutter()
        self.d = self.pipe(self.g, diameter=80, bottom=0.3)

    def end(self):
        return GU.assembly(self.p, self.d)["end"]

    def test_a_new_downpipe_is_one_free_section_down_to_its_discharge(self):
        self.assertEqual([c["kind"] for c in self.comps(self.d)], ["pipe"])
        self.assertAlmostEqual(self.end()[2], 0.3, places=9)
        self.assertAlmostEqual(self.p.model.world_bounds(self.d)[0][2], 0.3, places=9)

    def test_an_offset_bend_moves_the_pipe_by_its_offset_and_the_free_section_still_reaches_the_discharge(self):
        pf = GU.pipe_frame(self.p, self.d)
        f = pf["g"]
        for angle in (72, 45, 40):
            with self.subTest(angle=angle):
                comps = self.comps(self.d)
                for c in comps:
                    if c["kind"] == "offset_bend":
                        self.edit(self.d, "remove", component=c["id"])
                made = self.edit(self.d, "add", kind="offset_bend", index=0)["created"]
                self.edit(self.d, "set", component=made, field="angle", value=angle)
                self.edit(self.d, "set", component=made, field="offset", value=300)
                x, y, z = self.end()
                self.assertAlmostEqual(z, 0.3, places=9)
                moved = ((x - pf["at"][0]) * -f["n"][0] + (y - pf["at"][1]) * -f["n"][1])
                self.assertAlmostEqual(moved, 0.3, places=9)                                   # 300 mm towards the building
                self.assertAlmostEqual((x - pf["at"][0]) * f["u"][0] + (y - pf["at"][1]) * f["u"][1], 0.0, places=9)

    def test_angles_outside_5_to_90_degrees_and_a_bend_that_cannot_make_its_offset_are_refused(self):
        made = self.edit(self.d, "add", kind="elbow", index=0)["created"]
        for bad in (95, 2):
            with self.assertRaises(BadRequest):
                self.edit(self.d, "set", component=made, field="angle", value=bad)
        self.edit(self.d, "replace", component=made, kind="offset_bend")
        with self.assertRaises(BadRequest):
            self.edit(self.d, "set", component=made, field="offset", value=5)                  # two 45° bends of 80 mm radius need more than 5 mm
        self.assertEqual(self.comps(self.d)[0]["kind"], "offset_bend")

    def test_sections_meet_with_one_connector_each_and_none_is_made_twice(self):
        first = self.comps(self.d)[0]["id"]
        self.edit(self.d, "split", component=first, value=1500)
        self.assertEqual(len(GU.assembly(self.p, self.d)["connectors"]), 1)
        second = self.comps(self.d)[1]["id"]
        self.edit(self.d, "split", component=second, value=1000)
        a = GU.assembly(self.p, self.d)
        self.assertEqual(len(a["connectors"]), 2)
        self.assertEqual(len({tuple(k["between"]) for k in a["connectors"]}), 2)
        self.assertAlmostEqual(a["end"][2], 0.3, places=9)
        lengths = [p["length"] for p in a["parts"]]
        self.assertAlmostEqual(lengths[0], 1.5, places=9)
        runs = self.meta(self.d)[f"{NS}:component_faces"]
        self.assertEqual([r["id"] for r in runs], [c["id"] for c in self.comps(self.d)] + ["k1", "k2"])
        self.assertEqual(sum(r["count"] for r in runs), len(self.p.model.get(self.d)["geometry"]["faces"]))

    def test_a_section_of_another_diameter_is_refused_not_joined(self):
        first = self.comps(self.d)[0]["id"]
        with self.assertRaises(BadRequest):
            self.edit(self.d, "set", component=first, field="diameter", value=100)
        self.assertNotIn("diameter", self.comps(self.d)[0])

    def test_a_shoe_closes_the_bottom_and_a_hopper_head_the_top(self):
        self.edit(self.d, "add", kind="shoe")
        self.assertEqual(self.comps(self.d)[-1]["kind"], "shoe")
        with self.assertRaises(BadRequest):
            self.edit(self.d, "add", kind="shoe")
        shoe = self.comps(self.d)[-1]["id"]
        with self.assertRaises(BadRequest):
            self.edit(self.d, "move", component=shoe, index=0)
        x, y, z = self.end()
        self.assertAlmostEqual(z, 0.3, places=9)                                               # the free section still brings the outlet to the discharge height
        self.edit(self.d, "add", kind="hopper")
        self.assertEqual(self.comps(self.d)[0]["kind"], "hopper")
        g_tris = _world_triangles(self.p, self.g)
        x0, y0 = GU.pipe_frame(self.p, self.d)["at"]
        self.assertEqual(ray_hits(g_tris, x0, y0), 0)                                          # the gutter is still open over the hopper's outlet
        self.assertGreater(self.vol(self.d), 0)

    def test_a_y_branch_takes_a_branch_no_wider_than_the_pipe(self):
        made = self.edit(self.d, "add", kind="y_branch", index=0)["created"]
        before = self.vol(self.d)
        self.edit(self.d, "set", component=made, field="branch_length", value=400)
        self.assertGreater(self.vol(self.d), before)
        with self.assertRaises(BadRequest):
            self.edit(self.d, "set", component=made, field="branch_diameter", value=120)
        scene = {o["id"]: o for o in self.p.scene_data()["objects"]}
        self.assertGreater(len(scene[self.d]["faces"]), 0)

    def test_every_component_edit_is_one_undo_step_and_survives_save_and_reopen(self):
        made = self.edit(self.d, "add", kind="offset_bend", index=0)["created"]
        self.edit(self.d, "set", component=made, field="offset", value=250)
        self.edit(self.d, "add", kind="shoe")
        vol = self.vol(self.d)
        comps = self.comps(self.d)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pipes.spadra"
            self.p.save(path)
            again = Project.open(path)
            self.assertEqual(GU.components_of(E.meta_of(again, self.d)), comps)
            self.assertAlmostEqual(again.model.volume(self.d, unit="m"), vol, places=9)
            E.regenerate(again, self.d)                                                         # regenerated from its parameters: the same solid
            self.assertAlmostEqual(again.model.volume(self.d, unit="m"), vol, places=9)
        self.p.undo()
        self.assertEqual([c["kind"] for c in self.comps(self.d)], ["offset_bend", "pipe"])
        self.p.undo()
        self.assertAlmostEqual(self.comps(self.d)[0]["offset"], 0.3, places=9)

    def test_a_component_is_edited_from_its_own_properties_panel(self):
        made = self.edit(self.d, "add", kind="elbow", index=0)["created"]
        props = self.p.properties({"model": self.d, "part": f"comp:{made}"})
        keys = {r.get("key") for r in props["top"]}
        self.assertEqual(keys, {f"comp:{made}:angle", f"comp:{made}:direction"})
        self.assertIn("Elbow", props["title"])
        self.run_("model.set_property", id=self.d, key=f"comp:{made}:angle", value="72")
        self.assertEqual(self.comps(self.d)[0]["angle"], 72.0)
        whole = E.properties(self.p, self.d)
        links = [r for g in whole["groups"] if g["title"].startswith("Components") for r in g["rows"] if r["type"] == "link"]
        self.assertEqual([r["target"]["select"]["part"] for r in links], [f"comp:{c['id']}" for c in self.comps(self.d)])


class Classification(Case):
    def test_components_inherit_the_downpipes_or_the_gutters_code_and_keep_their_own_ifc_entity(self):
        g = self.gutter()
        d = self.pipe(g)
        bend = self.edit(d, "add", kind="offset_bend", index=0)["created"]
        c = next(x for x in GU.components_of(self.meta(d)) if x["id"] == bend)
        self.assertEqual(GU.component_classification(self.p, d, c)["sfb"], "")
        self.run_("model.set_property", id=g, key="sfb", value="(27.16)")
        cls = GU.component_classification(self.p, d, c)
        self.assertEqual((cls["sfb"], cls["sfb_from"]), ("(27.16)", "the gutter"))
        self.assertEqual((cls["ifc_entity"], cls["ifc_predefined_type"]), ("IfcPipeFitting", "BEND"))
        self.run_("model.set_property", id=d, key="sfb", value="(52.12)")
        self.assertEqual(GU.component_classification(self.p, d, c)["sfb_from"], "the downpipe")
        self.run_("model.set_property", id=d, key=f"comp:{bend}:sfb", value="(52.11)")
        c = next(x for x in GU.components_of(self.meta(d)) if x["id"] == bend)
        cls = GU.component_classification(self.p, d, c)
        self.assertEqual((cls["sfb"], cls["sfb_from"]), ("(52.11)", "this component"))
        with self.assertRaises(BadRequest):
            self.run_("model.set_property", id=d, key=f"comp:{bend}:sfb", value="(99.99)")      # not in NL-SfB table 1
        pipe_part = next(x for x in GU.components_of(self.meta(d)) if x["kind"] == "pipe")
        self.assertEqual(GU.component_classification(self.p, d, pipe_part)["ifc_predefined_type"], "RIGIDSEGMENT")
        self.assertEqual(GU.component_classification(self.p, d, pipe_part)["sfb"], "(52.12)")   # still inherited after the regenerations


if __name__ == "__main__":
    unittest.main()
