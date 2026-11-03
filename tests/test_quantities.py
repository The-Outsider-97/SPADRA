"""Quantities: closed-form checks of the centroid helper, then the take-off against SGEO's own volume and area, openings, density and totals."""
from __future__ import annotations

import math
import unittest

from applications.SPADRA import quantities as Q
from applications.SPADRA.engineering_bridge import BRIDGE as ENGINEERING
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest

BOX_FACES = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]


def box(x0, y0, z0, x1, y1, z1):
    return [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0], [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]]


class Centroid(unittest.TestCase):
    def test_a_box_has_its_volume_and_its_centre_at_the_middle(self):
        volume, c = Q.centroid_of_closed_mesh(box(1, 2, 3, 5, 4, 9), BOX_FACES)
        self.assertAlmostEqual(volume, 4 * 2 * 6, places=12)
        for got, want in zip(c, (3.0, 3.0, 6.0)):
            self.assertAlmostEqual(got, want, places=12)

    def test_the_result_does_not_depend_on_where_the_origin_is(self):
        a = Q.centroid_of_closed_mesh(box(0, 0, 0, 2, 2, 2), BOX_FACES)
        b = Q.centroid_of_closed_mesh(box(1000, -500, 70, 1002, -498, 72), BOX_FACES)
        self.assertAlmostEqual(a[0], b[0], places=6)
        for x, y in zip(b[1], (1001, -499, 71)):
            self.assertAlmostEqual(x, y, places=6)

    def test_inward_winding_gives_the_same_positive_volume(self):
        flipped = [(a, c, b) for a, b, c in BOX_FACES]
        self.assertAlmostEqual(Q.centroid_of_closed_mesh(box(0, 0, 0, 1, 2, 3), flipped)[0], 6.0, places=12)

    def test_a_tetrahedron_centroid_is_the_mean_of_its_vertices(self):
        v = [[0, 0, 0], [4, 0, 0], [0, 4, 0], [0, 0, 4]]
        volume, c = Q.centroid_of_closed_mesh(v, [(0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)])
        self.assertAlmostEqual(volume, 4 ** 3 / 6, places=12)
        for x in c:
            self.assertAlmostEqual(x, 1.0, places=12)

    def test_an_open_or_empty_mesh_is_refused(self):
        with self.assertRaises(BadRequest):
            Q.centroid_of_closed_mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [(0, 1, 2), (0, 2, 1)])


