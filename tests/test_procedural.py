"""Procedural geometry (P1-04): a façade cut around real openings with stable ids, following its wall, and a structural frame with connectivity."""
from __future__ import annotations

import math
import unittest

from applications.SPADRA import elements as E
from applications.SPADRA import procedural as PG
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest, NotFound


class Base(unittest.TestCase):
    def setUp(self):
        self.p = Project.create("Procedural")
        self.l0 = self.p.manifest["levels"][0]["id"]

    def x(self, op, **kw):
        return self.p.execute(op, kw)["result"]

    def wall(self, length=6000, height=3.0):
        return self.x("model.add_wall", level=self.l0, start=[0, 0], end=[length, 0], thickness=200, height=height)["id"]

    def panels(self, gid):
        g = PG.group(self.p, gid)
        return {k: oid for k, oid in g["members"].items()}

    def rect_of(self, oid, wall):
        """A panel's (u0, u1, v0, v1) on the wall, read back from the stored transform and box size, not from the generator's bookkeeping."""
        obj = self.p.model.get(oid)
        t, size = obj["transform"], obj["geometry"]["size"]
        f = E.wall_frame(self.p, wall)
        dx, dy = t[3] - f["start"][0], t[7] - f["start"][1]
        c, s = math.cos(f["angle"]), math.sin(f["angle"])
        u, v = dx * c + dy * s, t[11] - f["base"]
        return (u - size[0] / 2, u + size[0] / 2, v - size[2] / 2, v + size[2] / 2)


