"""How a 3D view is drawn: the five visualization modes, the edge thickness of Lined, the shadows of Lined / Colored / Gray and the sun.

This is a *view* setting, never a model one: it is stored on the 3D view (``view["visual"]``), changes no geometry, material or relationship, and a
project that stores none (every project made before this existed) reads as the defaults, so there is nothing to migrate in the file. The client has
the same rules in ``src/client/visual_shading.ts``; ``tests/test_visualization.py`` and ``tests_ts/visualization.test.ts`` read the same table of
cases (``tests/visual_cases.json``) so the two cannot drift.

=========  ===============  =========  ==============================================================================
mode       edge thickness   shadows    sun position
=========  ===============  =========  ==============================================================================
wireframe  –                never       –
lined      yes              optional    when shadows are on
realistic  –                always      yes
colored    –                optional    when shadows are on
gray       –                optional    when shadows are on
=========  ===============  =========  ==============================================================================

The sun is one shared setting (azimuth clockwise from north, elevation above the horizon, both in degrees): every mode that casts shadows, the
viewport and the snapshots read the same two numbers, so there is no second lighting state to contradict it. Edge thickness is a percentage added to
the baseline line weight, from +50 % (1.5×) to +200 % (3.0×).
"""
from __future__ import annotations

import math
from typing import Any, Mapping

VERSION = 1
MODES = ("wireframe", "lined", "realistic", "colored", "gray")
SHADOW_CHOICE = ("lined", "colored", "gray")            # the modes in which the person decides; wireframe never has shadows and realistic always does
EDGE_BOOST_RANGE = (50.0, 200.0)                         # percent over the baseline thickness: 1.5x ... 3.0x
SUN_ELEVATION_RANGE = (1.0, 90.0)                        # degrees above the horizon; a sun on the horizon casts shadows of unbounded length
DEFAULTS: dict[str, Any] = {
    "v": VERSION, "mode": "realistic", "edge_boost": 100.0,
    "shadows": {"lined": False, "colored": False, "gray": False},
    "sun": {"azimuth": 225.0, "elevation": 45.0},        # from the south-west, as the interactive view and the snapshots have always been lit
}


class VisualError(ValueError):
    """A visualization setting that is not allowed, with a sentence that names the setting and the allowed range."""


def edge_factor(boost: float) -> float:
    """The thickness a boost gives, as a multiple of the baseline: +50 % is 1.5, +200 % is 3.0."""
    return 1.0 + float(boost) / 100.0


def shadows_on(visual: Mapping[str, Any]) -> bool:
    """Whether the mode the view is in casts shadows: never in wireframe, always in realistic, as chosen in the others."""
    mode = visual["mode"]
    return mode == "realistic" or (mode in SHADOW_CHOICE and bool(visual["shadows"][mode]))


def sun_visible_control(visual: Mapping[str, Any]) -> bool:
    """Whether the sun position is a control that matters in this view: it is whenever shadows are cast."""
    return shadows_on(visual)


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise VisualError(f"{name} must be a number.")
    return float(value)


def _ranged(value: Any, name: str, low: float, high: float, unit: str = "") -> float:
    number = _finite(value, name)
    if not low <= number <= high:
        raise VisualError(f"{name} must be between {low:g}{unit} and {high:g}{unit}.")
    return number


def normalise(raw: Any) -> dict[str, Any]:
    """A stored value read as a complete, valid setting. Anything missing, of the wrong type or out of range falls back to the default for that one
    setting, so an old, edited or damaged file still opens and the rest of what it holds is kept."""
    out = {"v": VERSION, "mode": DEFAULTS["mode"], "edge_boost": DEFAULTS["edge_boost"], "shadows": dict(DEFAULTS["shadows"]), "sun": dict(DEFAULTS["sun"])}
    if not isinstance(raw, Mapping):
        return out
    if raw.get("mode") in MODES:
        out["mode"] = raw["mode"]
    try:
        out["edge_boost"] = _ranged(raw.get("edge_boost"), "Edge thickness", *EDGE_BOOST_RANGE, unit=" %")
    except VisualError:
        pass
    shadows = raw.get("shadows")
    if isinstance(shadows, Mapping):
        for mode in SHADOW_CHOICE:
            if isinstance(shadows.get(mode), bool):
                out["shadows"][mode] = shadows[mode]
    sun = raw.get("sun")
    if isinstance(sun, Mapping):
        try:
            out["sun"]["azimuth"] = _finite(sun.get("azimuth"), "Sun azimuth") % 360.0
        except VisualError:
            pass
        try:
            out["sun"]["elevation"] = _ranged(sun.get("elevation"), "Sun elevation", *SUN_ELEVATION_RANGE, unit="°")
        except VisualError:
            pass
    return out


def updated(current: Any, patch: Mapping[str, Any]) -> dict[str, Any]:
    """``current`` with the settings in ``patch`` changed, each checked; a value that is not allowed is refused (not quietly corrected), and nothing changes.

    ``patch`` may hold ``mode``, ``edge_boost``, ``shadows`` (a mode to on/off, only the three modes that have the choice) and ``sun`` (``azimuth`` and/or
    ``elevation``). The settings of the modes that are not shown are kept, so going Lined → Realistic → Lined finds Lined as it was left."""
    result = normalise(current)
    unknown = set(patch) - {"mode", "edge_boost", "shadows", "sun"}
    if unknown:
        raise VisualError(f"'{sorted(unknown)[0]}' is not a visualization setting.")
    if "mode" in patch:
        if patch["mode"] not in MODES:
            raise VisualError("The visualization mode must be one of " + ", ".join(MODES) + ".")
        result["mode"] = patch["mode"]
    if "edge_boost" in patch:
        result["edge_boost"] = _ranged(patch["edge_boost"], "Edge thickness", *EDGE_BOOST_RANGE, unit=" %")
    if "shadows" in patch:
        if not isinstance(patch["shadows"], Mapping):
            raise VisualError("Shadows must say which mode they are for.")
        for mode, on in patch["shadows"].items():
            if mode not in SHADOW_CHOICE:
                raise VisualError("Shadows can be switched on or off in " + ", ".join(SHADOW_CHOICE) + " only: wireframe never has them and realistic always does.")
            if not isinstance(on, bool):
                raise VisualError("Shadows are either on or off.")
            result["shadows"][mode] = on
    if "sun" in patch:
        if not isinstance(patch["sun"], Mapping) or not set(patch["sun"]) <= {"azimuth", "elevation"}:
            raise VisualError("The sun has an azimuth and an elevation.")
        if "azimuth" in patch["sun"]:
            result["sun"]["azimuth"] = _finite(patch["sun"]["azimuth"], "Sun azimuth") % 360.0
        if "elevation" in patch["sun"]:
            result["sun"]["elevation"] = _ranged(patch["sun"]["elevation"], "Sun elevation", *SUN_ELEVATION_RANGE, unit="°")
    return result


def sun_direction(azimuth: float, elevation: float) -> tuple[float, float, float]:
    """The unit vector towards the sun in the model's axes (x east, y north, z up) for an azimuth clockwise from north and an elevation above the horizon."""
    az, el = math.radians(azimuth), math.radians(elevation)
    return (math.sin(az) * math.cos(el), math.cos(az) * math.cos(el), math.sin(el))
