"""The report's beam example (sections 7.4-7.6) as tests: the stated deflection, the two-variable search against an analytic optimum, Pareto filtering,
solver diagnostics, and typed refusals that never substitute an assumption."""
from __future__ import annotations

import math
import unittest

from applications.SPADRA import optimization as OPZ
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest

L, W, E_GPA = 4.0, 5000.0, 30.0


def deflection(b_m, h_m, span=L, load=W, e_pa=E_GPA * 1e9):
    return 5 * load * span ** 4 / (384 * e_pa * (b_m * h_m ** 3 / 12))


class Base(unittest.TestCase):
    def setUp(self):
        self.p = Project.create("Beam")
        self.fid = self.p.execute("family.from_template", {"template": "beam"})["result"]["id"]
        self.p.execute("family.type_define", {"id": self.fid, "name": "B1", "values": {}})

    def request(self, **kw):
        base = dict(family=self.fid, type="B1", support="simply_supported", load="uniform", line_load_n_per_m=W, youngs_modulus_gpa=E_GPA, limit_ratio=360,
                    width_mm=[50, 400], height_mm=[100, 600], budget=800, seed=1)
        return {**base, **kw}


class TheReportsNumbers(Base):
    def test_the_starting_section(self):
        self.assertAlmostEqual(deflection(0.2, 0.4) * 1000, 0.5208, places=3)               # delta = 0.521 mm
        self.assertAlmostEqual(deflection(0.2, 0.4) / (L / 360), 0.0469, places=3)          # utilisation about 0.047
        self.assertAlmostEqual(0.2 * 0.4 * L, 0.320)                                         # V = b h L

    def test_the_engine_agrees_with_the_closed_form_at_the_starting_section(self):
        r = OPZ.optimize_beam_family(self.p, self.request(width_mm=[200, 200], height_mm=[400, 400], budget=5))
        check = r["proposal"]["analytical_check"]
        self.assertAlmostEqual(check["deflection_m"] * 1000, 0.5208, places=3)
        self.assertTrue(check["agrees_with_engine"])
        self.assertAlmostEqual(check["utilisation"], 0.0469, places=3)


class TwoVariableSearch(Base):
    def test_finds_the_analytic_optimum_when_the_limit_and_a_bound_both_bind(self):
        r = OPZ.optimize_beam_family(self.p, self.request())
        self.assertIn(r["status"], ("converged", "budget_exhausted"))
        i_req = 5 * W * L ** 4 / (384 * E_GPA * 1e9 * (L / 360))
        h_star = (12 * i_req / 0.05) ** (1 / 3) * 1000                                       # b is held at its 50 mm bound: b h^3 = 12 I
        point = r["proposal"]["point"]
        self.assertAlmostEqual(point["width"], 50.0, places=6)
        self.assertAlmostEqual(point["height"], h_star, delta=0.01 * h_star)                  # within 1 % of the analytic optimum
        self.assertTrue(r["proposal"]["feasible"])
        self.assertTrue(r["proposal"]["analytical_check"]["within_limit"])
        self.assertTrue(r["proposal"]["analytical_check"]["agrees_with_engine"])
        self.assertEqual(r["proposal"]["active_bounds"], ["width"])
        self.assertFalse(r["proved"])
        self.assertFalse(r["optimum_asserted"])

    def test_when_the_limit_is_slack_the_bounds_decide_and_the_result_says_so(self):
        r = OPZ.optimize_beam_family(self.p, self.request(width_mm=[100, 400], height_mm=[200, 600]))     # report: deflection alone is not enough
        point = r["proposal"]["point"]
        self.assertEqual((point["width"], point["height"]), (100.0, 200.0))
        self.assertEqual(sorted(r["proposal"]["active_bounds"]), ["height", "width"])
        self.assertLess(r["proposal"]["analytical_check"]["utilisation"], 1.0)

    def test_the_result_proposes_a_type_update_and_changes_nothing_itself(self):
        revision = self.p.revision
        r = OPZ.optimize_beam_family(self.p, self.request())
        self.assertEqual(self.p.revision, revision)
        cmd = r["proposal"]["commands"][0]
        self.assertEqual(cmd["command"], "family.type_update")
        self.p.execute(cmd["command"], cmd["params"])                                          # applying is a separate, ordinary, undoable command
        inst = self.p.execute("family.place", {"family": self.fid, "level": self.p.manifest["levels"][0]["id"], "point": [0, 0], "type": "B1"})["result"]["id"]
        from applications.SPADRA import families as FM
        v = FM.values(self.p, inst)
        self.assertLessEqual(deflection(v["width"], v["height"]), L / 360 * (1 + 1e-9))

    def test_the_search_is_deterministic_for_a_seed(self):
        a, b = OPZ.optimize_beam_family(self.p, self.request()), OPZ.optimize_beam_family(self.p, self.request())
        self.assertEqual(a["result_sha256"], b["result_sha256"])
        self.assertEqual(a["proposal"]["point"], b["proposal"]["point"])


