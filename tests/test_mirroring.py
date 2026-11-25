"""Mirror Element against the real SGEO engine: the mirrored copy of every supported element is, vertex for vertex, the reflection of the original;
by meaning (a wall's doors and windows keep their swing side, a roof its slope); handed things are refused and put back; copies keep their group and links;
one undo puts everything back."""
from __future__ import annotations

import math
import unittest

from applications.SPADRA import documentation as D
from applications.SPADRA import elements as E
from applications.SPADRA import mirroring as MR
from applications.SPADRA import relationships as RS
from applications.SPADRA.tests import profiler_env  # noqa: F401  (skips only the JSON-schema step where jsonschema is too old)
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest

SQUARE = [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]
VERTICAL = ([6000, 0], [6000, 1000])                       # the plane x = 6 m
OBLIQUE = ([1000, 2000], [3000, 3155.0])                   # a plane at about 30 degrees to the x axis, not through the origin


def plane_of(a, b) -> MR.Plane:
    return MR.Plane((a[0] / 1000, a[1] / 1000), (b[0] / 1000, b[1] / 1000))


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project.create("Mirror")
        self.level = self.p.manifest["levels"][0]["id"]

    def do(self, op, **params):
        return self.p.execute(op, params)["result"]

    def mirror(self, ids, plane=VERTICAL, **extra):
        return self.do("model.mirror", ids=ids, p1=plane[0], p2=plane[1], **extra)

    def assert_reflection(self, original: str, copy: str, plane=VERTICAL) -> None:
        pl = plane_of(*plane)
        self.assertTrue(MR._is_reflection(pl, D.world_mesh(self.p, original)[0], D.world_mesh(self.p, copy)[0]), f"{E.category(self.p, original)} is not the reflection of its original")

    def wall(self, **extra):
        values = dict(start=[0, 0], end=[4000, 0], level=self.level, thickness=200, height=3.0)
        values.update(extra)
        return self.do("model.add_wall", **values)["id"]

    def copy_of(self, result, original):
        return next(a["copy"] for a in result["applied"] if a["id"] == original)


class Walls(Case):
    def test_a_wall_and_its_door_and_window_are_the_reflection_of_the_originals(self):
        for plane in (VERTICAL, OBLIQUE):
            with self.subTest(plane=plane):
                p = Project.create("Mirror")
                self.p, self.level = p, p.manifest["levels"][0]["id"]
                w = self.wall()
                d = self.do("model.add_door", wall=w, offset=1.0)["id"]
                win = self.do("model.add_window", wall=w, offset=3.0)["id"]
                before = (E.meta_of(p, w), E.meta_of(p, d))
                out = self.mirror([w], plane)
                copy = self.copy_of(out, w)
                self.assertNotEqual(copy, w)
                self.assert_reflection(w, copy, plane)
                originals = E.hosted(p, w)
                copies = E.hosted(p, copy)
                self.assertEqual(len(copies), 2)
                self.assertFalse(set(copies) & set(originals), "the copy's openings are new elements")
                for o, c in zip(originals, copies):
                    self.assert_reflection(o, c, plane)
                    self.assertEqual(E.m(E.meta_of(p, c), "host"), copy, "hosted on the copy, not on the original")
                self.assertEqual((E.meta_of(p, w), E.meta_of(p, d)), before, "the originals are untouched")

    def test_a_door_hinged_at_the_start_is_hinged_at_the_end_on_the_copy_and_keeps_its_swing_side(self):
        w = self.wall()
        d = self.do("model.add_door", wall=w, offset=1.0)["id"]
        self.assertEqual(E.m(E.meta_of(self.p, d), "hinge"), "start")
        swing = E.m(E.meta_of(self.p, d), "swing")
        copy = self.copy_of(self.mirror([w]), w)
        door = next(h for h in E.hosted(self.p, copy) if E.category(self.p, h) == "door")
        meta = E.meta_of(self.p, door)
        self.assertEqual(E.m(meta, "hinge"), "end")
        self.assertEqual(E.m(meta, "swing"), swing)
        self.assertAlmostEqual(float(E.m(meta, "offset")), 4.0 - 1.0, places=6)

    def test_every_hinge_and_swing_combination_mirrors_exactly(self):
        for hinge in ("start", "end"):
            for swing in (1, -1):
                with self.subTest(hinge=hinge, swing=swing):
                    p = Project.create("Mirror")
                    self.p, self.level = p, p.manifest["levels"][0]["id"]
                    w = self.wall()
                    d = self.do("model.add_door", wall=w, offset=1.2, hinge=hinge, swing="left" if swing == 1 else "right")["id"]
                    copy = self.copy_of(self.mirror([w], OBLIQUE), w)
                    self.assert_reflection(d, E.hosted(p, copy)[0], OBLIQUE)

    def test_walls_that_meet_are_mirrored_into_the_same_corner(self):
        a = self.wall(start=[0, 0], end=[4000, 0])
        b = self.wall(start=[4000, 0], end=[4000, 3000])
        out = self.mirror([a, b])
        ca, cb = self.copy_of(out, a), self.copy_of(out, b)
        self.assert_reflection(a, ca)
        self.assert_reflection(b, cb)

    def test_a_layered_wall_keeps_its_exterior_on_the_mirrored_side(self):
        w = self.wall()
        self.do("model.add_layer", id=w, material="Masonry", thickness=100, name="Outer leaf", position=0)
        before = [(l["name"], l["thickness"]) for l in __import__("applications.SPADRA.layers", fromlist=["x"]).stack(self.p, w)]
        copy = self.copy_of(self.mirror([w], OBLIQUE), w)
        self.assert_reflection(w, copy, OBLIQUE)
        after = [(l["name"], l["thickness"]) for l in __import__("applications.SPADRA.layers", fromlist=["x"]).stack(self.p, copy)]
        self.assertEqual(after, before)

    def test_a_door_or_window_alone_is_skipped_with_the_reason(self):
        w = self.wall()
        d = self.do("model.add_door", wall=w, offset=1.0)["id"]
        c = self.do("model.add_column", level=self.level, point=[500, 500])["id"]
        out = self.mirror([d, c])
        self.assertEqual([s["id"] for s in out["skipped"]], [d])
        self.assertIn("select the wall", out["skipped"][0]["reason"])
        self.assertEqual(len(out["applied"]), 1)


