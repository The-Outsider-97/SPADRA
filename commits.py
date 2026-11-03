"""SPADRA's connection to SLAI's revision-aware commit contract (``src/agents/planning/project_adapter.py``).

What SPADRA owns, and so what it reports:

* **Revisions.** One artifact, ``"project"``: the project's ``revision``, which every command, undo and redo moves forward and nothing
  moves back. (SPADRA keeps no separate revision per drawing or model file, and this module invents none.)
* **The commit.** Running a proposal made by the assistant (or by any agent through it). The proposal must exist, must carry the
  recorded consent of a person (``Assistant.consent``), and runs through ``Project.execute`` exactly as any edit does, all steps or none.
  The expected-revision check and the change happen inside one critical section, the application lock.
* **The ledger.** Every decision is stored in the project's manifest under its idempotency key, so it is saved with the project and is
  not undone by Undo. A decision is therefore durable once the project is saved; before that a crash loses the change and its receipt
  together, which the outbox reports as an unresolved commit rather than a success.

The interface's own *Approve* goes through the same path: the person's click is the trusted authorisation, and the commit passes through
SLAI's ``CommitReconciliationStore`` (intent persisted before the change, an uncertain outcome resolved by lookup and never re-sent). An
automated workflow can use ``host`` and the same store; it cannot obtain an ``AuthorizationContext`` except through a person's approval.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

from ..tools.commit_host import Applied, CommitHost, CommitRefused, Outbox, planning_types
from ..tools.errors import Conflict
from .assistant import ASSISTANT

if TYPE_CHECKING:
    from .app import SpadraApp

ARTIFACT = "project"
ACTOR = "spadra-local-user"                       # the person at this loopback session (the server only answers a holder of its token)
OUTBOX = "commits.sqlite3"


class _OpenProjectLedger:
    """The ledger of whichever project is open. ``CommitHost`` only writes for a project whose artifact it found, which is the open one."""

    def __init__(self, app: "SpadraApp") -> None:
        self.app = app

    def get(self, key: str) -> dict[str, Any] | None:
        project = self.app.project
        return project.commit_ledger().get(key) if project is not None else None

    def put(self, key: str, entry: dict[str, Any]) -> None:
        self.app.require().commit_ledger().put(key, entry)


class SpadraCommits:
    def __init__(self, app: "SpadraApp") -> None:
        self.app = app
        self.host = CommitHost(revisions=self.revisions, apply=self._apply, ledger=_OpenProjectLedger(app), lock=app.lock)
        self.outbox = Outbox(app.settings.data_dir / OUTBOX, self.host)

    # ------------------------------------------------------------------------------------------------- the adapter's callbacks
    def revisions(self, project_id: str) -> Mapping[str, str]:
        project = self.app.project
        if project is None or project.manifest["id"] != project_id:
            return {}
        return {ARTIFACT: str(project.revision)}

    def _apply(self, request: Any) -> Applied:
        project = self.app.require()
        message = ASSISTANT.run(project, request.proposal_ref)
        proposal = message["proposal"]
        if proposal["status"] == "failed":                          # the project refused a step; run() already took back the earlier ones
            raise CommitRefused(proposal["status_note"])
        return Applied(revision=str(project.revision), evidence_id=message["id"])

    def close(self) -> None:
        self.outbox.close()

    # ------------------------------------------------------------------------------------------------------------- approval
    def approve(self, pid: str, *, expected_revision: int | None, confirm_high_risk: bool) -> dict[str, Any]:
        """The person approves a proposal: record consent, then commit through the outbox. Returns the message that reports the outcome."""
        with self.app.lock:
            project = self.app.require()
            proposal = ASSISTANT.consent(project, pid, actor=ACTOR, expected_revision=expected_revision, confirm_high_risk=confirm_high_risk)
            project_id = project.manifest["id"]
            request = planning_types().HostCommitRequest(project_id=project_id, artifact_id=ARTIFACT, expected_revision=str(proposal["revision"]),
                                                         proposal_ref=pid, idempotency_key=f"spadra:{project_id}:{pid}")
            record = self.outbox.commit(request, actor=ACTOR)
            if record["status"] == "confirmed" or proposal["status"] == "failed":
                return ASSISTANT.outcome(project, pid)
            raise Conflict(record["receipt"]["reason"], hint="Ask again so the proposal is made for the current project")
