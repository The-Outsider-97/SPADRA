"""SPADRA's connection to the real SLAI Spatial Agent (``src/agents/spatial_agent.py``) for spatial questions about the model (master prompt 14.2).

The model's elements are given to the agent as entities (identity, centre, and an axis-aligned bounding box in metres, taken from SGEO's own
world bounds), and the agent answers: which elements touch, overlap or contain one another, which are within a distance, how far apart two
elements are, and whether two boxes collide. Everything returned is the agent's answer for those boxes.

Be clear about what a bounding box is: a wall that runs diagonally has a box much larger than the wall, so "touching" and "overlapping" here mean
*the boxes* do. Every result says so. Relationships that the model itself states (a door or window is hosted by a wall; a wall bounds a room) are not
guessed from boxes: they come from the elements' parameters and are listed separately, as such.
"""
from __future__ import annotations

import threading
from typing import Any, Mapping

from ..tools.errors import BadRequest, DependencyMissing
from ..tools.validation import number, text

from . import elements as E
from .agent_hub import HUB

RELATIONS = ("touches", "overlaps", "contains", "inside", "intersects")
MAX_ELEMENTS = 5000
NOTE = ("Spatial relations are computed by the SLAI Spatial Agent on axis-aligned bounding boxes of the elements, so for diagonal or L-shaped elements they are "
        "conservative: boxes can touch or overlap where the elements themselves do not. Relationships stated by the model (hosting, room boundaries) are listed separately.")


class SpatialBridge:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._stamp: tuple[Any, int] | None = None
        self._ids: set[str] = set()

    def status(self) -> dict[str, Any]:
        ok = HUB.available("spatial")
        out: dict[str, Any] = {"available": ok}
        if ok:
            out["operations"] = list(HUB.require("spatial").capabilities().get("operations", []))
        else:
            out["reason"] = HUB.reason("spatial")
        return out

    def _task(self, payload: Mapping[str, Any]) -> Any:
        agent = HUB.require("spatial")
        try:
            answer = agent.perform_task(dict(payload))
        except Exception as exc:
            if type(exc).__module__.startswith("src.agents"):
                raise BadRequest(str(exc) or type(exc).__name__, details={"error": type(exc).__name__}) from exc
            raise
        HUB.record("Spatial Agent", str(payload.get("operation")), answer.get("request_id") if isinstance(answer, Mapping) else None)
        if not isinstance(answer, Mapping) or answer.get("status") != "ok":
            raise BadRequest("The Spatial Agent did not return a result", details={"answer": str(answer)[:400]})
        return answer["result"]

    def sync(self, project: Any) -> dict[str, dict[str, Any]]:
        """Give the agent the model as it is now: one entity per element that has geometry. Returns what the model says about each."""
        raw = project.model._model
        bounds = raw.bounds_by_object()
        info: dict[str, dict[str, Any]] = {}
        for oid in project.model.query_ids(has_geometry=True):
            obj = project.model.get(oid)
            meta = obj["metadata"]
            info[oid] = {"id": oid, "name": obj["name"], "category": E.m(meta, "category", "generic"), "type": E.m(meta, "type", "")}
        if len(info) > MAX_ELEMENTS:
            raise BadRequest(f"The model has {len(info)} elements; spatial queries are limited to {MAX_ELEMENTS}")
        stamp = (project.manifest["id"], project.revision)
        with self._lock:
            if self._stamp == stamp and self._ids == set(info):
                return info
            for gone in self._ids - set(info):
                self._task({"operation": "remove_entity", "entity_id": gone})
            for oid in info:
                lo, hi = (list(map(float, bounds[oid][0])), list(map(float, bounds[oid][1])))
                self._task({"operation": "upsert_entity", "entity": {
                    "entity_id": oid, "position": [(a + b) / 2.0 for a, b in zip(lo, hi)], "bounds": {"minimum": lo, "maximum": hi},
                    "geometry": {"type": "aabb", "minimum": lo, "maximum": hi}, "metadata": {"category": info[oid]["category"]}}})
            self._stamp, self._ids = stamp, set(info)
        return info

    @staticmethod
    def _label(info: Mapping[str, Mapping[str, Any]], oid: str) -> dict[str, Any]:
        row = info.get(oid, {})
        return {"id": oid, "name": row.get("name", oid), "category": row.get("category", "")}

    # --------------------------------------------------------------------------------------------------------------------- queries
    def connected(self, project: Any, oid: str) -> dict[str, Any]:
        """Elements whose boxes touch, overlap, contain or lie inside the box of ``oid`` (agent), and the elements the model itself says are attached."""
        info = self.sync(project)
        if oid not in info:
            raise BadRequest("That element has no geometry to query")
        found: dict[str, list[str]] = {}
        for relation in RELATIONS:
            result = self._task({"operation": "relation_query", "entity": oid, "relation": relation})
            for other in result["entity_ids"]:
                if other != oid:
                    found.setdefault(other, []).append(relation)
        meta = E.meta_of(project, oid)
        stated: list[dict[str, Any]] = []
        category = E.m(meta, "category")
        if category == "wall":
            for hid in E.hosted(project, oid):
                stated.append({**self._label(info, hid), "relation": "hosted by this wall (the model's own parameters)"})
        elif category in E.OPENINGS:
            host = E.m(meta, "host")
            if host in info:
                stated.append({**self._label(info, host), "relation": "the wall that hosts it (the model's own parameters)"})
        return {"id": oid, "name": info[oid]["name"], "revision": project.revision, "agent": "SLAI Spatial Agent", "basis": "axis-aligned bounding boxes",
                "boxes": [{**self._label(info, other), "relations": sorted(rels)} for other, rels in sorted(found.items())], "stated_by_model": stated, "note": NOTE}

    def nearby(self, project: Any, oid: str, radius_m: float) -> dict[str, Any]:
        info = self.sync(project)
        if oid not in info:
            raise BadRequest("That element has no geometry to query")
        radius = float(number(radius_m, "radius", 0.001, 100000))
        entity = self._task({"operation": "get_entity", "entity_id": oid})["entity"]
        centre = entity["position"]
        result = self._task({"operation": "within_radius", "point": centre, "radius": radius})
        rows = [{**self._label(info, other), "centre_distance_m": float(d)} for other, d in zip(result["entity_ids"], result["distances"]) if other != oid]
        return {"id": oid, "name": info[oid]["name"], "revision": project.revision, "agent": "SLAI Spatial Agent", "radius_m": radius, "basis": "distance from the element's centre to each box",
                "elements": sorted(rows, key=lambda r: r["centre_distance_m"]), "note": NOTE}

    def between(self, project: Any, a: str, b: str) -> dict[str, Any]:
        info = self.sync(project)
        for oid in (a, b):
            if oid not in info:
                raise BadRequest("Both elements need geometry")
        distance = self._task({"operation": "distance", "source": a, "target": b})
        collision = self._task({"operation": "collision", "source": a, "target": b})
        relations = self._task({"operation": "relation", "relation": "all", "source": a, "target": b})["relationships"]
        return {"a": self._label(info, a), "b": self._label(info, b), "revision": project.revision, "agent": "SLAI Spatial Agent", "basis": "axis-aligned bounding boxes",
                "centre_distance_m": float(distance["distance"]), "box_gap_m": float(collision["distance"]), "boxes_collide": bool(collision["collides"]),
                "relations": [str(r["relation"].value if hasattr(r["relation"], "value") else r["relation"]) for r in relations if r["value"] is True],
                "note": NOTE}


BRIDGE = SpatialBridge()
