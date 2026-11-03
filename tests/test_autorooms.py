"""Rooms that make themselves where walls close a space: found from the wall centre lines, moved in to the inner faces, kept when the walls change
(name and number survive), removed when the space opens, never touching a hand-drawn room, and undone together with the walls."""
from __future__ import annotations

import math
import unittest

from .. import autorooms as AR
from .. import elements as E
from .. import geometry as G
from .. import rooms as R
from ..project import Project
from ...tools.errors import BadRequest


def area(poly):
    return abs(G.signed_area([tuple(q) for q in poly]))


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project.create("Auto")                                    # drawing unit mm
        self.level = self.p.manifest["levels"][0]["id"]

    def wall(self, a, b, thickness=200, level=None):
        return self.p.execute("model.add_wall", {"start": list(a), "end": list(b), "level": level or self.level, "thickness": thickness, "height": 3})["result"]["id"]

    def box(self, x0, y0, x1, y1, thickness=200):
        return [self.wall((x0, y0), (x1, y0), thickness), self.wall((x1, y0), (x1, y1), thickness), self.wall((x1, y1), (x0, y1), thickness), self.wall((x0, y1), (x0, y0), thickness)]

    def rooms(self, level=None):
        return [o for o in E.element_ids(self.p) if E.m(E.meta_of(self.p, o), "category") == "room" and E.m(E.meta_of(self.p, o), "level") == (level or self.level)]

    def outline(self, rid):
        return R.boundary_of(E.meta_of(self.p, rid))


class Closing(Base):
    def test_no_room_until_the_last_wall_closes_the_space(self):
        self.wall((0, 0), (5000, 0)); self.wall((5000, 0), (5000, 4000)); self.wall((5000, 4000), (0, 4000))
        self.assertEqual(self.rooms(), [])
        self.wall((0, 4000), (0, 0))
        self.assertEqual(len(self.rooms()), 1)

    def test_the_room_is_the_space_between_the_inner_faces_of_the_walls(self):
        self.box(0, 0, 5000, 4000, 200)
        (rid,) = self.rooms()
        self.assertAlmostEqual(area(self.outline(rid)), (5 - 0.2) * (4 - 0.2), places=6)       # 200 mm walls: 100 mm in from the centre lines
        xs, ys = [q[0] for q in self.outline(rid)], [q[1] for q in self.outline(rid)]
        self.assertAlmostEqual(min(xs), 0.1, places=9); self.assertAlmostEqual(max(xs), 4.9, places=9)
        self.assertAlmostEqual(min(ys), 0.1, places=9); self.assertAlmostEqual(max(ys), 3.9, places=9)

    def test_no_roof_or_floor_is_needed_and_none_is_made(self):
        self.box(0, 0, 3000, 3000)
        cats = {E.m(E.meta_of(self.p, o), "category") for o in E.element_ids(self.p)}
        self.assertEqual(cats, {"wall", "room"})

    def test_walls_of_different_thickness_each_give_their_own_inset(self):
        a = self.wall((0, 0), (4000, 0), 400); self.wall((4000, 0), (4000, 3000), 200); self.wall((4000, 3000), (0, 3000), 100); self.wall((0, 3000), (0, 0), 300)
        (rid,) = self.rooms()
        xs, ys = [q[0] for q in self.outline(rid)], [q[1] for q in self.outline(rid)]
        self.assertAlmostEqual(min(ys), 0.2, places=9); self.assertAlmostEqual(max(ys), 2.95, places=9)
        self.assertAlmostEqual(min(xs), 0.15, places=9); self.assertAlmostEqual(max(xs), 3.9, places=9)

    def test_a_room_at_an_angle(self):
        s = math.radians(30)
        pts = [(0, 0), (4000 * math.cos(s), 4000 * math.sin(s)), (4000 * math.cos(s) - 3000 * math.sin(s), 4000 * math.sin(s) + 3000 * math.cos(s)), (-3000 * math.sin(s), 3000 * math.cos(s))]
        for i in range(4):
            self.wall(pts[i], pts[(i + 1) % 4], 200)
        (rid,) = self.rooms()
        self.assertAlmostEqual(area(self.outline(rid)), 3.8 * 2.8, places=6)

    def test_a_partition_that_meets_both_sides_splits_the_space_in_two(self):
        self.box(0, 0, 6000, 4000)
        self.wall((3000, 0), (3000, 4000), 100)
        rooms = self.rooms()
        self.assertEqual(len(rooms), 2)
        self.assertAlmostEqual(sum(area(self.outline(r)) for r in rooms), (5.8 * 3.8) - 0.1 * 3.8, places=6)

    def test_a_partition_that_stops_short_does_not_split_it(self):
        self.box(0, 0, 6000, 4000)
        self.wall((3000, 0), (3000, 2500), 100)
        self.assertEqual(len(self.rooms()), 1)

    def test_two_rooms_that_share_a_wall(self):
        self.box(0, 0, 4000, 3000)
        self.wall((4000, 0), (8000, 0)); self.wall((8000, 0), (8000, 3000)); self.wall((8000, 3000), (4000, 3000))
        self.assertEqual(len(self.rooms()), 2)

    def test_walls_on_another_level_make_their_own_rooms(self):
        other = self.p.manifest["levels"][1]["id"]
        self.box(0, 0, 3000, 3000)
        for a, b in (((0, 0), (3000, 0)), ((3000, 0), (3000, 3000)), ((3000, 3000), (0, 3000)), ((0, 3000), (0, 0))):
            self.wall(a, b, level=other)
        self.assertEqual((len(self.rooms()), len(self.rooms(other))), (1, 1))
        self.assertNotEqual(E.m(E.meta_of(self.p, self.rooms()[0]), "number"), E.m(E.meta_of(self.p, self.rooms(other)[0]), "number"))

    def test_a_ring_of_walls_inside_another_leaves_the_outer_space_without_a_room(self):
        self.box(0, 0, 10000, 10000)
        self.box(4000, 4000, 6000, 6000)
        self.assertEqual(len(self.rooms()), 1)                                  # only the inner ring is a room: the outer would need a hole


