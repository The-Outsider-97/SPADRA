"""Persistent parametric arrays against the real SGEO engine: X / Y / Z / XYZ and radial patterns, count / spacing / direction regeneration, linked and
independent items, hosted openings, deleting and releasing, and that every change is atomic, undoable and survives save and reopen."""
from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from .. import arrays as AY
from .. import elements as E
from ..project import Project
from ...tools.errors import BadRequest, NotFound
from .test_wall_joins import Joints


class Case(Joints):
    def run_(self, op, **p):
        return self.p.execute(op, p)["result"]

    def column(self, at=(0, 0), **extra):
        return self.run_("model.add_column", level=self.level, point=list(at), base=0, top=3, width=300, depth=300, **extra)["id"]

    def array(self, ids, **p):
        return self.run_("model.array", ids=ids if isinstance(ids, list) else [ids], **p)

    def at(self, oid):
        return tuple(E.m(self.meta(oid), "point"))

    def records(self):
        return AY.array_ids(self.p)

    def items(self, rid):
        return [s for s in E.m(self.meta(rid), "slots")]

    def columns(self):
        return [i for i in E.element_ids(self.p) if E.category(self.p, i) == "structural" and E.is_column(self.meta(i))]

    def points(self):
        return sorted(self.at(i) for i in self.columns())


class Directions(Case):
    def test_x_y_and_z_arrays_put_items_where_the_spacing_says(self):
        c = self.column()
        r = self.array(c, dimension="X", counts=[4], spacings=[2000])
        self.assertEqual(r["count"], 4)
        self.assertEqual(len(r["ids"]), 3)
        self.assertEqual(self.points(), [(0.0, 0.0), (2.0, 0.0), (4.0, 0.0), (6.0, 0.0)])
        self.assertEqual(self.meta(r["array"])["spadra:category"], "array")
        d = self.column((0, 5000))
        self.array(d, dimension="Y", counts=[3], spacings=[1500])
        self.assertEqual([p for p in self.points() if p[0] == 0.0 and p[1] >= 5.0], [(0.0, 5.0), (0.0, 6.5), (0.0, 8.0)])
        e = self.column((20000, 0))
        z = self.array(e, dimension="Z", counts=[3], spacings=[3000])
        tops = sorted((E.m(self.meta(i), "base"), E.m(self.meta(i), "top")) for i in [e, *z["ids"]])
        self.assertEqual(tops, [(0.0, 3.0), (3.0, 6.0), (6.0, 9.0)])              # a column stacked in Z keeps its height

    def test_xyz_is_a_three_dimensional_pattern_with_every_combination(self):
        c = self.column()
        r = self.array(c, dimension="XYZ", counts=[2, 3, 2], spacings=[1000, 2000, 3000])
        self.assertEqual((r["count"], len(r["ids"])), (12, 11))
        got = sorted((*self.at(i), E.m(self.meta(i), "base")) for i in [c, *r["ids"]])
        want = sorted((x, y, z) for x in (0.0, 1.0) for y in (0.0, 2.0, 4.0) for z in (0.0, 3.0))
        self.assertEqual(got, want)

    def test_a_direction_can_be_an_angle_or_a_vector_and_the_old_form_still_works(self):
        c = self.column()
        r = self.array(c, axes=[{"count": 3, "spacing": 1000, "direction": 90}])
        self.assertEqual([self.at(i) for i in r["ids"]][0][0], 0.0)
        self.assertAlmostEqual(self.at(r["ids"][1])[1], 2.0, places=9)
        d = self.column((10000, 0))
        r2 = self.array(d, axes=[{"count": 2, "spacing": 1000, "direction": [1, 1, 0]}])
        self.assertAlmostEqual(self.at(r2["ids"][0])[0], 10.0 + 1000 / 1000 / math.sqrt(2), places=9)
        e = self.column((0, 30000))
        r3 = self.array(e, mode="linear", count=3, step=[0, 500])
        self.assertEqual([self.at(i)[1] for i in r3["ids"]], [30.5, 31.0])
        self.assertTrue(r3["array"])

    def test_two_axes_make_a_grid_of_items(self):
        c = self.column()
        r = self.array(c, axes=[{"count": 3, "spacing": 2000, "direction": "x"}, {"count": 2, "spacing": 3000, "direction": "y"}])
        self.assertEqual(r["count"], 6)
        self.assertEqual(self.points(), sorted((x, y) for x in (0.0, 2.0, 4.0) for y in (0.0, 3.0)))


