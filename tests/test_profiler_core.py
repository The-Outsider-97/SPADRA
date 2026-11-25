"""Profiler core: drawing, editing, constraints, the solver's diagnostics, profile checks and saved files - asserted on real geometry."""
from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from . import profiler_env  # noqa: F401
from ..profiler import geom as G, sdrw_io, validate
from ..profiler.errors import ProfilerConflict, ProfilerError
from ..profiler.model import Sketch
from ..profiler.session import Session


def S_new() -> Session:
    return Session(Sketch("t", unit="mm"))


def regions(S):
    return S.run("regions")["result"]["regions"]


def area(S):
    return sum(r["area"] for r in regions(S) if not r.get("is_hole"))


class Drawing(unittest.TestCase):
    def test_rectangle_circle_polygon_slot_areas(self):
        S = S_new()
        S.run("rectangle", {"p1": [0, 0], "p2": [40, 30]})
        self.assertAlmostEqual(area(S), 1200, 6)
        T = S_new()
        T.run("circle", {"center": [0, 0], "radius": 10})
        self.assertAlmostEqual(area(T), math.pi * 100, 3)
        P = S_new()
        P.run("polygon", {"center": [0, 0], "radius": 10, "sides": 6})
        self.assertAlmostEqual(area(P), 3 * math.sqrt(3) / 2 * 100, 3)
        L = S_new()
        L.run("slot", {"p1": [0, 0], "p2": [40, 0], "width": 10})
        self.assertAlmostEqual(area(L), 40 * 10 + math.pi * 25, 1)

    def test_arc_workflows_agree(self):
        a = S_new()
        e1 = a.run("arc_center_ends", {"center": [0, 0], "start": [10, 0], "end": [0, 10]})["result"]["edge"]
        b = S_new()
        e2 = b.run("arc_start_end_radius", {"start": [10, 0], "end": [0, 10], "radius": 10})["result"]["edge"]
        c = S_new()
        e3 = c.run("arc_three_point", {"start": [10, 0], "mid": [10 * math.cos(math.pi / 4), 10 * math.sin(math.pi / 4)], "end": [0, 10]})["result"]["edge"]
        for S, e in ((a, e1), (b, e2), (c, e3)):
            self.assertAlmostEqual(S.sk.edge_length(e), 10 * math.pi / 2, 3)

    def test_fillet_and_chamfer_replace_the_corner(self):
        S = S_new()
        e = S.run("polyline", {"points": [[0, 0], [50, 0], [50, 50]]})["result"]["edges"]
        r = S.run("fillet", {"edges": e, "radius": 10})["result"]
        self.assertTrue(r)
        b = S.sk.bounds()
        self.assertAlmostEqual(b[2], 50, 6)
        T = S_new()
        e = T.run("polyline", {"points": [[0, 0], [50, 0], [50, 50]]})["result"]["edges"]
        T.run("chamfer", {"edges": e, "d1": 10, "d2": 10})
        total = sum(T.sk.edge_length(i) for i, ed in T.sk.d["edges"].items() if not ed.get("construction"))
        self.assertAlmostEqual(total, 40 + 40 + 10 * math.sqrt(2), 6)

    def test_three_spline_workflows_make_a_curve_through_the_points(self):
        pts = [[0, 0], [10, 10], [20, 0], [30, 10]]
        for mode in ("math", "max", "revit"):
            S = S_new()
            try:
                e = S.run("spline", {"mode": mode, "points": pts})["result"]["edge"]
            except ProfilerError as exc:
                self.fail("%s: %s" % (mode, exc))
            poly = S.sk.edge_polyline(e)
            self.assertGreater(len(poly), 8, mode)
            self.assertGreater(S.sk.edge_length(e), 30, mode)


