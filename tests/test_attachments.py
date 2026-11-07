"""Walls attached to a roof or extended to a level: their height is derived, follows what they follow, and goes back when freed."""
from __future__ import annotations

import math
import tempfile
from collections import Counter
from pathlib import Path

from applications.SPADRA import attachments as AT
from applications.SPADRA import documentation as DOC
from applications.SPADRA import elements as E
from applications.SPADRA import planar as P
from applications.SPADRA import rooms as RM
from applications.tools.errors import BadRequest

from .test_bim import Case

RECT = [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]


class Attachments(Case):
    def _eave_wall(self, thickness=500):
        """A wall parallel to the ridge, whose two faces lie at different roof heights."""
        return self.run_("model.add_wall", start=[1000, 500], end=[5000, 500], level=self.l1,
                         thickness=thickness, height=3.2)["id"]

    def _top_of_face(self, wall, y):
        verts, _ = DOC.world_mesh(self.p, wall, self.p.model._model.world_transforms())
        return max(v[2] for v in verts if abs(v[1] - y) < 1e-8)

    def test_eave_wall_is_clipped_to_roof_underside_at_both_faces(self):
        wall, roof = self._eave_wall(), self.roof()
        self.run_("model.attach_wall", ids=[wall, roof], to="roof")
        base = self.elevation(self.roof_level) - 0.1
        slope = math.tan(math.radians(30))
        self.assertAlmostEqual(self._top_of_face(wall, 0.25), base + 0.25 * slope, places=7)
        self.assertAlmostEqual(self._top_of_face(wall, 0.75), base + 0.75 * slope, places=7)
        self.assertAlmostEqual(self.meta(wall)["spadra:height"], base + 0.75 * slope, places=7)
        self.assertAlmostEqual(self.vol(wall), 4 * .5 * (base + .5 * slope), places=6)
        self.run_("model.set_property", id=roof, key="pitch", value=45)
        self.assertAlmostEqual(self._top_of_face(wall, 0.25), base + 0.25, places=7)
        self.assertAlmostEqual(self._top_of_face(wall, 0.75), base + 0.75, places=7)
        self.p.undo()
        self.assertAlmostEqual(self._top_of_face(wall, 0.25), base + 0.25 * slope, places=7)
        self.run_("model.set_property", id=roof, key="ridge_offset", value=300)
        slope_after_ridge_shift = 2.0 * slope / 2.3
        self.assertAlmostEqual(self._top_of_face(wall, 0.25), base + 0.25 * slope_after_ridge_shift, places=7)
        self.assertAlmostEqual(self._top_of_face(wall, 0.75), base + 0.75 * slope_after_ridge_shift, places=7)

    def test_a_door_cut_is_preserved_under_face_wise_roof_clipping(self):
        wall, roof = self._eave_wall(), self.roof()
        door = self.run_("model.add_door", wall=wall, offset=2.0, width=900, height=2100)["id"]
        self.run_("model.attach_wall", ids=[wall, roof], to="roof")
        base = self.elevation(self.roof_level) - 0.1
        slope = math.tan(math.radians(30))
        without_opening = 4 * .5 * (base + .5 * slope)
        self.assertAlmostEqual(self.vol(wall), without_opening - .9 * .5 * 2.1, places=6)
        self.assertEqual(E.m(self.meta(door), "host"), wall)
        self.assertAlmostEqual(self._top_of_face(wall, .25), base + .25 * slope, places=7)

    def test_face_wise_roof_attachment_survives_native_project_round_trip(self):
        wall, roof = self._eave_wall(), self.roof()
        self.run_("model.attach_wall", ids=[wall, roof], to="roof")
        before = [self._top_of_face(wall, y) for y in (.25, .75)]
        with tempfile.TemporaryDirectory() as tmp:
            self.p.save(Path(tmp) / "attached.spadra")
            self.p = type(self.p).open(Path(tmp) / "attached.spadra")
            self.assertEqual([round(self._top_of_face(wall, y), 8) for y in (.25, .75)],
                             [round(z, 8) for z in before])
            self.run_("model.set_property", id=roof, key="pitch", value=45)
            self.assertNotAlmostEqual(self._top_of_face(wall, .25), before[0], places=5)


    def test_layered_eave_wall_clips_each_independent_material_shell(self):
        wall, roof = self._eave_wall(), self.roof()
        self.run_("model.add_layer", id=wall, side="right", material="Mineral wool", thickness=100)
        self.run_("model.attach_wall", ids=[wall, roof], to="roof")
        base = self.elevation(self.roof_level) - 0.1
        slope = math.tan(math.radians(30))
        for y in (0.2, 0.3, 0.8):
            self.assertAlmostEqual(self._top_of_face(wall, y), base + slope * y, places=7)
        self.assertAlmostEqual(self.vol(wall), 4 * .6 * (base + .5 * slope), places=6)

    def test_shed_roof_wall_follows_transverse_incline_without_penetration(self):
        wall, roof = self._eave_wall(), self.roof(kind="shed")
        self.run_("model.attach_wall", ids=[wall, roof], to="roof")
        base = self.elevation(self.roof_level) - .1
        rise = math.tan(math.radians(30))
        for y in (.25, .75):
            self.assertAlmostEqual(self._top_of_face(wall, y), base + y * rise, places=7)

    def test_oblique_gable_and_hip_attachments_are_piecewise_exact_watertight_and_layer_aware(self):
        """A ridge crossing wall thickness obliquely must split top facets, not make a twisted quad."""
        for kind, layered in ((kind, layered) for kind in ("gable", "hip") for layered in (False, True)):
            with self.subTest(kind=kind, layered=layered):
                p = type(self.p).create("Diagonal roof clipping")
                self.p = p
                self.l1, self.l2, self.roof_level = (l["id"] for l in p.manifest["levels"])
                wall = self.run_("model.add_wall", start=[1000, 500], end=[5000, 3500], level=self.l1,
                                 thickness=500, height=3.2)["id"]
                roof = self.roof(kind=kind)
                if layered:
                    self.run_("model.add_layer", id=wall, side="right", material="Mineral wool", thickness=100)
                self.run_("model.attach_wall", ids=[wall, roof], to="roof")
                verts, faces = DOC.world_mesh(self.p, wall, self.p.model._model.world_transforms())
                edges = Counter(tuple(sorted((a, b))) for f in faces for a, b in zip(f, f[1:] + f[:1]))
                self.assertEqual(set(edges.values()), {2}, "every triangle edge is paired in a closed wall shell")

                # Independent exact integration of the piecewise planar roof
                # over the wall's footprint, including the ridge's oblique cut.
                fr = E.wall_frame(self.p, wall)
                c, s, half = math.cos(fr["angle"]), math.sin(fr["angle"]), fr["thickness"] / 2
                a, b = fr["start"], fr["end"]
                polygon = [(a[0] - s * half, a[1] + c * half), (b[0] - s * half, b[1] + c * half),
                           (b[0] + s * half, b[1] - c * half), (a[0] + s * half, a[1] - c * half)]
                expected = 0.0
                for ring, plane in E.roof_facets(self.p, E.meta_of(self.p, roof)):
                    for outer, holes in P.intersection(polygon, ring):
                        area, integrated_z = RM._region_integral(outer, holes, plane)
                        expected += integrated_z - area * fr["base"]
                self.assertAlmostEqual(self.vol(wall), expected, delta=1e-6 * max(1.0, expected))

                self.run_("model.set_property", id=roof, key="ridge_offset" if kind == "gable" else "pitch", value=300 if kind == "gable" else 45)
                self.p.undo()
                self.assertAlmostEqual(self.vol(wall), expected, delta=1e-6 * max(1.0, expected))

    def test_offset_ridge_preserves_peak_elevation_and_updates_attached_gable_wall(self):
        w, r = self.gable_wall(), self.roof()
        self.run_("model.attach_wall", ids=[w, r], to="roof")
        before = self.top(w)
        self.run_("model.set_property", id=r, key="ridge_offset", value=600)
        self.assertAlmostEqual(self.top(w), before, places=6)
        prof = self.meta(w)["spadra:top_profile"]
        self.assertEqual([round(s, 6) for s, _ in prof], [0.0, 2.6, 4.0])
        self.assertNotAlmostEqual(prof[1][0] - prof[0][0], prof[2][0] - prof[1][0])
        plan = next(v["id"] for v in self.p.manifest["views"] if v.get("level_id") == self.roof_level and v["kind"] == "plan")
        ridge = next(q["ridge"] for q in self.p.plan_overlay(plan) if q["id"] == r)
        self.assertAlmostEqual((ridge[0][0] + ridge[1][0]) / 2, 3000.0)
        self.assertAlmostEqual((ridge[0][1] + ridge[1][1]) / 2, 2600.0)
        handle = next(q["ridge_handle"] for q in self.p.scene_data()["objects"] if q["id"] == r)
        self.assertAlmostEqual(handle["offset_mm"], 600)
        self.assertAlmostEqual(handle["point"][2], self.elevation(self.roof_level) + 2 * math.tan(math.radians(30)))
        self.assertAlmostEqual(self.vol(w), .25 * (4 * (self.elevation(self.roof_level) - .1) + 2 * math.tan(math.radians(30)) * 2), places=5)
        self.p.undo()
        self.assertEqual([round(s, 6) for s, _ in self.meta(w)["spadra:top_profile"]], [0.0, 2.0, 4.0])

    def elevation(self, level):
        return self.p.level(level)["elevation"]

    def roof(self, kind="gable", **extra):
        return self.run_("model.add_roof", level=self.roof_level, roof=kind, points=RECT, thickness=100, pitch=30, **extra)["id"]

    def gable_wall(self):
        """The wall along the short edge, x = 0: under the roof's gable end, so the roof line rises to the ridge and falls again."""
        return self.run_("model.add_wall", start=[0, 0], end=[0, 4000], level=self.l1, thickness=250, height=3.2)["id"]

    def top(self, oid):
        return self.p.model.world_bounds(oid)[1][2]

    # ------------------------------------------------------------------------------------------------------------- roof
    def test_a_wall_under_a_gable_roof_follows_the_underside_up_to_the_ridge(self):
        w, r = self.gable_wall(), self.roof()
        out = self.run_("model.attach_wall", ids=[w, r], to="roof")
        self.assertEqual(out["walls"], [w])
        eaves = self.elevation(self.roof_level) - 0.1
        rise = math.tan(math.radians(30)) * 2.0
        prof = self.meta(w)["spadra:top_profile"]
        self.assertEqual([round(s, 6) for s, _ in prof], [0.0, 2.0, 4.0])
        self.assertAlmostEqual(prof[0][1], eaves - self.elevation(self.l1), places=6)
        self.assertAlmostEqual(prof[1][1], eaves + rise - self.elevation(self.l1), places=6)
        self.assertAlmostEqual(self.meta(w)["spadra:height"], eaves + rise, places=6)
        self.assertAlmostEqual(self.top(w), eaves + rise, places=6)
        # a gable: two trapezoids under the roof line, 0.25 thick
        self.assertAlmostEqual(self.vol(w), 0.25 * (4.0 * eaves + 0.5 * 4.0 * rise), places=5)

    def test_the_wall_follows_the_roof_when_the_roof_is_edited(self):
        w, r = self.gable_wall(), self.roof()
        self.run_("model.attach_wall", ids=[w, r], to="roof")
        before = self.vol(w)
        self.run_("model.set_property", id=r, key="pitch", value=45)
        rise = math.tan(math.radians(45)) * 2.0
        self.assertAlmostEqual(self.top(w), self.elevation(self.roof_level) - 0.1 + rise, places=6)
        self.assertGreater(self.vol(w), before)
        self.p.undo()
        self.assertAlmostEqual(self.top(w), self.elevation(self.roof_level) - 0.1 + math.tan(math.radians(30)) * 2.0, places=6)

    def test_a_flat_roof_gives_a_level_top_and_no_profile(self):
        w = self.wall(a=(0, 0), b=(6000, 0))
        r = self.roof("flat")
        self.run_("model.attach_wall", ids=[w, r], to="roof", offset=-100)
        self.assertIsNone(self.meta(w).get("spadra:top_profile"))
        self.assertAlmostEqual(self.meta(w)["spadra:height"], self.elevation(self.roof_level) - 0.1 - self.elevation(self.l1), places=6)

    def test_a_wall_that_sticks_out_from_under_the_roof_is_refused_with_how_much(self):
        w = self.wall(a=(0, 0), b=(8000, 0))
        r = self.roof("flat")
        with self.assertRaises(BadRequest) as c:
            self.run_("model.attach_wall", ids=[w, r], to="roof")
        self.assertIn("2.00 m", c.exception.message)
        self.assertIsNone(self.meta(w).get("spadra:top_to"))
        self.assertAlmostEqual(self.meta(w)["spadra:height"], 3.2)

    def test_a_height_cannot_be_typed_on_an_attached_wall_but_detaching_restores_it(self):
        w, r = self.gable_wall(), self.roof()
        self.run_("model.attach_wall", ids=[w, r], to="roof")
        with self.assertRaises(BadRequest) as c:
            self.run_("model.set_property", id=w, key="height", value=2.0)
        self.assertIn("attached", c.exception.message)
        self.run_("model.detach_wall", ids=[w], **{"from": "roof"})
        self.assertAlmostEqual(self.meta(w)["spadra:height"], 3.2)
        self.assertIsNone(self.meta(w).get("spadra:top_profile"))
        self.assertAlmostEqual(self.top(w), 3.2, places=6)
        self.assertAlmostEqual(self.vol(w), 0.25 * 4.0 * 3.2, places=5)

    def test_deleting_the_roof_frees_the_wall_at_its_old_height(self):
        w, r = self.gable_wall(), self.roof()
        self.run_("model.attach_wall", ids=[w, r], to="roof")
        self.run_("model.delete", id=r)
        self.assertIsNone(self.meta(w).get("spadra:top_to"))
        self.assertAlmostEqual(self.top(w), 3.2, places=6)
        self.p.undo()
        self.assertIsNotNone(self.meta(w).get("spadra:top_to"))

    def test_doors_are_kept_and_one_that_reaches_the_roof_is_refused(self):
        w, r = self.gable_wall(), self.roof()
        d = self.run_("model.add_door", wall=w, offset=2.0, width=900, height=2100)["id"]
        self.run_("model.attach_wall", ids=[w, r], to="roof")
        self.assertTrue(self.p.model.get(d))
        low = self.run_("model.add_wall", start=[0, 0], end=[0, 4000], level=self.l2, thickness=250, height=3.2)["id"]
        self.run_("model.add_door", wall=low, offset=2.0, width=900, height=2100)
        with self.assertRaises(BadRequest) as c:                                # a roof line that comes down below the head of the door
            self.run_("model.attach_wall", ids=[low, r], to="roof", offset=-1500)
        self.assertIsNone(self.meta(low).get("spadra:top_to"))

    def test_needs_a_roof_and_a_wall(self):
        w = self.gable_wall()
        with self.assertRaises(BadRequest):
            self.run_("model.attach_wall", ids=[w], to="roof")
        r = self.roof()
        with self.assertRaises(BadRequest):
            self.run_("model.attach_wall", ids=[r], to="roof")

    def test_a_layered_wall_follows_the_roof_in_every_layer(self):
        w, r = self.gable_wall(), self.roof()
        self.run_("model.add_layer", id=w, material="Mineral wool", thickness=100)
        self.run_("model.attach_wall", ids=[w, r], to="roof")
        eaves = self.elevation(self.roof_level) - 0.1
        rise = math.tan(math.radians(30)) * 2.0
        self.assertAlmostEqual(self.vol(w), 0.35 * (4.0 * eaves + 0.5 * 4.0 * rise), places=5)
        self.assertAlmostEqual(self.top(w), eaves + rise, places=6)

    # ------------------------------------------------------------------------------------------------------------ level
    def test_a_wall_extends_to_the_next_level_and_follows_it(self):
        w = self.wall()
        self.run_("model.attach_wall", ids=[w], to="level")
        self.assertAlmostEqual(self.meta(w)["spadra:height"], self.elevation(self.l2) - self.elevation(self.l1), places=9)
        self.run_("level.set", id=self.l2, elevation=self.elevation(self.l2) + 0.5)
        self.assertAlmostEqual(self.top(w), self.elevation(self.l2), places=6)
        self.p.undo()
        self.assertAlmostEqual(self.top(w), self.elevation(self.l2), places=6)

    def test_extending_to_a_level_with_an_offset_and_disconnecting(self):
        w = self.wall()
        self.run_("model.attach_wall", ids=[w], to="level", level=self.roof_level, offset=-250)
        self.assertAlmostEqual(self.top(w), self.elevation(self.roof_level) - 0.25, places=6)
        self.run_("model.detach_wall", ids=[w], **{"from": "level"})
        self.assertAlmostEqual(self.top(w), 3.2, places=6)
        with self.assertRaises(BadRequest):
            self.run_("model.detach_wall", ids=[w])

    def test_no_level_above_means_nothing_to_extend_to(self):
        w = self.run_("model.add_wall", start=[0, 0], end=[3000, 0], level=self.roof_level, thickness=250, height=1.0)["id"]
        with self.assertRaises(BadRequest) as c:
            self.run_("model.attach_wall", ids=[w], to="level")
        self.assertIn("no level above", c.exception.message)

    def test_properties_say_what_the_wall_follows(self):
        w, r = self.gable_wall(), self.roof()
        self.assertEqual(self.rows(w)["Top"]["value"], "Free (its own height)")
        self.run_("model.attach_wall", ids=[w, r], to="roof")
        rows = self.rows(w)
        self.assertIn("Attached to roof", rows["Top"]["value"])
        self.assertEqual(rows["Height"]["type"], "readonly")
        self.assertIn("Lowest / highest", rows)
