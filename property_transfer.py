"""Copy, mirror and paste *properties* between elements that already exist. No element is created, moved or replaced.

What moves
    The source's configuration, in named groups the person chooses: ``general`` (description, type), ``dimensions`` (the parameters that shape the element,
    never where it is), ``construction`` (the layer stack), ``appearance`` (material, colour, conditional appearance), ``formulas`` (parameter expressions),
    ``classification`` and ``custom`` (instance properties), ``modifiers`` (the non-destructive chamfer and bend stack of a generic object, extrusion,
    sweep or family instance) and ``connections`` (a wall's attachment of its top to a roof or level and of its base to a floor; a pitched roof's end
    connections, eave by eave). Everything is copied as an independent configuration: there is no live dependency between the
    source and its targets afterwards.

What never moves
    The target's identity, its place (ends, outline, point, level, host, base offset), its group and link membership, its connections to neighbours, and
    anything shared by a whole type or family (type-level property sets, family type parameters): changing one target never changes another instance.
    What cannot be carried over faithfully is reported as *not transferred* rather than half-copied: modifiers that refer to other elements or to faces
    of the source (boolean, split, face edits), the wall a roof end connection was resolved against, and wall joins (they are computed from the walls
    that meet).

Mirror
    The configuration is reflected by the meaning of each property, not by negating numbers. Across the element's own length axis (``side``, the default:
    swaps the two faces) the layer order of a wall is reversed, signed offsets change sign, lean and ridge offsets change sign, near and far swap, and a
    door's swing side flips. Along it (``length``: left-handed to right-handed) the hinge end flips and the panels run in the opposite order. ``both`` does
    both. The target's world-space position and shape are untouched. A bend's angle changes sign when the faces are swapped. Connections keep their host
    (the same roof, level or floor); a roof's end connections swap eave when the roof is mirrored.

    The mirror may instead be given as a **reference plane**: a vertical plane through two points of the plan. Its direction against the source's own
    direction decides the axis: parallel to the element's length it swaps the faces (``side``), square to it it swaps the ends (``length``). An oblique
    plane is refused (it would turn the element, which this operation never does), and so is an element that has no direction of its own.

All-or-nothing per target
    A target that is not compatible is *skipped* with the reason; a target that fails while being changed is *failed*, put back exactly as it was, and
    named with the reason; the rest are done. With ``incompatible="refuse"`` an incompatible target refuses the whole request instead (the behaviour of the
    original Paste Attributes).
"""
from __future__ import annotations

import copy
import math
from typing import Any, Mapping, Sequence

from src.utils.spatial_formats.common.errors import SpatialFormatError

from ..tools.errors import BadRequest, NotFound
from ..tools.validation import new_id
from . import attachments as AT
from . import elements as E
from . import geometry as G
from . import layers as LY

GROUPS = ("general", "dimensions", "construction", "appearance", "formulas", "classification", "custom", "modifiers", "connections")
LABELS = {"general": "Description and type", "dimensions": "Dimensions and parameters", "construction": "Layers (composition and order)",
          "appearance": "Material, colour and appearance", "formulas": "Parameter formulas", "classification": "Classification", "custom": "Custom properties",
          "modifiers": "Modifiers (chamfer, bend)", "connections": "Connections (attachments, roof end connections)"}
AXES = ("side", "length", "both")
MAX_TARGETS = 500
PLANE_TOLERANCE_DEG = 1.0
MODIFIER_KINDS = ("sgeo.chamfer", "sgeo.bend")
MODIFIER_CATEGORIES = ("generic", "extrusion", "sweep")

_COMMON_DIMS = ("height", "width", "thickness", "pitch", "slope", "lean_enabled", "lean_angle", "frame_width", "frame_depth", "mullion_depth", "transom_depth", "overhang")
_OPENING_DIMS = ("sill", "door_type", "opening_angle", "depth_ref", "frame_depth_offset", "leaf_depth_offset", "hinge", "swing")
_ROOF_DIMS = ("roof", "ridge_offset")
_STRUCT_DIMS = ("profile", "depth")
_SIGNED = ("lean_angle", "ridge_offset", "frame_depth_offset", "leaf_depth_offset", "depth_offset")      # change sign when the two faces are swapped
NOT_TRANSFERRED = ("Place and host: ends, outline, point, level, host wall or roof, base offset",
                   "Identity, group and link membership",
                   "Wall joins and corners, and hosted openings (they follow the walls that meet and the wall that hosts them)",
                   "The specific wall a roof end connection was resolved against (it is found again from the walls under each eave)",
                   "Type- and family-level values: they belong to every instance of the type, so no single target may change them",
                   "Modifiers that refer to other elements or to faces of the source: boolean, split, face edits, weld and subdivision")


