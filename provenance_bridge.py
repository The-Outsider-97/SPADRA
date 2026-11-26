"""SPADRA's connection to SLAI's Provenance subsystem: who changed what, when, from which revision to which, and on which evidence.

SPADRA defines no provenance engine of its own. The record is SLAI's tamper-evident ``AuditLedger`` (``src/agents/provenance/provenance_audit.py``); when the
SLAI Provenance Agent can be loaded it also mirrors every event into the agent's lineage graph (``ProvenanceAgent.record_audit_event``). What is SPADRA's own is
the *domain adapter*: which SPADRA operations are significant, how a command becomes an event, and how a project carries its history.

* **One ledger per project** (``<data dir>/provenance/<project id>.jsonl``), append-only and hash-chained. A saved project carries a snapshot of it
  (``provenance/ledger.jsonl``) and the chain head in its manifest, so opening a project verifies the history
  against the manifest: a cut or replaced history is reported, never silently accepted.
* **What is recorded**: every committed command, every rejected command (with the reason), undo and redo, creating/opening/saving/copying a project, proposals
  (made, approved, run, failed), and everything the regulation assistant does. Not recorded: cursor moves, view changes, rendering, reads. A command's
  parameters are kept when small and as a digest and size when large (an imported file is not copied into the history).
* **Who**: the person at this session (``human``), SPADRA itself (``system``), or an agent (``ai``). An agent's *suggestion* and a person's *approval of it* are
  separate events, so an AI-made proposal that a person approved never reads as a person's own edit.
* **Failure**: when the ledger cannot be written the event waits in memory, the state becomes ``degraded`` and says how many events are unrecorded, and the next
  event retries them in order. A caller that must not proceed without durable evidence (a regulation correction) asks ``ready()`` first. Modelling itself is
  never blocked by a failing history.
* **What it does not claim**: a hash chain shows the history is consistent and unchanged since it was written, not who wrote it; the ledger is unsigned unless
  an HMAC key is configured (``SPADRA_PROVENANCE_KEY``), and the status says which.
* **Elements with no recorded history** (made before this existed, or imported from outside SPADRA) are reported as having an unknown history, not given one.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from ..tools.errors import BadRequest

LEDGER_NAME = "provenance/ledger.jsonl"
MAX_PARAM_BYTES = 2048
HUMAN = {"id": "spadra-local-user", "type": "human"}
SYSTEM = {"id": "spadra", "type": "system"}

_CTX: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("spadra_provenance_context", default=None)


@contextlib.contextmanager
def context(*, actor: Mapping[str, Any] | None = None, origin: str | None = None, causes: Sequence[str] = (), note: str | None = None) -> Iterator[None]:
    """Say who and why for the commands run inside the block (a person's approval of a proposal, the regulation assistant's own steps)."""
    outer = _CTX.get() or {}
    merged = {**outer}
    if actor is not None:
        merged["actor"] = dict(actor)
    if origin is not None:
        merged["origin"] = origin
    if causes:
        merged["causes"] = list(dict.fromkeys([*outer.get("causes", ()), *causes]))
    if note is not None:
        merged["note"] = note
    token = _CTX.set(merged)
    try:
        yield
    finally:
        _CTX.reset(token)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")).hexdigest()


def _elements(params: Mapping[str, Any], result: Any) -> list[str]:
    """Element ids a command names (``id``, ``ids``, ``host``, a created id), for the per-element history."""
    out: list[str] = []
    for key in ("id", "host", "element", "target"):
        if isinstance(params.get(key), str):
            out.append(params[key])
    for key in ("ids", "elements", "targets"):
        if isinstance(params.get(key), list):
            out += [x for x in params[key] if isinstance(x, str)]
    if isinstance(result, Mapping):
        if isinstance(result.get("id"), str):
            out.append(result["id"])
        if isinstance(result.get("ids"), list):
            out += [x for x in result["ids"] if isinstance(x, str)]
    return list(dict.fromkeys(out))[:200]


def _bounded(params: Mapping[str, Any]) -> dict[str, Any]:
    """The parameters of a command as the history keeps them: whole when small, a digest and a size when large."""
    try:
        raw = json.dumps(params, sort_keys=True, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        return {"unrecorded": True, "reason": "parameters are not JSON"}
    if len(raw.encode("utf-8")) <= MAX_PARAM_BYTES:
        return {"params": json.loads(raw)}
    return {"params_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(), "params_bytes": len(raw.encode("utf-8")), "param_keys": sorted(map(str, params))[:40]}


def proposal_event_id(project: Any, proposal: Mapping[str, Any], stage: str) -> str:
    return f"{project.manifest['id']}:proposal:{proposal['id']}:{stage}"


def proposal_actor(source: str) -> dict[str, str]:
    """Who *made* a proposal: the rule engine and other deterministic checks are the system; anything else that suggests a change is an AI assistant, never the person."""
    if str(source).startswith(("regulation", "model-check")):
        return dict(SYSTEM)
    return {"id": f"spadra-{str(source)[:40] or 'assistant'}", "type": "ai"}


def record_proposal(project: Any, stage: str, proposal: Mapping[str, Any], *, actor: Mapping[str, Any] | None = None, payload: Mapping[str, Any] | None = None,
                    causes: Sequence[str] = ()) -> str | None:
    """Record one stage of a proposal's life (made, approved, rejected, cancelled, executed, failed). Returns the event id, or ``None`` when no history is attached.
    Only what identifies the proposal is kept: its title, risk, the operations and a digest of the steps, and the evidence *kind* with a digest, never the evidence text."""
    rec = getattr(project, "provenance", None)
    if rec is None:
        return None
    steps = proposal.get("steps") or []
    body = {"proposal": proposal["id"], "title": str(proposal.get("title", ""))[:200], "risk": proposal.get("risk"), "base_revision": proposal.get("revision"),
            "operations": [s.get("op") for s in steps][:200], "steps_sha256": _digest(steps), "evidence_kind": proposal.get("evidence_kind"),
            "evidence_sha256": _digest(proposal.get("evidence")) if proposal.get("evidence") is not None else None, **dict(payload or {})}
    eid = proposal_event_id(project, proposal, stage)
    rec.record(project, f"spadra.proposal.{stage}", actor=actor, subject={"elements": list(proposal.get("targets") or [])[:200], "proposal": proposal["id"]},
               revision={"at": project.revision}, payload=body, causes=causes, event_id=eid)
    return eid


class SpadraProvenance:
    """One per application. Owns the open project's ledger and turns SPADRA events into records."""

    def __init__(self, data_dir: Path) -> None:
        self.dir = Path(data_dir) / "provenance"
        self._lock = threading.RLock()
        self._ledger: Any = None
        self._ledger_project: str | None = None
        self._pending: list[dict[str, Any]] = []
        self._last_error: str | None = None
        self._agent: Any = None
        self._agent_tried = False
        self._mirror_failures = 0
        key = os.environ.get("SPADRA_PROVENANCE_KEY")
        self._hmac_key = key.encode("utf-8") if key else None
        self._import_error: str | None = None
        try:
            from src.agents.provenance.provenance_audit import AuditLedger            # noqa: F401
            self._ledger_cls = AuditLedger
        except Exception as exc:                                                       # noqa: BLE001 - a SLAI checkout without the provenance package
            self._ledger_cls = None
            self._import_error = f"{type(exc).__name__}: {exc}"[:300]

    # ------------------------------------------------------------------------------------------------------------------ availability
    @property
    def available(self) -> bool:
        return self._ledger_cls is not None

    def _agent_or_none(self) -> Any:
        if not self._agent_tried:
            self._agent_tried = True
            try:
                from .agent_hub import HUB
                HUB.configure_provenance(self.dir / "lineage.json")
                self._agent = HUB.load("provenance")
            except Exception:                                                         # noqa: BLE001 - the agent is an enhancement: the ledger stands without it
                self._agent = None
        return self._agent

    def _path(self, project_id: str) -> Path:
        return self.dir / f"{project_id}.jsonl"

    def _open(self, project: Any) -> Any:
        pid = str(project.manifest["id"])
        with self._lock:
            if self._ledger is not None and self._ledger_project == pid:
                return self._ledger
            if self._ledger_cls is None:
                raise RuntimeError(self._import_error or "provenance package unavailable")
            self._ledger = self._ledger_cls(self._path(pid), hmac_key=self._hmac_key)
            self._ledger_project = pid
            return self._ledger

    # ------------------------------------------------------------------------------------------------------------------ attaching
    def attach(self, project: Any) -> None:
        """Start recording a project's events (idempotent)."""
        project.provenance = self
        project._prov_id = str(project.manifest["id"])
        if self._observe not in project.observers:
            project.observers.append(self._observe)

    # ------------------------------------------------------------------------------------------------------------------ recording
    def record(self, project: Any, kind: str, *, subject: Mapping[str, Any] | None = None, revision: Mapping[str, Any] | None = None, payload: Mapping[str, Any] | None = None,
               actor: Mapping[str, Any] | None = None, causes: Sequence[str] = (), event_id: str | None = None) -> dict[str, Any] | None:
        """Record one event; returns the ledger record, or ``None`` when it could not be written yet (it is queued and the state says so)."""
        ctx = _CTX.get() or {}
        who = dict(actor or ctx.get("actor") or HUMAN)
        all_causes = list(dict.fromkeys([*ctx.get("causes", ()), *causes]))
        body = dict(payload or {})
        if ctx.get("origin"):
            body.setdefault("origin", ctx["origin"])
        if ctx.get("note"):
            body.setdefault("note", ctx["note"])
        rev = dict(revision or {"at": project.revision})
        pid = str(project.manifest["id"])
        eid = event_id or f"{pid}:r{rev.get('after', rev.get('at', project.revision))}:{kind}:{_digest([subject, body])[:12]}"
        event = {"project": pid, "event_id": eid, "kind": kind, "actor": who, "subject": dict(subject or {}), "revision": rev, "causes": all_causes, "payload": body}
        with self._lock:
            self._pending.append(event)
            return self._flush(project)

    def _flush(self, project: Any) -> dict[str, Any] | None:
        last = None
        if self._ledger_cls is None:
            self._last_error = self._import_error
            return None
        while self._pending:
            event = self._pending[0]
            if event["project"] != str(project.manifest["id"]):
                self._pending.pop(0)                                # an event of a project that is no longer the open one cannot be written to this ledger
                continue
            try:
                ledger = self._open(project)
                agent = self._agent_or_none()
                if agent is not None:
                    out = agent.record_audit_event(ledger, event_id=event["event_id"], kind=event["kind"], actor=event["actor"], subject=event["subject"], revision=event["revision"],
                                                   causes=event["causes"], payload=event["payload"])
                    last = out["record"]
                    if not out["mirrored"]:
                        self._mirror_failures += 1
                else:
                    last = ledger.append(event_id=event["event_id"], kind=event["kind"], actor=event["actor"], subject=event["subject"], revision=event["revision"],
                                         causes=event["causes"], payload=event["payload"])
                self._pending.pop(0)
                self._last_error = None
            except Exception as exc:                                  # noqa: BLE001 - history must never break modelling; the state says what is unrecorded
                kind = type(exc).__name__
                if kind in ("ProvenanceValidationError", "ProvenanceConflictError"):
                    self._pending.pop(0)                              # a malformed event can never succeed: drop it, but say so
                    self._last_error = f"an event was refused and dropped ({kind}: {str(exc)[:160]})"
                    continue
                self._last_error = f"{kind}: {str(exc)[:200]}"
                break
        return last

    def ready(self, project: Any) -> bool:
        """True when a record could be written durably right now: what an action that must not proceed without its evidence checks first."""
        with self._lock:
            if self._ledger_cls is None:
                return False
            try:
                ledger = self._open(project)
                if self._pending:
                    self._flush(project)
                return not self._pending and bool(ledger.writable())
            except Exception as exc:                                  # noqa: BLE001
                self._last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
                return False

    # ------------------------------------------------------------------------------------------------------------------ project events
    def _observe(self, project: Any, event: Mapping[str, Any]) -> None:
        kind = event["kind"]
        if kind == "command":
            raw = event.get("raw_params") or {}
            result = event.get("result")
            elements = _elements(raw, result)
            created = result.get("ids") if isinstance(result, Mapping) and isinstance(result.get("ids"), list) else ([result["id"]] if isinstance(result, Mapping) and isinstance(result.get("id"), str) else [])
            self.record(project, "spadra.command", subject={"elements": elements}, revision={"before": event["before"], "after": event["after"]},
                        payload={"op": event["op"], "label": event["label"], **_bounded(raw), "result_ids": [x for x in created if isinstance(x, str)][:200]})
        elif kind == "command.rejected":
            raw = event.get("raw_params") or {}
            self.record(project, "spadra.command.rejected", subject={"elements": _elements(raw, None)}, revision={"at": event["at"]},
                        payload={"op": event["op"], "error": event["error"], "message": event["message"], **_bounded(raw)})
        elif kind in ("undo", "redo"):
            self.record(project, f"spadra.{kind}", revision={"before": event["before"], "after": event["after"]}, payload={"label": event["label"]})
        else:                                                        # proposals and other domain events carry their own shape
            self.record(project, str(event["kind"]), subject=event.get("subject"), revision=event.get("revision"), payload=event.get("payload"), actor=event.get("actor"),
                        causes=event.get("causes", ()), event_id=event.get("event_id"))

    def on_new(self, project: Any) -> None:
        self.attach(project)
        self.record(project, "spadra.project.new", actor=HUMAN, payload={"name": project.name, "unit": project.manifest["unit"]}, event_id=f"{project.manifest['id']}:new")

    def on_open(self, project: Any) -> dict[str, Any]:
        """Take a saved project's history into the working ledger and check it against the manifest. Returns what was found; the result is also recorded."""
        self.attach(project)
        pid = str(project.manifest["id"])
        saved = Path(project.root) / LEDGER_NAME if project.root else None
        found: dict[str, Any] = {"state": "none", "detail": "This project was saved before history was recorded: its earlier history is unknown."}
        anchor = (project.manifest.get("provenance") or {}).get("head")
        if self._ledger_cls is None:
            return {"state": "unavailable", "detail": self._import_error}
        with self._lock:
            self._ledger = None
            work = self._path(pid)
            if saved is not None and saved.is_file():
                self.dir.mkdir(parents=True, exist_ok=True)
                snapshot = self._ledger_cls(saved, hmac_key=self._hmac_key)
                check = snapshot.verify()
                anchored = snapshot.verify_anchor(anchor) if anchor else {"ok": None, "detail": "the manifest carries no chain head"}
                discarded = None
                if work.exists():
                    current = self._ledger_cls(work, hmac_key=self._hmac_key)
                    if current.head()["hash"] != snapshot.head()["hash"]:           # work that was never saved: keep it aside, never lose it
                        discarded = self.dir / f"{pid}.{int(time.time())}.unsaved.jsonl"
                        shutil.copy2(work, discarded)
                shutil.copy2(saved, work)
                found = {"state": "verified" if check["ok"] and anchored["ok"] in (True, None) else "damaged", "records": check["records"], "chain": check["ok"],
                         "problems": check["problems"][:10], "anchor": anchored, "authenticity": check["authenticity"],
                         "unsaved_history_archived": str(discarded.name) if discarded else None}
            elif work.exists():
                found = {"state": "working-copy", "detail": "No history was saved with this project; the working history on this computer is used."}
        self.record(project, "spadra.project.open", actor=HUMAN, payload={"history": {k: v for k, v in found.items() if k != "problems"}, "name": project.name},
                    event_id=f"{pid}:open:{int(time.time() * 1000)}")
        return found

    def snapshot_for_save(self, project: Any, target: Path) -> dict[str, Any] | None:
        """Called by ``Project.save`` before the manifest is written: put the ledger beside the project and return the manifest entry (the chain head)."""
        if self._ledger_cls is None:
            return None
        with self._lock:
            try:
                pid = str(project.manifest["id"])
                origin = getattr(project, "_prov_id", pid)
                if pid != origin:                                       # Save As: the copy has its own id and carries the original's history
                    src = self._path(origin)
                    if src.exists() and not self._path(pid).exists():
                        self.dir.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, self._path(pid))
                    project._prov_id = pid
                    self._ledger = None
                    self.record(project, "spadra.project.copy", actor=HUMAN, payload={"from_project": origin, "name": project.name}, event_id=f"{pid}:copy")
                self.record(project, "spadra.project.save", actor=HUMAN, payload={"target": target.name}, event_id=f"{pid}:save:r{project.revision}:{int(time.time() * 1000)}")
                ledger = self._open(project)
                head = ledger.head()
                dest = target / LEDGER_NAME
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ledger.path, dest)
                return {"schema": "spadra.provenance.v1", "head": head, "file": LEDGER_NAME, "signed": self._hmac_key is not None}
            except Exception as exc:                                    # noqa: BLE001 - saving the model must not fail because its history could not be copied
                self._last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
                return None

    # ------------------------------------------------------------------------------------------------------------------ queries
    def status(self, project: Any | None) -> dict[str, Any]:
        base = {"available": self.available, "agent": self._agent_or_none() is not None if self.available else False, "signed": self._hmac_key is not None,
                "mirror_failures": self._mirror_failures, "pending": len(self._pending), "last_error": self._last_error}
        if not self.available:
            return {**base, "state": "unavailable", "reason": self._import_error, "hint": "Start SPADRA inside a SLAI checkout that contains src/agents/provenance"}
        if project is None:
            return {**base, "state": "idle"}
        with self._lock:
            ledger = self._open(project)
            head = ledger.head()
            state = "degraded" if self._pending or self._last_error else "ok"
            return {**base, "state": state, "records": head["seq"], "head": head,
                    "note": "Hash-chained and append-only. It shows the history is consistent and unchanged since it was written; it does not show who wrote it"
                            + ("" if self._hmac_key is None else " (records are HMAC-signed)") + "."}

    def events(self, project: Any, *, kind: str | None = None, element: str | None = None, actor_type: str | None = None, after: int = 0, limit: int = 200) -> dict[str, Any]:
        with self._lock:
            ledger = self._open(project)
            rows = ledger.query(kind=kind, subject_id=element, actor_type=actor_type, after_seq=after, limit=max(1, min(int(limit), 1000)))
            return {"events": [self._public(r) for r in rows], "head": ledger.head()}

    @staticmethod
    def _public(rec: Mapping[str, Any]) -> dict[str, Any]:
        return {k: rec[k] for k in ("seq", "event_id", "ts", "kind", "actor", "subject", "revision", "causes", "payload", "hash") if k in rec}

    def element(self, project: Any, element_id: str) -> dict[str, Any]:
        """What the history says about one element. An element the history never mentions has an *unknown* history."""
        with self._lock:
            ledger = self._open(project)
            rows = [self._public(r) for r in ledger.query(subject_id=element_id, limit=1000) if element_id in (r["subject"].get("elements") or []) or r["kind"].startswith("regulation.")]
            rows = [r for r in rows if element_id in json.dumps(r["subject"])]
            if not rows:
                return {"id": element_id, "known": False, "events": [],
                        "note": "No recorded history: this element was made before SPADRA recorded history, or it was imported from outside SPADRA. Nothing is inferred about it."}
            return {"id": element_id, "known": True, "first_seen": rows[0], "events": rows}

    def verify(self, project: Any) -> dict[str, Any]:
        with self._lock:
            ledger = self._open(project)
            out = ledger.verify()
            anchor = (project.manifest.get("provenance") or {}).get("head")
            out["anchor"] = ledger.verify_anchor(anchor) if anchor else {"ok": None, "detail": "no saved chain head to compare with (the project has not been saved with history)"}
            out["agent_mirror"] = {"enabled": self._agent_or_none() is not None, "failures": self._mirror_failures}
            return out

    def export(self, project: Any, redact: Sequence[str] = ()) -> dict[str, Any]:
        with self._lock:
            ledger = self._open(project)
            if not ledger.verify()["ok"]:
                raise BadRequest("The history failed verification, so it is not exported as a trustworthy record", hint="Run verification and inspect the problems first")
            out = ledger.export(redact=redact)
            out["project"] = {"id": project.manifest["id"], "name": project.name, "revision": project.revision}
            return out
