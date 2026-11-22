"""Layered model validation: one explainable pipeline over the checks SPADRA already has.

  Layer 1  geometric correctness   solid integrity measured on the element's real tessellation (closed edge-manifold, positive volume, finite
                                   coordinates, volume against the kernel's own figure), minimum thickness, and the bounding-box clash candidates of
                                   ``checks.coordination``
  Layer 2  BIM / functional        every other finding of ``Project.check()``: references, hosting, parameters, associations, requirements, documents
  Layer 3  physical (optional)     only for what the caller asks for: a beam analysed by the SLAI STEM Agent with the load, support and deflection limit
                                   the caller enters. Reported as an analytical baseline; it is not a structural design, certification or code check, and
                                   where the engine or an input is missing the result says "unsupported" instead of a number
  Layer 4  explainable diagnostics every finding carries its layer, rule, severity, the element, the measurement against its limit, what was assumed and,
                                   where one exists, a correction expressed as ordinary SPADRA commands

Nothing here changes the model. A correction is only ever a *proposal*: ``propose_fix`` hands the chosen corrections to the assistant's existing
proposal path (dry run on a scratch copy, bound to the revision, explicit approval, undo). Layers 1 and 2 are deterministic functions of the project at
a revision, so their results are cached per revision and identical on every run; the independent volume figure is computed here from the triangles
(divergence theorem) and compared with the kernel's, so the kernel is not the only judge of its own output.

The limits are the caller's to set (``limits``). The defaults are modelling sanity thresholds, not code requirements."""
from __future__ import annotations

import hashlib
import math
import threading
import time
from typing import Any, Mapping, Sequence

from ..tools.errors import BadRequest, Conflict
from ..tools.validation import number, text
from . import elements as E

LAYERS = ({"id": 1, "key": "geometry", "title": "Geometric correctness"},
          {"id": 2, "key": "bim", "title": "BIM and functional consistency"},
          {"id": 3, "key": "physical", "title": "Physical / engineering analysis (optional, analytical baseline only)"})
LAYER_OF_CATEGORY = {"geometry": 1}                       # every other category of Project.check() is a BIM / functional finding (layer 2)
DEFAULT_LIMITS = {"min_thickness_m": 0.02, "volume_rel_tolerance": 1e-6}
LIMIT_BOUNDS = {"min_thickness_m": (0.0001, 5.0), "volume_rel_tolerance": (1e-12, 0.5)}
THICKNESS_CATEGORIES = ("wall", "floor", "roof")
MERGE = 1e-7                                              # m: vertices closer than this are one vertex when edges are paired
CACHE_SIZE = 16
SEVERITY = {"error": 0, "warning": 1, "info": 2}
SCOPE = ("Layers 1 and 2 are consistency and geometry checks of the model's own data. Layer 3 is an analytical baseline for the loads you enter. "
         "None of it is professional structural certification, a code-compliance check or regulatory approval.")


def limits_of(raw: Mapping[str, Any] | None) -> dict[str, float]:
    out = dict(DEFAULT_LIMITS)
    for key, value in (raw or {}).items():
        if key not in DEFAULT_LIMITS:
            raise BadRequest(f"Unknown limit '{key}'. The limits are: {', '.join(DEFAULT_LIMITS)}")
        lo, hi = LIMIT_BOUNDS[key]
        out[key] = float(number(value, key, lo, hi))
    return out


# ------------------------------------------------------------------------------------------------------------------ layer 1: solids
def solid_metrics(vertices: Sequence[Sequence[float]], faces: Sequence[Sequence[int]]) -> dict[str, Any]:
    """Independent measurements of a triangle mesh: non-finite coordinates, edges that are not shared by exactly two triangles, signed volume, and the
    bounding-box size. Pure arithmetic on the triangles; it does not call the geometry kernel."""
    finite = all(math.isfinite(c) for v in vertices for c in v)
    if not finite or not faces:
        return {"finite": finite, "open_edges": None, "nonmanifold_edges": None, "volume": None, "size": None, "triangles": len(faces)}
    index: dict[tuple[int, int, int], int] = {}
    merged = []
    for v in vertices:
        key = tuple(round(c / MERGE) for c in v)
        merged.append(index.setdefault(key, len(index)))
    edges: dict[tuple[int, int], list[int]] = {}
    volume = 0.0
    ref = vertices[0]                                     # summed relative to a vertex: from the world origin a distant solid loses all precision
    for tri in faces:
        a, b, c = (merged[i] for i in tri)
        if a == b or b == c or a == c:
            continue                                      # a degenerate triangle carries neither area nor edges
        for u, w in ((a, b), (b, c), (c, a)):
            edges.setdefault((min(u, w), max(u, w)), []).append(1 if u < w else -1)
        p, q, r = ([vertices[i][k] - ref[k] for k in range(3)] for i in tri)
        volume += (p[0] * (q[1] * r[2] - q[2] * r[1]) - p[1] * (q[0] * r[2] - q[2] * r[0]) + p[2] * (q[0] * r[1] - q[1] * r[0])) / 6.0
    open_edges = sum(1 for d in edges.values() if len(d) == 1)
    # a closed, consistently oriented surface uses every edge exactly twice, once in each direction
    nonmanifold = sum(1 for d in edges.values() if len(d) > 2 or (len(d) == 2 and d[0] == d[1]))
    lo = [min(v[i] for v in vertices) for i in range(3)]
    hi = [max(v[i] for v in vertices) for i in range(3)]
    return {"finite": True, "open_edges": open_edges, "nonmanifold_edges": nonmanifold, "volume": volume, "size": [h - l for l, h in zip(lo, hi)], "triangles": len(faces)}


