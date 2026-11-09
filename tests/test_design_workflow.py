"""The typed, revision-aware design workflow (report 7.3): contract, per-stage status/agent/revision, stale evidence, typed failures, and no committed change
without a person's approval. Nothing here claims an engineering approval: the specialist checks that do not exist are reported ``unavailable``."""
from __future__ import annotations

import copy
import unittest

from applications.SPADRA import design_workflow as DW
from applications.SPADRA import families as FM
from applications.SPADRA.assistant import Assistant
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest, Conflict

BEAM = {"kind": "beam", "type": "B1", "support": "simply_supported", "load": "uniform", "line_load_n_per_m": 5000, "youngs_modulus_gpa": 30,
        "width_mm": [50, 400], "height_mm": [100, 600], "seed": 1}


class Base(unittest.TestCase):
    def setUp(self):
        self.p = Project.create("Workflow")
        self.fid = self.p.execute("family.from_template", {"template": "beam"})["result"]["id"]
        self.p.execute("family.type_define", {"id": self.fid, "name": "B1", "values": {}})

    def contract(self, **kw):
        c = {"project_id": self.p.manifest["id"], "expected_revision": self.p.revision, "family_id": self.fid, "parameter_ids": ["width", "height"], "correlation_key": "run-1",
             "objective": copy.deepcopy(BEAM), "budgets": {"evaluations": 800}}
        c.update(kw)
        return c

    def stages(self, result):
        return {s["stage"]: s for s in result["stages"]}

    def width(self):
        """The width B1 fixes, or None while it follows the family default."""
        return {t["name"]: t["values"].get("width") for t in FM.describe(self.p, FM.family(self.p, self.fid))["types"]}["B1"]


class HappyPath(Base):
    def test_every_stage_names_its_status_agent_and_revision(self):
        r = DW.run(self.p, self.contract())
        self.assertEqual(r["schema"], DW.SCHEMA)
        self.assertEqual(r["correlation_key"], "run-1")
        for s in r["stages"]:
            self.assertIn(s["status"], DW.STATUSES + ("not_run",))
            self.assertTrue(s["agent"])
            self.assertIsInstance(s["revision"], int)
        self.assertEqual([s["stage"] for s in r["stages"]],
                         ["snapshot", "geometry", "quantities", "problem", "solve", "recheck", "simulation", "verification", "revision_check"])
        self.assertEqual(self.stages(r)["solve"]["agent"], "spadra-deterministic-lhs-pattern-search")

    def test_unavailable_specialists_make_the_result_partial_never_success(self):
        r = DW.run(self.p, self.contract())
        st = self.stages(r)
        self.assertEqual((st["simulation"]["status"], st["verification"]["status"]), ("unavailable", "unavailable"))
        self.assertEqual(st["verification"]["coverage"], "none")
        self.assertEqual(r["status"], "partial")
        self.assertEqual(r["not_run_or_unavailable"], ["simulation", "verification"])
        self.assertTrue(r["commit_allowed"])
        self.assertEqual(r["approval"], {"required": True, "state": "not_approved", "note": r["approval"]["note"]})
        self.assertFalse(r["proposal"]["approved"])
        self.assertIn("compliance or safety statement", r["approval"]["note"])

    def test_the_result_carries_the_assumptions_and_what_was_not_checked(self):
        r = DW.run(self.p, self.contract())
        self.assertEqual(r["beam"]["assumptions"]["support"], "simply_supported")
        self.assertIn("buckling", r["beam"]["checks_not_performed"])

    def test_the_candidate_is_re_evaluated_independently_and_agrees(self):
        rc = self.stages(DW.run(self.p, self.contract()))["recheck"]
        self.assertEqual(rc["status"], "success")
        self.assertTrue(rc["agrees"])
        self.assertTrue(rc["analytical_check"]["within_limit"])
        self.assertTrue(rc["analytical_check"]["agrees_with_engine"])

    def test_running_changes_nothing_and_is_repeatable(self):
        revision, width = self.p.revision, self.width()
        a, b = DW.run(self.p, self.contract()), DW.run(self.p, self.contract())
        self.assertEqual((self.p.revision, self.width()), (revision, width))
        self.assertEqual(a["contract_sha256"], b["contract_sha256"])
        self.assertEqual(self.stages(a)["solve"]["result_sha256"], self.stages(b)["solve"]["result_sha256"])

    def test_a_generic_family_workflow(self):
        door = self.p.execute("family.from_template", {"template": "door"})["result"]["id"]
        self.p.execute("family.type_define", {"id": door, "name": "D", "values": {}})
        c = self.contract(family_id=door, parameter_ids=["width"], objective={"kind": "family", "type": "D", "variables": {"width": {"min": 700, "max": 1200}},
                                                                             "objectives": [{"expression": "width", "sense": "min"}], "constraints": ["clear_width >= 800 mm"], "seed": 2})
        r = DW.run(self.p, c)
        self.assertEqual(r["status"], "partial")
        self.assertAlmostEqual(r["proposal"]["point"]["width"], 870.0, delta=1.0)             # width - 2 * 35 mm >= 800 mm


