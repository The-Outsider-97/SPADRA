"""Parametric optimization of a family: a proposal that respects formulas, units and bounds, changes nothing by itself, and applies through commands."""
from __future__ import annotations

import unittest

from applications.SPADRA import families as FM
from applications.SPADRA import optimization as OPZ
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest


class Door(unittest.TestCase):
    def setUp(self):
        self.p = Project.create("Opt")
        self.fid = self.p.execute("family.define", {"name": "Door", "shape": "box", "parameters": {
            "width": {"default": 900, "min": 600, "max": 1800, "scope": "type"}, "height": {"default": 2100, "min": 1800, "max": 2600, "scope": "type"},
            "depth": {"default": 40}, "frame": {"default": 35, "min": 20, "max": 80},
            "clear_width": {"type": "length", "expression": "width - 2 * frame"},
            "leaf_area": {"type": "area", "expression": "width * height"}}})["result"]["id"]
        self.p.execute("family.type_define", {"id": self.fid, "name": "D-900", "values": {"width": 900}})

    def run_(self, **kw):
        base = {"family": self.fid, "type": "D-900", "variables": {"width": {"min": 700, "max": 1400}, "frame": {"min": 25, "max": 60}},
                "objectives": [{"expression": "leaf_area", "sense": "min"}], "constants": {"min_clear": {"type": "length", "value": 800}},
                "constraints": ["clear_width >= min_clear"], "budget": 500, "seed": 4}
        base.update(kw)
        return OPZ.optimize_family(self.p, base)

    def test_the_smallest_leaf_that_keeps_the_clear_width(self):
        before = self.p.revision
        r = self.run_()
        self.assertIn(r["status"], ("converged", "budget_exhausted"))
        pt = r["proposal"]["point"]
        self.assertTrue(r["proposal"]["feasible"] and r["proposal"]["buildable"])
        self.assertGreaterEqual(pt["width"] - 2 * pt["frame"], 800 - 1e-3)                  # the constraint holds, in mm, in the person's units
        self.assertLess(pt["width"], 870)                                                    # optimum is width = 800 + 2 * 25 = 850 (smallest frame)
        self.assertEqual(self.p.revision, before)                                            # a proposal changes nothing
        self.assertFalse(r["proved"])

    def test_the_proposal_applies_through_the_ordinary_command_and_is_undoable(self):
        r = self.run_()
        cmd = r["proposal"]["commands"][0]
        self.p.execute(cmd["command"], cmd["params"])
        types = {t["name"]: t["values"] for t in FM.describe(self.p, FM.family(self.p, self.fid))["types"]}
        self.assertAlmostEqual(types["D-900"]["width"], r["proposal"]["point"]["width"], places=6)
        self.p.undo()
        types = {t["name"]: t["values"] for t in FM.describe(self.p, FM.family(self.p, self.fid))["types"]}
        self.assertEqual(types["D-900"]["width"], 900)

    def test_deterministic(self):
        self.assertEqual(self.run_()["result_sha256"], self.run_()["result_sha256"])

    def test_an_impossible_requirement_is_infeasible_not_a_proposal(self):
        r = self.run_(constants={"min_clear": {"type": "length", "value": 5000}})            # wider than any allowed width
        self.assertEqual(r["status"], "infeasible")
        self.assertFalse(r["proposal"]["feasible"])
        self.assertNotIn("commands", r["proposal"])

    def test_bad_problems_are_refused_with_a_reason(self):
        for kw, why in (({"variables": {"nope": {"min": 1, "max": 2}}}, "no parameter"),
                        ({"variables": {"clear_width": {"min": 1, "max": 2}}}, "formula"),
                        ({"constraints": ["clear_width"]}, "compare"),
                        ({"constraints": ["clear_width >= 5"]}, "dimension"),                  # length compared with a bare number
                        ({"objectives": [{"expression": "nonsense + 1"}]}, "cannot be evaluated"),
                        ({"objectives": []}, "objectives")):
            with self.assertRaises(BadRequest, msg=str(kw)) as cm:
                self.run_(**kw)
            self.assertIn(why, str(cm.exception).lower() if why.islower() else str(cm.exception), kw)

    def test_two_objectives_give_a_front(self):
        r = self.run_(objectives=[{"expression": "leaf_area", "sense": "min"}, {"expression": "clear_width", "sense": "max"}], weights=None)
        self.assertGreater(len(r["pareto"]), 3)


if __name__ == "__main__":
    unittest.main()


class Alternatives(Door):
    """Several different designs for the same family, offered as new named types."""

    def alt(self, **kw):
        base = {"family": self.fid, "type": "D-900", "variables": {"width": {"min": 700, "max": 1400}, "frame": {"min": 25, "max": 60}},
                "objectives": [{"expression": "leaf_area", "sense": "min"}], "constants": {"min_clear": {"type": "length", "value": 800}},
                "constraints": ["clear_width >= min_clear"], "count": 4, "candidates": 200, "seed": 5}
        base.update(kw)
        return OPZ.alternatives_family(self.p, base)

    def test_alternatives_are_feasible_buildable_different_and_change_nothing(self):
        before = self.p.revision
        r = self.alt()
        self.assertEqual(r["status"], "ok")
        self.assertEqual(len(r["alternatives"]), 4)
        for a in r["alternatives"]:
            self.assertTrue(a["buildable"])
            self.assertGreaterEqual(a["point"]["width"] - 2 * a["point"]["frame"], 800 - 1e-3)
        self.assertEqual(len({(a["point"]["width"], a["point"]["frame"]) for a in r["alternatives"]}), 4)
        self.assertEqual(self.p.revision, before)
        self.assertFalse(r["proved"])

    def test_deterministic_and_named_without_clashes(self):
        self.p.execute("family.type_define", {"id": self.fid, "name": "Option 1", "values": {}})
        a, b = self.alt(), self.alt()
        self.assertEqual(a["result_sha256"], b["result_sha256"])
        self.assertEqual([x["type_name"] for x in a["alternatives"]], ["Option 2", "Option 3", "Option 4", "Option 5"])

    def test_each_option_applies_as_a_new_type_and_undoes(self):
        r = self.alt()
        before = {t["name"] for t in FM.describe(self.p, FM.family(self.p, self.fid))["types"]}
        for a in r["alternatives"]:
            self.p.execute(a["command"]["command"], a["command"]["params"])
        after = {t["name"]: t["values"] for t in FM.describe(self.p, FM.family(self.p, self.fid))["types"]}
        self.assertEqual(set(after) - before, {a["type_name"] for a in r["alternatives"]})
        first = r["alternatives"][0]
        self.assertAlmostEqual(after[first["type_name"]]["width"], first["point"]["width"], places=6)
        for _ in r["alternatives"]:
            self.p.undo()
        self.assertEqual({t["name"] for t in FM.describe(self.p, FM.family(self.p, self.fid))["types"]}, before)

    def test_an_impossible_requirement_gives_no_alternatives(self):
        r = self.alt(constants={"min_clear": {"type": "length", "value": 5000}})
        self.assertEqual((r["status"], r["alternatives"]), ("infeasible", []))
