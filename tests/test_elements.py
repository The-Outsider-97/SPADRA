"""Building elements against the real SGEO engine: doors and windows cut real openings (checked by volume), floors, roofs and beams
come out with the exact volumes their parameters imply, and everything stays consistent when walls move, change or disappear."""
from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from applications.SPADRA import elements as E
from applications.SPADRA.project import NS, Project
from applications.tools.errors import BadRequest


class ElementCase(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project.create("Elements")                         # drawing unit mm
        self.l1, self.l2, self.roof_level = (l["id"] for l in self.p.manifest["levels"])
        self.plan = next(v["id"] for v in self.p.manifest["views"] if v["kind"] == "plan")

    def run_(self, op, **params):
        return self.p.execute(op, params)["result"]

    def wall(self, a=(0, 0), b=(6000, 0), **extra):
        return self.run_("model.add_wall", start=list(a), end=list(b), level=self.l1, thickness=250, height=3.2, **extra)["id"]

    def vol(self, oid):
        return self.p.model.volume(oid, unit="m")

    def meta(self, oid):
        return self.p.model.get(oid)["metadata"]

    def bounds(self, oid):
        return self.p.model.world_bounds(oid)

    def kind(self, oid):
        return self.p.model.get(oid)["geometry"]["kind"]


class RoofAccessoryConnections(ElementCase):
    def roof(self):
        return self.run_("model.add_roof", points=[[0, 0], [8000, 0], [8000, 6000], [0, 6000]],
                         level=self.l2, roof="gable", pitch=30, thickness=200)["id"]

    def test_context_commands_create_real_hosted_members_and_follow_roof_edits(self):
        roof = self.roof()
        beam = self.run_("model.add_roof_accessory", roof=roof, kind="ridge_beam")["id"]
        strap = self.run_("model.add_roof_accessory", roof=roof, kind="ridge_strap")["id"]
        edges = self.run_("model.add_roof_accessory", roof=roof, kind="roof_edge", edge="all")["ids"]
        self.assertEqual(len(edges), 4)
        for oid in [beam, strap, *edges]:
            self.assertEqual(E.m(self.meta(oid), "roof_host"), roof)
            self.assertGreater(self.vol(oid), 0.0)
        upper_plan = next(v["id"] for v in self.p.manifest["views"] if v.get("level_id") == self.l2)
        self.assertTrue({beam, strap, *edges}.issubset({row["id"] for row in self.p.plan_overlay(upper_plan)}))
        self.assertNotEqual(E.m(self.meta(beam), "type"), E.m(self.meta(strap), "type"))
        rows = E.properties(self.p, beam)
        self.assertIn("vertical_offset", [r.get("key") for group in rows["groups"] for r in group["rows"]])
        self.assertIn("lateral_offset", [r.get("key") for group in rows["groups"] for r in group["rows"]])
        self.run_("model.set_property", id=beam, key="lateral_offset", value=250)
        self.assertAlmostEqual(E.m(self.meta(beam), "lateral_offset"), 0.25)
        self.run_("model.set_property", id=strap, key="span", value=1000)
        self.assertAlmostEqual(E.m(self.meta(strap), "span"), 1.0)
        with self.assertRaises(BadRequest):
            self.run_("model.set_property", id=beam, key="top", value=5)
        original = (E.m(self.meta(beam), "start"), self.bounds(edges[1]))
        self.run_("model.set_property", id=roof, key="ridge_offset", value=500)
        self.assertNotEqual(original[0], E.m(self.meta(beam), "start"))
        self.run_("model.set_property", id=roof, key="pitch", value=40)
        self.assertNotEqual(original[1], self.bounds(edges[1]))
        with self.assertRaises(BadRequest):
            self.run_("model.add_roof_accessory", roof=roof, kind="ridge_beam")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "accessory.spadra"
            self.p.save(path)
            reopened = Project.open(path)
            self.assertGreater(reopened.model.volume(beam, unit="m"), 0.0)
            self.assertEqual(E.m(reopened.model.get(strap)["metadata"], "roof_host"), roof)
        self.run_("model.delete", id=roof)
        self.assertFalse(any(E.alive(self.p, oid) for oid in [beam, strap, *edges]))

    def test_gable_end_roof_edges_have_one_watertight_ridge_mitre_and_flush_corner_seams(self):
        """A gable tip cannot be two overlapping square-ended beams; the neighbouring roof edges share mitred section corners."""
        roof = self.roof()
        ids = self.run_("model.add_roof_accessory", roof=roof, kind="roof_edge", edge="all")["ids"]
        scene = {o["id"]: o for o in self.p.scene_data()["objects"]}
        meshes = [scene[oid] for oid in ids]                               # use world coordinates: SGEO stores local vertices + transforms
        from collections import Counter
        for index in (1, 3):
            mesh = meshes[index]
            self.assertEqual(len(mesh["vertices"]), 12)                   # one continuous strip through the ridge, with no doubled end caps
            incidence = Counter(tuple(sorted((face[i], face[(i + 1) % 3]))) for face in mesh["faces"] for i in range(3))
            self.assertTrue(incidence)
            self.assertEqual(set(incidence.values()), {2})                # closed manifold, including its miter at the gable peak
        for index in range(4):
            outgoing = {tuple(round(c, 6) for c in v) for v in meshes[index]["vertices"][-4:]}
            incoming = {tuple(round(c, 6) for c in v) for v in meshes[(index + 1) % 4]["vertices"][:4]}
            self.assertEqual(outgoing, incoming)                          # same four world-space seam points, no overlap or corner gap

    def test_roof_end_profile_remains_vertical_on_both_pitched_segments(self):
        """The gable end trim follows the roof shape; section depth is vertical like roof thickness."""
        roof = self.roof()
        ids = self.run_("model.add_roof_accessory", roof=roof, kind="roof_edge", edge="all")["ids"]
        scene = {o["id"]: o for o in self.p.scene_data()["objects"]}
        # Short gable-end edges cross the ridge (three profile stations).
        for oid in (ids[1], ids[3]):
            vertices = scene[oid]["vertices"]
            self.assertEqual(len(vertices), 12)
            self.assertNotAlmostEqual(vertices[0][2], vertices[4][2], places=4)
            self.assertNotAlmostEqual(vertices[4][2], vertices[8][2], places=4)
            for k in range(0, len(vertices), 4):
                for top, below in ((0, 3), (1, 2)):
                    self.assertAlmostEqual(vertices[k + top][0], vertices[k + below][0], places=8)
                    self.assertAlmostEqual(vertices[k + top][1], vertices[k + below][1], places=8)
                    self.assertAlmostEqual(abs(vertices[k + top][2] - vertices[k + below][2]), 0.18, places=7)

    def test_flat_shed_and_hip_roof_edges_share_their_corner_mitre_without_overlapping_end_caps(self):
        from collections import Counter

        for kind in ("flat", "shed", "hip"):
            with self.subTest(roof=kind):
                roof = self.run_("model.add_roof", points=[[15000, 0], [23000, 0], [23000, 6000], [15000, 6000]],
                                 level=self.l2, roof=kind, pitch=30, thickness=200)["id"]
                ids = self.run_("model.add_roof_accessory", roof=roof, kind="roof_edge", edge="all")["ids"]
                scene = {o["id"]: o for o in self.p.scene_data()["objects"]}
                for index, oid in enumerate(ids):
                    mesh = scene[oid]
                    incidence = Counter(tuple(sorted((face[i], face[(i + 1) % 3]))) for face in mesh["faces"] for i in range(3))
                    self.assertEqual(set(incidence.values()), {2}, f"{kind} edge {index} must be a closed solid")
                    outgoing = {tuple(round(c, 6) for c in v) for v in mesh["vertices"][-4:]}
                    adjacent = {tuple(round(c, 6) for c in v) for v in scene[ids[(index + 1) % 4]]["vertices"][:4]}
                    self.assertEqual(outgoing, adjacent, f"{kind} adjacent edge {index} must meet at one shared mitre")


class Appearance(ElementCase):
    def test_shared_and_instance_colors_are_independent_and_undoable(self):
        a, b = self.wall(), self.wall(a=(0, 2000), b=(6000, 2000))
        self.run_("model.set_property", id=a, key="material_color", value="#164f7c")
        scene = {o["id"]: o for o in self.p.scene_data()["objects"]}
        self.assertEqual(scene[a]["color"], "#164F7C")
        self.assertEqual(scene[b]["color"], "#164F7C")
        self.run_("model.set_property", id=a, key="color", value="#AABBCC")
        scene = {o["id"]: o for o in self.p.scene_data()["objects"]}
        self.assertEqual(scene[a]["color"], "#AABBCC")
        self.assertEqual(scene[b]["color"], "#164F7C")
        self.p.undo()
        self.assertEqual(next(o for o in self.p.scene_data()["objects"] if o["id"] == a)["color"], "#164F7C")
        with self.assertRaises(BadRequest):
            self.run_("model.set_property", id=a, key="color", value="red; drop table")
        self.run_("model.set_property", id=a, key="material_color", value="")
        self.assertEqual(next(o for o in self.p.scene_data()["objects"] if o["id"] == b)["color"], E.MATERIAL_COLORS["Concrete"])

    def test_layer_color_override_tracks_the_layer_faces(self):
        wall = self.wall()
        extra = self.run_("model.add_layer", id=wall, material="Timber", thickness=50)["layer"]
        self.run_("model.set_property", id=wall, key=f"layer:{extra}:color", value="#ABCDEF")
        scene = next(o for o in self.p.scene_data()["objects"] if o["id"] == wall)
        self.assertEqual(len(scene["layer_colors"]), 2)
        self.assertEqual(scene["layer_colors"][-1]["color"], "#ABCDEF")
        self.assertEqual(sum(x["count"] for x in scene["layer_colors"]), len(scene["faces"]))
        self.assertEqual(self.meta(wall)["spadra:layers"][-1]["color"], "#ABCDEF")

    def test_material_colors_survive_native_project_round_trip(self):
        wall = self.wall()
        self.run_("model.set_property", id=wall, key="material_color", value="#123456")
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "colors.spadra"
            self.p.save_as(target)
            reloaded = Project.open(target)
            self.assertEqual(reloaded.manifest["material_colors"]["Concrete"], "#123456")
            obj = next(o for o in reloaded.scene_data()["objects"] if o["id"] == wall)
            self.assertEqual(obj["color"], "#123456")


class Doors(ElementCase):
    def test_a_door_cuts_exactly_its_own_volume_out_of_the_wall(self):
        w = self.wall()
        self.assertEqual(self.kind(w), "box")
        self.assertAlmostEqual(self.vol(w), 6 * 0.25 * 3.2, places=6)
        d = self.run_("model.add_door", wall=w, offset=2.0)["id"]
        self.assertEqual(self.kind(w), "mesh")
        self.assertAlmostEqual(self.vol(w), 6 * 0.25 * 3.2 - 0.9 * 2.1 * 0.25, places=6)
        (x0, y0, z0), (x1, y1, z1) = self.bounds(d)
        self.assertAlmostEqual((x0 + x1) / 2, 2.0, places=6); self.assertAlmostEqual(z0, 0.0, places=6); self.assertAlmostEqual(z1, 2.1, places=6)
        self.assertAlmostEqual(x1 - x0, 0.9, places=6)
        self.assertEqual(self.meta(d)[f"{NS}:host"], w)

    def test_a_point_in_drawing_units_is_projected_onto_the_wall_and_kept_inside_it(self):
        w = self.wall()
        a = self.run_("model.add_door", wall=w, point=[1500, 900])["id"]                 # 0.9 m off the wall's line: projected to x = 1.5 m
        self.assertAlmostEqual(self.meta(a)[f"{NS}:offset"], 1.5, places=9)
        b = self.run_("model.add_window", wall=w, point=[99000, 0])["id"]                # far past the end: pulled back so it fits
        self.assertAlmostEqual(self.meta(b)[f"{NS}:offset"], 6.0 - 0.6, places=9)

    def test_moving_or_stretching_the_wall_carries_the_door_with_it(self):
        w = self.wall()
        d = self.run_("model.add_door", wall=w, offset=2.0)["id"]
        before = self.bounds(d)
        self.run_("model.move", id=w, dx=1.0, dy=2.0)
        after = self.bounds(d)
        self.assertAlmostEqual(after[0][0] - before[0][0], 1.0, places=6); self.assertAlmostEqual(after[0][1] - before[0][1], 2.0, places=6)
        self.run_("model.set_property", id=w, key="base_offset", value=500)
        self.assertAlmostEqual(self.bounds(d)[0][2], 0.5, places=6)

    def test_a_wall_in_a_diagonal_keeps_the_door_in_its_opening(self):
        w = self.wall(b=(3000, 4000))                                                    # 5 m long at atan(4/3)
        d = self.run_("model.add_door", wall=w, offset=2.5)["id"]
        (x0, y0, _), (x1, y1, _) = self.bounds(d)
        self.assertAlmostEqual((x0 + x1) / 2, 1.5, places=6); self.assertAlmostEqual((y0 + y1) / 2, 2.0, places=6)
        self.assertAlmostEqual(self.vol(w), 5 * 0.25 * 3.2 - 0.9 * 2.1 * 0.25, places=6)

    def test_shortening_a_wall_past_its_door_is_refused_and_changes_nothing(self):
        w = self.wall()
        self.run_("model.add_door", wall=w, offset=5.0)
        rev, v = self.p.revision, self.vol(w)
        with self.assertRaises(BadRequest) as ctx:
            self.run_("model.set_property", id=w, key="length", value=3.0)
        self.assertIn("does not fit", str(ctx.exception))
        self.assertEqual(self.p.revision, rev); self.assertAlmostEqual(self.vol(w), v, places=9)
        self.assertEqual(self.meta(w)[f"{NS}:end"], [6.0, 0.0])

    def test_overlapping_openings_and_oversized_ones_are_refused(self):
        w = self.wall()
        self.run_("model.add_door", wall=w, offset=2.0)
        with self.assertRaises(BadRequest):
            self.run_("model.add_door", wall=w, offset=2.5)
        with self.assertRaises(BadRequest):
            self.run_("model.add_door", wall=w, offset=4.0, height=3500)                  # taller than the 3.2 m wall
        with self.assertRaises(BadRequest):
            self.run_("model.add_door", wall=self.wall(b=(500, 0)), offset=0.2)           # 0.5 m wall, 0.9 m door
        with self.assertRaises(BadRequest):
            self.run_("model.add_door", wall=w)                                          # no position given
        self.assertEqual(len(self.p.model.query_ids(limit=100)), 3)                        # the first wall and door, and the short wall; no failed door left behind


    def test_deleting_the_door_closes_the_opening_and_deleting_the_wall_takes_its_doors(self):
        w = self.wall()
        d = self.run_("model.add_door", wall=w, offset=2.0)["id"]
        self.run_("model.delete", id=d)
        self.assertEqual(self.kind(w), "box"); self.assertAlmostEqual(self.vol(w), 4.8, places=6)
        d2 = self.run_("model.add_window", wall=w, offset=3.0)["id"]
        self.run_("model.delete", id=w)
        self.assertEqual(self.p.model.query_ids(limit=100), [])
        self.p.undo()
        self.assertEqual(len(self.p.model.query_ids(limit=100)), 2)
        self.assertAlmostEqual(self.vol(w), 6 * 0.25 * 3.2 - 1.2 * 1.2 * 0.25, places=6)
        self.assertEqual(self.meta(d2)[f"{NS}:host"], w)

    def test_editing_a_door_rebuilds_the_wall_and_a_bad_edit_leaves_it_alone(self):
        w = self.wall()
        d = self.run_("model.add_door", wall=w, offset=2.0)["id"]
        self.run_("model.set_property", id=d, key="width", value=1200)
        self.assertAlmostEqual(self.vol(w), 4.8 - 1.2 * 2.1 * 0.25, places=6)
        with self.assertRaises(BadRequest):
            self.run_("model.set_property", id=d, key="width", value=9000)
        self.assertAlmostEqual(self.vol(w), 4.8 - 1.2 * 2.1 * 0.25, places=6)
        self.run_("model.set_property", id=d, key="sill", value=100)                       # a door can be raised (a threshold, a raised floor) like a window
        self.assertAlmostEqual(self.meta(d)[f"{NS}:sill"], 0.1)
        self.assertAlmostEqual(self.meta(d)[f"{NS}:height"], 2.1)                          # raising it does not change its size
        with self.assertRaises(BadRequest):                                                # but its head may not pass the top of the wall (3.2 m)
            self.run_("model.set_property", id=d, key="sill", value=1200)
        self.assertAlmostEqual(self.meta(d)[f"{NS}:sill"], 0.1)
        self.run_("model.set_property", id=d, key="hinge", value="end")
        self.assertEqual(self.meta(d)[f"{NS}:hinge"], "end")

    def test_moving_the_door_slides_it_along_the_wall_only(self):
        w = self.wall()
        d = self.run_("model.add_door", wall=w, offset=2.0)["id"]
        self.run_("model.move", id=d, dx=1.0, dy=5.0)                                     # the sideways part is ignored
        self.assertAlmostEqual(self.meta(d)[f"{NS}:offset"], 3.0, places=9)
        with self.assertRaises(BadRequest):
            self.run_("model.move", id=d, dx=10.0)
        self.assertAlmostEqual(self.meta(d)[f"{NS}:offset"], 3.0, places=9)

    def test_adding_and_undoing_a_door_restores_a_plain_box(self):
        w = self.wall()
        self.run_("model.add_door", wall=w, offset=2.0)
        self.p.undo()
        self.assertEqual(self.kind(w), "box"); self.assertEqual(self.p.model.query_ids(limit=100), [w])


class Windows(ElementCase):
    def test_a_window_has_a_sill_and_leaves_wall_above_and_below(self):
        w = self.wall()
        win = self.run_("model.add_window", wall=w, offset=3.0)["id"]
        self.assertAlmostEqual(self.vol(w), 4.8 - 1.2 * 1.2 * 0.25, places=6)
        (_, _, z0), (_, _, z1) = self.bounds(win)
        self.assertAlmostEqual(z0, 0.9, places=6); self.assertAlmostEqual(z1, 2.1, places=6)
        self.run_("model.set_property", id=win, key="sill", value=1500)
        self.assertAlmostEqual(self.bounds(win)[0][2], 1.5, places=6)
        self.run_("model.move", id=win, dz=0.2)
        self.assertAlmostEqual(self.meta(win)[f"{NS}:sill"], 1.7, places=9)

    def test_a_door_and_a_window_side_by_side_both_cut(self):
        w = self.wall()
        self.run_("model.add_door", wall=w, offset=1.5); self.run_("model.add_window", wall=w, offset=4.0)
        self.assertAlmostEqual(self.vol(w), 4.8 - (0.9 * 2.1 + 1.2 * 1.2) * 0.25, places=6)
        self.assertEqual(self.p.check()["issues"], [])

    def test_wall_levels_move_openings_with_them(self):
        w = self.wall()
        d = self.run_("model.add_door", wall=w, offset=2.0)["id"]
        self.run_("model.set_property", id=w, key="level", value=self.l2)
        self.assertAlmostEqual(self.bounds(d)[0][2], 3.2, places=6)
        self.assertEqual(self.meta(d)[f"{NS}:level"], self.l2)


class Floors(ElementCase):
    L = [[0, 0], [4000, 0], [4000, 2000], [2000, 2000], [2000, 4000], [0, 4000]]

    def test_an_l_shaped_slab_has_the_exact_area_thickness_and_level(self):
        f = self.run_("model.add_floor", points=self.L, level=self.l2, thickness=200)["id"]
        self.assertAlmostEqual(self.vol(f), 12 * 0.2, places=6)
        (x0, y0, z0), (x1, y1, z1) = self.bounds(f)
        self.assertAlmostEqual(z1, 3.2, places=6); self.assertAlmostEqual(z0, 3.0, places=6)
        self.assertEqual((x0, y0, x1, y1), (0, 0, 4, 4))
        self.assertEqual(self.p.check()["issues"], [])

    def test_clockwise_outlines_are_accepted_and_bad_ones_refused(self):
        f = self.run_("model.add_floor", points=list(reversed(self.L)), level=self.l1)["id"]
        self.assertAlmostEqual(self.vol(f), 12 * 0.2, places=6)
        for pts in ([[0, 0], [1000, 0]], [[0, 0], [1000, 1000], [1000, 0], [0, 1000]], [[0, 0], [1000, 0], [2000, 0]], [[0, 0], [1, 0], [1, "x"]]):
            with self.assertRaises(BadRequest):
                self.run_("model.add_floor", points=pts, level=self.l1)

    def test_edit_move_and_resize(self):
        f = self.run_("model.add_floor", points=self.L, level=self.l1)["id"]
        self.run_("model.set_property", id=f, key="thickness", value=300)
        self.assertAlmostEqual(self.vol(f), 12 * 0.3, places=6)
        self.run_("model.move", id=f, dx=1.0, dy=1.0, dz=0.5)
        (x0, y0, z0), (_, _, z1) = self.bounds(f)
        self.assertEqual((x0, y0), (1.0, 1.0)); self.assertAlmostEqual(z1, 0.5, places=6)

    def test_survives_save_and_reopen(self):
        f = self.run_("model.add_floor", points=self.L, level=self.l1)["id"]
        w = self.wall(); d = self.run_("model.add_door", wall=w, offset=2.0)["id"]
        with tempfile.TemporaryDirectory() as tmp:
            self.p.save(Path(tmp) / "x.spadra")
            q = Project.open(Path(tmp) / "x.spadra")
            self.assertAlmostEqual(q.model.volume(f, unit="m"), 12 * 0.2, places=6)
            self.assertAlmostEqual(q.model.volume(w, unit="m"), 4.8 - 0.9 * 2.1 * 0.25, places=6)
            self.assertEqual(q.model.get(d)["metadata"][f"{NS}:host"], w)
            self.assertEqual(q.check()["issues"], [])


class Roofs(ElementCase):
    RECT = [[0, 0], [10000, 0], [10000, 6000], [0, 6000]]

    def test_roof_plan_contains_editable_outline_and_ridge_after_boundary_change(self):
        roof = self.run_("model.add_roof", points=self.RECT, level=self.roof_level, roof="gable", pitch=30)["id"]
        view = next(v["id"] for v in self.p.manifest["views"] if v.get("level_id") == self.roof_level)
        plan = self.p.drawing_data(view)
        item = next(row for row in plan["model"] if row["id"] == roof)
        self.assertEqual(item["category"], "roof")
        self.assertEqual(item["outline"], self.RECT)
        self.assertEqual(item["ridge"], [[0, 3000], [10000, 3000]])
        self.assertEqual(len(item["polygon"]), 4)

        # The same command used by an on-canvas roof corner/edge grip changes
        # the actual SGEO roof, not just its plan outline or drawing overlay.
        modified = [[0, 0], [11000, 0], [11000, 6000], [0, 6000]]
        self.run_("model.set_boundary", id=roof, points=modified)
        changed = next(row for row in self.p.drawing_data(view)["model"] if row["id"] == roof)
        self.assertEqual(changed["outline"], modified)
        self.assertEqual(changed["ridge"], [[0, 3000], [11000, 3000]])
        self.assertAlmostEqual(self.vol(roof), 11 * 6 * 0.2)
        self.p.undo()
        self.assertEqual(next(row for row in self.p.drawing_data(view)["model"] if row["id"] == roof)["outline"], self.RECT)

    def test_flat_roof_over_any_outline(self):
        r = self.run_("model.add_roof", points=Floors.L, level=self.roof_level, roof="flat", thickness=150)["id"]
        self.assertAlmostEqual(self.vol(r), 12 * 0.15, places=6)
        self.assertAlmostEqual(self.bounds(r)[0][2], 6.4, places=6)

    def test_gable_roof_has_the_ridge_height_and_volume_its_pitch_implies(self):
        r = self.run_("model.add_roof", points=self.RECT, level=self.roof_level, roof="gable", thickness=200, pitch=30)["id"]
        self.assertAlmostEqual(self.vol(r), 6 * 0.2 * 10, places=6)                       # width x vertical thickness x length
        (x0, y0, z0), (x1, y1, z1) = self.bounds(r)
        self.assertAlmostEqual(z1 - 6.4, 3 * math.tan(math.radians(30)), places=6)        # ridge: half the 6 m width x tan(pitch)
        self.assertAlmostEqual(z0 - 6.4, -0.2, places=6)
        self.assertEqual((x0, y0, x1, y1), (0, 0, 10, 6))

    def test_shed_roof_rises_across_the_short_side(self):
        r = self.run_("model.add_roof", points=self.RECT, level=self.roof_level, roof="shed", thickness=200, pitch=10)["id"]
        self.assertAlmostEqual(self.vol(r), 6 * 0.2 * 10, places=6)
        self.assertAlmostEqual(self.bounds(r)[1][2] - 6.4, 6 * math.tan(math.radians(10)), places=6)

    def test_a_turned_rectangle_gives_the_same_roof(self):
        c, s = math.cos(math.radians(35)), math.sin(math.radians(35))
        pts = [[x * c - y * s + 5000, x * s + y * c + 5000] for x, y in self.RECT]
        r = self.run_("model.add_roof", points=pts, level=self.roof_level, roof="gable", pitch=30)["id"]
        self.assertAlmostEqual(self.vol(r), 6 * 0.2 * 10, places=5)
        self.assertAlmostEqual(self.bounds(r)[1][2] - 6.4, 3 * math.tan(math.radians(30)), places=6)

    def test_pitched_roofs_need_a_rectangle_and_a_sane_pitch(self):
        for kwargs in ({"points": Floors.L, "roof": "gable"}, {"points": self.RECT, "roof": "gable", "pitch": 0}, {"points": self.RECT, "roof": "gable", "pitch": 85},
                       {"points": self.RECT, "roof": "dome"}, {"points": [[0, 0], [10000, 0], [10000, 6000], [500, 6000]], "roof": "shed"}):
            with self.assertRaises(BadRequest):
                self.run_("model.add_roof", level=self.roof_level, **kwargs)

    def test_hip_roof_has_four_slopes_a_short_ridge_and_the_volume_its_pitch_implies(self):
        r = self.run_("model.add_roof", points=self.RECT, level=self.roof_level, roof="hip", thickness=200, pitch=30)["id"]
        self.assertAlmostEqual(self.vol(r), 10 * 6 * 0.2, places=6)                       # plan area x vertical thickness, as for any roof of that thickness
        (x0, y0, z0), (x1, y1, z1) = self.bounds(r)
        self.assertAlmostEqual(z1 - 6.4, 3 * math.tan(math.radians(30)), places=6)
        self.assertEqual((x0, y0, x1, y1), (0, 0, 10, 6))
        square = self.run_("model.add_roof", points=[[0, 0], [6000, 0], [6000, 6000], [0, 6000]], level=self.roof_level, roof="hip", thickness=200, pitch=45)["id"]
        self.assertAlmostEqual(self.vol(square), 36 * 0.2, places=6)                      # a square outline gives a pyramid
        self.assertAlmostEqual(self.bounds(square)[1][2] - 6.4, 3.0, places=6)

    def test_every_roof_type_can_be_switched_to_another_and_the_slope_edited_as_pitch_or_percentage(self):
        r = self.run_("model.add_roof", points=self.RECT, level=self.roof_level, roof="gable", pitch=30)["id"]
        for kind, rise in (("shed", 6 * math.tan(math.radians(30))), ("hip", 3 * math.tan(math.radians(30))), ("gable", 3 * math.tan(math.radians(30))), ("flat", 0.0)):
            self.run_("model.set_property", id=r, key="roof", value=kind)
            self.assertEqual(self.meta(r)[f"{NS}:roof"], kind)
            self.assertAlmostEqual(self.vol(r), 12.0, places=6)
            self.assertAlmostEqual(self.bounds(r)[1][2] - 6.4, rise if kind != "flat" else 0.2, places=6)            # a flat roof is a slab 200 mm thick on its base
        self.run_("model.set_property", id=r, key="roof", value="hip")
        self.run_("model.set_property", id=r, key="slope", value=50)                       # 50 % = rise 1 over run 2
        self.assertAlmostEqual(self.bounds(r)[1][2] - 6.4, 1.5, places=6)
        self.assertAlmostEqual(self.meta(r)[f"{NS}:pitch"], math.degrees(math.atan(0.5)), places=6)
        for bad in (0.5, 1000):
            with self.assertRaises(BadRequest):
                self.run_("model.set_property", id=r, key="slope", value=bad)
        self.assertAlmostEqual(self.meta(r)[f"{NS}:pitch"], math.degrees(math.atan(0.5)), places=6)

    def test_the_roof_properties_show_type_pitch_slope_and_rise(self):
        r = self.run_("model.add_roof", points=self.RECT, level=self.roof_level, roof="hip", pitch=45)["id"]
        top = {row["key"]: row for row in E.properties(self.p, r)["top"] if "key" in row}
        self.assertEqual((top["roof"]["value"], round(top["slope"]["value"], 6), round(top["pitch"]["value"], 6)), ("hip", 100.0, 45.0))
        self.assertEqual([c["value"] for c in top["roof"]["choices"]], ["flat", "shed", "gable", "hip"])
        flat = self.run_("model.add_roof", points=Floors.L, level=self.roof_level, roof="flat")["id"]
        self.assertNotIn("slope", {row["key"] for row in E.properties(self.p, flat)["top"] if "key" in row})
        with self.assertRaises(BadRequest):                                              # an L-shaped outline cannot take a pitched roof
            self.run_("model.set_property", id=flat, key="roof", value="gable")
        self.assertEqual(self.meta(flat)[f"{NS}:roof"], "flat")

    def test_the_eaves_are_the_edges_that_shed_water(self):
        def eaves(kind):
            r = self.run_("model.add_roof", points=self.RECT, level=self.roof_level, roof=kind, pitch=30)["id"]
            return E.roof_eaves(E.meta_of(self.p, r))
        self.assertEqual((len(eaves("flat")), len(eaves("hip")), len(eaves("gable")), len(eaves("shed"))), (4, 4, 2, 1))
        for a, b in eaves("gable"):
            self.assertAlmostEqual(abs(b[0] - a[0]), 10.0, places=6)                    # the long edges
        (a, b), = eaves("shed")
        self.assertAlmostEqual(abs(b[0] - a[0]), 10.0, places=6)

    def test_changing_the_pitch_rebuilds_the_roof(self):
        r = self.run_("model.add_roof", points=self.RECT, level=self.roof_level, roof="gable", pitch=30)["id"]
        self.run_("model.set_property", id=r, key="pitch", value=45)
        self.assertAlmostEqual(self.bounds(r)[1][2] - 6.4, 3.0, places=6)
        flat = self.run_("model.add_roof", points=self.RECT, level=self.roof_level, roof="flat")["id"]
        with self.assertRaises(BadRequest):
            self.run_("model.set_property", id=flat, key="pitch", value=20)


class Beams(ElementCase):
    def test_a_beam_has_its_section_length_and_top(self):
        b = self.run_("model.add_beam", start=[0, 0], end=[5000, 0], level=self.l2, top=3.0, width=200, depth=400)["id"]
        self.assertAlmostEqual(self.vol(b), 5 * 0.2 * 0.4, places=6)
        (_, _, z0), (_, _, z1) = self.bounds(b)
        self.assertAlmostEqual(z1, 3.2 + 3.0, places=6); self.assertAlmostEqual(z0, 3.2 + 2.6, places=6)

    def test_edit_move_and_validate(self):
        b = self.run_("model.add_beam", start=[0, 0], end=[3000, 4000], level=self.l1)["id"]
        self.assertAlmostEqual(self.bounds(b)[1][0], 3.0 + 0.0 + (0.1 * 4 / 5), places=6)
        self.run_("model.set_property", id=b, key="depth", value=600)
        self.assertAlmostEqual(self.vol(b), 5 * 0.2 * 0.6, places=6)
        self.run_("model.move", id=b, dx=1.0, dz=0.5)
        self.assertEqual(self.meta(b)[f"{NS}:start"][0], 1.0); self.assertAlmostEqual(self.meta(b)[f"{NS}:top"], 3.5, places=9)
        for bad in ({"start": [0, 0], "end": [0, 0]}, {"start": [0, 0], "end": [1000, 0], "material": "Cheese"}, {"start": [0, 0]}):
            with self.assertRaises(BadRequest):
                self.run_("model.add_beam", level=self.l1, **bad)


class PlanAndTree(ElementCase):
    def test_walls_are_cut_around_doors_and_windows_on_the_plan(self):
        w = self.wall()
        d = self.run_("model.add_door", wall=w, offset=2.0)["id"]
        win = self.run_("model.add_window", wall=w, offset=4.5)["id"]
        items = self.p.plan_overlay(self.plan)
        pieces = sorted((min(q[0] for q in i["polygon"]), max(q[0] for q in i["polygon"])) for i in items if i["category"] == "wall")
        self.assertEqual([(round(a), round(b)) for a, b in pieces], [(0, 1550), (2450, 3900), (5100, 6000)])
        door = next(i for i in items if i["id"] == d)
        self.assertEqual((round(min(q[0] for q in door["polygon"])), round(max(q[0] for q in door["polygon"]))), (1550, 2450))
        self.assertTrue(door["cut"]); self.assertEqual(door["symbol"]["kind"], "door")
        self.assertEqual(next(i for i in items if i["id"] == win)["symbol"]["kind"], "window")

    def test_a_high_window_does_not_cut_the_plan(self):
        w = self.wall()
        self.run_("model.add_window", wall=w, offset=3.0, sill=2000, height=800)
        items = self.p.plan_overlay(self.plan)
        self.assertEqual(len([i for i in items if i["category"] == "wall"]), 1)
        self.assertFalse(next(i for i in items if i["category"] == "window")["cut"])

    def test_door_swing_symbol_is_a_true_quarter_circle_on_the_chosen_side(self):
        w = self.wall()
        d = self.run_("model.add_door", wall=w, offset=3.0)["id"]
        s = next(i for i in self.p.plan_overlay(self.plan) if i["id"] == d)["symbol"]
        self.assertEqual([round(v, 6) for v in s["hinge"]], [2550.0, 25.0])             # the hinge pin: on the leaf's swing-side face (leaf 50 mm on the centre line), not the wall face
        self.assertEqual([round(v, 6) for v in s["leaf_end"]], [2550.0, 925.0])
        self.assertAlmostEqual(s["radius"], 900.0)
        self.assertAlmostEqual(s["end_angle"] - s["start_angle"], math.pi / 2, places=9)
        self.run_("model.set_property", id=d, key="swing", value="right")
        self.run_("model.set_property", id=d, key="hinge", value="end")
        s = next(i for i in self.p.plan_overlay(self.plan) if i["id"] == d)["symbol"]
        self.assertEqual([round(v, 6) for v in s["hinge"]], [3450.0, -25.0])
        self.assertEqual([round(v, 6) for v in s["leaf_end"]], [3450.0, -925.0])

    def test_floors_and_beams_appear_on_their_own_level_only(self):
        self.run_("model.add_floor", points=Floors.L, level=self.l1)
        self.run_("model.add_beam", start=[0, 0], end=[4000, 0], level=self.l1)
        self.run_("model.add_floor", points=Floors.L, level=self.l2)
        cats = sorted(i["category"] for i in self.p.plan_overlay(self.plan))
        self.assertEqual(cats, ["floor", "structural"])

    def test_the_browser_tree_lists_each_family(self):
        w = self.wall()
        self.run_("model.add_door", wall=w, offset=2.0); self.run_("model.add_window", wall=w, offset=4.0)
        self.run_("model.add_floor", points=Floors.L, level=self.l1)
        self.run_("model.add_roof", points=Roofs.RECT, level=self.roof_level, roof="gable")
        self.run_("model.add_beam", start=[0, 0], end=[1000, 0], level=self.l1)
        cats = {c["label"]: len(c["children"]) for c in next(s for s in self.p.tree()["sections"] if s["id"] == "categories")["children"]}
        self.assertEqual(cats, {"Walls": 1, "Doors": 1, "Windows": 1, "Roof windows": 0, "Floors": 1, "Roofs": 1, "Gutters": 0, "Downpipes": 0, "Drains": 0, "Flashings": 0, "Stairs": 0, "Structural": 1, "Rooms": 0, "Grids": 0, "Generic Models": 0, "Extrusions": 0, "Voids": 0, "Sweeps": 0, "Foundations": 0, "Curtain walls": 0, "Arrays": 0})

    def test_scene_draws_every_element(self):
        w = self.wall()
        self.run_("model.add_door", wall=w, offset=2.0); self.run_("model.add_floor", points=Floors.L, level=self.l1)
        self.run_("model.add_roof", points=Roofs.RECT, level=self.roof_level, roof="gable"); self.run_("model.add_beam", start=[0, 0], end=[1000, 0], level=self.l1)
        cats = sorted(o["category"] for o in self.p.scene_data()["objects"])
        self.assertEqual(cats, ["door", "floor", "roof", "structural", "wall"])


class PropertiesAndChecks(ElementCase):
    def rows(self, oid):
        props = self.p.properties({"model": oid})
        return {r["key"]: r for r in props["top"] + [r for g in props["groups"] for r in g["rows"]] if "key" in r}, props

    def test_each_family_exposes_its_own_editable_parameters(self):
        w = self.wall()
        d = self.run_("model.add_door", wall=w, offset=2.0)["id"]; win = self.run_("model.add_window", wall=w, offset=4.5)["id"]
        f = self.run_("model.add_floor", points=Floors.L, level=self.l1)["id"]
        r = self.run_("model.add_roof", points=Roofs.RECT, level=self.roof_level, roof="gable")["id"]
        b = self.run_("model.add_beam", start=[0, 0], end=[1000, 0], level=self.l1)["id"]
        self.assertTrue({"width", "height", "material", "offset", "hinge", "swing", "name"} <= set(self.rows(d)[0]))
        self.assertTrue({"width", "height", "sill", "offset", "name"} <= set(self.rows(win)[0]))
        self.assertTrue({"level", "thickness", "offset", "material"} <= set(self.rows(f)[0]))
        self.assertTrue({"pitch", "thickness"} <= set(self.rows(r)[0]))
        self.assertTrue({"width", "depth", "top", "material"} <= set(self.rows(b)[0]))
        self.assertEqual(self.rows(d)[1]["title"], "Door")
        area = next(x for g in self.rows(f)[1]["groups"] for x in g["rows"] if x["label"] == "Plan area")
        self.assertEqual(area["value"], "12.000 m²")

    def test_the_wall_area_excludes_its_openings(self):
        w = self.wall()
        self.run_("model.add_door", wall=w, offset=2.0)
        area = next(x for g in self.p.properties({"model": w})["groups"] for x in g["rows"] if x["label"] == "Area")
        self.assertEqual(area["value"], f"{6 * 3.2 - 0.9 * 2.1:.3f} m²")

    def test_every_row_the_panel_offers_can_be_applied(self):
        w = self.wall()
        ids = [self.run_("model.add_door", wall=w, offset=2.0)["id"], self.run_("model.add_window", wall=w, offset=4.5)["id"],
               self.run_("model.add_floor", points=Floors.L, level=self.l1)["id"], self.run_("model.add_roof", points=Roofs.RECT, level=self.roof_level, roof="gable")["id"],
               self.run_("model.add_beam", start=[0, 0], end=[1000, 0], level=self.l1)["id"], w]
        for oid in ids:
            rows, _ = self.rows(oid)
            for key, row in rows.items():
                if row["type"] in ("readonly",):
                    continue
                self.run_("model.set_property", id=oid, key=key, value=row["value"])              # setting each field to what it already shows must work
        self.assertEqual(self.p.check()["issues"], [])

    def test_check_reports_an_orphaned_door(self):
        w = self.wall()
        d = self.run_("model.add_door", wall=w, offset=2.0)["id"]
        self.p.model.attach_semantics(d, {"host": "gone"}, namespace=NS)
        self.assertTrue(any("lost its wall" in i["message"] for i in self.p.check()["issues"]))


if __name__ == "__main__":
    unittest.main()
