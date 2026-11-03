"""SPADRA's connection to the real SLAI Animation Agent (``src/agents/animation_agent.py``).

SPADRA defines no animation engine, no keyframe format and no interpolation of its own:

* the animation lives in the model's own SGEO scene (``slai.animation.v1`` / ``v2`` metadata), where SGEO validates it;
* every edit and every read is a request to the SLAI ``AnimationAgent``, which orchestrates SGEO's native commands and evaluator;
* the agent is bound to the model's native SGEO ``Scene`` only for the duration of one request (it treats a bound scene as a trusted,
  process-local provider, and SPADRA replaces its model document on undo, so a long-lived binding would go stale). The native scene is
  bound directly: the agent's ``SGEOAnimationAdapter`` wrapper cannot upgrade a scene to the v2 animation schema.

When the agent cannot be imported (a SLAI checkout without ``src/agents``, a missing dependency) the bridge says so and
animation stays unavailable: nothing here falls back to a copy of the agent's job.
"""
from __future__ import annotations

import importlib
import threading
from typing import Any, Mapping

from ..tools.errors import BadRequest, Conflict, DependencyMissing

AGENT_MODULE = "src.agents.animation_agent"

#: The agent's own answers to a request it refuses, mapped to the application's error classes.
_CONFLICT_NAMES = {"AnimationRevisionConflictError", "ConflictError"}


class AnimationBridge:
    """One agent per application, created on first use."""

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
                agent_cls = importlib.import_module(AGENT_MODULE).AnimationAgent
                self._agent = agent_cls()
            except Exception as exc:                                            # a missing module, dependency or configuration file
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

    def status(self) -> dict[str, Any]:
        self._load()
        if self._agent is None:
            return {"available": False, "reason": self._reason or "The SLAI Animation Agent is not available",
                    "hint": "Start SPADRA inside a SLAI checkout that contains src/agents/animation_agent.py and its dependencies"}
        info = self._agent.perform_task({"operation": "capabilities"})
        return {"available": True, "agent": "SLAI Animation Agent", "operations": info.get("operations", []), "unsupported": info.get("unsupported", [])}

    def _require(self) -> Any:
        self._load()
        if self._agent is None:
            raise DependencyMissing("The SLAI Animation Agent is not available", details={"reason": self._reason})
        return self._agent

    # ------------------------------------------------------------------------------------------------------------------ requests
    def request(self, document: Any, operation: str, payload: Mapping[str, Any] | None = None, *, dry_run: bool = False) -> dict[str, Any]:
        """Run one agent operation against a model document and return the agent's own result."""
        agent = self._require()
        scene = self._scene(document)
        with self._lock:                                                          # the agent keeps per-scene bindings; one request at a time
            scene_id = agent.bind_scene(scene)
            try:
                return agent.perform_task({"operation": operation, "scene_id": scene_id, "payload": dict(payload or {}), "dry_run": dry_run})
            except Exception as exc:
                raise _translate(exc) from exc
            finally:
                agent.unbind_scene(scene_id)

    def evaluate(self, document: Any, seconds: float) -> Any:
        """The detached scene state at ``seconds`` (SGEO's ``EvaluatedScene``), evaluated through the agent."""
        agent = self._require()
        scene = self._scene(document)
        with self._lock:
            scene_id = agent.bind_scene(scene)
            try:
                return agent.evaluate_state(scene_id, float(seconds))
            except Exception as exc:
                raise _translate(exc) from exc
            finally:
                agent.unbind_scene(scene_id)

    @staticmethod
    def _scene(document: Any) -> Any:
        scene = getattr(document, "_model", None)
        if scene is None:
            raise BadRequest("The model has no native SGEO scene to animate")
        return scene


#: The one bridge the application uses.
BRIDGE = AnimationBridge()


def _translate(exc: Exception) -> Exception:
    """The agent and SGEO raise their own typed errors; the application reports them as ordinary 4xx answers."""
    name = type(exc).__name__
    if name in _CONFLICT_NAMES:
        return Conflict(str(exc))
    module = type(exc).__module__
    if module.startswith(("src.agents", "src.utils.spatial_formats")):
        return BadRequest(str(exc) or name, details={"error": name})
    return exc


#: The edit operations the agent accepts, as it reports them (used to validate a request before it reaches the agent).
def edit_operations(bridge: AnimationBridge) -> list[str]:
    status = bridge.status()
    reads = {"capabilities", "status", "evaluate", "evaluate_many", "evaluate_frames", "get_animation", "recent_edits", "edit_counts",
             "playback_status", "play", "pause", "stop", "seek", "advance", "set_playback_rate", "set_loop"}
    return sorted(op for op in status.get("operations", []) if op not in reads)
