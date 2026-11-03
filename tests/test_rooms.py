"""Rooms and columns (master prompt 10): parameters, derived figures, relationships to walls / openings / columns, schedules, checks, undo."""
from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from applications.SPADRA import rooms as R
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest

RECT = [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]          # mm: a 6 m × 4 m room


class Base(unittest.TestCase):
    def setUp(self):
        self.p = Project.create("Rooms")
        self.l1, self.l2 = (l["id"] for l in self.p.manifest["levels"][:2])

    def ex(self, op, **params):
        return self.p.execute(op, params)["result"]

    def wall(self, a, b, level=None, **extra):
        return self.ex("model.add_wall", start=list(a), end=list(b), level=level or self.l1, thickness=200, height=3, **extra)["id"]

    def box_of_walls(self, level=None):
        return [self.wall(a, b, level) for a, b in zip(RECT, RECT[1:] + RECT[:1])]

    def room(self, points=RECT, **extra):
        return self.ex("model.add_room", level=extra.pop("level", self.l1), points=points, **extra)["id"]

    def props(self, oid):
        return self.p.properties({"model": oid})

    def row(self, props, group, label):
        rows = props["top"] + [r for g in props["groups"] for r in g["rows"]]
        return next(r for r in rows if r["label"] == label)


class RoomBasics(Base):
    def test_a_room_has_an_outline_area_perimeter_and_no_solid(self):
        rid = self.room(name="Studio")
        obj = self.p.model.get(rid)
        self.assertIsNone(obj["geometry"])
        d = R.data(self.p, rid)
        self.assertAlmostEqual(d["area"], 24.0, places=9)
        self.assertAlmostEqual(d["perimeter"], 20.0, places=9)
        self.assertAlmostEqual(d["volume"], 24.0 * 2.7, places=9)
        for got, want in zip(d["centre"], (3.0, 2.0)):
            self.assertAlmostEqual(got, want, places=9)
        self.assertEqual(self.p.model.query_ids(has_geometry=True), [], "a room adds nothing to draw or to measure")

    def test_numbers_follow_the_level_and_never_repeat_automatically(self):
        a, b = self.room(), self.room([[7000, 0], [9000, 0], [9000, 2000]])
        c = self.room(level=self.l2, points=RECT)
        nums = [R.data(self.p, x)["number"] for x in (a, b, c)]
        self.assertEqual(nums, ["101", "102", "201"])
        self.assertEqual(R.data(self.p, self.room(number="A-7"))["number"], "A-7")

    def test_an_irregular_outline_is_measured_exactly(self):
        rid = self.room([[0, 0], [4000, 0], [4000, 2000], [2000, 2000], [2000, 4000], [0, 4000]])          # an L shape: 16 m² - 4 m² = 12 m²
        self.assertAlmostEqual(R.data(self.p, rid)["area"], 12.0, places=9)
        self.assertAlmostEqual(R.data(self.p, rid)["perimeter"], 16.0, places=9)

    def test_bad_outlines_are_refused_and_nothing_is_added(self):
        before = (self.p.revision, len(self.p.model.query_ids()))
        for bad in ([[0, 0], [1000, 0]], [[0, 0], [1000, 1000], [1000, 0], [0, 1000]], [[0, 0], [1000, 0], [2000, 0]], [[0, 0], [1000, 0], ["x", 5]],
                    [[0, 0], [1000, 0], [float("nan"), 1000]], [[0, 0], [1000, 0], [0, 1e12]], "square", None):
            with self.assertRaises(BadRequest, msg=repr(bad)):
                self.room(bad)
        with self.assertRaises(BadRequest):
            self.room(height=0)
        with self.assertRaises(BadRequest):
            self.room(name="")
        self.assertEqual((self.p.revision, len(self.p.model.query_ids())), before)

    def test_edit_through_the_properties_commands_and_undo(self):
        rid = self.room(name="Studio")
        self.ex("model.set_property", id=rid, key="height", value=3.0)
        self.ex("model.set_property", id=rid, key="number", value="Z1")
        self.ex("model.set_property", id=rid, key="name", value="Kitchen")
        props = self.props(rid)
        self.assertEqual(props["title"], "Kitchen")
        self.assertEqual(self.row(props, "Geometry", "Volume")["value"], "72.000 m³ (area × height)")
        self.assertEqual(self.row(props, "", "Number")["value"], "Z1")
        for key, bad in (("height", 0), ("height", "tall"), ("number", "x" * 21), ("colour", "red")):
            before = self.p.revision
            with self.assertRaises(BadRequest, msg=key):
                self.ex("model.set_property", id=rid, key=key, value=bad)
            self.assertEqual(self.p.revision, before)
        self.p.undo(); self.p.undo(); self.p.undo()
        self.assertEqual((R.data(self.p, rid)["height"], R.data(self.p, rid)["number"], self.p.model.get(rid)["name"]), (2.7, "101", "Studio"))

    def test_moving_a_room_moves_its_outline_and_keeps_its_area(self):
        rid = self.room()
        self.ex("model.move", id=rid, dx=2.0, dy=-0.5, dz=0)
        poly = R.boundary_of(self.p.model.get(rid)["metadata"])
        self.assertEqual(poly[0], (2.0, -0.5))
        self.assertAlmostEqual(R.metrics(poly)["area"], 24.0, places=9)

    def test_delete_and_undo(self):
        rid = self.room()
        self.ex("model.delete", id=rid)
        self.assertEqual(self.p.model.query_ids(), [])
        self.p.undo()
        self.assertEqual(self.p.model.query_ids(), [rid])

    def test_a_room_survives_save_and_reopen_with_everything(self):
        rid = self.room(name="Studio", number="S1", height=3.1)
        with tempfile.TemporaryDirectory() as tmp:
            self.p.save(Path(tmp) / "R.spadra")
            q = Project.open(Path(tmp) / "R.spadra")
        d = R.data(q, rid)
        self.assertEqual((q.model.get(rid)["name"], d["number"], d["height"], round(d["area"], 9)), ("Studio", "S1", 3.1, 24.0))