class Regeneration(Case):
    def test_count_spacing_and_direction_regenerate_and_keep_the_items_that_did_not_change(self):
        c = self.column()
        r = self.array(c, dimension="X", counts=[3], spacings=[2000])
        rid = r["array"]
        first = r["ids"]
        out = self.run_("model.set_array", id=rid, dimension="X", counts=[5], spacings=[2000])
        self.assertEqual(self.points(), [(float(x), 0.0) for x in (0, 2, 4, 6, 8)])
        self.assertTrue(all(E.alive(self.p, i) for i in first), "the items that stayed in the pattern are the same elements")
        self.assertEqual(out["count"], 5)
        self.run_("model.set_array", id=rid, dimension="X", counts=[2], spacings=[2000])
        self.assertEqual(self.points(), [(0.0, 0.0), (2.0, 0.0)])
        self.assertTrue(E.alive(self.p, first[0]) and not E.alive(self.p, first[1]))
        self.run_("model.set_array", id=rid, axes=[{"count": 2, "spacing": 5000, "direction": 90}])
        self.assertEqual(self.points(), [(0.0, 0.0), (0.0, 5.0)])
        self.assertEqual(len(self.columns()), 2, "nothing is left behind")

    def test_properties_edit_count_spacing_and_direction(self):
        c = self.column()
        rid = self.array(c, dimension="X", counts=[3], spacings=[2000])["array"]
        rows = {r["key"]: r for g in E.properties(self.p, rid)["groups"] for r in g["rows"] if r.get("key")}
        self.assertEqual((rows["count_1"]["value"], rows["spacing_1"]["value"], rows["angle_1"]["value"]), (3, 2000.0, 0.0))
        self.run_("model.set_property", id=rid, key="count_1", value=4)
        self.run_("model.set_property", id=rid, key="spacing_1", value=1000)
        self.run_("model.set_property", id=rid, key="angle_1", value=90)
        self.assertEqual(self.points(), [(0.0, 0.0), (0.0, 1.0), (0.0, 2.0), (0.0, 3.0)])
        with self.assertRaises(BadRequest):
            self.run_("model.set_property", id=rid, key="size", value=3)

    def test_a_radial_array_regenerates_when_its_angle_count_or_centre_change(self):
        c = self.column((2000, 0))
        rid = self.array(c, mode="radial", count=4, centre=[0, 0], angle=360)["array"]
        self.assertEqual(len(self.columns()), 4)
        self.run_("model.set_array", id=rid, count=3, angle=90)
        pts = sorted(self.at(i) for i in self.columns())
        self.assertEqual(len(pts), 3)
        self.assertTrue(any(abs(p[0]) < 1e-9 and abs(p[1] - 2.0) < 1e-9 for p in pts), "the last item sits on the 90 degree line")
        self.run_("model.set_array", id=rid, centre=[1000, 0])
        self.assertTrue(any(abs(p[0] - 1.0) < 1e-9 and abs(p[1] - 1.0) < 1e-9 for p in (self.at(i) for i in self.columns())))
        rows = {r["key"]: r["value"] for g in E.properties(self.p, rid)["groups"] for r in g["rows"] if r.get("key")}
        self.assertEqual((rows["count"], rows["angle"]), (3, 90.0))


