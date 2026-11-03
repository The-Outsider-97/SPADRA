"""What went wrong, in words a person can act on, and a log for whoever has to find out why.

Every failure has a **category** (what kind of problem it is), a **recovery** (what to do next) and its own message and hint. The category and the
recovery are decided here once and served to the interface in the start-up data, so the screen never has its own copy of the table.

Failures of commands and background operations are written to ``diagnostics.log`` in SPADRA's data folder as one JSON line each: when, where, the
error's code and message, the project's name and revision. Never the content of the project, the files imported, or anything typed. The log is
rotated at ``MAX_BYTES``; ``recent`` returns the latest lines so the interface can show them. Writing it can never fail an operation."""
from __future__ import annotations

import json
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..tools.errors import AppError

MAX_BYTES = 512 * 1024
CATEGORIES: dict[str, dict[str, str]] = {
    "bad_request": {"category": "Input", "recovery": "Check what was entered or chosen and try again."},
    "not_found": {"category": "Missing", "recovery": "It may have been deleted or renamed. Refresh the browser and choose it again."},
    "conflict": {"category": "Conflict", "recovery": "Something else needs to change first; the message says what."},
    "limit_exceeded": {"category": "Limit", "recovery": "Use a smaller file or fewer items, or split the work."},
    "dependency_missing": {"category": "Not installed", "recovery": "This needs a library or SLAI agent that is not installed here. Nothing was changed."},
    "storage_error": {"category": "Disk", "recovery": "Check that the folder exists, has room and can be written to, then try again."},
    "forbidden": {"category": "Not allowed", "recovery": "This was refused by the application."},
    "unauthorized": {"category": "Signed out", "recovery": "Reopen SPADRA from the link it printed when it started."},
    "spadra_backup_damaged": {"category": "Damaged file", "recovery": "Choose an older backup."},
    "spadra_plan_limit": {"category": "Plan limit", "recovery": "The active resource plan does not allow this; see the plans file."},
    "spadra_feature_unavailable": {"category": "Not installed", "recovery": "This needs a SLAI agent that is not available here."},
    "revision_conflict": {"category": "Conflict", "recovery": "The project changed since you looked at it. Refresh and repeat the change."},
    "error": {"category": "Unexpected", "recovery": "Nothing was changed. Try again; if it keeps happening, the details are in the diagnostics log."},
}
FALLBACK = CATEGORIES["error"]


def classify(code: str) -> dict[str, str]:
    """The category and recovery for an error code (a storage code such as ``storage_disk_full`` is a disk problem)."""
    if code in CATEGORIES:
        return CATEGORIES[code]
    if code.startswith("storage_"):
        return CATEGORIES["storage_error"]
    return FALLBACK


def describe(exc: BaseException) -> dict[str, Any]:
    """An exception as the interface shows it: code, category, message, the error's own hint (or the category's recovery), and details."""
    if isinstance(exc, AppError):
        info = classify(exc.code)
        return {"code": exc.code, "category": info["category"], "message": exc.message, "recovery": exc.hint or info["recovery"], "details": exc.details}
    return {"code": "error", "category": FALLBACK["category"], "message": "Something went wrong inside SPADRA.", "recovery": FALLBACK["recovery"], "details": {}}


class Diagnostics:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._lock = threading.Lock()

    def record(self, source: str, exc: BaseException, **context: Any) -> None:
        """Log one failure. ``source`` says where (``command:model.add_wall``, ``job:import.model``). Never raises."""
        try:
            if self.path is None:
                return
            info = describe(exc)
            line: dict[str, Any] = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": source, "code": info["code"], "category": info["category"],
                                    "message": info["message"] if isinstance(exc, AppError) else f"{type(exc).__name__}: {exc}", **{k: v for k, v in context.items() if v is not None}}
            if not isinstance(exc, AppError):
                line["trace"] = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-2000:]
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.is_file() and self.path.stat().st_size > MAX_BYTES:
                    self.path.replace(self.path.with_suffix(".log.1"))
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
        except Exception:                                      # noqa: BLE001 - a log must never turn one failure into two
            pass

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        try:
            with self._lock:
                lines = self.path.read_text(encoding="utf-8").splitlines() if self.path and self.path.is_file() else []
        except OSError:
            return out
        for raw in lines[-limit:]:
            try:
                out.append(json.loads(raw))
            except ValueError:
                continue
        return out[::-1]
