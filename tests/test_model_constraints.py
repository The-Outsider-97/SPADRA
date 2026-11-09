"""Geometric constraints between walls, beams and columns (plan geometry), solved by SLAI's constraint engine and applied through ordinary element edits. Every claim
is checked on the real elements after the solve: corners coincide, sides are parallel or square, lengths are the stated ones, and a refused solve changes nothing."""
from __future__ import annotations

import math
import unittest

from applications.SPADRA import elements as E
from applications.SPADRA import model_constraints as MC
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest, Conflict, NotFound

from .harness import ServerCase


def wall_ends(p, oid):
    f = E.wall_frame(p, oid)
    return f["start"], f["end"]


class Base(unittest.TestCase):
    def setUp(self):
        self.p = Project.create("Constraints")
        self.l0 = self.p.manifest["levels"][0]["id"]

    def x(self, op, **kw):
        return self.p.execute(op, kw)["result"]

    def wall(self, start, end):
        return self.x("model.add_wall", level=self.l0, start=start, end=end, thickness=200, height=3.0)["id"]

    def skew_rectangle(self):
        """Four walls drawn roughly as a 6 m x 4 m rectangle: the first exactly horizontal (it will be the fixed one), the others a few centimetres off, ends not quite meeting."""
        self.a = self.wall([0, 0], [6000, 0])
        self.b = self.wall([6030, 20], [6000, 4000])
        self.c = self.wall([6010, 4010], [20, 3990])
        self.d = self.wall([10, 4020], [-15, 40])
        return self.a, self.b, self.c, self.d

    def add(self, kind, refs, value=None):
        kw = {"kind": kind, "refs": refs}
        if value is not None:
            kw["value"] = value
        return self.x("model.constraint_add", **kw)["id"]

    def close_rectangle(self):
        a, b, c, d = self.skew_rectangle()
        for p, q in ((a, b), (b, c), (c, d), (d, a)):
            self.add("coincident", [{"element": p, "part": "end"}, {"element": q, "part": "start"}])
        self.add("horizontal", [{"element": a}])
        self.add("perpendicular", [{"element": a}, {"element": b}])
        self.add("parallel", [{"element": a}, {"element": c}])
        self.add("perpendicular", [{"element": a}, {"element": d}])
        return a, b, c, d