class Following(Base):
    def test_moving_a_wall_reshapes_the_room_and_keeps_its_name_and_number(self):
        walls = self.box(0, 0, 5000, 4000)
        (rid,) = self.rooms()
        self.p.execute("model.set_property", {"id": rid, "key": "number", "value": "A-1"})
        self.p.execute("model.rename", {"id": rid, "name": "Kitchen"}) if "model.rename" in self.p._commands else self.p.model.rename_object(rid, "Kitchen")
        self.p.execute("model.set_wall_ends", {"id": walls[1], "start": [6000, 0], "end": [6000, 4000]})
        self.p.execute("model.set_wall_ends", {"id": walls[0], "end": [6000, 0]})
        self.p.execute("model.set_wall_ends", {"id": walls[2], "start": [6000, 4000]})
        self.assertEqual(self.rooms(), [rid])
        self.assertAlmostEqual(area(self.outline(rid)), 5.8 * 3.8, places=6)
        self.assertEqual(self.p.model.get(rid)["name"], "Kitchen")
        self.assertEqual(E.m(E.meta_of(self.p, rid), "number"), "A-1")

    def test_opening_the_space_removes_the_room_and_closing_it_again_makes_one(self):
        walls = self.box(0, 0, 5000, 4000)
        self.p.execute("model.delete", {"id": walls[3]})
        self.assertEqual(self.rooms(), [])
        self.wall((0, 4000), (0, 0))
        self.assertEqual(len(self.rooms()), 1)

    def test_a_room_the_person_made_their_own_is_kept_when_its_space_opens_and_comes_back_when_it_closes(self):
        walls = self.box(0, 0, 5000, 4000)
        (rid,) = self.rooms()
        self.p.model.rename_object(rid, "Kitchen")
        before = self.outline(rid)
        self.p.execute("model.delete", {"id": walls[3]})
        self.assertEqual(self.rooms(), [rid])                                   # kept, not lost
        self.assertEqual(self.outline(rid), before)
        self.assertTrue(E.m(E.meta_of(self.p, rid), "open"))
        self.assertTrue(any(i["ref"] == rid and "no longer enclosed" in i["message"] for i in E.check(self.p)))
        self.wall((0, 4000), (0, 0))
        self.assertEqual(self.rooms(), [rid])
        self.assertFalse(E.m(E.meta_of(self.p, rid), "open"))
        self.assertEqual(self.p.model.get(rid)["name"], "Kitchen")
        self.assertFalse(any(i["ref"] == rid for i in E.check(self.p)))

    def test_a_renumbered_room_is_kept_too(self):
        walls = self.box(0, 0, 5000, 4000)
        (rid,) = self.rooms()
        self.p.execute("model.set_property", {"id": rid, "key": "number", "value": "B-7"})
        self.p.execute("model.delete", {"id": walls[0]})
        self.assertEqual(self.rooms(), [rid])

    def test_one_undo_takes_the_wall_and_the_room_back_together(self):
        self.box(0, 0, 5000, 4000)
        self.assertEqual(len(self.rooms()), 1)
        self.p.undo()
        self.assertEqual(self.rooms(), [])
        self.p.redo()
        self.assertEqual(len(self.rooms()), 1)

    def test_a_wall_that_closes_nothing_leaves_everything_alone(self):
        self.box(0, 0, 5000, 4000)
        (rid,) = self.rooms()
        revision = self.p.revision
        self.wall((9000, 0), (12000, 0))
        self.assertEqual(self.rooms(), [rid])
        self.assertEqual(self.p.revision, revision + 1)

    def test_a_space_that_is_split_keeps_the_room_that_still_holds_its_probe(self):
        self.box(0, 0, 6000, 4000)
        (rid,) = self.rooms()
        self.p.model.rename_object(rid, "Hall")
        self.wall((3000, 0), (3000, 4000), 100)
        names = sorted(self.p.model.get(r)["name"] for r in self.rooms())
        self.assertEqual(names, ["Hall", "Room"])

    def test_tags_follow_the_room_and_go_when_it_does(self):
        walls = self.box(0, 0, 5000, 4000)
        (rid,) = self.rooms()
        plan = next(v["id"] for v in self.p.manifest["views"] if v["kind"] == "plan")
        self.p.execute("draw.tag", {"drawing": plan, "element": rid, "point": [2000, 2000]})
        self.p.execute("model.delete", {"id": walls[0]})
        self.assertEqual([t["detached"] for t in __import__("applications.SPADRA.annotations", fromlist=["x"]).all_tags(self.p)], [True])