class Relationships(Base):
    def test_walls_along_the_edges_bound_the_room_and_others_do_not(self):
        walls = self.box_of_walls()
        far = self.wall((0, 9000), (6000, 9000))                           # parallel but far away
        other_level = self.wall((0, 0), (6000, 0), level=self.l2)           # same place, another level
        across = self.wall((0, 2000), (6000, 2000))                        # inside the room, not along an edge
        rid = self.room()
        ids = {w["id"] for w in R.bounding_walls(self.p, rid)}
        self.assertEqual(ids, set(walls))
        for stray in (far, other_level, across):
            self.assertNotIn(stray, ids)

    def test_an_outline_drawn_on_the_inner_face_of_the_walls_still_counts(self):
        walls = self.box_of_walls()
        inner = [[100, 100], [5900, 100], [5900, 3900], [100, 3900]]
        self.assertEqual({w["id"] for w in R.bounding_walls(self.p, self.room(inner))}, set(walls))
        too_far = [[400, 400], [5600, 400], [5600, 3600], [400, 3600]]       # 0.4 m inside: not along the walls any more
        self.assertEqual(R.bounding_walls(self.p, self.room(too_far)), [])

    def test_a_long_wall_that_only_partly_runs_along_an_edge_still_bounds_it(self):
        long_wall = self.wall((-3000, 0), (9000, 0))
        rid = self.room()
        walls = R.bounding_walls(self.p, rid)
        self.assertEqual([(w["id"], round(w["shared_length"], 6)) for w in walls], [(long_wall, 6.0)])

    def test_doors_and_windows_belong_to_the_room_only_where_they_sit_on_its_edge(self):
        south = self.wall((-3000, 0), (9000, 0))                           # runs past the room at both ends
        north = self.wall((0, 4000), (6000, 4000))
        outside = self.wall((0, 9000), (6000, 9000))
        d_in = self.ex("model.add_door", wall=south, offset=4.0, width=900, height=2100)["id"]       # x = 1 m along the room's south edge (wall starts at -3)
        d_out = self.ex("model.add_door", wall=south, offset=1.0, width=900, height=2100)["id"]      # x = -2 m: beside the room, not in it
        w_in = self.ex("model.add_window", wall=north, offset=3.0, width=1200, height=1200, sill=900)["id"]
        w_out = self.ex("model.add_window", wall=outside, offset=3.0, width=1200, height=1200, sill=900)["id"]
        rid = self.room()
        rel = R.related(self.p, rid)
        self.assertEqual([d["id"] for d in rel["doors"]], [d_in])
        self.assertEqual([w["id"] for w in rel["windows"]], [w_in])
        self.assertNotIn(d_out, [d["id"] for d in rel["doors"]])
        self.assertNotIn(w_out, [w["id"] for w in rel["windows"]])

    def test_columns_are_in_the_room_by_their_centre(self):
        inside = self.ex("model.add_column", level=self.l1, point=[3000, 2000])["id"]
        edge = self.ex("model.add_column", level=self.l1, point=[0, 0])["id"]
        outside = self.ex("model.add_column", level=self.l1, point=[7000, 2000])["id"]
        up = self.ex("model.add_column", level=self.l2, point=[3000, 2000])["id"]
        got = {c["id"] for c in R.related(self.p, self.room())["columns"]}
        self.assertEqual(got, {inside, edge})
        self.assertNotIn(outside, got)
        self.assertNotIn(up, got)

    def test_relationships_follow_edits(self):
        self.box_of_walls()
        south = [w for w in self.p.model.query_ids(has_geometry=True) if self.p.model.get(w)["metadata"].get("spadra:start") == [0.0, 0.0]][0]
        rid = self.room()
        self.assertEqual(R.data(self.p, rid)["doors"], [])
        door = self.ex("model.add_door", wall=south, offset=2.0, width=900, height=2100)["id"]
        self.assertEqual([d["id"] for d in R.data(self.p, rid)["doors"]], [door])
        self.ex("model.delete", id=door)
        self.assertEqual(R.data(self.p, rid)["doors"], [])
        self.ex("model.delete", id=south)
        self.assertEqual(len(R.data(self.p, rid)["walls"]), 3)


