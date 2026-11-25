"""SPADRA's view of the SLAI agent ecosystem (master prompt 14).

SPADRA does not copy agent code. Loading, availability, recorded calls and call timing come from the one hub every SLAI application shares
(``applications/tools/agents.py``); this module adds only what is SPADRA's own: which agents SPADRA talks to and why the others are not
used, the agent role table, and the health report with the SLAI Observability Agent. An agent is *integrated* only where it gives SPADRA a
service that was run and tested, and it is *available* only if it really imports on the computer SPADRA runs on.

A reason for **not** using an agent is written only from a decision (what SPADRA does instead) or from what SLAI's own code or documentation says
the agent is for. Whether an agent imports on a given computer is never written down here, because it differs from one computer to the next and
a sentence copied from the machine SPADRA was built on goes stale. ``python -m applications.SPADRA --check`` observes it, on the computer in front of you.

SPADRA builds its agents directly and calls ``perform_task``. It does not go through ``AgentFactory`` and does not call ``BaseAgent.execute``, so the
SLAI Governance Agent (which installs a gate on ``execute``) is neither started nor consulted by SPADRA; see the ``governance`` row.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any

from ..tools.agents import AGENTS_PACKAGE, MEMORY_MODULE, AgentHub
from ..tools.agents import AgentSpec as SharedSpec

__all__ = ["AGENTS_PACKAGE", "MEMORY_MODULE", "AgentSpec", "SPECS", "BY_KEY", "Hub", "HUB", "ROLES", "roles"]


@dataclass(frozen=True)
class AgentSpec(SharedSpec):
    """The shared spec (key, label, module, cls, purpose) plus what SPADRA records about each agent."""
    section: str = ""           # the master-prompt section that names it ("" where none does)
    integrated: bool = False
    why_not: str = ""           # when not integrated: the decision, or what the agent's own documentation says it is for


SPECS: tuple[AgentSpec, ...] = (
    AgentSpec(key="stem", label="STEM Agent", section="14.3", module="stem_agent", cls="STEMAgent", integrated=True,
              purpose="Unit conversion, parameter formulas with units, constraint checks, beam response and the numerical integrator used by simulations"),
    AgentSpec(key="simulation", label="Simulation Agent", section="14.4", module="simulation_agent", cls="SimulationAgent", integrated=True,
              purpose="Runs the beam dynamic-response simulation (rollout with a supplied state-transition model); keeps the run's seed, steps and lineage"),
    AgentSpec(key="spatial", label="Spatial Agent", section="14.2", module="spatial_agent", cls="SpatialAgent", integrated=True,
              purpose="Proximity, contact and relation queries between model elements, distances and collision tests on their bounding boxes"),
    AgentSpec(key="optimization", label="Optimization Agent", section="14.9", module="optimization_agent", cls="OptimizationAgent", integrated=True,
              purpose="Bounded optimisation of one beam dimension against a stated objective and constraint, evaluated with the STEM Agent"),
    AgentSpec(key="animation", label="Animation Agent", section="14.6", module="animation_agent", cls="AnimationAgent", integrated=True,
              purpose="Every animation edit and the evaluation of motion (see the timeline)"),
    AgentSpec(key="language", label="Language Agent", section="14.5", module="language_agent", cls="LanguageAgent",
              why_not="No SPADRA feature is built on it. The assistant's interpreter is a fixed set of command patterns, not a language model, and it says so "
                      "(GET /api/assistant reports language_model: false)."),
    AgentSpec(key="planning", label="Planning Agent", section="14.7", module="planning_agent", cls="PlanningAgent",
              why_not="SPADRA does not hand multi-step requests to the Planning Agent: the construction order of a specification is a deterministic topological sort. "
                      "Separately, SPADRA does use the Planning package's commit adapters (src/agents/planning/project_adapter.py, commit_reconciliation.py and "
                      "workflow_runtime.py) to carry every approved assistant proposal; that is not this agent."),
    AgentSpec(key="reasoning", label="Reasoning Agent", section="14.8", module="reasoning_agent", cls="ReasoningAgent",
              why_not="No verified service for SPADRA was identified. Model rules are checked by SPADRA's own validators (the model check and the layered validation)."),
    AgentSpec(key="verification", label="Verification Agent", section="14.10", module="verification_agent", cls="VerificationAgent",
              why_not="Its own documentation limits it to formal objects (contracts, SAT/SMT, explicit-state model checking). Proposals are validated by dry-running them "
                      "on a copy of the project through SPADRA's own command validators, which is what checks a real edit."),
    AgentSpec(key="knowledge", label="Knowledge Agent", section="14.11", module="knowledge_agent", cls="KnowledgeAgent",
              why_not="SPADRA has no document store or retrieval feature to attach it to, and adds no knowledge store of its own."),
    AgentSpec(key="reader", label="Reader Agent", section="14.12", module="reader_agent", cls="ReaderAgent",
              why_not="SPADRA has no document-ingestion feature to attach it to yet; nothing was built on it. 2D-to-BIM reconstruction reads PDFs and images with its own pipeline."),
    AgentSpec(key="quality", label="Quality Agent", section="14.13", module="quality_agent", cls="QualityAgent", why_not="No verified service for SPADRA was identified."),
    AgentSpec(key="evaluation", label="Evaluation Agent", section="14.13", module="evaluation_agent", cls="EvaluationAgent", why_not="No verified service for SPADRA was identified."),
    AgentSpec(key="provenance", label="Provenance Agent", section="14.13", module="provenance_agent", cls="ProvenanceAgent",
              why_not="Specification runs, reconstructions, previews, simulations and optimisations carry their own provenance record (spadra:spec, spadra:recon, "
                      "the preview audit, the run's seed, steps and lineage); no verified service for the agent's lineage store was identified."),
    AgentSpec(key="safety", label="Safety Agent", section="14.13", module="safety_agent", cls="SafetyAgent", why_not="No verified service for SPADRA was identified."),
    AgentSpec(key="privacy", label="Privacy Agent", section="14.13", module="privacy_agent", cls="PrivacyAgent",
              why_not="No verified service identified; the assistant sends agents only the selection it is given."),
    AgentSpec(key="governance", label="Governance Agent", module="governance_agent", cls="GovernanceAgent",
              why_not="Not used on purpose. SLAI installs its governance gate on BaseAgent.execute; SPADRA calls perform_task on agents it builds itself, so its calls do not "
                      "pass that gate and SPADRA does not start the agent. What SPADRA sends agents is geometry and engineering quantities, and a simulation makes one "
                      "STEM call per step: running every call through a gate that evaluates and durably records it would add cost, and no service SPADRA needs."),
    AgentSpec(key="alignment", label="Alignment Agent", section="14.13", module="alignment_agent", cls="AlignmentAgent",
              why_not="No verified service for SPADRA was identified. SLAI's own dependency profile lists PyTorch as required for it (AgentFactory.DEFAULT_DEPENDENCY_PROFILES)."),
    AgentSpec(key="execution", label="Execution Agent", section="14.13", module="execution_agent", cls="ExecutionAgent",
              why_not="Not used on purpose: every change must run through SPADRA's validated commands, never through an agent that executes."),
    AgentSpec(key="handler", label="Handler Agent", section="14.13", module="handler_agent", cls="HandlerAgent", why_not="No verified service identified."),
    AgentSpec(key="collaborative", label="Collaborative Agent", section="14.13", module="collaborative_agent", cls="CollaborativeAgent",
              why_not="Not used directly; its SharedMemory is what the agents SPADRA runs are given."),
    AgentSpec(key="adaptive", label="Adaptive Agent", section="14.13", module="adaptive_agent", cls="AdaptiveAgent",
              why_not="No verified service for SPADRA was identified. SLAI's own dependency profile lists PyTorch as required for it (AgentFactory.DEFAULT_DEPENDENCY_PROFILES)."),
    AgentSpec(key="learning", label="Learning Agent", section="14.13", module="learning_agent", cls="LearningAgent",
              why_not="No verified service for SPADRA was identified. SLAI's own dependency profile lists PyTorch as required for it (AgentFactory.DEFAULT_DEPENDENCY_PROFILES)."),
    AgentSpec(key="observability", label="Observability Agent", section="14.13", module="observability_agent", cls="ObservabilityAgent", integrated=True,
              purpose="Summarises the latency of SPADRA's own agent calls (per agent and operation: count, mean, percentiles) for the agent health report"),
    AgentSpec(key="network", label="Network Agent", section="14.13", module="network_agent", cls="NetworkAgent",
              why_not="SPADRA is a loopback application; there is nothing for it to relay."),
    AgentSpec(key="perception", label="Perception Agent", section="14.13", module="perception_agent", cls="PerceptionAgent",
              why_not="No verified service for SPADRA was identified. SLAI's own dependency profile lists PyTorch as required for it (AgentFactory.DEFAULT_DEPENDENCY_PROFILES)."),
    AgentSpec(key="software", label="Software Agent", module="software_agent", cls="SoftwareAgent",
              why_not="It analyses source code (symbols, call and dependency graphs, patches); an operation graph of geometry is not a software artifact. Cycle detection and "
                      "ordering of operations are done by SPADRA's own graph code."),
)
BY_KEY = {s.key: s for s in SPECS}


def _build_stem(module: Any) -> Any:
    """The STEM Agent with a SharedMemory of its own, exactly as SPADRA's engineering bridge has always built it (so the hub and the bridge hold one agent, not two)."""
    return module.STEMAgent(importlib.import_module(MEMORY_MODULE).SharedMemory(), None)


