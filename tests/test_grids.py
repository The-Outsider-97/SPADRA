"""Editable grid lines against the real SGEO engine: end points, length, angle and label change in one step, labels stay unique, a grid is one element that every
view shows (plans and the sections and elevations it meets), and every change is atomic, undoable and survives save and reopen."""
from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from applications.SPADRA import datums as DT
from applications.SPADRA import documentation as D
from applications.SPADRA import elements as E
from applications.SPADRA.project import NS, Project
from applications.tools.errors import BadRequest, Conflict


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project.create("Grids")
        self.l1 = self.p.manifest["levels"][0]["id"]

    def run_(self, op, **params):
        return self.p.execute(op, params)["result"]

    def grid(self, a, b, **extra):
        return self.run_("model.add_grid", start=a, end=b, **extra)["id"]

    def meta(self, oid):
        return self.p.model.get(oid)["metadata"]

    def line(self, oid):
        return self.meta(oid)[f"{NS}:start"], self.meta(oid)[f"{NS}:end"]


class Editing(Case):
    def test_end_points_are_set_in_drawing_units_in_one_step(self):
        g = self.grid([0, 0], [6000, 0])
        rev = self.p.revision
        self.run_("model.set_grid", id=g, start=[500, 0], end=[6000, 4000])
        self.assertEqual(self.line(g), ([0.5, 0.0], [6.0, 4.0]))
        self.assertEqual(self.p.revision, rev + 1)
        self.p.undo()
        self.assertEqual(self.line(g), ([0.0, 0.0], [6.0, 0.0]))

    def test_length_and_angle_keep_the_start_unless_told_which_point_to_hold(self):
        g = self.grid([1000, 1000], [4000, 1000])
        self.run_("model.set_grid", id=g, length=5000)
        self.assertEqual(self.line(g), ([1.0, 1.0], [6.0, 1.0]))
        self.run_("model.set_grid", id=g, angle=90)
        s, e = self.line(g)
        self.assertAlmostEqual(e[0], 1.0, places=9)
        self.assertAlmostEqual(e[1], 6.0, places=9)
        self.run_("model.set_grid", id=g, length=2000, hold="end")
        s, e = self.line(g)
        self.assertAlmostEqual(math.dist(s, e), 2.0, places=9)
        self.assertEqual(e[1], 6.0)
        self.run_("model.set_grid", id=g, length=4000, angle=0, hold="middle")
        s, e = self.line(g)
        self.assertAlmostEqual((s[0] + e[0]) / 2, 1.0, places=9)
        self.assertAlmostEqual((s[1] + e[1]) / 2, 5.0, places=9)
        self.assertAlmostEqual(math.dist(s, e), 4.0, places=9)

    def test_bad_requests_are_refused_whole(self):
        g = self.grid([0, 0], [6000, 0])
        a = self.grid([0, 1000], [6000, 1000])
        before = (self.p.revision, self.line(g), self.p.model.get(g)["name"])
        for bad in (dict(start=[0, 0], end=[5, 0]),                       # 5 mm long
                    dict(length=0),
                    dict(start=[0, 0], end=[1000, 0], length=500),         # both forms at once
                    dict(hold="side", length=100),
                    dict(label=self.p.model.get(a)["name"]),               # taken
                    dict(bubble="middle"),
                    dict(),
                    dict(start=[1, 2, 3])):
            with self.subTest(bad=bad), self.assertRaises((BadRequest, Conflict)):
                self.run_("model.set_grid", id=g, **bad)
        self.assertEqual((self.p.revision, self.line(g), self.p.model.get(g)["name"]), before)

    def test_a_label_is_unique_and_a_rename_keeps_its_own(self):
        g = self.grid([0, 0], [6000, 0])
        h = self.grid([0, 1000], [6000, 1000])
        self.run_("model.set_grid", id=g, label="Axis 1")
        self.assertEqual(self.p.model.get(g)["name"], "Axis 1")
        self.run_("model.set_grid", id=g, label="Axis 1", bubble="both")        # its own label is not a duplicate
        with self.assertRaises(Conflict):
            self.run_("model.set_property", id=h, key="name", value="axis 1")    # the Properties panel path checks too, ignoring case
        self.assertNotEqual(self.p.model.get(h)["name"], "Axis 1")

    def test_properties_edit_start_end_length_angle_label_and_bubbles(self):
        g = self.grid([0, 0], [6000, 0])
        panel = self.p.properties({"model": g})
        keys = [r.get("key") for r in panel["groups"][0]["rows"]]
        self.assertEqual([k for k in keys if k], ["start_x", "start_y", "end_x", "end_y", "length", "angle", "bubble"])
        self.run_("model.set_property", id=g, key="length", value=8000)
        self.assertEqual(self.line(g)[1], [8.0, 0.0])
        self.run_("model.set_property", id=g, key="angle", value=45)
        self.assertAlmostEqual(self.line(g)[1][0], 8 * math.cos(math.pi / 4), places=9)
        self.run_("model.set_property", id=g, key="bubble", value="both")
        self.assertEqual(self.meta(g)[f"{NS}:bubble"], "both")
        self.run_("model.set_property", id=g, key="start_x", value=1000)
        self.assertEqual(self.line(g)[0], [1.0, 0.0])
        with self.assertRaises(BadRequest):
            self.run_("model.set_property", id=g, key="length", value=3)

    def test_move_and_rotate_act_on_the_same_element(self):
        g = self.grid([0, 0], [4000, 0])
        self.run_("model.move", id=g, dx=1.0, dy=2.0, dz=0)
        self.assertEqual(self.line(g), ([1.0, 2.0], [5.0, 2.0]))
        self.run_("model.rotate", ids=[g], centre=[1000, 2000], angle=90)
        s, e = self.line(g)
        self.assertAlmostEqual(e[0], 1.0, places=9)
        self.assertAlmostEqual(e[1], 6.0, places=9)
        self.assertEqual(len([o for o in E.element_ids(self.p) if E.category(self.p, o) == "grid"]), 1)


