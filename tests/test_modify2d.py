"""2D modify commands (offset, trim, extend, fillet, mirror, hatch, grips) against the real SDRW documents, with exact expected numbers."""
from __future__ import annotations

import math
import unittest

from applications.SPADRA import geometry as G
from applications.SPADRA.project import Project, hatch_strokes
from applications.tools.errors import BadRequest, NotFound


def close(test: unittest.TestCase, a, b, places=6):
    test.assertEqual(len(a), len(b))
    for x, y in zip(a, b):
        test.assertAlmostEqual(x, y, places=places)


class GeometryFunctions(unittest.TestCase):
    def test_offset_open_chain_mitres_the_corner(self):
        out = G.offset_chain([(0, 0), (10, 0), (10, 10)], False, 1.0)      # left of the direction = inside the L
        self.assertEqual(len(out), 3)
        close(self, out[0], (0, 1)); close(self, out[1], (9, 1)); close(self, out[2], (9, 10))

    def test_offset_closed_square_inward_and_outward(self):
        sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
        inner = G.offset_chain(sq, True, 2.0)                               # counter-clockwise: left is inside
        close(self, inner[0], (2, 2)); close(self, inner[2], (8, 8))
        outer = G.offset_chain(sq, True, -2.0)
        close(self, outer[0], (-2, -2)); close(self, outer[2], (12, 12))

    def test_offset_that_collapses_is_refused(self):
        with self.assertRaises(G.GeometryError):
            G.offset_chain([(0, 0), (10, 0), (10, 10), (0, 10)], True, 6.0)

    def test_trim_open_line_between_two_cuts_leaves_both_ends(self):
        cutters = [("seg", (40, -5), (40, 5)), ("seg", (70, -5), (70, 5))]
        pieces = G.trim_chain([(0, 0), (100, 0)], False, (55, 0), cutters)
        self.assertEqual(len(pieces), 2)
        close(self, pieces[0][0], (0, 0)); close(self, pieces[0][-1], (40, 0))
        close(self, pieces[1][0], (70, 0)); close(self, pieces[1][-1], (100, 0))

    def test_trim_to_the_end_when_only_one_side_is_cut(self):
        pieces = G.trim_chain([(0, 0), (100, 0)], False, (80, 0), [("seg", (40, -5), (40, 5))])
        self.assertEqual(len(pieces), 1)
        close(self, pieces[0][-1], (40, 0))

    def test_trim_without_a_crossing_edge_is_refused(self):
        with self.assertRaises(G.GeometryError):
            G.trim_chain([(0, 0), (100, 0)], False, (50, 0), [("seg", (40, 5), (40, 15))])

    def test_trim_polyline_around_a_corner_keeps_the_corner_side(self):
        pieces = G.trim_chain([(0, 0), (10, 0), (10, 10)], False, (10, 8), [("seg", (5, -1), (5, 1))])
        self.assertEqual(len(pieces), 1)                                    # the click is on the far leg; the cut at x=5 is behind the corner
        close(self, pieces[0][0], (0, 0)); close(self, pieces[0][-1], (5, 0))

    def test_trim_closed_shape_opens_it_between_cuts(self):
        sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
        cutters = [("seg", (5, -1), (5, 1)), ("seg", (5, 9), (5, 11))]
        pieces = G.trim_chain(sq, True, (2, 0), cutters)                    # remove the bottom-left stretch from x=5 round to x=5 on the bottom
        self.assertEqual(len(pieces), 1)
        self.assertGreater(len(pieces[0]), 2)

    def test_extend_end_to_nearest_boundary_only(self):
        out = G.extend_chain([(0, 0), (10, 0)], (9, 0), [("seg", (30, -5), (30, 5)), ("seg", (20, -5), (20, 5)), ("seg", (-5, -5), (-5, 5))])
        close(self, out[-1], (20, 0)); close(self, out[0], (0, 0))
        back = G.extend_chain([(0, 0), (10, 0)], (1, 0), [("seg", (-5, -5), (-5, 5))])
        close(self, back[0], (-5, 0))

    def test_extend_to_a_circle(self):
        out = G.extend_chain([(0, 0), (1, 0)], (1, 0), [("circle", (10, 0), 3.0)])
        close(self, out[-1], (7, 0))

    def test_fillet_square_corner_radius_zero_and_radius_two(self):
        sharp = G.fillet_lines(((0, 0), (10, 0)), ((0, 0), (0, 10)), (8, 0), (0, 8), 0)
        close(self, sharp[0][0], (0, 0)); close(self, sharp[0][1], (10, 0)); self.assertIsNone(sharp[2])
        l1, l2, arc = G.fillet_lines(((0, 0), (10, 0)), ((0, 0), (0, 10)), (8, 0), (0, 8), 2.0)
        close(self, l1[0], (2, 0)); close(self, l2[0], (0, 2))
        close(self, arc[0], (2, 2)); self.assertAlmostEqual(arc[1], 2.0)
        self.assertAlmostEqual(arc[3] - arc[2], math.pi / 2)                # a quarter turn

    def test_fillet_extends_short_lines_to_their_corner(self):
        l1, l2, _ = G.fillet_lines(((5, 0), (10, 0)), ((0, 5), (0, 10)), (8, 0), (0, 8), 0)
        close(self, l1[0], (0, 0)); close(self, l2[0], (0, 0))

    def test_fillet_refuses_parallel_lines_and_oversized_radius(self):
        with self.assertRaises(G.GeometryError):
            G.fillet_lines(((0, 0), (10, 0)), ((0, 5), (10, 5)), (5, 0), (5, 5), 1)
        with self.assertRaises(G.GeometryError):
            G.fillet_lines(((0, 0), (10, 0)), ((0, 0), (0, 10)), (8, 0), (0, 8), 20)

    def test_reflect_across_diagonal(self):
        close(self, G.reflect((3, 1), (0, 0), (1, 1)), (1, 3))

    def test_hatch_strokes_cover_the_outline_only(self):
        lines = hatch_strokes(self.square(0, 0, 10, 10, spacing=2.0))
        self.assertTrue(lines)
        for a, b in lines:
            self.assertGreater(a[0], -1e-9); self.assertLess(b[0], 10 + 1e-9); self.assertAlmostEqual(a[1], b[1], places=5)
            self.assertTrue(0 < a[1] < 10)                                  # strokes lie inside, none on the edges
        ys = sorted({round(a[1], 4) for a, _ in lines})
        gaps = {round(y2 - y1, 4) for y1, y2 in zip(ys, ys[1:])}
        self.assertEqual(gaps, {2.0})                                       # evenly spaced as asked

    def test_hatch_holes_are_left_open(self):
        g = self.square(0, 0, 10, 10, spacing=1.0)
        g["boundary"].append({"kind": "polygon", "points": [[4, 4], [6, 4], [6, 6], [4, 6]]})
        lines = hatch_strokes(g)
        inside = [(a, b) for a, b in lines if 4 < a[1] < 6]
        self.assertTrue(inside)
        for a, b in inside:                                                 # every stroke at that height stops at the hole or starts after it
            lo, hi = sorted((a[0], b[0]))
            self.assertTrue(hi <= 4 + 1e-9 or lo >= 6 - 1e-9, (a, b))

    def test_a_circle_outline_is_hatched_and_cross_doubles_the_strokes(self):
        circle = {"pattern": "lines", "spacing": 1.0, "angle": 0.0, "boundary": [{"kind": "circle", "center": [0, 0], "radius": 5}]}
        lines = hatch_strokes(circle)
        self.assertTrue(all(abs(math.hypot(*a)) <= 5.01 and abs(math.hypot(*b)) <= 5.01 for a, b in lines))
        cross = hatch_strokes({**circle, "pattern": "cross"})
        self.assertAlmostEqual(len(cross) / len(lines), 2, delta=0.3)

    def test_a_solid_hatch_has_no_strokes(self):
        self.assertEqual(hatch_strokes({**self.square(0, 0, 10, 10, spacing=2.0), "pattern": "solid"}), [])

    def test_hatch_spacing_guard(self):
        with self.assertRaises(BadRequest):
            hatch_strokes(self.square(0, 0, 1000, 1000, spacing=0.01))

    @staticmethod
    def square(x0, y0, x1, y1, *, spacing, pattern="lines"):
        return {"pattern": pattern, "spacing": spacing, "angle": 0.0,
                "boundary": [{"kind": "polygon", "points": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]}]}


