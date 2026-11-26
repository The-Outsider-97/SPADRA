"""Facts: what the model says about an element, in the form a rule reads it.

A fact is derived from the project's own parameters (never guessed): ``door.clear_width_m`` is the opening width less the face width of the frame on both sides.
What a fact does *not* account for is stated with it (``assumptions``) and shown on every finding that uses it. A fact that cannot be derived is absent, which the
engine reports as "insufficient data".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .. import elements as E

NS = E.NS
DOOR_ASSUMPTIONS = (
    "Clear width is the opening width less the frame's face width at both jambs; the leaf's own thickness, hinges, handles and any threshold are not deducted.",
    "Clear height is the opening height less the frame head, measured from the bottom of the opening (the door's base height), not from the floor of the room.",
)
CATEGORIES_SUPPORTED = ("door",)


@dataclass
class ElementFacts:
    id: str
    category: str
    name: str
    level: str | None
    level_name: str | None
    values: dict[str, Any] = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)
    missing: dict[str, str] = field(default_factory=dict)


class Index:
    """One pass over the model's metadata, so evaluating a thousand doors is not a thousand scans. Read-only: nothing here is changed."""

    def __init__(self, project: Any) -> None:
        self.project = project
        self.meta: dict[str, dict[str, Any]] = {}
        self.names: dict[str, str] = {}
        self.profile_of: dict[str, list[str]] = {}
        raw = getattr(project.model, "_model", None)
        if raw is not None and hasattr(raw, "objects") and hasattr(raw, "_mutation_lock"):
            with raw._mutation_lock:
                for obj in raw.objects:
                    self.meta[obj["id"]] = obj.get("metadata") or {}
                    self.names[obj["id"]] = str(obj.get("name") or "")
        else:                                                              # a document without the fast path: slower, same answers
            for oid in E.element_ids(project):
                self.meta[oid] = E.meta_of(project, oid)
                try:
                    self.names[oid] = str(project.model.get(oid)["name"] or "")
                except Exception:                                          # noqa: BLE001
                    self.names[oid] = ""
        for oid, meta in self.meta.items():
            if meta.get(f"{NS}:category") == "opening_profile" and isinstance(meta.get(f"{NS}:opening"), str):
                self.profile_of.setdefault(meta[f"{NS}:opening"], []).append(oid)

    def ids(self, category: str) -> list[str]:
        key = f"{NS}:category"
        return [oid for oid, meta in self.meta.items() if meta.get(key) == category]

    def level_name(self, level_id: str | None) -> str | None:
        if not level_id:
            return None
        try:
            return str(self.project.level(level_id)["name"])
        except Exception:                                                  # noqa: BLE001
            return None


def _num(meta: Mapping[str, Any], key: str) -> float | None:
    v = meta.get(f"{NS}:{key}")
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def door_facts(idx: Index, oid: str) -> ElementFacts:
    meta = idx.meta[oid]
    host = meta.get(f"{NS}:host")
    host_meta = idx.meta.get(host) if isinstance(host, str) else None
    level = (host_meta or meta).get(f"{NS}:level")
    f = ElementFacts(oid, "door", idx.names.get(oid) or "Door", level if isinstance(level, str) else None, idx.level_name(level if isinstance(level, str) else None))
    width, height = _num(meta, "width"), _num(meta, "height")
    if width is None or width <= 0:
        f.missing["door.clear_width_m"] = "the door has no valid width parameter"
    if height is None or height <= 0:
        f.missing["door.clear_height_m"] = "the door has no valid height parameter"
    profiles = idx.profile_of.get(oid, [])
    if len(profiles) > 1:
        f.missing["door.clear_width_m"] = f.missing["door.clear_height_m"] = "the door has more than one frame profile, so its clear opening is ambiguous"
        return f
    inset = 0.0
    if profiles:
        inset_v = _num(idx.meta[profiles[0]], "face_width")
        if inset_v is None or inset_v < 0:
            f.missing["door.clear_width_m"] = f.missing["door.clear_height_m"] = "the frame has no valid face width"
            return f
        inset = inset_v
    f.values["door.has_frame"] = bool(profiles)
    f.values["door.frame_inset_m"] = inset
    if width is not None and width > 0:
        f.values["door.width_m"] = width
        clear = width - 2 * inset
        if clear <= 0:
            f.missing["door.clear_width_m"] = "the frame leaves no opening"
        else:
            f.values["door.clear_width_m"] = clear
    if height is not None and height > 0:
        f.values["door.height_m"] = height
        clear_h = height - inset
        if clear_h <= 0:
            f.missing["door.clear_height_m"] = "the frame leaves no opening"
        else:
            f.values["door.clear_height_m"] = clear_h
    f.assumptions.extend(DOOR_ASSUMPTIONS)
    return f


def project_facts(cfg: Mapping[str, Any]) -> dict[str, Any]:
    """The facts that belong to the project, from its settings. Unset settings are absent, never defaulted."""
    out: dict[str, Any] = {}
    if cfg.get("building_function"):
        out["building.function"] = cfg["building_function"]
    if cfg.get("project_status"):
        out["project.status"] = cfg["project_status"]
    if cfg.get("reference_date"):
        out["project.reference_date"] = cfg["reference_date"]
    return out


def for_category(idx: Index, category: str) -> list[ElementFacts]:
    if category == "door":
        return [door_facts(idx, oid) for oid in idx.ids("door")]
    return []