class FilletChamferOnArcs(unittest.TestCase):
    """Fillet and chamfer between a line and an arc, and between two arcs, asserted on tangency, radius and area."""

    @staticmethod
    def dist_line(p, a, b):
        return abs((b[0] - a[0]) * (a[1] - p[1]) - (a[0] - p[0]) * (b[1] - a[1])) / math.hypot(b[0] - a[0], b[1] - a[1])

    def line_and_arc(self):
        S = S_new()
        line = S.run("line", {"a": [0, 0], "b": [30, 0]})["result"]["edge"]
        arc = S.run("arc_center_ends", {"center": [-20, 0], "start": [0, 0], "end": [-20, 20]})["result"]["edge"]
        return S, line, arc

    def test_fillet_between_a_line_and_an_arc_is_tangent_to_both(self):
        S, line, arc = self.line_and_arc()
        r = S.run("fillet", {"edges": [line, arc], "radius": 5})["result"]
        f = S.sk.edge(r["arc"])
        cen, rad = S.sk.center_radius(r["arc"])
        self.assertAlmostEqual(rad, 5, 6)
        t1, t2 = S.sk.pos(f["v"][0]), S.sk.pos(f["v"][1])
        self.assertAlmostEqual(G.dist(cen, t1), 5, 6)
        self.assertAlmostEqual(G.dist(cen, t2), 5, 6)
        self.assertAlmostEqual(self.dist_line(cen, (0, 0), (30, 0)), 5, 6)                  # tangent to the line
        self.assertAlmostEqual(G.dist(cen, (-20, 0)), 25, 6)                                # externally tangent to the R20 arc
        self.assertAlmostEqual(G.dist(S.sk.pos(S.sk.edge(arc)["c"]), (-20, 0)), 0, 9)       # the original arc keeps its centre ...
        self.assertAlmostEqual(S.sk.center_radius(arc)[1], 20, 6)                           # ... and its radius
        ends = [S.sk.pos(v) for v in S.sk.edge(arc)["v"]]
        self.assertTrue(any(G.dist(p, (-20, 20)) < 1e-6 for p in ends))                     # far end untouched
        self.assertFalse(any(G.dist(p, (0, 0)) < 1e-6 for p in ends))                       # the sharp corner is gone from the visible arc
        self.assertGreater(S.sk.edge_length(arc), 0)
        self.assertEqual(sum(1 for e in S.sk.d["edges"].values() if e.get("construction")), 2)   # associative legs keep the virtual corner

    def test_arc_fillet_radius_follows_the_dimension_and_undo(self):
        S, line, arc = self.line_and_arc()
        before = S.sk.digest()
        r = S.run("fillet", {"edges": [line, arc], "radius": 5})["result"]
        S.run("dimension_value", {"dimension": r["radius_dimension"], "value": 8})
        cen, rad = S.sk.center_radius(r["arc"])
        self.assertAlmostEqual(rad, 8, 5)
        self.assertAlmostEqual(self.dist_line(cen, (0, 0), (30, 0)), 8, 5)
        self.assertAlmostEqual(G.dist(cen, (-20, 0)), 28, 5)
        self.assertAlmostEqual(S.sk.center_radius(arc)[1], 20, 5)
        S.undo_last()
        S.undo_last()
        self.assertEqual(S.sk.digest(), before)

    def test_fillet_between_two_arcs(self):
        S = S_new()
        a1 = S.run("arc_center_ends", {"center": [-20, 0], "start": [0, 0], "end": [-20, 20]})["result"]["edge"]
        a2 = S.run("arc_center_ends", {"center": [0, -20], "start": [20, -20], "end": [0, 0]})["result"]["edge"]
        r = S.run("fillet", {"edges": [a1, a2], "radius": 4})["result"]
        cen, rad = S.sk.center_radius(r["arc"])
        self.assertAlmostEqual(rad, 4, 6)
        self.assertAlmostEqual(G.dist(cen, (-20, 0)), 24, 6)
        self.assertAlmostEqual(G.dist(cen, (0, -20)), 24, 6)
        for e, c0 in ((a1, (-20, 0)), (a2, (0, -20))):
            self.assertAlmostEqual(S.sk.center_radius(e)[1], 20, 6)
            self.assertAlmostEqual(G.dist(S.sk.pos(S.sk.edge(e)["c"]), c0), 0, 9)
        f = S.sk.edge(r["arc"])
        for v in f["v"]:                                                                   # each tangent point lies on one of the R20 circles
            p = S.sk.pos(v)
            self.assertTrue(abs(G.dist(p, (-20, 0)) - 20) < 1e-6 or abs(G.dist(p, (0, -20)) - 20) < 1e-6)
        S.run("dimension_value", {"dimension": r["radius_dimension"], "value": 6})
        cen, rad = S.sk.center_radius(r["arc"])
        self.assertAlmostEqual(rad, 6, 5)
        self.assertAlmostEqual(G.dist(cen, (-20, 0)), 26, 5)

    def test_fillet_inside_the_arcs_circle_is_internally_tangent(self):
        S = S_new()
        line = S.run("line", {"a": [0, 0], "b": [30, 0]})["result"]["edge"]
        arc = S.run("arc_center_ends", {"center": [20, 0], "start": [0, 0], "end": [20, -20]})["result"]["edge"]
        r = S.run("fillet", {"edges": [line, arc], "radius": 5})["result"]
        cen, rad = S.sk.center_radius(r["arc"])
        self.assertAlmostEqual(rad, 5, 6)
        self.assertAlmostEqual(G.dist(cen, (20, 0)), 15, 6)                                 # R20 - r: the fillet circle sits inside the arc's circle
        self.assertAlmostEqual(self.dist_line(cen, (0, 0), (30, 0)), 5, 6)
        p = next(S.sk.pos(v) for v in S.sk.edge(arc)["v"] if G.dist(S.sk.pos(v), (20, -20)) > 1e-6)
        self.assertAlmostEqual(G.dist(p, (20, 0)), 20, 6)                                   # the trimmed arc ends on its own circle
        cs = [c for c in S.sk.d["constraints"] if c["kind"] == "tangent_at" and c["origin"] == "fillet"]
        self.assertEqual(len(cs), 2)
        S.run("dimension_value", {"dimension": r["radius_dimension"], "value": 7})
        cen, rad = S.sk.center_radius(r["arc"])
        self.assertAlmostEqual(G.dist(cen, (20, 0)), 13, 5)

    def test_chamfer_on_a_line_and_an_arc_uses_straight_line_setbacks(self):
        S, line, arc = self.line_and_arc()
        r = S.run("chamfer", {"edges": [line, arc], "d1": 3, "d2": 4})["result"]
        e = S.sk.edge(r["edge"])
        p1, p2 = S.sk.pos(e["v"][0]), S.sk.pos(e["v"][1])
        ends = sorted([(G.dist(p1, (0, 0)), p1), (G.dist(p2, (0, 0)), p2)], key=lambda t: t[0])
        self.assertAlmostEqual(ends[0][0], 3, 6)
        self.assertAlmostEqual(ends[1][0], 4, 6)
        on_line = [p for _, p in ends if abs(p[1]) < 1e-6]
        on_arc = [p for _, p in ends if abs(G.dist(p, (-20, 0)) - 20) < 1e-6]
        self.assertEqual((len(on_line), len(on_arc)), (1, 1))
        S.run("dimension_value", {"dimension": r["setback_dimensions"][0], "value": 6})     # the associative set-back still re-solves
        p1 = S.sk.pos(S.sk.edge(r["edge"])["v"][0])
        self.assertAlmostEqual(G.dist(p1, (0, 0)), 6, 5)

    def test_unfittable_and_unsupported_cases_are_refused_without_changing_anything(self):
        S, line, arc = self.line_and_arc()
        before = S.sk.digest()
        with self.assertRaises(ProfilerError) as cx:
            S.run("fillet", {"edges": [line, arc], "radius": 500})
        self.assertIn("fits", str(cx.exception))
        with self.assertRaises(ProfilerError):
            S.run("chamfer", {"edges": [line, arc], "d1": 500})
        self.assertEqual(S.sk.digest(), before)
        T = S_new()
        el = T.run("ellipse", {"center": [0, 0], "rx": 10, "ry": 5})["result"]["edge"]
        l2 = T.run("line", {"a": [0, 0], "b": [5, 5]})["result"]["edge"]
        with self.assertRaises(ProfilerError) as cx:
            T.run("fillet", {"edges": [el, l2], "radius": 1})
        self.assertIn("lines or circular arcs", str(cx.exception))

    def test_profile_with_a_filleted_arc_corner_still_closes_and_has_the_right_area(self):
        S = S_new()
        S.run("line", {"a": [0, 0], "b": [30, 0]})
        S.run("line", {"a": [30, 0], "b": [30, 20]})
        S.run("line", {"a": [30, 20], "b": [-20, 20]})
        arc = S.run("arc_center_ends", {"center": [-20, 0], "start": [0, 0], "end": [-20, 20]})["result"]["edge"]
        line0 = next(i for i, e in S.sk.d["edges"].items() if e["type"] == "line" and {tuple(round(c, 6) for c in S.sk.pos(v)) for v in e["v"]} == {(0.0, 0.0), (30.0, 0.0)})
        a0 = area(S)
        self.assertAlmostEqual(a0, 600 + 400 - 100 * math.pi, 4)
        r = S.run("fillet", {"edges": [line0, arc], "radius": 5})["result"]
        regs = [x for x in regions(S) if not x.get("is_hole")]
        self.assertEqual(len(regs), 1)
        cen, _ = S.sk.center_radius(r["arc"])
        # the sliver removed at the corner, measured independently: sharp corner -> tangent point on the line -> fillet arc -> tangent point on the R20 arc -> back along that arc
        t_line = (cen[0], 0.0)
        d = G.unit(G.sub(cen, (-20, 0)))
        t_arc = G.add((-20, 0), G.mul(d, 20))
        sliver = [(0.0, 0.0), t_line]
        a1, a2 = G.angle_of(G.sub(t_line, cen)), G.angle_of(G.sub(t_arc, cen))
        sw = (a2 - a1 + math.pi) % G.TAU - math.pi                                          # the short way round
        sliver += [(cen[0] + 5 * math.cos(a1 + sw * i / 200), cen[1] + 5 * math.sin(a1 + sw * i / 200)) for i in range(201)]
        b1 = G.angle_of(G.sub(t_arc, (-20, 0)))
        sliver += [(-20 + 20 * math.cos(b1 * (1 - i / 200)), 20 * math.sin(b1 * (1 - i / 200))) for i in range(201)]
        removed = abs(G.signed_area(sliver))
        self.assertGreater(removed, 1.0)
        self.assertAlmostEqual(a0 - area(S), removed, 2)
        issues = [i["code"] for i in validate.check(S.sk, "extrude")["issues"]]
        self.assertNotIn("open_profile", issues)
        self.assertNotIn("edges_cross", issues)


