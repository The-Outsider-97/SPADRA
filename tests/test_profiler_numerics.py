"""Profiler: the numbers the specification fixes, checked against real geometry (positions of points, areas), not against flags."""
from __future__ import annotations

import unittest

from . import profiler_env  # noqa: F401  (must come before anything saves an .sdrw)
from ..profiler.errors import ProfilerConflict, ProfilerError
from ..profiler.model import Sketch
from ..profiler.session import Session
from ..profiler import templates


def new_session() -> Session:
    return Session(Sketch("t", unit="mm"))


def x_of(S: Session, vid: str) -> float:
    return S.sk.pos(vid)[0]


class SingleDimensionAnchors(unittest.TestCase):
    """A 120 mm line is edited to 130 mm. Which end stays still depends on the anchor."""

    def line(self):
        S = new_session()
        e = S.run("line", {"a": [0, 0], "b": [120, 0]})["result"]["edge"]
        a, b = S.sk.edge_ends(e)
        S.run("constrain", {"kind": "horizontal", "refs": [e]})
        d = S.run("dimension", {"type": "length", "refs": [e], "mode": "reference"})["result"]["dimension"]
        return S, e, a, b, d

    def edit(self, anchor):
        S, e, a, b, d = self.line()
        S.run("dimension_value", {"dimension": d, "value": 130, "anchor": anchor})
        return x_of(S, a), x_of(S, b)

    def test_start_anchor_keeps_start_and_moves_end(self):
        a, b = self.edit("start")
        self.assertAlmostEqual(a, 0, 6)
        self.assertAlmostEqual(b, 130, 6)

    def test_end_anchor_keeps_end_and_moves_start(self):
        a, b = self.edit("end")
        self.assertAlmostEqual(b, 120, 6)
        self.assertAlmostEqual(a, -10, 6)

    def test_centre_anchor_keeps_the_midpoint(self):
        a, b = self.edit("center")
        self.assertAlmostEqual((a + b) / 2, 60, 6)
        self.assertAlmostEqual(b - a, 130, 6)

    def test_no_anchor_still_gives_exactly_130(self):
        a, b = self.edit("none")
        self.assertAlmostEqual(b - a, 130, 6)

    def test_value_must_be_positive_and_document_unchanged(self):
        S, e, a, b, d = self.line()
        before = S.sk.digest()
        with self.assertRaises(ProfilerError):
            S.run("dimension_value", {"dimension": d, "value": -5, "anchor": "start"})
        self.assertEqual(S.sk.digest(), before)


class ChainOf80And40(unittest.TestCase):
    """80 + 40 = 120. Changing the total to 130 follows the locks."""

    def chain(self):
        S = new_session()
        edges = S.run("polyline", {"points": [[0, 0], [80, 0], [120, 0]]})["result"]["edges"]
        for e in edges:
            S.run("constrain", {"kind": "horizontal", "refs": [e]})
        verts = [S.sk.edge_ends(edges[0])[0], S.sk.edge_ends(edges[0])[1], S.sk.edge_ends(edges[1])[1]]
        out = S.run("chain", {"vertices": verts, "with_total": True})["result"]
        S.run("chain_end_locks", {"chain": out["chain"], "vertices": [verts[0]]})            # the start of the chain stays where it is
        return S, out["chain"], verts

    def widths(self, S, verts):
        xs = [x_of(S, v) for v in verts]
        return [round(xs[1] - xs[0], 6), round(xs[2] - xs[1], 6)]

    def test_starts_as_80_and_40(self):
        S, cid, verts = self.chain()
        self.assertEqual(self.widths(S, verts), [80, 40])

    def test_40_locked_gives_90_and_40(self):
        S, cid, verts = self.chain()
        S.run("chain_lock_segment", {"chain": cid, "index": 1, "locked": True})
        S.run("chain_total", {"chain": cid, "total": 130, "policy": "min_change"})
        self.assertEqual(self.widths(S, verts), [90, 40])

    def test_80_locked_gives_80_and_50(self):
        S, cid, verts = self.chain()
        S.run("chain_lock_segment", {"chain": cid, "index": 0, "locked": True})
        S.run("chain_total", {"chain": cid, "total": 130, "policy": "min_change"})
        self.assertEqual(self.widths(S, verts), [80, 50])

    def test_equal_growth_gives_85_and_45(self):
        S, cid, verts = self.chain()
        S.run("chain_total", {"chain": cid, "total": 130, "policy": "equal_growth"})
        self.assertEqual(self.widths(S, verts), [85, 45])

    def test_proportional_keeps_the_ratio(self):
        S, cid, verts = self.chain()
        S.run("chain_total", {"chain": cid, "total": 130, "policy": "proportional"})
        w = self.widths(S, verts)
        self.assertAlmostEqual(w[0], 86.666667, 5)
        self.assertAlmostEqual(w[1], 43.333333, 5)

    def test_persistent_eq_gives_65_and_65_and_stays_equal(self):
        S, cid, verts = self.chain()
        S.run("chain_eq", {"chain": cid, "on": True})
        self.assertEqual(self.widths(S, verts), [60, 60])
        S.run("chain_total", {"chain": cid, "total": 130})
        self.assertEqual(self.widths(S, verts), [65, 65])
        S.run("chain_total", {"chain": cid, "total": 200})                                  # EQ is still in force later
        self.assertEqual(self.widths(S, verts), [100, 100])

    def test_both_segments_locked_is_a_reported_conflict_and_nothing_moves(self):
        S, cid, verts = self.chain()
        S.run("chain_lock_segment", {"chain": cid, "index": 0, "locked": True})
        S.run("chain_lock_segment", {"chain": cid, "index": 1, "locked": True})
        before = S.sk.digest()
        with self.assertRaises((ProfilerConflict, ProfilerError)) as ctx:
            S.run("chain_total", {"chain": cid, "total": 130})
        self.assertTrue(ctx.exception.details)                                              # it says what conflicts
        self.assertEqual(S.sk.digest(), before)
        self.assertEqual(self.widths(S, verts), [80, 40])


