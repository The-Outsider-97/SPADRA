"""SPADRA's errors. They extend the shared hierarchy in ``applications/tools/errors.py`` (never a parallel one), so the HTTP layer turns
each into the same ``{"ok": false, "error": {code, message, hint, details}}`` envelope every SLAI application uses, with the same status.

Raise the shared classes (``BadRequest``, ``NotFound``, ``Conflict`` …) for ordinary input problems. The classes here exist for conditions
that only SPADRA has, so the interface can tell them apart by ``code``.
"""
from __future__ import annotations

from ...tools.errors import AppError, BadRequest, DependencyMissing, LimitExceeded


class SpadraError(AppError):
    """Base of every error that is specific to SPADRA."""

    status, code = 500, "spadra_error"


class ConfigError(SpadraError):
    """``spadra_config.yaml`` or ``spadra_plans.yaml`` is unreadable or has a wrong value. The message names the file and the setting."""

    status, code = 500, "spadra_config_invalid"


class StartupError(SpadraError):
    """SPADRA cannot start or be built here: the web client is missing or will not build, or the environment cannot run it.
    ``hint`` is the command that fixes it; ``details`` carries what was observed (the last lines of a failed build, for one)."""

    status, code = 500, "spadra_startup_failed"


class PlanError(BadRequest):
    """A resource plan was asked for that the plans file does not define."""

    code = "spadra_plan_unknown"


class PlanLimitExceeded(LimitExceeded):
    """The active resource plan does not allow what was asked for. ``details`` carries the limit and the value."""

    code = "spadra_plan_limit"


class BackupDamaged(BadRequest):
    """A project backup does not match the hashes recorded when it was made, so it is refused rather than opened."""

    code = "spadra_backup_damaged"


class FeatureUnavailable(DependencyMissing):
    """A feature needs a SLAI agent or library that is not available in this installation. ``hint`` says which."""

    code = "spadra_feature_unavailable"


__all__ = ["SpadraError", "ConfigError", "StartupError", "PlanError", "PlanLimitExceeded", "BackupDamaged", "FeatureUnavailable"]
