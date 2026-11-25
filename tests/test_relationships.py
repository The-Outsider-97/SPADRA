"""Links, chains and groups against the real SGEO engine: no mocks, real files in a temporary folder.

Every claim of the specification's acceptance table is a test here: moving a parent, a child and an intermediate element, turning a hierarchy,
unlinking without a jump, cycles, re-parenting, groups (mixed, nested, individual editing, pivot), attach and detach, undo/redo, persistence,
deleting a link's end, and that nothing is ever transformed twice."""
from __future__ import annotations

import math
import tempfile
import time
import unittest
from pathlib import Path

from applications.SPADRA import elements as E
from applications.SPADRA import relationships as RS
from applications.SPADRA.tests import profiler_env  # noqa: F401  (skips only the JSON-schema step where jsonschema is too old)
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest, NotFound


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.p = Project.create("Relations")
        self.level = self.p.manifest["levels"][0]["id"]

    def do(self, op, **params):
        return self.p.execute(op, params)["result"]

    def wall(self, y: float, x0: float = 0, x1: float = 4000) -> str:
        return self.do("model.add_wall", start=[x0, y], end=[x1, y], level=self.level, thickness=200, height=3.0)["id"]

    def walls(self, n: int) -> list[str]:
        return [self.wall(1000.0 * i) for i in range(n)]

    def ends(self, oid):
        meta = E.meta_of(self.p, oid)
        return [list(E.m(meta, "start")), list(E.m(meta, "end"))]

    def snapshot(self, ids):
        return {i: self.ends(i) for i in ids}

    def assertSame(self, a, b, places=9):
        self.assertEqual(a.keys(), b.keys())
        for k in a:
            for pa, pb in zip(a[k], b[k]):
                for x, y in zip(pa, pb):
                    self.assertAlmostEqual(x, y, places=places, msg=k)

    def assertShifted(self, before, after, ids, dx, dy):
        for i in ids:
            for pb, pa in zip(before[i], after[i]):
                self.assertAlmostEqual(pa[0], pb[0] + dx, places=9, msg=i)
                self.assertAlmostEqual(pa[1], pb[1] + dy, places=9, msg=i)

    def tree(self):
        """The specification's example: A > (B > (C > D, E), F)."""
        a, b, c, d, e, f = self.walls(6)
        self.do("rel.link", ids=[b, f], parent=a)
        self.do("rel.link", ids=[c, e], parent=b)
        self.do("rel.link", ids=[d], parent=c)
        return dict(zip("ABCDEF", (a, b, c, d, e, f)))