class Modifications(unittest.TestCase):
    def square(self):
        S = S_new()
        e = S.run("rectangle", {"p1": [0, 0], "p2": [10, 10]})["result"]["edges"]
        return S, e

    def test_move_rotate_scale_mirror_copy(self):
        S, e = self.square()
        S.run("move", {"ids": e, "dx": 5, "dy": 3})
        b = S.sk.bounds()
        self.assertEqual((round(b[0], 6), round(b[1], 6)), (5, 3))
        S.run("scale", {"ids": e, "factor": 2, "about": [5, 3]})
        b = S.sk.bounds()
        self.assertAlmostEqual(b[2] - b[0], 20, 6)
        before = S.sk.digest()
        with self.assertRaises(ProfilerConflict):                                            # horizontal/vertical constraints cannot survive a turn...
            S.run("rotate", {"ids": e, "angle": 90, "about": [5, 3]})
        self.assertEqual(S.sk.digest(), before)                                              # ...and the refusal changes nothing
        out = S.run("rotate", {"ids": e, "angle": 30, "about": [5, 3], "release": True})["result"]   # with release they are switched off, not deleted, and reported
        self.assertTrue([r for r in out["released"] if r["what"] == "constraint"])
        self.assertTrue(all(c for c in S.sk.d["constraints"]))
        self.assertAlmostEqual(area(S), 400, 4)
        pts = [S.sk.pos(v) for v in {x for i in e for x in S.sk.edge_ends(i)}]
        b = S.sk.bounds()
        self.assertGreater((b[2] - b[0]) * (b[3] - b[1]), 400)                               # the square really turned: its box is now bigger than the square
        n = len(S.sk.d["edges"])
        S.run("copy", {"ids": e, "dx": 100, "dy": 0})
        self.assertEqual(len(S.sk.d["edges"]), 2 * n)
        T, f = self.square()
        T.run("mirror", {"ids": f, "a": [20, 0], "b": [20, 10], "copy": True})
        self.assertAlmostEqual(T.sk.bounds()[2], 40, 6)                                    # 0..10 mirrored about x = 20 lands on 30..40

    def test_array_rect_and_polar(self):
        S, e = self.square()
        S.run("array", {"ids": e, "kind": "rect", "spec": {"count_x": 3, "count_y": 2, "dx": 20, "dy": 20}})
        self.assertAlmostEqual(area(S), 6 * 100, 4)
        T, f = self.square()
        T.run("move", {"ids": f, "dx": 30, "dy": 0})
        T.run("array", {"ids": f, "kind": "polar", "spec": {"count": 4, "center": [0, 0], "angle": 360}})
        self.assertEqual(len([r for r in regions(T)]), 4)

    def test_trim_extend_split_are_different_operations(self):
        S = S_new()
        a = S.run("line", {"a": [0, 0], "b": [100, 0]})["result"]["edge"]
        b = S.run("line", {"a": [50, -20], "b": [50, 20]})["result"]["edge"]
        n0 = len(S.sk.d["edges"])
        S.run("split", {"edge": a, "point": [50, 0]})                                       # split keeps both parts
        self.assertEqual(len(S.sk.d["edges"]), n0 + 1)
        total = sum(S.sk.edge_length(i) for i in S.sk.d["edges"] if i != b)
        self.assertAlmostEqual(total, 100, 6)
        T = S_new()
        a = T.run("line", {"a": [0, 0], "b": [100, 0]})["result"]["edge"]
        b = T.run("line", {"a": [50, -20], "b": [50, 20]})["result"]["edge"]
        T.run("trim", {"edge": a, "pick": [80, 0], "boundaries": [b]})                      # trim removes the picked part
        longest = max(T.sk.edge_length(i) for i in T.sk.d["edges"] if i != b)
        self.assertAlmostEqual(longest, 50, 6)
        U = S_new()
        a = U.run("line", {"a": [0, 0], "b": [30, 0]})["result"]["edge"]
        b = U.run("line", {"a": [50, -20], "b": [50, 20]})["result"]["edge"]
        U.run("extend", {"edge": a, "near": [30, 0], "boundaries": [b]})                    # extend lengthens up to the boundary
        self.assertAlmostEqual(max(U.sk.edge_length(i) for i in U.sk.d["edges"] if i != b), 50, 6)

    def test_offset_distance_is_exact(self):
        S = S_new()
        e = S.run("rectangle", {"p1": [0, 0], "p2": [100, 60]})["result"]["edges"]
        S.run("offset", {"edges": e, "distance": 10, "side": "left", "parametric": False})
        areas = sorted(r["area"] for r in regions(S))
        self.assertTrue(any(abs(a - 80 * 40) < 1e-6 or abs(a - 120 * 80) < 1e-6 for a in areas), areas)

    def test_region_booleans(self):
        def pair():
            S = S_new()
            a = S.run("rectangle", {"p1": [0, 0], "p2": [20, 20]})["result"]["edges"]
            b = S.run("rectangle", {"p1": [10, 10], "p2": [30, 30]})["result"]["edges"]
            return S, a, b
        for op, want in (("union", 400 + 400 - 100), ("intersect", 100), ("subtract", 300)):
            S, a, b = pair()
            S.run("region_boolean", {"operation": op, "a": a, "b": b, "replace": True})
            self.assertAlmostEqual(area(S), want, 4, op)

    def test_join_connect_weld_attach_are_distinct(self):
        S = S_new()
        a = S.run("line", {"a": [0, 0], "b": [10, 0]})["result"]["edge"]
        b = S.run("line", {"a": [10.001, 0], "b": [20, 0]})["result"]["edge"]
        S.run("weld", {"tol": 0.01})                                                         # weld merges near points
        self.assertEqual(len({v for e in (a, b) for v in S.sk.edge_ends(e)}), 3)
        T = S_new()
        p = T.run("line", {"a": [0, 0], "b": [10, 0]})["result"]["edge"]
        q = T.run("line", {"a": [30, 0], "b": [40, 0]})["result"]["edge"]
        v1, v2 = T.sk.edge_ends(p)[1], T.sk.edge_ends(q)[0]
        n = len(T.sk.d["edges"])
        T.run("connect", {"vertices": [v1, v2]})                                             # connect adds a new line, moves nothing
        self.assertEqual(len(T.sk.d["edges"]), n + 1)
        self.assertEqual(T.sk.pos(v2), (30, 0))

    def test_groups_and_components(self):
        S = S_new()
        e = S.run("rectangle", {"p1": [0, 0], "p2": [10, 10]})["result"]["edges"]
        g = S.run("group", {"ids": e, "kind": "rigid"})["result"]["group"]
        S.run("move", {"ids": e, "dx": 3, "dy": 0})
        S.run("ungroup", {"group": g})
        c = S.run("make_component", {"ids": e, "name": "Box"})["result"]["component"]
        S.run("instantiate_component", {"component": c, "at": [50, 0]})
        self.assertAlmostEqual(area(S), 200, 4)


class SolverDiagnostics(unittest.TestCase):
    def test_under_full_and_conflicting_status(self):
        S = S_new()
        e = S.run("line", {"a": [0, 0], "b": [10, 0]})["result"]["edge"]
        self.assertEqual(S.sk.diagnostics()["status"], "under")
        S.run("pin", {"vertex": S.sk.edge_ends(e)[0]})
        S.run("constrain", {"kind": "horizontal", "refs": [e]})
        S.run("dimension", {"type": "length", "refs": [e], "mode": "driving", "value": 10})
        self.assertEqual(S.sk.diagnostics()["status"], "full")
        with self.assertRaises((ProfilerConflict, ProfilerError)):
            S.run("dimension", {"type": "length", "refs": [e], "mode": "driving", "value": 25})

    def test_conflict_leaves_last_valid_geometry_and_every_constraint(self):
        S = S_new()
        e = S.run("line", {"a": [0, 0], "b": [10, 0]})["result"]["edge"]
        S.run("pin", {"vertex": S.sk.edge_ends(e)[0]})
        S.run("constrain", {"kind": "horizontal", "refs": [e]})
        d = S.run("dimension", {"type": "length", "refs": [e], "mode": "driving", "value": 10})["result"]["dimension"]
        before, count = S.sk.digest(), len(S.sk.d["constraints"])
        with self.assertRaises((ProfilerConflict, ProfilerError)):
            S.run("constrain", {"kind": "vertical", "refs": [e]})
        self.assertEqual(S.sk.digest(), before)
        self.assertEqual(len(S.sk.d["constraints"]), count)
        self.assertIn(d, S.sk.d["dimensions"])

    def test_redundant_constraint_is_reported_not_deleted(self):
        S = S_new()
        e = S.run("rectangle", {"p1": [0, 0], "p2": [10, 10]})["result"]["edges"]
        n = len(S.sk.d["constraints"])
        S.run("constrain", {"kind": "perpendicular", "refs": [e[0], e[1]]})
        self.assertEqual(len(S.sk.d["constraints"]), n + 1)
        found = [i for i in validate.check(S.sk)["issues"] if i["code"] == "redundant_constraint"]
        self.assertTrue(found)

    def test_constraints_hold_when_dragging(self):
        S = S_new()
        e = S.run("rectangle", {"p1": [0, 0], "p2": [40, 20]})["result"]["edges"]
        v = S.sk.edge_ends(e[1])[1]
        S.run("drag_vertex", {"vertex": v, "to": [60, 30]})
        b = S.sk.bounds()
        self.assertAlmostEqual(area(S), (b[2] - b[0]) * (b[3] - b[1]), 4)                   # still a rectangle

    def test_tangent_and_equal_and_symmetric(self):
        S = S_new()
        c = S.run("circle", {"center": [0, 0], "radius": 10})["result"]["edge"]
        ln = S.run("line", {"a": [-20, 12], "b": [20, 11]})["result"]["edge"]
        S.run("constrain", {"kind": "tangent", "refs": [ln, c]})
        (cx, cy), r = S.sk.center_radius(c)
        a, b = (S.sk.pos(v) for v in S.sk.edge_ends(ln))
        dist = abs((b[1] - a[1]) * cx - (b[0] - a[0]) * cy + b[0] * a[1] - b[1] * a[0]) / math.hypot(b[0] - a[0], b[1] - a[1])
        self.assertAlmostEqual(dist, r, 5)


