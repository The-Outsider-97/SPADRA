"""Beam analysis for SPADRA's beams, computed by the SLAI STEM Agent (see ``engineering_bridge``).

This module only prepares the agent's inputs from a beam's own parameters and labels the answer. The load, the support condition and the
load case are the person's; nothing is assumed on their behalf, and self-weight is not added.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

from ..tools.errors import BadRequest
from ..tools.validation import number, text

from . import elements as E
from .engineering_bridge import BRIDGE, ENGINE_MATERIAL

LOAD_TYPES = ("udl", "point_center", "point_end")
SUPPORTS = ("simply_supported", "cantilever", "fixed_fixed")
NOTE = ("Analytical baseline for one prismatic member under the load you entered: linear elastic, small deflections, self-weight not included, "
        "no load factors, no section capacity or code check. It is not a structural design or a substitute for an engineer's verification.")


def section_of(meta: Mapping[str, Any]) -> dict[str, Any]:
    """Second moment (m⁴), section modulus (m³), area (m²) and the depth of the section as placed, for the bending that a beam's load causes about its
    horizontal axis. A rectangle, a round tube and a square or rectangular tube turned a quarter-turn at a time are the engine's own section properties
    (a tube is the outer rectangle less the inner one); every other profile is worked out from the outline of the modelled section."""
    sec = E.beam_section(meta)
    if sec["family"] == "rect":
        width, depth = float(E.m(meta, "width")), float(E.m(meta, "depth"))
        r = BRIDGE.section(width, depth)
        return {"second_moment_m4": float(r["second_moment_m4"]), "section_modulus_m3": float(r["section_modulus_m3"]), "area_m2": width * depth, "width_m": width,
                "depth_m": depth, "profile": "Rectangular", "source": "the SLAI engine's rectangle"}
    from . import profiles as PR
    fam, d = sec["family"], sec["dims"]
    rs = PR.rolled(PR.rings(fam, d), sec["roll"])
    width_mm, depth_mm = PR.bounds(rs)
    out = {"width_m": width_mm / 1000.0, "depth_m": depth_mm / 1000.0, "profile": sec["size"]}
    quarter = abs(sec["roll"] / 90.0 - round(sec["roll"] / 90.0)) < 1e-9
    if fam == "CHS":
        r = BRIDGE.hollow_circle(d["d"] / 1000.0, (d["d"] - 2 * d["t"]) / 1000.0)
        out.update(second_moment_m4=float(r["second_moment_m4"]), section_modulus_m3=float(r["section_modulus_m3"]), area_m2=float(r["area_m2"]), source="the SLAI engine's hollow circle")
    elif fam in ("SHS", "RHS") and quarter:
        h, b, t = (d["a"], d["a"], d["t"]) if fam == "SHS" else (d["h"], d["b"], d["t"])
        if round(sec["roll"] / 90.0) % 2:
            h, b = b, h
        outer, inner = BRIDGE.section(b / 1000.0, h / 1000.0), BRIDGE.section((b - 2 * t) / 1000.0, (h - 2 * t) / 1000.0)
        inertia = float(outer["second_moment_m4"]) - float(inner["second_moment_m4"])
        out.update(second_moment_m4=inertia, section_modulus_m3=inertia / (h / 2000.0), area_m2=(b * h - (b - 2 * t) * (h - 2 * t)) / 1e6,
                   source="the SLAI engine's rectangle (outer less inner)")
    else:
        props = PR.properties(rs)
        inertia = props["ix_mm4"] / 1e12
        out.update(second_moment_m4=inertia, section_modulus_m3=inertia / (props["extreme_fibre_mm"] / 1000.0), area_m2=props["area_mm2"] / 1e6,
                   source="the modelled section outline (shoelace sums; root radii and flange tapers not modelled)")
    return out


def analyze_beam(project: Any, oid: str, p: Mapping[str, Any]) -> dict[str, Any]:
    """Peak moment, shear and deflection of one beam. Reads the model; changes nothing."""
    meta = E.meta_of(project, oid)
    if E.m(meta, "category") != "structural":
        raise BadRequest("Beam analysis applies to beams only")
    support = text(p.get("support", "simply_supported"), "support", 40)
    load_type = text(p.get("load_type", "udl"), "load_type", 40)
    if support not in SUPPORTS or load_type not in LOAD_TYPES:
        raise BadRequest(f"support must be one of {', '.join(SUPPORTS)} and load_type one of {', '.join(LOAD_TYPES)}")
    if p.get("load") is None:
        raise BadRequest("Enter the load: kN/m for a uniform load, kN for a point load")
    load_kn = float(number(p["load"], "load", 1e-6, 1e7))
    material = str(E.m(meta, "material", ""))
    engine = BRIDGE.materials().get(material) if BRIDGE.available else None
    if p.get("youngs_modulus_gpa") not in (None, ""):
        modulus_pa = float(number(p["youngs_modulus_gpa"], "youngs_modulus_gpa", 0.001, 2000)) * 1e9
        modulus_source = "entered by you"
    elif engine:
        modulus_pa = float(engine["youngs_modulus_pa"])
        modulus_source = f"the SLAI engine's value for {engine['engine_material']}"
    else:
        raise BadRequest(f"The engine has no elastic modulus for {material or 'this material'}. Enter E (GPa) for this beam's material; "
                         "SPADRA does not assume one.", details={"needs": "youngs_modulus_gpa", "material": material})
    start, end = E.m(meta, "start"), E.m(meta, "end")
    span = math.dist(start, end)
    section = section_of(meta)
    width, depth = section["width_m"], section["depth_m"]
    result = BRIDGE.beam_response(span_m=span, load=load_kn * 1000.0, load_type=load_type, support=support,
                                  second_moment_m4=float(section["second_moment_m4"]), youngs_modulus_pa=modulus_pa)
    return {"id": oid, "name": project.model.get(oid)["name"], "agent": "SLAI STEM Agent", "method": result["method"],
            "inputs": {"span_m": span, "width_m": width, "depth_m": depth, "profile": section["profile"], "section_source": section["source"], "area_m2": section["area_m2"], "material": material, "support": support, "load_type": load_type,
                       "load": load_kn, "load_unit": "kN/m" if load_type == "udl" else "kN",
                       "youngs_modulus_gpa": modulus_pa / 1e9, "youngs_modulus_source": modulus_source,
                       "second_moment_m4": float(section["second_moment_m4"]), "section_modulus_m3": float(section["section_modulus_m3"])},
            "outputs": result["outputs"], "assumptions": result["assumptions"] + ["self-weight not included", "bending about the section's horizontal centroidal axis, as the profile is placed (depth vertical)"],
            "details": result["details"], "validation": result["validation"], "note": NOTE, "revision": project.revision}