def _id_of(layer: int, rule: str, ref: str, extra: str = "") -> str:
    return f"L{layer}-{rule}-" + hashlib.sha1(f"{ref}|{extra}".encode()).hexdigest()[:8]


def _finding(layer: int, rule: str, severity: str, project: Any, ref: str, message: str, *, measurement: Mapping[str, Any] | None = None,
             basis: str = "", fix: Mapping[str, Any] | None = None, extra: str = "") -> dict[str, Any]:
    alive = E.alive(project, ref)
    out: dict[str, Any] = {"id": _id_of(layer, rule, ref, extra or message), "layer": layer, "rule": rule, "severity": severity, "ref": ref,
                           "element": {"id": ref, "name": project.model.get(ref)["name"], "category": E.category(project, ref)} if alive else {"id": ref},
                           "message": message, "basis": basis}
    if measurement:
        out["measurement"] = dict(measurement)
    if fix:
        out["fix"] = dict(fix)
    return out


def _geometry(project: Any, scope: set[str] | None, limits: Mapping[str, float]) -> tuple[list[dict[str, Any]], int]:
    found: list[dict[str, Any]] = []
    checked = 0
    for oid in E.element_ids(project):
        if scope is not None and oid not in scope:
            continue
        try:
            mesh = project.model.tessellate(oid)
        except Exception as exc:                                                                    # noqa: BLE001 - a solid that cannot be built is itself the finding
            found.append(_finding(1, "solid_buildable", "error", project, oid, f"The solid cannot be built: {type(exc).__name__}: {exc}", basis="geometry kernel tessellation"))
            continue
        if not mesh.get("faces"):
            continue                                                                                # e.g. a purely logical element: nothing to measure
        checked += 1
        m = solid_metrics(mesh["vertices"], mesh["faces"])
        if not m["finite"]:
            found.append(_finding(1, "finite_coordinates", "error", project, oid, "The solid has non-finite (NaN or infinite) coordinates",
                                  basis="triangle vertices of the tessellation"))
            continue
        if m["open_edges"] or m["nonmanifold_edges"]:
            found.append(_finding(1, "solid_closed", "error", project, oid,
                                  f"The solid is not watertight: {m['open_edges']} open and {m['nonmanifold_edges']} non-manifold or inconsistently oriented edge(s)",
                                  measurement={"open_edges": m["open_edges"], "nonmanifold_edges": m["nonmanifold_edges"], "limit": 0, "unit": "edges"},
                                  basis="every edge of a closed solid is used by exactly two triangles, once in each direction"))
            continue
        volume = m["volume"]
        if volume <= 0:
            found.append(_finding(1, "solid_volume", "error", project, oid, "The solid has no positive volume (it is flat or turned inside out)",
                                  measurement={"value": volume, "limit": 0, "unit": "m3"}, basis="signed volume of the triangles (divergence theorem)"))
            continue
        kernel = float(project.model.volume(oid, unit="m"))
        rel = abs(volume - kernel) / max(abs(kernel), 1e-12)
        if rel > limits["volume_rel_tolerance"]:
            found.append(_finding(1, "volume_agreement", "error", project, oid,
                                  f"The independently measured volume ({volume:.6f} m³) differs from the kernel's ({kernel:.6f} m³) by {rel:.2e}",
                                  measurement={"value": volume, "limit": kernel, "relative_difference": rel, "tolerance": limits["volume_rel_tolerance"], "unit": "m3"},
                                  basis="two independent evaluations of the same solid disagree"))
        category = E.category(project, oid)
        if category in THICKNESS_CATEGORIES:
            thickness = E.m(E.meta_of(project, oid), "thickness")
            if isinstance(thickness, (int, float)) and thickness < limits["min_thickness_m"]:
                found.append(_finding(1, "min_thickness", "error", project, oid,
                                      f"{category.title()} thickness {thickness * 1000:.1f} mm is below the minimum of {limits['min_thickness_m'] * 1000:.1f} mm",
                                      measurement={"value": thickness, "limit": limits["min_thickness_m"], "unit": "m"},
                                      basis="the minimum is a limit you set (default: a modelling sanity threshold), not a code requirement",
                                      fix={"title": f"Set the thickness to {limits['min_thickness_m'] * 1000:.0f} mm", "risk": "medium",
                                           "steps": [{"op": "model.set_property", "params": {"id": oid, "key": "thickness", "value": round(limits["min_thickness_m"] * 1000, 6)}}]}))
    return found, checked