class TypedRefusals(Base):
    def test_a_missing_assumption_is_named_and_nothing_is_substituted(self):
        for key in ("line_load_n_per_m", "youngs_modulus_gpa", "support", "load"):
            r = OPZ.optimize_beam_family(self.p, self.request(**{key: None}))
            self.assertEqual(r["status"], "unavailable", key)
            self.assertEqual(r["missing"], [key])
            self.assertIsNone(r["proposal"])
            self.assertEqual(r["assumptions"][key], None)                                      # still not filled in

    def test_missing_bounds_are_refused_not_guessed(self):
        r = OPZ.optimize_beam_family(self.p, self.request(width_mm=None))
        self.assertEqual((r["status"], r["missing"]), ("unavailable", ["width_mm"]))

    def test_an_unsupported_support_case_is_unsupported_not_inferred(self):
        for kw in ({"support": "cantilever"}, {"load": "point"}):
            r = OPZ.optimize_beam_family(self.p, self.request(**kw))
            self.assertEqual(r["status"], "unsupported")
            self.assertIsNone(r["proposal"])

    def test_a_family_that_is_not_a_beam_is_unsupported(self):
        other = self.p.execute("family.define", {"name": "Plain", "shape": "box", "parameters": {"width": 100, "depth": 100, "height": 100}})["result"]["id"]
        r = OPZ.optimize_beam_family(self.p, self.request(family=other, type=None))
        self.assertEqual(r["status"], "unsupported")
        self.assertIn("second_moment", r["missing_parameters"])

    def test_impossible_bounds_are_infeasible_with_the_residual_not_a_fabricated_success(self):
        r = OPZ.optimize_beam_family(self.p, self.request(width_mm=[50, 60], height_mm=[100, 120]))
        self.assertEqual(r["status"], "infeasible")
        self.assertFalse(r["proposal"]["feasible"])
        self.assertNotIn("commands", r["proposal"])
        self.assertGreater(r["diagnostics"]["max_constraint_violation"], 0)

    def test_every_result_carries_the_assumption_table_and_what_was_not_checked(self):
        r = OPZ.optimize_beam_family(self.p, self.request())
        a = r["assumptions"]
        self.assertEqual((a["support"], a["load"], a["line_load_n_per_m"], a["youngs_modulus_gpa"]), ("simply_supported", "uniform", W, E_GPA))
        self.assertIn("shear", a["not_checked"])
        self.assertIn("buckling", r["checks_not_performed"])
        self.assertIn("not evaluated", a["compliance"])

    def test_bad_numbers_are_bad_requests(self):
        with self.assertRaises(BadRequest):
            OPZ.optimize_beam_family(self.p, self.request(youngs_modulus_gpa=-1))
        with self.assertRaises(BadRequest):
            OPZ.optimize_beam_family(self.p, self.request(limit_ratio=1))