class Linking(Case):
    def test_a_linked_array_follows_its_source(self):
        c = self.column()
        r = self.array(c, dimension="X", counts=[3], spacings=[2000])
        self.run_("model.set_property", id=c, key="width", value=500)
        widths = {E.m(self.meta(i), "width") for i in self.columns()}
        self.assertEqual(widths, {0.5}, "every copy has the new width")
        self.run_("model.move", id=c, dx=1.0, dy=0, dz=0)
        self.assertEqual(self.points(), [(1.0, 0.0), (3.0, 0.0), (5.0, 0.0)])
        self.assertEqual(len(self.columns()), 3)
        self.assertEqual(self.items(r["array"])[0]["state"], "driven")

    def snapshot(self):
        """Everything an array's copies are, as numbers: where each element is and every vertex of its mesh in world space, so two states compare exactly."""
        from .. import documentation as D
        out = []
        for oid in sorted(E.element_ids(self.p), key=lambda i: (E.category(self.p, i), self.p.model.get(i)["name"], i)):
            try:
                verts, faces = D.world_mesh(self.p, oid)
            except Exception:
                continue
            out.append((E.category(self.p, oid), sorted((round(v[0], 9), round(v[1], 9), round(v[2], 9)) for v in verts), len(faces)))
        return sorted(out)

    def test_regeneration_is_deterministic_whatever_path_led_to_the_same_parameters(self):
        c = self.column()
        rid = self.array(c, dimension="X", counts=[3], spacings=[2000])["array"]
        reference = self.snapshot()
        self.run_("model.set_array", id=rid, dimension="X", counts=[5], spacings=[700])             # elsewhere ...
        self.run_("model.set_array", id=rid, axes=[{"count": 2, "spacing": 5000, "direction": 90}])
        self.run_("model.set_array", id=rid, dimension="X", counts=[3], spacings=[2000])            # ... and back
        self.assertEqual(self.snapshot(), reference)
        self.run_("model.set_property", id=c, key="width", value=500)
        self.run_("model.set_property", id=c, key="width", value=300)                               # the source edited and edited back
        self.assertEqual(self.snapshot(), reference)
        self.p.undo()
        self.p.undo()
        self.p.redo()
        self.p.redo()
        self.assertEqual(self.snapshot(), reference)

    def test_an_array_of_voids_cuts_a_floor_in_each_place_and_follows_count_and_spacing(self):
        floor = self.run_("model.add_floor", level=self.level, points=[[0, 0], [7000, 0], [7000, 5000], [0, 5000]], thickness=200)["id"]
        void = self.run_("model.add_extrusion", level=self.level, points=[[1000, 1000], [2000, 1000], [2000, 2000], [1000, 2000]], void=True)["id"]
        rid = self.array(void, dimension="X", counts=[3], spacings=[2000])["array"]
        vol = lambda: self.p.model.volume(floor, unit="m")
        self.assertAlmostEqual(vol(), 7.0 - 3 * 0.2, places=9)                                       # three separate 1 m² holes, 200 mm thick
        self.run_("model.set_array", id=rid, dimension="X", counts=[3], spacings=[500])             # the copies now overlap: their union is cut once
        self.assertAlmostEqual(vol(), 7.0 - 2.0 * 0.2, places=9)
        self.run_("model.set_array", id=rid, dimension="X", counts=[2], spacings=[2000])
        self.assertAlmostEqual(vol(), 7.0 - 2 * 0.2, places=9)
        self.run_("model.set_array", id=rid, dimension="X", counts=[3], spacings=[2800])             # the last copy reaches over the floor's edge: 0.4 m of it is cut as a notch
        self.assertAlmostEqual(vol(), 7.0 - (1.0 + 1.0 + 0.4) * 0.2, places=9)
        self.assertEqual(E.solid_status(self.p, floor).state, "solid")
        self.run_("model.set_array", id=rid, dimension="X", counts=[3], spacings=[2000])
        self.assertAlmostEqual(vol(), 7.0 - 3 * 0.2, places=9)
        self.run_("model.delete", id=rid)
        self.assertAlmostEqual(vol(), 7.0 - 0.2, places=9)                                           # the source void is still there

    def test_an_unlinked_array_does_not_follow_until_it_is_linked_again(self):
        c = self.column()
        rid = self.array(c, dimension="X", counts=[3], spacings=[2000], linked=False)["array"]
        self.run_("model.set_property", id=c, key="width", value=500)
        self.assertEqual(sorted(E.m(self.meta(i), "width") for i in self.columns()), [0.3, 0.3, 0.5])
        self.run_("model.set_array", id=rid, linked=True)
        self.assertEqual({E.m(self.meta(i), "width") for i in self.columns()}, {0.5}, "linking again re-syncs the copies")
        self.run_("model.set_array", id=rid, linked=False)
        self.run_("model.set_property", id=c, key="width", value=0.4 * 1000)
        self.assertEqual(sorted(E.m(self.meta(i), "width") for i in self.columns()), [0.4, 0.5, 0.5])

    def test_an_item_edited_by_hand_becomes_independent_and_is_left_alone(self):
        c = self.column()
        r = self.array(c, dimension="X", counts=[4], spacings=[2000])
        rid, mine = r["array"], r["ids"][1]
        self.run_("model.set_property", id=mine, key="width", value=700)
        states = [s["state"] for s in self.items(rid)]
        self.assertEqual(states.count("independent"), 1)
        self.assertEqual(E.m(self.meta(mine), "array"), "", "it no longer says it belongs to the array")
        self.run_("model.set_property", id=c, key="width", value=500)
        self.assertEqual(E.m(self.meta(mine), "width"), 0.7)
        self.assertEqual(sorted(E.m(self.meta(i), "width") for i in self.columns()), [0.5, 0.5, 0.5, 0.7])
        self.run_("model.set_array", id=rid, dimension="X", counts=[4], spacings=[2500])
        self.assertEqual(len(self.columns()), 4, "the independent item keeps its place: no second item is made there")
        self.assertEqual(self.at(mine), (4.0, 0.0), "and it stays where it was put")
        out = self.run_("model.set_array", id=rid, dimension="X", counts=[2], spacings=[2500])
        self.assertTrue(E.alive(self.p, mine), "lowering the count never deletes an independent item")
        self.assertEqual(out["kept_independent"], 1)
        self.assertEqual(E.m(self.meta(mine), "array"), "")

    def test_making_an_item_independent_by_command(self):
        c = self.column()
        r = self.array(c, dimension="X", counts=[3], spacings=[2000])
        self.assertEqual(self.run_("model.detach_array_item", id=r["ids"][0])["array"], r["array"])
        self.run_("model.move", id=c, dx=0, dy=1.0, dz=0)
        self.assertEqual(self.at(r["ids"][0]), (2.0, 0.0))
        with self.assertRaises(BadRequest):
            self.run_("model.detach_array_item", id=c)

    def test_a_deleted_item_leaves_its_place_empty_and_stays_empty(self):
        c = self.column()
        r = self.array(c, dimension="X", counts=[4], spacings=[2000])
        self.run_("model.delete", id=r["ids"][1])
        self.assertEqual([s["state"] for s in self.items(r["array"])].count("removed"), 1)
        self.run_("model.set_property", id=c, key="width", value=500)
        self.assertEqual(len(self.columns()), 3, "regenerating does not bring the deleted item back")
        self.run_("model.set_array", id=r["array"], dimension="X", counts=[6], spacings=[2000])
        self.assertEqual(len(self.columns()), 5)

    def test_deleting_the_source_keeps_the_items_as_ordinary_elements(self):
        c = self.column()
        r = self.array(c, dimension="X", counts=[3], spacings=[2000])
        self.run_("model.delete", id=c)
        self.assertEqual(self.records(), [])
        self.assertEqual(len(self.columns()), 2)
        self.assertTrue(all(E.m(self.meta(i), "array") == "" for i in r["ids"]))

    def test_deleting_the_array_removes_the_items_that_follow_it_but_not_the_source_or_an_independent_one(self):
        c = self.column()
        r = self.array(c, dimension="X", counts=[4], spacings=[2000])
        self.run_("model.set_property", id=r["ids"][0], key="width", value=700)
        self.run_("model.delete", id=r["array"])
        self.assertEqual(self.records(), [])
        self.assertEqual(sorted(self.at(i) for i in self.columns()), [(0.0, 0.0), (2.0, 0.0)])
        self.assertTrue(E.alive(self.p, c) and E.alive(self.p, r["ids"][0]))

    def test_release_keeps_every_item(self):
        c = self.column()
        r = self.array(c, dimension="X", counts=[3], spacings=[2000])
        out = self.run_("model.release_array", id=r["ids"][0])                   # any item will do
        self.assertEqual(out["released"], 2)
        self.assertEqual((self.records(), len(self.columns())), ([], 3))
        self.run_("model.set_property", id=c, key="width", value=500)
        self.assertEqual(sorted(E.m(self.meta(i), "width") for i in self.columns()), [0.3, 0.3, 0.5])
        with self.assertRaises(BadRequest):
            self.run_("model.release_array", id=c)

    def test_plain_copies_are_still_available(self):
        c = self.column()
        r = self.array(c, dimension="X", counts=[3], spacings=[2000], persistent=False)
        self.assertEqual((self.records(), r["array"], len(self.columns())), ([], None, 3))