def _build_animation(module: Any) -> Any:
    """The Animation Agent without a shared memory, exactly as SPADRA's animation bridge has always built it (the bridge binds each scene itself)."""
    return module.AnimationAgent()


class Hub(AgentHub):
    """The shared hub plus SPADRA's specification table. Loads the integrated agents once, on first use, and records every call SPADRA makes to one
    (so the assistant reports only real calls)."""

    def __init__(self) -> None:
        super().__init__(SPECS)
        self.register_factory("stem", _build_stem)
        self.register_factory("animation", _build_animation)

    def health(self) -> dict[str, Any]:
        """How SPADRA's agent calls have gone this session: per agent and operation the number of calls, failures and the latency (mean, 95th percentile,
        maximum), computed from the recorded calls. When the SLAI Observability Agent is available it summarises the same latency samples and its
        figures are given beside these; its incident level is not reported, because it rates the synthetic trace it opens for a summary as an anomaly."""
        out = super().health()
        out["basis"] = "the last 200 agent calls SPADRA made in this session"
        with self._lock:
            calls = list(self.calls)
        samples = [{"subject": f"{c['agent']} · {c['operation']}", "duration_ms": c["ms"], "status": "ok" if c.get("ok", True) else "error"} for c in calls if "ms" in c]
        agent = self.load("observability") if samples else None
        if agent is None:
            out["observability"] = {"used": False, "reason": "no timed calls yet" if not samples else (self._reasons.get("observability") or "not available")}
            return out
        try:
            with self.timed("Observability Agent", "latency summary") as call:
                report = agent.perform_task({"task_name": "spadra_agent_calls", "agent_name": "SPADRA", "operation_name": "agent_call_latency", "latencies": samples})
                call["request_id"] = report.get("trace_id")
            subjects = (report.get("performance") or {}).get("subjects") or {}
            summary = {k: {"samples": v["latency"]["sample_count"], "mean_ms": v["latency"]["mean_ms"], "p95_ms": v["latency"]["percentiles_ms"].get("95"),
                           "max_ms": v["latency"]["max_ms"], "status": v["latency"].get("status_counts", {})}
                       for k, v in subjects.items() if isinstance(v, dict) and isinstance(v.get("latency"), dict) and v["latency"].get("sample_count") and k != "SPADRA"}
            out["observability"] = {"used": True, "agent": "SLAI Observability Agent", "subjects": summary, "pipeline_errors": len(report.get("pipeline_errors") or [])}
        except Exception as exc:                                       # noqa: BLE001 - the health report never fails because an agent did
            out["observability"] = {"used": False, "reason": f"{type(exc).__name__}: {exc}"[:300]}
        return out

    def status(self) -> list[dict[str, Any]]:
        out = []
        for s in SPECS:
            row: dict[str, Any] = {"key": s.key, "label": s.label, "section": s.section, "integrated": s.integrated, "present": self.present(s.key)}
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
# For a role whose agent SPADRA does not use, ``used_for`` says what the role would be and what SPADRA does instead; the reason itself is the agent's ``why_not`` above, given once.
ROLES: tuple[dict[str, Any], ...] = (
    {"role": "Language", "agent": "language", "used_for": "Would turn a request into a typed design specification. Specifications are written or imported as data instead, and the "
     "deterministic kernel runs them with no model in the loop", "agent_calls": False},
    {"role": "Software", "agent": "software", "used_for": "Would analyse source code. Cycle detection and ordering of the operations in an operation graph are done by SPADRA's own graph code", "agent_calls": False},
    {"role": "STEM", "agent": "stem", "used_for": "Unit conversion and formula checks used by the assistant and beam checks. Specification parameters are evaluated by the SGEO parameter graph, not by an agent", "agent_calls": True},
    {"role": "Spatial", "agent": "spatial", "used_for": "Connection and proximity queries between model elements (the connections view). Candidate collisions in an impact analysis are bounding-box tests made by SPADRA itself", "agent_calls": True},
    {"role": "Optimization", "agent": "optimization", "used_for": "Bounded beam-dimension optimisation, surfaced as an assistant proposal", "agent_calls": True},
    {"role": "Planning", "agent": "planning", "used_for": "Would decompose multi-step requests. The construction order of a specification is computed by a deterministic topological sort instead", "agent_calls": False},
    {"role": "Verification", "agent": "verification", "used_for": "Would verify a specification. A specification is verified by running it on a throw-away copy and measuring (``spec.verify``); a repair is offered only after it re-verifies", "agent_calls": False},
    {"role": "Provenance", "agent": "provenance", "used_for": "Would record lineage. Specification runs, reconstructions and previews carry their own provenance records (``spadra:spec``, ``spadra:recon``, the preview audit)", "agent_calls": False},
)


def roles() -> list[dict[str, Any]]:
    """The role table with each agent's availability probed now (an agent that is not integrated is not imported)."""
    out = []
    for r in ROLES:
        row = dict(r)
        key = r["agent"]
        row["present"] = bool(key) and HUB.present(key)
        row["available"] = HUB.available(key) if key and r["agent_calls"] else None
        if row["available"] is False:
            row["reason"] = HUB.reason(key)
        if key and not r["agent_calls"]:
            row["why_not"] = BY_KEY[key].why_not
        out.append(row)
    return out


HUB = Hub()
