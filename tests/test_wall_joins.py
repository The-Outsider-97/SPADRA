"""Walls that meet are joined: a mitre at any angle, a T cut flush to the wall it meets, faces round a joint of three, and everything
follows edits. The checks are geometric (areas, overlaps, shared corner points) and compare the plan outline with the 3D solid."""
from __future__ import annotations

import math
import random
import unittest

from .. import elements as E
from .. import geometry as G
from ..project import Project
from ...tools.errors import BadRequest


def area(poly):
    return abs(G.signed_area([tuple(q) for q in poly]))


def inside(poly, p):
    hit = False
    for a, b in zip(poly, poly[1:] + poly[:1]):
        if (a[1] > p[1]) != (b[1] > p[1]) and p[0] < (b[0] - a[0]) * (p[1] - a[1]) / (b[1] - a[1]) + a[0]:
            hit = not hit
    return hit


class Joints(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project.create("Joints")                         # drawing unit mm
        self.level = self.p.manifest["levels"][0]["id"]

    def wall(self, a, b, thickness=200, **extra):
        return self.p.execute("model.add_wall", {"start": list(a), "end": list(b), "level": self.level, "thickness": thickness, "height": 3, **extra})["result"]["id"]

    def plan(self, oid):
        return [i["polygon"] for i in E.plan_items(self.p, self.level, 1000.0) if i["id"] == oid and i["category"] == "wall"]

    def meta(self, oid):
        return self.p.model.get(oid)["metadata"]

    def ends(self, oid):
        return E.wall_frame(self.p, oid)["ends"]

    def assert_plan_matches_solid(self, oid):
        """The plan outline and the 3D solid are one wall: outline area × height is the solid's volume."""
        polys = self.plan(oid)
        self.assertAlmostEqual(sum(area(q) for q in polys) * 3.0 / 1e6, self.p.model.volume(oid, unit="m"), places=6)


class Corners(Joints):
    def test_a_right_angled_corner_is_a_mitre_with_no_overlap_and_no_gap(self):
        a, b = self.wall((0, 0), (4000, 0)), self.wall((4000, 0), (4000, 3000))
        pa, pb = self.plan(a)[0], self.plan(b)[0]
        self.assertEqual({tuple(round(c, 6) for c in q) for q in pa} & {tuple(round(c, 6) for c in q) for q in pb},
                         {(3900.0, 100.0), (4100.0, -100.0)})              # they share the inner and the outer corner, the mitre line between
        for x in range(3800, 4201, 10):                                     # no point is inside both outlines
            for y in range(-200, 201, 10):
                self.assertFalse(inside(pa, (x + .5, y + .5)) and inside(pb, (x + .5, y + .5)), (x, y))
        self.assertAlmostEqual(area(pa) + area(pb), (4000 + 3000) * 200, places=6)         # the corner is counted once: thickness × centre-line lengths
        self.assert_plan_matches_solid(a)
        self.assert_plan_matches_solid(b)

    def test_an_angled_corner_has_matching_faces_and_neither_bow_tie_nor_overlap(self):
        for angle in (30, 60, 100, 135, 160, 200, 250, 320):                 # interior angles of every kind, both turning directions
            with self.subTest(angle=angle):
                p = Project.create("angled")
                level = p.manifest["levels"][0]["id"]
                a = p.execute("model.add_wall", {"start": [0, 0], "end": [4000, 0], "level": level, "thickness": 200, "height": 3})["result"]["id"]
                turn = math.radians(angle)
                end = [4000 + 3000 * math.cos(turn), 3000 * math.sin(turn)]
                b = p.execute("model.add_wall", {"start": [4000, 0], "end": end, "level": level, "thickness": 200, "height": 3})["result"]["id"]
                items = {i["id"]: i["polygon"] for i in E.plan_items(p, level, 1000.0)}
                pa, pb = items[a], items[b]
                self.assertTrue(G.is_simple_polygon([tuple(q) for q in pa]) and G.is_simple_polygon([tuple(q) for q in pb]))   # no bow-tie
                shared = {tuple(round(c, 3) for c in q) for q in pa} & {tuple(round(c, 3) for c in q) for q in pb}
                self.assertEqual(len(shared), 2)                             # one mitre line: the same two corner points on both walls
                self.assertAlmostEqual(area(pa) + area(pb), (4000 + 3000) * 200, places=4)       # a mitre neither adds nor removes wall
                for w, poly in ((a, pa), (b, pb)):
                    self.assertAlmostEqual(area(poly) * 3.0 / 1e6, p.model.volume(w, unit="m"), places=6)          # plan and 3D are one wall
                probes = [(4000 + dx, dy) for dx in range(-400, 401, 20) for dy in range(-400, 401, 20)]
                self.assertEqual([q for q in probes if inside(pa, q) and inside(pb, q)], [])          # the two walls never overlap

    def test_nearly_straight_and_collinear_walls_join_square(self):
        a, b = self.wall((0, 0), (4000, 0)), self.wall((4000, 0), (8000, 0))
        self.assertEqual(self.ends(a), G.SQUARE_ENDS)
        self.assertEqual(self.ends(b), G.SQUARE_ENDS)
        self.assertEqual(self.plan(a)[0], [[0.0, 100.0], [4000.0, 100.0], [4000.0, -100.0], [0.0, -100.0]])

    def test_a_corner_too_sharp_for_a_mitre_stays_square_rather_than_spiking(self):
        a, b = self.wall((0, 0), (4000, 0)), self.wall((0, 0), (4000, 100))      # about 1.4 degrees apart: the mitre would be metres long
        self.assertEqual(self.ends(a), G.SQUARE_ENDS)
        self.assertEqual(self.ends(b), G.SQUARE_ENDS)

    def test_walls_of_different_thickness_still_meet_on_one_line(self):
        a, b = self.wall((0, 0), (4000, 0), 300), self.wall((4000, 0), (4000, 3000), 100)
        pa, pb = self.plan(a)[0], self.plan(b)[0]
        shared = {tuple(round(c, 6) for c in q) for q in pa} & {tuple(round(c, 6) for c in q) for q in pb}
        self.assertEqual(len(shared), 2)
        self.assert_plan_matches_solid(a)
        self.assert_plan_matches_solid(b)

    def test_three_walls_at_one_point_meet_face_to_face(self):
        walls = [self.wall((0, 0), (3000, 0)), self.wall((0, 0), (-1500, 2600)), self.wall((0, 0), (-1500, -2600))]
        polys = [self.plan(w)[0] for w in walls]
        for x in range(-400, 401, 20):
            for y in range(-400, 401, 20):
                self.assertLessEqual(sum(inside(q, (x + .5, y + .5)) for q in polys), 1, (x, y))
        for w in walls:
            self.assertEqual(len(self.meta(w)["spadra:joins"]), 2)
            self.assert_plan_matches_solid(w)

    def test_a_rectangle_of_four_walls_is_a_closed_ring_of_exact_area(self):
        pts = [(0, 0), (6000, 0), (6000, 4000), (0, 4000)]
        ws = [self.wall(a, b, 250) for a, b in zip(pts, pts[1:] + pts[:1])]
        total = sum(area(self.plan(w)[0]) for w in ws)
        self.assertAlmostEqual(total, 6250 * 4250 - 5750 * 3750, places=3)         # outer rectangle minus inner rectangle, nothing counted twice
        self.assertAlmostEqual(sum(self.p.model.volume(w, unit="m") for w in ws), total * 3 / 1e6, places=6)      # 3 m high


class Tees(Joints):
    def test_a_wall_that_ends_on_another_is_cut_flush_to_its_face(self):
        through, branch = self.wall((0, 0), (6000, 0)), self.wall((3000, 0), (3000, 3000))
        (sl, sr), _ = self.ends(branch)
        self.assertAlmostEqual(sl, -0.1)                                    # cut back by half the through wall's thickness (metres), both faces
        self.assertAlmostEqual(sr, -0.1)
        self.assertEqual(self.ends(through), G.SQUARE_ENDS)                 # the through wall is untouched
        self.assertEqual(self.plan(through)[0], [[0.0, 100.0], [6000.0, 100.0], [6000.0, -100.0], [0.0, -100.0]])
        self.assertAlmostEqual(self.plan(branch)[0][0][1], 100.0)           # the branch starts at the through wall's near face
        self.assert_plan_matches_solid(branch)

    def test_the_order_the_walls_are_drawn_in_does_not_matter(self):
        branch, through = self.wall((3000, 0), (3000, 3000)), self.wall((0, 0), (6000, 0))          # the branch is drawn first this time
        self.assertEqual(self.ends(branch)[0], (-0.1, -0.1))
        self.assertEqual(self.meta(branch)["spadra:joins"], [through])
        self.assert_plan_matches_solid(branch)

    def test_a_slanted_branch_is_cut_to_a_slanted_end_on_the_face(self):
        through, branch = self.wall((0, 0), (6000, 0)), self.wall((3000, 0), (4500, 2600))
        (sl, sr), _ = self.ends(branch)
        self.assertNotAlmostEqual(sl, sr)
        near = [q for q in self.plan(branch)[0] if abs(q[1] - 100) < 1e-6]    # both corners of the branch's end lie on the near face of the through wall
        self.assertEqual(len(near), 2)
        self.assert_plan_matches_solid(branch)

    def test_a_wall_that_only_comes_close_is_not_joined(self):
        self.wall((0, 0), (6000, 0))
        gap = self.wall((3000, 500), (3000, 3000))
        self.assertEqual(self.ends(gap), G.SQUARE_ENDS)
        self.assertEqual(self.meta(gap)["spadra:joins"], [])


class Following(Joints):
    def test_moving_a_wall_re_joins_its_neighbour_and_the_old_neighbour_is_square_again(self):
        a, b, c = self.wall((0, 0), (4000, 0)), self.wall((4000, 0), (4000, 3000)), self.wall((9000, 0), (12000, 0))
        self.assertNotEqual(self.ends(a), G.SQUARE_ENDS)
        self.p.execute("model.move", {"id": b, "dx": 5, "dy": 0, "dz": 0})                 # (metres) b leaves a and now stands on c's start
        self.assertEqual(self.ends(a), G.SQUARE_ENDS)
        self.assertEqual(self.plan(a)[0], [[0.0, 100.0], [4000.0, 100.0], [4000.0, -100.0], [0.0, -100.0]])
        self.assertEqual(self.meta(a)["spadra:joins"], [])
        self.assertEqual(self.meta(b)["spadra:joins"], [c])
        self.assertEqual(self.meta(c)["spadra:joins"], [b])

    def test_deleting_a_wall_squares_off_the_walls_it_joined_and_undo_restores_the_joint(self):
        a, b = self.wall((0, 0), (4000, 0)), self.wall((4000, 0), (4000, 3000))
        joined = self.plan(a)
        self.p.execute("model.delete", {"id": b})
        self.assertEqual(self.ends(a), G.SQUARE_ENDS)
        self.p.undo()
        self.assertEqual(self.plan(a), joined)
        self.assertEqual(self.meta(a)["spadra:joins"], [b])

    def test_editing_an_end_point_a_thickness_or_the_level_updates_both_walls(self):
        a, b = self.wall((0, 0), (4000, 0)), self.wall((4000, 0), (4000, 3000))
        self.p.execute("model.set_property", {"id": b, "key": "thickness", "value": 400})
        pa, pb = self.plan(a)[0], self.plan(b)[0]
        self.assertEqual(len({tuple(round(c, 6) for c in q) for q in pa} & {tuple(round(c, 6) for c in q) for q in pb}), 2)
        self.p.execute("model.set_property", {"id": b, "key": "start_x", "value": 3000})   # b no longer starts on a's end: a is square again
        self.assertEqual(self.ends(a), G.SQUARE_ENDS)
        upper = self.p.manifest["levels"][1]["id"]
        self.p.execute("model.set_property", {"id": a, "key": "level", "value": upper})
        self.assertEqual(self.meta(b)["spadra:joins"], [])

    def test_a_door_cannot_be_left_inside_a_corner_and_the_whole_edit_is_refused(self):
        a = self.wall((0, 0), (4000, 0))
        d = self.p.execute("model.add_door", {"wall": a, "offset": 3500, "width": 900})["result"]["id"]
        before = self.p.revision
        with self.assertRaises(BadRequest) as why:
            self.wall((4000, 0), (4000, 3000))                                # the corner would cut into the door
        self.assertIn("corner", str(why.exception))
        self.assertEqual(self.p.revision, before)
        self.assertEqual(self.ends(a), G.SQUARE_ENDS)
        self.assertIsNotNone(d)

    def test_a_door_placed_near_a_corner_is_kept_clear_of_it(self):
        a, _ = self.wall((0, 0), (4000, 0)), self.wall((4000, 0), (4000, 3000))
        d = self.p.execute("model.add_door", {"wall": a, "point": [3990, 0], "width": 900})["result"]["id"]
        x0, x1, _, _ = E.opening_span(self.meta(d))
        self.assertLessEqual(x1, 4.0 - 0.1 + 1e-9)                          # no further than the inner corner, 100 mm in
        footprint = 0.5 * (3900 + 4100) * 200 / 1e6
        self.assertAlmostEqual(self.p.model.volume(a, unit="m"), footprint * 3 - 0.9 * 2.1 * 0.2, places=6)       # the mitred wall, less the door opening

    def test_the_joints_survive_save_close_and_open_and_a_full_rebuild_changes_nothing(self):
        import tempfile
        from pathlib import Path
        a, b = self.wall((0, 0), (4000, 0)), self.wall((4000, 0), (4000, 3000), 250)
        self.p.execute("model.add_door", {"wall": a, "offset": 1500, "width": 900})
        before = {w: (self.plan(w), self.ends(w), self.p.model.volume(w, unit="m")) for w in (a, b)}
        with tempfile.TemporaryDirectory() as tmp:
            self.p.save_as(Path(tmp) / "joints.spadra")
            reopened = Project.open(Path(tmp) / "joints.spadra")
            E.regenerate_all(reopened)
            for w in (a, b):
                plan = [i["polygon"] for i in E.plan_items(reopened, self.level, 1000.0) if i["id"] == w and i["category"] == "wall"]
                self.assertEqual(plan, before[w][0])
                self.assertEqual(E.wall_frame(reopened, w)["ends"], before[w][1])
                self.assertAlmostEqual(reopened.model.volume(w, unit="m"), before[w][2], places=9)

    def test_the_stored_joints_always_equal_the_joints_derived_from_the_walls(self):
        """After any sequence of edits no wall is left with a joint that disagrees with what its neighbours now say."""
        rng = random.Random(7)
        walls: list[str] = []
        spots = [(x * 2000, y * 2000) for x in range(4) for y in range(3)]
        for _ in range(60):
            op = rng.choice(["add", "add", "move", "delete", "undo", "thick"])
            try:
                if op == "add" or not walls:
                    a, b = rng.sample(spots, 2)
                    walls.append(self.wall(a, b, rng.choice([100, 200, 300])))
                elif op == "move":
                    self.p.execute("model.move", {"id": rng.choice(walls), "dx": rng.choice([-2, 0, 2]), "dy": rng.choice([-2, 0, 2]), "dz": 0})
                elif op == "delete":
                    gone = rng.choice(walls)
                    self.p.execute("model.delete", {"id": gone})
                    walls.remove(gone)
                elif op == "thick":
                    self.p.execute("model.set_property", {"id": rng.choice(walls), "key": "thickness", "value": rng.choice([100, 200, 300])})
                else:
                    self.p.undo()
                    walls = [w for w in walls if w in self.p.model]
            except BadRequest:
                continue                                                      # an edit that cannot be made leaves the project as it was
            table = E._wall_table(self.p)
            for w in table:
                derived = E.wall_joins(self.p, w, table)
                self.assertTrue(E._same_ends(derived["ends"], self.meta(w).get("spadra:ends")), (op, w))
                self.assertEqual(derived["with"], sorted(self.meta(w).get("spadra:joins") or []), (op, w))
                self.assert_plan_matches_solid(w)


class Mesh(unittest.TestCase):
    def test_slanted_end_faces_are_planar_and_the_solid_is_closed(self):
        verts, faces = G.wall_mesh(4.0, 0.2, 3.0, [(-0.5, 0.4, -1.5, 0.6)], ((0.1, -0.1), (-0.3, 0.3)))
        self.assertTrue(all(len(f) == 3 for f in faces))
        edges: dict[tuple[int, int], int] = {}
        for a, b, c in faces:
            for u, v in ((a, b), (b, c), (c, a)):
                edges[(u, v)] = edges.get((u, v), 0) + 1
        self.assertTrue(all(edges.get((v, u)) == n for (u, v), n in edges.items()))      # every edge is used once each way: closed and oriented
        volume = sum(sum(verts[a][i] * (verts[b][(i + 1) % 3] * verts[c][(i + 2) % 3] - verts[b][(i + 2) % 3] * verts[c][(i + 1) % 3]) for i in range(3)) / 6
                     for a, b, c in faces)
        # the +y face runs from -2.1 to 1.7 (3.8 m), the -y face from -1.9 to 2.3 (4.2 m): a mean footprint of 4.0 × 0.2, 3 m high, less the opening
        self.assertAlmostEqual(volume, 4.0 * 0.2 * 3.0 - 0.9 * 2.1 * 0.2, places=9)

    def test_an_opening_may_not_run_into_a_cut_back_end(self):
        with self.assertRaises(G.GeometryError):
            G.wall_mesh(4.0, 0.2, 3.0, [(-2.0, -1.0, -1.5, 0.6)], ((-0.3, -0.3), (0.0, 0.0)))
        with self.assertRaises(G.GeometryError):
            G.wall_mesh(0.3, 0.2, 3.0, [], ((-0.2, -0.2), (-0.2, -0.2)))                       # the joints cut the whole wall away


if __name__ == "__main__":
    unittest.main()
