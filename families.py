"""Parametric families and generic model elements, built on SGEO's own family mechanism (no second family format).

SGEO already provides what a BIM family needs: a family definition with typed parameters (length, angle, number, integer, boolean), bounds,
derived parameters written as safe formulas, and an allow-listed generator (box, cylinder, sphere); instances that override parameters; and
atomic regeneration of every instance when the definition changes. SPADRA adds only what SGEO cannot know: where an instance stands (its
level, point, rotation and height above the level) and what the person sees.

Vocabulary used in the application:

* **Type parameters** are the family's definition: default, lower and upper bound, formula. Editing them changes every instance.
* **Instance parameters** are the overrides one placed element carries. A parameter with no override follows the family default.
* A parameter with a **formula** is derived (``depth`` = ``width / 2``): it cannot be overridden, it follows the parameters it names.
* **Constraints** are the bounds, which SGEO enforces on every instance; a change that would break one is refused, naming the element.

Formulas are written in metres, radians and plain numbers (SGEO evaluates them in its own units and checks dimensions: ``width / 2`` is a length,
``length(shelves * 0.4)`` turns a count into one); the application shows and edits lengths in
millimetres and angles in degrees and converts at the edge. Nothing in a family is executable."""
from __future__ import annotations

import math
import re
from copy import deepcopy
from typing import Any, Mapping, Sequence

from src.utils.spatial_formats.common import units as U
from src.utils.spatial_formats.sgeo import families as SG
from src.utils.spatial_formats.sgeo import parameters as SP

from ..tools.errors import BadRequest, Conflict, NotFound
from ..tools.validation import number, text
from . import elements as E

SHAPES: dict[str, tuple[str, tuple[str, ...]]] = {
    "box": ("builtin.box_v1", ("width", "depth", "height")),
    "cylinder": ("builtin.cylinder_v1", ("radius", "height")),
    "sphere": ("builtin.sphere_v1", ("radius",)),
}
GENERATOR_SHAPE = {gen: shape for shape, (gen, _) in SHAPES.items()}
# The types a person can give a parameter: SGEO's own list (its typed-parameter contract), the original five first.
TYPES = ("length", "angle", "number", "integer", "boolean") + tuple(sorted(t for t in SP.PARAMETER_TYPES if t not in ("length", "angle", "number", "integer", "boolean")))
DATA_TYPES = SP.DATA_TYPES
# Shown and entered in these units (internal values are SI; SGEO's unit table converts). Lengths are mm and angles degrees as always.
DISPLAY_UNIT = {"length": "mm", "angle": "deg", "area": "m2", "volume": "m3", "mass": "kg", "time": "s", "frequency": "Hz", "angular_velocity": "rad/s",
                "velocity": "m/s", "acceleration": "m/s2", "force": "kN", "pressure": "MPa", "energy": "kJ", "power": "kW", "density": "kg/m3",
                "second_moment": "mm4", "temperature": "C", "temperature_difference": "K", "number": "", "integer": ""}
UNIT_OF = {k: ("°" if v == "deg" else v) for k, v in DISPLAY_UNIT.items()}
UNIT_OF.update({k: "" for k in ("boolean",) + tuple(DATA_TYPES)})
LABELS = {"width": "Width", "depth": "Depth", "height": "Height", "radius": "Radius"}
MIN_SIZE = 0.001                         # metres: a generator's size parameters must stay above 1 mm
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,39}$")
MAX_FAMILIES = 200


# ------------------------------------------------------------------------------------------------------------------- units
def to_si(kind: str, value: Any, label: str) -> Any:
    """A value as the person entered it (mm, degrees, kN, MPa, °C...) to the internal SI value, through SGEO's unit table."""
    if kind == "boolean":
        if not isinstance(value, bool):
            raise BadRequest(f"{label} must be true or false")
        return value
    if kind in DATA_TYPES:
        return value
    v = number(value, label, -1e12, 1e12)
    if kind == "length":
        return v / 1000.0
    if kind == "angle":
        return math.radians(v)
    if kind in ("number", "integer"):
        return int(v) if kind == "integer" and float(v).is_integer() else v
    return U.to_si(kind, v, DISPLAY_UNIT[kind])


def from_si(kind: str, value: Any) -> Any:
    if kind == "boolean" or kind in DATA_TYPES or value is None:
        return value
    if kind == "length":
        return round(value * 1000.0, 6)
    if kind == "angle":
        return round(math.degrees(value), 6)
    if kind in ("number", "integer"):
        return value
    return round(U.from_si(kind, value, DISPLAY_UNIT[kind]), 9)