class ParametersAndFormulas(unittest.TestCase):
    def rectangle(self):
        S = templates.build("rectangle", "r")
        S.run("param_add", {"name": "Return", "type": "length", "value": 40})
        S.run("param_add", {"name": "RemainingWidth", "type": "length", "expression": "Width - Return"})
        return S

    def val(self, S, name):
        return S.sk.d["params"][name]["value"]

    def test_defaults(self):
        S = self.rectangle()
        self.assertEqual((self.val(S, "Width"), self.val(S, "Height"), self.val(S, "Return"), self.val(S, "RemainingWidth")), (120, 100, 40, 80))

    def test_fixed_return_40_at_width_150_gives_remaining_110(self):
        S = self.rectangle()
        S.run("param_set", {"name": "Width", "value": 150})
        self.assertEqual(self.val(S, "Return"), 40)
        self.assertEqual(self.val(S, "RemainingWidth"), 110)

    def test_return_as_width_over_3(self):
        S = self.rectangle()
        S.run("param_set", {"name": "Return", "expression": "Width / 3"})
        self.assertAlmostEqual(self.val(S, "Return"), 40, 9)
        S.run("param_set", {"name": "Width", "value": 150})
        self.assertAlmostEqual(self.val(S, "Return"), 50, 9)
        self.assertAlmostEqual(self.val(S, "RemainingWidth"), 100, 9)

    def test_width_change_moves_real_geometry(self):
        S = self.rectangle()
        S.run("param_set", {"name": "Width", "value": 150})
        b = S.sk.bounds()
        self.assertAlmostEqual(b[2] - b[0], 150, 6)
        self.assertAlmostEqual(b[3] - b[1], 100, 6)

    def test_circular_dependency_is_refused_and_nothing_changes(self):
        S = self.rectangle()
        before = S.sk.digest()
        with self.assertRaises(ProfilerError):
            S.run("param_set", {"name": "Return", "expression": "RemainingWidth / 2"})
        self.assertEqual(S.sk.digest(), before)

    def test_division_by_zero_is_refused(self):
        S = self.rectangle()
        before = S.sk.digest()
        with self.assertRaises(ProfilerError):
            S.run("param_set", {"name": "Return", "expression": "Width / (Height - 100)"})
        self.assertEqual(S.sk.digest(), before)

    def test_unit_mismatch_is_refused(self):
        S = self.rectangle()
        S.run("param_add", {"name": "Turn", "type": "angle", "value": 30})
        with self.assertRaises(ProfilerError):
            S.run("param_set", {"name": "Return", "expression": "Width + Turn"})

    def test_no_arbitrary_code(self):
        S = self.rectangle()
        for bad in ("__import__('os').getcwd()", "open('x')", "Width.__class__", "[1 for _ in range(3)]", "exec('1')"):
            with self.assertRaises(ProfilerError, msg=bad):
                S.run("param_set", {"name": "Return", "expression": bad})

    def test_undo_restores_parameters_and_geometry(self):
        S = self.rectangle()
        before = S.sk.digest()
        S.run("param_set", {"name": "Width", "value": 150})
        S.undo_last()
        self.assertEqual(S.sk.digest(), before)


if __name__ == "__main__":
    unittest.main()
