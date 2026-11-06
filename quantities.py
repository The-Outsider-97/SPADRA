"""Quantities of the model's elements: volume, surface area, size, mass and centre of mass.

Volume and surface area are SGEO's own (``SGEODocument.volume`` / ``surface_area``), measured on the element's real geometry with its door and
window openings. Density is never assumed: the SLAI STEM engine's value is used where the engine has one for the material (structural steel),
otherwise the person supplies it. An element without a density has no mass, and a total that would need it says it is incomplete.

The centre is the volume-weighted centroid of the element's closed tessellation (signed tetrahedra). SGEO and the STEM engine have no such
helper, so this is the one calculation here that is SPADRA's own; it is cross-checked against SGEO's volume for every element and refused
where the two disagree. A uniform density per element is assumed, so a combined centre is a centre of mass only where every element has a density,
and is labelled a centre of volume otherwise.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from src.utils.spatial_formats.common.errors import SpatialFormatError

from ..tools.errors import BadRequest
from ..tools.validation import number
from . import elements as E
from .engineering_bridge import BRIDGE as ENGINEERING

_REFUSALS = (SpatialFormatError, BadRequest)                      # what a measurement may legitimately refuse with; anything else is a defect and is not hidden

NOTE = ("Quantities are measured on the modelled geometry (openings included, finishes and reinforcement not). Mass uses one uniform density per "
        "material, as given. This is a quantity take-off, not a structural or cost certification.")
CROSS_CHECK_TOLERANCE = 1e-6


def centroid_of_closed_mesh(vertices: Sequence[Sequence[float]], faces: Sequence[Sequence[int]]) -> tuple[float, list[float]]:
    """Volume (positive) and volume centroid of a closed, consistently wound triangle mesh. Pure: no SGEO, no model."""
    volume = 0.0
    weighted = [0.0, 0.0, 0.0]
    for a, b, c in faces:
        p, q, r = vertices[a], vertices[b], vertices[c]
        v = (p[0] * (q[1] * r[2] - q[2] * r[1]) + p[1] * (q[2] * r[0] - q[0] * r[2]) + p[2] * (q[0] * r[1] - q[1] * r[0])) / 6.0
        volume += v
        for i in range(3):
            weighted[i] += v * (p[i] + q[i] + r[i]) / 4.0
    if abs(volume) < 1e-12:
        raise BadRequest("The element has no volume to find a centre of")
    return abs(volume), [w / volume for w in weighted]


def _world(vertices: Sequence[Sequence[float]], t: Sequence[float]) -> list[list[float]]:
    return [[t[0] * x + t[1] * y + t[2] * z + t[3], t[4] * x + t[5] * y + t[6] * z + t[7], t[8] * x + t[9] * y + t[10] * z + t[11]] for x, y, z in vertices]


def engine_densities() -> dict[str, float]:
    """SPADRA material name -> density (kg/m³) where the SLAI engine has one."""
    if not ENGINEERING.available:
        return {}
    return {name: float(info["density_kg_m3"]) for name, info in ENGINEERING.materials().items() if info.get("density_kg_m3")}


def _measure(model: Any, obj: Mapping[str, Any], world: Sequence[float]) -> tuple[Any, Any]:
    """SGEO's ``volume`` and ``surface_area`` of one element, as callables. Each depends only on the element's geometry and its world
    transform, but asking the full model re-validates every element in it each time, which makes a whole-model take-off quadratic. So the
    element is placed alone in a scratch SGEO document with that same geometry and world transform, and SGEO measures it there: the same
    SGEO code, the same inputs, an answer that does not depend on how many other elements the model has (tests compare the two exactly).
    If the scratch document cannot be made, the full model is asked instead."""
    try:
        from src.utils.sgeo import SGEODocument
        scratch = SGEODocument.new("measure", unit="m")
        sid = scratch.add_box(width=1.0, depth=1.0, height=1.0, name="measure")
        scratch.set_geometry(sid, obj["geometry"])
        scratch.set_transform(sid, list(world))
        return (lambda: scratch.volume(sid, unit="m")), (lambda: scratch.surface_area(sid, unit="m"))
    except SpatialFormatError:                                       # the geometry cannot be measured on its own: measure it where it lives
        oid = obj["id"]
        return (lambda: model.volume(oid, unit="m")), (lambda: model.surface_area(oid, unit="m"))


def _row(project: Any, oid: str, densities: Mapping[str, tuple[float, str]], matrices: Mapping[str, Any], bounds: Mapping[str, Any]) -> dict[str, Any]:
    model = project.model
    obj = model.get(oid)
    volume_of, area_of = _measure(model, obj, matrices[oid])
    meta = obj["metadata"]
    material = str(E.m(meta, "material", "") or "")
    row: dict[str, Any] = {"id": oid, "name": obj["name"], "category": str(E.m(meta, "category", "generic")), "material": material or None,
                           "volume_m3": None, "surface_area_m2": None, "size_m": None, "density_kg_m3": None, "density_source": None,
                           "mass_kg": None, "centroid_m": None, "notes": []}
    lo, hi = bounds[oid]
    row["size_m"] = [hi[i] - lo[i] for i in range(3)]
    try:
        row["surface_area_m2"] = float(area_of())
    except _REFUSALS as exc:
        row["notes"].append(f"surface area: {exc}")
    try:
        volume = float(volume_of())
    except _REFUSALS as exc:
        row["notes"].append(f"volume: {exc}")
        return row
    row["volume_m3"] = volume
    try:
        mesh = model.tessellate(oid)
        measured, centre = centroid_of_closed_mesh(_world(mesh["vertices"], matrices[oid]), mesh["faces"])
        if abs(measured - volume) > CROSS_CHECK_TOLERANCE * max(1.0, volume):
            raise BadRequest(f"the tessellated volume ({measured:.6g}) disagrees with SGEO's ({volume:.6g})")
        row["centroid_m"] = centre
    except _REFUSALS as exc:
        row["notes"].append(f"centre: {exc}")
    layered = row["category"] == "foundation" and bool(E.m(meta, "above") or E.m(meta, "below"))
    if layered:                                                       # several materials in one element: the mass is the sum over its layers (see take_off)
        row["notes"].append("built up of layers of different materials: volumes and mass are given per material")
        row["layers"] = _layer_volumes(project, oid)
    elif material in densities:
        density, source = densities[material]
        row.update(density_kg_m3=density, density_source=source, mass_kg=volume * density)
    elif material:
        row["notes"].append(f"no density for {material}")
    else:
        row["notes"].append("no material, so no density")
    return row


def _layer_volumes(project: Any, oid: str) -> list[dict[str, Any]]:
    from . import foundations as FD
    return [{"material": l["material"], "volume_m3": l["volume_m3"]} for l in FD.quantities(project, oid)["layers"]]


def take_off(project: Any, ids: Sequence[str] | None, densities: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Quantities of ``ids`` (every element when None). ``densities`` maps a material name to kg/m³ entered by the person; they take
    precedence over the engine's value. Reads the model; changes nothing."""
    from .project import all_ids
    everything = all_ids(project.model, has_geometry=True)
    cutters = {i for i in everything if E.m(project.model.get(i)["metadata"], "category") == "void"}          # a void removes material: it has no quantities
    if ids is not None and cutters & set(ids):
        raise BadRequest("A void removes material and has no quantities", details={"ids": sorted(cutters & set(ids))[:10]})
    everything = [i for i in everything if i not in cutters]
    chosen = list(everything) if ids is None else list(dict.fromkeys(ids))
    unknown = [i for i in chosen if i not in set(everything)]
    if unknown:
        raise BadRequest("Some of the selected elements no longer exist or have no geometry", details={"ids": unknown[:10]})
    if not chosen:
        raise BadRequest("There is nothing to measure: the model has no elements", hint="Draw a wall first")
    table: dict[str, tuple[float, str]] = {m: (d, "the SLAI engine's value") for m, d in engine_densities().items()}
    for material, value in (densities or {}).items():
        table[str(material)] = (float(number(value, f"density of {material}", 1.0, 25000.0)), "entered by you")
    raw = project.model._model                                       # the native SGEO scene: one pass answers for every element
    matrices, bounds = raw.world_transforms(), raw.bounds_by_object()      # (asking per element re-validates the whole scene each time)
    rows = [_row(project, oid, table, matrices, bounds) for oid in chosen]

    measured = [r for r in rows if r["volume_m3"] is not None]
    massed = [r for r in measured if r["mass_kg"] is not None]
    totals: dict[str, Any] = {"count": len(rows), "measured": len(measured),
                              "volume_m3": math.fsum(r["volume_m3"] for r in measured),
                              "surface_area_m2": math.fsum(r["surface_area_m2"] for r in rows if r["surface_area_m2"] is not None),
                              "mass_kg": math.fsum(r["mass_kg"] for r in massed) if massed else None,
                              "mass_complete": bool(measured) and len(massed) == len(measured)}
    centred = [r for r in measured if r["centroid_m"] is not None]
    centre = None
    if centred:
        by_mass = totals["mass_complete"] and len(centred) == len(measured)
        weights = [r["mass_kg"] if by_mass else r["volume_m3"] for r in centred]
        total = math.fsum(weights)
        centre = {"kind": "centre of mass" if by_mass else "centre of volume", "point_m": [math.fsum(w * r["centroid_m"][i] for w, r in zip(weights, centred)) / total for i in range(3)],
                  "complete": len(centred) == len(rows)}
    materials: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r.get("layers"):                                          # a built-up element counts each layer under its own material; its surface goes to the element's
            for ly in r["layers"]:
                lb = materials.setdefault(ly["material"], {"material": ly["material"], "count": 0, "volume_m3": 0.0, "surface_area_m2": 0.0, "mass_kg": 0.0, "mass_complete": True})
                lb["count"] += 1
                lb["volume_m3"] += ly["volume_m3"]
                if ly["material"] in table:
                    lb["mass_kg"] += ly["volume_m3"] * table[ly["material"]][0]
                else:
                    lb["mass_complete"] = False
            sb = materials.setdefault(r["material"] or "(none)", {"material": r["material"] or "(none)", "count": 0, "volume_m3": 0.0, "surface_area_m2": 0.0, "mass_kg": 0.0, "mass_complete": True})
            sb["surface_area_m2"] += r["surface_area_m2"] or 0.0      # the whole outer surface is counted once, under the element's own material
            continue
        bucket = materials.setdefault(r["material"] or "(none)", {"material": r["material"] or "(none)", "count": 0, "volume_m3": 0.0, "surface_area_m2": 0.0, "mass_kg": 0.0, "mass_complete": True})
        bucket["count"] += 1
        bucket["volume_m3"] += r["volume_m3"] or 0.0
        bucket["surface_area_m2"] += r["surface_area_m2"] or 0.0
        if r["mass_kg"] is None:
            bucket["mass_complete"] = False
        else:
            bucket["mass_kg"] += r["mass_kg"]
    for b in materials.values():
        if not b["mass_complete"]:
            b["mass_kg"] = None
    needs = sorted({r["material"] for r in measured if r["mass_kg"] is None and r["material"]})
    return {"rows": rows, "totals": totals, "centre": centre, "by_material": sorted(materials.values(), key=lambda b: b["material"]),
            "needs_density": needs, "note": NOTE, "revision": project.revision,
            "source": "SGEO volume and surface area" + (" · densities from the SLAI STEM engine where it has them" if engine_densities() else "")}