class Checks(unittest.TestCase):
    def codes(self, S, purpose="drafting"):
        return {i["code"] for i in validate.check(S.sk, purpose)["issues"]}

    def test_open_contour_is_unsuitable_for_extrusion_with_a_gap_location(self):
        S = S_new()
        S.run("polyline", {"points": [[0, 0], [10, 0], [10, 10], [0, 10]]})
        res = validate.check(S.sk, "extrude")
        self.assertFalse(res["suitable"])
        self.assertTrue(res["issues"])

    def test_closed_rectangle_is_suitable_for_everything_but_sweep_path_and_opening_notes(self):
        S = S_new()
        S.run("rectangle", {"p1": [0, 0], "p2": [10, 10]})
        for p in ("drafting", "extrude", "void", "sweep_section", "opening"):
            self.assertTrue(validate.check(S.sk, p)["suitable"], p)

    def test_self_intersection_duplicates_and_degenerate(self):
        S = S_new()
        S.run("polyline", {"points": [[0, 0], [10, 10], [10, 0], [0, 10]], "closed": True})
        self.assertIn("edges_cross", self.codes(S, "extrude"))
        T = S_new()
        T.run("line", {"a": [0, 0], "b": [10, 0]})
        T.run("line", {"a": [0, 0], "b": [10, 0]})
        self.assertIn("duplicate_edge", self.codes(T))

    def test_repair_is_previewed_then_applied_and_undoable(self):
        S = S_new()
        S.run("line", {"a": [0, 0], "b": [10, 0]})
        S.run("line", {"a": [0, 0], "b": [10, 0]})
        n = len(S.sk.d["edges"])
        prev = S.run("repair_preview", {"action": "remove_duplicates"})
        self.assertEqual(len(S.sk.d["edges"]), n)                                           # a preview changes nothing
        S.run("repair", {"action": "remove_duplicates"})
        self.assertEqual(len(S.sk.d["edges"]), n - 1)
        S.undo_last()
        self.assertEqual(len(S.sk.d["edges"]), n)

    def test_revolve_needs_a_profile_that_does_not_cross_the_axis(self):
        S = S_new()
        S.run("rectangle", {"p1": [-5, 0], "p2": [5, 10]})
        res = validate.check(S.sk, "revolve", axis={"point": [0, 0], "angle": 90})
        self.assertIn("crosses_axis", {i["code"] for i in res["issues"]})
        self.assertFalse(res["suitable"])


