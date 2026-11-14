"""SPADRA's view of the SLAI agent ecosystem (master prompt 14).

SPADRA does not copy agent code. This module is the one place that knows which agents SPADRA talks to, loads them lazily from the SLAI
checkout, and reports - for every agent the specification names - whether SPADRA uses it, what it is used for, and if not, why not.
Integration is capability-driven: an agent is *integrated* only where it gives SPADRA a service that was run and tested, and it is *available*
only if it really imports in this installation.

Agents SPADRA does not use say so with the reason. A reason is written only from something that was observed: an import that failed where
SPADRA was built, or what the agent's own documentation says it is for. Nothing here is a stand-in for an agent that is missing.
"""
from __future__ import annotations

import importlib
import importlib.util
import math
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from ..tools.errors import DependencyMissing

AGENTS_PACKAGE = "src.agents"
MEMORY_MODULE = "src.agents.collaborative.shared_memory"


@dataclass(frozen=True)
class AgentSpec:
    key: str                    # the registry name used in the API
    label: str
    section: str                # the master-prompt section that names it
    module: str                 # module under src.agents
    cls: str
    purpose: str                # what SPADRA uses it for, when integrated
    integrated: bool
    why_not: str = ""           # when not integrated: what was observed


SPECS: tuple[AgentSpec, ...] = (
    AgentSpec("stem", "STEM Agent", "14.3", "stem_agent", "STEMAgent",
              "Unit conversion, parameter formulas with units, constraint checks, beam response and the numerical integrator used by simulations", True),
    AgentSpec("simulation", "Simulation Agent", "14.4", "simulation_agent", "SimulationAgent",
              "Runs the beam dynamic-response simulation (rollout with a supplied state-transition model); keeps the run's seed, steps and lineage", True),
    AgentSpec("spatial", "Spatial Agent", "14.2", "spatial_agent", "SpatialAgent",
              "Proximity, contact and relation queries between model elements, distances and collision tests on their bounding boxes", True),
    AgentSpec("optimization", "Optimization Agent", "14.9", "optimization_agent", "OptimizationAgent",
              "Bounded optimisation of one beam dimension against a stated objective and constraint, evaluated with the STEM Agent", True),
    AgentSpec("animation", "Animation Agent", "14.6", "animation_agent", "AnimationAgent",
              "Every animation edit and the evaluation of motion (see the timeline)", True),
    AgentSpec("language", "Language Agent", "14.5", "language_agent", "LanguageAgent", "", False,
              "Needs PyTorch and trained checkpoints. It could not be imported where SPADRA was built, so no adapter could be run or tested. "
              "The assistant's interpreter is therefore a fixed set of command patterns, not a language model, and it says so."),
    AgentSpec("planning", "Planning Agent", "14.7", "planning_agent", "PlanningAgent", "", False,
              "Needs PyTorch, which could not be imported where SPADRA was built, so no adapter could be run or tested. Multi-step requests are not decomposed by an agent."),
    AgentSpec("reasoning", "Reasoning Agent", "14.8", "reasoning_agent", "ReasoningAgent", "", False,
              "Needs PyTorch, which could not be imported where SPADRA was built, so no adapter could be run or tested."),
    AgentSpec("verification", "Verification Agent", "14.10", "verification_agent", "VerificationAgent", "", False,
              "Its own documentation limits it to formal objects (contracts, SAT/SMT, explicit-state model checking). Proposals are validated by dry-running them "
              "on a copy of the project through SPADRA's own command validators, which is what checks a real edit."),
    AgentSpec("knowledge", "Knowledge Agent", "14.11", "knowledge_agent", "KnowledgeAgent", "", False,
              "Needs statsmodels, which could not be imported where SPADRA was built, so no adapter could be run or tested. SPADRA adds no knowledge store of its own."),
    AgentSpec("reader", "Reader Agent", "14.12", "reader_agent", "ReaderAgent", "", False,
              "Imports, but SPADRA has no document-ingestion feature to attach it to yet; nothing was built on it."),
    AgentSpec("quality", "Quality Agent", "14.13", "quality_agent", "QualityAgent", "", False, "No verified service for SPADRA was identified."),
    AgentSpec("evaluation", "Evaluation Agent", "14.13", "evaluation_agent", "EvaluationAgent", "", False, "Needs PyTorch (not importable where SPADRA was built); no service identified."),
    AgentSpec("provenance", "Provenance Agent", "14.13", "provenance_agent", "ProvenanceAgent", "", False,
              "Not importable where SPADRA was built (missing project data package). Simulation and optimisation results carry their own provenance record instead."),
    AgentSpec("safety", "Safety Agent", "14.13", "safety_agent", "SafetyAgent", "", False, "Needs PyTorch (not importable where SPADRA was built); no service identified."),
    AgentSpec("privacy", "Privacy Agent", "14.13", "privacy_agent", "PrivacyAgent", "", False,
              "No verified service identified; the assistant sends agents only the selection it is given."),
    AgentSpec("alignment", "Alignment Agent", "14.13", "alignment_agent", "AlignmentAgent", "", False, "Needs statsmodels (not importable where SPADRA was built); no service identified."),
    AgentSpec("execution", "Execution Agent", "14.13", "execution_agent", "ExecutionAgent", "", False,
              "Not used on purpose: every change must run through SPADRA's validated commands, never through an agent that executes."),
    AgentSpec("handler", "Handler Agent", "14.13", "handler_agent", "HandlerAgent", "", False, "No verified service identified."),
    AgentSpec("collaborative", "Collaborative Agent", "14.13", "collaborative_agent", "CollaborativeAgent", "", False,
              "Not used directly; its SharedMemory is what the agents SPADRA runs are given."),
    AgentSpec("adaptive", "Adaptive Agent", "14.13", "adaptive_agent", "AdaptiveAgent", "", False, "Needs PyTorch (not importable where SPADRA was built); no service identified."),
    AgentSpec("learning", "Learning Agent", "14.13", "learning_agent", "LearningAgent", "", False, "Needs PyTorch (not importable where SPADRA was built); no service identified."),
    AgentSpec("observability", "Observability Agent", "14.13", "observability_agent", "ObservabilityAgent",
              "Summarises the latency of SPADRA's own agent calls (per agent and operation: count, mean, percentiles) for the agent health report", True),
    AgentSpec("network", "Network Agent", "14.13", "network_agent", "NetworkAgent", "", False, "Needs a websocket package (not importable where SPADRA was built); SPADRA is a loopback application."),
    AgentSpec("perception", "Perception Agent", "14.13", "perception_agent", "PerceptionAgent", "", False, "Needs PyTorch (not importable where SPADRA was built); no service identified."),
)
BY_KEY = {s.key: s for s in SPECS}


