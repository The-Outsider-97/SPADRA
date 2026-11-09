"""Background operations: work that can take a while runs off the request thread, so the interface stays usable while it does.

A **job** has a state (queued, running, done, failed, cancelled), a progress between 0 and 1 with a message saying what it is doing, and a result or an
error (with the error's category and recovery). The interface starts one, shows its progress, and may cancel it.

How a job and the project fit together is the point of this module, because one project is open at a time and every request takes the application lock:

* **Reading and heavy work happen outside the lock.** A job takes a private snapshot of the project (a scratch copy: its own memory, no folder, never
  shown) while it holds the lock for a moment, then parses, converts, measures or simulates on the snapshot with the lock released.
* **Writing is one short command under the lock.** An import finishes by running the ordinary ``model.import`` command, so it is one revision, one undo
  step, validated and rolled back like any other change. The file was already parsed during the job and the result is reused, so the lock is held only
  for the merge.
* **Cancelling is honest.** It is checked between steps; once the job has begun to apply its change to the project it can no longer be cancelled, and the
  answer says so. A cancelled job has changed nothing.
* If the project was closed or replaced while a job ran, the job does not apply its change: it fails with a message saying so.

Results that are files (an export) are kept in memory for a limited time and size, and downloaded from the job."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from ..tools.errors import AppError, BadRequest, Conflict, LimitExceeded, NotFound
from ..tools.validation import choice, new_id, text
from . import interop as IO
from .diagnostics import Diagnostics, describe

MAX_RUNNING = 2
KEEP_FINISHED = 40
MAX_FILE_BYTES_KEPT = 256 * 1024 * 1024


class Cancelled(Exception):
    """Raised inside a job at the next step after the person asked to cancel."""


@dataclass
class Job:
    id: str
    kind: str
    label: str
    project_id: str
    status: str = "queued"
    progress: float = 0.0
    message: str = "Waiting to start"
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    result: Any = None
    error: dict[str, Any] | None = None
    file: tuple[bytes, str, str] | None = None
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    _committing: bool = False

    # -- called by the job's own code ------------------------------------------------------------------------------------------
    def step(self, progress: float, message: str) -> None:
        """Report where the job is, and stop here if the person cancelled."""
        if self._cancel.is_set():
            raise Cancelled()
        self.progress, self.message = max(0.0, min(1.0, progress)), message

    def begin_commit(self) -> None:
        """From here the change is being applied to the project and cannot be cancelled."""
        if self._cancel.is_set():
            raise Cancelled()
        self._committing = True
        self.progress, self.message = 0.9, "Applying to the project"

    def public(self) -> dict[str, Any]:
        out = {"id": self.id, "kind": self.kind, "label": self.label, "status": self.status, "progress": round(self.progress, 3), "message": self.message,
               "created": self.created, "started": self.started, "finished": self.finished, "cancellable": self.status in ("queued", "running") and not self._committing,
               "error": self.error, "hasFile": self.file is not None}
        if self.file is not None:
            out["file"] = {"name": self.file[1], "bytes": len(self.file[0])}
        if self.result is not None:
            out["result"] = self.result
        return out


Runner = Callable[[Any, Job, Mapping[str, Any]], Any]


def _project(app: Any, job: Job) -> Any:
    """The open project, provided it is still the one the job was started for."""
    project = app.require()
    if project.manifest["id"] != job.project_id:
        raise Conflict("The project was closed or replaced while this was running, so nothing was applied")
    return project


def _snapshot(app: Any, job: Job) -> Any:
    with app.lock:
        return _project(app, job).scratch()


def _commit(app: Any, job: Job, op: str, params: dict[str, Any]) -> Any:
    job.begin_commit()
    with app.lock:
        return _project(app, job).execute(op, params)["result"]


# --------------------------------------------------------------------------------------------------------------------------------- kinds
def _file(p: Mapping[str, Any]) -> tuple[bytes, str, str, str]:
    fmt = text(p.get("format"), "format", 8).lower()
    return IO.decode(p.get("data")), fmt, text(p.get("name") or "Imported file", "name", 120), text(p.get("unit") or "mm", "unit", 8)


def import_model(app: Any, job: Job, p: Mapping[str, Any]) -> Any:
    raw, fmt, name, unit = _file(p)
    job.step(0.1, f"Reading {fmt.upper()}")
    check = IO.inspect_file("model", fmt, raw, name=name, unit=unit)
    job.step(0.7, "Checked the file")
    IO._require_valid(check)
    return _commit(app, job, "model.import", {"format": fmt, "data": p["data"], "name": name, "unit": unit})


def import_drawing(app: Any, job: Job, p: Mapping[str, Any]) -> Any:
    raw, fmt, name, _ = _file(p)
    job.step(0.1, f"Reading {fmt.upper()}")
    check = IO.inspect_file("drawing", fmt, raw, name=name, unit=p.get("unit") or None)
    job.step(0.7, "Checked the file")
    IO._require_valid(check)
    params = {"drawing": text(p.get("drawing"), "drawing", 80), "format": fmt, "data": p["data"], "name": name}
    if p.get("unit"):
        params["unit"] = p["unit"]
    return _commit(app, job, "draw.import", params)


def export_model(app: Any, job: Job, p: Mapping[str, Any]) -> Any:
    fmt = choice(p.get("format", "glb"), IO.MODEL_EXPORT, "format")
    ids = p.get("ids")
    if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, str) for i in ids)):
        raise BadRequest("ids must be a list of element ids")
    job.step(0.1, "Copying the model")
    snap = _snapshot(app, job)
    job.step(0.3, f"Writing {fmt.upper()}")
    data, report = IO.export_model(snap, fmt, ids or None)
    job.file = (data, f"{_slug(snap.name)}.{IO.EXTENSION[fmt]}", IO.MEDIA[fmt])
    return {"report": report}


def export_drawing(app: Any, job: Job, p: Mapping[str, Any]) -> Any:
    fmt = choice(p.get("format", "svg"), IO.DRAWING_EXPORT, "format")
    job.step(0.1, "Copying the drawing")
    with app.lock:
        project = _project(app, job)
        view = project.view(text(p.get("view"), "view", 80))
        doc = project.drawing(view["id"]).copy(fresh_id=False)
    job.step(0.3, f"Writing {fmt.upper()}")
    data, report = IO.export_drawing(doc, fmt, name=view["name"])
    job.file = (data, f"{_slug(view['name'])}.{fmt}", IO.MEDIA[fmt])
    return {"report": report}


def make_report(app: Any, job: Job, p: Mapping[str, Any]) -> Any:
    job.step(0.1, "Collecting the project's data")
    return _commit(app, job, "report.create", {k: p[k] for k in ("name", "sections") if k in p})


def check(app: Any, job: Job, p: Mapping[str, Any]) -> Any:
    job.step(0.1, "Copying the project")
    snap = _snapshot(app, job)
    job.step(0.3, "Checking the model")
    return snap.check()


def rebuild(app: Any, job: Job, p: Mapping[str, Any]) -> Any:
    job.step(0.1, "Rebuilding every element from its parameters")
    return _commit(app, job, "model.rebuild", {})


def simulate_beam(app: Any, job: Job, p: Mapping[str, Any]) -> Any:
    from . import simulation_bridge as SB
    job.step(0.1, "Copying the project")
    snap = _snapshot(app, job)
    job.step(0.3, "Running the simulation")
    return SB.simulate_beam(snap, text(p.get("id"), "id", 80), p)


def optimize_beam(app: Any, job: Job, p: Mapping[str, Any]) -> Any:
    from . import optimization_bridge as OB
    job.step(0.1, "Copying the project")
    snap = _snapshot(app, job)
    job.step(0.3, "Searching for the smallest size that meets the limit")
    return OB.optimize_beam(snap, text(p.get("id"), "id", 80), p)


RUNNERS: dict[str, tuple[str, Runner]] = {
    "import.model": ("Import into the model", import_model), "import.drawing": ("Import into the drawing", import_drawing),
    "export.model": ("Export the model", export_model), "export.drawing": ("Export the drawing", export_drawing),
    "report": ("Make a project report", make_report), "check": ("Check the model", check), "rebuild": ("Rebuild the model", rebuild),
    "simulate.beam": ("Simulate the beam", simulate_beam), "optimize.beam": ("Optimise the beam", optimize_beam),
}


def _slug(name: str) -> str:
    from .project import slug
    return slug(name)


# ------------------------------------------------------------------------------------------------------------------------------ manager
class JobManager:
    def __init__(self, app: Any, diagnostics: Diagnostics) -> None:
        self.app = app
        self.diagnostics = diagnostics
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(self, kind: str, params: Mapping[str, Any]) -> Job:
        if kind not in RUNNERS:
            raise BadRequest(f"'{kind}' is not a background operation", details={"kinds": sorted(RUNNERS)})
        if not isinstance(params, dict):
            raise BadRequest("params must be an object")
        with self.app.lock:
            project = self.app.require()
            project_id = project.manifest["id"]
        with self._lock:
            if sum(1 for j in self._jobs.values() if j.status in ("queued", "running")) >= MAX_RUNNING:
                raise LimitExceeded(f"{MAX_RUNNING} operations are already running", hint="Wait for one to finish, or cancel it")
            job = Job(new_id(), kind, RUNNERS[kind][0], project_id)
            self._jobs[job.id] = job
            self._trim()
        threading.Thread(target=self._run, args=(job, dict(params)), name=f"spadra-job-{kind}", daemon=True).start()
        return job

    def _run(self, job: Job, params: dict[str, Any]) -> None:
        job.status, job.started = "running", time.time()
        job.message = "Starting"
        try:
            job.result = RUNNERS[job.kind][1](self.app, job, params)
            job.status, job.progress, job.message = "done", 1.0, "Finished"
        except Cancelled:
            job.status, job.message = "cancelled", "Cancelled; nothing was changed"
        except AppError as exc:
            self.diagnostics.record(f"job:{job.kind}", exc, job=job.id)      # recorded first: a caller that sees "failed" can already read the entry
            job.error, job.message, job.status = describe(exc), exc.message, "failed"
        except Exception as exc:                                   # noqa: BLE001 - a job must always end in a state the interface can show
            self.diagnostics.record(f"job:{job.kind}", exc, job=job.id)
            job.error, job.message, job.status = describe(exc), "Something went wrong inside SPADRA", "failed"
        finally:
            job.finished = time.time()
            if job.file is not None and job.status != "done":
                job.file = None

    def _trim(self) -> None:
        done = sorted((j for j in self._jobs.values() if j.status in ("done", "failed", "cancelled")), key=lambda j: j.finished or 0)
        for j in done[:-KEEP_FINISHED] if len(done) > KEEP_FINISHED else ():
            del self._jobs[j.id]
        held = sorted((j for j in self._jobs.values() if j.file is not None), key=lambda j: j.finished or 0)
        while held and sum(len(j.file[0]) for j in held if j.file) > MAX_FILE_BYTES_KEPT:
            held.pop(0).file = None

    def get(self, job_id: Any) -> Job:
        job = self._jobs.get(text(job_id, "id", 80))
        if job is None:
            raise NotFound("That operation does not exist (it may have been dismissed)")
        return job

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [j.public() for j in sorted(self._jobs.values(), key=lambda j: j.created, reverse=True)]

    def cancel(self, job_id: Any) -> Job:
        job = self.get(job_id)
        if job.status not in ("queued", "running"):
            raise Conflict(f"It has already {job.status if job.status != 'done' else 'finished'}")
        if job._committing:
            raise Conflict("It is already being applied to the project and can no longer be cancelled", hint="Use Undo to take the change back")
        job._cancel.set()
        return job

    def dismiss(self, job_id: Any) -> None:
        job = self.get(job_id)
        if job.status in ("queued", "running"):
            raise Conflict("It is still running; cancel it first")
        with self._lock:
            self._jobs.pop(job.id, None)