def _convert_in(spec: Mapping[str, Any], value: Any, label: str) -> Any:
    """``to_si`` for a whole parameter: vectors and coordinates convert each component by their declared dimension."""
    kind = spec["type"]
    if kind in ("vector", "coordinate") and isinstance(value, (list, tuple)):
        dim = spec.get("dimension", "length" if kind == "coordinate" else "number")
        return [to_si(dim, c, label) if isinstance(c, (int, float)) and not isinstance(c, bool) else c for c in value]
    return to_si(kind, value, label)


def _convert_out(spec: Mapping[str, Any], value: Any) -> Any:
    kind = spec["type"]
    if kind in ("vector", "coordinate") and isinstance(value, (list, tuple)):
        dim = spec.get("dimension", "length" if kind == "coordinate" else "number")
        return [from_si(dim, c) for c in value]
    return from_si(kind, value)


# ------------------------------------------------------------------------------------------------------------------ access
def families(project: Any) -> list[dict[str, Any]]:
    """The model's family definitions (SGEO's copies, without the instance count SGEO adds)."""
    return project.model.families()


def family(project: Any, family_id: Any) -> dict[str, Any]:
    fid = text(family_id, "family", 80)
    for f in families(project):
        if f["id"] == fid:
            return f
    raise NotFound(f"Family {fid} does not exist")


def shape_of(fam: Mapping[str, Any]) -> str:
    return GENERATOR_SHAPE[fam["generator"]["kind"]]


def instances(project: Any, family_id: str) -> list[str]:
    from .project import all_ids
    return list(all_ids(project.model, family_id=family_id))


def _natural(name: str) -> list[Any]:
    """Sort key putting 'D-800' before 'D-1000' (JSON objects do not keep insertion order, so types are always listed in this order)."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def _shown(value: Any, unit: str = "") -> str:
    """A parameter value as text for a read-only row."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, list):
        return ", ".join(f"{c:g}" for c in value if isinstance(c, (int, float))) + (f" {unit}" if unit else "")
    if isinstance(value, (int, float)):
        return f"{value:g} {unit}".strip()
    return str(value)


def type_usage(project: Any, family_id: str) -> dict[str, int]:
    """How many placed elements use each named type of a family (an element without a type is not counted)."""
    use: dict[str, int] = {}
    for oid in instances(project, family_id):
        t = project.model.get(oid).get("family_type")
        if t is not None:
            use[t] = use.get(t, 0) + 1
    return use


def describe(project: Any, fam: Mapping[str, Any]) -> dict[str, Any]:
    """A family for the person: parameters in the units they edit in, its named types, its dependency graph, and how many elements use it."""
    required = SHAPES[shape_of(fam)][1]
    graph = SP.ParameterGraph(fam["parameters"])
    rows = []
    for name, spec in fam["parameters"].items():
        kind = spec["type"]
        rows.append({"name": name, "label": LABELS.get(name, name), "type": kind, "unit": UNIT_OF[kind], "required": name in required,
                     "default": _convert_out(spec, spec.get("default")), "min": from_si(kind, spec.get("min")), "max": from_si(kind, spec.get("max")),
                     "expression": spec.get("expression"), "description": spec.get("description", ""),
                     "scope": "derived" if "expression" in spec else spec.get("scope", "instance"), "read_only": bool(spec.get("read_only")) or "expression" in spec,
                     "choices": spec.get("choices"), "size": spec.get("size"), "dimension": spec.get("dimension"),
                     "depends_on": sorted(graph.dependencies[name]), "dependents": sorted(graph.transitive_dependents({name}))})
    use = type_usage(project, fam["id"])
    types = [{"name": t, "values": {n: _convert_out(fam["parameters"][n], v) for n, v in vals.items()}, "instances": use.get(t, 0)}
             for t, vals in sorted((fam.get("types") or {}).items(), key=lambda kv: _natural(kv[0]))]
    return {"id": fam["id"], "name": fam["name"], "shape": shape_of(fam), "parameters": rows, "types": types,
            "graph": {"order": list(graph.order), "edges": [list(e) for e in graph.edges()]},
            "instances": fam.get("instance_count", len(instances(project, fam["id"])))}


def listing(project: Any) -> list[dict[str, Any]]:
    return [describe(project, f) for f in families(project)]


# ---------------------------------------------------------------------------------------------------------------- definitions
def _unique_name(project: Any, name: Any, ignore: str | None = None) -> str:
    name = text(name, "name", 80)
    if any(f["name"].lower() == name.lower() and f["id"] != ignore for f in families(project)):
        raise Conflict(f"A family named '{name}' already exists", hint="Choose another name")
    return name


