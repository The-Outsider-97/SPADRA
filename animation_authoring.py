"""Authoring helpers for SPADRA's animation commands.

These functions prepare and read animation *documents*; they never evaluate motion. SGEO validates the document and the SLAI Animation
Agent commits it (``set_animation_v2``) and evaluates it. What is here:

* keyframe bookkeeping on the plain-data animation spec (add / move / delete a key, change a track's interpolation, markers);
* the tangents a "smooth" (cubic) track needs. SGEO requires explicit Hermite tangents on cubic keyframes and offers no way to ask for
  "automatic" ones, so the authoring side supplies a standard Catmull-Rom choice (zero slope at the two ends: an ease in and out);
* reading a pose back from an evaluated transform, so the Pose panel can show what the agent evaluated rather than what was typed.

Property vocabulary used by the UI and the commands: ``translation`` (metres, relative to the element as built), ``turn`` (degrees about the
vertical axis; stored as SGEO ``rotation_angle`` about +Z, in radians), ``scale`` (factors, about the element's own origin) and
``visibility``.
"""
from __future__ import annotations

import copy
import math
import uuid

from typing import Any, Mapping, Sequence

from ..tools.errors import BadRequest

DEFAULT_FPS = 24
FPS_RANGE = (1, 120)
VERTICAL = [0.0, 0.0, 1.0]
PROPERTIES = ("translation", "turn", "scale", "visibility")
INTERPOLATIONS = ("linear", "smooth", "step")                       # "smooth" is SGEO's cubic with authored tangents
_SPEC_PROPERTY = {"translation": "translation", "turn": "rotation_angle", "scale": "scale", "visibility": "visibility"}
_TIME_EPS = 1e-9
SETTINGS_DEFAULT = {"fps": DEFAULT_FPS, "loop": False}


# ------------------------------------------------------------------------------------------------------------------------ names
def spec_property(prop: str) -> str:
    try:
        return _SPEC_PROPERTY[prop]
    except KeyError:
        raise BadRequest(f"'{prop}' is not an animatable property here", details={"properties": list(PROPERTIES)}) from None


def ui_property(spec_prop: str) -> str | None:
    for ui, sp in _SPEC_PROPERTY.items():
        if sp == spec_prop:
            return ui
    return None


def rest_value(prop: str) -> Any:
    """The value that leaves the element exactly as built."""
    return {"translation": [0.0, 0.0, 0.0], "turn": 0.0, "scale": [1.0, 1.0, 1.0], "visibility": True}[prop]


def interpolation_name(track: Mapping[str, Any]) -> str:
    kind = track.get("interpolation", "linear")
    return "smooth" if kind == "cubic" else str(kind)


def frame_time(seconds: float, fps: int, duration: float) -> float:
    """A time snapped to a whole frame and kept inside the animation."""
    t = round(float(seconds) * fps) / fps
    return round(min(max(t, 0.0), float(duration)), 6)


# ------------------------------------------------------------------------------------------------------------------------ the spec
def clean_spec(raw: Mapping[str, Any] | None, duration: float | None = None) -> dict[str, Any]:
    """A deep copy of an animation spec ready to edit (an empty one of ``duration`` if there is none)."""
    if raw is None:
        if duration is None:
            raise BadRequest("The model has no animation yet", hint="Create one first")
        return {"duration": float(duration), "tracks": []}
    return copy.deepcopy(dict(raw))


def find_track(spec: Mapping[str, Any], oid: str, prop: str) -> int:
    target = spec_property(prop)
    for i, track in enumerate(spec["tracks"]):
        if track["object_id"] == oid and track["property"] == target:
            return i
    return -1


def _vector(value: Any, label: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3 or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in value):
        raise BadRequest(f"{label} must be three finite numbers")
    return [float(v) for v in value]


def spec_value(prop: str, value: Any) -> Any:
    """A UI value in the spec's own units (degrees become radians)."""
    if prop == "translation":
        return _vector(value, "A move")
    if prop == "scale":
        v = _vector(value, "A scale")
        if any(abs(x) < 1e-6 for x in v):
            raise BadRequest("A scale factor cannot be zero")
        return v
    if prop == "turn":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise BadRequest("A turn must be a number of degrees")
        return math.radians(float(value))
    if not isinstance(value, bool):
        raise BadRequest("Visibility must be true (shown) or false (hidden)")
    return value


