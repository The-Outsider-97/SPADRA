"""The SLAI Assistant (master prompt 15): requests become typed proposals or read-only answers, a proposal is validated on a scratch copy, nothing
changes without approval, a stale proposal is refused, and a failure leaves the project exactly as it was. The interpreter is a pattern interpreter and says so."""
from __future__ import annotations

import unittest

from applications.SPADRA.assistant import ASSISTANT, CAPABILITIES, INTERPRETER, Assistant
from applications.SPADRA.project import Project
from applications.tools.errors import Conflict, NotFound


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.a = Assistant()
        self.p = Project.create("Assistant")
        self.level = self.p.manifest["levels"][0]["id"]
        self.wall = self.p.execute("model.add_wall", {"start": [0, 0], "end": [5000, 0], "level": self.level, "thickness": 200, "height": 3})["result"]["id"]

    def ask(self, text, *sel):
        return self.a.ask(self.p, text, {"selection": list(sel)})

    def rows(self, oid):
        props = self.p.properties({"model": oid})
        return {r["key"]: r["value"] for r in [*props["top"], *[x for g in props["groups"] for x in g["rows"]]] if r.get("key")}


class Honesty(Base):
    def test_it_says_it_is_a_pattern_interpreter_not_a_language_model(self):
        self.assertFalse(INTERPRETER["language_model"])
        m = self.ask("tell me a joke about concrete")
        self.assertEqual(m["kind"], "unsupported")
        self.assertIn("not a language model", m["text"])
        self.assertEqual(self.p.revision, 1)                       # nothing happened
        self.assertEqual(self.a.history(self.p)["capabilities"], CAPABILITIES)

    def test_every_capability_has_an_example_that_routes_to_it(self):
        self.assertGreaterEqual(len(CAPABILITIES), 10)
        self.assertTrue(all(c["example"] and c["needs"] for c in CAPABILITIES))

    def test_context_is_only_what_is_needed(self):
        m = self.a.ask(self.p, "Explain the selected element's parameters", {"selection": [self.wall, "gone"], "level": self.level, "secret": "x"})
        self.assertEqual(set(m["context"]), {"project", "unit", "revision", "selection", "view", "level"})
        self.assertEqual([s["id"] for s in m["context"]["selection"]], [self.wall])     # an id that is not in the model is dropped


class ReadOnly(Base):
    def test_explain_lists_the_real_parameters_and_marks_editable_ones(self):
        m = self.ask("Explain the selected element's parameters.", self.wall)
        self.assertEqual(m["kind"], "explanation")
        rows = {r["label"]: r for r in m["data"]["elements"][0]["rows"]}
        self.assertEqual(rows["Height"]["value"], 3.0)
        self.assertTrue(rows["Height"]["editable"])
        self.assertFalse(rows["Volume"]["editable"])

    def test_measure_a_room_is_computed_from_the_model(self):
        room = self.p.execute("model.add_room", {"level": self.level, "points": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]})["result"]["id"]
        m = self.ask("Calculate the area of the selected room", room)
        self.assertEqual(m["kind"], "calculation")
        self.assertAlmostEqual(m["data"]["figures"][0]["value"], 24.0)
        self.assertEqual(m["revision"], self.p.revision)
        self.assertEqual(self.p.revision, 2)                       # reading does not change the project

    def test_check_reports_nothing_found_and_a_real_problem(self):
        self.assertIn("No inconsistencies", self.ask("Check whether the selected elements contain inconsistent dimensions", self.wall)["text"])
        door = self.p.execute("model.add_door", {"wall": self.wall, "offset": 1.0})["result"]["id"]
        self.p.execute("model.delete", {"id": self.wall})          # takes the hosted door with it: no orphan, still consistent
        self.assertNotIn(door, self.p.model.query_ids())

    def test_a_selection_is_required_when_the_request_refers_to_it(self):
        m = self.a.ask(self.p, "Explain the selected element's parameters", {})
        self.assertEqual(m["kind"], "failed")
        self.assertIn("Select", m["text"])