# ------------------------------------------------------------------------------------------------------------- layers 1/2: existing checks
def _from_project_checks(project: Any, scope: set[str] | None) -> list[dict[str, Any]]:
    found = []
    for item in project.check()["issues"]:
        layer = LAYER_OF_CATEGORY.get(item["category"], 2)
        if scope is not None and item["ref"] not in scope:
            continue
        basis = ("bounding-box overlap candidate: a box around an element with an opening also covers the opening; the person decides" if layer == 1
                 else "the project's own data (references, hosting, parameters, associations, requirements, documents)")
        found.append(_finding(layer, item["category"], item["severity"], project, item["ref"], item["message"], basis=basis))
    return found


# --------------------------------------------------------------------------------------------------------------------- layer 3: physical
def deflection_finding(project: Any, oid: str, outputs: Mapping[str, Any], span_m: float, ratio: float) -> dict[str, Any] | None:
    """A finding when the analysed deflection exceeds span/ratio. ``ratio`` is the caller's: nothing is assumed on their behalf."""
    key = next((k for k in outputs if "deflection" in k.lower()), None)
    if key is None:
        return None
    value = abs(float(outputs[key]["value"]))
    unit = outputs[key]["unit"]
    metres = value * {"m": 1.0, "mm": 1e-3, "cm": 1e-2}.get(unit, math.nan)
    if not math.isfinite(metres):
        return None
    limit = span_m / ratio
    if metres <= limit:
        return None
    return _finding(3, "beam_deflection", "warning", project, oid,
                    f"Analysed deflection {metres * 1000:.1f} mm exceeds the limit span/{ratio:g} = {limit * 1000:.1f} mm",
                    measurement={"value": metres, "limit": limit, "unit": "m", "span_m": span_m, "ratio": ratio},
                    basis="analytical baseline for the load you entered (linear elastic, self-weight not included); not a structural design or code check")