class Solving(Base):
    def test_four_walls_become_an_exact_rectangle_with_one_wall_held_still(self):
        a, b, c, d = self.close_rectangle()
        a_before = wall_ends(self.p, a)
        self.add("distance", [{"element": b, "part": "start"}, {"element": b, "part": "end"}], 3500)
        r = self.x("model.constraints_solve", fixed=[a])
        self.assertTrue(r["max_residual_after"] < 1e-6)
        self.assertEqual(wall_ends(self.p, a), a_before)                                            # the fixed wall did not move
        (a0, a1), (b0, b1), (c0, c1), (d0, d1) = (wall_ends(self.p, i) for i in (a, b, c, d))
        for end, start in ((a1, b0), (b1, c0), (c1, d0), (d1, a0)):
            self.assertAlmostEqual(math.dist(end, start), 0.0, places=7)                              # the corners meet
        self.assertAlmostEqual(a1[1], a0[1], places=7)                                              # a is horizontal ...
        self.assertAlmostEqual(a0[1], 0.0, places=7)
        dot = lambda u, v: u[0] * v[0] + u[1] * v[1]                                                 # noqa: E731
        va, vb, vc, vd = ((e[0] - s[0], e[1] - s[1]) for s, e in ((a0, a1), (b0, b1), (c0, c1), (d0, d1)))
        self.assertAlmostEqual(dot(va, vb), 0.0, places=6)                                          # square corners
        self.assertAlmostEqual(dot(va, vd), 0.0, places=6)
        self.assertAlmostEqual(va[0] * vc[1] - va[1] * vc[0], 0.0, places=6)                        # c parallel to a
        self.assertAlmostEqual(math.hypot(*vb), 3.5, places=6)                                      # the stated 3500 mm

    def test_the_analysis_before_solving_reports_freedom_and_changes_nothing(self):
        a, b, c, d = self.close_rectangle()
        before = [wall_ends(self.p, i) for i in (a, b, c, d)]
        rev = self.p.revision
        out = MC.analyze(self.p, [a])
        self.assertEqual(out["status"], "under_constrained")                                        # the rectangle's height is free
        self.assertGreater(out["dof"], 0)
        self.assertEqual(out["unresolved"], [])
        self.assertTrue(out["moves"])                                                               # the walls would move to meet
        self.assertEqual(([wall_ends(self.p, i) for i in (a, b, c, d)], self.p.revision), (before, rev))
        self.assertIn("local analysis", out["scope"])

    def test_a_fully_determined_system_reports_none_free_and_the_redundancy(self):
        a, b, c, d = self.close_rectangle()
        self.add("distance", [{"element": b, "part": "start"}, {"element": b, "part": "end"}], 3500)
        out = MC.analyze(self.p, [a])
        self.assertEqual(out["dof"], 0)
        self.assertEqual(out["status"], "over_constrained_consistent")                               # a closed loop with square corners and a parallel pair says some of it twice
        self.assertTrue(out["redundant"])
        self.assertTrue(all(c["satisfied"] for c in out["constraints"]))

    def test_solving_is_one_undo_step_and_undo_restores_the_walls(self):
        a, b, c, d = self.close_rectangle()
        before = [wall_ends(self.p, i) for i in (a, b, c, d)]
        n = len(self.p.undo_stack)
        self.x("model.constraints_solve", fixed=[a])
        self.assertEqual(len(self.p.undo_stack), n + 1)
        self.assertNotEqual([wall_ends(self.p, i) for i in (a, b, c, d)], before)
        self.p.undo()
        self.assertEqual([wall_ends(self.p, i) for i in (a, b, c, d)], before)

    def test_an_already_satisfied_system_moves_nothing(self):
        a = self.wall([0, 0], [4000, 0])
        self.add("horizontal", [{"element": a}])
        r = self.x("model.constraints_solve")
        self.assertEqual(r["moved"], {})

    def test_hosted_openings_follow_a_stretched_wall(self):
        a = self.wall([0, 0], [4000, 30])
        door = self.x("model.add_door", wall=a, offset=1.0, width=900)["id"]
        self.add("horizontal", [{"element": a}])
        self.x("model.constraints_solve", fixed=[])
        s, e = wall_ends(self.p, a)
        self.assertAlmostEqual(s[1], e[1], places=7)
        self.assertTrue(E.alive(self.p, door))
        self.assertEqual(E.m(E.meta_of(self.p, door), "host"), a)

    def test_a_column_is_pulled_onto_a_wall_line_and_a_beam_joins_a_wall_end(self):
        wall = self.wall([0, 0], [6000, 0])
        col = self.x("model.add_column", level=self.l0, point=[2000, 90])["id"]
        beam = self.x("model.add_beam", level=self.l0, start=[6100, 80], end=[6100, 5000], top=3.0)["id"]
        self.add("point_on_line", [{"element": col}, {"element": wall}])
        self.add("coincident", [{"element": beam, "part": "start"}, {"element": wall, "part": "end"}])
        self.x("model.constraints_solve", fixed=[wall])
        cx, cy = E.m(E.meta_of(self.p, col), "point")
        self.assertAlmostEqual(cy, 0.0, places=7)                                                    # on the wall's line
        bs = E.m(E.meta_of(self.p, beam), "start")
        self.assertAlmostEqual(math.dist(bs, wall_ends(self.p, wall)[1]), 0.0, places=7)             # at its end
        self.assertEqual(wall_ends(self.p, wall), ((0.0, 0.0), (6.0, 0.0)))


