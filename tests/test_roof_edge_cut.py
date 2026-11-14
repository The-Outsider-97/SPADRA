"""Roof edges parallel to the roof-end cut, and parametric drip edges on the roof's top face (issue sheet 11-11-2026, item 2).

The edge member's section is laid out in the cross-section of its edge: its inner face lies on the roof end as it is actually cut (plumb, square to the
slope, or horizontal), so it is parallel to that cut for every pitch, turn and preset. A drip edge lies on the roof's top face, turns down over the end
(outside an edge member on the same edge) parallel to the cut, and follows the top face when the roof is thickened, re-pitched or turned."""
from __future__ import annotations

import math
import tempfile
from pathlib import Path

from applications.SPADRA import elements as E
from applications.SPADRA import geometry as G
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest

from .test_bim import Case

RECT = [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]


def turned(degrees):
    a = math.radians(degrees)
    return [[3000 + math.cos(a) * (x - 3000) - math.sin(a) * (y - 2000), 2000 + math.sin(a) * (x - 3000) + math.cos(a) * (y - 2000)] for x, y in RECT]


class EdgeCut(Case):
    def roof(self, points=RECT, pitch=40, kind="gable"):
        return self.run_("model.add_roof", points=points, level=self.roof_level, roof=kind, pitch=pitch, thickness=200)["id"]

    def mid_station(self, oid, roof, edge):
        """The section of an edge member at the middle of its edge, measured from the roof's top corner there: (q outward, z up) per section corner."""
        rm = E.meta_of(self.p, roof)
        eb = E.effective_boundary(self.p, rm)
        a, b = eb[edge], eb[(edge + 1) % len(eb)]
        sigma = 1.0 if G.signed_area(eb) > 0 else -1.0
        L = math.dist(a, b)
        n = (sigma * (b[1] - a[1]) / L, -sigma * (b[0] - a[0]) / L)
        u = ((b[0] - a[0]) / L, (b[1] - a[1]) / L)
        verts = next(o for o in self.p.scene_data()["objects"] if o["id"] == oid)["vertices"]
        sec = E.fascia_section(self.p, rm, edge, float(E.meta_of(self.p, oid)["spadra:width"]), float(E.meta_of(self.p, oid)["spadra:depth"]), 0.0, 0.0)
        return verts, sec, a, u, n

    def test_the_edge_member_lies_on_the_cut_and_parallel_to_it_for_every_cut_and_turn(self):
        for degrees in (0, 30, 135):
            for preset, cut in (("A1", "square"), ("B2", "plumb"), ("C3", "horizontal"), (None, "plumb")):
                with self.subTest(turn=degrees, preset=preset):
                    roof = self.roof(turned(degrees))
                    self.run_("model.add_roof_accessory", roof=roof, kind="roof_edge", edge=1)
                    if preset:
                        self.run_("model.set_roof_end", id=roof, edge=0, type=preset)
                    rm = E.meta_of(self.p, roof)
                    fr = E.edge_section_frame(self.p, rm, 0)
                    self.assertEqual(fr["kind"], cut)
                    sec = E.fascia_section(self.p, rm, 0, 0.06, 0.18, 0.0, 0.0)
                    du = fr["du"]
                    for idx in (0, 3):                                                              # the inner face lies on the cut line through the top corner
                        q, z = sec[idx]
                        self.assertAlmostEqual(du[0] * z - du[1] * q, 0.0, places=9)
                    inner = (sec[0][0] - sec[3][0], sec[0][1] - sec[3][1])
                    self.assertAlmostEqual(inner[0] * du[1] - inner[1] * du[0], 0.0, places=9)     # parallel to the cut
                    outer = (sec[1][0] - sec[2][0], sec[1][1] - sec[2][1])
                    self.assertAlmostEqual(outer[0] * du[1] - outer[1] * du[0], 0.0, places=9)
                    across = (sec[1][0] - sec[0][0], sec[1][1] - sec[0][1])
                    self.assertAlmostEqual(across[0] * du[0] + across[1] * du[1], 0.0, places=9)   # its width is square to the cut
                    self.assertAlmostEqual(math.hypot(*across), 0.06, places=9)
                    self.p.execute("model.delete", {"id": roof})

    def test_the_built_solid_has_that_section_on_the_eave(self):
        roof = self.roof()
        edge = self.run_("model.add_roof_accessory", roof=roof, kind="roof_edge", edge=1)["ids"][0]
        self.run_("model.set_roof_end", id=roof, edge=0, type="A1")
        verts, sec, a, u, n = self.mid_station(edge, roof, 0)
        # the gable's eave has two stations (its two corners); the first four vertices are the start section, carried onto the corner's bisector
        z_top = max(v[2] for v in verts[:4]) - max(z for _, z in sec)
        for (q, z), v in zip(sec, verts[:4]):
            self.assertAlmostEqual(v[2] - z_top, z, places=9)                                   # heights follow the tilted section exactly
        tan = math.tan(math.radians(40))
        dz = verts[0][2] - verts[3][2]
        dn = (verts[0][0] - verts[3][0]) * n[0] + (verts[0][1] - verts[3][1]) * n[1]
        self.assertGreater(abs(dn), 0.05)                                                       # tilted: not the old upright fascia
        self.assertAlmostEqual(dn / dz, tan, places=6)                                          # square to a 40° slope: its top leans out by tan 40°

    def test_an_edge_member_follows_a_new_cut_and_a_new_pitch(self):
        roof = self.roof()
        edge = self.run_("model.add_roof_accessory", roof=roof, kind="roof_edge", edge=1)["ids"][0]
        self.run_("model.set_roof_end", id=roof, edge=0, type="A1")
        before = [list(v) for v in next(o for o in self.p.scene_data()["objects"] if o["id"] == edge)["vertices"][:4]]
        self.run_("model.set_property", id=roof, key="pitch", value=25)
        after = next(o for o in self.p.scene_data()["objects"] if o["id"] == edge)["vertices"][:4]
        self.assertNotEqual([round(c, 6) for v in before for c in v], [round(c, 6) for v in after for c in v])
        fr = E.edge_section_frame(self.p, E.meta_of(self.p, roof), 0)
        self.assertAlmostEqual(fr["dcut"][0] / fr["dcut"][1], math.tan(math.radians(25)), places=9)