class Hosted(Case):
    def test_a_wall_array_carries_its_openings_and_follows_new_ones(self):
        w = self.wall((0, 0), (4000, 0))
        d = self.run_("model.add_door", wall=w, offset=2.0)["id"]
        r = self.array([w], dimension="Y", counts=[3], spacings=[3000])
        walls = [i for i in E.element_ids(self.p) if E.category(self.p, i) == "wall"]
        doors = [i for i in E.element_ids(self.p) if E.category(self.p, i) == "door"]
        self.assertEqual((len(walls), len(doors)), (3, 3))
        self.run_("model.add_window", wall=w, offset=0.5)                         # a window added to the source reaches the copies
        windows = [i for i in E.element_ids(self.p) if E.category(self.p, i) == "window"]
        self.assertEqual(len(windows), 3)
        self.assertEqual(sorted(E.m(self.meta(i), "host") for i in windows), sorted(i for i in E.element_ids(self.p) if E.category(self.p, i) == "wall"))
        self.assertEqual(len(self.items(r["array"])), 2)
        self.assertTrue(all(s["state"] == "driven" for s in self.items(r["array"])))
        self.run_("model.delete", id=d)
        self.assertEqual(len([i for i in E.element_ids(self.p) if E.category(self.p, i) == "door"]), 0, "removing the source's door removes the copies' doors")

    def test_an_opening_alone_is_refused(self):
        w = self.wall((0, 0), (4000, 0))
        d = self.run_("model.add_door", wall=w, offset=2.0)["id"]
        rev = self.p.revision
        with self.assertRaises(BadRequest):
            self.array([d], dimension="X", counts=[2], spacings=[1000])
        self.assertEqual(self.p.revision, rev)

    def test_walls_in_a_radial_array_join_where_they_meet(self):
        a = self.wall((0, 0), (3000, 0))
        self.array([a], mode="radial", count=4, centre=[0, 0], angle=360)
        walls = [i for i in E.element_ids(self.p) if E.category(self.p, i) == "wall"]
        self.assertTrue(all(len(E.m(self.meta(i), "joins")) >= 1 for i in walls))
        self.run_("model.set_property", id=a, key="height", value=4)
        walls = [i for i in E.element_ids(self.p) if E.category(self.p, i) == "wall"]                 # the copies were regenerated from the source
        self.assertEqual(sorted(E.m(self.meta(i), "height") for i in walls), [4, 4, 4, 4])
        self.assertTrue(all(len(E.m(self.meta(i), "joins")) >= 1 for i in walls))