def _clone(value: Any) -> Any:
    return copy.deepcopy(value)


# ------------------------------------------------------------------------------------------------------------------------- reflection
def _flip(key: str, value: Any, axis: str) -> Any:
    """The value a property takes in the mirrored configuration (``axis`` is side, length or both)."""
    side, length = axis in ("side", "both"), axis in ("length", "both")
    if side:
        if key in _SIGNED and isinstance(value, (int, float)) and not isinstance(value, bool):
            return -value
        if key == "depth_ref":
            return {"near": "far", "far": "near"}.get(value, value)
        if key == "swing":
            return {"left": "right", "right": "left", 1: -1, -1: 1}.get(value, value)
    if length and key == "hinge":
        return {"start": "end", "end": "start"}.get(value, value)
    return value


def _flip_panels(panels: Sequence[Mapping[str, Any]], axis: str) -> list[dict[str, Any]]:
    out = [_clone(dict(q)) for q in panels]
    side, length = axis in ("side", "both"), axis in ("length", "both")
    for q in out:
        if side and "swing" in q:
            q["swing"] = -int(q.get("swing", 1))
        if length:
            swap = {"start": "end", "end": "start"}
            for k in ("hinge", "slide"):
                if k in q:
                    q[k] = swap.get(q[k], q[k])
    return list(reversed(out)) if length else out


# ------------------------------------------------------------------------------------------------------------------------- compatibility
def _family(project: Any, oid: str) -> Any:
    return project.model.get(oid).get("family_id")


def incompatibility(project: Any, source: str, dest: str, groups: set[str]) -> str | None:
    """Why ``dest`` cannot take ``source``'s properties, or ``None``. Nothing is changed here."""
    if dest == source:
        return "it is the source"
    if not E.alive(project, dest):
        return "it no longer exists"
    sc, dc = E.category(project, source), E.category(project, dest)
    if sc != dc:
        return f"it is a {dc}, the source is a {sc}"
    if sc == "structural" and E.is_column(E.meta_of(project, source)) != E.is_column(E.meta_of(project, dest)):
        return "a beam and a column do not share a configuration"
    if _family(project, source) != _family(project, dest):
        return "it belongs to a different family"
    if "construction" in groups and sc not in LY.LAYERED:
        return f"{sc} elements have no construction layers"
    return None


# ------------------------------------------------------------------------------------------------------------------------- the source, read once
class _Source:
    def __init__(self, project: Any, oid: str, groups: set[str], axis: str, mirror: bool) -> None:
        from . import relations as RL
        self.oid, self.groups, self.axis, self.mirror = oid, groups, axis, mirror
        self.category = E.category(project, oid)
        self.meta = E.meta_of(project, oid)
        props = E.properties(project, oid)
        self.rows = {r["key"]: r for section in ([{"rows": props["top"]}] + props["groups"]) for r in section["rows"] if r.get("key")}
        self.layers = LY.stack(project, oid) if self.category in LY.LAYERED else []
        self.family = _family(project, oid)
        self.custom = RL.custom_of(self.meta)
        self.custom_types = E.m(self.meta, "custom_types") or {}
        keys = list(_COMMON_DIMS)
        if self.category in E.OPENINGS:
            keys += _OPENING_DIMS
        elif self.category == "roof":
            keys += _ROOF_DIMS
        elif self.category == "structural":
            keys += _STRUCT_DIMS
        self.dim_keys = [k for k in dict.fromkeys(keys) if k not in ("length",)]
        mods = [m for m in project.model.modifiers() if m["target"]["item_id"] == oid]
        self.modifiers = [m for m in mods if m["kind"] in MODIFIER_KINDS and "subelement" not in m["target"] and not m.get("operands")]
        self.unsupported_modifiers = sorted({m["kind"].split(".")[-1] for m in mods if m not in self.modifiers})
        self.top_to, self.base_to = AT.attachment(self.meta), AT.base_attachment(self.meta)
        self.roof_ends = self._roof_ends()
        self.roof_ends_eaves = E.roof_eave_edges(self.meta) if self.roof_ends else []

    def _roof_ends(self) -> list[tuple[int, dict[str, Any]]]:
        """A pitched roof's end connections as (position among its eaves, settings), in eave order."""
        stored = E.m(self.meta, "roof_ends") or {}
        if self.category != "roof" or not stored or E.m(self.meta, "roof") not in ("gable", "shed"):
            return []
        eaves = E.roof_eave_edges(self.meta)
        return [(eaves.index(int(k)), dict(v)) for k, v in sorted(stored.items(), key=lambda kv: int(kv[0])) if int(k) in eaves]