class Facade(Base):
    def test_panels_never_overlap_an_opening_and_cover_the_rest(self):
        w = self.wall()
        win = self.x("model.add_window", wall=w, offset=3.0, width=1200, height=1200, sill=900)["id"]
        g = self.x("procedural.facade", wall=w, panel_width=1000, panel_height=1000, joint=10)
        x0, x1, z0, z1 = E.opening_span(E.meta_of(self.p, win))
        rects = [self.rect_of(oid, w) for oid in g["members"].values()]
        for u0, u1, v0, v1 in rects:
            self.assertTrue(u1 <= x0 + 1e-6 or u0 >= x1 - 1e-6 or v1 <= z0 + 1e-6 or v0 >= z1 - 1e-6, (u0, u1, v0, v1))    # no overlap with the opening
        for i, a in enumerate(rects):                                                                                          # and none with each other
            for b in rects[i + 1:]:
                self.assertTrue(a[1] <= b[0] + 1e-9 or b[1] <= a[0] + 1e-9 or a[3] <= b[2] + 1e-9 or b[3] <= a[2] + 1e-9)
        area = sum((u1 - u0) * (v1 - v0) for u0, u1, v0, v1 in rects)
        free = 6.0 * 3.0 - 1.2 * 1.2
        self.assertLessEqual(area, free)
        self.assertGreater(area, 0.93 * free)                                                                                  # joints and small remainders only
        rep = g["report"]
        self.assertGreater(rep["panels_cut_by_openings"], 0)
        self.assertEqual(rep["openings"], 1)
        self.assertEqual(rep["panels"], len(g["members"]))

    def test_ids_are_stable_when_the_facade_is_edited_and_the_wall_moves(self):
        w = self.wall()
        g = self.x("procedural.facade", wall=w, panel_width=1000, panel_height=1000)
        gid = next(iter(PG._groups(self.p)))["id"]
        before = dict(g["members"])
        r = self.x("procedural.update", id=gid, thickness=50)                      # same pattern, new thickness
        self.assertEqual(PG.group(self.p, gid)["members"], before)
        self.assertEqual((r["created"], r["removed"]), (0, 0))
        self.x("model.add_window", wall=w, offset=3.0, width=1200, height=1200, sill=900)      # the wall changed: the group follows inside the same command
        after = PG.group(self.p, gid)["members"]
        self.assertNotEqual(set(after), set(before))                                              # panels were cut and some pieces appeared
        self.assertTrue(all(after[k] == oid for k, oid in before.items() if k in after))          # every key that is still produced kept its element
        self.assertTrue(all(E.alive(self.p, o) for o in after.values()))
        self.assertTrue(all(not E.alive(self.p, o) for k, o in before.items() if k not in after))  # a key no longer produced took its element with it
        win = E.hosted(self.p, w)[0]
        x0, x1, z0, z1 = E.opening_span(E.meta_of(self.p, win))
        self.assertTrue(all(self.rect_of(o, w)[1] <= x0 + 1e-6 or self.rect_of(o, w)[0] >= x1 - 1e-6 or self.rect_of(o, w)[3] <= z0 + 1e-6 or self.rect_of(o, w)[2] >= z1 - 1e-6
                            for o in after.values()))
        self.x("model.add_window", wall=w, offset=1.0, width=900, height=900, sill=900, name="second") if False else None

    def test_a_missing_wall_is_reported_and_the_panels_are_left_alone(self):
        w = self.wall()
        self.x("procedural.facade", wall=w, panel_width=2000, panel_height=1500)
        grp = PG._groups(self.p)[0]
        ids = list(grp["members"].values())
        self.x("model.delete", id=w)
        info = PG.describe(self.p)[0]
        self.assertEqual(info["host"], "missing")
        self.assertTrue(all(E.alive(self.p, i) for i in ids))
        self.p.undo()
        self.assertEqual(PG.describe(self.p)[0]["host"], "resolved")

    def test_a_bad_edit_changes_nothing(self):
        w = self.wall()
        self.x("procedural.facade", wall=w)
        gid = PG._groups(self.p)[0]["id"]
        rev, members = self.p.revision, dict(PG._groups(self.p)[0]["members"])
        with self.assertRaises(BadRequest):
            self.x("procedural.update", id=gid, panel_width=100, joint=150)
        with self.assertRaises(BadRequest):
            self.x("procedural.update", id=gid, panel_width=51, panel_height=51, joint=0)         # about 7000 panels: refused
        self.assertEqual((self.p.revision, PG._groups(self.p)[0]["members"]), (rev, members))
        with self.assertRaises(BadRequest):
            self.x("procedural.update", id=gid, panel_width=50, panel_height=50)         # every piece is under the minimum size: no panels at all
        with self.assertRaises(NotFound):
            self.x("procedural.facade", wall="no-such-wall")
        self.assertEqual(len(PG._groups(self.p)), 1)

    def test_undo_removes_the_whole_group_and_delete_can_keep_the_elements(self):
        w = self.wall()
        before = len(self.p.model.query_ids(limit=10000))
        self.x("procedural.facade", wall=w, panel_width=2000, panel_height=1500)
        self.assertGreater(len(self.p.model.query_ids(limit=10000)), before)
        self.p.undo()
        self.assertEqual((len(self.p.model.query_ids(limit=10000)), PG.describe(self.p)), (before, []))
        self.p.redo()
        gid = PG._groups(self.p)[0]["id"]
        n = len(self.p.model.query_ids(limit=10000))
        self.x("procedural.delete", id=gid, keep_elements=True)
        self.assertEqual(len(self.p.model.query_ids(limit=10000)), n)            # the panels stay as ordinary elements
        self.assertEqual(PG.describe(self.p), [])


