"""Wall placement: the reference line (near face, centre, far face, plus a numeric offset) a wall is drawn along, and what keeps it there."""
from __future__ import annotations

import tempfile
from pathlib import Path

from applications.SPADRA import elements as E
from applications.tools.errors import BadRequest

from .test_bim import Case

NS = "spadra"


class WallReference(Case):
    def add(self, a, b, **extra):
        return self.run_("model.add_wall", start=list(a), end=list(b), level=self.l1, thickness=250, height=3.2, **extra)["id"]

    def span_y(self, oid):
        lo, hi = self.p.model.bounds(oid)
        return round(lo[1], 6), round(hi[1], 6)

    def centre(self, oid):
        s = self.meta(oid)[f"{NS}:start"]
        e = self.meta(oid)[f"{NS}:end"]
        return (round(s[0], 6), round(s[1], 6)), (round(e[0], 6), round(e[1], 6))

    # ---------------------------------------------------------------- creation
    def test_near_centre_and_far_put_the_drawn_line_on_that_face(self):
        # path +x: the left (far) side is +y, the near face is the right-hand one
        self.assertEqual(self.span_y(self.add((0, 0), (6000, 0), reference="near")), (0.0, 0.25))
        self.assertEqual(self.span_y(self.add((0, 1000), (6000, 1000), reference="centre")), (0.875, 1.125))
        self.assertEqual(self.span_y(self.add((0, 3000), (6000, 3000), reference="far")), (2.75, 3.0))

    def test_an_old_wall_without_a_reference_is_centred_and_unchanged(self):
        w = self.add((0, 0), (6000, 0))
        self.assertEqual(self.meta(w)[f"{NS}:reference"], "centre")
        self.assertEqual(self.span_y(w), (-0.125, 0.125))

    def test_the_offset_moves_the_wall_from_its_reference_toward_the_far_side(self):
        self.assertEqual(self.span_y(self.add((0, 0), (6000, 0), reference="centre", reference_offset=100)), (-0.025, 0.225))
        self.assertEqual(self.span_y(self.add((0, 2000), (6000, 2000), reference="near", reference_offset=-50)), (1.95, 2.2))

    def test_near_and_far_follow_the_direction_of_the_path(self):
        a = self.span_y(self.add((6000, 0), (0, 0), reference="near"))              # drawn the other way, the right-hand face is the -y one...
        self.assertEqual(a, (-0.25, 0.0))                                           # ...and the wall lies on that side of the line

    def test_a_bad_reference_is_refused(self):
        with self.assertRaises(BadRequest):
            self.add((0, 0), (1000, 0), reference="inside")

    # ---------------------------------------------------------------- after drawing
    def test_changing_the_reference_keeps_the_line_and_moves_the_wall_to_it(self):
        w = self.add((0, 0), (6000, 0), reference="near")
        self.run_("model.set_property", id=w, key="reference", value="far")
        self.assertEqual(self.span_y(w), (-0.25, 0.0))                              # the line stays at y=0; the wall now hangs on its other side
        self.run_("model.set_property", id=w, key="reference", value="centre")
        self.assertEqual(self.span_y(w), (-0.125, 0.125))
        self.run_("model.set_property", id=w, key="reference_offset", value=200)
        self.assertEqual(self.span_y(w), (0.075, 0.325))

    def test_the_reference_survives_a_thickness_change(self):
        for ref, expect in (("near", (0.0, 0.4)), ("far", (-0.4, 0.0)), ("centre", (-0.2, 0.2))):
            w = self.add((0, 0), (6000, 0), reference=ref)
            self.run_("model.set_property", id=w, key="thickness", value=400)
            self.assertEqual(self.span_y(w), expect, ref)
            self.run_("model.set_property", id=w, key="thickness", value=250)
            self.run_("model.delete", id=w)

    def test_a_layered_wall_keeps_its_reference_when_a_layer_is_added(self):
        w = self.add((0, 0), (6000, 0), reference="near")
        self.run_("model.add_layer", id=w, side="right", material="Mineral wool", thickness=100)
        self.assertAlmostEqual(self.meta(w)[f"{NS}:ref_thickness"], 0.35, places=9)
        self.assertAlmostEqual(self.span_y(w)[1] - self.span_y(w)[0], 0.35, places=6)
        self.assertAlmostEqual(self.span_y(w)[0], 0.0, places=6)                    # the near face is still on the drawn line

    # ---------------------------------------------------------------- corners and thickness
    def test_walls_drawn_around_a_corner_by_their_faces_meet_in_a_corner(self):
        a = self.add((0, 0), (6000, 0), reference="near")
        b = self.add((6000, 0), (6000, 4000), reference="near")
        self.assertEqual(self.centre(a)[1], (5.875, 0.125))                          # both centre lines end where they cross
        self.assertEqual(self.centre(b)[0], (5.875, 0.125))
        self.assertEqual(self.meta(a)[f"{NS}:joins"], [b])
        lo, hi = self.p.model.bounds(a)
        self.assertAlmostEqual(lo[0], 0.0, places=6)
        self.assertAlmostEqual(hi[0], 6.0, places=6)                                 # the outer corner is the drawn point, not 125 mm beyond it

    def test_thickening_one_wall_of_a_corner_keeps_the_corner_closed(self):
        a = self.add((0, 0), (6000, 0), reference="near")
        b = self.add((6000, 0), (6000, 4000), reference="near")
        self.run_("model.set_property", id=a, key="thickness", value=400)
        self.assertEqual(self.span_y(a), (0.0, 0.4))
        self.assertEqual(self.centre(a)[1], self.centre(b)[0])                       # the walls still share their corner
        self.assertEqual(self.meta(b)[f"{NS}:joins"], [a])

    def test_changing_the_reference_of_a_wall_in_a_corner_keeps_it_joined(self):
        a = self.add((0, 0), (6000, 0))
        b = self.add((6000, 0), (6000, 4000))
        self.run_("model.set_property", id=a, key="reference", value="near")
        self.assertEqual(self.span_y(a), (0.0, 0.25))
        self.assertEqual(self.centre(a)[1], self.centre(b)[0])

    def test_a_door_does_not_move_when_a_corner_wall_is_re_referenced(self):
        a = self.add((0, 0), (6000, 0))
        b = self.add((6000, 0), (6000, 4000))
        d = self.run_("model.add_door", wall=b, offset=2.0, width=900, height=2100)["id"]
        before = self.p.model.bounds(d)
        self.run_("model.set_property", id=a, key="reference", value="near")
        lo, hi = self.p.model.bounds(d)
        self.assertAlmostEqual(lo[1], before[0][1], places=6)                        # the door stays where it was in the room
        self.assertAlmostEqual(hi[1], before[1][1], places=6)

    # ---------------------------------------------------------------- flip
    def test_flip_reverses_the_path_without_moving_the_wall(self):
        w = self.add((0, 0), (6000, 0), reference="near", reference_offset=30)
        d = self.run_("model.add_door", wall=w, offset=1.5, width=900, height=2100, hinge="start", swing="left")["id"]
        box0, door0 = self.p.model.bounds(w), self.p.model.bounds(d)
        v0 = self.vol(w)
        self.run_("model.flip_wall", id=w)
        m = self.meta(w)
        self.assertEqual(m[f"{NS}:reference"], "far")
        self.assertAlmostEqual(m[f"{NS}:reference_offset"], -0.03)
        self.assertEqual(self.centre(w), ((6.0, 0.155), (0.0, 0.155)))
        for was, now in ((box0, self.p.model.bounds(w)), (door0, self.p.model.bounds(d))):
            for i in (0, 1):
                for j in range(3):
                    self.assertAlmostEqual(was[i][j], now[i][j], places=6)
        self.assertAlmostEqual(self.vol(w), v0, places=6)
        self.assertEqual(self.meta(d)[f"{NS}:hinge"], "end")                         # the same jamb, now at the other end of the path
        self.run_("model.flip_wall", id=w)
        self.assertEqual(self.meta(w)[f"{NS}:reference"], "near")
        self.assertEqual(self.centre(w), ((0.0, 0.155), (6.0, 0.155)))
        self.assertEqual(self.meta(d)[f"{NS}:hinge"], "start")

    def test_flipping_a_wall_in_a_corner_keeps_the_corner(self):
        a = self.add((0, 0), (6000, 0))
        b = self.add((6000, 0), (6000, 4000))
        E.set_wall_corner_mode(self.p, a, b, "end", "through_selected")             # a saved corner style names the wall's ends
        before = dict(self.p.manifest["wall_corners"])
        self.assertTrue(all(f"{a}:end" in k for k in before))
        self.run_("model.flip_wall", id=a)
        self.assertEqual(self.meta(a)[f"{NS}:joins"], [b])
        self.assertEqual(self.meta(b)[f"{NS}:joins"], [a])
        self.assertTrue(all(f"{a}:start" in k and f"{a}:end" not in k for k in self.p.manifest["wall_corners"]))   # the style followed the wall's end

    # ---------------------------------------------------------------- properties and persistence
    def test_properties_expose_the_reference_and_offset(self):
        w = self.add((0, 0), (6000, 0), reference="far", reference_offset=40)
        rows = self.rows(w)
        self.assertEqual(rows["reference"]["value"], "far")
        self.assertEqual(rows["reference_offset"]["value"], 40)
        group = next(g for g in self.p.properties({"model": w})["groups"] if g["title"] == "Placement")
        self.assertEqual(group["action"]["command"], "model.flip_wall")

    def test_the_reference_survives_save_and_reload_and_undo(self):
        w = self.add((0, 0), (6000, 0), reference="near", reference_offset=20)
        with tempfile.TemporaryDirectory() as tmp:
            self.p.save(Path(tmp) / "w.spadra")
            self.p = type(self.p).open(Path(tmp) / "w.spadra")
        self.assertEqual(self.meta(w)[f"{NS}:reference"], "near")
        self.assertEqual(self.span_y(w), (0.02, 0.27))
        self.run_("model.set_property", id=w, key="reference", value="far")
        self.p.undo()
        self.assertEqual(self.meta(w)[f"{NS}:reference"], "near")
        self.assertEqual(self.span_y(w), (0.02, 0.27))

    def test_the_reference_line_is_derived_from_the_centre_line(self):
        w = self.add((0, 0), (6000, 0), reference="far", reference_offset=40)
        a, b = E.wall_reference_line(self.meta(w))
        self.assertAlmostEqual(a[1], 0.0, places=9)
        self.assertAlmostEqual(b[0], 6.0, places=9)

    def test_the_plan_exposes_the_reference_line_for_display_and_snapping(self):
        w = self.add((0, 0), (6000, 0), reference="near")
        plan = next(v for v in self.p.manifest["views"] if v.get("level_id") == self.l1 and v["kind"] == "plan")
        item = next(i for i in self.p.plan_overlay(plan["id"]) if i["id"] == w)
        ref = item["reference"]
        self.assertEqual(ref["kind"], "near")
        self.assertAlmostEqual(ref["a"][1], 0.0, places=6)                           # the drawn line, not the centre line (y = 125 mm)
        self.assertGreater(item["axis"]["a"][1], ref["a"][1])                        # the centre line lies on the far side of a near-face line
