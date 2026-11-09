"""Geometric constraints in SPADRA drawings: analysis, preview-free solve as one undoable command, and refusal of unsolvable systems."""
from __future__ import annotations

import unittest

from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest


class Constraints(unittest.TestCase):
    def setUp(self):
        self.p = Project.create("C")
        self.view = next(iter(self.p.drawings))                                   # the project's first drawing view
        self.doc = self.p.drawing(self.view)

    def x(self, op, **kw):
        return self.p.execute(op, {"drawing": self.view, **kw})["result"]

    def test_analyze_then_solve_then_undo(self):
        a = self.x("draw.line", start=[0, 0], end=[4000, 300])["id"]
        b = self.x("draw.line", start=[0, 1000], end=[4000, 1500])["id"]
        cons = [{"kind": "horizontal", "entities": [a]}, {"kind": "parallel", "entities": [a, b]}]
        self.x("draw.constraints_set", constraints=cons)
        rep = self.p.drawing(self.view).analyze_constraints()
        self.assertEqual((rep["unresolved"], rep["status"]), ([], "under_constrained"))
        self.assertGreater(rep["dof"], 0)
        before = self.p.drawing(self.view).get(a)["geometry"]
        r = self.x("draw.constraints_solve")
        self.assertTrue(r["converged"] and a in r["moved"])
        g = self.p.drawing(self.view).get(a)["geometry"]
        self.assertAlmostEqual(g["start"][1], g["end"][1], places=7)
        self.p.undo()
        self.assertEqual(self.p.drawing(self.view).get(a)["geometry"], before)

    def test_an_unsolvable_system_is_refused_and_nothing_moves(self):
        a = self.x("draw.line", start=[0, 0], end=[4000, 300])["id"]
        cons = [{"kind": "horizontal", "entities": [a]}, {"kind": "vertical", "entities": [a]}]
        rev = self.p.revision
        before = self.p.drawing(self.view).get(a)["geometry"]
        with self.assertRaises(Exception):
            self.x("draw.constraints_solve", constraints=cons)
        self.assertEqual((self.p.revision, self.p.drawing(self.view).get(a)["geometry"]), (rev, before))
        rep = self.p.drawing(self.view).analyze_constraints(cons)
        self.assertEqual(rep["status"], "conflicting")
        self.assertEqual(rep["degenerate"], [a])                                  # it can only be "solved" by collapsing the line to a point


if __name__ == "__main__":
    unittest.main()