def _validate_source(src: _Source) -> None:
    if "construction" in src.groups:
        for row in src.layers:
            if row["openings"] == "selected" and row["opening_ids"]:
                raise BadRequest("This construction references specific source openings; choose the destination's cuts first")


# ------------------------------------------------------------------------------------------------------------------------- one target
def _editable(project: Any, oid: str) -> set[str]:
    props = E.properties(project, oid)
    return {r["key"] for section in ([{"rows": props["top"]}] + props["groups"]) for r in section["rows"] if r.get("key") and r.get("type") != "readonly"}


def _apply(project: Any, src: _Source, dest: str) -> tuple[list[str], list[str]]:
    """Change ``dest`` to carry the source's configuration; also say which parameters it has no equivalent of. May raise; the caller puts the model back."""
    from . import parametric_properties as PP
    from . import relations as RL
    groups, changed, unavailable = src.groups, [], []
    mirror_side = src.mirror and src.axis in ("side", "both")
    layer_ids: dict[str, str] = {}
    if _family(project, dest) is not None:
        groups = groups & {"general", "appearance", "classification", "custom"}                # a family's parameters are its type's: not an instance's to rewrite
    if "construction" in groups:
        layers = []
        for row in src.layers:
            copied = _clone(row)
            copied["id"] = new_id()[:8]
            layer_ids[row["id"]] = copied["id"]
            copied.pop("offset", None)
            layers.append(copied)
        if mirror_side and src.category == "wall":
            layers.reverse()                                                                   # the interior face becomes the exterior face
        LY._store(project, dest, layers)
        E.regenerate(project, dest)
        changed.append("construction")
    if "general" in groups:
        for key in ("description", "type"):
            row = src.rows.get(key)
            if row and row.get("value") not in (None, ""):
                if key == "type" and row.get("type") != "choice":
                    continue
                E.set_property(project, dest, key, row["value"])
                changed.append(key)
    if "appearance" in groups:
        for key in ("material", "color"):
            if E.m(src.meta, key) is not None:
                if key == "material" and "construction" in groups:
                    continue                                                                   # the layer stack already carries the materials
                E.set_property(project, dest, key, E.m(src.meta, key))
                changed.append(key)
        for key in ("material_rule", "layer_material_rules"):
            if E.m(src.meta, key) is not None:
                rule = _clone(E.m(src.meta, key))
                if key == "layer_material_rules" and rule:
                    if not layer_ids:
                        target_layers = LY.stack(project, dest) if src.category in LY.LAYERED else []
                        if len(src.layers) != len(target_layers):
                            raise BadRequest("Copy the construction layers too before transferring layer-specific material rules")
                        layer_ids = {s["id"]: d["id"] for s, d in zip(src.layers, target_layers)}
                    rule = {layer_ids[k]: v for k, v in rule.items() if k in layer_ids}
                E._attach(project, dest, {key: rule})
                changed.append(key)
    if "dimensions" in groups:
        keys = ["roof"] + [k for k in src.dim_keys if k != "roof"] if "roof" in src.dim_keys else list(src.dim_keys)          # the roof type decides which parameters exist
        have = _editable(project, dest)
        for key in keys:
            if key == "thickness" and "construction" in groups:
                continue
            row = src.rows.get(key)
            if not (row and row.get("type") in ("number", "choice")):
                continue
            if key not in have:
                unavailable.append(key)                                                        # the target has no such parameter: said, not forced
                continue
            value = _flip(key, row["value"], src.axis) if src.mirror else row["value"]
            E.set_property(project, dest, key, value)
            changed.append(key)
            if key == "roof":
                have = _editable(project, dest)
        if src.category == "door" and E.has_panels(src.meta):
            panels = [dict(q) for q in E.door_panels(src.meta)]
            E._store_panels(project, dest, _flip_panels(panels, src.axis) if src.mirror else _clone(panels))
            E.regenerate(project, dest)
            changed.append("panels")
    if "classification" in groups:
        for key, row in src.rows.items():
            if (E.SF.is_field(key) or E.CL.is_field(key)) and row.get("value") is not None:
                E.set_property(project, dest, key, row["value"])
                changed.append(key)
    if "custom" in groups:
        for name, value in src.custom.items():
            RL.set_custom(project, dest, name, value, src.custom_types.get(name))
            changed.append(f"custom:{name}")
    if "formulas" in groups:
        formulas = dict(E.m(src.meta, "formulas") or {})
        if src.mirror and src.axis in ("side", "both"):
            formulas = {k: (f"-({str(v).lstrip('=').strip()})" if k in _SIGNED else v) for k, v in formulas.items()}      # the sign flips with the parameter it drives
        current = dict(E.m(E.meta_of(project, dest), "formulas") or {})
        current.update(formulas)
        PP._ordered(current, set(PP._context(project, dest)[1]))                                 # a reference the target cannot resolve refuses this target
        E._attach(project, dest, {"formulas": current})
        if current:
            project.manifest["has_property_formulas"] = True
            PP.recompute(project, dest)
        changed.extend(f"formula:{k}" for k in formulas)
    if "modifiers" in groups:
        _apply_modifiers(project, src, dest, changed, unavailable)
    if "connections" in groups:
        _apply_connections(project, src, dest, changed, unavailable)
    return changed, unavailable