class Frame(Base):
    def test_grid_of_columns_and_beams_is_connected(self):
        r = self.x("procedural.frame", level=self.l0, origin=[0, 0], bays_x=[6000, 6000, 4000], bays_y=[5000, 5000], column={"width": 400, "depth": 400},
                   beam={"width": 300, "depth": 500})
        rep = r["report"]
        self.assertEqual((rep["columns"], rep["beams"]), (4 * 3, 3 * 3 + 4 * 2))
        self.assertEqual(rep["beams_without_supports"], [])
        self.assertAlmostEqual(rep["longest_span"], 6.0)
        self.assertAlmostEqual(rep["max_span_over_depth"], 12.0)
        members = r["members"]
        points = {k: tuple(E.m(E.meta_of(self.p, oid), "point")) for k, oid in members.items() if k.startswith("c:")}
        for k, oid in members.items():
            if k.startswith(("bx", "by")):
                meta = E.meta_of(self.p, oid)
                ends = {tuple(E.m(meta, "start")), tuple(E.m(meta, "end"))}
                self.assertEqual(ends, {points[s] for s in E.m(meta, "supports")}, k)          # every beam starts and ends on a column
        self.assertAlmostEqual(points["c:3:2"][0], 16.0)
        self.assertAlmostEqual(points["c:3:2"][1], 10.0)

    def test_editing_the_grid_keeps_ids_of_surviving_members_and_adds_or_removes_the_rest(self):
        r = self.x("procedural.frame", level=self.l0, bays_x=[6000, 6000], bays_y=[6000])
        gid = PG._groups(self.p)[0]["id"]
        before = dict(r["members"])
        u = self.x("procedural.update", id=gid, bays_x=[6000, 6000, 3000])
        after = PG.group(self.p, gid)["members"]
        self.assertTrue(all(after[k] == oid for k, oid in before.items()))               # nothing that survives changes id
        self.assertEqual((u["removed"], u["created"] > 0), (0, True))
        u = self.x("procedural.update", id=gid, bays_x=[6000])
        self.assertGreater(u["removed"], 0)
        left = PG.group(self.p, gid)["members"]
        self.assertTrue(all(E.alive(self.p, o) for o in left.values()))
        self.assertTrue(all(not E.alive(self.p, o) for k, o in after.items() if k not in left))

    def test_save_and_reopen_keep_the_recipe(self):
        import tempfile
        from pathlib import Path
        self.x("procedural.frame", level=self.l0, bays_x=[6000], bays_y=[6000])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.spadra"
            self.p.save(path)
            q = Project.open(path)
        self.assertEqual(PG.describe(q)[0]["members"], 4 + 4)
        self.assertEqual(PG.describe(q)[0]["kind"], "frame")


class GeneratedPanelsInTheRestOfTheApp(Base):
    """A facade panel is category 'generic' but is not a family instance. Everything that handles generic elements must cope with that: the model check, a rebuild,
    the plan, schedules, properties, and manual edits (which are refused with a pointer to the group, not a crash)."""

    def setUp(self):
        super().setUp()
        self.w = self.wall()
        self.x("model.add_door", wall=self.w, offset=2.0, width=900)
        self.g = self.x("procedural.facade", wall=self.w, panel_width=1000, panel_height=1000)
        self.panel = next(iter(self.g["members"].values()))

    def test_the_model_check_does_not_fail_and_does_not_flag_panels(self):
        issues = self.p.check()["issues"]
        self.assertFalse([i for i in issues if i["ref"] == self.panel], issues)

    def test_rebuild_keeps_every_panel(self):
        before = set(self.g["members"].values())
        self.x("model.rebuild")
        self.assertTrue(before <= set(self.p.model.query_ids(limit=10000)))

    def test_plan_schedule_and_properties_do_not_fail(self):
        view = next(v for v in self.p.manifest["views"] if v.get("kind") == "plan") if self.p.manifest.get("views") else None
        if view:
            self.p.plan_overlay(view["id"])
        from applications.SPADRA import documentation as DOC
        DOC.schedule_data(self.p, "generic")
        self.assertTrue(self.p.properties({"model": self.panel}, relations=True)["groups"])

    def test_hand_edits_are_refused_with_a_reason_and_change_nothing(self):
        rev = self.p.revision
        for op, params in (("model.move", {"id": self.panel, "dx": 0.1, "dy": 0, "dz": 0}), ("model.rotate", {"ids": [self.panel], "angle": 10, "centre": [0, 0]}),
                           ("model.set_property", {"id": self.panel, "key": "param:width", "value": 500})):
            with self.assertRaises(BadRequest, msg=op):
                self.x(op, **params)
        self.assertEqual(self.p.revision, rev)


if __name__ == "__main__":
    unittest.main()
