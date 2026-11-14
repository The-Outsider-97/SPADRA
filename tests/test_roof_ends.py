"""Roof end connections: the 14 details A1 to D3 as parameters of a cut across an eave, how they sit on the wall that carries them, and what follows."""
from __future__ import annotations

import math
import tempfile
from pathlib import Path

from applications.SPADRA import elements as E
from applications.tools.errors import BadRequest

from .test_bim import Case

NS = "spadra"
RECT = [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]
T, PITCH = 0.2, 40


class RoofEnds(Case):
    def setUp(self) -> None:
        super().setUp()
        self.top = float(self.p.level(self.roof_level)["elevation"])                  # the roof's top surface at its outline
        wall_top = self.top - T                                                       # a wall that ends where the roof's underside is at the outline
        base = float(self.p.level(self.l2)["elevation"])
        # the south eave is edge 0 (y = 0); the wall's faces are at y = 0 (far, outside) and y = 0.3 (near, inside)
        self.wall = self.run_("model.add_wall", start=[0, 150], end=[6000, 150], level=self.l2, thickness=300, height=wall_top - base)["id"]
        self.roof = self.run_("model.add_roof", points=RECT, level=self.roof_level, roof="gable", pitch=PITCH, thickness=T * 1000)["id"]

    def cut(self, edge=0):
        return E.roof_end_cuts(self.p, self.meta(self.roof))[edge]

    def end(self, edge=0, **params):
        return self.run_("model.set_roof_end", id=self.roof, edge=edge, **params)

    def y_range(self):
        lo, hi = self.p.model.bounds(self.roof)
        return lo[1], hi[1]

    # ---------------------------------------------------------------- the 14 presets
    def test_every_preset_is_applied_as_the_cut_it_describes(self):
        tan = math.tan(math.radians(PITCH))
        for kind, (title, cut, ref, anchor, bearing, heel) in E.ROOF_END_TYPES.items():
            with self.subTest(kind):
                self.end(type=kind)
                c = self.cut()
                (qu, zu), (qt, zt) = c["under"], c["top"]
                self.assertEqual((c["cfg"]["cut"], c["cfg"]["ref"], c["cfg"]["anchor"]), (cut, ref, anchor))
                if cut == "plumb":
                    self.assertAlmostEqual(qu, qt, places=9)
                elif cut == "horizontal":
                    self.assertAlmostEqual(zu, zt, places=9)
                    self.assertGreater(qt, qu)                                          # the bar runs outward along the seat
                else:                                                                   # square: perpendicular to the slope
                    self.assertAlmostEqual((qt - qu) * 1 + (zt - zu) * (-tan), 0.0, places=9)
                lam = E.ROOF_ANCHORS[anchor]
                self.assertAlmostEqual(c["anchor"][0], c["wall_q"][ref], places=9)      # the chosen point of the cut stands over the chosen wall line
                self.assertAlmostEqual(qu + lam * (qt - qu), c["wall_q"][ref], places=9)
                self.assertGreater(self.vol(self.roof), 0.0)

    def test_the_wall_lines_are_where_the_reference_sheet_puts_them(self):
        c = self.end(type="B1") and self.cut()
        self.assertEqual({k: round(v, 6) for k, v in c["wall_q"].items()}, {"near": -0.3, "centre": -0.15, "far": 0.0})
        self.assertEqual(c["wall"]["id"], self.wall)

    def test_a_plumb_cut_moves_the_end_of_the_slab_in_plan(self):
        before = self.y_range()[0]
        self.end(type="B1")                                                           # at the near face, 300 mm inside the outline
        self.assertAlmostEqual(self.y_range()[0], before + 0.3, places=6)
        self.end(type="B2")
        self.assertAlmostEqual(self.y_range()[0], before + 0.15, places=6)
        self.end(type="B3")
        self.assertAlmostEqual(self.y_range()[0], before, places=6)
        self.end(type="B3", offset=100)                                               # 100 mm outward of the far face
        self.assertAlmostEqual(self.y_range()[0], before - 0.1, places=6)

    def test_a_square_cut_leaves_a_face_perpendicular_to_the_slope(self):
        self.end(type="A3")
        c = self.cut()
        self.assertAlmostEqual(c["top"][0] - c["under"][0], T * math.cos(math.radians(PITCH)) * math.sin(math.radians(PITCH)), places=9)
        self.assertAlmostEqual(c["under"][0], 0.0, places=9)                           # lower corner on the far face

    def test_a_horizontal_cut_leaves_a_flat_seat_of_the_slab_thickness_over_tan_pitch(self):
        self.end(type="C1")                                                            # begins at the far face and runs outward
        c = self.cut()
        bar = T / math.tan(math.radians(PITCH))
        self.assertAlmostEqual(c["top"][0] - c["under"][0], bar, places=9)
        self.assertAlmostEqual(c["under"][0], 0.0, places=9)
        self.assertAlmostEqual(c["projection"], bar, places=9)
        self.end(type="C4")                                                            # ends flush with the far face
        self.assertAlmostEqual(self.cut()["top"][0], 0.0, places=9)
        self.assertEqual(self.cut()["projection"], 0.0)

    # ---------------------------------------------------------------- bearing, heel, embedding, seating
    def test_seating_puts_the_roof_corner_on_the_wall_top_and_reports_what_moved(self):
        self.end(type="B1")
        gap = self.cut()["gap"]
        out = self.end(type="B1", seat=True)
        self.assertAlmostEqual(out["roof_moved"], -gap, places=9)
        self.assertAlmostEqual(self.cut()["gap"], 0.0, places=9)
        self.assertEqual([n["code"] for n in self.cut()["notes"]], ["bearing"])

    def test_the_raised_heel_stands_at_its_height_and_is_adjustable(self):
        self.end(type="D1", seat=True)
        self.assertAlmostEqual(self.cut()["gap"], 0.1, places=9)
        self.assertIn("raised_heel", [n["code"] for n in self.cut()["notes"]])
        self.end(heel=250, seat=True)
        self.assertAlmostEqual(self.cut()["gap"], 0.25, places=9)
        self.assertEqual(self.cut()["cfg"]["type"], "D1")                              # a heel is a parameter of D1, not a different detail

    def test_an_embedded_end_overlaps_the_wall_and_says_so(self):
        self.end(type="D3", seat=True)
        c = self.cut()
        self.assertAlmostEqual(c["gap"], -0.1, places=9)
        self.assertIn("embedded", [n["code"] for n in c["notes"]])
        self.assertAlmostEqual(c["under"][0], 0.0, places=9)                           # plumb at the far face

    def test_an_abutting_end_aligns_the_roof_upper_corner_with_the_wall_top(self):
        self.end(type="D2")
        self.assertIn("wall_below_roof", [n["code"] for n in self.cut()["notes"]])      # this wall stops at the roof's underside: it does not reach the upper corner
        out = self.end(type="D2", seat=True)                                              # the roof is lowered until its upper corner is at the wall top
        c = self.cut()
        self.assertAlmostEqual(c["base"] + c["top"][1], c["wall"]["top"], places=9)
        self.assertIn("abutment", [n["code"] for n in c["notes"]])
        self.assertLess(out["roof_moved"], 0)
        self.run_("model.set_property", id=self.wall, key="height", value=float(self.meta(self.wall)[f"{NS}:height"]) + 1.5)
        self.assertIn("wall_above_roof", [n["code"] for n in self.cut()["notes"]])

    def test_an_unseated_roof_is_a_warning_with_the_distance(self):
        self.end(type="B1")                                                            # its lower corner stands 250 mm above the wall top
        issues = [i for i in E.check(self.p) if i["category"] == "connection"]
        self.assertTrue(any(i["severity"] == "warning" and "seat the roof" in i["message"] for i in issues))
        self.end(type="B1", seat=True)
        self.assertFalse([i for i in E.check(self.p) if i["category"] == "connection" and i["severity"] == "warning"])

    def test_a_roof_without_a_wall_is_positioned_from_its_outline_and_says_so(self):
        self.run_("model.delete", id=self.wall)
        self.end(type="B2")
        c = self.cut()
        self.assertIsNone(c["wall"])
        self.assertEqual(c["wall_q"], {"near": 0.0, "centre": 0.0, "far": 0.0})
        self.assertEqual([n["code"] for n in c["notes"]], ["no_wall"])

    # ---------------------------------------------------------------- what follows
    def test_the_connection_follows_its_wall(self):
        self.end(type="B3")
        self.run_("model.set_property", id=self.wall, key="thickness", value=500)       # the far face moves 100 mm outward (reference line: centre)
        self.assertAlmostEqual(self.cut()["under"][0], 0.1, places=6)
        self.assertAlmostEqual(self.y_range()[0], -0.1, places=6)
        self.run_("model.move", id=self.wall, dx=0, dy=0.2, dz=0)             # metres
        self.assertAlmostEqual(self.cut()["under"][0], -0.1, places=6)

    def test_the_other_eave_is_not_touched(self):
        lo, hi = self.y_range()
        self.end(type="B1")
        self.assertAlmostEqual(self.y_range()[1], hi, places=6)
        self.assertIsNone(E.roof_end_config(self.meta(self.roof), 2))

    def test_the_fascia_follows_the_cut(self):
        edge = self.run_("model.add_roof_accessory", roof=self.roof, kind="roof_edge", edge=1)["ids"][0]
        before = self.p.model.bounds(edge)
        self.end(type="B1")
        after = self.p.model.bounds(edge)
        self.assertAlmostEqual(after[0][1] - before[0][1], 0.3, places=6)               # it hangs where the roof now ends

    def test_the_roof_edge_stays_parallel_to_its_roof_end_in_plan_and_in_3d_whatever_the_orientation(self):
        """The edge member's plan line is the roof end itself (not the outline it was cut back from) and its solid lies along that line, for any roof turn and any cut."""
        from applications.SPADRA.stairs import _world_triangles

        def check(label, oid, roof):
            eb = E.effective_boundary(self.p, self.meta(roof))
            edge = int(E.m(self.meta(oid), "edge"))
            a, b = eb[edge], eb[(edge + 1) % len(eb)]
            start, end = E.m(self.meta(oid), "start"), E.m(self.meta(oid), "end")
            ex, ey = b[0] - a[0], b[1] - a[1]
            length = math.hypot(ex, ey)
            self.assertAlmostEqual(abs((end[0] - start[0]) * ey - (end[1] - start[1]) * ex) / length, 0.0, places=7, msg=f"{label}: plan line parallel to the roof end")
            self.assertAlmostEqual(((start[0] - a[0]) * ey - (start[1] - a[1]) * ex) / length, 0.0, places=7, msg=f"{label}: plan line lies on the roof end")
            vs = [v for t in _world_triangles(self.p, oid) for v in t]
            mx, my = sum(v[0] for v in vs) / len(vs), sum(v[1] for v in vs) / len(vs)
            sxx, syy = sum((v[0] - mx) ** 2 for v in vs), sum((v[1] - my) ** 2 for v in vs)
            sxy = sum((v[0] - mx) * (v[1] - my) for v in vs)
            principal = 0.5 * math.atan2(2 * sxy, sxx - syy)
            turn = abs(((principal - math.atan2(ey, ex) + math.pi / 2) % math.pi) - math.pi / 2)
            self.assertLess(math.degrees(turn), 1e-3, f"{label}: the solid runs along the roof end")

        previous = self.roof
        for degrees in (0, 30, 135, 250):
            with self.subTest(turn=degrees):
                self.p.execute("model.delete", {"id": previous})
                a = math.radians(degrees)
                pts = [[3000 + math.cos(a) * (x - 3000) - math.sin(a) * (y - 2000), 2000 + math.sin(a) * (x - 3000) + math.cos(a) * (y - 2000)] for x, y in RECT]
                roof = self.run_("model.add_roof", points=pts, level=self.roof_level, roof="gable", pitch=PITCH, thickness=T * 1000)["id"]
                previous = roof
                ids = self.run_("model.add_roof_accessory", roof=roof, kind="roof_edge", edge="all")["ids"]
                for oid in ids:
                    check(f"{degrees} plain", oid, roof)
                for kind in ("A1", "B2", "C3", "D1"):
                    for edge in (0, 2):
                        self.run_("model.set_roof_end", id=roof, edge=edge, type=kind, offset=120)
                    for oid in ids:
                        check(f"{degrees} {kind}", oid, roof)
                self.run_("model.rotate", ids=[roof], centre=[3000, 2000], angle=25)
                for oid in ids:
                    check(f"{degrees} then turned", oid, roof)
                self.run_("model.clear_roof_end", id=roof, edge=0)
                for oid in ids:
                    check(f"{degrees} end cleared", oid, roof)

    def test_layers_are_cut_by_the_same_plane_or_each_by_its_own(self):
        self.run_("model.add_layer", id=self.roof, material="Mineral wool", thickness=100)
        self.end(type="A1", layers="through")
        through = self.vol(self.roof), self.y_range()
        self.end(layers="own")
        own = self.vol(self.roof), self.y_range()
        self.assertGreater(through[0], 0)
        self.assertGreater(own[0], 0)
        self.assertNotAlmostEqual(through[0], own[0], places=6)                         # a layer cut square to the slope by itself ends differently

    # ---------------------------------------------------------------- limits
    def test_a_cut_that_would_leave_nothing_of_the_slope_is_refused_and_changes_nothing(self):
        before = self.vol(self.roof)
        with self.assertRaises(BadRequest):
            self.end(type="B3", offset=-2500)
        self.assertIsNone(E.roof_end_config(self.meta(self.roof), 0))
        self.assertAlmostEqual(self.vol(self.roof), before, places=9)

    def test_flat_and_hip_roofs_and_non_eave_edges_are_refused(self):
        flat = self.run_("model.add_roof", points=RECT, level=self.roof_level, roof="flat")["id"]
        with self.assertRaises(BadRequest):
            self.run_("model.set_roof_end", id=flat, edge=0, type="B1")
        with self.assertRaises(BadRequest):
            self.end(edge=1, type="B1")                                                 # a gable end is not an eave
        with self.assertRaises(BadRequest):
            self.end(type="Z9")

    def test_a_roof_changed_to_flat_keeps_its_ends_but_warns_they_are_ignored(self):
        self.end(type="B1")
        self.run_("model.set_property", id=self.roof, key="roof", value="flat")
        self.assertTrue(any("ignored" in i["message"] for i in E.check(self.p) if i["category"] == "connection"))

    # ---------------------------------------------------------------- UI surfaces, preview, persistence
    def test_preview_changes_nothing_and_describes_the_cut(self):
        before = self.vol(self.roof)
        out = self.p.preview_roof_end({"id": self.roof, "edge": 0, "type": "C2"})
        self.assertEqual(self.vol(self.roof), before)
        self.assertIsNone(E.roof_end_config(self.meta(self.roof), 0))
        self.assertEqual(out["end"]["cut"], "horizontal")
        self.assertEqual(len(out["slab"]), 4)
        self.assertEqual(out["wall"]["near"], -0.3)
        self.assertIn("seat_mm", out)

    def test_properties_edit_every_parameter_and_default_clears(self):
        self.run_("model.set_property", id=self.roof, key="end:0:type", value="B1")
        rows = self.rows(self.roof)
        for key in ("cut", "ref", "anchor", "offset", "heel", "layers"):
            self.assertIn(f"end:0:{key}", rows)
        self.run_("model.set_property", id=self.roof, key="end:0:cut", value="horizontal")
        self.assertEqual(self.cut()["cfg"]["type"], "custom")                           # edited away from B1
        self.run_("model.set_property", id=self.roof, key="end:0:type", value="default")
        self.assertIsNone(E.roof_end_config(self.meta(self.roof), 0))

    def test_undo_redo_and_save_reload(self):
        original = self.vol(self.roof)
        self.end(type="C3", seat=True)
        changed = self.vol(self.roof)
        self.assertNotAlmostEqual(changed, original, places=6)
        self.p.undo()
        self.assertAlmostEqual(self.vol(self.roof), original, places=9)
        self.p.redo()
        self.assertAlmostEqual(self.vol(self.roof), changed, places=9)
        with tempfile.TemporaryDirectory() as tmp:
            self.p.save(Path(tmp) / "r.spadra")
            self.p = type(self.p).open(Path(tmp) / "r.spadra")
        self.assertEqual(self.meta(self.roof)[f"{NS}:roof_ends"]["0"]["type"], "C3")
        self.assertAlmostEqual(self.vol(self.roof), changed, places=9)