class Refusals(Case):
    def test_requests_that_cannot_be_carried_out_change_nothing(self):
        c = self.column()
        rid = self.array(c, dimension="X", counts=[3], spacings=[2000])["array"]
        rev = self.p.revision
        snapshot = sorted(self.at(i) for i in self.columns())
        bad = [("model.array", {"ids": [c], "dimension": "X", "counts": [1], "spacings": [1000]}),
               ("model.array", {"ids": [c], "dimension": "X", "counts": [2], "spacings": [0]}),
               ("model.array", {"ids": [c], "dimension": "XX", "counts": [2, 2], "spacings": [1, 1]}),
               ("model.array", {"ids": [c], "dimension": "X", "counts": [501], "spacings": [10]}),
               ("model.array", {"ids": [c], "dimension": "XYZ", "counts": [20, 20, 20], "spacings": [10, 10, 10]}),
               ("model.array", {"ids": [c], "mode": "radial", "count": 3, "centre": [0, 0], "angle": 0}),
               ("model.array", {"ids": [rid], "dimension": "X", "counts": [2], "spacings": [1000]}),
               ("model.set_array", {"id": rid, "dimension": "X", "counts": [1], "spacings": [1000]}),
               ("model.set_array", {"id": rid, "axes": []}),
               ("model.set_array", {"id": c, "count": 3}),
               ("model.move", {"id": rid, "dx": 1.0, "dy": 0, "dz": 0}),
               ("model.rotate", {"ids": [rid], "centre": [0, 0], "angle": 90})]
        for op, params in bad:
            with self.subTest(op=op, params=params), self.assertRaises((BadRequest, NotFound)):
                self.p.execute(op, params)
        self.assertEqual((self.p.revision, sorted(self.at(i) for i in self.columns()), len(self.records())), (rev, snapshot, 1))

    def test_an_item_cannot_be_the_source_of_another_array(self):
        c = self.column()
        r = self.array(c, dimension="X", counts=[3], spacings=[2000])
        with self.assertRaises(BadRequest):
            self.array(r["ids"][0], dimension="Y", counts=[2], spacings=[1000])

    def test_a_grid_line_is_not_repeated(self):
        g = self.run_("model.add_grid", start=[0, 0], end=[5000, 0])["id"]
        with self.assertRaises(BadRequest):
            self.array(g, dimension="Y", counts=[3], spacings=[3000])