class Hierarchy(Case):
    def test_moving_each_element_moves_exactly_its_descendants(self):
        t = self.tree()
        follows = {"A": "BCDEF", "B": "CDE", "C": "D", "D": "", "E": "", "F": ""}
        for name, below in follows.items():
            before = self.snapshot(t.values())
            moved = self.do("model.move", id=t[name], dx=0.5, dy=0.25, dz=0)["moved"]
            after = self.snapshot(t.values())
            mover = {t[name], *(t[x] for x in below)}
            self.assertEqual(set(moved), mover, name)
            self.assertShifted(before, after, mover, 0.5, 0.25)
            self.assertSame({k: v for k, v in before.items() if k not in mover}, {k: v for k, v in after.items() if k not in mover})

    def test_moving_a_child_changes_its_offset_from_the_parent_and_nothing_else(self):
        t = self.tree()
        gap = lambda: E.m(E.meta_of(self.p, t["B"]), "start")[1] - E.m(E.meta_of(self.p, t["A"]), "start")[1]
        before = gap()
        self.do("model.move", id=t["B"], dx=0, dy=0.3, dz=0)
        self.assertAlmostEqual(gap(), before + 0.3)
        self.assertEqual(RS.parent_of(self.p, t["B"]), t["A"])                      # still linked at the new offset

    def test_linking_does_not_move_anything(self):
        a, b = self.walls(2)
        before = self.snapshot([a, b])
        self.do("rel.link", ids=[b], parent=a)
        self.assertSame(before, self.snapshot([a, b]))

    def test_rotating_a_hierarchy_keeps_every_relative_transformation(self):
        t = self.tree()
        ids = list(t.values())
        before = self.snapshot(ids)
        self.do("model.rotate", ids=[t["A"]], centre=[1000, 2000], angle=30)
        after = self.snapshot(ids)
        for i in ids:                                                               # every wall turned rigidly about the same centre
            (bs, be), (as_, ae) = before[i], after[i]
            self.assertAlmostEqual(math.dist(bs, be), math.dist(as_, ae), places=9)
            self.assertAlmostEqual(math.hypot(bs[0] - 1.0, bs[1] - 2.0), math.hypot(as_[0] - 1.0, as_[1] - 2.0), places=9)
            self.assertAlmostEqual(math.atan2(ae[1] - as_[1], ae[0] - as_[0]) - math.atan2(be[1] - bs[1], be[0] - bs[0]), math.radians(30), places=9)

    def test_rotating_a_descendant_does_not_turn_its_ancestors_or_siblings(self):
        t = self.tree()
        before = self.snapshot(t.values())
        self.do("model.rotate", ids=[t["C"]], centre=[0, 0], angle=45)
        after = self.snapshot(t.values())
        for name in "ABEF":
            self.assertSame({t[name]: before[t[name]]}, {t[name]: after[t[name]]})
        for name in "CD":
            self.assertNotEqual(before[t[name]], after[t[name]])

    def test_translate_only_carries_a_child_round_without_turning_it(self):
        a, b = self.walls(2)
        self.do("rel.link", ids=[b], parent=a, inherit=["translate"])
        before_dir = [E.m(E.meta_of(self.p, b), "end")[i] - E.m(E.meta_of(self.p, b), "start")[i] for i in (0, 1)]
        self.do("model.rotate", ids=[a], centre=[0, 0], angle=90)
        meta = E.meta_of(self.p, b)
        self.assertEqual([round(E.m(meta, "end")[i] - E.m(meta, "start")[i], 9) for i in (0, 1)], [round(v, 9) for v in before_dir])   # same direction
        centre = [(E.m(meta, "start")[i] + E.m(meta, "end")[i]) / 2 for i in (0, 1)]
        self.assertAlmostEqual(centre[0], -1.0, places=6)                              # (2, 1) m turned 90° about the origin is (-1, 2)
        self.assertAlmostEqual(centre[1], 2.0, places=6)

    def test_rotate_only_turns_a_child_in_place(self):
        a, b = self.walls(2)
        self.do("rel.link", ids=[b], parent=a, inherit=["rotate"])
        anchor = lambda: [(E.m(E.meta_of(self.p, b), "start")[i] + E.m(E.meta_of(self.p, b), "end")[i]) / 2 for i in (0, 1)]
        before = anchor()
        self.do("model.rotate", ids=[a], centre=[0, 0], angle=90)
        self.assertAlmostEqual(anchor()[0], before[0], places=6)
        self.assertAlmostEqual(anchor()[1], before[1], places=6)
        meta = E.meta_of(self.p, b)
        self.assertAlmostEqual(abs(E.m(meta, "end")[0] - E.m(meta, "start")[0]), 0.0, places=6)                      # it now runs along y

    def test_scaling_is_refused_not_pretended(self):
        a, b = self.walls(2)
        with self.assertRaises(BadRequest):
            self.do("rel.link", ids=[b], parent=a, inherit=["translate", "scale"])


class Validation(Case):
    def test_cycles_and_self_links_are_refused_and_change_nothing(self):
        a, b, c = self.walls(3)
        self.do("rel.chain", ids=[a, b, c])
        saved = self.p.manifest["relationships"]["links"].copy()
        for ids, parent in (([a], c), ([a], b), ([b], b), ([c], c)):
            with self.assertRaises(BadRequest):
                self.do("rel.link", ids=ids, parent=parent)
        self.assertEqual(self.p.manifest["relationships"]["links"], saved)

    def test_a_failed_batch_leaves_no_partial_link(self):
        a, b = self.walls(2)
        with self.assertRaises(NotFound):
            self.do("rel.link", ids=[b, "does-not-exist"], parent=a)
        self.assertNotIn("relationships", self.p.manifest)

    def test_a_door_cannot_be_linked_because_it_already_follows_its_wall(self):
        w = self.wall(0)
        d = self.do("model.add_door", wall=w, offset=1.0, width=900, height=2100)["id"]
        other = self.wall(3000)
        with self.assertRaises(BadRequest) as ctx:
            self.do("rel.link", ids=[d], parent=other)
        self.assertIn("host wall", str(ctx.exception))

    def test_reparenting_replaces_the_old_parent(self):
        a, b, c = self.walls(3)
        self.do("rel.link", ids=[c], parent=a)
        out = self.do("rel.link", ids=[c], parent=b)
        self.assertEqual(out["reparented_from"], {c: a})
        self.assertEqual(RS.parent_of(self.p, c), b)
        self.assertEqual(RS.children_of(self.p, a), [])

    def test_a_chain_is_all_or_nothing(self):
        a, b, c = self.walls(3)
        with self.assertRaises(BadRequest):
            self.do("rel.chain", ids=[a, b, a])
        self.assertEqual(RS.descendants(self.p, a), [])