class Regions(Case):
    def test_floors_and_rooms(self):
        f = self.do("model.add_floor", level=self.level, points=SQUARE)["id"]
        room = self.do("model.add_room", level=self.level, points=SQUARE)["id"]
        for plane in (VERTICAL, OBLIQUE):
            out = self.mirror([f, room], plane)
            self.assertEqual(len(out["applied"]), 2, out)
            self.assert_reflection(f, self.copy_of(out, f), plane)
            got = E.m(E.meta_of(self.p, self.copy_of(out, room)), "boundary")[0]
            want = plane_of(*plane).point(E.m(E.meta_of(self.p, room), "boundary")[-1])
            self.assertAlmostEqual(got[0], want[0], places=9)
            self.assertAlmostEqual(got[1], want[1], places=9)

    def test_every_kind_of_roof_keeps_its_slope(self):
        roofs = {
            "flat": self.do("model.add_roof", level=self.level, points=SQUARE)["id"],
            "gable": self.do("model.add_roof", level=self.level, points=SQUARE, roof="gable", ridge_offset=300)["id"],
            "gable_other_way": self.do("model.add_roof", level=self.level, points=SQUARE, roof="gable", ridge_offset=-300)["id"],
            "shed": self.do("model.add_roof", level=self.level, points=SQUARE, roof="shed")["id"],
            "hip": self.do("model.add_roof", level=self.level, points=SQUARE, roof="hip")["id"],
            "tall": self.do("model.add_roof", level=self.level, points=[[0, 0], [2000, 0], [2000, 6000], [0, 6000]], roof="gable", ridge_offset=200)["id"],
        }
        for plane in (VERTICAL, OBLIQUE, ([0, 5000], [1000, 5000])):
            out = self.mirror(list(roofs.values()), plane)
            self.assertEqual(len(out["applied"]), len(roofs), out["failed"])
            for name, oid in roofs.items():
                with self.subTest(roof=name, plane=plane):
                    self.assert_reflection(oid, self.copy_of(out, oid), plane)

    def test_a_roofs_end_connections_move_to_the_mirrored_eave(self):
        roof = self.do("model.add_roof", level=self.level, points=SQUARE, roof="gable")["id"]
        self.do("model.set_roof_end", id=roof, edge=0, type="A1")
        copy = self.copy_of(self.mirror([roof]), roof)
        ends = E.m(E.meta_of(self.p, copy), "roof_ends")
        self.assertEqual(len(ends), 1)
        (edge,) = ends
        boundary = E.m(E.meta_of(self.p, copy), "boundary")
        a, b = boundary[int(edge)], boundary[(int(edge) + 1) % 4]
        # edge 0 of the original runs (0,0)-(4,0); mirrored across x = 6 it is (12,0)-(8,0)
        self.assertEqual({tuple(round(c, 6) for c in a), tuple(round(c, 6) for c in b)}, {(8.0, 0.0), (12.0, 0.0)})
        self.assert_reflection(roof, copy)

    def test_a_roofs_windows_go_with_it_to_the_mirrored_eave(self):
        roof = self.do("model.add_roof", level=self.level, points=SQUARE, roof="gable")["id"]
        win = self.do("model.add_roof_window", roof=roof, edge=0, x=1000, d=800, width=800, height=1000, kerb=50)["id"]
        for plane in (VERTICAL, OBLIQUE, ([0, 5000], [1000, 5000])):
            out = self.mirror([roof], plane)
            copy = self.copy_of(out, roof)
            self.assert_reflection(roof, copy, plane)
            mirrored = [o for o in E.element_ids(self.p) if E.category(self.p, o) == "roof_window" and E.m(E.meta_of(self.p, o), "host") == copy]
            self.assertEqual(len(mirrored), 1)
            self.assertNotEqual(mirrored[0], win)
            self.assert_reflection(win, mirrored[0], plane)

    def test_what_hangs_on_a_roof_and_is_not_mirrored_is_reported(self):
        roof = self.do("model.add_roof", level=self.level, points=SQUARE, roof="gable")["id"]
        self.do("model.add_gutters", roof=roof, downpipe=False)
        out = self.mirror([roof])
        self.assertTrue(any("not mirrored with it" in n for n in out["applied"][0]["notes"]))