class Hub:
    """Loads the integrated agents once, on first use, and records every call SPADRA makes to one (so the assistant reports only real calls)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._agents: dict[str, Any] = {}
        self._reasons: dict[str, str] = {}
        self._memory: Any = None
        self.calls: list[dict[str, Any]] = []
        self._factories: dict[str, Callable[[Any], Any]] = {}

    def register_factory(self, key: str, factory: Callable[[Any], Any]) -> None:
        """How to build the agent with its own configuration (the bridges that need non-default construction register it)."""
        self._factories[key] = factory

    def _shared_memory(self) -> Any:
        if self._memory is None:
            self._memory = importlib.import_module(MEMORY_MODULE).SharedMemory()
        return self._memory

    def load(self, key: str) -> Any | None:
        spec = BY_KEY[key]
        with self._lock:
            if key in self._agents:
                return self._agents[key]
            if key in self._reasons:
                return None
            try:
                module = importlib.import_module(f"{AGENTS_PACKAGE}.{spec.module}")
                factory = self._factories.get(key)
                self._agents[key] = factory(module) if factory else getattr(module, spec.cls)(self._shared_memory(), None)
            except Exception as exc:
                self._reasons[key] = f"{type(exc).__name__}: {exc}"
                return None
            return self._agents[key]

    def require(self, key: str) -> Any:
        agent = self.load(key)
        if agent is None:
            raise DependencyMissing(f"The SLAI {BY_KEY[key].label} is not available", details={"reason": self._reasons.get(key)})
        return agent

    def reason(self, key: str) -> str | None:
        self.load(key)
        return self._reasons.get(key)

    def available(self, key: str) -> bool:
        return self.load(key) is not None

    def record(self, agent: str, operation: str, request_id: Any = None, ms: float | None = None, ok: bool = True) -> None:
        """One call SPADRA made to an agent: which, what for, its own request id, how long it took (ms) and whether it answered."""
        with self._lock:
            row: dict[str, Any] = {"agent": agent, "operation": operation, "request_id": request_id, "ok": bool(ok)}
            if ms is not None:
                row["ms"] = round(float(ms), 3)
            self.calls.append(row)
            del self.calls[:-200]

    def health(self) -> dict[str, Any]:
        """How SPADRA's agent calls have gone this session: per agent and operation the number of calls, failures and the latency (mean, 95th percentile,
        maximum), computed here from the recorded calls. When the SLAI Observability Agent is available it summarises the same latency samples and its
        figures are given beside these; its incident level is not reported, because it rates the synthetic trace it opens for a summary as an anomaly."""
        with self._lock:
            calls = list(self.calls)
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for c in calls:
            groups.setdefault((c["agent"], c["operation"]), []).append(c)
        rows = []
        for (agent, op), cs in sorted(groups.items()):
            ms = sorted(float(c["ms"]) for c in cs if "ms" in c)
            row: dict[str, Any] = {"agent": agent, "operation": op, "calls": len(cs), "failures": sum(1 for c in cs if not c.get("ok", True)), "timed": len(ms)}
            if ms:
                row.update({"mean_ms": round(sum(ms) / len(ms), 3), "p95_ms": round(ms[min(len(ms) - 1, math.ceil(0.95 * len(ms)) - 1)], 3), "max_ms": round(ms[-1], 3)})
            rows.append(row)
        out: dict[str, Any] = {"operations": rows, "calls": len(calls), "basis": "the last 200 agent calls SPADRA made in this session"}
        samples = [{"subject": f"{c['agent']} · {c['operation']}", "duration_ms": c["ms"], "status": "ok" if c.get("ok", True) else "error"} for c in calls if "ms" in c]
        agent = self.load("observability") if samples else None
        if agent is None:
            out["observability"] = {"used": False, "reason": "no timed calls yet" if not samples else (self._reasons.get("observability") or "not available")}
            return out
        t0 = time.perf_counter()
        try:
            report = agent.perform_task({"task_name": "spadra_agent_calls", "agent_name": "SPADRA", "operation_name": "agent_call_latency", "latencies": samples})
            subjects = (report.get("performance") or {}).get("subjects") or {}
            summary = {k: {"samples": v["latency"]["sample_count"], "mean_ms": v["latency"]["mean_ms"], "p95_ms": v["latency"]["percentiles_ms"].get("95"),
                           "max_ms": v["latency"]["max_ms"], "status": v["latency"].get("status_counts", {})}
                       for k, v in subjects.items() if isinstance(v, dict) and isinstance(v.get("latency"), dict) and v["latency"].get("sample_count") and k != "SPADRA"}
            out["observability"] = {"used": True, "agent": "SLAI Observability Agent", "subjects": summary, "pipeline_errors": len(report.get("pipeline_errors") or [])}
            self.record("Observability Agent", "latency summary", report.get("trace_id"), (time.perf_counter() - t0) * 1000)
        except Exception as exc:                                       # noqa: BLE001 - the health report never fails because an agent did
            out["observability"] = {"used": False, "reason": f"{type(exc).__name__}: {exc}"[:300]}
            self.record("Observability Agent", "latency summary", None, (time.perf_counter() - t0) * 1000, ok=False)
        return out

    def present(self, spec: AgentSpec) -> bool:
        """Whether this SLAI checkout contains the agent's module at all (without importing it)."""
        try:
            return importlib.util.find_spec(f"{AGENTS_PACKAGE}.{spec.module}") is not None
        except (ImportError, ValueError):
            return False

    def status(self) -> list[dict[str, Any]]:
        out = []
        for s in SPECS:
            row: dict[str, Any] = {"key": s.key, "label": s.label, "section": s.section, "integrated": s.integrated, "present": self.present(s)}
            if s.integrated:
                row["available"] = self.available(s.key)
                row["purpose"] = s.purpose
                if not row["available"]:
                    row["reason"] = self.reason(s.key) or "not importable"
            else:
                row["available"] = None                       # not imported: SPADRA has no adapter for it, so it is not asked
                row["why_not"] = s.why_not
            out.append(row)
        return out