class Unlinking(Case):
    def test_unlink_keeps_world_position_and_geometry(self):
        t = self.tree()
        ids = list(t.values())
        before = self.snapshot(ids)
        volume = {i: self.p.model.volume(i, unit="m") for i in ids}
        self.do("rel.unlink", ids=[t["B"]])
        self.assertSame(before, self.snapshot(ids))
        self.assertEqual(volume, {i: self.p.model.volume(i, unit="m") for i in ids})
        self.assertIsNone(RS.parent_of(self.p, t["B"]))
        self.assertEqual(RS.parent_of(self.p, t["C"]), t["B"])                       # the branch below B stayed with B

    def test_after_unlinking_the_child_no_longer_follows(self):
        a, b = self.walls(2)
        self.do("rel.link", ids=[b], parent=a)
        self.do("rel.unlink", ids=[b])
        before = self.snapshot([b])
        self.do("model.move", id=a, dx=1, dy=1, dz=0)
        self.assertSame(before, self.snapshot([b]))

    def test_removing_an_intermediate_element_can_close_the_chain_or_release_its_children(self):
        t = self.tree()
        self.do("rel.unlink", ids=[t["B"]], descendants="reparent")
        self.assertEqual(RS.parent_of(self.p, t["C"]), t["A"])
        self.assertEqual(RS.parent_of(self.p, t["E"]), t["A"])
        self.assertIsNone(RS.parent_of(self.p, t["B"]))
        t2 = Case.tree(self)  # noqa: F841  (a second tree in the same project is fine)
        u = self.tree.__self__  # noqa: F841

    def test_release_frees_the_children_too(self):
        t = self.tree()
        self.do("rel.unlink", ids=[t["B"]], descendants="release")
        self.assertIsNone(RS.parent_of(self.p, t["C"]))
        self.assertIsNone(RS.parent_of(self.p, t["E"]))
        self.assertEqual(RS.parent_of(self.p, t["D"]), t["C"])                       # grandchildren still follow their own parent

    def test_inserting_into_a_chain_keeps_the_rest_of_it(self):
        a, b, x = self.walls(3)
        self.do("rel.link", ids=[b], parent=a)
        self.do("rel.attach", ids=[x], after=a, adopt=[b])
        self.assertEqual(RS.parent_of(self.p, x), a)
        self.assertEqual(RS.parent_of(self.p, b), x)

    def test_deleting_an_intermediate_element_hands_its_children_to_its_parent(self):
        t = self.tree()
        out = self.do("model.delete", id=t["B"])
        self.assertTrue(out["notes"])
        self.assertEqual(RS.parent_of(self.p, t["C"]), t["A"])
        self.assertEqual(RS.parent_of(self.p, t["E"]), t["A"])
        self.assertEqual(RS.validate(self.p), [])

    def test_deleting_a_root_releases_its_children_and_leaves_nothing_dangling(self):
        a, b = self.walls(2)
        self.do("rel.link", ids=[b], parent=a)
        self.do("model.delete", id=a)
        self.assertNotIn("relationships", self.p.manifest)