class TakeOff(unittest.TestCase):
    def setUp(self):
        self.p = Project.create("Q")
        self.level = self.p.manifest["levels"][0]["id"]
        self.wall = self.p.execute("model.add_wall", {"start": [0, 0], "end": [4000, 0], "level": self.level, "thickness": 200, "height": 3})["result"]["id"]

    def test_a_wall_matches_closed_form_and_sgeo(self):
        row = self.p.quantities()["rows"][0]
        self.assertAlmostEqual(row["volume_m3"], 4 * 0.2 * 3, places=9)
        self.assertAlmostEqual(row["surface_area_m2"], 2 * (4 * 0.2 + 0.2 * 3 + 4 * 3), places=9)
        self.assertAlmostEqual(row["volume_m3"], self.p.model.volume(self.wall, unit="m"), places=12)
        for got, want in zip(row["centroid_m"], (2.0, 0.0, 1.5)):
            self.assertAlmostEqual(got, want, places=9)
        for got, want in zip(row["size_m"], (4.0, 0.2, 3.0)):
            self.assertAlmostEqual(got, want, places=9)

    def test_a_door_opening_takes_its_volume_out_and_moves_the_centre_away_from_it(self):
        before = self.p.quantities()["rows"][0]
        self.p.execute("model.add_door", {"wall": self.wall, "offset": 3000, "width": 900, "height": 2100})
        wall = next(r for r in self.p.quantities()["rows"] if r["id"] == self.wall)
        self.assertAlmostEqual(before["volume_m3"] - wall["volume_m3"], 0.9 * 0.2 * 2.1, places=9)
        self.assertLess(wall["centroid_m"][0], before["centroid_m"][0])                    # the door is at the right-hand end, so the centre shifts left
        self.assertGreater(wall["centroid_m"][2], before["centroid_m"][2])                  # and up, since the material removed is low down

    def test_no_density_means_no_mass_and_says_so(self):
        out = self.p.quantities()
        self.assertIsNone(out["rows"][0]["mass_kg"])
        self.assertIsNone(out["totals"]["mass_kg"])
        self.assertFalse(out["totals"]["mass_complete"])
        self.assertEqual(out["needs_density"], ["Concrete"])
        self.assertEqual(out["centre"]["kind"], "centre of volume")
        self.assertTrue(any("no density for Concrete" in n for n in out["rows"][0]["notes"]))

    def test_an_entered_density_gives_mass_and_a_centre_of_mass(self):
        out = self.p.quantities(None, {"Concrete": 2400})
        row = out["rows"][0]
        self.assertAlmostEqual(row["mass_kg"], 2.4 * 2400, places=6)
        self.assertEqual(row["density_source"], "entered by you")
        self.assertTrue(out["totals"]["mass_complete"])
        self.assertEqual(out["centre"]["kind"], "centre of mass")
        self.assertEqual(out["needs_density"], [])

    def test_combined_centre_is_weighted_by_mass(self):
        other = self.p.execute("model.add_wall", {"start": [0, 5000], "end": [4000, 5000], "level": self.level, "thickness": 200, "height": 3})["result"]["id"]
        self.p.execute("model.set_property", {"id": other, "key": "material", "value": "Masonry"})
        light = self.p.quantities([self.wall, other], {"Concrete": 1000, "Masonry": 3000})
        y = light["centre"]["point_m"][1]
        self.assertAlmostEqual(y, (1000 * 0.0 + 3000 * 5.0) / 4000, places=9)               # same volume, three times the density: three quarters of the way
        by_volume = self.p.quantities([self.wall, other])
        self.assertEqual(by_volume["centre"]["kind"], "centre of volume")
        self.assertAlmostEqual(by_volume["centre"]["point_m"][1], 2.5, places=9)

    def test_a_partly_known_density_does_not_pretend_to_be_a_total(self):
        other = self.p.execute("model.add_wall", {"start": [0, 5000], "end": [4000, 5000], "level": self.level, "thickness": 200, "height": 3})["result"]["id"]
        self.p.execute("model.set_property", {"id": other, "key": "material", "value": "Timber"})
        out = self.p.quantities([self.wall, other], {"Concrete": 2400})
        self.assertAlmostEqual(out["totals"]["mass_kg"], 2.4 * 2400, places=6)              # only what has a density
        self.assertEqual(out["needs_density"], ["Timber"])
        self.assertFalse(out["totals"]["mass_complete"])
        self.assertEqual(out["centre"]["kind"], "centre of volume")

    def test_material_quantities_group_by_material(self):
        self.p.execute("model.add_wall", {"start": [0, 5000], "end": [2000, 5000], "level": self.level, "thickness": 200, "height": 3})
        out = self.p.quantities(None, {"Concrete": 2400})
        (concrete,) = out["by_material"]
        self.assertEqual((concrete["material"], concrete["count"]), ("Concrete", 2))
        self.assertAlmostEqual(concrete["volume_m3"], 2.4 + 1.2, places=9)
        self.assertAlmostEqual(concrete["mass_kg"], 3.6 * 2400, places=6)
        self.assertAlmostEqual(out["totals"]["volume_m3"], 3.6, places=9)

    def test_a_selection_and_the_whole_model_differ_as_they_should(self):
        self.p.execute("model.add_wall", {"start": [0, 5000], "end": [2000, 5000], "level": self.level, "thickness": 200, "height": 3})
        self.assertEqual(self.p.quantities([self.wall])["totals"]["count"], 1)
        self.assertEqual(self.p.quantities()["totals"]["count"], 2)

    def test_edits_change_the_quantities(self):
        a = self.p.quantities()["totals"]["volume_m3"]
        self.p.execute("model.set_property", {"id": self.wall, "key": "height", "value": 6})
        self.assertAlmostEqual(self.p.quantities()["totals"]["volume_m3"], 2 * a, places=9)

    def test_bad_requests_are_refused(self):
        for ids, dens in ((["nope"], None), ([5], None), ("x", None), (None, {"Concrete": -1}), (None, {"Concrete": "heavy"}), (None, {"Concrete": 1e9}), (None, [1])):
            with self.assertRaises(BadRequest, msg=f"{ids} {dens}"):
                self.p.quantities(ids, dens)
        empty = Project.create("E")
        with self.assertRaises(BadRequest):
            empty.quantities()

    def test_the_model_is_not_changed(self):
        revision = self.p.revision
        self.p.quantities(None, {"Concrete": 2400})
        self.assertEqual((self.p.revision, self.p.dirty), (revision, True))                 # read-only: no new revision, no undo entry
        self.assertEqual(len(self.p.undo_stack), 1)

    @unittest.skipUnless(ENGINEERING.available, "the SLAI STEM Agent is not importable here")
    def test_steel_uses_the_engines_density_and_says_whose_it_is(self):
        beam = self.p.execute("model.add_beam", {"start": [0, 0], "end": [6000, 0], "level": self.level, "top": 3, "width": 200, "depth": 400, "material": "Steel"})["result"]["id"]
        row = next(r for r in self.p.quantities([beam])["rows"] if r["id"] == beam)
        self.assertAlmostEqual(row["volume_m3"], 6 * 0.2 * 0.4, places=9)
        self.assertEqual(row["density_kg_m3"], ENGINEERING.materials()["Steel"]["density_kg_m3"])
        self.assertEqual(row["density_source"], "the SLAI engine's value")
        self.assertAlmostEqual(row["mass_kg"], row["volume_m3"] * row["density_kg_m3"], places=9)
        entered = next(r for r in self.p.quantities([beam], {"Steel": 7000})["rows"] if r["id"] == beam)
        self.assertEqual((entered["density_kg_m3"], entered["density_source"]), (7000.0, "entered by you"))


