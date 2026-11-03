"""Editing walls the way a person drags them: stretching an end, turning elements about a point. Every edit is one undoable change, and the
joints with neighbouring walls and the openings in the wall follow it."""
from __future__ import annotations

import math
import unittest

from .. import elements as E
from ..project import Project
from ...tools.errors import BadRequest
from .test_wall_joins import Joints, area


class Stretch(Joints):
    def test_stretching_an_end_changes_only_that_end_and_rejoins(self):
        a, b = self.wall((0, 0), (4000, 0)), self.wall((4000, 0), (4000, 3000))
        self.p.execute("model.set_wall_ends", {"id": a, "end": [2000, 0]})
        f = E.wall_frame(self.p, a)
        self.assertEqual((f["start"], round(f["length"], 9)), ((0.0, 0.0), 2.0))
        self.assertEqual(E.wall_frame(self.p, b)["ends"], ((0.0, 0.0), (0.0, 0.0)))     # b no longer meets a: it is square-ended again
        self.assertEqual(self.meta(b)["spadra:joins"], [])

    def test_stretching_through_a_wall_makes_a_T_joint(self):
        a, b = self.wall((0, 0), (4000, 0)), self.wall((4000, 0), (4000, 3000))
        self.p.execute("model.set_wall_ends", {"id": a, "end": [6000, 0]})
        self.assertEqual(self.meta(b)["spadra:joins"], [a])                              # b is cut flush to a, which runs through
        self.assertEqual(E.wall_frame(self.p, a)["ends"], ((0.0, 0.0), (0.0, 0.0)))

    def test_both_ends_in_one_step_and_one_undo(self):
        a = self.wall((0, 0), (4000, 0))
        before = self.p.revision
        self.p.execute("model.set_wall_ends", {"id": a, "start": [0, 1000], "end": [5000, 1000]})
        self.assertEqual(self.p.revision, before + 1)
        self.assertEqual(E.wall_frame(self.p, a)["start"], (0.0, 1.0))
        self.p.undo()
        self.assertEqual(E.wall_frame(self.p, a)["start"], (0.0, 0.0))

    def test_stretching_onto_another_wall_makes_the_joint(self):
        a, b = self.wall((0, 0), (3000, 0)), self.wall((4000, 0), (4000, 3000))
        self.assertEqual(self.meta(a)["spadra:joins"], [])
        self.p.execute("model.set_wall_ends", {"id": a, "end": [4000, 0]})
        self.assertEqual(self.meta(a)["spadra:joins"], [b])
        self.assertEqual(self.meta(b)["spadra:joins"], [a])

    def test_a_door_left_past_the_end_refuses_the_whole_edit(self):
        a = self.wall((0, 0), (4000, 0))
        self.p.execute("model.add_door", {"wall": a, "offset": 3.0})
        with self.assertRaises(BadRequest):
            self.p.execute("model.set_wall_ends", {"id": a, "end": [2000, 0]})
        self.assertEqual(E.wall_frame(self.p, a)["end"], (4.0, 0.0))             # nothing changed

    def test_refuses_a_zero_length_wall_and_other_elements(self):
        a = self.wall((0, 0), (4000, 0))
        with self.assertRaises(BadRequest):
            self.p.execute("model.set_wall_ends", {"id": a, "end": [0, 0]})
        self.assertEqual(E.wall_frame(self.p, a)["end"], (4.0, 0.0))
        col = self.p.execute("model.add_column", {"point": [1000, 1000], "level": self.level})["result"]["id"]
        with self.assertRaises(BadRequest):
            self.p.execute("model.set_wall_ends", {"id": col, "end": [0, 0]})
        with self.assertRaises(BadRequest):
            self.p.execute("model.set_wall_ends", {"id": a})


class PlanAxis(Joints):
    def test_every_piece_of_a_wall_carries_its_centre_line_and_thickness_in_drawing_units(self):
        a = self.wall((0, 0), (4000, 0), thickness=250)
        self.p.execute("model.add_door", {"wall": a, "offset": 2.0})                     # the door splits the wall's plan outline in two pieces
        pieces = [i for i in E.plan_items(self.p, self.level, 1000.0) if i["id"] == a]
        self.assertEqual(len(pieces), 2)
        for piece in pieces:
            self.assertEqual(piece["axis"], {"a": [0.0, 0.0], "b": [4000.0, 0.0], "thickness": 250.0})