class Staleness(Base):
    def test_a_project_that_moved_on_makes_the_result_stale_and_stops_the_run(self):
        c = self.contract()
        self.p.execute("family.type_define", {"id": self.fid, "name": "Other", "values": {"width": 300}})
        r = DW.run(self.p, c)
        self.assertEqual(r["status"], "stale")
        st = self.stages(r)
        self.assertEqual(st["snapshot"]["status"], "stale")
        self.assertEqual(st["solve"]["status"], "not_run")
        self.assertFalse(r["commit_allowed"])
        self.assertIsNone(r["proposal"])
        self.assertEqual(r["revision_now"], self.p.revision)

    def test_a_proposal_from_a_workflow_cannot_be_approved_after_the_project_changes(self):
        r = DW.run(self.p, self.contract())
        assistant = Assistant()
        prop = assistant.propose_commands(self.p, source="workflow", title="t", explanation="e",
                                          steps=[{"op": c["command"], "params": c["params"]} for c in r["proposal"]["commands"]])["proposal"]
        self.p.execute("family.type_define", {"id": self.fid, "name": "Other", "values": {"width": 300}})
        with self.assertRaises(Conflict):
            assistant.approve(self.p, prop["id"])
        self.assertIsNone(self.width())                                                        # B1 still follows the default: nothing was applied

    def test_nothing_is_committed_without_approval_and_approval_applies_it_once(self):
        r = DW.run(self.p, self.contract())
        assistant = Assistant()
        prop = assistant.propose_commands(self.p, source="workflow", title="t", explanation="e",
                                          steps=[{"op": c["command"], "params": c["params"]} for c in r["proposal"]["commands"]])["proposal"]
        self.assertIsNone(self.width())
        assistant.approve(self.p, prop["id"], expected_revision=self.p.revision)
        self.assertAlmostEqual(self.width(), 50.0)
        self.p.undo()
        self.assertIsNone(self.width())


class TypedFailures(Base):
    def test_a_missing_assumption_is_unavailable_and_names_it(self):
        obj = copy.deepcopy(BEAM)
        del obj["youngs_modulus_gpa"]
        r = DW.run(self.p, self.contract(objective=obj))
        self.assertEqual(r["status"], "unavailable")
        g = self.stages(r)["geometry"]
        self.assertEqual((g["status"], g["missing"]), ("unavailable", ["youngs_modulus_gpa"]))
        self.assertIsNone(r["proposal"])
        self.assertFalse(r["commit_allowed"])
        self.assertEqual(self.stages(r)["solve"]["status"], "not_run")

    def test_infeasible_bounds_are_infeasible_with_diagnostics_and_no_proposal(self):
        obj = dict(copy.deepcopy(BEAM), width_mm=[50, 60], height_mm=[100, 120])
        r = DW.run(self.p, self.contract(objective=obj))
        self.assertEqual(r["status"], "infeasible")
        s = self.stages(r)["solve"]
        self.assertEqual(s["status"], "infeasible")
        self.assertGreater(s["diagnostics"]["max_constraint_violation"], 0)
        self.assertIsNone(r["proposal"])
        self.assertFalse(r["commit_allowed"])

    def test_a_time_limit_is_a_timeout(self):
        r = DW.run(self.p, self.contract(budgets={"evaluations": 20000, "time_limit_s": 0.001}))
        self.assertEqual(r["status"], "timeout")
        self.assertFalse(r["commit_allowed"])

    def test_a_problem_that_cannot_be_evaluated_fails_with_its_reason(self):
        c = self.contract(objective={"kind": "family", "type": "B1", "variables": {"width": {"min": 100, "max": 300}, "height": {"min": 100, "max": 300}},
                                     "objectives": [{"expression": "width + volume"}]})
        r = DW.run(self.p, c)
        self.assertEqual(r["status"], "failed")
        q = self.stages(r)["quantities"]
        self.assertEqual(q["status"], "failed")
        self.assertIn("dimensions", q["reason"])
        self.assertIsNone(r["proposal"])

    def test_parameter_ids_must_match_what_the_objective_varies(self):
        r = DW.run(self.p, self.contract(parameter_ids=["width"]))
        self.assertEqual(r["status"], "failed")
        self.assertIn("parameter_ids", self.stages(r)["geometry"]["reason"])

    def test_required_verification_that_does_not_exist_is_unavailable_and_blocks_the_proposal(self):
        r = DW.run(self.p, self.contract(verification="required"))
        self.assertEqual(r["status"], "unavailable")
        self.assertFalse(r["commit_allowed"])
        self.assertIsNone(r["proposal"])

    def test_a_non_beam_family_is_unsupported_for_the_beam_objective(self):
        other = self.p.execute("family.define", {"name": "Plain", "shape": "box", "parameters": {"width": 100, "depth": 100, "height": 100}})["result"]["id"]
        obj = dict(copy.deepcopy(BEAM), type=None)
        r = DW.run(self.p, self.contract(family_id=other, objective=obj))
        self.assertEqual(r["status"], "unavailable")
        self.assertEqual(self.stages(r)["geometry"]["refusal"], "unsupported")


class ContractValidation(Base):
    def test_malformed_contracts_are_bad_requests(self):
        for change in ({"project_id": "other"}, {"expected_revision": "x"}, {"expected_revision": True}, {"parameter_ids": []}, {"parameter_ids": ["a", "a"]},
                       {"objective": {"kind": "nonsense"}}, {"budgets": {"seconds": 1}}, {"output_schema": "other/9"}, {"verification": "maybe"}, {"unknown": 1}):
            with self.assertRaises(BadRequest, msg=str(change)):
                DW.run(self.p, self.contract(**change))
        with self.assertRaises(BadRequest):
            DW.run(self.p, "not an object")

    def test_required_fields_are_named(self):
        c = self.contract()
        del c["correlation_key"]
        with self.assertRaises(BadRequest) as caught:
            DW.run(self.p, c)
        self.assertIn("correlation_key", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