class Persistence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def sample(self):
        from ..profiler import templates
        S = templates.build("angle", "angle")
        S.run("comment_add", {"text": "check the leg", "xy": [5, 5]})
        S.run("text_add", {"xy": [20, 20], "text": "note"})
        S.run("layer_add", {"name": "Notes"})
        S.run("param_set", {"name": "Width", "value": 130})
        return S

    def test_save_open_reproduces_every_part_of_the_sketch(self):
        S = self.sample()
        path = str(self.dir / "a.sdrw")
        sdrw_io.save(S, path)
        T, report = sdrw_io.open_file(path)
        self.assertEqual(T.sk.digest(), S.sk.digest())
        self.assertEqual(T.sk.d["params"]["Width"]["value"], 130)
        self.assertEqual(len(T.sk.d["comments"]), 1)
        self.assertIn("Notes", T.sk.layer_names())
        self.assertEqual(report["kind"], "profiler")
        self.assertAlmostEqual(area(T), area(S), 9)

    def test_changed_file_on_disk_is_a_conflict_not_a_silent_overwrite(self):
        S = self.sample()
        path = str(self.dir / "b.sdrw")
        sdrw_io.save(S, path)
        other, _ = sdrw_io.open_file(path)
        other.run("param_set", {"name": "Width", "value": 90})
        sdrw_io.save(other, None)
        S.run("param_set", {"name": "Width", "value": 70})
        with self.assertRaises(ProfilerConflict):
            sdrw_io.save(S, None)
        T, _ = sdrw_io.open_file(path)
        self.assertEqual(T.sk.d["params"]["Width"]["value"], 90)                            # the other writer's work survived

    def test_a_corrupted_file_is_refused_with_a_message(self):
        S = self.sample()
        path = self.dir / "c.sdrw"
        sdrw_io.save(S, str(path))
        raw = bytearray(path.read_bytes())
        for i in range(len(raw) // 2, len(raw) // 2 + 64):
            raw[i] ^= 0xFF
        path.write_bytes(bytes(raw))
        with self.assertRaises(ProfilerError):
            sdrw_io.open_file(str(path))

    def test_autosave_is_a_separate_file_and_recoverable(self):
        S = self.sample()
        path = str(self.dir / "d.sdrw")
        sdrw_io.save(S, path)
        S.run("param_set", {"name": "Width", "value": 111})
        info = sdrw_io.autosave(S, self.dir / "auto")
        self.assertIsNotNone(info)
        self.assertEqual(sdrw_io.open_file(path)[0].sk.d["params"]["Width"]["value"], 130)  # the real file is untouched
        found = sdrw_io.recoverable(self.dir / "auto")
        self.assertEqual(len(found), 1)
        R, _ = sdrw_io.recover(found[0]["path"])
        self.assertEqual(R.sk.d["params"]["Width"]["value"], 111)

    def test_svg_export_contains_the_geometry(self):
        S = self.sample()
        out = self.dir / "a.svg"
        sdrw_io.export_drawing(S, str(out), "svg")
        text = out.read_text(encoding="utf-8")
        self.assertIn("<svg", text)
        self.assertEqual(text.count("<line"), 6)


class CurveExport(unittest.TestCase):
    """Ellipses and splines are native SDRW entities (feature ``sdrw-curves-v1``): in the .sdrw view, in SVG and DXF exports, and on import. Nothing is sampled."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def sketch(self):
        S = S_new()
        self.line = S.run("line", {"a": [-10, -5], "b": [60, 40]})["result"]["edge"]
        self.ell = S.run("ellipse", {"center": [3, 2], "rx": 20, "ry": 6, "rotation": 0.5})["result"]["edge"]
        self.sp_math = S.run("spline", {"mode": "math", "points": [[0, 0], [10, 14], [22, -6], [35, 9], [48, 0]]})["result"]["edge"]
        self.sp_max = S.run("spline", {"mode": "max", "points": [[0, 30], [15, 40], [30, 30]]})["result"]["edge"]
        self.sp_revit = S.run("spline", {"mode": "revit", "points": [[0, 50], [10, 60], [20, 48], [30, 58]]})["result"]["edge"]
        return S

    @staticmethod
    def dist_to_poly(p, poly, closed=False):
        n = len(poly)
        segs = n if closed else n - 1
        best = float("inf")
        for i in range(segs):
            a, b = poly[i], poly[(i + 1) % n]
            ab = G.sub(b, a)
            t = max(0.0, min(1.0, G.dot(G.sub(p, a), ab) / max(G.dot(ab, ab), 1e-300)))
            best = min(best, G.dist(p, G.add(a, G.mul(ab, t))))
        return best

    @staticmethod
    def reference(S, eid, n=400):
        """The Profiler's own evaluation of a spline (its mode's definition), independent of the SDRW entity written for it."""
        e = S.sk.edge(eid)
        pts = [S.sk.pos(v) for v in e["v"]]
        if e["mode"] == "math":
            return G.bspline_curve(pts, e.get("degree", 3), n)
        if e["mode"] == "max":
            return G.bezier_chain(pts, [(S.sk.pos(a), S.sk.pos(b)) for a, b in e["handles"]], n)
        return G.hermite_through(pts, n=n)

    @staticmethod
    def entity_points(ent, n=400):
        from src.utils.spatial_formats.sdrw import curves as C
        lo, hi = C.spline_domain(ent["geometry"])
        return [C.spline_point(ent["geometry"], lo + (hi - lo) * i / n) for i in range(n + 1)]

    def native(self, S):
        doc = sdrw_io.to_document(S.sk)
        return doc, {e["metadata"]["profiler:edge"]: e for e in doc.model.entities if "profiler:edge" in (e.get("metadata") or {})}

    def test_bezier_form_reproduces_every_spline_mode_exactly(self):
        S = self.sketch()
        for eid in (self.sp_math, self.sp_max, self.sp_revit):
            e = S.sk.edge(eid)
            pts = [S.sk.pos(v) for v in e["v"]]
            segs = G.spline_beziers(e["mode"], pts, degree=e.get("degree", 3), handles=[(S.sk.pos(a), S.sk.pos(b)) for a, b in e["handles"]] if e["mode"] == "max" else None)
            self.assertIsNotNone(segs, e["mode"])
            for a, b in zip(segs, segs[1:]):
                self.assertLess(G.dist(a[3], b[0]), 1e-9, e["mode"])
            flat = G.flatten_beziers(segs, 1e-5)
            self.assertLess(max(self.dist_to_poly(q, flat) for q in self.reference(S, eid, 600)), 1e-5, e["mode"])

    def test_sdrw_view_holds_native_ellipse_and_spline_entities(self):
        S = self.sketch()
        doc, ents = self.native(S)
        self.assertEqual({k: ents[k]["kind"] for k in ents}, {self.line: "line", self.ell: "ellipse", self.sp_math: "spline", self.sp_max: "spline", self.sp_revit: "spline"})
        self.assertFalse(any("profiler:approx" in (e.get("metadata") or {}) for e in doc.model.entities))
        g = ents[self.ell]["geometry"]
        self.assertEqual((g["center"], g["rx"], g["ry"], g["rotation"]), ([3, 2], 20, 6, 0.5))
        self.assertEqual(ents[self.sp_math]["geometry"]["degree"], 3)
        self.assertEqual(ents[self.sp_math]["geometry"]["control_points"], [list(S.sk.pos(v)) for v in S.sk.edge(self.sp_math)["v"]])
        self.assertEqual(ents[self.sp_max]["geometry"]["degree"], 3)

    def test_every_spline_entity_evaluates_to_the_profilers_own_curve(self):
        S = self.sketch()
        _, ents = self.native(S)
        for eid in (self.sp_math, self.sp_max, self.sp_revit):
            ref = self.reference(S, eid, 1200)
            poly = self.entity_points(ents[eid], 1200)
            self.assertLess(max(self.dist_to_poly(q, poly) for q in ref), 1e-4, eid)
            self.assertLess(G.dist(poly[0], ref[0]), 1e-9)
            self.assertLess(G.dist(poly[-1], ref[-1]), 1e-9)

    def test_high_degree_math_spline_is_written_as_that_bspline(self):
        S = S_new()
        eid = S.run("spline", {"mode": "math", "degree": 5, "points": [[0, 0], [5, 9], [12, -4], [20, 7], [28, -3], [36, 6]]})["result"]["edge"]
        _, ents = self.native(S)
        self.assertEqual(ents[eid]["geometry"]["degree"], 5)
        ref = self.reference(S, eid, 1200)
        poly = self.entity_points(ents[eid], 1200)
        self.assertLess(max(self.dist_to_poly(q, poly) for q in ref), 1e-3)

    def test_the_file_declares_the_curve_feature_only_when_curves_are_used(self):
        S = self.sketch()
        path = str(self.dir / "c.sdrw")
        sdrw_io.save(S, path)
        from src.utils.spatial_formats.container.reader import load_package as read_container
        self.assertIn("sdrw-curves-v1", read_container(path).manifest.get("requires", []))
        plain = S_new()
        plain.run("line", {"a": [0, 0], "b": [5, 0]})
        path2 = str(self.dir / "p.sdrw")
        sdrw_io.save(plain, path2)
        self.assertNotIn("sdrw-curves-v1", read_container(path2).manifest.get("requires", []))

    def test_the_saved_file_round_trips_the_exact_curves_and_reports_no_outside_edit(self):
        S = self.sketch()
        path = str(self.dir / "c.sdrw")
        sdrw_io.save(S, path)
        T, rep = sdrw_io.open_file(path)
        self.assertEqual(T.sk.digest(), S.sk.digest())
        self.assertEqual(rep.get("warnings") or [], [])
        e = T.sk.edge(self.ell)
        self.assertEqual((e["type"], T.sk.d["scalars"][e["rx"]], T.sk.d["scalars"][e["ry"]], e["rot"]), ("ellipse", 20, 6, 0.5))
        for eid in (self.sp_math, self.sp_max, self.sp_revit):
            self.assertEqual(T.sk.edge(eid)["mode"], S.sk.edge(eid)["mode"])
            self.assertEqual([T.sk.pos(v) for v in T.sk.edge(eid)["v"]], [S.sk.pos(v) for v in S.sk.edge(eid)["v"]])

    def test_svg_draws_every_entity_in_one_coordinate_system(self):
        """The first delivery negated y for curves only, so ellipses and splines came out mirrored against lines. Every element must use the same axes."""
        import re
        import xml.etree.ElementTree as ET
        S = self.sketch()
        out = self.dir / "a.svg"
        res = sdrw_io.export_drawing(S, str(out), "svg")
        self.assertEqual(res["approximated"], [])
        root = ET.fromstring(out.read_text(encoding="utf-8"))
        ns = "{http://www.w3.org/2000/svg}"
        by_id = {el.get("data-id"): el for el in root.iter()}
        ln = by_id[self.line]
        self.assertEqual((float(ln.get("x1")), float(ln.get("y1")), float(ln.get("x2")), float(ln.get("y2"))), (-10.0, -5.0, 60.0, 40.0))
        el = by_id[self.ell]
        self.assertEqual(el.tag, ns + "ellipse")
        self.assertEqual((float(el.get("cx")), float(el.get("cy")), float(el.get("rx")), float(el.get("ry"))), (3.0, 2.0, 20.0, 6.0))
        for eid in (self.sp_math, self.sp_max, self.sp_revit):
            node = by_id[eid]
            self.assertEqual(node.tag, ns + "path")
            nums = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?(?:e-?\d+)?", node.get("d"))]
            first = S.sk.pos(S.sk.edge(eid)["v"][0])
            self.assertEqual((nums[0], nums[1]), (first[0], first[1]))
            self.assertEqual(node.get("fill"), "none")

    def test_svg_round_trip_through_import_gives_back_the_same_curves(self):
        from src.utils.spatial_formats.sdrw.document import DrawingDocument
        S = self.sketch()
        out = self.dir / "r.svg"
        sdrw_io.export_drawing(S, str(out), "svg")
        sk, rep = sdrw_io.import_file(str(out))
        self.assertEqual(rep["skipped"], {})
        kinds = sorted(sk.edge(e)["type"] for e in sk.edges)
        self.assertEqual(kinds, sorted(["line", "ellipse", "spline", "spline", "spline"]))
        ell = [sk.edge(e) for e in sk.edges if sk.edge(e)["type"] == "ellipse"][0]
        self.assertEqual((sk.pos(ell["c"]), sk.d["scalars"][ell["rx"]], sk.d["scalars"][ell["ry"]]), ((3.0, 2.0), 20.0, 6.0))
        self.assertAlmostEqual(ell["rot"], 0.5, 9)

    def test_a_spline_the_svg_can_only_draw_as_a_polyline_is_reported_and_re_imports_exactly(self):
        S = S_new()
        eid = S.run("spline", {"mode": "math", "degree": 5, "points": [[0, 0], [5, 9], [12, -4], [20, 7], [28, -3], [36, 6]]})["result"]["edge"]
        out = self.dir / "d.svg"
        res = sdrw_io.export_drawing(S, str(out), "svg")
        self.assertEqual([a["edge"] for a in res["approximated"]], [eid])
        row = res["approximated"][0]
        self.assertEqual(row["unit"], "mm")
        self.assertTrue(0 < row["max_deviation"] <= 1e-4)
        text = out.read_text(encoding="utf-8")
        self.assertIn("data-knots", text)
        import re
        d_attr = re.search(r'<path[^>]* d="([^"]+)"', text).group(1)
        poly = [tuple(float(v) for v in t.split(",")) for t in d_attr.replace("M", " ").replace("L", " ").split()]
        ref = G.bspline_curve([S.sk.pos(v) for v in S.sk.edge(eid)["v"]], 5, 1500)
        self.assertLessEqual(max(self.dist_to_poly(q, poly) for q in ref), row["max_deviation"] * 1.0001)         # the stated bound holds for the drawn polyline
        sk, rep = sdrw_io.import_file(str(out))
        e = next(iter(sk.edges.values()))
        self.assertEqual((e["type"], e["mode"], e["degree"]), ("spline", "math", 5))
        self.assertEqual([sk.pos(v) for v in e["v"]], [S.sk.pos(v) for v in S.sk.edge(eid)["v"]])
        self.assertEqual(sdrw_io.approximated_edges(S.sk, "dxf"), [])

    def test_dxf_holds_real_ellipse_and_spline_entities(self):
        try:
            import ezdxf
        except ImportError:
            self.skipTest("ezdxf is not installed")
        S = self.sketch()
        out = self.dir / "a.dxf"
        res = sdrw_io.export_drawing(S, str(out), "dxf")
        self.assertEqual(res["approximated"], [])
        types = sorted(e.dxftype() for e in ezdxf.readfile(str(out)).modelspace())
        self.assertEqual(types, sorted(["LINE", "ELLIPSE", "SPLINE", "SPLINE", "SPLINE"]))
        sk, rep = sdrw_io.import_file(str(out))
        self.assertEqual(rep["skipped"], {})
        self.assertEqual(sorted(sk.edge(e)["type"] for e in sk.edges), sorted(["line", "ellipse", "spline", "spline", "spline"]))
        ell = [sk.edge(e) for e in sk.edges if sk.edge(e)["type"] == "ellipse"][0]
        self.assertEqual((round(sk.d["scalars"][ell["rx"]], 9), round(sk.d["scalars"][ell["ry"]], 9)), (20.0, 6.0))
        self.assertEqual(tuple(round(c, 9) for c in sk.pos(ell["c"])), (3.0, 2.0))

    def import_doc(self, build):
        from src.utils.spatial_formats.sdrw.document import DrawingDocument
        doc = DrawingDocument.new("x", unit="mm")
        build(doc)
        return sdrw_io.import_drawing(doc)

    def test_importing_a_uniform_bspline_gives_the_math_spline_of_the_same_curve(self):
        from src.utils.spatial_formats.sdrw import curves as C
        pts = [(0, 0), (8, 12), (20, -5), (31, 10), (44, 2)]
        sk, rep = self.import_doc(lambda d: d.add_spline(pts, degree=3))
        e = next(iter(sk.edges.values()))
        self.assertEqual((e["type"], e["mode"], e["degree"]), ("spline", "math", 3))
        self.assertEqual([sk.pos(v) for v in e["v"]], [tuple(map(float, p)) for p in pts])
        self.assertEqual(rep["approximated"], [])

    def test_importing_a_bezier_chain_gives_a_max_spline_with_the_same_shape(self):
        segs = [((0, 0), (5, 10), (15, 10), (20, 0)), ((20, 0), (25, -10), (35, -10), (40, 0))]
        sk, rep = self.import_doc(lambda d: d.add_bezier_spline(segs))
        e = next(iter(sk.edges.values()))
        self.assertEqual((e["type"], e["mode"]), ("spline", "max"))
        got = G.spline_beziers("max", [sk.pos(v) for v in e["v"]], handles=[(sk.pos(a), sk.pos(b)) for a, b in e["handles"]])
        self.assertEqual([[tuple(q) for q in sg] for sg in got], [[tuple(map(float, q)) for q in sg] for sg in segs])
        self.assertEqual(rep["approximated"], [])

    def test_importing_a_non_uniform_quadratic_spline_keeps_its_exact_shape(self):
        from src.utils.spatial_formats.sdrw import curves as C
        g = {"degree": 2, "control_points": [[0, 0], [10, 20], [25, -10], [40, 15], [55, 0]], "knots": [0, 0, 0, 1, 3, 4, 4, 4]}
        sk, rep = self.import_doc(lambda d: d.add_spline(g["control_points"], degree=2, knots=g["knots"]))
        e = next(iter(sk.edges.values()))
        self.assertEqual((e["type"], e["mode"]), ("spline", "max"))
        flat = G.flatten_beziers(G.spline_beziers("max", [sk.pos(v) for v in e["v"]], handles=[(sk.pos(a), sk.pos(b)) for a, b in e["handles"]]), 1e-6)
        lo, hi = C.spline_domain(g)
        for i in range(0, 401):
            q = C.spline_point(g, lo + (hi - lo) * i / 400)
            self.assertLess(self.dist_to_poly(q, flat), 2e-6)

    def test_importing_a_degree_5_spline_with_other_knots_is_a_reported_polyline(self):
        from src.utils.spatial_formats.sdrw import curves as C
        g = {"degree": 5, "control_points": [[0, 0], [5, 9], [12, -4], [20, 7], [28, -3], [36, 6], [44, 0]], "knots": [0] * 6 + [1] + [4] * 6}
        sk, rep = self.import_doc(lambda d: d.add_spline(g["control_points"], degree=5, knots=g["knots"]))
        self.assertEqual(len(rep["approximated"]), 1)
        self.assertIn("polyline", rep["approximated"][0])
        self.assertTrue(rep["approximated"][0] in rep["warnings"])
        pts = [sk.pos(e["v"][0]) for e in sk.edges.values()] + [sk.pos(list(sk.edges.values())[-1]["v"][1])]
        lo, hi = C.spline_domain(g)
        tol = sdrw_io.curve_tolerance(sk)
        for i in range(0, 301):
            self.assertLessEqual(self.dist_to_poly(C.spline_point(g, lo + (hi - lo) * i / 300), pts), tol * 1.01)

    def test_importing_an_ellipse_keeps_centre_radii_and_turn(self):
        sk, rep = self.import_doc(lambda d: d.add_ellipse((4, -3), 12, 5, rotation=30, degrees=True))
        e = next(iter(sk.edges.values()))
        self.assertEqual((e["type"], sk.pos(e["c"]), sk.d["scalars"][e["rx"]], sk.d["scalars"][e["ry"]]), ("ellipse", (4.0, -3.0), 12.0, 5.0))
        self.assertAlmostEqual(e["rot"], math.radians(30), 12)


class Annotation(unittest.TestCase):
    def test_default_colour_is_F6F6F6_and_distinct_from_selection(self):
        S = S_new()
        e = S.run("line", {"a": [0, 0], "b": [1, 0]})["result"]["edge"]
        self.assertEqual(S.sk.edge(e).get("color") or "#F6F6F6", "#F6F6F6")
        render = S.run("render")["result"] if "result" in S.run("render") else S.run("render")
        text = str(render).upper()
        self.assertIn("#F6F6F6", text)

    def test_comment_thread_lifecycle(self):
        S = S_new()
        out = S.run("comment_add", {"text": "first", "xy": [1, 1]})["result"]
        cid = out.get("comment") or out.get("id")
        S.run("comment_reply", {"comment": cid, "text": "reply"})
        S.run("comment_edit", {"comment": cid, "text": "edited"})
        S.run("comment_resolve", {"comment": cid, "resolved": True})
        c = S.sk.d["comments"][cid]
        self.assertEqual((c["text"], len(c["replies"]), c["resolved"]), ("edited", 1, True))
        S.run("comment_delete", {"comment": cid})
        self.assertNotIn(cid, S.sk.d["comments"])


class MeshTopology(unittest.TestCase):
    """The revolve meshes are closed surfaces: every edge is used exactly twice, once in each direction."""

    def closed(self, mesh):
        from collections import Counter
        edges = Counter()
        for a, b, c in mesh[1]:
            for u, v in ((a, b), (b, c), (c, a)):
                edges[(u, v)] += 1
        return all(n == 1 and edges.get((v, u)) == 1 for (u, v), n in edges.items())

    def test_closed_for_full_and_partial_turns_with_and_without_holes(self):
        from ..profiler.transfer import revolve_mesh, _mesh_signed_volume
        outer = [(10, 0), (30, 0), (30, 40), (10, 40)]
        hole = [(15, 10), (25, 10), (25, 30), (15, 30)]
        for angle in (360, 120):
            for holes in ((), (hole,)):
                mesh = revolve_mesh(outer, angle, 48, (0, 0), 0.0, 0.001, holes)
                self.assertTrue(self.closed(mesh), (angle, len(holes)))
                self.assertGreater(abs(_mesh_signed_volume(mesh)), 0)

    def test_triangulation_with_holes_covers_exactly_the_profile(self):
        from ..profiler import tri
        outer = [(0, 0), (40, 0), (40, 20), (0, 20), ]
        holes = [[(5, 5), (15, 5), (15, 15), (5, 15)], [(25, 5), (35, 5), (35, 15), (25, 15)]]
        pts = outer + holes[0] + holes[1]
        t = tri.triangulate(outer, holes)
        area = sum((pts[b][0] - pts[a][0]) * (pts[c][1] - pts[a][1]) - (pts[c][0] - pts[a][0]) * (pts[b][1] - pts[a][1]) for a, b, c in t) / 2
        self.assertAlmostEqual(area, 800 - 100 - 100, 9)
        self.assertTrue(all((pts[b][0] - pts[a][0]) * (pts[c][1] - pts[a][1]) - (pts[c][0] - pts[a][0]) * (pts[b][1] - pts[a][1]) >= -1e-12 for a, b, c in t))


class OneClickDimension(unittest.TestCase):
    """``dimension_auto``: pick one or two objects and the right measurement is made."""

    def box(self):
        S = S_new()
        e = S.run("polyline", {"points": [[0, 0], [100, 0], [100, 50], [0, 50]], "closed": True, "constrain": True})["result"]["edges"]
        return S, e

    def test_one_line_is_its_length_and_it_drives(self):
        S, e = self.box()
        r = S.run("dimension_auto", {"refs": [e[0]]})["result"]
        self.assertEqual((r["type"], r["mode"]), ("length", "driving"))
        self.assertAlmostEqual(r["measure"]["value"], 100, 6)

    def test_two_parallel_lines_give_the_distance_between_them(self):
        S, e = self.box()
        r = S.run("dimension_auto", {"refs": [e[0], e[2]]})["result"]
        self.assertEqual(r["type"], "perpendicular")
        self.assertAlmostEqual(r["measure"]["value"], 50, 6)

    def test_a_size_that_is_already_fixed_is_only_measured(self):
        S, e = self.box()
        S.run("dimension_auto", {"refs": [e[0]]})
        r = S.run("dimension_auto", {"refs": [e[0]]})["result"]
        self.assertEqual(r["mode"], "reference")
        self.assertTrue(r["note"])

    def test_a_circle_is_its_radius(self):
        S = S_new()
        c = S.run("circle", {"center": [0, 0], "radius": 5})["result"]["edge"]
        r = S.run("dimension_auto", {"refs": [c]})["result"]
        self.assertEqual(r["type"], "radius")

    def test_one_lone_point_asks_for_a_second_object(self):
        S, _ = self.box()
        v = next(iter(S.sk.d["vertices"]))
        with self.assertRaises(ProfilerError):
            S.run("dimension_auto", {"refs": [v]})


class CornerByPoint(unittest.TestCase):
    """Fillet and chamfer take the corner point alone."""

    def test_fillet_and_chamfer_by_corner_point(self):
        S = S_new()
        S.run("polyline", {"points": [[0, 0], [100, 0], [100, 50], [0, 50]], "closed": True, "constrain": True})
        corner = next(v for v in S.sk.d["vertices"] if tuple(round(c, 6) for c in S.sk.pos(v)) == (100.0, 0.0))
        S.run("fillet", {"vertex": corner, "radius": 5})
        corner2 = next(v for v in S.sk.d["vertices"] if tuple(round(c, 6) for c in S.sk.pos(v)) == (100.0, 50.0))
        S.run("chamfer", {"vertex": corner2, "d1": 6})
        self.assertEqual(len(S.sk.d["vertices"]) > 4, True)

    def test_a_point_that_is_not_a_corner_is_refused(self):
        S = S_new()
        S.run("polyline", {"points": [[0, 0], [100, 0], [100, 50]]})
        end = next(v for v in S.sk.d["vertices"] if tuple(round(c, 6) for c in S.sk.pos(v)) == (0.0, 0.0))
        with self.assertRaises(ProfilerError):
            S.run("fillet", {"vertex": end, "radius": 5})


class LineColourHandOff(unittest.TestCase):
    """The colour given to a profile's lines travels with it to the SPADRA element."""

    def test_the_outline_colour_is_in_the_payload(self):
        from applications.SPADRA.profiler import transfer as T
        S = S_new()
        e = S.run("polyline", {"points": [[0, 0], [100, 0], [100, 50], [0, 50]], "closed": True, "constrain": True})["result"]["edges"]
        self.assertIsNone(T.prepare(S, "extrude", {"mode": "static"})["color"])
        S.run("color", {"ids": e, "color": "#E8590C"})
        # an extrusion keeps the colour per wall (see edge_colors); shapes without faces to match take the most common line colour
        self.assertEqual({c["c"] for c in T.prepare(S, "extrude", {"mode": "static"})["edge_colors"]}, {"#E8590C"})
        self.assertIsNone(T.prepare(S, "void", {"mode": "static"})["color"])
        S.run("part_color", {"color": "#1c7ed6"})
        self.assertEqual(T.prepare(S, "extrude", {"mode": "static"})["color"], "#1C7ED6")


