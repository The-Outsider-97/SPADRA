"""SPADRA's connection to the real SLAI STEM Agent (``src/agents/stem_agent.py``), for beam analysis.

SPADRA contains no structural formulas. A request is turned into the agent's own ``engineering_analysis.beam_response`` task:

* geometry (span, section depth and width) is read from the beam element's parameters and the second moment of area comes from the
  engine's own ``section_properties`` for a rectangle;
* the elastic modulus is the engine's configured value where the engine has one for the material, otherwise the person must supply it
  (the engine has no concrete or timber entry, and a number is not invented to fill the gap);
* the loads, the support condition and the load case are the person's input, never inferred;
* everything the agent returns (moment, shear, deflection, the engine's own deflection-limit check, its stated assumptions) is passed
  on as it is, together with the inputs that were used.

The result is an analytical baseline for a single prismatic member, not a code check. When the agent cannot be imported the bridge says
so and the capability stays off.
"""
from __future__ import annotations

import importlib
import threading
from typing import Any, Mapping

from ..tools.errors import BadRequest, DependencyMissing

AGENT_MODULE = "src.agents.stem_agent"
MEMORY_MODULE = "src.agents.collaborative.shared_memory"

#: SPADRA beam material → the engine's own material name, where the engine has one. Anything else needs a modulus from the person.
ENGINE_MATERIAL = {"Steel": "structural_steel_a36"}


