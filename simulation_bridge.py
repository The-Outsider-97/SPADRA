"""SPADRA's connection to the real SLAI Simulation Agent (``src/agents/simulation_agent.py``), for the dynamic response of one beam (master prompt 12.3).

What is simulated: a **single-degree-of-freedom** model of a beam - one lumped mass on one spring - driven by a load that is either suddenly applied
or suddenly released. It is a textbook idealisation, stated as such in every result; it is not a finite-element analysis and not a structural design check.

Who does what (SPADRA computes none of the structural mechanics itself):

* the **STEM Agent** gives the beam's static response (deflection under the load you entered, from which the spring stiffness is taken), the
  natural frequency and period through its unit-aware parameter evaluator, and the numerical integrator (its own ``rk4``) that advances the motion;
* the **Simulation Agent** runs the rollout: it holds the initial state, calls the supplied transition once per step, records the trajectory, the
  seed, the termination reason and the run's identity, and refuses limits that are exceeded;
* SPADRA states the physical model (the equation of motion and the idealisation), takes the inputs from the beam's parameters and yours, validates
  them, and checks the finished trajectory against the closed-form solution of the same equation. The check is reported, with its error, and is not
  the agent's claim.

Nothing is invented to fill a gap: the elastic modulus and the density are the engine's where it has them (steel) and yours otherwise, and the damping
ratio, the mass fraction and the load are yours.
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
from typing import Any, Mapping

from ..tools.errors import BadRequest, DependencyMissing
from ..tools.validation import choice, number, text

from . import elements as E
from . import structural as S
from .agent_hub import HUB
from .engineering_bridge import BRIDGE as STEM

MODEL_ID = "spadra.beam-sdof"
MODEL_VERSION = "1"
SCENARIOS = {
    "release": "Release: the beam starts displaced by the static deflection under the load and is let go from rest",
    "step": "Step load: the load is applied suddenly to a beam at rest and stays on",
}
MAX_STEPS = 10000
POINTS = 600                      # samples sent to the chart
NOTE = ("A single-degree-of-freedom idealisation of one beam: linear elastic, small displacements, the stiffness taken from the static deflection under the "
        "load you entered, one mode only. The trajectory was checked against the closed-form solution of the same equation. It was not validated against "
        "a measurement or a finite-element model, and it is not a structural design, serviceability check or certification.")

_lock = threading.RLock()


def status() -> dict[str, Any]:
    sim, stem = HUB.available("simulation"), STEM.available
    out: dict[str, Any] = {"available": sim and stem, "agents": {"simulation": sim, "stem": stem}, "scenarios": [{"id": k, "label": v} for k, v in SCENARIOS.items()],
                           "model": {"id": MODEL_ID, "version": MODEL_VERSION}}
    if not sim:
        out["reason"] = HUB.reason("simulation")
    elif not stem:
        out["reason"] = STEM.reason
    return out


def _quantities(oid: str, project: Any, density: float | None) -> dict[str, Any]:
    """The beam's own volume from SGEO, and a density from the engine (steel) or from the person."""
    row = project.quantities([oid], {E.m(E.meta_of(project, oid), "material"): density} if density else None)["rows"][0]
    if row.get("mass_kg") is None:
        raise BadRequest(f"The mass of this beam needs a density: enter the density of {row.get('material') or 'its material'} (kg/m³). SPADRA does not assume one.",
                         details={"needs": "density_kg_m3", "material": row.get("material")})
    return row


def _parameters(project: Any, oid: str, p: Mapping[str, Any]) -> dict[str, Any]:
    scenario = choice(p.get("scenario", "release"), tuple(SCENARIOS), "scenario")
    zeta = float(number(p.get("damping_ratio", 0.0), "damping_ratio", 0.0, 0.99))
    fraction = float(number(p.get("mass_fraction", 0.5), "mass_fraction", 0.01, 1.0))
    added = float(number(p.get("added_mass_kg", 0.0), "added_mass_kg", 0.0, 1e9))
    duration = float(number(p.get("duration_s", 2.0), "duration_s", 0.01, 600.0))
    step = None if p.get("time_step_s") in (None, "") else float(number(p["time_step_s"], "time_step_s", 1e-6, 10.0))
    density = None if p.get("density_kg_m3") in (None, "") else float(number(p["density_kg_m3"], "density_kg_m3", 1.0, 30000.0))
    return {"scenario": scenario, "damping_ratio": zeta, "mass_fraction": fraction, "added_mass_kg": added, "duration_s": duration, "time_step_s": step, "density": density}