class Views(Case):
    def test_the_plan_item_carries_the_axis_for_grips_and_the_bubbles_asked_for(self):
        g = self.grid([0, 0], [6000, 0], bubble="both")
        item = next(i for i in E.plan_items(self.p, self.l1, 1000.0) if i["id"] == g)
        self.assertEqual(item["axis"], {"a": [0.0, 0.0], "b": [6000.0, 0.0], "thickness": 0.0})
        self.assertEqual(item["label"]["bubbles"], ["start", "end"])
        self.run_("model.set_grid", id=g, bubble="none")
        item = next(i for i in E.plan_items(self.p, self.l1, 1000.0) if i["id"] == g)
        self.assertEqual(item["label"]["bubbles"], [])

    def test_every_plan_shows_the_same_grid(self):
        self.run_("level.add", name="Level 2", elevation=3.0)
        g = self.grid([0, 0], [6000, 0])
        for level in self.p.manifest["levels"]:
            self.assertIn(g, [i["id"] for i in E.plan_items(self.p, level["id"], 1000.0)])

    def test_a_section_shows_the_grids_it_cuts_where_they_cross_the_cut_plane_and_follows_edits(self):
        across = self.grid([2000, -1000], [2000, 5000])                      # runs along the view direction: shown end-on
        along = self.grid([0, 1500], [6000, 1500])                          # parallel to the cut line: not shown
        vid = self.run_("view.add_section", a=[0, 0], b=[6000, 0])["id"]
        marks = D.project_view(self.p, self.p.view(vid), 1000.0)["grid_marks"]
        self.assertEqual([(m["id"], m["x"]) for m in marks], [(across, 2000.0)])
        self.assertNotIn(along, [m["id"] for m in marks])
        self.run_("model.move", id=across, dx=1.5, dy=0, dz=0)
        self.run_("model.set_grid", id=across, label="K")
        marks = D.project_view(self.p, self.p.view(vid), 1000.0)["grid_marks"]
        self.assertEqual([(m["name"], m["x"]) for m in marks], [("K", 3500.0)])
        self.run_("model.set_grid", id=across, start=[2000, 1000], end=[2000, 5000])       # no longer reaches the cut line
        self.assertEqual(D.project_view(self.p, self.p.view(vid), 1000.0)["grid_marks"], [])

    def test_an_elevation_shows_grids_that_face_it_at_their_near_end(self):
        g = self.grid([3000, 0], [3000, 8000])
        view = next(v for v in self.p.manifest["views"] if v.get("side") == "south")        # looks along +y... from the south
        marks = D.project_view(self.p, view, 1000.0)["grid_marks"]
        self.assertEqual([m["id"] for m in marks], [g])
        self.grid([0, 2000], [6000, 2000])                                                   # parallel to the picture
        self.assertEqual(len(D.project_view(self.p, view, 1000.0)["grid_marks"]), 1)


class Lifecycle(Case):
    def test_it_survives_save_and_reopen_and_the_deleted_grid_leaves_the_views(self):
        g = self.grid([0, 0], [6000, 0], bubble="end")
        self.run_("model.set_grid", id=g, label="B", length=7000)
        with tempfile.TemporaryDirectory() as tmp:
            self.p.save(Path(tmp) / "g.spadra")
            q = Project.open(Path(tmp) / "g.spadra")
            self.assertEqual(q.model.get(g)["name"], "B")
            self.assertEqual(q.model.get(g)["metadata"][f"{NS}:end"], [7.0, 0.0])
            self.assertEqual(q.model.get(g)["metadata"][f"{NS}:bubble"], "end")
        self.run_("model.delete", id=g)
        self.assertNotIn(g, [i["id"] for i in E.plan_items(self.p, self.l1, 1000.0)])

    def test_a_grid_made_before_bubbles_existed_still_shows_its_start_bubble(self):
        g = self.grid([0, 0], [6000, 0])
        E._attach(self.p, g, {"bubble": None})                                 # as an older file would have it
        self.assertEqual(DT.bubbles_of(self.meta(g)), "start")
        item = next(i for i in E.plan_items(self.p, self.l1, 1000.0) if i["id"] == g)
        self.assertEqual(item["label"]["bubbles"], ["start"])
        self.assertEqual(self.p.check()["issues"], [])


if __name__ == "__main__":
    unittest.main()
