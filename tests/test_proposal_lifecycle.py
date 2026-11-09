"""P2-03 (report Table 17): out-of-order, stale, cancelled and repeated approvals never commit twice or against the wrong model.

Two proposals are made for the same revision (two agents, or one agent twice). Whatever order people act in, the model only ever receives a proposal that
was current when it was approved, at most once, and a proposal that was cancelled, rejected, superseded or already run cannot be run."""
from __future__ import annotations

import unittest

from applications.tools.errors import Conflict

from .test_proposals import Proposals as Base


class Lifecycle(Base):
    def make(self, width_m: float):
        """A proposal that sets D-900's width, from a script run with that input."""
        from applications.SPADRA import scripting as SC
        rec = SC.run(self.p, {"script": self.sid, "inputs": {"w": width_m}})
        pl = SC.plan(self.p, {"run": rec["id"], "targets": [{"output": "width", "family": self.fid, "type": "D-900", "parameter": "width"}]})
        steps = pl["steps"] + [{"op": "script.record", "params": {"record": pl["record"]}}]
        return self.a.propose_commands(self.p, source="script:w v1", title=f"width {width_m}", explanation="e", steps=steps, evidence_kind="script")["proposal"]

    def test_out_of_order_approval_the_later_proposal_commits_and_the_earlier_one_goes_stale(self) -> None:
        first, second = self.make(1.0), self.make(1.2)
        self.assertEqual(first["revision"], second["revision"])
        self.a.approve(self.p, second["id"], expected_revision=second["revision"])               # acted on in the opposite order
        self.assertEqual(self.width(), 1200)
        with self.assertRaises(Conflict):
            self.a.approve(self.p, first["id"], expected_revision=first["revision"])
        self.assertEqual(self.width(), 1200, "the superseded proposal must not overwrite the one that committed")
        self.assertEqual(self.a.proposals[first["id"]]["status"], "stale")
        self.assertEqual(self.a.proposals[second["id"]]["status"], "executed")

    def test_a_cancelled_proposal_cannot_be_consented_or_run(self) -> None:
        prop = self.make(1.0)
        self.a.consent(self.p, prop["id"], actor="person")                                       # consent was given...
        self.a.cancel(self.p, prop["id"])                                                        # ...and then the person withdrew it
        with self.assertRaises(Conflict):
            self.a.run(self.p, prop["id"])
        with self.assertRaises(Conflict):
            self.a.consent(self.p, prop["id"], actor="person")
        self.assertEqual(self.width(), 900)

    def test_approving_twice_applies_once(self) -> None:
        prop = self.make(1.0)
        self.a.approve(self.p, prop["id"], expected_revision=prop["revision"])
        revision, depth = self.p.revision, len(self.p.undo_stack)
        for call in (lambda: self.a.approve(self.p, prop["id"], expected_revision=prop["revision"]), lambda: self.a.run(self.p, prop["id"])):
            with self.assertRaises(Conflict):
                call()
        self.assertEqual((self.p.revision, len(self.p.undo_stack), self.width()), (revision, depth, 1000))

    def test_consent_does_not_survive_the_model_moving_on_before_the_run(self) -> None:
        prop = self.make(1.0)
        self.a.consent(self.p, prop["id"], actor="person")
        self.p.execute("family.type_define", {"id": self.fid, "name": "D-1000", "values": {"width": 1000}})    # something else commits between consent and run
        with self.assertRaises(Conflict):
            self.a.run(self.p, prop["id"])
        self.assertEqual(self.width(), 900)
        self.assertEqual(self.a.proposals[prop["id"]]["status"], "stale")

    def test_a_proposal_cannot_be_run_without_a_persons_consent_whatever_its_status(self) -> None:
        prop = self.make(1.0)
        with self.assertRaises(Conflict):
            self.a.run(self.p, prop["id"])
        self.assertEqual(self.a.proposals[prop["id"]]["status"], "pending")
        self.assertEqual(self.width(), 900)

    def test_a_proposal_made_for_one_project_is_not_found_in_another(self) -> None:
        from applications.SPADRA.project import Project
        prop = self.make(1.0)
        other = Project.create("Other")
        with self.assertRaises(Exception):
            self.a.approve(other, prop["id"])
        self.assertEqual(self.width(), 900)


del Base                                                                                         # only Lifecycle is collected, not the inherited tests twice

if __name__ == "__main__":
    unittest.main()