def _apply_modifiers(project: Any, src: _Source, dest: str, changed: list[str], unavailable: list[str]) -> None:
    """Give ``dest`` the source's chamfer and bend modifiers (replacing its own of those kinds); a bend's angle changes sign when the faces are swapped."""
    unavailable.extend(f"modifier:{k} (it refers to other elements or to faces of the source)" for k in src.unsupported_modifiers)
    if not src.modifiers:
        return
    if E.category(project, dest) not in MODIFIER_CATEGORIES and not _family(project, dest):
        unavailable.append("modifiers (this element keeps its own construction and takes no modifier stack)")
        return
    for old in project.model.modifiers():
        if old["target"]["item_id"] == dest and old["kind"] in MODIFIER_KINDS and "subelement" not in old["target"] and not old.get("operands"):
            project.model.remove_modifier(old["id"])
    made = []
    for mod in src.modifiers:
        params = _clone(mod["parameters"])
        if src.mirror and src.axis in ("side", "both") and mod["kind"] == "sgeo.bend" and isinstance(params.get("angle"), (int, float)):
            params["angle"] = -params["angle"]                                                 # it bends the other way once the faces are swapped
        made.append(project.model.add_modifier(dest, kind=mod["kind"], parameters=params, evaluation_policy=_clone(mod.get("evaluation_policy")),
                                               enabled=mod.get("enabled", True)))
        changed.append(f"modifier:{mod['kind'].split('.')[-1]}")
    if src.mirror and src.axis == "length" and any(m["kind"] == "sgeo.bend" for m in src.modifiers):
        unavailable.append("bend span (measured from the element's own origin, so it is not turned end to end)")
    for item in project.model.evaluate_modifiers():
        if item["modifier_id"] in made and (item["status"] != "ok" or not item["result"] or item["result"].get("geometry") is None):
            raise BadRequest(f"The {item['modifier_id'][:6]} modifier does not fit this element: {item.get('error') or (item.get('diagnostics') or {}).get('reason') or 'it produced no geometry'}")