def ui_value(prop: str, value: Any) -> Any:
    return math.degrees(value) if prop == "turn" else value


def new_track(oid: str, prop: str, interpolation: str) -> dict[str, Any]:
    if interpolation not in INTERPOLATIONS:
        raise BadRequest(f"interpolation must be one of {', '.join(INTERPOLATIONS)}")
    if prop == "visibility":
        interpolation = "step"                                                   # a switch has no in-between
    track: dict[str, Any] = {"object_id": oid, "property": spec_property(prop), "interpolation": "cubic" if interpolation == "smooth" else interpolation, "keyframes": []}
    if prop == "turn":
        track["axis"] = list(VERTICAL)
    return track


def set_key(spec: dict[str, Any], oid: str, prop: str, seconds: float, value: Any, *, fps: int, interpolation: str = "smooth") -> list[str]:
    """Write ``value`` for ``prop`` of ``oid`` at ``seconds`` (a key already there is replaced). A new track also gets a key at time 0 holding
    the as-built value, so the motion starts from where the element is rather than jumping. Returns notes about what else was done."""
    duration = float(spec["duration"])
    t = frame_time(seconds, fps, duration)
    notes: list[str] = []
    at = find_track(spec, oid, prop)
    if at < 0:
        track = new_track(oid, prop, interpolation)
        spec["tracks"].append(track)
        if t > _TIME_EPS:
            track["keyframes"].append({"time": 0.0, "value": rest_value(prop)})
            notes.append(f"Also keyed the starting value at 0.00 s")
    else:
        track = spec["tracks"][at]
    keys = track["keyframes"]
    for k in keys:
        if abs(k["time"] - t) < _TIME_EPS:
            k["value"] = value
            break
    else:
        keys.append({"time": t, "value": value})
        keys.sort(key=lambda k: k["time"])
    retangent(track)
    return notes


def delete_key(spec: dict[str, Any], oid: str, prop: str, seconds: float, *, fps: int) -> bool:
    """Remove a key; a track left with no keys is removed. Returns whether a key was there."""
    at = find_track(spec, oid, prop)
    if at < 0:
        return False
    t = frame_time(seconds, fps, spec["duration"])
    track = spec["tracks"][at]
    keep = [k for k in track["keyframes"] if abs(k["time"] - t) >= _TIME_EPS]
    if len(keep) == len(track["keyframes"]):
        return False
    if keep:
        track["keyframes"] = keep
        retangent(track)
    else:
        del spec["tracks"][at]
    return True


def move_key(spec: dict[str, Any], oid: str, prop: str, source: float, target: float, *, fps: int) -> float:
    """Move a key to another time (snapped to a frame). Another key at the target time is refused rather than silently replaced."""
    at = find_track(spec, oid, prop)
    if at < 0:
        raise BadRequest("That element has no track for this property")
    track = spec["tracks"][at]
    t0 = frame_time(source, fps, spec["duration"])
    t1 = frame_time(target, fps, spec["duration"])
    key = next((k for k in track["keyframes"] if abs(k["time"] - t0) < _TIME_EPS), None)
    if key is None:
        raise BadRequest("There is no key at that time")
    if abs(t1 - t0) < _TIME_EPS:
        return t1
    if any(abs(k["time"] - t1) < _TIME_EPS for k in track["keyframes"]):
        raise BadRequest(f"There is already a key at {t1:.2f} s", hint="Move it to a free frame, or delete the other key first")
    key["time"] = t1
    track["keyframes"].sort(key=lambda k: k["time"])
    retangent(track)
    return t1


def set_interpolation(spec: dict[str, Any], oid: str, prop: str, interpolation: str) -> None:
    if interpolation not in INTERPOLATIONS:
        raise BadRequest(f"interpolation must be one of {', '.join(INTERPOLATIONS)}")
    at = find_track(spec, oid, prop)
    if at < 0:
        raise BadRequest("That element has no track for this property")
    if prop == "visibility" and interpolation != "step":
        raise BadRequest("Show / hide can only switch (step); it has no in-between")
    track = spec["tracks"][at]
    track["interpolation"] = "cubic" if interpolation == "smooth" else interpolation
    retangent(track)


def delete_track(spec: dict[str, Any], oid: str, prop: str) -> None:
    at = find_track(spec, oid, prop)
    if at < 0:
        raise BadRequest("That element has no track for this property")
    del spec["tracks"][at]