class Groups(Case):
    def test_a_group_is_selected_and_moved_together_and_each_member_once(self):
        a, b, c = self.walls(3)
        g = self.do("rel.group", ids=[a, b])["group"]
        before = self.snapshot([a, b, c])
        moved = self.do("model.move", id=a, dx=1, dy=0, dz=0)["moved"]
        self.assertEqual(sorted(moved), sorted([a, b]))
        after = self.snapshot([a, b, c])
        self.assertShifted(before, after, [a, b], 1, 0)
        self.assertSame({c: before[c]}, {c: after[c]})
        self.assertEqual(RS.group_members(self.p, g), [a, b])

    def test_mixed_categories_group_and_keep_their_identity_and_properties(self):
        w = self.wall(0)
        f = self.do("model.add_floor", points=[[0, 0], [3000, 0], [3000, 3000], [0, 3000]], level=self.level, thickness=200)["id"]
        meta_before = {i: E.meta_of(self.p, i) for i in (w, f)}
        mesh_before = {i: self.p.model.volume(i, unit="m") for i in (w, f)}
        g = self.do("rel.group", ids=[w, f], name="Shell")["group"]
        self.assertEqual({i: E.meta_of(self.p, i) for i in (w, f)}, meta_before)           # nothing about the elements changed
        self.assertIsNone(RS.parent_of(self.p, w))                                          # a group is not a dependency
        self.do("rel.ungroup", groups=[g])
        self.assertEqual({i: self.p.model.volume(i, unit="m") for i in (w, f)}, mesh_before)
        self.assertNotIn("relationships", self.p.manifest)

    def test_one_member_can_be_edited_on_its_own(self):
        a, b = self.walls(2)
        self.do("rel.group", ids=[a, b])
        before = self.snapshot([a, b])
        self.do("model.move", id=a, dx=0, dy=0.5, dz=0, scope="member")
        after = self.snapshot([a, b])
        self.assertShifted(before, after, [a], 0, 0.5)
        self.assertSame({b: before[b]}, {b: after[b]})

    def test_groups_nest_and_selecting_a_member_takes_the_outermost_group(self):
        a, b, c = self.walls(3)
        inner = self.do("rel.group", ids=[a, b], name="Inner")["group"]
        outer = self.do("rel.group", ids=[a, c], name="Outer")["group"]               # a is in Inner: the unit is Inner as a whole
        self.assertEqual(sorted(RS.group_members(self.p, outer)), sorted([a, b, c]))
        self.assertEqual(RS.outermost_group(self.p, a), outer)
        moved = self.do("model.move", id=b, dx=1, dy=0, dz=0)["moved"]
        self.assertEqual(sorted(moved), sorted([a, b, c]))
        self.do("rel.ungroup", groups=[outer])                                         # one level only
        self.assertEqual(RS.group_of(self.p, a), inner)
        self.assertIsNone(RS.group_of(self.p, c))

    def test_grouping_inside_a_group_creates_a_nested_group(self):
        a, b, c = self.walls(3)
        outer = self.do("rel.group", ids=[a, b, c])["group"]
        inner = self.do("rel.group", ids=[a, b], within=outer)["group"]
        self.assertEqual(RS.group_of(self.p, a), inner)
        self.assertEqual(RS.group_of(self.p, c), outer)

    def test_grouping_needs_two_things(self):
        a = self.wall(0)
        with self.assertRaises(BadRequest):
            self.do("rel.group", ids=[a])

    def test_a_group_and_a_link_never_double_transform(self):
        a, b, c = self.walls(3)
        self.do("rel.link", ids=[b], parent=a)
        self.do("rel.group", ids=[a, b])                                               # b follows a AND is in a's group
        before = self.snapshot([a, b, c])
        moved = self.do("model.move", id=a, dx=2, dy=0, dz=0)["moved"]
        self.assertEqual(sorted(moved), sorted([a, b]))
        self.assertShifted(before, self.snapshot([a, b, c]), [a, b], 2, 0)               # 2, not 4

    def test_link_followers_do_not_drag_their_group_mates(self):
        a, b, c = self.walls(3)
        self.do("rel.group", ids=[b, c])
        self.do("rel.link", ids=[b], parent=a)
        before = self.snapshot([a, b, c])
        self.do("model.move", id=a, dx=1, dy=0, dz=0)
        after = self.snapshot([a, b, c])
        self.assertShifted(before, after, [a, b], 1, 0)
        self.assertSame({c: before[c]}, {c: after[c]})

    def test_a_wall_and_its_door_in_one_group_move_once(self):
        w = self.wall(0)
        d = self.do("model.add_door", wall=w, offset=1.0, width=900, height=2100)["id"]
        other = self.wall(2000)
        self.do("rel.group", ids=[w, d, other])
        before = E.m(E.meta_of(self.p, d), "offset")
        self.do("model.move", id=other, dx=1, dy=0, dz=0)                               # the whole group, door included
        self.assertAlmostEqual(E.m(E.meta_of(self.p, d), "offset"), before, places=9)       # it travelled with its wall only; its offset on the wall is unchanged

    def test_group_pivot_defaults_to_the_centre_and_can_be_set(self):
        a, b = self.walls(2)
        g = self.do("rel.group", ids=[a, b])["group"]
        centre = RS.group_pivot(self.p, g)
        self.assertAlmostEqual(centre[0], 2.0, places=6)
        self.assertAlmostEqual(centre[1], 0.5, places=6)
        self.do("rel.set_group", group=g, pivot=[1, 1, 0], name="Pair")
        self.assertEqual(RS.group_pivot(self.p, g), [1.0, 1.0, 0.0])
        self.do("rel.set_group", group=g, pivot=None)
        self.assertAlmostEqual(RS.group_pivot(self.p, g)[0], 2.0, places=6)

    def test_rotating_a_group_turns_every_member_once(self):
        a, b = self.walls(2)
        self.do("rel.group", ids=[a, b])
        before = self.snapshot([a, b])
        self.do("model.rotate", ids=[a], centre=[0, 0], angle=90)
        after = self.snapshot([a, b])
        for i in (a, b):
            self.assertAlmostEqual(math.dist(*before[i]), math.dist(*after[i]), places=9)
            self.assertNotEqual(before[i], after[i])