def _apply_connections(project: Any, src: _Source, dest: str, changed: list[str], unavailable: list[str]) -> None:
    """Attach ``dest`` to the same host as the source (roof, level or floor, with the same offset) and give a pitched roof the source's end connections."""
    if src.category in ("wall", "curtainwall"):
        if src.top_to:
            offset = float(src.top_to.get("offset", 0.0)) * 1000.0
            if src.top_to["kind"] == "roof":
                AT.attach_to_roof(project, [dest], src.top_to["roof"], offset)
            else:
                AT.extend_to_level(project, [dest], src.top_to["level"], offset)
            changed.append("top attachment")
        if src.base_to:
            AT.attach_base(project, [dest], src.base_to["floor"], float(src.base_to.get("offset", 0.0)) * 1000.0)
            changed.append("base attachment")
    elif src.category == "roof" and src.roof_ends:
        meta = E.meta_of(project, dest)
        if E.m(meta, "roof") not in ("gable", "shed"):
            unavailable.append("end connections (they are defined for gable and shed roofs)")
            return
        eaves = E.roof_eave_edges(meta)
        ends: dict[str, Any] = {}
        for position, cfg in src.roof_ends:
            at = (len(src.roof_ends_eaves) - 1 - position) if src.mirror and len(eaves) == len(src.roof_ends_eaves) else position
            if at >= len(eaves):
                unavailable.append(f"end connection on eave {position + 1} (this roof has {len(eaves)})")
                continue
            cfg.pop("wall", None)                                                              # found again from the walls under the eave
            ends[str(eaves[at])] = cfg
        E._attach(project, dest, {"roof_ends": ends or None})
        E.roof_end_cuts(project, E.meta_of(project, dest))                                      # refuses a cut that leaves nothing of the slope, before anything is built
        E.regenerate_roof(project, dest)
        from . import gutters as GU
        GU.follow(project, dest)
        changed.append("end connections")


# ------------------------------------------------------------------------------------------------------------------------- the operation
def _direction(project: Any, oid: str) -> tuple[float, float] | None:
    """The unit direction of an element's own length in the plan (a wall, curtain wall or beam from its ends, an opening from its host wall, a pitched
    roof along its ridge), or ``None`` when it has none."""
    meta = E.meta_of(project, oid)
    cat = E.m(meta, "category")
    if cat in E.OPENINGS and E.m(meta, "host") and E.alive(project, str(E.m(meta, "host"))):
        return _direction(project, str(E.m(meta, "host")))
    if cat == "roof" and E.m(meta, "roof") in ("gable", "shed") and E.m(meta, "boundary") and len(E.m(meta, "boundary")) == 4:
        try:
            return E._rectangle([list(q) for q in E.m(meta, "boundary")])[1]
        except BadRequest:
            return None
    start, end = E.m(meta, "start"), E.m(meta, "end")
    if start and end and G.norm(G.sub(end, start)) > 1e-9:
        return G.unit(G.sub(tuple(end), tuple(start)))
    return None


def axis_for_plane(project: Any, source: str, a: Sequence[float], b: Sequence[float]) -> str:
    """The mirror axis a vertical plane through plan points ``a`` and ``b`` means for ``source``: ``side`` when it runs along the element's length,
    ``length`` when it is square to it. Refuses an oblique plane and an element with no direction."""
    d = _direction(project, source)
    if d is None:
        raise BadRequest("This element has no length direction, so a reference plane cannot say which way to mirror it",
                         hint="Choose its sides, its length or both instead of a plane")
    line = G.sub((float(b[0]), float(b[1])), (float(a[0]), float(a[1])))
    if G.norm(line) < 1e-9:
        raise BadRequest("The two points of the reference plane must be apart")
    cross = abs(d[0] * line[1] - d[1] * line[0]) / G.norm(line)           # sin of the angle between the plane and the element
    if cross < math.sin(math.radians(PLANE_TOLERANCE_DEG)):
        return "side"
    if cross > math.cos(math.radians(PLANE_TOLERANCE_DEG)):
        return "length"
    raise BadRequest(f"The plane is {math.degrees(math.asin(min(1.0, cross))):.1f}° from the element's length: only a plane along it or square to it can be used",
                     hint="An oblique plane would turn the element, and this operation never changes where an element is or which way it points")