class Schedule(Base):
    def test_the_room_schedule_is_read_from_the_rooms_and_updates_with_them(self):
        walls = self.box_of_walls()
        self.ex("model.add_door", wall=walls[0], offset=2.0, width=900, height=2100)
        self.ex("model.add_window", wall=walls[2], offset=3.0, width=1200, height=1200, sill=900)
        a = self.room(name="Studio", number="101")
        self.room([[7000, 0], [9000, 0], [9000, 2000], [7000, 2000]], name="Store", number="102")
        data = self.p.schedule(self.ex("view.add_schedule", category="room")["id"])
        self.assertEqual([c["key"] for c in data["columns"]][:3], ["number", "name", "level"])
        rows = data["rows"]
        self.assertEqual([(r["number"], r["name"]) for r in rows], [("101", "Studio"), ("102", "Store")])
        self.assertEqual((rows[0]["area"], rows[0]["perimeter"], rows[0]["volume"], rows[0]["walls"], rows[0]["doors"], rows[0]["windows"]), (24.0, 20.0, 64.8, 4, 1, 1))
        self.assertEqual((rows[1]["area"], rows[1]["walls"]), (4.0, 0))
        self.assertEqual(data["totals"]["area"], 28.0)
        self.ex("model.set_property", id=a, key="height", value=3.0)
        self.assertEqual(self.p.schedule(data["view"]["id"])["rows"][0]["volume"], 72.0)
        self.ex("model.delete", id=a)
        self.assertEqual(self.p.schedule(data["view"]["id"])["count"], 1)

    def test_csv_export_has_units_and_totals(self):
        from applications.SPADRA.documentation import schedule_csv
        self.room(name="Studio")
        csv = schedule_csv(self.p.schedule(self.ex("view.add_schedule", category="room")["id"]))
        head, first, total = csv.strip().split("\r\n")
        self.assertIn("Area (m²)", head)
        self.assertTrue(first.startswith("101,Studio,"))
        self.assertTrue(total.startswith("Total,"))

    def test_the_structural_schedule_lists_columns_and_beams_with_their_type(self):
        self.ex("model.add_column", level=self.l1, point=[1000, 1000], width=300, depth=400, top=3.5)
        self.ex("model.add_beam", start=[0, 0], end=[4000, 0], level=self.l1, top=3, width=200, depth=400)
        rows = self.p.schedule(self.ex("view.add_schedule", category="structural")["id"])["rows"]
        self.assertEqual(sorted((r["type"], r["length"], r["volume"]) for r in rows), [("Beam", 4.0, 0.32), ("Column", 3.5, 0.42)])


class PlanAndChecks(Base):
    def test_the_plan_shows_the_room_with_its_label(self):
        rid = self.room(name="Studio")
        plan = next(v["id"] for v in self.p.manifest["views"] if v["kind"] == "plan" and v.get("level_id") == self.l1)
        item = next(w for w in self.p.drawing_data(plan)["model"] if w["id"] == rid)
        self.assertEqual((item["category"], len(item["polygon"])), ("room", 4))
        self.assertEqual((item["label"]["name"], item["label"]["number"], round(item["label"]["area_m2"], 6)), ("Studio", "101", 24.0))
        self.assertEqual([round(v, 6) for v in item["label"]["at"]], [3000.0, 2000.0])
        other = next(v["id"] for v in self.p.manifest["views"] if v["kind"] == "plan" and v.get("level_id") == self.l2)
        self.assertNotIn(rid, [w["id"] for w in self.p.drawing_data(other)["model"]])

    def test_checks_flag_an_open_room_and_a_damaged_outline(self):
        rid = self.room()
        issues = self.p.check()["issues"]
        self.assertTrue(any(i["ref"] == rid and i["severity"] == "warning" and "not enclosed" in i["message"] for i in issues))
        self.box_of_walls()
        self.assertFalse(any(i["ref"] == rid for i in self.p.check()["issues"]))
        self.p.model.attach_semantics(rid, {"boundary": [[0, 0], [1, 1]]}, namespace="spadra")           # damaged outside SPADRA
        self.assertTrue(any(i["ref"] == rid and i["severity"] == "error" for i in self.p.check()["issues"]))