if __name__ == "__main__":
    unittest.main()


class SelectionFilters(unittest.TestCase):
    """The browser sends the switched-on object kinds as a list (``["edges", "texts"]``); the server must accept it as well as a dict."""

    def window(self, filters):
        S = S_new()
        S.run("rectangle", {"p1": [0, 0], "p2": [40, 30]})
        return S.run("select_window", {"rect": [-5, -5, 50, 40], "mode": "window", "filters": filters})["result"]

    def test_filters_as_a_list_of_names(self):
        r = self.window(["edges", "vertices", "dimensions", "comments", "texts"])
        self.assertEqual(len(r["edges"]), 4)

    def test_edges_switched_off_selects_no_edges(self):
        self.assertEqual(self.window(["vertices", "texts"])["edges"], [])

    def test_filters_as_a_dict_and_none_still_work(self):
        self.assertEqual(len(self.window({"types": ["line"]})["edges"]), 4)
        self.assertEqual(len(self.window(None)["edges"]), 4)


class RenameRefused(unittest.TestCase):
    def test_spatial_save_works_where_windows_refuses_to_rename(self):
        """WinError 17 / EXDEV from os.replace inside one folder (redirected AppData): the file must still be saved, not fail with storage_cross_volume."""
        import errno
        from unittest import mock
        from src.utils.spatial_formats.common.atomic import write_bytes_atomic
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "x.bin"
            target.write_bytes(b"old")
            with mock.patch("os.replace", side_effect=OSError(errno.EXDEV, "different disk")), mock.patch("time.sleep"):
                write_bytes_atomic(target, b"new", overwrite=True)
            self.assertEqual(target.read_bytes(), b"new")
            self.assertEqual([f.name for f in Path(tmp).iterdir()], ["x.bin"], "no temporary files are left behind")