# ------------------------------------------------------------------------------------------------------------------------ tangents
def retangent(track: dict[str, Any]) -> None:
    """Give a cubic track its tangents (and strip them from any other kind: SGEO refuses stray tangents)."""
    keys = track["keyframes"]
    if track.get("interpolation") != "cubic":
        for k in keys:
            k.pop("in_tangent", None)
            k.pop("out_tangent", None)
        return
    times = [k["time"] for k in keys]
    vector = isinstance(keys[0]["value"], list)
    components = len(keys[0]["value"]) if vector else 1
    slopes: list[list[float]] = []
    for i in range(len(keys)):
        if i == 0 or i == len(keys) - 1:
            slopes.append([0.0] * components)                                  # ease in and out at the ends
            continue
        span = times[i + 1] - times[i - 1]
        a, b = keys[i - 1]["value"], keys[i + 1]["value"]
        slopes.append([((b[c] - a[c]) if vector else (b - a)) / span for c in range(components)] if vector else [(b - a) / span])
    for k, s in zip(keys, slopes):
        tangent: Any = list(s) if vector else s[0]
        k["in_tangent"] = tangent
        k["out_tangent"] = list(s) if vector else s[0]


# ------------------------------------------------------------------------------------------------------------------------ markers
def add_marker(spec: dict[str, Any], seconds: float, name: str, *, fps: int, oid: str | None = None) -> str:
    t = frame_time(seconds, fps, spec["duration"])
    marker: dict[str, Any] = {"id": str(uuid.uuid4()), "name": name, "time": t}
    if oid:
        marker["object_id"] = oid
    spec.setdefault("events", []).append(marker)
    spec["events"].sort(key=lambda e: (e["time"], e["name"]))
    return marker["id"]


def delete_marker(spec: dict[str, Any], marker_id: str) -> None:
    events = spec.get("events", [])
    keep = [e for e in events if e["id"] != marker_id]
    if len(keep) == len(events):
        raise BadRequest("There is no such marker")
    if keep:
        spec["events"] = keep
    else:
        spec.pop("events", None)


# ------------------------------------------------------------------------------------------------------------------------ presets
PRESETS = {
    "slide": "Slide: move by a distance over the animation",
    "spin": "Spin: turn through an angle over the animation",
    "grow": "Grow: scale from the built size to a factor",
    "appear": "Appear: hidden at the start, shown at the end",
    "disappear": "Disappear: shown at the start, hidden at the end",
}


def apply_preset(spec: dict[str, Any], oid: str, kind: str, params: Mapping[str, Any], *, fps: int, start: float, end: float) -> list[str]:
    """Two keys that make a common motion in one step. ``start`` / ``end`` are seconds; values come from ``params`` (units as in the UI)."""
    if kind not in PRESETS:
        raise BadRequest(f"preset must be one of {', '.join(PRESETS)}")
    if end <= start:
        raise BadRequest("The motion needs to end after it starts")
    notes: list[str] = []
    if kind == "slide":
        goal = _vector(params.get("by", [0.0, 0.0, 0.0]), "'by'")
        if not any(goal):
            raise BadRequest("Slide needs a distance in at least one direction")
        steps = [("translation", rest_value("translation"), goal)]
    elif kind == "spin":
        degrees = params.get("degrees", 360)
        if isinstance(degrees, bool) or not isinstance(degrees, (int, float)) or degrees == 0:
            raise BadRequest("Spin needs a non-zero angle in degrees")
        steps = [("turn", 0.0, float(degrees))]
    elif kind == "grow":
        factor = params.get("factor", 2.0)
        if isinstance(factor, bool) or not isinstance(factor, (int, float)) or factor <= 0 or factor == 1:
            raise BadRequest("Grow needs a scale factor above zero other than 1")
        steps = [("scale", [1.0, 1.0, 1.0], [float(factor)] * 3)]
    else:
        steps = [("visibility", kind == "disappear", kind == "appear")]
    for prop, a, b in steps:
        notes += set_key(spec, oid, prop, start, spec_value(prop, a), fps=fps, interpolation="smooth" if prop != "visibility" else "step")
        set_key(spec, oid, prop, end, spec_value(prop, b), fps=fps)
        if prop != "visibility":
            ease_between(spec, oid, prop, start, end)
    return notes