class AttachDetach(Case):
    def test_attach_to_group_and_to_parent_keep_placement(self):
        a, b, c = self.walls(3)
        g = self.do("rel.group", ids=[a, b])["group"]
        before = self.snapshot([a, b, c])
        self.do("rel.attach", ids=[c], group=g)
        self.do("rel.attach", ids=[c], parent=a)
        self.assertSame(before, self.snapshot([a, b, c]))
        self.assertEqual(RS.group_of(self.p, c), g)
        self.assertEqual(RS.parent_of(self.p, c), a)

    def test_detach_distinguishes_group_and_link(self):
        a, b, c = self.walls(3)
        g = self.do("rel.group", ids=[a, b, c])["group"]
        self.do("rel.link", ids=[b], parent=a)
        self.do("rel.detach", ids=[b], scope="link")
        self.assertIsNone(RS.parent_of(self.p, b))
        self.assertEqual(RS.group_of(self.p, b), g)                                    # still grouped
        self.do("rel.link", ids=[b], parent=a)
        self.do("rel.detach", ids=[b], scope="group")
        self.assertIsNone(RS.group_of(self.p, b))
        self.assertEqual(RS.parent_of(self.p, b), a)                                   # still linked

    def test_detach_a_branch_or_only_the_element(self):
        t = self.tree()
        self.do("rel.detach", ids=[t["B"]], scope="link", descendants="branch")
        self.assertEqual(set(RS.descendants(self.p, t["B"])), {t["C"], t["D"], t["E"]})
        t2 = self.tree()
        self.do("rel.detach", ids=[t2["B"]], scope="link", descendants="reparent")
        self.assertEqual(RS.parent_of(self.p, t2["C"]), t2["A"])

    def test_detaching_something_not_attached_says_so(self):
        a = self.wall(0)
        with self.assertRaises(BadRequest):
            self.do("rel.detach", ids=[a])

    def test_an_element_is_in_one_group_and_attach_reports_where_it_left(self):
        a, b, c = self.walls(3)
        g1 = self.do("rel.group", ids=[a, b])["group"]
        g2 = self.do("rel.group", ids=[c, self.wall(9000)])["group"]
        out = self.do("rel.attach", ids=[b], group=g2)
        self.assertEqual(out["left_group"], {b: g1})
        self.assertEqual(RS.group_of(self.p, b), g2)


class Transactions(Case):
    def test_undo_and_redo_take_relationships_with_the_model(self):
        a, b = self.walls(2)
        self.do("rel.link", ids=[b], parent=a)
        self.p.undo()
        self.assertNotIn("relationships", self.p.manifest)
        self.p.redo()
        self.assertEqual(RS.parent_of(self.p, b), a)
        self.do("rel.group", ids=[a, b])
        self.p.undo()
        self.assertIsNone(RS.group_of(self.p, a))

    def test_a_move_that_fails_rolls_everything_back(self):
        a, b = self.walls(2)
        w = self.wall(5000)
        d = self.do("model.add_door", wall=w, offset=1.0, width=900, height=2100)["id"]
        self.do("rel.group", ids=[a, d])
        before = self.snapshot([a, b])
        with self.assertRaises(BadRequest):                                             # a door turns with its wall: the group turn is refused as a whole
            self.do("model.rotate", ids=[a], centre=[0, 0], angle=20)
        self.assertSame(before, self.snapshot([a, b]))

    def test_projects_that_never_use_relationships_are_unchanged(self):
        a = self.wall(0)
        self.do("model.move", id=a, dx=1, dy=0, dz=0)
        self.do("model.rotate", ids=[a], centre=[0, 0], angle=10)
        self.assertNotIn("relationships", self.p.manifest)


