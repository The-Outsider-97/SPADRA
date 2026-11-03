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
from typing import Any, Mapping

from ..tools.errors import BadRequest, Conflict, NotFound
from ..tools.validation import number, text
from . import elements as E

SHAPES: dict[str, tuple[str, tuple[str, ...]]] = {
    "box": ("builtin.box_v1", ("width", "depth", "height")),
    "cylinder": ("builtin.cylinder_v1", ("radius", "height")),
    "sphere": ("builtin.sphere_v1", ("radius",)),
}
GENERATOR_SHAPE = {gen: shape for shape, (gen, _) in SHAPES.items()}
TYPES = ("length", "angle", "number", "integer", "boolean")
UNIT_OF = {"length": "mm", "angle": "°", "number": "", "integer": "", "boolean": ""}
LABELS = {"width": "Width", "depth": "Depth", "height": "Height", "radius": "Radius"}
MIN_SIZE = 0.001                         # metres: a generator's size parameters must stay above 1 mm
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,39}$")
MAX_FAMILIES = 200


# ------------------------------------------------------------------------------------------------------------------- units
def to_si(kind: str, value: Any, label: str) -> Any:
    if kind == "boolean":
        if not isinstance(value, bool):
            raise BadRequest(f"{label} must be true or false")
        return value
    v = number(value, label, -1e9, 1e9)
    return v / 1000.0 if kind == "length" else math.radians(v) if kind == "angle" else (int(v) if kind == "integer" and float(v).is_integer() else v)


def from_si(kind: str, value: Any) -> Any:
    if kind == "boolean" or value is None:
        return value
    return round(value * 1000.0, 6) if kind == "length" else round(math.degrees(value), 6) if kind == "angle" else value


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


def describe(project: Any, fam: Mapping[str, Any]) -> dict[str, Any]:
    """A family for the person: parameters in the units they edit in, and how many elements use it."""
    required = SHAPES[shape_of(fam)][1]
    rows = []
    for name, spec in fam["parameters"].items():
        kind = spec["type"]
        rows.append({"name": name, "label": LABELS.get(name, name), "type": kind, "unit": UNIT_OF[kind], "required": name in required,
                     "default": from_si(kind, spec.get("default")), "min": from_si(kind, spec.get("min")), "max": from_si(kind, spec.get("max")),
                     "expression": spec.get("expression"), "description": spec.get("description", "")})
    return {"id": fam["id"], "name": fam["name"], "shape": shape_of(fam), "parameters": rows, "instances": fam.get("instance_count", len(instances(project, fam["id"])))}


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
        spec.update({k: v for k, v in base.items() if k in ("default", "min", "max", "expression", "description", "read_only")})
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
        spec["default"] = to_si(kind, raw["default"], f"default of {name}")
    if "expression" not in spec and "default" not in spec:
        raise BadRequest(f"{name} needs a default value or a formula")
    if kind != "boolean":
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
    for oid in instances(project, fam["id"]):
        regenerate(project, oid)
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


def regenerate(project: Any, oid: str) -> None:
    """Place the element: SGEO generated the solid around the origin, so it is put at its point, on its level, above its base offset."""
    obj = project.model.get(oid)
    meta, fam = obj["metadata"], family(project, obj["family_id"])
    z = E.level_elevation(project, E.m(meta, "level")) + float(E.m(meta, "base_offset", 0.0)) + height_of(fam, values(project, oid)) / 2.0
    x, y = E.m(meta, "point")
    project.model.set_transform(oid, E._rot(float(E.m(meta, "rotation", 0.0)), x, y, z))


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
    try:
        oid = project.model.instantiate(fam["id"], overrides, name=name)
    except Exception as exc:
        raise BadRequest(f"{fam['name']} cannot be placed with these values: {exc}") from exc
    E._attach(project, oid, {"category": "generic", "level": level["id"], "point": point, "base_offset": number(p.get("base_offset", 0.0), "base_offset", -100000, 100000) / 1000.0,
                             "rotation": math.radians(number(p.get("rotation", 0.0), "rotation", -360, 360)), "material": material})
    regenerate(project, oid)
    return oid


def _overrides(fam: Mapping[str, Any], raw: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, value in raw.items():
        spec = fam["parameters"].get(name)
        if spec is None:
            raise BadRequest(f"{fam['name']} has no parameter '{name}'")
        if "expression" in spec or spec.get("read_only"):
            raise BadRequest(f"{LABELS.get(name, name)} follows a formula in {fam['name']}; change the formula in the family")
        out[name] = _bounded(name, spec, to_si(spec["type"], value, LABELS.get(name, name)))
    return out


def _bounded(name: str, spec: Mapping[str, Any], v: Any) -> Any:
    kind = spec["type"]
    if kind == "boolean":
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
        if row["expression"]:
            rows.append({"label": f"{row['label']} (formula)", "type": "readonly", "value": f"{value:g} {row['unit']}".strip() + f"  = {row['expression']}"})
        else:
            label = row["label"] + ("" if name in overrides else " (type)")
            if kind == "boolean":
                rows.append({"key": f"param:{name}", "label": label, "type": "choice", "value": "true" if resolved[name] else "false", "choices": [{"value": "true", "label": "Yes"}, {"value": "false", "label": "No"}]})
            else:
                rows.append({"key": f"param:{name}", "label": label, "type": "number", **({"unit": row["unit"]} if row["unit"] else {}), "value": value})
        type_rows.append({"label": row["label"], "type": "readonly",
                          "value": (f"= {row['expression']}" if row["expression"] else f"{row['default']:g} {row['unit']}".strip() if row["default"] is not None else "") + (f"   [{limits} {row['unit']}]".rstrip() if limits and not row["expression"] else "")})
    out["top"] = [{"label": "Type", "type": "readonly", "value": fam["name"]},
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
    doc.define_family(fam["name"], deepcopy(fam["parameters"]), fam["generator"]["kind"], family_id=fam["id"])
    doc.validate().raise_for_errors()
    return doc.dumps()


def import_family_file(project: Any, source: Any) -> list[str]:
    """Add the families of a ``.sgeo`` family file; a name already used gets a numeric suffix. Returns the new family ids."""
    out = []
    for fam in source.families():
        fam.pop("instance_count", None)
        name, n = fam["name"], 1
        while any(f["name"].lower() == name.lower() for f in families(project)):
            n += 1
            name = f"{fam['name']} {n}"
        out.append(project.model.define_family(name, deepcopy(fam["parameters"]), fam["generator"]["kind"]))
    return out
