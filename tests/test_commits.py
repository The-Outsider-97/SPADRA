"""SPADRA connected to SLAI's commit contract: revisions, consented proposals committed through ``CommitReconciliationStore``, receipts that
survive undo and save/reopen, and the failure modes (no consent, stale base, lost reply, a refused step). Uses SLAI's real planning modules."""
from __future__ import annotations

import importlib.util
import json
import time
import unittest
from pathlib import Path
from unittest import mock

from ...tools.errors import Conflict
from .. import assistant as AS
from ..assistant import ASSISTANT
from ...tools.commit_host import CommitOutcomeUnknown
from .harness import ServerCase

HAVE_PLANNING = importlib.util.find_spec("src.agents.planning.commit_reconciliation") is not None


@unittest.skipUnless(HAVE_PLANNING, "needs SLAI's src/agents/planning commit adapters")
class Committing(ServerCase):
    def setUp(self) -> None:
        super().setUp()
        from src.agents.planning.project_adapter import HostCommitRequest
        self.Request = HostCommitRequest
        self.p = self.app.new_project("Commits", "mm")
        level = self.p.manifest["levels"][0]["id"]
        self.wall = self.p.execute("model.add_wall", {"start": [0, 0], "end": [6000, 0], "level": level, "thickness": 200, "height": 3.0})["result"]["id"]
        self.pid = self.p.manifest["id"]
        self.addCleanup(self.app.commits.close)

    def propose(self, height: float = 3.4) -> dict:
        m = ASSISTANT.ask(self.p, f"Change the selected wall height to {height} meters", {"selection": [self.wall]})
        return m["proposal"]

    def height(self) -> float:
        props = self.p.properties({"model": self.wall})
        return {r["key"]: r["value"] for r in [*props["top"], *[x for g in props["groups"] for x in g["rows"]]] if r.get("key")}["height"]

    def request(self, proposal: dict, key: str = "k1", revision: str | None = None):
        return self.Request(self.pid, "project", str(proposal["revision"]) if revision is None else revision, proposal["id"], key)

    # --------------------------------------------------------------------------------------------------------------- revisions
    def test_revisions_are_the_projects_own_and_only_for_the_open_project(self) -> None:
        host = self.app.commits.host
        self.assertEqual(host.current_revisions(self.pid), {"project": str(self.p.revision)})
        self.assertEqual(host.current_revisions("someone-else"), {})
        self.p.execute("model.set_property", {"id": self.wall, "key": "height", "value": 3.1})
        self.assertEqual(host.current_revisions(self.pid), {"project": str(self.p.revision)}, "follows every edit")
        self.app.project = None
        self.assertEqual(host.current_revisions(self.pid), {})

    # ---------------------------------------------------------------------------------------------------------------- approval
    def test_approval_commits_through_the_outbox_and_records_a_receipt_in_the_project(self) -> None:
        proposal = self.propose()
        before = self.p.revision
        message = self.app.commits.approve(proposal["id"], expected_revision=proposal["revision"], confirm_high_risk=False)
        self.assertEqual(message["kind"], "success"); self.assertEqual(self.height(), 3.4)
        key = f"spadra:{self.pid}:{proposal['id']}"
        record = self.app.commits.outbox.store().get(key)
        self.assertEqual(record["status"], "confirmed")
        self.assertEqual(record["receipt"]["committed_revision"], str(self.p.revision)); self.assertGreater(self.p.revision, before)
        self.assertEqual(record["receipt"]["evidence_id"], message["id"])
        self.assertEqual(self.app.commits.host.lookup_commit(self.pid, key).status, "confirmed")
        self.assertIn(key, self.p.manifest["commits"], "the receipt is in the project, so it is saved with it")
        self.assertTrue((Path(self.app.settings.data_dir) / "commits.sqlite3").is_file())

    def test_the_interface_route_takes_the_same_path(self) -> None:
        wall = self.wall
        m = self.api.post("/api/assistant/ask", {"request": "Change the selected wall height to 3.3 meters", "context": {"selection": [wall]}})["message"]
        pr = m["proposal"]
        self.api.post("/api/assistant/approve", {"proposal": pr["id"], "expected_revision": pr["revision"]})
        self.assertEqual(self.app.commits.outbox.store().get(f"spadra:{self.pid}:{pr['id']}")["status"], "confirmed")
        self.assertEqual(self.height(), 3.3)

    def test_approving_twice_does_not_apply_twice(self) -> None:
        proposal = self.propose()
        self.app.commits.approve(proposal["id"], expected_revision=None, confirm_high_risk=False)
        revision = self.p.revision
        with self.assertRaises(Conflict):
            self.app.commits.approve(proposal["id"], expected_revision=None, confirm_high_risk=False)
        self.assertEqual(self.p.revision, revision)

    # ------------------------------------------------------------------------------------------------------------- refusals
    def test_a_proposal_without_a_persons_consent_is_rejected_by_the_host(self) -> None:
        proposal = self.propose()
        receipt = self.app.commits.host.commit(self.request(proposal))
        self.assertEqual(receipt.status, "rejected"); self.assertIn("not been approved by a person", receipt.reason)
        self.assertEqual(self.height(), 3.0)
        self.assertEqual(self.p.revision, int(proposal["revision"]), "nothing was applied")

    def test_a_commit_for_an_old_revision_is_rejected_stale(self) -> None:
        proposal = self.propose()
        ASSISTANT.consent(self.p, proposal["id"], actor="test")
        self.p.execute("model.set_property", {"id": self.wall, "key": "thickness", "value": 220})          # someone else edits first
        receipt = self.app.commits.host.commit(self.request(proposal))
        self.assertEqual(receipt.status, "rejected"); self.assertIn("Stale", receipt.reason)
        self.assertEqual(self.height(), 3.0)

    def test_a_step_the_project_refuses_is_rejected_and_the_earlier_steps_are_undone(self) -> None:
        proposal = self.propose()
        proposal["steps"].append({"op": "model.set_property", "params": {"id": self.wall, "key": "height", "value": 900}})   # beyond what a wall may be
        message = self.app.commits.approve(proposal["id"], expected_revision=None, confirm_high_risk=False)
        self.assertEqual(message["kind"], "failed"); self.assertEqual(self.height(), 3.0, "all or nothing")
        self.assertEqual(self.app.commits.outbox.store().get(f"spadra:{self.pid}:{proposal['id']}")["status"], "rejected")

    # --------------------------------------------------------------------------------------------------------- failure modes
    def test_a_lost_reply_is_reconciled_by_lookup_and_the_change_is_applied_once(self) -> None:
        proposal = self.propose()
        revision = self.p.revision
        real = self.app.commits.host.commit
        def lose_reply(request):
            real(request)
            raise ConnectionError("the reply never arrived")
        with mock.patch.object(self.app.commits.host, "commit", side_effect=lose_reply):
            message = self.app.commits.approve(proposal["id"], expected_revision=None, confirm_high_risk=False)
        self.assertEqual(message["kind"], "success"); self.assertEqual(self.height(), 3.4)
        self.assertEqual(self.p.revision, revision + 1, "one step, applied once")
        self.assertEqual(self.app.commits.outbox.store().get(f"spadra:{self.pid}:{proposal['id']}")["status"], "confirmed")

    def test_a_failure_nobody_can_classify_stays_unknown_and_is_never_retried(self) -> None:
        proposal = self.propose()
        with mock.patch.object(AS.Assistant, "run", side_effect=RuntimeError("disk vanished")):
            with self.assertRaises(CommitOutcomeUnknown) as caught:
                self.app.commits.approve(proposal["id"], expected_revision=None, confirm_high_risk=False)
        key = caught.exception.details["idempotency_key"]
        self.assertEqual(self.app.commits.outbox.store().get(key)["status"], "outcome_unknown")
        self.assertEqual([r["idempotency_key"] for r in self.app.commits.outbox.store().unresolved(self.pid)], [key])
        self.assertEqual(self.height(), 3.0)

    # ---------------------------------------------------------------------------------------------------------- durability
    def test_the_receipt_survives_undo_and_redo(self) -> None:
        proposal = self.propose()
        self.app.commits.approve(proposal["id"], expected_revision=None, confirm_high_risk=False)
        key = f"spadra:{self.pid}:{proposal['id']}"
        receipt = self.app.commits.host.lookup_commit(self.pid, key)
        self.p.undo()
        self.assertEqual(self.height(), 3.0)
        self.assertEqual(self.app.commits.host.lookup_commit(self.pid, key), receipt, "undoing the content does not forget that it was committed")
        self.p.redo()
        self.assertEqual(self.app.commits.host.lookup_commit(self.pid, key), receipt)

    def test_the_receipt_is_saved_with_the_project_and_found_after_reopening(self) -> None:
        proposal = self.propose()
        self.app.commits.approve(proposal["id"], expected_revision=None, confirm_high_risk=False)
        key = f"spadra:{self.pid}:{proposal['id']}"
        receipt = self.app.commits.host.lookup_commit(self.pid, key)
        saved = self.app.save_project(None, "Commits")
        self.app.close_project(discard=True)
        self.assertIsNone(self.app.commits.host.lookup_commit(self.pid, key), "with nothing open there is nothing to consult")
        self.app.open_project(saved["path"])
        self.assertEqual(self.app.commits.host.lookup_commit(self.pid, key), receipt)
        self.assertEqual(json.loads((Path(saved["path"]) / "project.json").read_text())["commits"][key]["receipt"]["status"], "confirmed")

    def test_a_copy_made_with_save_as_is_a_different_project_and_inherits_no_receipts(self) -> None:
        proposal = self.propose()
        self.app.commits.approve(proposal["id"], expected_revision=None, confirm_high_risk=False)
        key = f"spadra:{self.pid}:{proposal['id']}"
        self.app.save_project(None, "Commits")
        copy = self.app.save_project(None, "Copy", save_as=True)
        self.assertNotEqual(self.app.require().manifest["id"], self.pid)
        self.assertIsNone(self.app.commits.host.lookup_commit(self.pid, key))
        self.assertEqual(self.app.require().manifest["commits"], {})
        self.assertTrue(copy["path"])

    # ------------------------------------------------------------------------------------------ SLAI's workflow store on top
    def test_a_revision_bound_plan_goes_stale_when_a_commit_moves_the_project(self) -> None:
        from src.agents.planning.revision_workflow import RevisionTask, RevisionWorkflowStore
        store = RevisionWorkflowStore(Path(self.app.settings.data_dir) / "plans.sqlite3", revision_provider=self.app.commits.host.current_revisions)
        self.addCleanup(store.close)
        source = dict(self.app.commits.host.current_revisions(self.pid))
        store.create_plan(self.pid, [RevisionTask("check", reads=source, writes=("project",))], source, plan_id="plan1")
        self.assertEqual(store.refresh_authoritative_revisions("plan1")["status"]["check"], "pending", "nothing changed yet")
        proposal = self.propose()
        self.app.commits.approve(proposal["id"], expected_revision=None, confirm_high_risk=False)
        refreshed = store.refresh_authoritative_revisions("plan1")
        self.assertEqual(refreshed["status"]["check"], "stale", "the task was planned against a revision that no longer exists")
        self.assertEqual(refreshed["source_revisions"], {"project": str(self.p.revision)})
        self.assertEqual(store.replan_stale("plan1")["status"]["check"], "pending")

    def test_a_confirmed_commit_is_published_as_provenance_evidence_only_when_confirmed(self) -> None:
        events = []
        class Provenance:
            def record_event(self, event): events.append(event)
        proposal = self.propose()
        key = f"spadra:{self.pid}:{proposal['id']}"
        auth = self.app.commits.outbox.authorization(self.pid, "test")
        from src.agents.planning.workflow_runtime import AuthorizationContext
        auth = AuthorizationContext(auth.actor_id, self.pid, frozenset({"workflow:provenance:write"}), time.time() + 60)
        with self.assertRaises(Exception):
            self.app.commits.outbox.store().get(key)                                     # nothing has been prepared yet, so there is nothing to publish
        self.app.commits.approve(proposal["id"], expected_revision=None, confirm_high_risk=False)
        self.assertTrue(self.app.commits.outbox.store().publish_confirmed(key, Provenance(), auth=auth))
        payload = events[0]["payload"]
        self.assertEqual((payload["event_type"], payload["project_id"], payload["artifact_id"]), ("commit.confirmed", self.pid, "project"))
        self.assertEqual(payload["revision_id"], str(self.p.revision)); self.assertEqual(payload["previous_revision_id"], str(proposal["revision"]))


if __name__ == "__main__":
    unittest.main()