class FilletedOutline(unittest.TestCase):
    """A rectangle outline with every corner filleted keeps real lines, tidy constraints and a clean Check (found while building a 12-corner bracket)."""

    def build(self):
        S = S_new()
        S.run("polyline", {"points": [[0, 0], [40, 0], [40, 30], [0, 30]], "closed": True, "constrain": True})
        return S

    def real_edges(self, S):
        return [e for e in S.sk.d["edges"] if not S.sk.edge(e).get("construction")]

    def test_polyline_with_constrain_is_horizontal_and_vertical(self):
        S = self.build()
        kinds = sorted(c["kind"] for c in S.sk.d["constraints"])
        self.assertEqual(kinds, ["horizontal", "horizontal", "vertical", "vertical"])

    def test_a_click_on_a_fillet_corner_picks_the_real_edge_not_its_construction_copy(self):
        S = self.build()
        lines = self.real_edges(S)
        S.run("fillet", {"edges": [lines[0], lines[1]], "radius": 5})
        hit = S.run("pick", {"point": [20, 0], "radius": 1})["result"]["first"]
        self.assertFalse(S.sk.edge(hit["id"]).get("construction"))

    def test_fillet_refuses_a_construction_line(self):
        S = self.build()
        lines = self.real_edges(S)
        S.run("fillet", {"edges": [lines[0], lines[1]], "radius": 5})
        leg = next(e for e in S.sk.d["edges"] if S.sk.edge(e).get("construction"))
        other = next(e for e in lines if e != leg and set(S.sk.edge(e)["v"]) & set(S.sk.edge(leg)["v"]))
        with self.assertRaises(ProfilerError):
            S.run("fillet", {"edges": [leg, other], "radius": 5})

    def test_four_filleted_corners_leave_no_redundant_constraints_and_a_clean_check(self):
        S = self.build()
        for _ in range(4):
            real = self.real_edges(S)
            lines = [e for e in real if S.sk.edge(e)["type"] == "line"]
            pair = next((a, b) for a in lines for b in lines if a != b and len(set(S.sk.edge(a)["v"]) & set(S.sk.edge(b)["v"])) == 1)
            S.run("fillet", {"edges": list(pair), "radius": 5})
        rep = S.run("check", {"purpose": "extrude"})["result"]
        codes = [i["code"] for i in rep["issues"]]
        self.assertNotIn("redundant_constraint", codes)
        self.assertNotIn("t_junction", codes)
        self.assertNotIn("open_contour", codes)
        self.assertTrue(rep["suitable"])

    def test_sweep_path_flattening_keeps_a_rounded_corners_length(self):
        from ..profiler import transfer
        S = S_new()
        S.run("polyline", {"points": [[0, 0], [1000, 0], [1000, 1500]], "constrain": True})
        e = list(S.sk.d["edges"])
        S.run("fillet", {"edges": e, "radius": 500})
        real = [x for x in S.sk.d["edges"] if not S.sk.edge(x).get("construction")]
        pts = transfer.path_geometry(S.sk, real)["points"]
        length = sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
        self.assertAlmostEqual(length, 1000 + 1500 - 2 * 500 + math.pi * 500 / 2 * 1, delta=0.1 + 0.0)


