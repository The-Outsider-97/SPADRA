"""Profiler errors. They derive from SLAI's shared ``AppError`` so the HTTP layer reports them with the usual envelope."""
from __future__ import annotations

from ...tools.errors import BadRequest, Conflict, NotFound


class ProfilerError(BadRequest):
    """An operation was refused; the document is exactly as it was. ``details`` carries diagnostics a person can act on."""
    code = "profiler_error"


class ProfilerConflict(Conflict):
    """The constraints cannot all hold (or an anchor/lock forbids the edit). ``details["conflicting"]`` names the constraints."""
    code = "profiler_conflict"


class ProfilerNotFound(NotFound):
    code = "profiler_not_found"