class Modify2D(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project.create("Mod")
        self.view = next(v["id"] for v in self.p.manifest["views"] if v["kind"] == "plan")

    @property
    def doc(self):
        return self.p.drawing(self.view)                                    # undo swaps the document object, so never hold on to it

    def run_(self, op, **params):
        return self.p.execute(op, {"drawing": self.view, **params})["result"]

    def line(self, a, b):
        return self.run_("draw.line", start=list(a), end=list(b))["id"]

    def geom(self, eid):
        return self.doc.get(eid)["geometry"]

    def count(self):
        return len(self.doc.query_ids(limit=1000))

    # -- offset
    def test_offset_line_to_the_clicked_side(self):
        a = self.line((0, 0), (1000, 0))
        up = self.run_("draw.offset", id=a, distance=300, side=[500, 900])["id"]
        down = self.run_("draw.offset", id=a, distance=300, side=[500, -50])["id"]
        close(self, self.geom(up)["start"], (0, 300)); close(self, self.geom(down)["start"], (0, -300))
        self.assertEqual(self.count(), 3)

    def test_offset_rectangle_inward_and_circle_outward_keep_the_layer(self):
        r = self.run_("draw.rectangle", corner=[0, 0], width=4000, height=3000, layer="Walls")["id"]
        inner = self.run_("draw.offset", id=r, distance=200, side=[2000, 1500])["id"]
        pts = self.geom(inner)["points"]
        close(self, pts[0], (200, 200)); close(self, pts[2], (3800, 2800))
        self.assertTrue(self.geom(inner)["closed"]); self.assertEqual(self.doc.get(inner)["layer"], "Walls")
        c = self.run_("draw.circle", center=[0, 0], radius=500)["id"]
        out = self.run_("draw.offset", id=c, distance=100, side=[900, 0])["id"]
        self.assertEqual(self.geom(out)["radius"], 600)
        inn = self.run_("draw.offset", id=c, distance=100, side=[10, 0])["id"]
        self.assertEqual(self.geom(inn)["radius"], 400)

    def test_offset_errors_are_plain_and_change_nothing(self):
        c = self.run_("draw.circle", center=[0, 0], radius=500)["id"]
        t = self.run_("draw.text", position=[0, 0], text="hello")["id"]
        before = self.p.revision
        with self.assertRaises(BadRequest):
            self.run_("draw.offset", id=c, distance=800, side=[10, 0])
        with self.assertRaises(BadRequest):
            self.run_("draw.offset", id=t, distance=10, side=[0, 5])
        with self.assertRaises(BadRequest):
            self.run_("draw.offset", id=c, distance=-5, side=[0, 5])
        with self.assertRaises(NotFound):
            self.run_("draw.offset", id="nope", distance=5, side=[0, 5])
        self.assertEqual(self.p.revision, before)

    # -- trim / extend
    def test_trim_splits_a_line_into_two_entities_on_the_same_layer(self):
        target = self.run_("draw.line", start=[0, 0], end=[1000, 0], layer="Grid")["id"]
        self.line((400, -50), (400, 50)); self.line((700, -50), (700, 50))
        out = self.run_("draw.trim", id=target, pick=[550, 0])
        self.assertEqual(len(out["ids"]), 2)
        ends = sorted((self.geom(i)["start"][0], self.geom(i)["end"][0]) for i in out["ids"])
        self.assertEqual(ends, [(0.0, 400.0), (700.0, 1000.0)])
        self.assertTrue(all(self.doc.get(i)["layer"] == "Grid" for i in out["ids"]))

    def test_trim_with_named_edges_ignores_other_geometry(self):
        target = self.line((0, 0), (1000, 0))
        edge = self.line((300, -50), (300, 50)); self.line((600, -50), (600, 50))
        out = self.run_("draw.trim", id=target, pick=[800, 0], edges=[edge])
        self.assertEqual(self.geom(out["ids"][0])["end"], [300.0, 0.0])

    def test_trim_polyline_keeps_one_polyline(self):
        poly = self.run_("draw.polyline", points=[[0, 0], [1000, 0], [1000, 1000]])["id"]
        self.line((500, -50), (500, 50))
        out = self.run_("draw.trim", id=poly, pick=[100, 0])
        self.assertEqual(len(out["ids"]), 1)
        close(self, self.geom(out["ids"][0])["points"][0], (500, 0)); close(self, self.geom(out["ids"][0])["points"][-1], (1000, 1000))

    def test_trim_is_one_undo_step(self):
        target = self.line((0, 0), (1000, 0)); self.line((400, -50), (400, 50)); self.line((700, -50), (700, 50))
        n = self.count()
        self.run_("draw.trim", id=target, pick=[550, 0])
        self.assertEqual(self.count(), n + 1)
        self.p.undo()
        self.assertEqual(self.count(), n)
        self.assertEqual(self.geom(target)["end"], [1000.0, 0.0])

    def test_trim_nothing_crossing_is_refused_without_changes(self):
        target = self.line((0, 0), (1000, 0))
        before = self.p.revision
        with self.assertRaises(BadRequest):
            self.run_("draw.trim", id=target, pick=[500, 0])
        self.assertEqual(self.p.revision, before); self.assertEqual(self.geom(target)["end"], [1000.0, 0.0])

    def test_extend_line_to_the_next_edge(self):
        target = self.line((0, 0), (500, 0))
        self.line((900, -100), (900, 100)); self.line((1500, -100), (1500, 100))
        self.run_("draw.extend", id=target, pick=[480, 0])
        self.assertEqual(self.geom(target)["end"], [900.0, 0.0])
        self.assertEqual(self.geom(target)["start"], [0.0, 0.0])

    def test_extend_refuses_closed_shapes_and_missing_boundaries(self):
        r = self.run_("draw.rectangle", corner=[0, 0], width=10, height=10)["id"]
        with self.assertRaises(BadRequest):
            self.run_("draw.extend", id=r, pick=[0, 0])
        a = self.line((0, 0), (10, 0))
        with self.assertRaises(BadRequest):
            self.run_("draw.extend", id=a, pick=[10, 0])

    # -- fillet
    def test_fillet_with_radius_adds_a_tangent_arc(self):
        a = self.line((0, 0), (1000, 0)); b = self.line((0, 0), (0, 1000))
        out = self.run_("draw.fillet", ids=[a, b], picks=[[800, 0], [0, 800]], radius=200)
        arc = self.geom(out["arc"])
        close(self, arc["center"], (200, 200)); self.assertAlmostEqual(arc["radius"], 200)
        close(self, self.geom(a)["start"], (200, 0)); close(self, self.geom(b)["start"], (0, 200))
        self.assertEqual(self.count(), 3)

    def test_fillet_radius_zero_makes_a_sharp_corner_and_no_arc(self):
        a = self.line((300, 0), (1000, 0)); b = self.line((0, 300), (0, 1000))
        out = self.run_("draw.fillet", ids=[a, b], picks=[[800, 0], [0, 800]], radius=0)
        self.assertNotIn("arc", out)
        self.assertEqual(self.geom(a)["start"], [0.0, 0.0]); self.assertEqual(self.geom(b)["start"], [0.0, 0.0])

    def test_fillet_needs_two_different_lines(self):
        a = self.line((0, 0), (1000, 0))
        for ids in ([a, a], [a]):
            with self.assertRaises(BadRequest):
                self.run_("draw.fillet", ids=ids, picks=[[1, 0], [1, 0]], radius=1)

    # -- mirror
    def test_mirror_copy_keeps_the_original(self):
        a = self.line((100, 0), (500, 200))
        out = self.run_("draw.mirror", ids=[a], p1=[0, 0], p2=[0, 1000])
        self.assertEqual(self.count(), 2)
        g = self.geom(out["ids"][0])
        close(self, g["start"], (-100, 0)); close(self, g["end"], (-500, 200))
        self.assertEqual(self.geom(a)["start"], [100.0, 0.0])

    def test_mirror_move_and_shapes(self):
        r = self.run_("draw.rectangle", corner=[100, 100], width=200, height=100)["id"]
        c = self.run_("draw.circle", center=[300, 50], radius=20)["id"]
        t = self.run_("draw.text", position=[10, 10], text="A")["id"]
        d = self.run_("draw.dimension", p1=[0, 0], p2=[100, 0], dimline_point=[50, 20])["id"]
        self.run_("draw.mirror", ids=[r, c, t, d], p1=[0, 0], p2=[1000, 0], copy=False)
        self.assertEqual(self.count(), 4)
        close(self, self.geom(r)["points"][0], (100, -100)); close(self, self.geom(c)["center"], (300, -50))
        close(self, self.geom(t)["position"], (10, -10)); close(self, self.geom(d)["dimline_point"], (50, -20))

    def test_mirror_across_a_point_is_refused(self):
        a = self.line((0, 0), (10, 0))
        with self.assertRaises(BadRequest):
            self.run_("draw.mirror", ids=[a], p1=[1, 1], p2=[1, 1])

    def test_mirror_is_all_or_nothing(self):
        a = self.line((0, 0), (10, 0))
        h = self.run_("draw.rectangle", corner=[0, 0], width=10, height=10)["id"]
        hatch = self.run_("draw.hatch", ids=[h], pattern="lines", spacing=2)["id"]
        self.assertEqual(self.p.drawing(self.view).get(hatch)["kind"], "hatch")
        self.run_("draw.mirror", ids=[a, hatch], p1=[0, 0], p2=[0, 1], copy=False)         # hatches mirror too
        self.assertEqual(self.doc.get(hatch)["geometry"]["boundary"][0]["points"][1][0], -10.0)

    # -- hatch
    def test_hatch_a_rectangle_solid_and_lines(self):
        r = self.run_("draw.rectangle", corner=[0, 0], width=1000, height=1000)["id"]
        solid = self.run_("draw.hatch", ids=[r], pattern="solid")["id"]
        self.assertEqual(self.geom(solid)["pattern"], "solid")
        lines = self.run_("draw.hatch", ids=[r], pattern="lines", angle=45, spacing=100)["id"]
        g = self.geom(lines)
        self.assertEqual(g["spacing"], 100); self.assertAlmostEqual(g["angle"], math.pi / 4)
        strokes = hatch_strokes(g)
        self.assertGreater(len(strokes), 10)
        for (x1, y1), (x2, y2) in strokes:                                   # every stroke lies inside the square at 45 degrees
            for x, y in ((x1, y1), (x2, y2)):
                self.assertTrue(-1e-3 <= x <= 1000.001 and -1e-3 <= y <= 1000.001)
            self.assertAlmostEqual(abs(y2 - y1), abs(x2 - x1), places=3)

    def test_hatch_a_circle_and_cross_hatch_has_two_directions(self):
        c = self.run_("draw.circle", center=[0, 0], radius=500)["id"]
        h = self.run_("draw.hatch", ids=[c], pattern="cross", angle=0, spacing=100)["id"]
        strokes = hatch_strokes(self.geom(h))
        horizontal = [s for s in strokes if abs(s[0][1] - s[1][1]) < 1e-6]
        vertical = [s for s in strokes if abs(s[0][0] - s[1][0]) < 1e-6]
        self.assertTrue(horizontal and vertical)

    def test_hatch_command_refuses_a_spacing_that_could_never_be_drawn_and_changes_nothing(self):
        r = self.run_("draw.rectangle", corner=[0, 0], width=100000, height=100000)["id"]
        before = self.p.revision
        with self.assertRaises(BadRequest):
            self.run_("draw.hatch", ids=[r], pattern="lines", spacing=0.001)
        self.assertEqual(self.p.revision, before)
        self.assertEqual([e for e in self.doc.query_ids(kind="hatch")], [])

    def test_hatch_refuses_open_shapes_lines_and_bad_patterns(self):
        poly = self.run_("draw.polyline", points=[[0, 0], [10, 0], [10, 10]])["id"]
        a = self.line((0, 0), (10, 0))
        for ids in ([poly], [a]):
            with self.assertRaises(BadRequest):
                self.run_("draw.hatch", ids=ids, pattern="solid")
        r = self.run_("draw.rectangle", corner=[0, 0], width=10, height=10)["id"]
        with self.assertRaises(BadRequest):
            self.run_("draw.hatch", ids=[r], pattern="ansi31")

    def test_hatch_default_spacing_is_a_twentieth_of_the_shape(self):
        r = self.run_("draw.rectangle", corner=[0, 0], width=2000, height=1000)["id"]
        h = self.run_("draw.hatch", ids=[r], pattern="lines")["id"]
        self.assertAlmostEqual(self.geom(h)["spacing"], 100.0)

    # -- grips
    def test_set_geometry_moves_the_handles_of_each_kind(self):
        a = self.line((0, 0), (100, 0))
        self.run_("draw.set_geometry", id=a, geometry={"start": [0, 10], "end": [100, 40]})
        self.assertEqual(self.geom(a), {"start": [0.0, 10.0], "end": [100.0, 40.0]})
        poly = self.run_("draw.polyline", points=[[0, 0], [10, 0], [10, 10]])["id"]
        self.run_("draw.set_geometry", id=poly, geometry={"points": [[0, 0], [20, 0], [20, 20]]})
        self.assertEqual(self.geom(poly)["points"][1], [20.0, 0.0])
        c = self.run_("draw.circle", center=[0, 0], radius=5)["id"]
        self.run_("draw.set_geometry", id=c, geometry={"center": [1, 1], "radius": 9})
        self.assertEqual(self.geom(c)["radius"], 9)
        self.p.undo()
        self.assertEqual(self.geom(c)["radius"], 5)

    def test_set_geometry_validates(self):
        a = self.line((0, 0), (100, 0))
        for bad in ({"start": [0, 0], "end": [0, 0]}, {"start": [0, 0]}, {"start": [0, "x"], "end": [1, 1]}, {"start": [0, 0], "end": [float("inf"), 0]}):
            with self.assertRaises(BadRequest):
                self.run_("draw.set_geometry", id=a, geometry=bad)
        r = self.run_("draw.rectangle", corner=[0, 0], width=10, height=10)["id"]
        h = self.run_("draw.hatch", ids=[r], pattern="solid")["id"]
        with self.assertRaises(BadRequest):
            self.run_("draw.set_geometry", id=h, geometry={})
        self.assertEqual(self.geom(a)["end"], [100.0, 0.0])

    def test_drawing_data_carries_hatch_strokes(self):
        r = self.run_("draw.rectangle", corner=[0, 0], width=1000, height=1000)["id"]
        h = self.run_("draw.hatch", ids=[r], pattern="lines", spacing=250, angle=0)["id"]
        data = self.p.drawing_data(self.view)
        item = next(e for e in data["entities"] if e["id"] == h)
        strokes = item["strokes"]
        self.assertEqual(len(strokes), 4)                                   # a 1000 high outline at 250 spacing
        ys = sorted(s[0][1] for s in strokes)
        self.assertTrue(all(abs(b - a - 250) < 1e-6 for a, b in zip(ys, ys[1:])))
        for a, b in strokes:                                                # horizontal, edge to edge, inside the outline
            self.assertAlmostEqual(a[1], b[1])
            self.assertEqual((a[0], b[0]), (0.0, 1000.0))
            self.assertTrue(0 < a[1] < 1000)


if __name__ == "__main__":
    unittest.main()