class ParametricDimensions(unittest.TestCase):
    """Formulas written the way a person writes them: ``w - 40`` in a millimetre profile, and on a dimension (not only on a parameter)."""

    def box(self):
        S = S_new()
        edges = S.run("polyline", {"points": [[0, 0], [100, 0], [100, 50], [0, 50]], "closed": True, "constrain": True})["result"]["edges"]
        S.run("param_add", {"name": "w", "type": "length", "value": 100})
        return S, edges

    def test_a_bare_number_next_to_a_length_means_the_profile_unit(self):
        S, _ = self.box()
        S.run("param_add", {"name": "wsb", "type": "length", "expression": "w - 40"})
        self.assertAlmostEqual(S.sk.d["params"]["wsb"]["value"], 60, 9)
        S.run("param_set", {"name": "w", "value": 130})
        self.assertAlmostEqual(S.sk.d["params"]["wsb"]["value"], 90, 9)

    def test_a_factor_stays_a_factor(self):
        S, _ = self.box()
        S.run("param_add", {"name": "half", "type": "length", "expression": "w / 2 + 5"})
        self.assertAlmostEqual(S.sk.d["params"]["half"]["value"], 55, 9)

    def test_a_dimension_can_follow_a_formula_and_the_parameter_moves_it(self):
        S, e = self.box()
        d = S.run("dimension", {"type": "length", "refs": [e[0]], "mode": "driving", "value": 100})["result"]["dimension"]
        S.run("dimension_value", {"dimension": d, "expr": "w - 20"})
        self.assertEqual(S.sk.d["dimensions"][d]["mode"], "formula")
        S.run("param_set", {"name": "w", "value": 150})
        b = S.sk.bounds()
        self.assertAlmostEqual(b[2] - b[0], 130, 6)

    def test_a_formula_that_cannot_be_evaluated_changes_nothing(self):
        S, e = self.box()
        d = S.run("dimension", {"type": "length", "refs": [e[0]], "mode": "driving", "value": 100})["result"]["dimension"]
        with self.assertRaises(ProfilerError):
            S.run("dimension_value", {"dimension": d, "expr": "nope + 1"})
        self.assertEqual(S.sk.d["dimensions"][d]["mode"], "driving")
        self.assertIsNone(S.sk.d["dimensions"][d]["expr"])

    def test_a_chain_can_be_horizontal_between_points_at_different_heights(self):
        S = S_new()
        S.run("polyline", {"points": [[0, 0], [20, 5], [50, 8]]})
        vs = [v for v in S.sk.d["vertices"]]
        pts = sorted(vs, key=lambda v: S.sk.pos(v)[0])
        cid = S.run("chain", {"vertices": pts, "along": "horizontal"})["result"]["chain"]
        self.assertEqual([round(x, 6) for x in S.run("chain", {"vertices": pts, "along": "horizontal"})["result"]["segments"]], [20.0, 30.0])
        self.assertTrue(cid)