def _physical(project: Any, requests: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from . import structural as ST
    findings: list[dict[str, Any]] = []
    analyses: list[dict[str, Any]] = []
    for req in requests:
        oid = text(req.get("id"), "id", 80)
        if not E.alive(project, oid):
            analyses.append({"id": oid, "status": "unsupported", "reason": "No such element"})
            continue
        try:
            result = ST.analyze_beam(project, oid, req)
        except Exception as exc:                                                                    # noqa: BLE001 - reported as unsupported with the reason; never a made-up number
            analyses.append({"id": oid, "status": "unsupported", "reason": f"{type(exc).__name__}: {exc}"})
            continue
        entry = {"id": oid, "status": "analysed", "agent": result["agent"], "method": result["method"], "inputs": result["inputs"], "outputs": result["outputs"],
                 "assumptions": result["assumptions"], "note": result["note"]}
        ratio = req.get("max_deflection_ratio")
        if ratio in (None, ""):
            entry["limit"] = "no deflection limit was entered, so no pass/fail is given"
        else:
            ratio = float(number(ratio, "max_deflection_ratio", 1, 100000))
            finding = deflection_finding(project, oid, result["outputs"], float(result["inputs"]["span_m"]), ratio)
            entry["limit"] = f"span/{ratio:g}"
            entry["within_limit"] = finding is None
            if finding:
                findings.append(finding)
        analyses.append(entry)
    return findings, analyses


# ---------------------------------------------------------------------------------------------------------------------------- the pipeline
class Pipeline:
    """Runs the layers and caches layers 1 and 2 per (project, revision, scope, limits); a model edit changes the revision, so nothing stale is served."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[tuple, dict[str, Any]] = {}

    def validate(self, project: Any, *, ids: Sequence[str] | None = None, limits: Mapping[str, Any] | None = None,
                 layers: Sequence[int] | None = None, physical: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
        wanted = tuple(sorted(set(layers or (1, 2))))
        if not wanted or any(l not in (1, 2, 3) for l in wanted):
            raise BadRequest("layers is any of 1 (geometry), 2 (BIM) and 3 (physical)")
        if physical and 3 not in wanted:
            wanted = tuple(sorted({*wanted, 3}))
        if 3 in wanted and not physical:
            raise BadRequest("Layer 3 needs the beams to analyse, each with its load, support and (to get a pass/fail) max_deflection_ratio")
        lim = limits_of(limits)
        scope = {i for i in ids if E.alive(project, i)} if ids else None
        key = (project.manifest["id"], project.revision, tuple(sorted(scope)) if scope is not None else None, tuple(sorted(lim.items())), tuple(l for l in wanted if l != 3))
        started = time.perf_counter()
        with self._lock:
            base = self._cache.get(key)
        cached = base is not None
        if base is None:
            findings: list[dict[str, Any]] = []
            checked = 0
            if 1 in wanted:
                geo, checked = _geometry(project, scope, lim)
                findings += geo
            if 1 in wanted or 2 in wanted:
                findings += [f for f in _from_project_checks(project, scope) if f["layer"] in wanted]
            base = {"findings": findings, "checked_solids": checked}
            with self._lock:
                self._cache[key] = base
                while len(self._cache) > CACHE_SIZE:
                    self._cache.pop(next(iter(self._cache)))
        findings = list(base["findings"])
        analyses: list[dict[str, Any]] = []
        if 3 in wanted:
            extra, analyses = _physical(project, physical or [])
            findings += extra
        findings.sort(key=lambda f: (f["layer"], SEVERITY.get(f["severity"], 3), f["rule"], f["ref"], f["id"]))
        counts = {sev: sum(1 for f in findings if f["severity"] == sev) for sev in SEVERITY}
        return {"project": project.manifest["id"], "revision": project.revision, "layers": [l for l in LAYERS if l["id"] in wanted],
                "scope": sorted(scope) if scope is not None else "model", "limits": lim, "findings": findings, "counts": counts,
                "by_layer": {str(l): sum(1 for f in findings if f["layer"] == l) for l in wanted}, "ok": counts["error"] == 0,
                "fixable": sum(1 for f in findings if f.get("fix")), "analyses": analyses, "checked_solids": base["checked_solids"],
                "unsupported": [a for a in analyses if a["status"] == "unsupported"], "basis": SCOPE, "cached": cached,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3)}

    def propose_fix(self, project: Any, revision: Any, finding_ids: Sequence[str], **options: Any) -> dict[str, Any]:
        """One reviewable assistant proposal from the corrections of the chosen findings. A finding from another revision is refused (validate again);
        the assistant dry-runs the steps on a scratch copy and binds the proposal to the revision. Nothing changes until it is approved."""
        from .assistant import ASSISTANT
        if revision is None or int(revision) != project.revision:
            raise Conflict(f"These findings were made at revision {revision}; the project is now at revision {project.revision}", hint="Validate again")
        result = self.validate(project, **options)
        chosen = [f for f in result["findings"] if f["id"] in set(finding_ids) and f.get("fix")]
        if not chosen:
            raise BadRequest("None of those findings has a correction to propose")
        steps = [s for f in chosen for s in f["fix"]["steps"]]
        targets = list(dict.fromkeys([f["ref"] for f in chosen] + [s["params"]["id"] for s in steps if isinstance(s["params"].get("id"), str)]))
        risk = "high" if any(f["fix"].get("risk") == "high" for f in chosen) else "medium"
        title = chosen[0]["fix"]["title"] if len(chosen) == 1 else f"Correct {len(chosen)} validation findings"
        explanation = (f"Proposed from the layered validation at revision {project.revision}: " + "; ".join(f"{f['message']} → {f['fix']['title']}" for f in chosen)
                       + ". Every step is an ordinary SPADRA command, dry-run on a copy first; approving applies them together and Undo takes them back.")
        return ASSISTANT.propose_commands(project, source="layered validation", title=title, explanation=explanation, steps=steps, targets=targets,
                                          evidence={"revision": project.revision, "findings": [{k: f[k] for k in ("id", "layer", "rule", "message", "measurement") if k in f} for f in chosen]},
                                          evidence_kind="validation", risk=risk)


PIPELINE = Pipeline()