class Members(Case):
    def test_columns_beams_grids_stairs_and_forms(self):
        col = self.do("model.add_column", level=self.level, point=[500, 500], rotation=30)["id"] if False else self.do("model.add_column", level=self.level, point=[500, 500])["id"]
        E._attach(self.p, col, {"rotation": math.radians(30)})
        E.regenerate(self.p, col)
        beam = self.do("model.add_beam", level=self.level, start=[0, 0], end=[3000, 1000])["id"]
        grid = self.do("model.add_grid", start=[0, -1000], end=[0, 5000], name="A")["id"]
        stair = self.do("model.add_stair", level=self.level, start=[0, 0], end=[3500, 800])["id"]
        form = self.do("model.add_extrusion", level=self.level, points=[[0, 0], [1000, 0], [1000, 500], [500, 500], [500, 1000], [0, 1000]], depth=2.0)["id"]
        for plane in (VERTICAL, OBLIQUE):
            out = self.mirror([col, beam, grid, stair, form], plane)
            self.assertEqual(len(out["applied"]), 5, out["failed"])
            for oid in (col, beam, stair, form):
                self.assert_reflection(oid, self.copy_of(out, oid), plane)
            g = E.meta_of(self.p, self.copy_of(out, grid))
            pl = plane_of(*plane)
            self.assertEqual([round(c, 6) for c in E.m(g, "start")], [round(c, 6) for c in pl.point(E.m(E.meta_of(self.p, grid), "start"))])

    def test_a_beam_with_a_handed_section_is_refused_and_put_back(self):
        # an unequal angle is not its own mirror image: its reflection is a different section, so there is no honest mirrored copy
        beam = self.do("model.add_beam", level=self.level, start=[0, 0], end=[3000, 0], profile="L_unequal", material="Steel")["id"]
        col = self.do("model.add_column", level=self.level, point=[500, 500])["id"]
        count = len(E.element_ids(self.p))
        out = self.mirror([beam, col])
        self.assertEqual([f["id"] for f in out["failed"]], [beam])
        self.assertIn("handed", out["failed"][0]["reason"])
        self.assertEqual(len(out["applied"]), 1, "the column is still mirrored")
        self.assertEqual(len(E.element_ids(self.p)), count + 1, "no half-made copy of the beam is left")

    def test_unsupported_things_say_why(self):
        sweep = self.do("model.add_sweep", level=self.level, points=[[0, 0], [2000, 0], [2000, 2000]], shape="rectangle", width=100, height=100)["id"]
        c = self.do("model.add_column", level=self.level, point=[500, 500])["id"]
        out = self.mirror([sweep, c])
        reasons = [f["reason"] for f in out["failed"] + out["skipped"]]
        self.assertEqual(len(reasons), 1)
        self.assertEqual(len(out["applied"]), 1)

    def test_nothing_mirrorable_is_an_error_with_the_reasons(self):
        w = self.wall()
        d = self.do("model.add_door", wall=w, offset=1.0)["id"]
        with self.assertRaises(BadRequest) as caught:
            self.mirror([d])
        self.assertIn("select the wall", str(caught.exception))


