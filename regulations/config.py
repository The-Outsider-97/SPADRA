"""A project's regulation settings, kept in its manifest (``manifest["regulation"]``).

Off by default: a project that never turned regulation assistance on has no ``regulation`` entry, is never evaluated, and shows nothing about it. Turning it on,
changing a setting and changing the pinned dataset are commands (``regulation.configure``): undoable, revision-fenced and recorded in the project's history.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Mapping

from ...tools.errors import BadRequest
from . import datasets as DS

PROJECT_STATUSES = ("new_build", "renovation", "existing")
STATUS_LABELS = {"new_build": "New build", "renovation": "Renovation / alteration", "existing": "Existing building"}
DEFAULTS: dict[str, Any] = {"enabled": False, "jurisdiction": None, "project_status": None, "building_function": None, "reference_date": None,
                            "warning_margin": 0.05, "dataset": None, "accept_unverified": False}
MAX_MARGIN = 0.5
_ID = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,59}$")


def current(project: Any) -> dict[str, Any]:
    """The project's settings with every key present (``DEFAULTS`` where the project says nothing)."""
    raw = project.manifest.get("regulation")
    out = dict(DEFAULTS)
    if isinstance(raw, Mapping):
        out.update({k: raw[k] for k in DEFAULTS if k in raw})
    return out


def _date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise BadRequest("reference_date must be a date (YYYY-MM-DD)")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise BadRequest("reference_date must be a date (YYYY-MM-DD)") from exc
    return value


def normalise(project: Any, patch: Mapping[str, Any], *, root: Any = None) -> dict[str, Any]:
    """The settings after applying ``patch`` to the project's current ones. Raises ``BadRequest`` (with a sentence a person can act on) for anything invalid.

    The dataset is *pinned* by hash whenever the jurisdiction or reference date is set or changes, or when ``repin`` is true, so the same project gives the same
    answer later. An explicit ``dataset`` pin must name an installed dataset."""
    unknown = set(patch) - set(DEFAULTS) - {"repin"}
    if unknown:
        raise BadRequest(f"Unknown regulation setting(s): {', '.join(sorted(unknown))}")
    cfg = current(project)
    before = dict(cfg)
    if "enabled" in patch:
        if not isinstance(patch["enabled"], bool):
            raise BadRequest("enabled must be true or false")
        cfg["enabled"] = patch["enabled"]
    if "jurisdiction" in patch:
        j = patch["jurisdiction"]
        if j is not None and j not in DS.JURISDICTIONS:
            raise BadRequest(f"jurisdiction must be one of {', '.join(DS.JURISDICTIONS)}")
        cfg["jurisdiction"] = j
    if "project_status" in patch:
        s = patch["project_status"]
        if s is not None and s not in PROJECT_STATUSES:
            raise BadRequest(f"project_status must be one of {', '.join(PROJECT_STATUSES)}")
        cfg["project_status"] = s
    if "reference_date" in patch:
        cfg["reference_date"] = _date(patch["reference_date"])
    if "building_function" in patch:
        f = patch["building_function"]
        if f is not None and (not isinstance(f, str) or not _ID.match(f)):
            raise BadRequest("building_function must be one of the dataset's function ids")
        cfg["building_function"] = f
    if "warning_margin" in patch:
        w = patch["warning_margin"]
        if isinstance(w, bool) or not isinstance(w, (int, float)) or not 0 <= float(w) <= MAX_MARGIN:
            raise BadRequest(f"warning_margin must be a number from 0 to {MAX_MARGIN} (a fraction of the limit: 0.05 means within 5%)")
        cfg["warning_margin"] = float(w)
    if "accept_unverified" in patch:
        if not isinstance(patch["accept_unverified"], bool):
            raise BadRequest("accept_unverified must be true or false")
        cfg["accept_unverified"] = patch["accept_unverified"]
    if cfg["enabled"] and not cfg["jurisdiction"]:
        raise BadRequest("Choose a jurisdiction before turning regulation assistance on")
    if "dataset" in patch:
        pin = patch["dataset"]
        if pin is None:
            cfg["dataset"] = None
        else:
            if not isinstance(pin, Mapping) or not isinstance(pin.get("sha256"), str):
                raise BadRequest("dataset must be {id, version, sha256} of an installed dataset")
            hit = [d for d in DS.load_all(root) if d.sha256 == pin["sha256"] and (not cfg["jurisdiction"] or d.jurisdiction == cfg["jurisdiction"])]
            if not hit:
                raise BadRequest("That dataset is not installed for this jurisdiction")
            cfg["dataset"] = hit[0].pin()
    elif patch.get("repin") or cfg["jurisdiction"] != before["jurisdiction"] or cfg["reference_date"] != before["reference_date"] or (cfg["enabled"] and not cfg["dataset"]):
        cfg["dataset"] = None
        if cfg["jurisdiction"]:
            ds, _ = DS.select(cfg["jurisdiction"], cfg["reference_date"], root=root)
            cfg["dataset"] = ds.pin() if ds is not None else None
    if cfg["building_function"] and cfg["dataset"]:
        ds, _ = DS.select(cfg["jurisdiction"], cfg["reference_date"], pin=cfg["dataset"], root=root)
        if ds is not None and ds.functions() and cfg["building_function"] not in {f["id"] for f in ds.functions()}:
            raise BadRequest(f"'{cfg['building_function']}' is not a building function of {ds.id} {ds.version}")
    return cfg


def store(project: Any, cfg: Mapping[str, Any]) -> None:
    """Write the settings. A project that has never enabled regulation and still has the defaults keeps no entry in its manifest."""
    if dict(cfg) == DEFAULTS:
        project.manifest.pop("regulation", None)
    else:
        project.manifest["regulation"] = dict(cfg)