class ByHandAndSetting(Base):
    def test_a_room_drawn_by_hand_is_never_touched_and_is_not_doubled(self):
        hand = self.p.execute("model.add_room", {"points": [[500, 500], [4500, 500], [4500, 3500], [500, 3500]], "level": self.level, "name": "Study"})["result"]["id"]
        self.box(0, 0, 5000, 4000)
        self.assertEqual(self.rooms(), [hand])
        self.assertEqual(self.outline(hand)[0], (0.5, 0.5))
        self.p.execute("model.delete", {"id": self.p.model.query_ids(metadata={"spadra:category": "wall"}, limit=1)[0]})
        self.assertEqual(self.rooms(), [hand])

    def test_a_room_drawn_by_hand_in_an_automatic_one_takes_its_place(self):
        self.box(0, 0, 5000, 4000)
        (auto,) = self.rooms()
        hand = self.p.execute("model.add_room", {"points": [[100, 100], [4900, 100], [4900, 3900], [100, 3900]], "level": self.level, "name": "Lounge"})["result"]["id"]
        self.assertEqual(self.rooms(), [hand])
        self.assertEqual(E.m(E.meta_of(self.p, hand), "number"), "101")           # the number the automatic room had is free again
        self.p.undo()
        self.assertEqual(self.rooms(), [auto])

    def test_the_project_summary_says_whether_it_is_on(self):
        self.assertTrue(self.p.summary()["auto_rooms"])
        self.p.execute("model.set_auto_rooms", {"on": False})
        self.assertFalse(self.p.summary()["auto_rooms"])

    def test_turning_it_off_keeps_the_rooms_as_ordinary_rooms_and_stops_new_ones(self):
        self.box(0, 0, 5000, 4000)
        (rid,) = self.rooms()
        self.p.execute("model.set_auto_rooms", {"on": False})
        self.assertFalse(E.m(E.meta_of(self.p, rid), "auto"))
        self.box(8000, 0, 11000, 3000)
        self.assertEqual(self.rooms(), [rid])
        self.p.execute("model.delete", {"id": self.p.model.query_ids(metadata={"spadra:category": "wall"}, limit=1)[0]})
        self.assertEqual(self.rooms(), [rid])                                    # it no longer follows the walls

    def test_turning_it_on_makes_rooms_for_the_spaces_already_closed(self):
        self.p.execute("model.set_auto_rooms", {"on": False})
        self.box(0, 0, 5000, 4000)
        self.assertEqual(self.rooms(), [])
        out = self.p.execute("model.set_auto_rooms", {"on": True})["result"]
        self.assertEqual(out["rooms"]["added"], 1)
        self.assertEqual(len(self.rooms()), 1)

    def test_update_rooms_reports_what_it_did(self):
        self.p.execute("model.set_auto_rooms", {"on": False})
        self.box(0, 0, 5000, 4000)
        self.p.manifest["auto_rooms"]["on"] = True
        out = self.p.execute("model.update_rooms", {})["result"]
        self.assertEqual(out["rooms"], {"added": 1, "updated": 0, "removed": 0, "not_enclosed": 0})

    def test_the_setting_is_saved_with_the_project(self):
        import tempfile
        from pathlib import Path
        self.box(0, 0, 5000, 4000)
        self.p.execute("model.set_auto_rooms", {"on": False})
        with tempfile.TemporaryDirectory() as d:
            self.p.save_as(Path(d) / "auto.spadra") if hasattr(self.p, "save_as") else self.p.save(Path(d) / "auto.spadra")
            q = Project.open(Path(d) / "auto.spadra")
            self.assertFalse(AR.is_on(q))
            self.assertEqual(len([o for o in E.element_ids(q) if E.m(E.meta_of(q, o), "category") == "room"]), 1)

    def test_a_bad_value_is_refused(self):
        with self.assertRaises(BadRequest):
            self.p.execute("model.set_auto_rooms", {"on": "maybe"})


class Geometry(unittest.TestCase):
    def test_spaces_finds_a_box_and_ignores_open_walls(self):
        walls = [("a", (0, 0), (4, 0), .2), ("b", (4, 0), (4, 3), .2), ("c", (4, 3), (0, 3), .2), ("d", (0, 3), (0, 0), .2)]
        self.assertEqual(len(AR.spaces(walls)), 1)
        self.assertEqual(AR.spaces(walls[:3]), [])

    def test_walls_that_cross_make_four_spaces(self):
        walls = [("a", (0, 0), (6, 0), .1), ("b", (6, 0), (6, 6), .1), ("c", (6, 6), (0, 6), .1), ("d", (0, 6), (0, 0), .1), ("e", (0, 3), (6, 3), .1), ("f", (3, 0), (3, 6), .1)]
        self.assertEqual(len(AR.spaces(walls)), 4)

    def test_the_probe_point_is_inside_even_for_an_l_shape(self):
        l = [(0, 0), (4, 0), (4, 1), (1, 1), (1, 4), (0, 4)]
        self.assertTrue(R.contains(l, AR.interior_point(l)))


if __name__ == "__main__":
    unittest.main()
