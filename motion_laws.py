"""Time-dependent parameters (report 8.4): a motion written as a law of time, authored as keys and checked against the law.

Two laws, both kinematic prescriptions (no forces, no dynamics):

  linear      x(t) = x0 + v (t - start)           a move at constant velocity; two keys with linear interpolation reproduce it exactly
  sinusoid    theta(t) = theta0 + A sin(w (t - start)),  w = 2 pi / period     a turn about the vertical axis through the element's origin

SGEO interpolates keys; it does not evaluate formulas of time. A sinusoid is therefore authored as cubic Hermite keys whose values *and tangents* come from
the law (value and derivative at each key), and the result is measured, not assumed: the evaluated animation is sampled between the keys and compared with
the exact law. If the worst error exceeds ``tolerance`` the command is refused (and so rolls back) and says how many keys per period would be needed.
Before ``start`` and after ``end`` the element holds the value at that end (SGEO's behaviour outside a track's keys)."""
from __future__ import annotations

import math
from typing import Any

from ..tools.errors import BadRequest, Conflict
from ..tools.validation import number
from . import animation_authoring as AA
from . import animation_bridge as AB

KINDS = ("linear", "sinusoid")
MAX_KEYS = 600
MAX_AMPLITUDE = 170.0                                                       # degrees: the evaluated angle is unwrapped from atan2, so it must stay inside (-180, 180)
DEFAULT_TOLERANCE = {"linear": 1e-6, "sinusoid": 0.05}                      # metres, degrees


def _vec(value: Any, label: str, default: Any = None) -> list[float]:
    value = default if value is None else value
    if not isinstance(value, (list, tuple)):
        raise BadRequest(f"{label} must be three numbers")
    return AA.spec_value("translation", value)


def law_value(kind: str, spec: dict[str, Any], t: float) -> Any:
    """The exact law at ``t`` seconds, in UI units (metres as a vector, degrees as a number); held at the ends of [start, end]."""
    u = min(max(t, spec["start"]), spec["end"]) - spec["start"]
    if kind == "linear":
        return [spec["offset"][i] + spec["velocity"][i] * u for i in range(3)]
    return spec["offset"] + spec["amplitude"] * math.sin(2.0 * math.pi * u / spec["period"])


def _slope(kind: str, spec: dict[str, Any], t: float) -> Any:
    """d(value)/dt in the spec's own units (metres/s; radians/s), zero outside [start, end] where the value is held."""
    if not spec["start"] <= t <= spec["end"]:
        return [0.0, 0.0, 0.0] if kind == "linear" else 0.0
    if kind == "linear":
        return list(spec["velocity"])
    w = 2.0 * math.pi / spec["period"]
    return math.radians(spec["amplitude"]) * w * math.cos(w * (t - spec["start"]))


def parse(p: dict[str, Any], *, fps: int, duration: float) -> tuple[str, dict[str, Any]]:
    kind = p.get("kind")
    if kind not in KINDS:
        raise BadRequest(f"kind must be one of {', '.join(KINDS)}")
    start = AA.frame_time(number(p.get("start", 0.0), "start", 0, duration), fps, duration)
    end = AA.frame_time(number(p.get("end", duration), "end", 0, duration), fps, duration)
    if end <= start:
        raise BadRequest("The motion needs to end after it starts", hint="Both times are snapped to whole frames; give them at least one frame apart")
    spec: dict[str, Any] = {"start": start, "end": end}
    if kind == "linear":
        spec["velocity"] = _vec(p.get("velocity"), "velocity (m/s)")
        if not any(spec["velocity"]):
            raise BadRequest("A linear law needs a velocity in at least one direction")
        spec["offset"] = _vec(p.get("offset"), "offset (m)", [0.0, 0.0, 0.0])
        return kind, spec
    spec["amplitude"] = number(p.get("amplitude"), "amplitude (degrees)", -MAX_AMPLITUDE, MAX_AMPLITUDE)
    if spec["amplitude"] == 0:
        raise BadRequest("A sinusoid needs a non-zero amplitude")
    spec["period"] = number(p.get("period"), "period (seconds)", 2.0 / fps, 1e6)
    spec["offset"] = number(p.get("offset", 0.0), "offset (degrees)", -MAX_AMPLITUDE, MAX_AMPLITUDE)
    if abs(spec["offset"]) + abs(spec["amplitude"]) > MAX_AMPLITUDE:
        raise BadRequest(f"offset plus amplitude must stay within {MAX_AMPLITUDE:g} degrees")
    perk = p.get("keys_per_period", 12)
    if isinstance(perk, bool) or not isinstance(perk, int) or not 4 <= perk <= 240:
        raise BadRequest("keys_per_period must be a whole number from 4 to 240")
    spec["keys_per_period"] = perk
    return kind, spec


