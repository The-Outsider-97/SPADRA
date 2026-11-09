"""Starter families that follow the worked examples of the research report (sections 4.4-4.6): a door, a window and a rectangular beam.

A template is only a ``family.define`` request: the parameters are SGEO typed parameters, the derived values are SGEO formulas, and the bounds are SGEO
bounds, so everything a template does a person could have typed into the family editor. Lengths are in mm in the request, formulas are written in SI
(a unit literal such as ``800 mm`` is allowed), and nothing here computes anything itself.

What a template does *not* claim: the door's clear width is arithmetic, not a free-passage or accessibility measure; the beam's area and second moment
are geometric identities of an ideal rectangle, not a structural check (see the beam analysis and optimization for the assumptions that apply there).
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from ..tools.errors import BadRequest
from . import families as FM

TEMPLATES: dict[str, dict[str, Any]] = {
    "door": {
        "title": "Single-leaf door (report 4.4)",
        "name": "Door",
        "shape": "box",
        "parameters": {
            "width": {"default": 900, "min": 600, "max": 1800, "scope": "type", "description": "Leaf width"},
            "height": {"default": 2100, "min": 1800, "max": 2600, "scope": "type", "description": "Leaf height"},
            "depth": {"default": 44, "min": 30, "max": 120, "scope": "type", "description": "Leaf thickness"},
            "frame_thickness": {"default": 35, "min": 20, "max": 80, "scope": "type", "description": "Frame member thickness; must stay under half the width"},
            "nominal_clearance": {"default": 10, "min": 0, "max": 50, "scope": "type", "description": "Nominal clearance used in the budget below"},
            "clear_width": {"expression": "width - 2 * frame_thickness", "min": 100, "description": "Width between the frames; the bound keeps it positive"},
            "clearance_budget": {"expression": "clear_width - nominal_clearance", "min": 50, "description": "Illustrative; not a regulatory free-passage width"},
            "show_threshold": {"type": "boolean", "expression": "width > 800 mm", "description": "Example of a Boolean-driven visibility flag"},
        },
    },
    "window": {
        "title": "Multi-light window (report 4.5)",
        "name": "Window",
        "shape": "box",
        "parameters": {
            "width": {"default": 1500, "min": 400, "max": 4000, "scope": "type", "description": "Overall width"},
            "height": {"default": 1200, "min": 400, "max": 3000, "scope": "type", "description": "Overall height"},
            "depth": {"default": 90, "min": 40, "max": 300, "scope": "type", "description": "Frame depth"},
            "frame_width": {"default": 60, "min": 20, "max": 150, "scope": "type", "description": "Frame member width"},
            "panels": {"type": "integer", "default": 3, "min": 1, "max": 8, "scope": "type", "description": "Number of lights"},
            "mullion": {"default": 40, "min": 10, "max": 120, "scope": "type", "description": "Width of each internal mullion"},
            "show_muntin": {"type": "boolean", "expression": "panels > 1", "description": "Mullions exist only when there is more than one light"},
            "clear_span": {"expression": "width - 2 * frame_width", "min": 100, "description": "Glazed span between the frames"},
            "light_width": {"expression": "(clear_span - (panels - 1) * mullion) / panels", "min": 20,
                            "description": "Width of one light; the bound refuses a combination that leaves a negative or sliver glazing region"},
        },
    },
    "beam": {
        "title": "Rectangular beam section (report 4.6)",
        "name": "Rectangular beam",
        "shape": "box",
        "parameters": {
            "width": {"default": 200, "min": 50, "max": 1500, "scope": "type", "description": "Section width b"},
            "height": {"default": 400, "min": 50, "max": 2500, "scope": "type", "description": "Section depth h"},
            "depth": {"default": 4000, "min": 500, "max": 30000, "scope": "type", "description": "Span L (the box's long axis)"},
            "area": {"type": "area", "expression": "width * height", "description": "A = b h (gross section)"},
            "second_moment": {"type": "second_moment", "expression": "width * height ** 3 / 12",
                              "description": "I = b h^3 / 12 about the centroidal strong axis of an ideal rectangle"},
            "volume": {"type": "volume", "expression": "width * height * depth", "description": "Gross material volume V = b h L"},
        },
    },
}


def catalog() -> list[dict[str, Any]]:
    """What can be created: name, title, parameter names, and the derived ones."""
    return [{"template": key, "title": t["title"], "name": t["name"], "shape": t["shape"], "parameters": list(t["parameters"]),
             "derived": [n for n, s in t["parameters"].items() if "expression" in s]} for key, t in TEMPLATES.items()]


def define(project: Any, p: Mapping[str, Any]) -> str:
    """Create a family from a template (``template``; optional ``name``)."""
    key = p.get("template")
    if key not in TEMPLATES:
        raise BadRequest(f"template must be one of {', '.join(TEMPLATES)}", details={"templates": list(TEMPLATES)})
    t = deepcopy(TEMPLATES[key])
    return FM.define(project, {"name": p.get("name") or t["name"], "shape": t["shape"], "parameters": t["parameters"]})
