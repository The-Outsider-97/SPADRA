"""P2-01 (report 10.2 / Table 17): a repeatable engineering study over the family optimizer with one case of each outcome the report asks for - feasible
(against a known analytical optimum), infeasible, unbounded / ill-posed and non-convergent - each carrying the solver's termination evidence, the budget it
was given and the statement of what is *not* proved. Nothing here changes the project."""
from __future__ import annotations

import unittest

from applications.SPADRA import optimization as OPZ
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest


class Study(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project.create("Study")
        self.fid = self.p.execute("family.define", {"name": "Door", "shape": "box", "parameters": {
            "width": {"default": 900, "min": 600, "max": 1800, "scope": "type"}, "height": {"default": 2100, "min": 1800, "max": 2600, "scope": "type"},
            "depth": {"default": 40}, "frame": {"default": 35, "min": 20, "max": 80},
            "clear_width": {"type": "length", "expression": "width - 2 * frame"},
            "leaf_area": {"type": "area", "expression": "width * height"}}})["result"]["id"]
        self.p.execute("family.type_define", {"id": self.fid, "name": "D-900", "values": {"width": 900}})
        self.revision = self.p.revision

    def solve(self, **kw) -> dict:
        problem = {"family": self.fid, "type": "D-900", "variables": {"width": {"min": 700, "max": 1400}, "frame": {"min": 25, "max": 60}},
                   "objectives": [{"expression": "leaf_area", "sense": "min"}], "constants": {"min_clear": {"type": "length", "value": 800}},
                   "constraints": ["clear_width >= min_clear"], "budget": 600, "seed": 11}
        problem.update(kw)
        result = OPZ.optimize_family(self.p, problem)
        self.assertEqual(self.p.revision, self.revision, "a study must not change the project")
        return result

    def evidence(self, r: dict) -> None:
        """What every outcome must carry, whatever it is."""
        d = r["diagnostics"]
        self.assertTrue(d["termination"], "no termination evidence")
        self.assertLessEqual(r["evaluations"], r["budget"], "the evaluation budget was exceeded")
        self.assertEqual((d["evaluations"], r["proved"]), (r["evaluations"], False))
        self.assertIn("none:", d["proof_scope"])
        self.assertTrue(d["backend"]["name"] and d["tolerances"])

    # ------------------------------------------------------------------------------------------------------------- the four outcomes
    def test_feasible_case_reaches_the_analytical_optimum_within_tolerance(self) -> None:
        """Minimise width*height with clear_width = width - 2*frame >= 800: the optimum is the smallest frame, width = 800 + 2*25 = 850 mm, frame = 25 mm."""
        r = self.solve()
        self.evidence(r)
        self.assertIn(r["status"], ("converged", "budget_exhausted"))
        point = r["proposal"]["point"]
        self.assertTrue(r["proposal"]["feasible"] and r["proposal"]["buildable"])
        optimum = 0.850 * 2.100                                                                  # m2: the analytical optimum
        self.assertGreaterEqual(r["proposal"]["objectives"][0], optimum - 1e-6)                    # nothing feasible can beat it
        self.assertLessEqual(r["proposal"]["objectives"][0] / optimum - 1, 0.02)                   # a derivative-free search gets within 2 % of it on this budget
        self.assertTrue(25 - 1e-6 <= point["frame"] <= 60 and 850 - 1e-3 <= point["width"] <= 850 * 1.02)
        self.assertLessEqual(r["diagnostics"]["max_constraint_violation"], 1e-6)                # the residual at the returned point is stated, and it is small
        self.assertGreater(r["feasible_found"], 0)
        self.assertIn("commands", r["proposal"])

    def test_infeasible_case_is_not_offered_as_a_proposal_and_says_it_is_not_a_proof(self) -> None:
        r = self.solve(constants={"min_clear": {"type": "length", "value": 5000}})             # no allowed width gives a 5 m clear opening
        self.evidence(r)
        self.assertEqual((r["status"], r["feasible_found"]), ("infeasible", 0))
        self.assertFalse(r["proposal"]["feasible"])
        self.assertNotIn("commands", r["proposal"])
        self.assertIn("not a proof", r["diagnostics"]["termination"])
        self.assertGreater(r["diagnostics"]["max_constraint_violation"], 0)

    def test_contradictory_constraints_are_infeasible_too(self) -> None:
        r = self.solve(constraints=["clear_width >= min_clear", "clear_width <= 500 mm"])
        self.evidence(r)
        self.assertEqual(r["status"], "infeasible")
        self.assertNotIn("commands", r["proposal"])

    def test_an_objective_beyond_the_engines_numeric_range_is_refused_up_front_not_called_unbounded(self) -> None:
        """The parameter engine caps magnitudes at 1e12, below the optimizer's own "absurdly large" threshold (1e15), so through a family an overflowing objective
        is an evaluation error and never reaches the optimizer's *unbounded* status (that status is exercised on the neutral optimizer in SLAI's tests)."""
        with self.assertRaises(BadRequest) as cm:
            self.solve(objectives=[{"expression": "leaf_area * 1e17", "sense": "max"}])
        self.assertIn("numeric limits", str(cm.exception))

    def test_a_region_where_the_objective_overflows_is_reported_as_failed_evaluations_and_never_proposed(self) -> None:
        """Ill-posed over part of the domain: the objective is finite in the middle and overflows at wide leaves. The search counts those as failures and the
        proposal stays inside the region where the model can be evaluated."""
        r = self.solve(objectives=[{"expression": "leaf_area * 4e11", "sense": "max"}], constraints=["clear_width >= min_clear"], budget=400)
        self.evidence(r)
        self.assertGreater(r["failed_evaluations"], 0)
        self.assertEqual(r["diagnostics"]["failed_evaluations"], r["failed_evaluations"])
        self.assertLess(abs(r["proposal"]["objectives"][0]), 1e12)
        self.assertLessEqual(r["proposal"]["point"]["width"], 1400)

    def test_ill_posed_problems_are_refused_with_the_reason_before_any_search(self) -> None:
        for kw, why in (({"variables": {"width": {"min": 1400, "max": 700}}}, "well posed"),     # an empty range
                        ({"variables": {"width": {"min": 100, "max": 200}}}, ""),                # outside the parameter's own bounds
                        ({"variables": {}}, ""),
                        ({"budget": 0}, ""),
                        ({"constraints": ["clear_width >= 5"]}, "dimension")):
            with self.subTest(kw=kw):
                with self.assertRaises(BadRequest) as cm:
                    self.solve(**kw)
                self.assertTrue(str(cm.exception))
                self.assertIn(why, str(cm.exception).lower())

    def test_non_convergent_case_ends_on_its_budget_and_says_so(self) -> None:
        r = self.solve(budget=6)
        self.evidence(r)
        self.assertEqual(r["status"], "budget_exhausted")
        self.assertEqual(r["evaluations"], 6)
        self.assertIn("budget", r["diagnostics"]["termination"])
        self.assertIn("before the local search converged", r["diagnostics"]["termination"])

    def test_a_time_limit_is_a_timeout_not_a_success(self) -> None:
        r = self.solve(time_limit_s=0.001, budget=20000)
        self.evidence(r)
        self.assertEqual(r["status"], "timeout")

    # ------------------------------------------------------------------------------------------------------------- repeatability
    def test_the_study_is_repeatable_and_the_seed_matters(self) -> None:
        self.assertEqual(self.solve()["result_sha256"], self.solve()["result_sha256"])
        other = self.solve(seed=12)["result_sha256"]
        self.assertNotEqual(self.solve()["result_sha256"], other)


if __name__ == "__main__":
    unittest.main()