class Refusals(Base):
    def test_conflicting_constraints_are_refused_and_nothing_moves(self):
        a = self.wall([0, 0], [4000, 0])
        self.add("distance", [{"element": a, "part": "start"}, {"element": a, "part": "end"}], 4000)
        self.add("distance", [{"element": a, "part": "start"}, {"element": a, "part": "end"}], 5000)
        before, rev = wall_ends(self.p, a), self.p.revision
        with self.assertRaises(Conflict) as cm:
            self.x("model.constraints_solve", fixed=[])
        self.assertIn("conflict", str(cm.exception))
        self.assertEqual((wall_ends(self.p, a), self.p.revision), (before, rev))
        self.assertIn(MC.analyze(self.p)["status"], ("conflicting",))

    def test_the_fixed_wall_cannot_be_asked_to_move(self):
        a = self.wall([0, 0], [4000, 0])
        self.add("distance", [{"element": a, "part": "start"}, {"element": a, "part": "end"}], 5000)
        before = wall_ends(self.p, a)
        with self.assertRaises(Conflict):
            self.x("model.constraints_solve", fixed=[a])
        self.assertEqual(wall_ends(self.p, a), before)

    def test_a_deleted_element_leaves_an_unresolved_reference_and_blocks_the_solve(self):
        a, b = self.wall([0, 0], [4000, 0]), self.wall([4000, 0], [4000, 3000])
        cid = self.add("coincident", [{"element": a, "part": "end"}, {"element": b, "part": "start"}])
        self.x("model.delete", id=b)
        listed = next(c for c in MC.describe(self.p) if c["id"] == cid)
        self.assertFalse(listed["valid"])
        with self.assertRaises(Conflict) as cm:
            self.x("model.constraints_solve")
        self.assertIn("unresolved", str(cm.exception))

    def test_the_analysis_names_the_state_of_an_unresolved_reference(self):
        """Report 5.4 / P1-04: a reference is resolved, missing, stale, ambiguous or unsupported. By id a SPADRA reference can be resolved or missing (the neutral
        engine's other states are covered in SLAI's engine tests and cannot arise from an id here); the analysis says which, per constraint."""
        a, b = self.wall([0, 0], [4000, 0]), self.wall([4000, 0], [4000, 3000])
        self.add("coincident", [{"element": a, "part": "end"}, {"element": b, "part": "start"}])
        self.assertEqual(MC.analyze(self.p)["unresolved"], [])
        self.x("model.delete", id=b)
        report = MC.analyze(self.p)
        self.assertEqual([(u["state"], u["kind"]) for u in report["unresolved"]], [("missing", "coincident")])
        self.assertIn(b, report["unresolved"][0]["message"])

    def test_bad_constraints_are_refused_at_the_door(self):
        a, b = self.wall([0, 0], [4000, 0]), self.wall([0, 0], [0, 3000])
        door = self.x("model.add_door", wall=a, offset=2.0, width=900)["id"]
        seg = lambda i, part=None: {"element": i, **({"part": part} if part else {})}                 # noqa: E731
        bad = [
            dict(kind="spiral", refs=[seg(a)]),
            dict(kind="parallel", refs=[seg(a)]),                                                     # wrong arity
            dict(kind="parallel", refs=[seg(a, "start"), seg(b)]),                                    # a point where a whole segment is needed
            dict(kind="coincident", refs=[seg(a), seg(b, "start")]),                                  # a whole segment where a point is needed
            dict(kind="coincident", refs=[seg(a, "start"), seg(a, "start")]),                         # a point with itself
            dict(kind="distance", refs=[seg(a, "start"), seg(b, "start")]),                           # no value
            dict(kind="distance", refs=[seg(a, "start"), seg(b, "start")], value=-5),
            dict(kind="angle", refs=[seg(a), seg(b)], value=270),
            dict(kind="horizontal", refs=[seg(a)], value=5),                                          # a value it does not take
            dict(kind="horizontal", refs=[seg(door)]),                                                # not a wall, beam or column
            dict(kind="horizontal", refs=[seg("nope")]),
            dict(kind="horizontal", refs=[{"element": a, "part": "middle"}]),
            dict(kind="horizontal", refs="a"),
        ]
        for kw in bad:
            with self.subTest(kw=kw):
                with self.assertRaises((BadRequest, NotFound)):
                    self.x("model.constraint_add", **kw)
        self.assertEqual(MC.constraints(self.p), [])

    def test_a_column_has_no_ends(self):
        col = self.x("model.add_column", level=self.l0, point=[1000, 1000])["id"]
        other = self.x("model.add_column", level=self.l0, point=[2000, 1000])["id"]
        with self.assertRaises(BadRequest):
            self.x("model.constraint_add", kind="coincident", refs=[{"element": col, "part": "start"}, {"element": other}])

    def test_nothing_to_solve(self):
        with self.assertRaises(BadRequest):
            self.x("model.constraints_solve")
        self.assertEqual(MC.analyze(self.p)["status"], "none")

    def test_deleting_a_constraint(self):
        a = self.wall([0, 0], [4000, 30])
        cid = self.add("horizontal", [{"element": a}])
        self.x("model.constraint_delete", id=cid)
        self.assertEqual(MC.constraints(self.p), [])
        with self.assertRaises(NotFound):
            self.x("model.constraint_delete", id=cid)

    def test_constraints_survive_save_and_reopen(self):
        import tempfile
        from pathlib import Path
        a = self.wall([0, 0], [4000, 30])
        self.add("horizontal", [{"element": a}])
        self.add("distance", [{"element": a, "part": "start"}, {"element": a, "part": "end"}], 3500)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "c.spadra"
            self.p.save(target)
            q = Project.open(target)
        self.assertEqual([(c["kind"], c["value"]) for c in MC.describe(q)], [("horizontal", None), ("distance", 3500.0)])
        q.execute("model.constraints_solve", {"fixed": []})
        s, e = wall_ends(q, a)
        self.assertAlmostEqual(math.dist(s, e), 3.5, places=7)


class Http(ServerCase):
    def test_list_and_read_only_analysis(self):
        a = self.api
        a.post("/api/project/new", {"name": "C", "unit": "mm"})
        level = a.get("/api/project/tree")["levels"][0]["id"]
        w = a.cmd("model.add_wall", {"start": [0, 0], "end": [4000, 40], "level": level, "thickness": 200, "height": 3})["result"]["id"]
        a.cmd("model.constraint_add", {"kind": "horizontal", "refs": [{"element": w}]})
        listed = a.get("/api/model-constraints")["constraints"]
        self.assertEqual([(c["kind"], c["valid"]) for c in listed], [("horizontal", True)])
        rev = a.get("/api/project/tree")["project"]["revision"]
        out = a.post("/api/model-constraints/analyze", {"fixed": []})
        self.assertEqual(out["status"], "under_constrained")
        self.assertIn(w, out["moves"])
        self.assertEqual(a.get("/api/project/tree")["project"]["revision"], rev)                    # analysis is read-only
        a.cmd("model.constraints_solve", {"fixed": []})
        self.assertEqual(a.post("/api/model-constraints/analyze", {})["moves"], {})


if __name__ == "__main__":
    unittest.main()
