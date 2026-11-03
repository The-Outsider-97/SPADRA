"""Loads SPADRA's two configuration files and applies the chosen resource plan.

    configs/spadra_config.yaml   the ``app:`` section: port, request size, undo depth, scene size, recent projects, backups kept, plan name
    configs/spadra_plans.yaml    named resource plans: the largest value each setting may take under that plan

Both are read through SLAI's shared ``ConfigRepository`` (via ``applications/tools/config.py``); this module adds nothing that
duplicates it. Every value is checked and a wrong one is an error that names the file and the setting: nothing is silently replaced.
When the SLAI checkout is not available the declared defaults and the built-in ``standard`` plan are used and the caller is told.

A plan only ever *lowers* a setting: the value the application uses is ``min(configured value, plan limit)``. Plans are resource
profiles (memory and disk use); they are not licences, prices or feature switches.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from ...tools.config import Field, load_document, load_section
from .spadra_errors import ConfigError, PlanError

HERE = Path(__file__).resolve().parents[1]
CONFIG_DIR = HERE / "configs"
CONFIG_PATH = CONFIG_DIR / "spadra_config.yaml"
LEGACY_CONFIG_PATH = CONFIG_DIR / "config.yaml"        # the name used before spadra_config.yaml; read only when the new file is absent
PLANS_PATH = CONFIG_DIR / "spadra_plans.yaml"
SCHEMA_VERSION = 1
DEFAULT_PLAN = "standard"
DEFAULT_PORT = 8770

SETTINGS_FIELDS: dict[str, Field] = {
    "port": Field(DEFAULT_PORT, 1, 65535),
    "max_request_bytes": Field(4_194_304, 16_384, 64 * 1024 * 1024),
    "undo_depth": Field(100, 1, 10_000),
    "max_scene_vertices": Field(400_000, 1_000, 20_000_000),
    "recent_projects": Field(12, 0, 100),
    "backups_kept": Field(5, 1, 50),
    "plan": Field(DEFAULT_PLAN, kind=str),
}
LIMITED = tuple(k for k in SETTINGS_FIELDS if k not in ("port", "plan"))        # the settings a plan may lower
_PLAN_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


@dataclass(frozen=True)
class Plan:
    name: str
    display_name: str
    description: str
    limits: Mapping[str, int]
    source: str                                         # the plans file, or "built-in"

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "display_name": self.display_name, "description": self.description, "limits": dict(self.limits), "source": self.source}


@dataclass
class Loaded:
    values: dict[str, Any]                              # every SETTINGS_FIELDS key, with plan limits applied
    plan: Plan
    notes: list[str] = field(default_factory=list)      # what a person should know: defaults used, values a plan lowered


def builtin_plan() -> Plan:
    """The plan used when there is no plans file: every setting may take any value its own range allows."""
    return Plan(DEFAULT_PLAN, "Standard", "No plans file: only each setting's own range applies.",
                {k: int(SETTINGS_FIELDS[k].high) for k in LIMITED}, "built-in")        # type: ignore[arg-type]


def config_file(explicit: str | Path | None = None) -> Path:
    if explicit:
        return Path(explicit)
    return CONFIG_PATH if CONFIG_PATH.is_file() or not LEGACY_CONFIG_PATH.is_file() else LEGACY_CONFIG_PATH


def _int_limit(path: Path, plan: str, key: str, value: Any) -> int:
    spec = SETTINGS_FIELDS[key]
    if type(value) is not int or value < spec.low or value > spec.high:                 # type: ignore[operator]
        raise ConfigError(f"Invalid {path.name}: plan '{plan}' limit {key} must be a whole number from {spec.low:g} to {spec.high:g}",
                          details={"file": str(path), "plan": plan, "setting": key})
    return value


def load_plans(path: Path | None = PLANS_PATH) -> tuple[dict[str, Plan], list[str]]:
    """Every plan in the file, validated, and notes. A missing file gives the built-in ``standard`` plan alone."""
    if path is None or not path.is_file():
        return {DEFAULT_PLAN: builtin_plan()}, ([f"No plans file at {path}; the built-in standard plan applies."] if path else [])
    try:
        data, note = load_document(path)
    except ValueError as exc:
        raise ConfigError(f"{path.name} could not be read: {exc}", details={"file": str(path)}) from exc
    notes = [note] if note else []
    if not data and note:                                # the repository is unavailable: fall back, as the config file does
        return {DEFAULT_PLAN: builtin_plan()}, notes
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ConfigError(f"{path.name}: schema_version must be {SCHEMA_VERSION}", details={"file": str(path)})
    block = data.get("plans")
    if not isinstance(block, dict) or not block:
        raise ConfigError(f"{path.name}: 'plans' must be a mapping with at least one plan", details={"file": str(path)})
    plans: dict[str, Plan] = {}
    for name, body in block.items():
        if not isinstance(name, str) or not _PLAN_NAME.match(name):
            raise ConfigError(f"{path.name}: plan names are lower-case letters, digits, '-' or '_' (got {name!r})", details={"file": str(path)})
        if not isinstance(body, dict):
            raise ConfigError(f"{path.name}: plan '{name}' must be a mapping", details={"file": str(path), "plan": name})
        limits = body.get("limits")
        if not isinstance(limits, dict):
            raise ConfigError(f"{path.name}: plan '{name}' needs a 'limits' mapping", details={"file": str(path), "plan": name})
        unknown = sorted(set(limits) - set(LIMITED))
        if unknown:
            raise ConfigError(f"{path.name}: plan '{name}' limits unknown setting(s): {', '.join(map(str, unknown))}",
                              details={"file": str(path), "plan": name, "known": list(LIMITED)})
        checked = {k: _int_limit(path, name, k, limits[k]) if k in limits else int(SETTINGS_FIELDS[k].high) for k in LIMITED}     # type: ignore[arg-type]
        plans[name] = Plan(name, str(body.get("display_name") or name.title()), str(body.get("description") or ""), checked, str(path))
    return plans, notes


def apply_plan(values: Mapping[str, Any], plan: Plan) -> tuple[dict[str, Any], list[str]]:
    """``values`` with each limited setting lowered to the plan's limit when it is above it, and a sentence for each one lowered."""
    out, notes = dict(values), []
    for key in LIMITED:
        if out[key] > plan.limits[key]:
            notes.append(f"Plan '{plan.name}' lowered {key} from {out[key]:,} to {plan.limits[key]:,}.")
            out[key] = plan.limits[key]
    return out, notes