# What each agent *role* named for parametric design, reconstruction and impact work does in SPADRA. The host owns state, the operation graph, execution and commits; an agent may only
# propose, analyse or verify, and a proposal is only ever applied by a person through ``assistant.consent`` / ``run`` (validated commands, one transaction, Undo).
ROLES: tuple[dict[str, Any], ...] = (
    {"role": "Language", "agent": "language", "used_for": "Would turn a request into a typed design specification. Not integrated; specifications are written or imported as data, and the "
     "deterministic kernel runs them with no model in the loop", "agent_calls": False},
    {"role": "Software", "agent": None, "used_for": "Not integrated. The SLAI Software Agent analyses source code (syntax trees, call and dependency graphs, patches); it did not import where SPADRA was built "
     "(ModuleNotFoundError: logs) and an operation graph of geometry is not a software artifact. Cycle detection and ordering of operations are done by SPADRA's own graph code", "agent_calls": False},
    {"role": "STEM", "agent": "stem", "used_for": "Unit conversion and formula checks used by the assistant and beam checks. Specification parameters are evaluated by the SGEO parameter graph, not by an agent", "agent_calls": True},
    {"role": "Spatial", "agent": "spatial", "used_for": "Connection and proximity queries between model elements (the connections view). Candidate collisions in an impact analysis are bounding-box tests made by SPADRA itself", "agent_calls": True},
    {"role": "Optimization", "agent": "optimization", "used_for": "Bounded beam-dimension optimisation, surfaced as an assistant proposal", "agent_calls": True},
    {"role": "Planning", "agent": "planning", "used_for": "Would decompose multi-step requests. Not integrated (needs PyTorch, which could not be imported where SPADRA was built); the construction order of a specification is computed by a deterministic topological sort", "agent_calls": False},
    {"role": "Verification", "agent": "verification", "used_for": "Not integrated: its own scope is formal objects. A specification is verified by running it on a throw-away copy and measuring (``spec.verify``); a repair is offered only after it re-verifies", "agent_calls": False},
    {"role": "Provenance", "agent": "provenance", "used_for": "Not integrated (not importable where SPADRA was built). Specification runs, reconstructions and previews carry their own provenance records (``spadra:spec``, ``spadra:recon``, the preview audit)", "agent_calls": False},
)


def roles() -> list[dict[str, Any]]:
    """The role table with each agent's availability probed now (an agent that is not integrated is not imported)."""
    out = []
    for r in ROLES:
        row = dict(r)
        key = r["agent"]
        row["present"] = bool(key) and HUB.present(BY_KEY[key])
        row["available"] = HUB.available(key) if key and r["agent_calls"] else None
        if row["available"] is False:
            row["reason"] = HUB.reason(key)
        out.append(row)
    return out


HUB = Hub()