def ease_between(spec: dict[str, Any], oid: str, prop: str, start: float, end: float) -> None:
    """Give the keys at ``start`` and ``end`` zero slope. A preset is an ease-in / ease-out between two keys; when the motion starts after 0 s the track also has a hold key at 0,
    and the automatic tangent at the start key would then be the slope of the whole run, which overshoots (the leaf would swing back before it opens)."""
    track = spec["tracks"][find_track(spec, oid, prop)]
    if track.get("interpolation") != "cubic":
        return
    for k in track["keyframes"]:
        if abs(k["time"] - start) < _TIME_EPS or abs(k["time"] - end) < _TIME_EPS:
            zero: Any = [0.0] * len(k["value"]) if isinstance(k["value"], list) else 0.0
            k["in_tangent"], k["out_tangent"] = zero, (list(zero) if isinstance(zero, list) else zero)


def has_preset(spec: Mapping[str, Any], oid: str, kind: str) -> bool:
    if kind not in PRESETS:
        raise BadRequest(f"preset must be one of {', '.join(PRESETS)}")
    if kind == "slide":
        return find_track(spec, oid, "translation") >= 0
    if kind == "spin":
        return find_track(spec, oid, "turn") >= 0
    if kind == "grow":
        return find_track(spec, oid, "scale") >= 0
    return find_track(spec, oid, "visibility") >= 0

# ------------------------------------------------------------------------------------------------------------------------ reading a pose
def _angle(m: Sequence[float]) -> float:
    return math.atan2(m[4], m[0])


def decode_pose(base: Sequence[float], evaluated: Sequence[float]) -> dict[str, Any]:
    """The animated part of an evaluated transform, relative to the element as built.

    SGEO composes the animation as rotation x scale about the element's origin, then the translation. ``base`` and ``evaluated`` are 4x4
    row-major transforms. The turn is returned as the principal angle in degrees; the caller unwraps it against the track's own keys."""
    delta = [evaluated[3] - base[3], evaluated[7] - base[7], evaluated[11] - base[11]]
    scale = []
    for j in range(3):
        norm_e = math.sqrt(sum(evaluated[4 * i + j] ** 2 for i in range(3)))
        norm_b = math.sqrt(sum(base[4 * i + j] ** 2 for i in range(3)))
        scale.append(norm_e / norm_b if norm_b > 1e-12 else 1.0)
    unscaled = [evaluated[4 * i + j] / (scale[j] if scale[j] > 1e-12 else 1.0) for i in range(3) for j in range(3)]
    q = [unscaled[0], unscaled[1], unscaled[2], 0.0, unscaled[3], unscaled[4], unscaled[5], 0.0]
    turn = math.degrees(math.atan2(q[4], q[0]) - math.atan2(base[4], base[0]))
    turn = (turn + 180.0) % 360.0 - 180.0
    return {"translation": delta, "scale": scale, "turn": turn}


def unwrap_turn(decoded: float, spec: Mapping[str, Any], oid: str, seconds: float) -> float:
    """Pick the equivalent angle nearest the track's key closest in time (a turn of 270° must not read back as -90°)."""
    for track in spec.get("tracks", []):
        if track["object_id"] == oid and track["property"] == "rotation_angle" and track["keyframes"]:
            nearest = min(track["keyframes"], key=lambda k: abs(k["time"] - seconds))
            ref = math.degrees(nearest["value"])
            return decoded + 360.0 * round((ref - decoded) / 360.0)
    return decoded

def read_pose(base: Sequence[float], evaluated: Sequence[float], spec: Mapping[str, Any], oid: str, seconds: float) -> dict[str, Any]:
    """The animated part of an evaluated transform, relative to the element as built. The turn is unwrapped against the track's own keys."""
    pose = decode_pose(base, evaluated)
    pose["turn"] = unwrap_turn(pose["turn"], spec, oid, seconds)
    return pose

def read_pose_from_transform(base: Sequence[float], evaluated: Sequence[float], spec: Mapping[str, Any], oid: str, seconds: float) -> dict[str, Any]:
    """The animated part of an evaluated transform, relative to the element as built. The turn is unwrapped against the track's own keys."""
    pose = decode_pose(base, evaluated)
    pose["turn"] = unwrap_turn(pose["turn"], spec, oid, seconds)
    return pose