if __name__ == "__main__":
    unittest.main()


class ScratchMeasurementEqualsTheFullModel(unittest.TestCase):
    """The take-off measures each element alone in a scratch SGEO document (so the cost does not grow with the model). The numbers must be
    exactly what SGEO answers for the element inside the real model, for every kind of element, including rotated and imported ones."""

    def build(self):
        import base64
        p = Project.create("Scratch")
        level = p.manifest["levels"][0]["id"]
        sq = [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]
        wall = p.execute("model.add_wall", {"start": [0, 0], "end": [5000, 3000], "level": level, "thickness": 230, "height": 3.1})["result"]["id"]      # not axis-aligned
        p.execute("model.add_door", {"wall": wall, "offset": 1.0, "width": 900, "height": 2100})
        p.execute("model.add_window", {"wall": wall, "offset": 3.5, "width": 1200, "height": 1200, "sill": 900})
        p.execute("model.add_floor", {"level": level, "points": sq, "thickness": 200})
        p.execute("model.add_roof", {"level": level, "points": sq, "pitch": 20, "thickness": 150})
        p.execute("model.add_beam", {"start": [0, 0], "end": [6000, 1000], "level": level, "top": 3, "width": 200, "depth": 400, "material": "Steel"})
        glb, _ = p.model.to_glb()
        p.execute("model.import", {"format": "glb", "data": base64.b64encode(glb).decode(), "name": "copy.glb"})
        return p

    def test_every_element_measures_the_same_alone_and_in_the_model(self):
        from applications.SPADRA.project import all_ids
        p = self.build()
        matrices = p.model._model.world_transforms()
        ids = all_ids(p.model, has_geometry=True)
        self.assertGreaterEqual(len(ids), 12)
        for oid in ids:
            volume, area = Q._measure(p.model, p.model.get(oid), matrices[oid])
            self.assertEqual(volume(), p.model.volume(oid, unit="m"), oid)
            self.assertEqual(area(), p.model.surface_area(oid, unit="m"), oid)

    def test_the_whole_model_take_off_is_unchanged_by_it(self):
        p = self.build()
        q = p.quantities(None, None)
        for row in q["rows"]:
            self.assertEqual(row["volume_m3"], p.model.volume(row["id"], unit="m"), row["name"])
            self.assertEqual(row["surface_area_m2"], p.model.surface_area(row["id"], unit="m"), row["name"])

    def test_it_falls_back_to_the_full_model_if_a_scratch_document_cannot_be_made(self):
        from unittest import mock
        from src.utils.spatial_formats.common.errors import ValidationError
        p = self.build()
        oid = next(r["id"] for r in p.quantities(None, None)["rows"] if r["category"] == "wall")
        with mock.patch("src.utils.sgeo.SGEODocument.new", side_effect=ValidationError("no scratch")):
            volume, area = Q._measure(p.model, p.model.get(oid), p.model._model.world_transforms()[oid])
        self.assertEqual(volume(), p.model.volume(oid, unit="m"))
        self.assertEqual(area(), p.model.surface_area(oid, unit="m"))
        with mock.patch("src.utils.sgeo.SGEODocument.new", side_effect=RuntimeError("a bug in the library")):
            with self.assertRaises(RuntimeError):                                  # only the library's own refusals fall back; a bug is not hidden
                Q._measure(p.model, p.model.get(oid), p.model._model.world_transforms()[oid])