class DripEdge(Case):
    def roof(self, kind="gable", pitch=40):
        return self.run_("model.add_roof", points=RECT, level=self.roof_level, roof=kind, pitch=pitch, thickness=200)["id"]

    def drip(self, roof, edge=1, **kw):
        return self.run_("model.add_roof_accessory", roof=roof, kind="drip_edge", edge=edge, **kw)["ids"][0]

    def verts(self, oid):
        return next(o for o in self.p.scene_data()["objects"] if o["id"] == oid)["vertices"]

    def test_the_flange_lies_on_the_top_face_and_the_face_runs_down_parallel_to_the_cut(self):
        roof = self.roof()
        self.run_("model.set_roof_end", id=roof, edge=0, type="A1")
        d = self.drip(roof)
        rm = E.meta_of(self.p, roof)
        fr = E.edge_section_frame(self.p, rm, 0)
        sec = E.drip_section(self.p, rm, 0, E.meta_of(self.p, d))
        p0, corner, p2 = sec[0], sec[1], sec[2]
        self.assertAlmostEqual(p0[1] - fr["g"] * p0[0], 0.0, places=9)                          # on the top surface (z = g·q through the top corner)
        self.assertAlmostEqual(corner[1] - fr["g"] * corner[0], 0.0, places=9)
        face = (corner[0] - p2[0], corner[1] - p2[1])
        self.assertAlmostEqual(face[0] * fr["dcut"][1] - face[1] * fr["dcut"][0], 0.0, places=9)   # parallel to the square cut
        self.assertAlmostEqual(math.hypot(*face), 0.05, places=9)
        self.assertGreater(self.p.model.volume(d, unit="m"), 0)
        scene = {o["id"]: o for o in self.p.scene_data()["objects"]}
        self.assertTrue(G.is_closed((scene[d]["vertices"], scene[d]["faces"])))

    def test_it_wraps_the_edge_member_on_its_edge_and_follows_it(self):
        roof = self.roof()
        d = self.drip(roof)
        rm = E.meta_of(self.p, roof)
        bare = E.drip_section(self.p, rm, 0, E.meta_of(self.p, d))
        fascia = self.run_("model.add_roof_accessory", roof=roof, kind="roof_edge", edge=1)["ids"][0]
        wrapped = E.drip_section(self.p, rm, 0, E.meta_of(self.p, d))
        self.assertAlmostEqual(wrapped[2][0] - bare[2][0], 0.06, places=9)                     # its face stands outside the 60 mm member
        self.run_("model.set_property", id=fascia, key="width", value=80)
        self.assertAlmostEqual(E.drip_section(self.p, rm, 0, E.meta_of(self.p, d))[2][0] - bare[2][0], 0.08, places=9)
        self.run_("model.delete", id=fascia)
        self.assertAlmostEqual(E.drip_section(self.p, rm, 0, E.meta_of(self.p, d))[2][0], bare[2][0], places=9)

    def test_it_follows_the_top_face_when_layers_are_added_and_the_roof_changes(self):
        roof = self.roof(kind="flat")
        d = self.drip(roof)
        top = max(v[2] for v in self.verts(d))
        self.run_("model.add_layer", id=roof, material="Timber", thickness=30)                  # a flat roof's top rises as it thickens
        self.assertAlmostEqual(max(v[2] for v in self.verts(d)) - top, 0.03, places=9)
        pitched = self.roof(kind="gable", pitch=30)
        p_drip = self.drip(pitched)
        self.run_("model.set_property", id=pitched, key="pitch", value=45)
        fr = E.edge_section_frame(self.p, E.meta_of(self.p, pitched), 0)
        self.assertAlmostEqual(-fr["g"], 1.0, places=9)
        sec = E.drip_section(self.p, E.meta_of(self.p, pitched), 0, E.meta_of(self.p, p_drip))
        self.assertAlmostEqual(sec[0][1] - fr["g"] * sec[0][0], 0.0, places=9)                 # still on the re-pitched top face

    def test_parameters_are_edited_checked_undone_and_saved(self):
        roof = self.roof()
        d = self.drip(roof)
        self.run_("model.set_property", id=d, key="flange", value=100)
        self.run_("model.set_property", id=d, key="kick_angle", value=45)
        with self.assertRaises(BadRequest):
            self.run_("model.set_property", id=d, key="face", value=0)
        vol = self.p.model.volume(d, unit="m")
        props = E.properties(self.p, d)
        self.assertEqual({r.get("key") for r in props["top"]}, {"flange", "face", "kick", "kick_angle", "thickness", "material"})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "drip.spadra"
            self.p.save(path)
            again = Project.open(path)
            self.assertAlmostEqual(again.model.volume(d, unit="m"), vol, places=12)
        self.p.undo()
        self.assertAlmostEqual(E.meta_of(self.p, d)["spadra:kick_angle"], 30.0)
        self.run_("model.delete", id=roof)
        self.assertFalse(E.alive(self.p, d))                                                    # it goes with its roof