def load_settings_values(config: str | Path | None = None, *, plans: str | Path | None = None, port: int | None = None,
                         plan: str | None = None) -> Loaded:
    """Read ``spadra_config.yaml``, choose the plan (``plan`` argument, else the file's ``plan``), and apply it."""
    path = config_file(config)
    try:
        values, note = load_section(path, "app", SETTINGS_FIELDS, label=f"SPADRA configuration ({path.name})",
                                    overrides={"port": port, "plan": plan})
    except ValueError as exc:
        raise ConfigError(str(exc), details={"file": str(path)}) from exc
    notes = [note] if note else []
    plans_path = Path(plans) if plans else PLANS_PATH
    known, plan_notes = load_plans(plans_path)
    notes += plan_notes
    chosen = values["plan"]
    if chosen not in known:
        raise PlanError(f"The plan '{chosen}' is not defined in {plans_path.name}", hint=f"Choose one of: {', '.join(sorted(known))}",
                        details={"plan": chosen, "known": sorted(known)})
    final, lowered = apply_plan(values, known[chosen])
    return Loaded(final, known[chosen], notes + lowered)


__all__ = ["SETTINGS_FIELDS", "LIMITED", "Plan", "Loaded", "builtin_plan", "config_file", "load_plans", "apply_plan", "load_settings_values",
           "CONFIG_PATH", "PLANS_PATH", "DEFAULT_PLAN", "DEFAULT_PORT"]
