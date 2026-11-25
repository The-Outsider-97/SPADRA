"""Copy, mirror and paste properties between existing elements, against the real SGEO engine.

The rules under test: no element is ever created, moved or replaced; the target keeps its identity and place; the configuration arrives independent
and editable; a mirror reflects by meaning (layer order, signed offsets, swing, hinge), not by negating every number; one target failing never
corrupts it or its neighbours; and nothing shared by a type changes."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from applications.SPADRA import elements as E
from applications.SPADRA import layers as LY
from applications.SPADRA import property_transfer as PT
from applications.SPADRA.project import NS, Project
from applications.SPADRA.tests import profiler_env  # noqa: F401  (skips only the JSON-schema step where jsonschema is too old)
from applications.tools.errors import BadRequest

SQUARE = [[0, 0], [4000, 0], [4000, 4000], [0, 4000]]


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project.create("Transfer")
        self.l1, self.l2, _ = (l["id"] for l in self.p.manifest["levels"])

    def do(self, op, **params):
        return self.p.execute(op, params)["result"]

    def wall(self, y=0):
        return self.do("model.add_wall", start=[0, y], end=[6000, y], level=self.l1, thickness=250, height=3.2)["id"]

    def meta(self, oid):
        return self.p.model.get(oid)["metadata"]

    def count(self):
        return len(E.element_ids(self.p))

    def paste(self, source, targets, groups=PT.GROUPS, **kw):
        return self.do("model.paste_properties", source=source, destinations=targets, groups=list(groups), **kw)

    def materials(self, oid):
        return [row["material"] for row in LY.stack(self.p, oid)]


class Copy(Case):
    def test_a_wall_hands_its_configuration_to_other_walls_and_nothing_else_changes(self):
        src = self.wall(0)
        a, b = self.wall(5000), self.wall(9000)
        self.do("model.add_layer", id=src, material="Mineral wool", thickness=100)
        self.do("model.set_property", id=src, key="lean_angle", value=8)
        self.do("model.set_property", id=src, key="color", value="#AABBCC")
        self.do("model.set_property", id=src, key="description", value="Party wall")
        before = {t: (self.meta(t)[f"{NS}:start"], self.meta(t)[f"{NS}:end"], self.meta(t)[f"{NS}:level"]) for t in (a, b)}
        elements = self.count()
        out = self.paste(src, [a, b])
        self.assertEqual(self.count(), elements)                                                       # no wall was added
        self.assertEqual([x["id"] for x in out["applied"]], [a, b])
        for t in (a, b):
            self.assertEqual(self.materials(t), self.materials(src))
            self.assertFalse({r["id"] for r in LY.stack(self.p, t)} & {r["id"] for r in LY.stack(self.p, src)})     # new layer ids: independent
            self.assertEqual(E.m(self.meta(t), "lean_angle"), 8)
            self.assertEqual(E.m(self.meta(t), "color"), "#AABBCC")
            self.assertEqual(E.m(self.meta(t), "description"), "Party wall")
            self.assertEqual((self.meta(t)[f"{NS}:start"], self.meta(t)[f"{NS}:end"], self.meta(t)[f"{NS}:level"]), before[t])      # place untouched
        self.assertTrue(out["not_transferred"])

    def test_the_copy_is_independent_of_its_source(self):
        src, t = self.wall(0), self.wall(5000)
        self.do("model.set_property", id=src, key="lean_angle", value=8)
        self.paste(src, [t])
        self.do("model.set_property", id=src, key="lean_angle", value=20)
        self.assertEqual(E.m(self.meta(t), "lean_angle"), 8)                                           # no live dependency
        self.do("model.set_property", id=t, key="lean_angle", value=3)                                 # and the target stays editable
        self.assertEqual(E.m(self.meta(t), "lean_angle"), 3)

    def test_transferred_formulas_stay_formulas(self):
        src, t = self.wall(0), self.wall(5000)
        self.do("model.set_property", id=src, key="lean_angle", value="=angle * 0 + 7 deg")
        self.paste(src, [t], groups=["formulas"])
        self.assertIn("lean_angle", E.m(self.meta(t), "formulas"))
        self.assertAlmostEqual(E.m(self.meta(t), "lean_angle"), 7)

    def test_one_target_never_changes_another_instance_of_the_same_type(self):
        src, t, bystander = self.wall(0), self.wall(5000), self.wall(9000)
        self.do("model.set_property", id=src, key="lean_angle", value=8)
        snap = {k: v for k, v in self.meta(bystander).items()}
        self.paste(src, [t])
        self.assertEqual(self.meta(bystander), snap)

    def test_floors_roofs_openings_beams_and_columns(self):
        f1 = self.do("model.add_floor", points=SQUARE, level=self.l1)["id"]
        f2 = self.do("model.add_floor", points=[[x + 6000, y] for x, y in SQUARE], level=self.l1)["id"]
        self.do("model.set_property", id=f1, key="thickness", value=300)
        self.paste(f1, [f2])
        self.assertAlmostEqual(E.m(self.meta(f2), "thickness"), 0.3)
        r1 = self.do("model.add_roof", points=SQUARE, level=self.l2, roof="gable", pitch=20)["id"]
        r2 = self.do("model.add_roof", points=[[x + 6000, y] for x, y in SQUARE], level=self.l2, roof="flat")["id"]
        out = self.paste(r1, [r2], groups=["dimensions"])
        self.assertEqual(out["failed"], [])
        self.assertEqual(E.m(self.meta(r2), "roof"), "gable")
        self.assertAlmostEqual(E.m(self.meta(r2), "pitch"), 20, places=5)
        w1, w2 = self.wall(0), self.wall(5000)
        d1 = self.do("model.add_door", wall=w1, offset=2.0, width=1000)["id"]
        d2 = self.do("model.add_door", wall=w2, offset=3.0)["id"]
        self.paste(d1, [d2], groups=["dimensions"])
        self.assertAlmostEqual(E.m(self.meta(d2), "width"), 1.0)
        self.assertAlmostEqual(E.m(self.meta(d2), "offset"), 3.0)                                       # where it stands is its own
        self.assertEqual(E.m(self.meta(d2), "host"), w2)                                                # and so is its host
        b1 = self.do("model.add_beam", start=[0, 0], end=[4000, 0], level=self.l1, top=3.0, width=200, depth=500)["id"]
        b2 = self.do("model.add_beam", start=[0, 1000], end=[4000, 1000], level=self.l1, top=3.0)["id"]
        self.paste(b1, [b2], groups=["dimensions"])
        self.assertAlmostEqual(E.m(self.meta(b2), "depth"), 0.5)
        self.assertEqual(E.m(self.meta(b2), "start"), [0.0, 1.0])

    def test_an_incompatible_target_is_skipped_with_its_reason_and_the_rest_are_done(self):
        src, good = self.wall(0), self.wall(5000)
        floor = self.do("model.add_floor", points=SQUARE, level=self.l1)["id"]
        self.do("model.set_property", id=src, key="color", value="#112233")
        out = self.paste(src, [floor, good], groups=["appearance"])
        self.assertEqual([x["id"] for x in out["applied"]], [good])
        self.assertEqual([x["id"] for x in out["skipped"]], [floor])
        self.assertIn("floor", out["skipped"][0]["reason"])
        self.assertEqual(E.m(self.meta(good), "color"), "#112233")
        self.assertIsNone(E.m(self.meta(floor), "color"))

    def test_the_original_paste_attributes_still_refuses_instead_of_skipping(self):
        src, good = self.wall(0), self.wall(5000)
        floor = self.do("model.add_floor", points=SQUARE, level=self.l1)["id"]
        with self.assertRaises(BadRequest):
            self.do("model.copy_attributes", source=src, destinations=[good, floor], groups=["appearance"])

    def test_a_source_cannot_paste_onto_itself_and_groups_are_checked(self):
        src = self.wall(0)
        with self.assertRaises(BadRequest):
            self.paste(src, [src])
        with self.assertRaises(BadRequest):
            self.paste(src, [self.wall(5000)], groups=["nonsense"])

    def test_undo_takes_every_target_back_together(self):
        src, a, b = self.wall(0), self.wall(5000), self.wall(9000)
        self.do("model.set_property", id=src, key="lean_angle", value=8)
        self.paste(src, [a, b])
        self.p.undo()
        for t in (a, b):
            self.assertIsNone(E.m(self.meta(t), "lean_angle"))

    def test_the_transfer_survives_save_and_reopen(self):
        src, t = self.wall(0), self.wall(5000)
        self.do("model.add_layer", id=src, material="Mineral wool", thickness=100)
        self.paste(src, [t])
        with tempfile.TemporaryDirectory() as tmp:
            self.p.save(Path(tmp) / "T.spadra")
            q = Project.open(Path(tmp) / "T.spadra")
        self.assertEqual([r["material"] for r in LY.stack(q, t)], self.materials(t))


class Failure(Case):
    def test_a_target_that_fails_is_put_back_and_named_and_the_others_still_get_it(self):
        src, a, bad, c = self.wall(0), self.wall(5000), self.wall(9000), self.wall(13000)
        self.do("model.set_property", id=src, key="lean_angle", value=8)
        real = PT._apply

        def flaky(project, source, dest):
            changed = real(project, source, dest)                                    # the target IS changed ...
            if dest == bad:
                raise BadRequest("the last step is refused")                         # ... and then the operation fails
            return changed
        PT._apply = flaky
        try:
            before_bad = {k: v for k, v in self.meta(bad).items()}
            out = self.paste(src, [a, bad, c], groups=["dimensions"])
        finally:
            PT._apply = real
        self.assertEqual([x["id"] for x in out["failed"]], [bad])
        self.assertIn("refused", out["failed"][0]["reason"])
        self.assertEqual(self.meta(bad), before_bad)                                 # not even partly changed
        for t in (a, c):
            self.assertEqual(E.m(self.meta(t), "lean_angle"), 8)
        self.assertEqual(sorted(out["destinations"]), sorted([a, c]))


class Mirror(Case):
    def test_mirroring_a_wall_reverses_its_layers_and_flips_its_lean_but_not_its_place(self):
        src, t = self.wall(0), self.wall(5000)
        self.do("model.add_layer", id=src, material="Mineral wool", thickness=100)
        self.do("model.set_property", id=src, key="lean_angle", value=10)
        place = (self.meta(t)[f"{NS}:start"], self.meta(t)[f"{NS}:end"])
        self.paste(src, [t], mode="mirror")
        self.assertEqual(self.materials(t), list(reversed(self.materials(src))))
        self.assertEqual(E.m(self.meta(t), "lean_angle"), -10)
        self.assertAlmostEqual(E.m(self.meta(t), "thickness"), E.m(self.meta(src), "thickness"))     # sizes are not negated
        self.assertEqual((self.meta(t)[f"{NS}:start"], self.meta(t)[f"{NS}:end"]), place)
        self.assertEqual(E.m(self.meta(src), "lean_angle"), 10)                                      # and the source is untouched

    def test_mirroring_twice_returns_the_original_configuration(self):
        src, mid, last = self.wall(0), self.wall(5000), self.wall(9000)
        self.do("model.add_layer", id=src, material="Mineral wool", thickness=100)
        self.do("model.set_property", id=src, key="lean_angle", value=10)
        self.paste(src, [mid], mode="mirror")
        self.paste(mid, [last], mode="mirror")
        self.assertEqual(self.materials(last), self.materials(src))
        self.assertEqual(E.m(self.meta(last), "lean_angle"), 10)

    def test_a_door_mirrors_its_facing_and_its_hand_separately(self):
        w1, w2, w3, w4 = (self.wall(y) for y in (0, 5000, 9000, 13000))
        src = self.do("model.add_door", wall=w1, offset=2.0)["id"]
        self.do("model.set_property", id=src, key="swing", value="left")
        self.do("model.set_property", id=src, key="hinge", value="start")
        side = self.do("model.add_door", wall=w2, offset=2.0)["id"]
        length = self.do("model.add_door", wall=w3, offset=2.0)["id"]
        both = self.do("model.add_door", wall=w4, offset=2.0)["id"]
        self.paste(src, [side], groups=["dimensions"], mode="mirror", axis="side")
        self.paste(src, [length], groups=["dimensions"], mode="mirror", axis="length")
        self.paste(src, [both], groups=["dimensions"], mode="mirror", axis="both")
        sw = lambda oid: int(E.m(self.meta(oid), "swing", 1))                                       # noqa: E731
        hg = lambda oid: E.m(self.meta(oid), "hinge", "start")                                      # noqa: E731
        self.assertEqual((sw(side), hg(side)), (-sw(src), hg(src)))
        self.assertEqual((sw(length), hg(length)), (sw(src), "end" if hg(src) == "start" else "start"))
        self.assertEqual((sw(both), hg(both)), (-sw(src), "end" if hg(src) == "start" else "start"))
        for oid in (side, length, both):
            self.assertEqual(E.m(self.meta(oid), "host"), {side: w2, length: w3, both: w4}[oid])      # a door stays in its own wall

    def test_a_gable_roof_mirrors_its_ridge_offset_and_keeps_its_pitch(self):
        r1 = self.do("model.add_roof", points=SQUARE, level=self.l2, roof="gable", pitch=25, ridge_offset=300)["id"]
        r2 = self.do("model.add_roof", points=[[x + 6000, y] for x, y in SQUARE], level=self.l2, roof="gable")["id"]
        self.paste(r1, [r2], groups=["dimensions"], mode="mirror")
        self.assertAlmostEqual(E.m(self.meta(r2), "ridge_offset"), -0.3)
        self.assertAlmostEqual(E.m(self.meta(r2), "pitch"), 25, places=5)

    def test_a_mirrored_formula_on_a_signed_parameter_is_negated_with_it(self):
        src, t = self.wall(0), self.wall(5000)
        self.do("model.set_property", id=src, key="lean_angle", value="=angle * 0 + 7 deg")
        self.paste(src, [t], groups=["formulas"], mode="mirror")
        self.assertAlmostEqual(E.m(self.meta(t), "lean_angle"), -7)
        self.assertIn("lean_angle", E.m(self.meta(t), "formulas"))

    def test_a_parameter_the_target_has_no_equivalent_of_is_reported_not_forced(self):
        gable = self.do("model.add_roof", points=SQUARE, level=self.l2, roof="gable", pitch=20)["id"]
        flat = self.do("model.add_roof", points=[[x + 6000, y] for x, y in SQUARE], level=self.l2, roof="flat")["id"]
        out = self.paste(flat, [gable], groups=["dimensions"])                       # a flat roof has no pitch to hand to a gable
        self.assertEqual(out["failed"], [])
        self.assertEqual(E.m(self.meta(gable), "roof"), "flat")

    def test_bad_axis_and_mode_are_refused(self):
        src, t = self.wall(0), self.wall(5000)
        with self.assertRaises(BadRequest):
            self.paste(src, [t], mode="mirror", axis="diagonal")
        with self.assertRaises(BadRequest):
            self.paste(src, [t], mode="flip")


class Describe(Case):
    def test_describe_lists_what_a_source_can_hand_over(self):
        src = self.wall(0)
        self.do("model.set_property", id=src, key="color", value="#123456")
        info = PT.describe(self.p, src)
        groups = {g["id"]: g["has_values"] for g in info["groups"]}
        self.assertTrue(groups["appearance"])
        self.assertTrue(groups["construction"])
        self.assertFalse(groups["custom"])
        self.assertTrue(info["mirrors"])


if __name__ == "__main__":
    unittest.main()