class ParetoAndDiagnostics(Base):
    def test_the_front_is_feasible_and_no_member_dominates_another(self):
        r = OPZ.optimize_beam_family(self.p, self.request(pareto=True, budget=1500))
        front = r["pareto"]
        self.assertGreater(len(front), 2)
        limit = L / 360
        for entry in front:
            volume, delta = entry["objectives"]
            self.assertLessEqual(delta, limit * (1 + 1e-9))                                   # every member is feasible
            self.assertAlmostEqual(delta, deflection(entry["point"]["width"] / 1000, entry["point"]["height"] / 1000), places=9)
        for i, a in enumerate(front):
            for j, b in enumerate(front):
                if i != j:
                    self.assertFalse(all(x <= y for x, y in zip(a["objectives"], b["objectives"])) and any(x < y for x, y in zip(a["objectives"], b["objectives"])), (a, b))
        ranges = r["pareto_normalization"]["objective_ranges"]
        self.assertEqual(len(ranges), 2)
        self.assertLess(ranges[0]["min"], ranges[0]["max"])

    def test_the_front_trades_volume_against_deflection(self):
        front = OPZ.optimize_beam_family(self.p, self.request(pareto=True, budget=1500))["pareto"]
        by_volume = sorted(front, key=lambda e: e["objectives"][0])
        self.assertGreater(by_volume[-1]["objectives"][0], by_volume[0]["objectives"][0])
        self.assertLess(by_volume[-1]["objectives"][1], by_volume[0]["objectives"][1])      # more material, less deflection

    def test_diagnostics_name_what_a_reviewer_needs(self):
        r = OPZ.optimize_beam_family(self.p, self.request())
        d = r["diagnostics"]
        self.assertEqual(d["backend"]["name"], "spadra-deterministic-lhs-pattern-search")
        self.assertTrue(d["backend"]["version"])
        self.assertTrue(d["termination"])
        self.assertEqual(d["evaluations"], r["evaluations"])
        self.assertGreaterEqual(d["runtime_ms"], 0)
        self.assertIn("feasibility", d["tolerances"])
        self.assertLessEqual(d["max_constraint_violation"], 1e-9)
        self.assertEqual(len(d["constraint_residuals"]), 1)
        self.assertEqual(d["constraint_residuals"][0]["dimension"], "length")
        self.assertEqual(d["objectives"][0]["unit"], "volume")
        self.assertEqual(d["objectives"][0]["sense"], "min")
        self.assertTrue(d["proof_scope"].startswith("none"))

    def test_a_time_limit_ends_the_search_as_a_timeout_not_a_success(self):
        r = OPZ.optimize_beam_family(self.p, self.request(budget=20000, time_limit_s=0.001))
        self.assertEqual(r["status"], "timeout")
        self.assertTrue(r["diagnostics"]["time_limit_s"])
        self.assertIn("wall-clock", r["diagnostics"]["termination"])
        if r["proposal"]:                                                                       # whatever was found is judged on its own residual, never assumed good
            self.assertEqual(r["proposal"]["feasible"], r["diagnostics"]["max_constraint_violation"] <= 1e-9)


class ConstantsAsExpressions(Base):
    def test_a_line_load_expression_carries_its_dimension(self):
        prob = OPZ.family_problem(self.p, {"family": self.fid, "type": "B1", "variables": {"width": {"min": 100, "max": 300}},
                                           "constants": {"q": {"expression": "5 kN / 1 m"}}, "objectives": [{"expression": "volume"}]})
        self.assertEqual(prob["constants"]["q"]["si_value"], 5000.0)
        self.assertEqual(prob["constants"]["q"]["dimension"].replace(" ", ""), prob["constants"]["q"]["dimension"].replace(" ", ""))

    def test_a_constraint_with_inconsistent_units_is_refused_before_any_search(self):
        with self.assertRaises(BadRequest) as caught:
            OPZ.family_problem(self.p, {"family": self.fid, "type": "B1", "variables": {"width": {"min": 100, "max": 300}}, "constants": {"q": {"expression": "5 kN / 1 m"}},
                                        "objectives": [{"expression": "volume"}], "constraints": ["q <= depth"]})
        self.assertIn("dimension", str(caught.exception))

    def test_a_missing_constant_is_an_error_not_a_default(self):
        with self.assertRaises(BadRequest) as caught:
            OPZ.family_problem(self.p, {"family": self.fid, "type": "B1", "variables": {"width": {"min": 100, "max": 300}},
                                        "objectives": [{"expression": "volume * modulus_E"}]})
        self.assertIn("modulus_E", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