class Proposals(Base):
    def test_create_wall_is_a_pending_proposal_and_changes_nothing_until_approved(self):
        before = self.p.revision
        m = self.ask("Create a wall 5 meters long, 200 mm thick, and 3 meters high.")
        pr = m["proposal"]
        self.assertEqual((m["kind"], pr["status"]), ("proposal", "pending"))
        self.assertEqual(self.p.revision, before)
        self.assertEqual(len(list(self.p.model.query_ids())), 1)
        self.assertEqual(pr["revision"], before)
        self.assertEqual(pr["operation"], ["model.add_wall"])
        self.assertEqual(pr["steps"][0]["params"]["thickness"], 200.0)
        self.assertTrue(any("origin" in a for a in pr["assumptions"]))           # the placement it chose is stated
        for field in ("id", "task", "project", "revision", "targets", "operation", "units", "preconditions", "validation", "risk", "explanation", "changes"):
            self.assertIn(field, pr)
        done = self.a.approve(self.p, pr["id"])
        self.assertEqual(done["kind"], "success")
        self.assertEqual(len(list(self.p.model.query_ids())), 2)
        self.assertTrue(all(v["ok"] for v in done["proposal"]["result"]["verification"]))

    def test_set_property_converts_units_previews_and_verifies(self):
        m = self.ask("Change the selected wall height to 3.2 meters.", self.wall)
        pr = m["proposal"]
        shown = {c["label"]: (c["before"], c["after"]) for c in pr["changes"]}
        self.assertEqual(shown["Height"], (3.0, 3.2))
        self.assertEqual(shown["Area"], ("15.000 m²", "16.000 m²"))             # what follows from the change is previewed too
        self.assertEqual(shown["Volume"], ("3.000 m³", "3.200 m³"))
        self.assertEqual(self.rows(self.wall)["height"], 3.0)                    # the preview ran on a scratch copy
        self.a.approve(self.p, pr["id"])
        self.assertEqual(self.rows(self.wall)["height"], 3.2)
        m2 = self.ask("set the thickness of the selected wall to 25 cm", self.wall)
        self.a.approve(self.p, m2["proposal"]["id"])
        self.assertEqual(self.rows(self.wall)["thickness"], 250.0)               # 25 cm read as 250 mm, the parameter's unit

    def test_a_value_the_project_refuses_never_becomes_a_proposal(self):
        m = self.ask("Change the selected wall height to 900 meters", self.wall)
        self.assertEqual(m["kind"], "failed")
        self.assertNotIn("proposal", m)
        self.assertEqual(self.rows(self.wall)["height"], 3.0)

    def test_reject_and_cancel_leave_the_project_alone(self):
        pr = self.ask("Change the selected wall height to 4 m", self.wall)["proposal"]
        self.a.reject(self.p, pr["id"])
        self.assertEqual(self.rows(self.wall)["height"], 3.0)
        with self.assertRaises(Conflict):
            self.a.approve(self.p, pr["id"])                                     # a rejected proposal cannot be approved
        pr2 = self.ask("Change the selected wall height to 4 m", self.wall)["proposal"]
        self.a.cancel(self.p, pr2["id"])
        self.assertEqual(self.a.proposals[pr2["id"]]["status"], "cancelled")

    def test_a_stale_proposal_is_refused_not_applied(self):
        pr = self.ask("Change the selected wall height to 4 m", self.wall)["proposal"]
        self.p.execute("model.set_property", {"id": self.wall, "key": "thickness", "value": 300})      # the project moves on
        with self.assertRaises(Conflict) as c:
            self.a.approve(self.p, pr["id"])
        self.assertIn("changed", str(c.exception))
        self.assertEqual(self.rows(self.wall)["height"], 3.0)
        self.assertEqual(self.a.proposals[pr["id"]]["status"], "stale")

    def test_approving_for_another_revision_is_refused(self):
        pr = self.ask("Change the selected wall height to 4 m", self.wall)["proposal"]
        with self.assertRaises(Conflict):
            self.a.approve(self.p, pr["id"], expected_revision=pr["revision"] + 5)

    def test_delete_is_high_risk_and_needs_confirmation(self):
        pr = self.ask("Delete the selected elements", self.wall)["proposal"]
        self.assertEqual((pr["risk"], pr["requires_confirmation"]), ("high", True))
        with self.assertRaises(Conflict):
            self.a.approve(self.p, pr["id"])
        self.assertIn(self.wall, self.p.model.query_ids())
        self.a.approve(self.p, pr["id"], confirm_high_risk=True)
        self.assertNotIn(self.wall, self.p.model.query_ids())

    def test_changing_many_elements_is_high_risk(self):
        ids = [self.wall] + [self.p.execute("model.add_wall", {"start": [0, 1000 * i], "end": [5000, 1000 * i], "level": self.level})["result"]["id"] for i in range(1, 7)]
        pr = self.ask("Set the height of the selected walls to 3.5 m", *ids)["proposal"]
        self.assertEqual(pr["risk"], "high")

    def test_a_failure_part_way_is_undone_so_the_proposal_applies_in_full_or_not_at_all(self):
        pr = self.ask("Move the selected element by 1 m in x", self.wall)["proposal"]
        pr["steps"].append({"op": "model.set_property", "params": {"id": self.wall, "key": "height", "value": 9999}})      # a second step the project will refuse
        before = (self.p.revision, self.rows(self.wall))
        m = self.a.approve(self.p, pr["id"])
        self.assertEqual(m["kind"], "failed")
        self.assertEqual(self.a.proposals[pr["id"]]["status"], "failed")
        self.assertEqual(self.rows(self.wall), before[1])
        self.assertEqual(next(r for r in self.p.properties({"model": self.wall})["groups"][0]["rows"] if r.get("key") == "start_x")["value"], 0.0)      # the move was taken back

    def test_move_and_section_proposals_show_what_would_change(self):
        mv = self.ask("Move the selected element by 1.5 m in x", self.wall)["proposal"]
        self.assertTrue(any(c["label"] == "Start X" and c["before"] == 0.0 and c["after"] == 1500.0 for c in mv["changes"]))
        sec = self.ask("Generate a section through the selected objects.", self.wall)["proposal"]
        self.assertEqual(sec["operation"], ["view.add_section"])
        self.assertTrue(any(c["label"] == "New view" for c in sec["changes"]))
        n = len(self.p.manifest["views"])
        self.a.approve(self.p, sec["id"])
        self.assertEqual(len(self.p.manifest["views"]), n + 1)

    def test_undo_reverts_an_approved_change(self):
        pr = self.ask("Change the selected wall height to 4 m", self.wall)["proposal"]
        done = self.a.approve(self.p, pr["id"])
        for _ in range(done["proposal"]["undo_steps"]):
            self.p.undo()
        self.assertEqual(self.rows(self.wall)["height"], 3.0)

    def test_unknown_proposal_and_new_project_reset(self):
        with self.assertRaises(NotFound):
            self.a.approve(self.p, "nope")
        pr = self.ask("Change the selected wall height to 4 m", self.wall)["proposal"]
        other = Project.create("Other")
        self.assertEqual(self.a.history(other)["messages"], [])                # a different project starts with no proposals
        with self.assertRaises(NotFound):
            self.a.approve(other, pr["id"])