class Persistence(Case):
    def test_everything_survives_save_and_reopen(self):
        t = self.tree()
        g = self.do("rel.group", ids=[t["E"], t["F"]], name="Pair")["group"]
        self.do("rel.set_inherit", ids=[t["C"]], inherit=["translate"])
        target = self.dir / "R.spadra"
        self.p.save(target)
        q = Project.open(target)
        self.assertEqual(RS.descendants(q, t["A"]), RS.descendants(self.p, t["A"]))
        self.assertEqual(RS.parent_of(q, t["D"]), t["C"])
        self.assertEqual(q.manifest["relationships"]["links"][t["C"]]["inherit"], ["translate"])
        self.assertEqual(RS.group_members(q, g), [t["E"], t["F"]])
        self.assertEqual(RS.validate(q), [])
        q.execute("model.move", {"id": t["A"], "dx": 1, "dy": 0, "dz": 0})              # and they still drive the moved model
        self.assertAlmostEqual(E.m(E.meta_of(q, t["D"]), "start")[0], 1.0, places=9)

    def test_an_old_project_without_relationships_opens_and_works(self):
        a, b = self.walls(2)
        target = self.dir / "Old.spadra"
        self.p.save(target)
        q = Project.open(target)
        self.assertNotIn("relationships", q.manifest)
        q.execute("rel.link", {"ids": [b], "parent": a})
        self.assertEqual(RS.parent_of(q, b), a)

    def test_a_file_edited_by_hand_cannot_leave_a_cycle_or_a_dangling_link(self):
        a, b = self.walls(2)
        self.p.manifest["relationships"] = {"links": {a: {"parent": b, "inherit": ["translate", "rotate"]}, b: {"parent": a, "inherit": ["translate", "rotate"]},
                                                      "gone": {"parent": a, "inherit": ["translate"]}}, "groups": []}
        notes = RS.reconcile(self.p)
        self.assertTrue(notes)
        self.assertEqual(RS.validate(self.p), [])
        for oid in (a, b):                                                              # no cycle: walking up always ends
            self.assertLess(len(RS.ancestors(self.p, oid)), 3)


class Properties(Case):
    def test_properties_show_the_relationships(self):
        a, b = self.walls(2)
        self.do("rel.link", ids=[b], parent=a)
        self.do("rel.group", ids=[a, b], name="Pair")
        rows = {r["label"]: r for r in RS.rows(self.p, b)}
        self.assertEqual(rows["Follows"]["target"], {"select": {"model": a}})
        self.assertIn("Pair", rows["Group"]["value"])
        shown = self.p.properties({"model": a})
        titles = [g["title"] for g in shown["groups"]]
        self.assertIn("Relationships", titles)

    def test_highlight_names_every_role(self):
        t = self.tree()
        h = RS.highlight(self.p, [t["B"]])
        self.assertEqual(h["roles"][t["A"]], "parent")
        self.assertEqual(h["roles"][t["C"]], "child")
        self.assertEqual(h["roles"][t["D"]], "descendant")
        self.assertEqual(h["roles"][t["B"]], "selected")
        self.assertNotIn(t["F"], h["roles"])
        self.assertIn([t["A"], t["B"]], h["edges"])


class Scale(Case):
    def test_a_large_hierarchy_evaluates_quickly_and_deterministically(self):
        ids = [f"e{i}" for i in range(20000)]
        links = {ids[i]: {"parent": ids[(i - 1) // 3], "inherit": ["translate", "rotate"]} for i in range(1, len(ids))}
        self.p.manifest["relationships"] = {"links": links, "groups": []}
        t0 = time.perf_counter()
        first = RS.descendants(self.p, ids[0])
        elapsed = time.perf_counter() - t0
        self.assertEqual(len(first), 19999)
        self.assertEqual(first, RS.descendants(self.p, ids[0]))
        self.assertLess(elapsed, 1.0)
        self.assertEqual(len(RS.ancestors(self.p, ids[-1])), 9)


if __name__ == "__main__":
    unittest.main()