def _spec(name: str, raw: Mapping[str, Any], *, required: bool, base: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """One SGEO parameter specification from what the person gave (lengths in mm, angles in degrees); ``base`` is the existing spec to amend."""
    if not _NAME.match(name):
        raise BadRequest(f"'{name}' is not a usable parameter name", hint="Start with a letter; letters, digits and _ only (up to 40)")
    kind = raw.get("type", base["type"] if base else "length")
    if kind not in TYPES:
        raise BadRequest(f"The type of {name} must be one of {', '.join(TYPES)}")
    if required and kind != "length":
        raise BadRequest(f"{LABELS.get(name, name)} is a size of the shape, so it must be a length")

    spec: dict[str, Any] = {"type": kind}
    if base is not None and base["type"] == kind:
        spec.update({k: v for k, v in base.items() if k in ("default", "min", "max", "expression", "description", "read_only", "scope", "choices", "size", "dimension")})
    for key in ("scope", "choices", "size", "dimension"):
        if raw.get(key) is not None:
            spec[key] = raw[key]
        elif key in raw:
            spec.pop(key, None)
    if spec.get("scope") not in (None, "type", "instance"):
        raise BadRequest(f"The scope of {name} must be 'type' or 'instance'")
    if spec.get("scope") == "instance":
        spec.pop("scope")                                                      # the default; keeps the stored definition minimal
    if raw.get("description") is not None:
        spec["description"] = text(raw["description"], "description", 200, empty=True)
    if "expression" in raw:
        if raw["expression"] in (None, ""):
            spec.pop("expression", None)
            if "default" not in spec and "default" not in raw:
                raise BadRequest(f"{name} needs a default value once its formula is removed")
        else:
            spec["expression"] = text(raw["expression"], "formula", 200)
            spec.pop("default", None)
    if "default" in raw and raw["default"] is not None and "expression" not in spec:
        spec["default"] = _convert_in(spec, raw["default"], f"default of {name}")
    elif "default" in raw and raw["default"] is None and kind in ("material_ref", "element_ref", "geometry_ref"):
        spec["default"] = None
    if "expression" not in spec and "default" not in spec:
        raise BadRequest(f"{name} needs a default value or a formula")
    if kind != "boolean" and kind not in DATA_TYPES:
        for bound in ("min", "max"):
            if bound in raw:
                if raw[bound] in (None, ""):
                    spec.pop(bound, None)
                else:
                    spec[bound] = to_si(kind, raw[bound], f"{bound} of {name}")
        if required:
            spec["min"] = max(MIN_SIZE, spec.get("min", MIN_SIZE))
    return spec


def _check_generates(fam: Mapping[str, Any]) -> None:
    from src.utils.spatial_formats.sgeo.families import generate_geometry
    try:
        generate_geometry(dict(fam))
    except Exception as exc:
        raise BadRequest(f"The family is not valid: {exc}", hint="Every size needs a positive value inside its bounds") from exc


def define(project: Any, p: Mapping[str, Any]) -> str:
    if len(families(project)) >= MAX_FAMILIES:
        raise BadRequest(f"A project holds at most {MAX_FAMILIES} families")
    name = _unique_name(project, p.get("name"))
    shape = p.get("shape", "box")
    if shape not in SHAPES:
        raise BadRequest(f"shape must be one of {', '.join(SHAPES)}")
    generator, required = SHAPES[shape]
    raw = p.get("parameters")
    if not isinstance(raw, Mapping) or not raw:
        raise BadRequest("Give the family's parameters, at least its sizes")
    missing = [n for n in required if n not in raw]
    if missing:
        raise BadRequest(f"A {shape} family needs {', '.join(required)}; missing {', '.join(missing)}")
    specs = {n: _spec(str(n), r if isinstance(r, Mapping) else {"default": r}, required=n in required) for n, r in raw.items()}
    fam = {"id": "00000000-0000-4000-8000-000000000000", "name": name, "parameters": specs, "generator": {"kind": generator}}
    _check_generates(fam)
    try:
        return project.model.define_family(name, specs, generator)
    except Exception as exc:
        raise BadRequest(f"The family was refused: {exc}") from exc


def update(project: Any, p: Mapping[str, Any]) -> dict[str, Any]:
    """Change a family's name and/or parameters (add, amend or ``remove``) and regenerate every element made from it, all or nothing."""
    fam = family(project, p.get("id"))
    fam.pop("instance_count", None)
    required = SHAPES[shape_of(fam)][1]
    if p.get("name") is not None:
        fam["name"] = _unique_name(project, p["name"], ignore=fam["id"])
    changes = p.get("parameters") or {}
    if not isinstance(changes, Mapping):
        raise BadRequest("parameters must map a name to its new definition")
    for name, raw in changes.items():
        base = fam["parameters"].get(name)
        fam["parameters"][name] = _spec(str(name), raw if isinstance(raw, Mapping) else {"default": raw}, required=name in required, base=base)
    for name in p.get("remove") or []:
        if name in required:
            raise BadRequest(f"{LABELS.get(name, name)} is a size of the shape and cannot be removed")
        if name not in fam["parameters"]:
            raise NotFound(f"The family has no parameter '{name}'")
        del fam["parameters"][name]
    _check_generates(fam)
    from src.utils.spatial_formats.sgeo.families import resolve_parameters
    for oid in instances(project, fam["id"]):                       # name the element that would break, which SGEO's own message does not
        obj = project.model.get(oid)
        try:
            resolve_parameters(fam, obj.get("parameters", {}))
        except Exception as exc:
            raise BadRequest(f"{obj['name']} would no longer fit the family: {exc}", hint="Change its own parameters first, or widen the bounds") from exc
    try:
        project.model.update_family(fam)
    except Exception as exc:
        raise BadRequest(f"The family was refused: {exc}") from exc
    regenerate_many(project, instances(project, fam["id"]))
    return {"id": fam["id"], "instances": len(instances(project, fam["id"]))}


def delete(project: Any, family_id: Any) -> None:
    fam = family(project, family_id)
    n = len(instances(project, fam["id"]))
    if n:
        raise Conflict(f"{n} element{'s' if n != 1 else ''} {'are' if n != 1 else 'is'} made from {fam['name']}", hint="Delete them first")
    project.model.remove_family(fam["id"])


# ---------------------------------------------------------------------------------------------------------------- instances
def values(project: Any, oid: str) -> dict[str, Any]:
    """The fully resolved parameter values of an element (SGEO's: defaults, overrides and formulas)."""
    return project.model.parameter_values(oid)


def height_of(fam: Mapping[str, Any], resolved: Mapping[str, Any]) -> float:
    shape = shape_of(fam)
    return float(resolved["height"]) if shape in ("box", "cylinder") else 2.0 * float(resolved["radius"])


def _placement(project: Any, oid: str) -> list[float]:
    """Where the element stands: SGEO generated the solid around the origin, so it is put at its point, on its level, above its base offset."""
    obj = project.model.get(oid)
    meta, fam = obj["metadata"], family(project, obj["family_id"])
    z = E.level_elevation(project, E.m(meta, "level")) + float(E.m(meta, "base_offset", 0.0)) + height_of(fam, values(project, oid)) / 2.0
    x, y = E.m(meta, "point")
    return [float(v) for v in E._rot(float(E.m(meta, "rotation", 0.0)), x, y, z)]


def regenerate(project: Any, oid: str) -> None:
    """Place the element."""
    project.model.set_transform(oid, _placement(project, oid))


BATCH = 500                      # SGEO accepts up to 1000 operations in one transaction


def regenerate_many(project: Any, oids: Sequence[str]) -> None:
    """Place several elements in as few SGEO transactions as possible. Each transaction validates the whole scene once, so placing N instances one by one costs N
    validations of N instances; a batch costs one. All the placements are worked out first, so a failure while working them out changes nothing; the caller's command
    rolls the model back if a later batch is refused."""
    oids = list(oids)
    moves = [{"op": "transform", "id": oid, "value": _placement(project, oid)} for oid in oids]
    for start in range(0, len(moves), BATCH):
        project.model.apply(moves[start:start + BATCH])


def place(project: Any, p: Mapping[str, Any], to_m: float) -> str:
    fam = family(project, p.get("family"))
    level = project.level(text(p.get("level"), "level", 80))
    q = p.get("point")
    if not isinstance(q, (list, tuple)) or len(q) != 2:
        raise BadRequest("point must be [x, y]")
    point = [float(number(q[0], "x", -1e9, 1e9)) * to_m, float(number(q[1], "y", -1e9, 1e9)) * to_m]
    overrides = _overrides(fam, p.get("parameters") or {})
    material = E._choice(p.get("material", "Timber"), E.MATERIALS, "material")
    name = text(p.get("name") or fam["name"], "name", 80)
    type_name = text(p["type"], "type", 80) if p.get("type") not in (None, "") else None
    if type_name is not None and type_name not in (fam.get("types") or {}):
        raise NotFound(f"{fam['name']} has no type named '{type_name}'", hint="Define the type in the family first")
    base_offset = number(p.get("base_offset", 0.0), "base_offset", -100000, 100000) / 1000.0
    rotation = math.radians(number(p.get("rotation", 0.0), "rotation", -360, 360))
    meta = {f"{E.NS}:{k}": v for k, v in {"category": "generic", "level": level["id"], "point": point, "base_offset": base_offset, "rotation": rotation, "material": material}.items()}
    try:
        # One SGEO transaction, so the scene is validated once for this placement, not three times (instantiate, metadata, transform).
        resolved = SG.resolve_parameters(fam, overrides, type_name)
        z = E.level_elevation(project, level["id"]) + base_offset + height_of(fam, resolved) / 2.0
        oid = project.model.instantiate(fam["id"], overrides, name=name, family_type=type_name, metadata=meta,
                                        transform=[float(v) for v in E._rot(rotation, point[0], point[1], z)])
    except Exception as exc:
        raise BadRequest(f"{fam['name']} cannot be placed with these values: {exc}") from exc
    return oid


def _overrides(fam: Mapping[str, Any], raw: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, value in raw.items():
        spec = fam["parameters"].get(name)
        if spec is None:
            raise BadRequest(f"{fam['name']} has no parameter '{name}'")
        if "expression" in spec or spec.get("read_only"):
            raise BadRequest(f"{LABELS.get(name, name)} follows a formula in {fam['name']}; change the formula in the family")
        if spec.get("scope") == "type":
            raise BadRequest(f"{LABELS.get(name, name)} is a type parameter of {fam['name']}; change it in the family type, not on one element")
        out[name] = _bounded(name, spec, _convert_in(spec, value, LABELS.get(name, name)))
    return out


def _bounded(name: str, spec: Mapping[str, Any], v: Any) -> Any:
    kind = spec["type"]
    if kind == "boolean" or kind in DATA_TYPES:
        return v
    lo, hi = spec.get("min", MIN_SIZE if kind == "length" else None), spec.get("max")
    shown = lambda x: f"{from_si(kind, x):g}{(' ' + UNIT_OF[kind]) if UNIT_OF[kind] else ''}"            # noqa: E731
    if lo is not None and v < lo - 1e-12 or hi is not None and v > hi + 1e-12:
        span = " and ".join(([f"at least {shown(lo)}"] if lo is not None else []) + ([f"at most {shown(hi)}"] if hi is not None else []))
        raise BadRequest(f"{LABELS.get(name, name)} must be {span}")
    return v


def set_instance_parameter(project: Any, oid: str, name: str, value: Any) -> None:
    """Override one parameter of an element, or give it back to the family when ``value`` is empty."""
    obj = project.model.get(oid)
    fam = family(project, obj["family_id"])
    spec = fam["parameters"].get(name)
    if spec is None:
        raise BadRequest(f"{fam['name']} has no parameter '{name}'")
    current = dict(obj.get("parameters", {}))
    if value in (None, ""):
        current.pop(name, None)
    else:
        current.update(_overrides(fam, {name: value}))
    try:
        project.model.set_parameters(oid, current)
    except Exception as exc:
        raise BadRequest(f"That value does not fit {fam['name']}: {exc}") from exc
    regenerate(project, oid)


def set_property(project: Any, oid: str, meta: Mapping[str, Any], key: str, value: Any) -> dict[str, Any]:
    """The editable properties of a placed element besides its parameters (those go through ``set_instance_parameter``)."""
    if key in ("x", "y"):
        pt = list(E.m(meta, "point"))
        pt[0 if key == "x" else 1] = number(value, key, -1e6, 1e6) / 1000.0
        return {"point": pt}
    if key == "rotation":
        return {"rotation": math.radians(number(value, key, -360, 360))}
    if key == "base_offset":
        return {"base_offset": number(value, key, -100000, 100000) / 1000.0}
    if key == "level":
        return {"level": project.level(text(value, "level", 80))["id"]}
    if key == "material":
        return {"material": E._choice(value, E.MATERIALS, "material")}
    raise BadRequest(f"'{key}' is not an editable property of this element")


def plan_item(project: Any, oid: str, f: float) -> dict[str, Any]:
    obj = project.model.get(oid)
    meta, fam = obj["metadata"], family(project, obj["family_id"])
    v = values(project, oid)
    x, y = E.m(meta, "point")
    shape = shape_of(fam)
    if shape == "box":
        c, s = math.cos(float(E.m(meta, "rotation", 0.0))), math.sin(float(E.m(meta, "rotation", 0.0)))
        w, d = float(v["width"]) / 2, float(v["depth"]) / 2
        poly = [[(x + c * dx - s * dy) * f, (y + s * dx + c * dy) * f] for dx, dy in ((-w, -d), (w, -d), (w, d), (-w, d))]
    else:
        r = float(v["radius"])
        poly = [[(x + r * math.cos(a)) * f, (y + r * math.sin(a)) * f] for a in (2 * math.pi * i / 32 for i in range(32))]
    return {"id": oid, "category": "generic", "name": obj["name"], "polygon": poly}


def move(project: Any, oid: str, meta: Mapping[str, Any], dx: float, dy: float, dz: float) -> None:
    x, y = E.m(meta, "point")
    E._attach(project, oid, {"point": [x + dx, y + dy], "base_offset": float(E.m(meta, "base_offset", 0.0)) + dz})


def properties(project: Any, oid: str, out: dict[str, Any], volume: dict[str, Any]) -> dict[str, Any]:
    obj = project.model.get(oid)
    meta, fam = obj["metadata"], family(project, obj["family_id"])
    info = describe(project, fam)
    resolved = values(project, oid)
    overrides = obj.get("parameters", {})
    mm = lambda v: round(v * 1000, 6)                                                            # noqa: E731
    x, y = E.m(meta, "point")
    rows, type_rows = [], []
    for row in info["parameters"]:
        kind, name = row["type"], row["name"]
        value = from_si(kind, resolved[name])
        limits = " – ".join(f"{b:g}" for b in (row["min"], row["max"]) if b is not None)
        shown_type = obj.get("family_type")
        if row["expression"]:
            rows.append({"label": f"{row['label']} (formula)", "type": "readonly", "value": _shown(value, row["unit"]) + f"  = {row['expression']}"})
        elif kind in DATA_TYPES and kind != "enumeration":
            rows.append({"label": row["label"], "type": "readonly", "value": _shown(value)})
        elif row["scope"] == "type":
            rows.append({"label": f"{row['label']} (type{(' ' + shown_type) if shown_type else ''})", "type": "readonly", "value": _shown(value, row["unit"])})
        else:
            label = row["label"] + ("" if name in overrides else f" ({'type ' + shown_type if shown_type and name in (fam.get('types') or {}).get(shown_type, {}) else 'family'})")
            if kind == "enumeration":
                rows.append({"key": f"param:{name}", "label": label, "type": "choice", "value": resolved[name], "choices": [{"value": c, "label": c} for c in row["choices"]]})
            elif kind == "boolean":
                rows.append({"key": f"param:{name}", "label": label, "type": "choice", "value": "true" if resolved[name] else "false", "choices": [{"value": "true", "label": "Yes"}, {"value": "false", "label": "No"}]})
            else:
                rows.append({"key": f"param:{name}", "label": label, "type": "number", **({"unit": row["unit"]} if row["unit"] else {}), "value": value})
        type_rows.append({"label": row["label"], "type": "readonly",
                          "value": (f"= {row['expression']}" if row["expression"] else _shown(row["default"], row["unit"])) + (f"   [{limits} {row['unit']}]".rstrip() if limits and not row["expression"] else "")})
    type_row = ([{"key": "family_type", "label": "Family Type", "type": "choice", "value": obj.get("family_type") or "",
                  "choices": [{"value": "", "label": "(family defaults)"}] + [{"value": t, "label": t} for t in sorted(fam.get("types") or {}, key=_natural)]}]
                if fam.get("types") else [])
    out["top"] = [{"label": "Type", "type": "readonly", "value": fam["name"]}] + type_row + [
                  {"key": "level", "label": "Level", "type": "choice", "value": E.m(meta, "level"), "choices": E._levels(project)},
                  {"key": "material", "label": "Material", "type": "choice", "value": E.m(meta, "material", "Timber"), "choices": E._choices(E.MATERIALS)}] + rows
    out["groups"] = [
        {"title": "Geometry", "rows": [{"key": "x", "label": "Position X", "type": "number", "unit": "mm", "value": mm(x)},
                                        {"key": "y", "label": "Position Y", "type": "number", "unit": "mm", "value": mm(y)},
                                        {"key": "rotation", "label": "Rotation", "type": "number", "unit": "°", "value": round(math.degrees(float(E.m(meta, "rotation", 0.0))), 6)}, volume]},
        {"title": "Constraints", "rows": [{"key": "base_offset", "label": "Base Offset", "type": "number", "unit": "mm", "value": mm(float(E.m(meta, "base_offset", 0.0)))}]},
        {"title": f"Type parameters ({fam['name']})", "rows": type_rows + [{"label": "Used by", "type": "readonly", "value": f"{info['instances']} element(s); edit in Families"}]}]
    return out


def export_family_file(project: Any, family_id: Any) -> bytes:
    """One family as a native ``.sgeo`` document of kind ``family`` (the format's own family file, not a new format)."""
    from src.utils.sgeo import SGEODocument
    fam = family(project, family_id)
    fam.pop("instance_count", None)
    doc = SGEODocument.new(fam["name"], unit="m", document_kind="family")
    doc.define_family(fam["name"], deepcopy(fam["parameters"]), fam["generator"]["kind"], family_id=fam["id"], types=deepcopy(fam.get("types")))
    doc.validate().raise_for_errors()
    return doc.dumps()


def import_family_file(project: Any, source: Any) -> list[dict[str, Any]]:
    """Add the families of a ``.sgeo`` family file together with their named types. A family that is already here unchanged (same id and content) is
    not added twice; a name already used by another family gets a numeric suffix. A file written by an older SGEO is upgraded in memory by SGEO itself
    (the original is never rewritten). Returns one row per family: ``{id, name, types, status}`` with status ``added``, ``renamed`` or ``present``."""
    out: list[dict[str, Any]] = []
    have = {f["id"]: f for f in families(project)}
    for fam in source.families():
        fam = {k: v for k, v in fam.items() if k != "instance_count"}
        existing = have.get(fam["id"])
        if existing is not None and {k: v for k, v in existing.items() if k != "instance_count"} == fam:
            out.append({"id": fam["id"], "name": fam["name"], "types": len(fam.get("types") or {}), "status": "present"})
            continue
        name, n = fam["name"], 1
        while any(f["name"].lower() == name.lower() for f in families(project)):
            n += 1
            name = f"{fam['name']} {n}"
        new_id = project.model.define_family(name, deepcopy(fam["parameters"]), fam["generator"]["kind"], types=deepcopy(fam.get("types")),
                                             family_id=fam["id"] if fam["id"] not in have else None)
        have[new_id] = {"id": new_id}
        out.append({"id": new_id, "name": name, "types": len(fam.get("types") or {}), "status": "added" if name == fam["name"] else "renamed"})
    return out


# ------------------------------------------------------------------------------------------------------------------ named types
def _type_name(value: Any) -> str:
    name = text(value, "type name", 80)
    if name != name.strip():
        raise BadRequest("A type name cannot start or end with a space")
    return name


def _type_values(fam: Mapping[str, Any], raw: Any) -> dict[str, Any]:
    """The parameter values a type fixes, entered in the units the person uses; derived and read-only parameters cannot be set by a type."""
    if not isinstance(raw, Mapping):
        raise BadRequest("values must map a parameter name to its value for the type")
    out: dict[str, Any] = {}
    for name, value in raw.items():
        spec = fam["parameters"].get(name)
        if spec is None:
            raise BadRequest(f"{fam['name']} has no parameter '{name}'")
        if "expression" in spec or spec.get("read_only"):
            raise BadRequest(f"{LABELS.get(name, name)} follows a formula in {fam['name']}, so a type cannot set it")
        out[name] = _bounded(name, spec, _convert_in(spec, value, LABELS.get(name, name)))
    return out


def _commit_family(project: Any, fam: dict[str, Any], renames: Mapping[str, str] | None = None) -> None:
    """Replace the family in SGEO (every instance is rebuilt in the same revision, or nothing changes) and re-place the elements."""
    fam.pop("instance_count", None)
    if not fam.get("types"):
        fam.pop("types", None)
    _check_generates(fam)
    try:
        project.model.update_family(fam, type_renames=dict(renames) if renames else None)
    except Exception as exc:
        raise BadRequest(f"The change was refused: {exc}") from exc
    regenerate_many(project, instances(project, fam["id"]))


def _find_type(fam: Mapping[str, Any], name: str) -> str:
    for existing in (fam.get("types") or {}):
        if existing == name:
            return existing
    raise NotFound(f"{fam['name']} has no type named '{name}'")


def _free_type_name(fam: Mapping[str, Any], name: str, ignore: str | None = None) -> str:
    if any(t.lower() == name.lower() and t != ignore for t in (fam.get("types") or {})):
        raise Conflict(f"{fam['name']} already has a type named '{name}'", hint="Type names are not case-sensitive")
    return name


def type_define(project: Any, p: Mapping[str, Any]) -> dict[str, Any]:
    """Add a named type, optionally starting from another type's values (``copy_from``): the values it fixes are in ``values``."""
    fam = family(project, p.get("id"))
    name = _free_type_name(fam, _type_name(p.get("name")))
    fam.setdefault("types", {})
    values = {}
    if p.get("copy_from") not in (None, ""):
        values.update(fam["types"][_find_type(fam, text(p["copy_from"], "copy_from", 80))])
    values.update(_type_values(fam, p.get("values") or {}))
    fam["types"][name] = values
    _commit_family(project, fam)
    return {"id": fam["id"], "type": name}


def type_duplicate(project: Any, p: Mapping[str, Any]) -> dict[str, Any]:
    fam = family(project, p.get("id"))
    source = _find_type(fam, text(p.get("name"), "type name", 80))
    return type_define(project, {"id": fam["id"], "name": p.get("new_name") or f"{source} copy", "copy_from": source})


def type_update(project: Any, p: Mapping[str, Any]) -> dict[str, Any]:
    """Change what a type fixes. A value of ``null`` or ``""`` takes the parameter out of the type, so it follows the family default again."""
    fam = family(project, p.get("id"))
    name = _find_type(fam, text(p.get("name"), "type name", 80))
    changes = p.get("values") or {}
    if not isinstance(changes, Mapping):
        raise BadRequest("values must map a parameter name to its value")
    current = dict(fam["types"][name])
    for key in [k for k, v in changes.items() if v in (None, "")]:
        if key not in fam["parameters"]:
            raise NotFound(f"{fam['name']} has no parameter '{key}'")
        current.pop(key, None)
    current.update(_type_values(fam, {k: v for k, v in changes.items() if v not in (None, "")}))
    fam["types"][name] = current
    _commit_family(project, fam)
    return {"id": fam["id"], "type": name, "instances": type_usage(project, fam["id"]).get(name, 0)}


def type_rename(project: Any, p: Mapping[str, Any]) -> dict[str, Any]:
    """Rename a type; the elements that use it follow it in the same step."""
    fam = family(project, p.get("id"))
    old = _find_type(fam, text(p.get("name"), "type name", 80))
    new = _free_type_name(fam, _type_name(p.get("new_name")), ignore=old)
    if new != old:
        fam["types"] = {(new if k == old else k): v for k, v in fam["types"].items()}
        _commit_family(project, fam, renames={old: new})
    return {"id": fam["id"], "type": new}


def type_delete(project: Any, p: Mapping[str, Any]) -> dict[str, Any]:
    fam = family(project, p.get("id"))
    name = _find_type(fam, text(p.get("name"), "type name", 80))
    n = type_usage(project, fam["id"]).get(name, 0)
    if n:
        raise Conflict(f"{n} element{'s' if n != 1 else ''} {'use' if n != 1 else 'uses'} the type '{name}'",
                       hint="Give them another type (or none) first, then delete the type")
    del fam["types"][name]
    _commit_family(project, fam)
    return {"id": fam["id"], "removed": name}


def assign_type(project: Any, oid: str, type_name: Any) -> None:
    """Put an element on a named type (``""``/``None`` returns it to the family defaults). Its own overrides are kept."""
    obj = project.model.get(oid)
    fam = family(project, obj["family_id"])
    name = None if type_name in (None, "") else text(type_name, "type", 80)
    if name is not None and name not in (fam.get("types") or {}):
        raise NotFound(f"{fam['name']} has no type named '{name}'")
    try:
        project.model.set_family_type(oid, name)
    except Exception as exc:
        raise BadRequest(f"{obj['name']} cannot take that type: {exc}") from exc
    regenerate(project, oid)


def reset_overrides(project: Any, oid: str, names: Any = None) -> list[str]:
    """Give the element's own values back to its type (all of them, or the ``names`` listed). Returns what was reset."""
    obj = project.model.get(oid)
    current = dict(obj.get("parameters", {}))
    chosen = list(current) if names in (None, []) else [text(n, "parameter", 40) for n in names]
    reset = [n for n in chosen if n in current]
    for n in reset:
        del current[n]
    if reset:
        try:
            project.model.set_parameters(oid, current)
        except Exception as exc:
            raise BadRequest(f"The values could not be reset: {exc}") from exc
        regenerate(project, oid)
    return reset


# ------------------------------------------------------------------------------------------------------------- formula assistance
def check_formula(project: Any, p: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a formula for one parameter of a family and preview its value, changing nothing. The value is shown in the person's units."""
    fam = family(project, p.get("family"))
    name = text(p.get("parameter"), "parameter", 40)
    if not _NAME.match(name):
        raise BadRequest(f"'{name}' is not a usable parameter name")
    kind = p.get("type") or (fam["parameters"].get(name) or {}).get("type") or "number"
    if kind not in TYPES or kind in DATA_TYPES:
        raise BadRequest(f"A formula gives a number or a Boolean, not a {kind}")
    out = SP.check_formula(fam["parameters"], name, text(p.get("expression"), "formula", 200), type=kind)
    if out["ok"] and not isinstance(out["value"], bool):
        out["value"] = from_si(kind, out["value"])
    out["unit"] = UNIT_OF.get(kind, "")
    return out


def functions() -> list[dict[str, Any]]:
    """The functions and constants a formula may use, for the editor's autocomplete and help."""
    return SP.function_catalog()