class Scratch(unittest.TestCase):
    def test_a_scratch_copy_is_independent(self):
        p = Project.create("S")
        level = p.manifest["levels"][0]["id"]
        w = p.execute("model.add_wall", {"start": [0, 0], "end": [5000, 0], "level": level})["result"]["id"]
        s = p.scratch()
        s.execute("model.delete", {"id": w})
        self.assertIn(w, p.model.query_ids())
        self.assertNotIn(w, s.model.query_ids())
        self.assertEqual(p.revision, 1)
        self.assertIsNone(s.root)


class DeleteAnimated(unittest.TestCase):
    def test_deleting_an_animated_element_takes_its_animation_with_it(self):
        from applications.SPADRA import animation_bridge as AB
        if not AB.BRIDGE.available:
            self.skipTest("the SLAI Animation Agent is not importable here")
        p = Project.create("A")
        level = p.manifest["levels"][0]["id"]
        w = p.execute("model.add_wall", {"start": [0, 0], "end": [5000, 0], "level": level})["result"]["id"]
        p.execute("anim.pose", {"id": w, "time": 1.0, "values": {"translation": [1, 0, 0]}})
        p.execute("model.delete", {"id": w})
        self.assertNotIn(w, p.model.query_ids())
        p.undo()
        self.assertIn(w, p.model.query_ids())


if __name__ == "__main__":
    unittest.main()