class Columns(Base):
    def test_a_column_is_a_box_of_the_stated_size_at_the_stated_place(self):
        cid = self.ex("model.add_column", level=self.l1, point=[2000, 3000], width=300, depth=400, base=0.5, top=3.5, material="Steel")["id"]
        self.assertAlmostEqual(self.p.model.volume(cid, unit="m"), 0.3 * 0.4 * 3.0, places=12)
        lo, hi = self.p.model.world_bounds(cid)
        for got, want in zip(list(lo) + list(hi), (1.85, 2.8, 0.5, 2.15, 3.2, 3.5)):
            self.assertAlmostEqual(got, want, places=9)

    def test_rotation_edit_move_and_regeneration(self):
        cid = self.ex("model.add_column", level=self.l1, point=[0, 0], width=200, depth=600, rotation=90)["id"]
        lo, hi = self.p.model.world_bounds(cid)
        self.assertAlmostEqual(hi[0] - lo[0], 0.6, places=9)             # turned a quarter: the long side runs along X
        self.ex("model.set_property", id=cid, key="width", value=400)
        self.assertAlmostEqual(self.p.model.volume(cid, unit="m"), 0.4 * 0.6 * 3.0, places=9)
        self.ex("model.set_property", id=cid, key="top", value=4.0)
        self.assertAlmostEqual(self.p.model.volume(cid, unit="m"), 0.4 * 0.6 * 4.0, places=9)
        self.ex("model.move", id=cid, dx=1.0, dy=2.0, dz=0.5)
        meta = self.p.model.get(cid)["metadata"]
        self.assertEqual((meta["spadra:point"], meta["spadra:base"], meta["spadra:top"]), ([1.0, 2.0], 0.5, 4.5))
        self.ex("model.set_property", id=cid, key="level", value=self.l2)
        self.assertAlmostEqual(self.p.model.world_bounds(cid)[0][2], float(self.p.manifest["levels"][1]["elevation"]) + 0.5, places=9)

    def test_bad_columns_are_refused_whole(self):
        before = (self.p.revision, len(self.p.model.query_ids()))
        for params in ({"top": 0.0}, {"top": 3, "base": 3}, {"width": 0}, {"depth": 10**6}, {"point": [1]}, {"material": "Glass"}, {"point": [float("inf"), 0]}):
            with self.assertRaises(BadRequest, msg=repr(params)):
                self.ex("model.add_column", level=self.l1, **{"point": [0, 0], **params})
        self.assertEqual((self.p.revision, len(self.p.model.query_ids())), before)
        cid = self.ex("model.add_column", level=self.l1, point=[0, 0])["id"]
        rev = self.p.revision
        with self.assertRaises(BadRequest):
            self.ex("model.set_property", id=cid, key="top", value=-5)
        self.assertEqual(self.p.revision, rev)

    def test_properties_plan_outline_and_check(self):
        cid = self.ex("model.add_column", level=self.l1, point=[1000, 1000], width=300, depth=300)["id"]
        props = self.props(cid)
        self.assertEqual(self.row(props, "Constraints", "Top above level")["value"], 3.0)
        self.assertEqual(self.row(props, "Geometry", "Rotation")["value"], 0.0)
        plan = next(v["id"] for v in self.p.manifest["views"] if v["kind"] == "plan" and v.get("level_id") == self.l1)
        item = next(w for w in self.p.drawing_data(plan)["model"] if w["id"] == cid)
        self.assertEqual(item["category"], "column")
        xs = sorted(q[0] for q in item["polygon"])
        self.assertEqual((xs[0], xs[-1]), (850.0, 1150.0))
        self.assertEqual(self.p.check()["issues"], [])
        self.p.model.attach_semantics(cid, {"top": -1.0}, namespace="spadra")
        self.assertTrue(any("at or below its base" in i["message"] for i in self.p.check()["issues"]))

    def test_columns_are_measured_by_quantities(self):
        self.ex("model.add_column", level=self.l1, point=[0, 0], width=300, depth=300, top=3.0, material="Steel")
        q = self.p.quantities(None, None)
        self.assertAlmostEqual(q["rows"][0]["volume_m3"], 0.27, places=12)
        self.assertEqual(q["rows"][0]["category"], "structural")


if __name__ == "__main__":
    unittest.main()