class InPlace(Case):
    def test_reflecting_in_place_moves_the_elements_and_what_follows_them(self):
        a, b = self.wall(start=[0, 0], end=[4000, 0]), self.wall(start=[0, 2000], end=[4000, 2000])
        self.do("rel.link", ids=[b], parent=a, inherit=["translate", "rotate"])
        mesh_a = D.world_mesh(self.p, a)[0]
        mesh_b = D.world_mesh(self.p, b)[0]
        out = self.mirror([a], copy=False)
        self.assertEqual(out["mode"], "move")
        self.assertEqual(sorted(x["id"] for x in out["applied"]), sorted([a, b]), "the linked child is reflected with its parent")
        pl = plane_of(*VERTICAL)
        self.assertTrue(MR._is_reflection(pl, mesh_a, D.world_mesh(self.p, a)[0]))
        self.assertTrue(MR._is_reflection(pl, mesh_b, D.world_mesh(self.p, b)[0]))
        self.assertEqual(len(E.element_ids(self.p)), 2, "no copies")

    def test_scope_member_does_not_pull_in_the_rest_of_the_group(self):
        a, b = self.wall(start=[0, 0], end=[4000, 0]), self.wall(start=[0, 2000], end=[4000, 2000])
        self.do("rel.group", ids=[a, b], name="Pair")
        self.assertEqual(len(self.mirror([a], copy=False)["applied"]), 2)
        self.assertEqual(len(self.mirror([a], copy=False, scope="member")["applied"]), 1)


class Structure(Case):
    def test_copies_of_a_group_and_a_linked_set_keep_their_structure(self):
        a, b, c = (self.wall(start=[0, 1000 * i], end=[4000, 1000 * i]) for i in range(3))
        self.do("rel.link", ids=[b], parent=a, inherit=["translate", "rotate"])
        self.do("rel.group", ids=[a, b, c], name="Walls")
        out = self.mirror([a, b, c])
        ca, cb, cc = (self.copy_of(out, x) for x in (a, b, c))
        self.assertEqual(RS.parent_of(self.p, cb), ca, "the copy of the child follows the copy of the parent")
        self.assertIsNone(RS.parent_of(self.p, ca))
        self.assertEqual(RS.group_of(self.p, ca), RS.group_of(self.p, cb))
        self.assertNotEqual(RS.group_of(self.p, ca), RS.group_of(self.p, a), "the copies are a new group, not members of the original one")
        self.assertEqual(sorted(RS.group_members(self.p, RS.group_of(self.p, a))), sorted([a, b, c]), "the original group is unchanged")
        self.assertEqual(out["structure"]["links"], 1)
        self.assertEqual(len(out["structure"]["groups"]), 1)

    def test_a_copy_does_not_follow_the_originals_parent_when_the_parent_was_not_copied(self):
        parent, child = self.wall(start=[0, 0], end=[4000, 0]), self.wall(start=[0, 2000], end=[4000, 2000])
        self.do("rel.link", ids=[child], parent=parent, inherit=["translate", "rotate"])
        out = self.mirror([child])
        self.assertIsNone(RS.parent_of(self.p, self.copy_of(out, child)))

    def test_one_undo_puts_everything_back(self):
        w = self.wall()
        self.do("model.add_door", wall=w, offset=1.0)
        before = sorted(E.element_ids(self.p))
        self.mirror([w])
        self.assertGreater(len(E.element_ids(self.p)), len(before))
        self.p.undo()
        self.assertEqual(sorted(E.element_ids(self.p)), before)
        self.p.redo()
        self.assertGreater(len(E.element_ids(self.p)), len(before))

    def test_paste_element_copies_keep_their_group(self):
        a, b = self.wall(start=[0, 0], end=[4000, 0]), self.wall(start=[0, 2000], end=[4000, 2000])
        self.do("rel.group", ids=[a, b], name="Pair")
        out = self.do("model.array", ids=[a, b], count=2, step=[0, 0, 5.0], persistent=False)
        ca, cb = out["ids"]
        self.assertEqual(RS.group_of(self.p, ca), RS.group_of(self.p, cb))
        self.assertIsNotNone(RS.group_of(self.p, ca))
        self.assertNotEqual(RS.group_of(self.p, ca), RS.group_of(self.p, a))


if __name__ == "__main__":
    unittest.main()