def closed_form(scenario: str, k: float, m: float, zeta: float, load_n: float, t: float) -> float:
    """Displacement of m·x'' + c·x' + k·x = F(t) at time t for the two scenarios, from the textbook solution (used only to check the run).
    release: x(0) = F/k, x'(0) = 0, F = 0.   step: x(0) = 0, x'(0) = 0, F = load for t > 0."""
    w = math.sqrt(k / m)
    if zeta == 0.0:
        c = math.cos(w * t)
        return load_n / k * c if scenario == "release" else load_n / k * (1.0 - c)
    wd = w * math.sqrt(1.0 - zeta * zeta)
    decay = math.exp(-zeta * w * t)
    shape = decay * (math.cos(wd * t) + zeta * w / wd * math.sin(wd * t))
    return load_n / k * shape if scenario == "release" else load_n / k * (1.0 - shape)


def simulate_beam(project: Any, oid: str, p: Mapping[str, Any]) -> dict[str, Any]:
    """Run the simulation. Reads the project; changes nothing."""
    st = status()
    if not st["available"]:
        raise DependencyMissing("The simulation needs the SLAI Simulation Agent and STEM Agent", details={"reason": st.get("reason")})
    meta = E.meta_of(project, oid)
    if E.m(meta, "category") != "structural" or E.is_column(meta):
        raise BadRequest("The simulation applies to beams")
    prm = _parameters(project, oid, p)
    # 1. the static response, by the STEM Agent: the spring stiffness is the load divided by the deflection it causes
    beam = S.analyze_beam(project, oid, p)
    deflection = float(beam["outputs"]["max_deflection_m"]["value"])
    if deflection <= 0.0:
        raise BadRequest("The engine returned no deflection for this load, so there is no stiffness to simulate with")
    load_n = float(beam["inputs"]["load"]) * 1000.0 * (beam["inputs"]["span_m"] if beam["inputs"]["load_type"] == "udl" else 1.0)
    stiffness = load_n / deflection
    # 2. the mass: the beam's SGEO volume times a density, the fraction taking part, plus any mass you add
    row = _quantities(oid, project, prm["density"])
    mass = prm["mass_fraction"] * float(row["mass_kg"]) + prm["added_mass_kg"]
    if mass <= 0.0:
        raise BadRequest("The vibrating mass is zero; raise the mass fraction or add mass")
    # 3. natural frequency and period, evaluated by the STEM Agent with units
    freq = STEM.task("parametric", "evaluate_serializable", args=[{"omega": "sqrt(force/defl/mass)", "period": "2*pi/omega", "frequency": "omega/(2*pi)"},
                     {"force": {"value": load_n, "unit": "N"}, "defl": {"value": deflection, "unit": "m"}, "mass": {"value": mass, "unit": "kg"}}])
    omega = float(freq["result"]["omega"]["magnitude"])
    period = float(freq["result"]["period"]["magnitude"])
    damping = 2.0 * prm["damping_ratio"] * mass * omega
    dt = prm["time_step_s"] if prm["time_step_s"] else period / 100.0
    steps = int(math.ceil(prm["duration_s"] / dt))
    if steps > MAX_STEPS:
        raise BadRequest(f"{steps} steps are needed ({prm['duration_s']} s at {dt:.4g} s). The limit is {MAX_STEPS}: shorten the duration or use a larger step",
                         details={"steps": steps, "limit": MAX_STEPS})
    if dt > period / 20.0:
        raise BadRequest(f"The step {dt:.4g} s is too coarse for a {period:.4g} s period (at least 20 steps per period are needed)", details={"period_s": period})
    release = prm["scenario"] == "release"
    force = 0.0 if release else load_n
    x0 = load_n / stiffness if release else 0.0

    from src.agents.simulation.simulation_models import CallableSimulationModel

    def transition(state: Mapping[str, float], action: float, parameters: Mapping[str, float], **_: Any) -> dict[str, float]:
        k, m, c, h = parameters["k"], parameters["m"], parameters["c"], parameters["h"]
        rhs = lambda _t, y: [y[1], (action - c * y[1] - k * y[0]) / m]                       # the equation of motion; the integrator is the STEM Agent's
        y = STEM.task("numerical_methods", "rk4", args=[rhs, [state["x"], state["v"]], 0.0, h], kwargs={"h": h})["result"]["y"][-1]
        return {"x": float(y[0]), "v": float(y[1])}

    model = CallableSimulationModel(transition, model_id=MODEL_ID, version=MODEL_VERSION, stochastic=False,
                                    metadata={"idealisation": "single-degree-of-freedom, linear elastic"})
    sim = HUB.require("simulation")
    request = {"operation": "rollout", "model": model, "initial_state": {"x": x0, "v": 0.0}, "actions": [force] * steps, "time_step": dt, "mode": "deterministic",
               "seed": 0, "parameters": {"k": stiffness, "m": mass, "c": damping, "h": dt},
               "metadata": {"project": project.manifest["id"], "revision": project.revision, "element": oid, "scenario": prm["scenario"]}}
    with _lock:
        run = sim.perform_task(request)
    HUB.record("Simulation Agent", "rollout", run.get("run_id"))
    HUB.record("STEM Agent", "numerical_methods.rk4 (one call per step)", None)
    HUB.record("STEM Agent", "parametric.evaluate_serializable", freq.get("request_id"))
    steps_out = run["trajectory"]["steps"]
    times = [float(s["simulation_time"]) for s in steps_out]
    disp = [float(s["state"]["x"]) for s in steps_out]
    vel = [float(s["state"]["v"]) for s in steps_out]
    # 4. the check against the closed form of the same equation
    reference = [closed_form(prm["scenario"], stiffness, mass, prm["damping_ratio"], load_n, t) for t in times]
    err = max(abs(a - b) for a, b in zip(disp, reference))
    peak = max(abs(v) for v in disp) or 1e-300
    relative = err / peak
    verified = relative < 1e-4
    i_peak = max(range(len(disp)), key=lambda i: abs(disp[i]))
    stride = max(1, len(times) // POINTS)
    idx = list(range(0, len(times), stride)) + ([len(times) - 1] if (len(times) - 1) % stride else [])
    inputs = {"element": oid, "name": project.model.get(oid)["name"], "scenario": prm["scenario"], "support": beam["inputs"]["support"], "load_type": beam["inputs"]["load_type"],
              "load": beam["inputs"]["load"], "load_unit": beam["inputs"]["load_unit"], "span_m": beam["inputs"]["span_m"], "material": beam["inputs"]["material"],
              "youngs_modulus_gpa": beam["inputs"]["youngs_modulus_gpa"], "youngs_modulus_source": beam["inputs"]["youngs_modulus_source"],
              "density_kg_m3": row["density_kg_m3"], "density_source": row["density_source"], "beam_mass_kg": row["mass_kg"], "mass_fraction": prm["mass_fraction"],
              "added_mass_kg": prm["added_mass_kg"], "damping_ratio": prm["damping_ratio"], "duration_s": prm["duration_s"], "time_step_s": dt, "steps": steps}
    digest = hashlib.sha256(json.dumps(inputs, sort_keys=True, default=str).encode()).hexdigest()
    return {
        "id": oid, "name": inputs["name"], "revision": project.revision,
        "idealization": {"label": "Idealized single-degree-of-freedom model",
                         "meaning": "The beam is replaced by one lumped mass on one linear spring and dashpot. It is not a finite-element or full dynamic analysis of the member."},
        "derived": {"static_deflection_m": deflection, "total_load_n": load_n, "stiffness_n_per_m": stiffness, "effective_mass_kg": mass, "damping_n_s_per_m": damping,
                    "natural_frequency_rad_s": omega, "natural_frequency_hz": float(freq["result"]["frequency"]["magnitude"]), "period_s": period},
        "response": {"peak_displacement_m": disp[i_peak], "peak_time_s": times[i_peak], "peak_velocity_m_s": max(vel, key=abs), "final_displacement_m": disp[-1],
                     "static_deflection_m": deflection,
                     "amplification": (abs(disp[i_peak]) / deflection)},
        "series": {"time_s": [times[i] for i in idx], "displacement_m": [disp[i] for i in idx], "reference_m": [reference[i] for i in idx]},
        "verification": {"reference": "closed-form solution of the same single-degree-of-freedom equation", "max_abs_error_m": err, "relative_to_peak": relative,
                         "tolerance": 1e-4, "passed": bool(verified)},
        "provenance": {"input_model": {"project": project.manifest["id"], "name": project.name, "revision": project.revision, "unit": project.manifest["unit"]},
                       "inputs": inputs, "inputs_sha256": digest,
                       "solver": {"executor": "SLAI Simulation Agent (rollout)", "integrator": "SLAI STEM Agent numerical_methods.rk4", "model": f"{MODEL_ID} v{MODEL_VERSION}",
                                  "run_id": run.get("run_id"), "seed": run.get("seed"), "deterministic": run["runtime"].get("deterministic"),
                                  "termination": run.get("termination_reason"), "transitions": run["runtime"].get("transition_count")},
                       "assumptions": ["single degree of freedom: one mass on one linear spring, one dashpot", "stiffness = total load ÷ static deflection from the STEM engine for the stated support and load case",
                                       f"{prm['mass_fraction']:g} of the beam's mass takes part in the motion (your input), plus {prm['added_mass_kg']:g} kg added",
                                       "viscous damping with the ratio you entered", "linear elastic material, small displacements, no axial effects or shear deformation",
                                       "the load is a force on the lumped mass, constant in time after it starts"],
                       "validity": {"status": "numerically verified against the closed form" if verified else "failed the comparison with the closed form",
                                    "validated_against_measurement": False, "structural_certification": False},
                       "warnings": list(run.get("warnings") or [])},
        "note": NOTE,
    }