class Rotate(Joints):
    def test_a_wall_turns_about_a_point(self):
        a = self.wall((1000, 0), (3000, 0))
        self.p.execute("model.rotate", {"ids": [a], "centre": [0, 0], "angle": 90})
        f = E.wall_frame(self.p, a)
        self.assertAlmostEqual(f["start"][0], 0.0, places=9)
        self.assertAlmostEqual(f["start"][1], 1.0, places=9)
        self.assertAlmostEqual(f["end"][1], 3.0, places=9)
        self.assertAlmostEqual(f["length"], 2.0, places=9)
        self.assert_plan_matches_solid(a)

    def test_walls_that_turn_together_stay_joined(self):
        a, b = self.wall((0, 0), (4000, 0)), self.wall((4000, 0), (4000, 3000))
        before = (area(self.plan(a)[0]), area(self.plan(b)[0]))
        self.p.execute("model.rotate", {"ids": [a, b], "centre": [2000, 1500], "angle": 33})
        self.assertEqual(self.meta(a)["spadra:joins"], [b])
        self.assertAlmostEqual(area(self.plan(a)[0]), before[0], places=3)
        self.assertAlmostEqual(area(self.plan(b)[0]), before[1], places=3)
        self.assert_plan_matches_solid(a)
        self.assert_plan_matches_solid(b)

    def test_a_turned_wall_leaves_the_wall_it_met(self):
        a, b = self.wall((0, 0), (4000, 0)), self.wall((4000, 0), (4000, 3000))
        self.p.execute("model.rotate", {"ids": [a], "centre": [0, 0], "angle": 45})
        self.assertEqual(self.meta(b)["spadra:joins"], [])

    def test_a_door_turns_with_its_wall_and_cannot_turn_alone(self):
        a = self.wall((0, 0), (4000, 0))
        d = self.p.execute("model.add_door", {"wall": a, "offset": 2.0})["result"]["id"]
        with self.assertRaises(BadRequest):
            self.p.execute("model.rotate", {"ids": [d], "centre": [0, 0], "angle": 10})
        self.p.execute("model.rotate", {"ids": [a], "centre": [0, 0], "angle": 90})
        self.assertEqual((self.meta(d)["spadra:host"], self.meta(d)["spadra:offset"]), (a, 2.0))     # still in its wall, at the same place along it
        self.assertAlmostEqual(E.wall_frame(self.p, a)["end"][1], 4.0, places=9)

    def test_all_or_none_and_one_undo(self):
        a = self.wall((0, 0), (4000, 0))
        d = self.p.execute("model.add_door", {"wall": a, "offset": 2.0})["result"]["id"]
        with self.assertRaises(BadRequest):
            self.p.execute("model.rotate", {"ids": [a, d], "centre": [0, 0], "angle": 10})
        self.assertEqual(E.wall_frame(self.p, a)["end"], (4.0, 0.0))
        self.p.execute("model.rotate", {"ids": [a], "centre": [0, 0], "angle": 90})
        self.p.undo()
        self.assertEqual(E.wall_frame(self.p, a)["end"], (4.0, 0.0))

    def test_every_kind_of_element_that_can_turn_does(self):
        col = self.p.execute("model.add_column", {"point": [1000, 0], "level": self.level})["result"]["id"]
        beam = self.p.execute("model.add_beam", {"start": [0, 0], "end": [2000, 0], "level": self.level})["result"]["id"]
        floor = self.p.execute("model.add_floor", {"points": [[0, 0], [2000, 0], [2000, 1000], [0, 1000]], "level": self.level})["result"]["id"]
        self.p.execute("model.rotate", {"ids": [col, beam, floor], "centre": [0, 0], "angle": 90})
        self.assertAlmostEqual(self.meta(col)["spadra:point"][1], 1.0, places=9)
        self.assertAlmostEqual(self.meta(col)["spadra:rotation"], math.pi / 2, places=9)
        self.assertAlmostEqual(self.meta(beam)["spadra:end"][1], 2.0, places=9)
        self.assertAlmostEqual(self.meta(floor)["spadra:boundary"][1][1], 2.0, places=9)

    def test_validation(self):
        a = self.wall((0, 0), (4000, 0))
        for bad in ({"ids": [], "centre": [0, 0], "angle": 10}, {"ids": [a], "centre": [0], "angle": 10}, {"ids": [a], "centre": [0, 0]},
                    {"ids": [a], "centre": [0, 0], "angle": 1000}, {"ids": ["nope"], "centre": [0, 0], "angle": 10}):
            with self.assertRaises(Exception):
                self.p.execute("model.rotate", bad)


if __name__ == "__main__":
    unittest.main()