def key_times(kind: str, spec: dict[str, Any], fps: int, duration: float) -> list[float]:
    """Whole-frame times for the keys: both ends, and for a sinusoid ``keys_per_period`` per period between them."""
    if kind == "linear":
        return [spec["start"], spec["end"]]
    step = spec["period"] / spec["keys_per_period"]
    n = int(math.floor((spec["end"] - spec["start"]) / step + 1e-9))
    raw = [spec["start"] + i * step for i in range(n + 1)] + [spec["end"]]
    times: list[float] = []
    for t in raw:
        t = AA.frame_time(t, fps, duration)
        if not times or t - times[-1] > 1e-9:
            times.append(t)
    if len(times) > MAX_KEYS:
        raise BadRequest(f"That needs {len(times)} keys (limit {MAX_KEYS}); lower keys_per_period or shorten the motion")
    if len(times) < 2:
        raise BadRequest("The motion is too short to key")
    if kind == "sinusoid" and len(times) - 1 < spec["keys_per_period"] * (spec["end"] - spec["start"]) / spec["period"] * 0.5:
        raise BadRequest("The frame rate is too low for that many keys per period: whole-frame key times collide",
                         hint="Lower keys_per_period, lengthen the period, or raise the frame rate")
    return times


def author(spec_doc: dict[str, Any], oid: str, kind: str, law: dict[str, Any], *, fps: int) -> list[float]:
    """Replace the element's track for the law's property with keys carrying the law's values and analytic tangents. Returns the key times."""
    prop = "translation" if kind == "linear" else "turn"
    times = key_times(kind, law, fps, float(spec_doc["duration"]))
    at = AA.find_track(spec_doc, oid, prop)
    if at >= 0:
        del spec_doc["tracks"][at]
    track = AA.new_track(oid, prop, "linear" if kind == "linear" else "smooth")
    for t in times:
        value = law_value(kind, law, t)
        key: dict[str, Any] = {"time": t, "value": AA.spec_value(prop, value)}
        if kind == "sinusoid":
            slope = _slope(kind, law, t)
            key["in_tangent"] = slope
            key["out_tangent"] = slope
        track["keyframes"].append(key)
    spec_doc["tracks"].append(track)
    return times


def measure(project: Any, oid: str, kind: str, law: dict[str, Any], times: list[float], *, per_interval: int = 8) -> dict[str, Any]:
    """Sample SGEO's evaluated animation between the keys and compare with the exact law (metres for a move, degrees for a turn)."""
    base = project.model.world_transform(oid)
    base_angle = math.atan2(base[4], base[0])
    worst, at, samples = 0.0, times[0], 0
    grid = sorted({a + (b - a) * j / per_interval for a, b in zip(times, times[1:]) for j in range(per_interval + 1)})
    for t in grid:
        wt = AB.BRIDGE.evaluate(project.model, t).get_object_state(oid)["world_transform"]
        expected = law_value(kind, law, t)
        if kind == "linear":
            got = [wt[3] - base[3], wt[7] - base[7], wt[11] - base[11]]
            err = math.dist(got, expected)
        else:
            got = math.degrees(math.atan2(wt[4], wt[0]) - base_angle)
            got = (got + 180.0) % 360.0 - 180.0
            err = abs(got - expected)
        samples += 1
        if err > worst:
            worst, at = err, t
    return {"max_error": worst, "at_s": at, "samples": samples, "unit": "m" if kind == "linear" else "deg"}


def describe(kind: str, law: dict[str, Any]) -> str:
    if kind == "linear":
        return "x(t) = x0 + v (t - start), held at x(start) before and x(end) after"
    return "theta(t) = theta0 + A sin(2 pi (t - start) / period), held outside [start, end]"


def apply(project: Any, oid: str, p: dict[str, Any]) -> dict[str, Any]:
    """The ``anim.law`` command body (the caller supplies the transaction): author the law, commit it to SGEO, measure it, refuse if it is not within tolerance."""
    spec_doc, fps = project._authoring_spec(True)
    kind, law = parse(p, fps=fps, duration=float(spec_doc["duration"]))
    tolerance = number(p.get("tolerance", DEFAULT_TOLERANCE[kind]), "tolerance", 1e-12, 180.0)
    times = author(spec_doc, oid, kind, law, fps=fps)
    project._commit_animation(spec_doc)
    m = measure(project, oid, kind, law, times)
    if m["max_error"] > tolerance:
        hint = "Use more keys per period" if kind == "sinusoid" else "Check the units"
        raise Conflict(f"The authored motion differs from the law by {m['max_error']:.4g} {m['unit']} (allowed {tolerance:g})", hint=hint,
                       details={"max_error": m["max_error"], "tolerance": tolerance, "keys": len(times)})
    return {"id": oid, "kind": kind, "law": describe(kind, law), "start_s": law["start"], "end_s": law["end"], "keys": len(times),
            "tolerance": tolerance, **m,
            "scope": "kinematic prescription evaluated by SGEO between keys; the error is measured against the exact law, not guaranteed beyond the sampled points"}