class Persistence(Case):
    def test_undo_and_redo_restore_the_array_and_its_items(self):
        c = self.column()
        self.array(c, dimension="X", counts=[3], spacings=[2000])
        before = self.points()
        self.p.undo()
        self.assertEqual((self.points(), self.records()), ([(0.0, 0.0)], []))
        self.p.redo()
        self.assertEqual((self.points(), len(self.records())), (before, 1))
        rid = self.records()[0]
        self.run_("model.set_array", id=rid, dimension="X", counts=[5], spacings=[1000])
        self.p.undo()
        self.assertEqual(self.points(), before)
        self.assertEqual(E.m(self.meta(rid), "axes")[0]["count"], 3)

    def test_a_source_edit_that_is_undone_takes_its_regenerated_items_back_too(self):
        c = self.column()
        self.array(c, dimension="X", counts=[3], spacings=[2000])
        self.run_("model.set_property", id=c, key="width", value=500)
        self.p.undo()
        self.assertEqual({E.m(self.meta(i), "width") for i in self.columns()}, {0.3})
        self.assertEqual(len(self.columns()), 3)

    def test_the_array_survives_save_and_reopen_and_still_regenerates(self):
        c = self.column()
        rid = self.array(c, dimension="X", counts=[3], spacings=[2000])["array"]
        with tempfile.TemporaryDirectory() as tmp:
            self.p.save(Path(tmp) / "a.spadra")
            q = Project.open(Path(tmp) / "a.spadra")
            self.assertEqual(AY.array_ids(q), [rid])
            q.execute("model.set_array", {"id": rid, "dimension": "X", "counts": [4], "spacings": [1000]})
            cols = [i for i in E.element_ids(q) if E.category(q, i) == "structural"]
            self.assertEqual(sorted(E.m(q.model.get(i)["metadata"], "point")[0] for i in cols), [0.0, 1.0, 2.0, 3.0])
            q.execute("model.set_property", {"id": c, "key": "width", "value": 600})
            cols = [i for i in E.element_ids(q) if E.category(q, i) == "structural"]
            self.assertEqual(len(cols), 4)
            self.assertEqual({E.m(q.model.get(i)["metadata"], "width") for i in cols}, {0.6})

    def test_tree_and_properties_name_the_array_and_its_items(self):
        c = self.column()
        r = self.array(c, dimension="X", counts=[3], spacings=[2000])
        tree = self.p.tree()
        listed = next(n for n in next(s for s in tree["sections"] if s["id"] == "categories")["children"] if n["label"] == "Arrays")
        self.assertEqual([x["id"] for x in listed["children"]], [r["array"]])
        props = E.properties(self.p, r["ids"][0])
        group = next(g for g in props["groups"] if g["title"] == "Array")
        self.assertEqual(group["rows"][0]["target"], {"model": r["array"]})
        self.assertEqual(group["rows"][1]["value"], "2")
        arr = E.properties(self.p, r["array"])
        items = next(g for g in arr["groups"] if g["title"] == "Items")
        self.assertEqual([x["value"] for x in items["rows"][:4]], ["3", "2", "0", "0"])


class Foundations(Case):
    def test_an_array_of_a_pad_makes_pads_that_do_not_snap_back_and_follow_the_source(self):
        col = self.column()
        pad = self.run_("model.add_foundation", kind="poer", support=[col], height=500)["id"]
        r = self.array(pad, dimension="X", counts=[3], spacings=[3000])
        self.assertTrue(all(E.m(self.meta(i), "support") == [] for i in r["ids"]))
        self.run_("model.move", id=col, dx=1.0, dy=0, dz=0)                       # the pad follows its column; the linked copies follow the pad
        self.assertEqual(sorted(E.m(self.meta(i), "point")[0] for i in E.element_ids(self.p) if E.category(self.p, i) == "foundation"), [1.0, 4.0, 7.0])


class Scale(Case):
    def test_a_larger_array_is_made_in_one_step_and_cleaned_in_one_step(self):
        c = self.column()
        r = self.array(c, dimension="XY", counts=[10, 10], spacings=[1000, 1000])
        self.assertEqual((r["count"], len(self.columns())), (100, 100))
        self.run_("model.delete", id=r["array"])
        self.assertEqual(len(self.columns()), 1)
        self.p.undo()
        self.assertEqual(len(self.columns()), 100)


if __name__ == "__main__":
    unittest.main()