def transfer(project: Any, source: str, destinations: Sequence[str], groups: Sequence[str], *, mode: str = "copy", axis: str = "side",
             incompatible: str = "skip", plane: Sequence[Sequence[float]] | None = None) -> dict[str, Any]:
    """Apply ``source``'s configuration to every compatible destination. Returns what was applied, skipped and failed (each with its reason)."""
    if mode not in ("copy", "mirror"):
        raise BadRequest("mode must be copy or mirror")
    if axis not in AXES:
        raise BadRequest("axis must be side, length or both")
    if incompatible not in ("skip", "refuse"):
        raise BadRequest("incompatible must be skip or refuse")
    if plane is not None:
        if mode != "mirror":
            raise BadRequest("A reference plane is for Mirror Properties")
        if not E.alive(project, source):
            raise NotFound(f"Source element {source} does not exist")
        axis = axis_for_plane(project, source, plane[0], plane[1])
    chosen = set(groups or ())
    if not chosen or chosen - set(GROUPS):
        raise BadRequest("Choose one or more supported property groups", hint=", ".join(GROUPS))
    if not E.alive(project, source):
        raise NotFound(f"Source element {source} does not exist")
    targets = list(dict.fromkeys(destinations or ()))
    if not targets or len(targets) > MAX_TARGETS:
        raise BadRequest(f"Select between 1 and {MAX_TARGETS} destination elements")
    if source in targets:
        raise BadRequest("A source cannot also be a destination")
    src = _Source(project, source, chosen, axis, mode == "mirror")
    _validate_source(src)
    names = {t: _name(project, t) for t in targets if E.alive(project, t)}
    applicable: list[str] = []
    skipped: list[dict[str, str]] = []
    for dest in targets:
        why = incompatibility(project, source, dest, chosen)
        if why is None:
            applicable.append(dest)
        elif incompatible == "refuse":
            raise BadRequest(f"{dest} is not a compatible {src.category} destination: {why}")
        else:
            skipped.append({"id": dest, "name": names.get(dest, dest), "reason": why})
    start = project.model.copy(fresh_id=False)                                                  # one picture of the model to put a failing target back from
    manifest_start = _clone(project.manifest)
    transferred: dict[str, list[str]] = {}
    unavailable: dict[str, list[str]] = {}
    failed: list[dict[str, str]] = []
    for dest in applicable:
        try:
            transferred[dest], unavailable[dest] = _apply(project, src, dest)
        except (BadRequest, NotFound, G.GeometryError, SpatialFormatError) as exc:
            if incompatible == "refuse":
                raise
            failed.append({"id": dest, "name": names.get(dest, dest), "reason": str(exc)})
            project.model, project.manifest = start, _clone(manifest_start)                      # that target exactly as it was: then the ones that worked again
            project._cache.clear()
            done = [d for d in transferred]
            transferred, unavailable = {}, {}
            start = project.model.copy(fresh_id=False)
            for d in done:
                transferred[d], unavailable[d] = _apply(project, src, d)
    return {"source": source, "category": src.category, "mode": mode, "axis": axis if mode == "mirror" else None, "by_plane": plane is not None, "groups": sorted(chosen),
            "destinations": transferred, "applied": [{"id": d, "name": names.get(d, d), "changed": c, "no_equivalent": unavailable.get(d, [])} for d, c in transferred.items()],
            "skipped": skipped, "failed": failed, "not_transferred": list(NOT_TRANSFERRED)}


def _name(project: Any, oid: str) -> str:
    try:
        return project.model.get(oid)["name"] or E.category(project, oid).title()
    except Exception:
        return oid


def describe(project: Any, source: str) -> dict[str, Any]:
    """What can be transferred from ``source``: the groups that have something in them, for the Paste Properties dialog."""
    if not E.alive(project, source):
        raise NotFound(f"Element {source} does not exist")
    src = _Source(project, source, set(GROUPS), "side", False)
    has = {
        "general": any(src.rows.get(k) and src.rows[k].get("value") not in (None, "") for k in ("description", "type")),
        "dimensions": any(src.rows.get(k) and src.rows[k].get("type") in ("number", "choice") for k in src.dim_keys),
        "construction": bool(src.layers) and src.category in LY.LAYERED,
        "appearance": any(E.m(src.meta, k) is not None for k in ("material", "color", "material_rule", "layer_material_rules")),
        "formulas": bool(E.m(src.meta, "formulas")),
        "classification": any((E.SF.is_field(k) or E.CL.is_field(k)) and r.get("value") not in (None, "") for k, r in src.rows.items()),
        "custom": bool(src.custom),
        "modifiers": bool(src.modifiers),
        "connections": bool(src.top_to or src.base_to or src.roof_ends),
    }
    return {"source": source, "category": src.category, "groups": [{"id": g, "label": LABELS[g], "has_values": has[g]} for g in GROUPS],
            "axes": list(AXES), "has_direction": _direction(project, source) is not None, "mirrors": src.category in ("wall", "floor", "roof", "door", "window", "structural", "curtainwall"),
            "not_transferred": list(NOT_TRANSFERRED)}