class EngineeringBridge:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._agent: Any = None
        self._reason: str | None = None
        self._tried = False

    # ------------------------------------------------------------------------------------------------------------------ availability
    def _load(self) -> None:
        with self._lock:
            if self._tried:
                return
            self._tried = True
            try:
                memory = importlib.import_module(MEMORY_MODULE).SharedMemory()
                self._agent = importlib.import_module(AGENT_MODULE).STEMAgent(memory, None)
                _ = self._agent.engineering.civil.BEAM_CASES               # fail now, not on the first request, if the engine is not what we expect
            except Exception as exc:
                self._agent = None
                self._reason = f"{type(exc).__name__}: {exc}"

    @property
    def available(self) -> bool:
        self._load()
        return self._agent is not None

    @property
    def reason(self) -> str | None:
        self._load()
        return self._reason

    def _require(self) -> Any:
        self._load()
        if self._agent is None:
            raise DependencyMissing("The SLAI STEM Agent is not available", details={"reason": self._reason})
        return self._agent

    def status(self) -> dict[str, Any]:
        self._load()
        if self._agent is None:
            return {"available": False, "reason": self._reason or "The SLAI STEM Agent is not available",
                    "hint": "Start SPADRA inside a SLAI checkout that contains src/agents/stem_agent.py and its dependencies (numpy, scipy, psutil, pyyaml)"}
        return {"available": True, "agent": "SLAI STEM Agent", "cases": self.cases(), "materials": self.materials()}

    # ------------------------------------------------------------------------------------------------------------------ what the engine knows
    def cases(self) -> list[dict[str, str]]:
        civil = self._require().engineering.civil
        return [{"support": s, "load_type": t} for s, t in civil.BEAM_CASES]

    def materials(self) -> dict[str, dict[str, Any]]:
        """For each SPADRA material the engine knows: its modulus (Pa), density (kg/m³) and the name it is filed under."""
        civil = self._require().engineering.civil
        out: dict[str, dict[str, Any]] = {}
        for name, engine_name in ENGINE_MATERIAL.items():
            props = civil.material(engine_name)
            out[name] = {"engine_material": engine_name, "youngs_modulus_pa": props["youngs_modulus"], "density_kg_m3": props.get("density")}
        return out

    def section(self, width_m: float, depth_m: float) -> dict[str, float]:
        """Rectangular section properties from the engine (width b, depth h; bending about the horizontal axis)."""
        civil = self._require().engineering.civil
        try:
            return dict(civil.section_properties("rectangle", width_m=width_m, height_m=depth_m))
        except Exception as exc:
            raise _translate(exc) from exc

    def hollow_circle(self, diameter_m: float, inner_diameter_m: float) -> dict[str, float]:
        """Circular hollow section properties from the engine."""
        civil = self._require().engineering.civil
        try:
            return dict(civil.section_properties("hollow_circle", diameter_m=diameter_m, inner_diameter_m=inner_diameter_m))
        except Exception as exc:
            raise _translate(exc) from exc

    # ------------------------------------------------------------------------------------------------------------------ any STEM task
    def task(self, domain: str, operation: str, *, args: list[Any] | None = None, kwargs: Mapping[str, Any] | None = None) -> Any:
        """One allowlisted STEM operation, exactly as the agent runs it. Returns the agent's own ``result``; the agent's typed errors
        (a unit it does not know, an expression it cannot parse) become ordinary 400 answers that carry the agent's message."""
        agent = self._require()
        with self._lock:
            try:
                answer = agent.perform_task({"domain": domain, "operation": operation, "args": list(args or []), "kwargs": dict(kwargs or {}),
                                             "cache": False, "publish": False})
            except Exception as exc:
                raise _translate(exc) from exc
        if not isinstance(answer, Mapping) or answer.get("status") not in ("success", "completed_with_warning"):
            raise BadRequest("The STEM Agent did not return a result", details={"answer": str(answer)[:500]})
        return {"result": answer.get("result"), "request_id": answer.get("request_id"), "status": answer.get("status"), "diagnostics": answer.get("diagnostics")}

    def numerical(self) -> Any:
        """The agent's own numerical-methods engine (used as the integrator of a simulation model; it is the agent's code, not a copy)."""
        return self._require().numerical_methods

    # ------------------------------------------------------------------------------------------------------------------ analysis
    def beam_response(self, *, span_m: float, load: float, load_type: str, support: str, second_moment_m4: float,
                      youngs_modulus_pa: float) -> dict[str, Any]:
        agent = self._require()
        cases = {(c["support"], c["load_type"]) for c in self.cases()}
        if (support, load_type) not in cases:
            allowed = ", ".join(f"{s} + {t}" for s, t in sorted(cases))
            raise BadRequest(f"The engine does not support '{support}' with '{load_type}'. Supported: {allowed}", details={"allowed": sorted(f"{s}:{t}" for s, t in cases)})
        load_unit = "N/m" if load_type == "udl" else "N"
        with self._lock:
            try:
                answer = agent.perform_task({
                    "domain": "engineering_analysis", "operation": "beam_response",
                    "kwargs": {"span": {"value": span_m, "unit": "m"}, "load": {"value": load, "unit": load_unit},
                               "second_moment": {"value": second_moment_m4, "unit": "m**4"},
                               "youngs_modulus": {"value": youngs_modulus_pa, "unit": "Pa"},
                               "support": support, "load_type": load_type}})
            except Exception as exc:
                raise _translate(exc) from exc
        result = answer.get("result") if isinstance(answer, Mapping) else None
        if not isinstance(result, Mapping) or answer.get("status") != "success":
            raise BadRequest("The STEM Agent did not return a beam result", details={"answer": str(answer)[:500]})
        outputs = {key: {"value": float(item["value"]), "unit": item["unit"]["symbol"]} for key, item in result["outputs"].items()}
        return {"method": result.get("method"), "outputs": outputs, "assumptions": list(result.get("assumptions", [])),
                "details": result.get("details", {}), "validation": result.get("validation"), "request_id": answer.get("request_id")}


BRIDGE = EngineeringBridge()


def _translate(exc: Exception) -> Exception:
    """The agent raises its own typed errors for bad input; the application reports those as ordinary 400 answers."""
    if type(exc).__module__.startswith("src.agents"):
        return BadRequest(str(exc) or type(exc).__name__, details={"error": type(exc).__name__})
    return exc
