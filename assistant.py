"""The SLAI Assistant (master prompt section 15): model-aware requests turned into typed operations or verified read-only queries.

What this is, plainly
---------------------
The interpreter is a **pattern interpreter**: it recognises a fixed set of request forms (listed in ``CAPABILITIES``), reads the quantities and units out of
them, and builds a structured operation. It is **not** a language model - the SLAI Language Agent is not used - so a request outside those forms is answered
"unsupported" with the forms it does understand, never guessed at. The engineering done *for* a request is real: figures come from the model (SGEO),
the STEM, Spatial, Simulation and Optimization agents, and each answer lists the agent calls that were actually made.

The pipeline (15.3)
-------------------
request -> context collection -> intent interpretation -> capability routing -> structured proposal -> validation (a dry run on a scratch copy of the
project) -> preview (before / after of the affected figures) -> authorisation (the person approves) -> deterministic execution (the project's own commands,
one revision each) -> verification (the changed parameters are read back) -> result.

* read-only requests are answered at once and change nothing;
* anything that would change the project becomes a **proposal** and waits. Approve, reject and cancel are the person's; undo reverts an approved change;
* a proposal records the model revision it was made for. If the project has moved on, approving it is refused (409) and the proposal is marked stale:
  an out-of-date suggestion is never applied silently;
* high-risk proposals (deletions, or changes to many elements) need a second, explicit confirmation;
* if a step fails part-way, the steps already applied are undone, so a proposal applies in full or not at all;
* nothing here mutates the model except through ``Project.execute`` (validated commands with snapshot / rollback).

Messages and proposals are kept in memory for the open project (they are a working log, not a record, and are not written into the project file).
"""
from __future__ import annotations

import math
import re
import threading
import time
from typing import Any, Callable, Mapping, Sequence

from ..tools.errors import AppError, BadRequest, Conflict, NotFound, DependencyMissing
from ..tools.validation import text

from . import calculator as C
from . import elements as E
from . import optimization_bridge as OB
from . import simulation_bridge as SB
from . import structural as S
from .agent_hub import HUB
from .spatial_bridge import BRIDGE as SPATIAL
from .project import MM_PER, new_id

INTERPRETER = {"name": "SPADRA pattern interpreter", "language_model": False,
               "note": "Recognises a fixed set of request forms. It is not a language model and does not guess at other wording."}
KINDS = ("success", "proposal", "explanation", "analysis", "calculation", "unsupported", "failed", "cancelled")
MAX_MESSAGES = 200
MAX_SELECTION = 50
HIGH_RISK_ELEMENTS = 6
CAPABILITIES = [
    {"id": "create_wall", "kind": "proposal", "example": "Create a wall 5 meters long, 200 mm thick and 3 meters high", "needs": "nothing selected; place it with 'from (0, 0) to (5, 0)' (metres)"},
    {"id": "set_property", "kind": "proposal", "example": "Change the selected wall height to 3.2 meters", "needs": "a selection that has that parameter"},
    {"id": "move", "kind": "proposal", "example": "Move the selected element by 1.5 m in x", "needs": "a selection"},
    {"id": "delete", "kind": "proposal (high risk)", "example": "Delete the selected elements", "needs": "a selection"},
    {"id": "section", "kind": "proposal", "example": "Generate a section through the selected objects", "needs": "a selection"},
    {"id": "animate", "kind": "proposal", "example": "Create an animation moving the selected object from (0, 0, 0) to (4, 0, 0) in 3 seconds", "needs": "one selected element and the SLAI Animation Agent"},
    {"id": "measure", "kind": "calculation", "example": "Calculate the area of the selected room", "needs": "one or two selected elements"},
    {"id": "connected", "kind": "analysis", "example": "Find all elements connected to this wall", "needs": "one selected element and the SLAI Spatial Agent"},
    {"id": "explain", "kind": "explanation", "example": "Explain the selected element's parameters", "needs": "a selection"},
    {"id": "check", "kind": "analysis", "example": "Check whether the selected elements contain inconsistent dimensions", "needs": "nothing, or a selection"},
    {"id": "simulate", "kind": "analysis", "example": "Simulate the selected beam, simply supported, uniform load 10 kN/m, damping 2%", "needs": "a beam, its support and its load; the Simulation and STEM agents"},
    {"id": "optimize", "kind": "proposal", "example": "Optimize the depth of the selected beam between 200 and 600 mm, simply supported, uniform load 10 kN/m", "needs": "a beam, bounds, support and load; the Optimization and STEM agents"},
]

_NUM = r"(-?\d+(?:[.,]\d+)?)"
_UNIT = r"(mm|millimet(?:er|re)s?|cm|centimet(?:er|re)s?|m|met(?:er|re)s?)"
QTY = _NUM + r"\s*" + _UNIT + r"\b"
_FACTOR = {"mm": 0.001, "cm": 0.01, "m": 1.0}


def _unit_key(word: str) -> str:
    w = word.lower()
    return "mm" if w.startswith("mill") or w == "mm" else "cm" if w.startswith("cent") or w == "cm" else "m"


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def _metres(value: str, unit: str) -> float:
    return _num(value) * _FACTOR[_unit_key(unit)]


def _attr(t: str, after: str, before: str) -> float | None:
    """A length in metres written either as '5 m long' (``after``) or 'length of 5 m' (``before``)."""
    m = re.search(QTY + r"\s*(?:" + after + r")\b", t) or re.search(r"\b(?:" + before + r")\s*(?:of|is|=|:)?\s*" + QTY, t)
    return _metres(m.group(1), m.group(2)) if m else None


_POINT = r"\(?\s*" + _NUM + r"\s*,\s*" + _NUM + r"(?:\s*,\s*" + _NUM + r")?\s*\)?"


def _points(t: str) -> list[list[float]]:
    return [[_num(g) if g is not None else 0.0 for g in m.groups()] for m in re.finditer(_POINT, t)]


def _to_unit(value: float, unit: str, target: str) -> float:
    """Metres-based conversion between the length units a parameter row can have (m, mm); anything else is refused rather than guessed."""
    if target not in ("m", "mm"):
        raise BadRequest(f"A value in {unit} cannot be entered for a parameter in {target}")
    return value / _FACTOR[target]


class Assistant:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.proposals: dict[str, dict[str, Any]] = {}
        self._project_id: str | None = None
        self._lock = threading.RLock()

    # --------------------------------------------------------------------------------------------------------------- state
    def _bind(self, project: Any) -> None:
        if self._project_id != project.manifest["id"]:
            self.messages, self.proposals, self._project_id = [], {}, project.manifest["id"]

    def history(self, project: Any) -> dict[str, Any]:
        with self._lock:
            self._bind(project)
            self._mark_stale(project)
            return {"revision": project.revision, "interpreter": INTERPRETER, "capabilities": CAPABILITIES, "messages": list(self.messages)}

    def _mark_stale(self, project: Any) -> None:
        for p in self.proposals.values():
            if p["status"] == "pending" and p["revision"] != project.revision:
                p["status"] = "stale"
                p["status_note"] = f"The project changed (revision {project.revision}); this proposal was made for revision {p['revision']}."

    def _post(self, project: Any, request: str | None, kind: str, body: str, context: Mapping[str, Any], calls_from: int, **extra: Any) -> dict[str, Any]:
        assert kind in KINDS
        msg = {"id": new_id(), "kind": kind, "request": request, "text": body, "revision": project.revision, "context": dict(context), "created": time.time(),
               "interpreter": INTERPRETER["name"], "agents": [dict(c) for c in HUB.calls[calls_from:]], **extra}
        self.messages.append(msg)
        del self.messages[:-MAX_MESSAGES]
        return msg

    # -------------------------------------------------------------------------------------------------------------- context
    @staticmethod
    def collect_context(project: Any, ctx: Mapping[str, Any] | None) -> dict[str, Any]:
        """Only what the request needs: the selected elements (id, name, category), the view and level, the unit and the revision."""
        ctx = ctx or {}
        raw = ctx.get("selection") or []
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or len(raw) > MAX_SELECTION:
            raise BadRequest(f"The selection must be a list of at most {MAX_SELECTION} element ids")
        selection = []
        for oid in dict.fromkeys(text(i, "selection id", 80) for i in raw):
            if not E.alive(project, oid):
                continue                                       # selected in the client but no longer in the model
            obj = project.model.get(oid)
            selection.append({"id": oid, "name": obj["name"], "category": E.m(obj["metadata"], "category", "generic")})
        view = ctx.get("view")
        level = ctx.get("level")
        known_levels = {l["id"] for l in project.manifest["levels"]}
        return {"project": project.name, "unit": project.manifest["unit"], "revision": project.revision, "selection": selection,
                "view": view if isinstance(view, str) and any(v["id"] == view for v in project.manifest["views"]) else None,
                "level": level if level in known_levels else None}

    # ------------------------------------------------------------------------------------------------------------------ ask
    def ask(self, project: Any, request: str, ctx: Mapping[str, Any] | None = None) -> dict[str, Any]:
        request = text(request, "request", 600)
        with self._lock:
            self._bind(project)
            self._mark_stale(project)
            context = self.collect_context(project, ctx)
            mark = len(HUB.calls)
            t = " ".join(request.lower().split())
            try:
                for name, matcher, handler in self._routes():
                    if matcher(t):
                        return handler(project, request, t, context, mark)
                return self._unsupported(project, request, context, mark)
            except AppError as exc:
                return self._post(project, request, "failed", exc.message + (f" {exc.hint}" if getattr(exc, "hint", None) else ""), context, mark,
                                  error={"code": getattr(exc, "code", None), "details": getattr(exc, "details", None)})

    def _routes(self) -> list[tuple[str, Callable[[str], bool], Callable[..., dict[str, Any]]]]:
        has = lambda *words: (lambda t: any(re.search(w, t) for w in words))
        return [
            ("simulate", has(r"\bsimulat", r"\bvibrat", r"\bdynamic response"), self._simulate),
            ("optimize", has(r"\boptimi[sz]", r"\bsmallest (?:depth|width)", r"\bminimi[sz]e the (?:beam|volume)"), self._optimize),
            ("create_wall", lambda t: bool(re.search(r"\b(?:create|add|make|draw|build|place)\b", t)) and "wall" in t and not re.search(r"\b(?:selected|section|animation)\b", t), self._create_wall),
            ("delete", has(r"\b(?:delete|remove|erase)\b"), self._delete),
            ("section", lambda t: "section" in t and bool(re.search(r"\b(?:generate|create|add|make|cut)\b", t)), self._section),
            ("animate", has(r"\banimat"), self._animate),
            ("move", has(r"\bmove\b"), self._move),
            ("connected", has(r"\bconnected\b", r"\btouching\b", r"\badjacent\b", r"\bneighbou?rs?\b", r"\bnearby\b", r"\bcolliding\b"), self._connected),
            ("check", has(r"\bcheck\b", r"\binconsisten", r"\bvalidate\b", r"\bverify\b"), self._check),
            ("explain", has(r"\bexplain\b", r"\bdescribe\b", r"\bwhat is\b", r"\btell me about\b"), self._explain),
            ("measure", has(r"\b(?:area|perimeter|volume|mass|weight|distance|measure|calculate|how (?:big|long|far))\b"), self._measure),
            ("set_property", has(r"\b(?:change|set|make|update|adjust|resize)\b"), self._set_property),
        ]

    def _unsupported(self, project: Any, request: str, context: Mapping[str, Any], mark: int) -> dict[str, Any]:
        lines = "; ".join(f"“{c['example']}”" for c in CAPABILITIES[:6])
        return self._post(project, request, "unsupported",
                          "I could not match that to something I can do reliably. I am a pattern interpreter, not a language model, so I only act on the request forms "
                          f"listed here, for example {lines}. Nothing was changed.", context, mark, capabilities=CAPABILITIES)

    # -------------------------------------------------------------------------------------------------------- read-only answers
    def _need_selection(self, context: Mapping[str, Any], n: int | None = None, what: str = "an element") -> list[dict[str, Any]]:
        sel = context["selection"]
        if not sel:
            raise BadRequest(f"Select {what} first; the request refers to the selection")
        if n is not None and len(sel) != n:
            raise BadRequest(f"Select exactly {n} element{'s' if n != 1 else ''} (you have {len(sel)} selected)")
        return sel

    def _measure(self, project, request, t, context, mark):
        sel = self._need_selection(context, None, "one or two elements")
        if len(sel) > 2:
            raise BadRequest("Select one element for its figures, or two for the distance between them")
        out = C.measure(project, [s["id"] for s in sel])
        wanted = [w for w in ("area", "perimeter", "volume", "mass", "surface", "distance", "centre", "center", "gap") if w in t]
        shown = [f for f in out["figures"] if not wanted or any(w.replace("center", "centre") in f["label"].lower() for w in wanted)] or out["figures"]
        lines = [f"{f['label']}: {f['value']:.4g} {f['unit']}".strip() if isinstance(f["value"], (int, float)) and not isinstance(f["value"], bool) else f"{f['label']}: {f['value']} {f['unit']}".strip() for f in shown]
        return self._post(project, request, "calculation", "; ".join(lines) + ". Computed from the model's own geometry and parameters" +
                          (f" and the {out['agent']}" if out.get("agent") else "") + f" at revision {project.revision}.", context, mark, data={**out, "figures": shown})

    def _connected(self, project, request, t, context, mark):
        sel = self._need_selection(context, 1, "one element")
        radius = None
        m = re.search(r"within\s+" + QTY, t)
        if m:
            radius = _metres(m.group(1), m.group(2))
        out = SPATIAL.nearby(project, sel[0]["id"], radius) if radius else SPATIAL.connected(project, sel[0]["id"])
        rows = out.get("boxes") or out.get("elements") or []
        names = ", ".join(r["name"] for r in rows[:12]) or "none"
        stated = out.get("stated_by_model") or []
        extra = f" The model itself also records: {', '.join(s['name'] for s in stated)}." if stated else ""
        what = f"within {radius:g} m" if radius else "whose bounding boxes touch, overlap or contain it"
        return self._post(project, request, "analysis", f"Elements {what}: {names}.{extra} This is a bounding-box query by the SLAI Spatial Agent, so it is preliminary: "
                          "a diagonal wall's box can overlap elements the wall does not touch.", context, mark, data=out)

    def _explain(self, project, request, t, context, mark):
        sel = self._need_selection(context, None, "an element")
        blocks = []
        for s in sel[:6]:
            props = project.properties({"model": s["id"]})
            rows = [*props.get("top", []), *[r for g in props.get("groups", []) for r in g["rows"]]]
            lines = []
            for r in rows:
                value = r.get("value")
                if r.get("type") == "choice":
                    value = next((c["label"] for c in r.get("choices", []) if c["value"] == value), value)
                tag = "editable" if r.get("key") else "calculated from the others"
                lines.append({"label": r["label"], "value": value, "unit": r.get("unit") or "", "editable": bool(r.get("key")), "key": r.get("key"), "basis": tag})
            blocks.append({"id": s["id"], "title": props["title"], "category": props["category"], "rows": lines})
        body = " ".join(f"{b['title']}: " + ", ".join(f"{r['label']} {r['value']}{(' ' + r['unit']) if r['unit'] else ''}" for r in b["rows"]) + "." for b in blocks)
        return self._post(project, request, "explanation", body + " These are the element's own stored parameters; rows marked editable can be changed, the others follow from them.",
                          context, mark, data={"elements": blocks})

    def _check(self, project, request, t, context, mark):
        result = project.check()
        ids = {s["id"] for s in context["selection"]}
        issues = [i for i in result["issues"] if not ids or i["ref"] in ids]
        scope = "the selected elements" if ids else "the whole project"
        body = (f"No inconsistencies found in {scope}." if not issues else f"{len(issues)} issue{'s' if len(issues) != 1 else ''} in {scope}: " + "; ".join(i["message"] for i in issues[:8]) + ".")
        return self._post(project, request, "analysis", body + " " + result["scope"], context, mark, data={"issues": issues, "ok": not issues, "scope": result["scope"]})

    # -------------------------------------------------------------------------------------------------------- engineering
    def _beam_inputs(self, t: str) -> dict[str, Any]:
        """The load case, only as the person stated it (kN/m for a uniform load, kN for a point load)."""
        support = ("cantilever" if "cantilever" in t else "fixed_fixed" if re.search(r"fixed[- ](?:fixed|ended|both)|fixed at both", t) else
                   "simply_supported" if re.search(r"simply|pinned", t) else None)
        load_type = None
        load = None
        m = re.search(_NUM + r"\s*kn\s*/\s*m\b", t)
        if m:
            load_type, load = "udl", _num(m.group(1))
        else:
            m = re.search(_NUM + r"\s*kn\b", t)
            if m:
                load = _num(m.group(1))
                load_type = "point_end" if re.search(r"\bend\b|tip|free end", t) else "point_center"
        missing = [n for n, v in (("support (simply supported, cantilever or fixed-fixed)", support), ("load (for example 10 kN/m, or 20 kN at midspan)", load)) if v is None]
        if missing:
            raise BadRequest("Say the " + " and the ".join(missing) + ". The load case is yours to state; I do not assume one.", details={"needs": missing})
        p: dict[str, Any] = {"support": support, "load_type": load_type, "load": load}
        g = re.search(r"(?:e|modulus)\s*(?:=|of)?\s*" + _NUM + r"\s*gpa", t)
        if g:
            p["youngs_modulus_gpa"] = _num(g.group(1))
        d = re.search(_NUM + r"\s*kg\s*/\s*m3|" + _NUM + r"\s*kg/m³", t)
        if d:
            p["density_kg_m3"] = _num(d.group(1) or d.group(2))
        return p

    def _one_beam(self, project: Any, context: Mapping[str, Any]) -> str:
        sel = self._need_selection(context, 1, "one beam")
        if sel[0]["category"] != "structural":
            raise BadRequest("This applies to a beam; the selected element is not one")
        return sel[0]["id"]

    def _simulate(self, project, request, t, context, mark):
        oid = self._one_beam(project, context)
        p = self._beam_inputs(t)
        p["scenario"] = "step" if re.search(r"\bstep\b|suddenly|sudden", t) else "release"
        d = re.search(r"damping(?: ratio)?\s*(?:of|=)?\s*" + _NUM + r"\s*(%?)", t)
        if d:
            z = _num(d.group(1))
            p["damping_ratio"] = z / 100.0 if d.group(2) or z > 1 else z
        s = re.search(r"for\s+" + _NUM + r"\s*(?:s|sec|seconds?)\b", t)
        if s:
            p["duration_s"] = _num(s.group(1))
        out = SB.simulate_beam(project, oid, p)
        d, r, v = out["derived"], out["response"], out["verification"]
        body = (f"{out.get('name', 'The beam')}, {p['scenario']} scenario: natural frequency {d['natural_frequency_hz']:.4g} Hz, static deflection {d['static_deflection_m'] * 1000:.4g} mm, "
                f"peak displacement {r['peak_displacement_m'] * 1000:.4g} mm (amplification {r['amplification']:.3g}). Run by the SLAI Simulation Agent with the STEM Agent as integrator; "
                f"{'agrees' if v['passed'] else 'DOES NOT agree'} with the closed-form solution of the same equation. A single-degree-of-freedom idealisation, so this is a preliminary analysis, not a design check.")
        return self._post(project, request, "analysis", body, context, mark, data=out)

    def _optimize(self, project, request, t, context, mark):
        oid = self._one_beam(project, context)
        p = self._beam_inputs(t)
        p["variable"] = "width" if re.search(r"\bwidth\b", t) and not re.search(r"\bdepth\b", t) else "depth"
        b = re.search(r"between\s+" + _NUM + r"\s*(?:mm)?\s*(?:and|to|-)\s*" + _NUM + r"\s*(mm|cm|m)?\b", t)
        if not b:
            raise BadRequest("Say the bounds of the dimension to optimise, for example “between 200 and 600 mm”. I do not choose them for you.")
        unit = _unit_key(b.group(3)) if b.group(3) else "mm"
        p["lower_mm"], p["upper_mm"] = (_num(b.group(1)) * _FACTOR[unit] * 1000, _num(b.group(2)) * _FACTOR[unit] * 1000)
        r = re.search(r"(?:l|span)\s*/\s*(\d+)", t)
        if r:
            p["deflection_ratio"] = float(r.group(1))
        out = OB.optimize_beam(project, oid, p)
        if not out.get("found"):
            return self._post(project, request, "failed", out.get("message", "No feasible value was found."), context, mark, data=out)
        ch = out["change"]
        return self._propose(project, request, context, mark, intent="optimize", title=f"Set {ch['key']} of {out['name']} to {ch['value']:g} mm",
                             explanation=(f"The SLAI Optimization Agent found {ch['value']:g} mm as the smallest {ch['key']} between {p['lower_mm']:g} and {p['upper_mm']:g} mm that keeps the deflection "
                                          f"within span ÷ {out['problem']['constraint'].split('÷')[1].split('=')[0].strip()} (the deflection limit alone: strength, buckling and code checks are not considered). "
                                          "It was re-checked through the STEM Agent. Approving applies this one change to the model."),
                             steps=[{"op": "model.set_property", "params": {"id": ch["id"], "key": ch["key"], "value": ch["value"]}}],
                             targets=[ch["id"]], units="mm", evidence=out, risk="medium", evidence_kind="optimization")

    # ------------------------------------------------------------------------------------------------------------- proposals
    def _propose(self, project: Any, request: str, context: Mapping[str, Any], mark: int, *, intent: str, title: str, explanation: str, steps: list[dict[str, Any]],
                 targets: Sequence[str], units: str, risk: str = "low", assumptions: Sequence[str] = (), skipped: Sequence[str] = (), evidence: Any = None,
                 evidence_kind: str | None = None) -> dict[str, Any]:
        """Validate a structured proposal by running it on a scratch copy; if the project refuses it, no proposal is made."""
        preview = self._dry_run(project, steps, targets)
        n_targets = len(set(targets))
        if risk != "high" and n_targets >= HIGH_RISK_ELEMENTS:
            risk = "high"
        proposal = {"id": new_id(), "task": intent, "project": project.manifest["id"], "revision": project.revision, "targets": list(targets), "operation": [s["op"] for s in steps],
                    "steps": steps, "units": units, "title": title, "explanation": explanation, "risk": risk, "requires_confirmation": risk == "high",
                    "preconditions": [f"The project is still at revision {project.revision}", "Every step passes the project's own validation when it runs"],
                    "validation": preview["validation"], "changes": preview["changes"], "assumptions": list(assumptions), "skipped": list(skipped),
                    "status": "pending", "created": time.time(), "evidence": evidence, "evidence_kind": evidence_kind}
        self.proposals[proposal["id"]] = proposal
        return self._post(project, request, "proposal", f"{title}. Nothing has been changed yet; review the proposal and approve or reject it.", context, mark, proposal=proposal)

    @staticmethod
    def _rows(props: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        return {r["key"]: r for r in [*props.get("top", []), *[x for g in props.get("groups", []) for x in g["rows"]]] if r.get("key")}

    @staticmethod
    def _all_rows(props: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        """Every row, editable or calculated, by its key (or its label): what a preview compares."""
        return {str(r.get("key") or r["label"]): r for r in [*props.get("top", []), *[x for g in props.get("groups", []) for x in g["rows"]]]}

    def _dry_run(self, project: Any, steps: list[dict[str, Any]], targets: Sequence[str]) -> dict[str, Any]:
        scratch = project.scratch()
        before_ids = set(scratch.model.query_ids())
        before_props = {}
        for oid in dict.fromkeys(targets):
            if E.alive(scratch, oid):                              # a target the project does not hold has nothing to compare
                before_props[oid] = scratch.properties({"model": oid})
        baseline = {(i["ref"], i["message"]) for i in scratch.check()["issues"]}
        created: list[str] = []
        views_before = {v["id"] for v in scratch.manifest["views"]}
        notes: list[dict[str, Any]] = []
        for n, step in enumerate(steps, 1):
            try:
                res = scratch.execute(step["op"], step["params"])
            except AppError as exc:
                raise BadRequest(f"Step {n} ({step['op']}) would be refused by the project: {exc.message}", hint=getattr(exc, "hint", None)) from exc
            if step["op"] == "anim.pose":
                prm = step["params"]
                v = prm["values"]["translation"]
                notes.append({"element": prm["id"], "name": scratch.model.get(prm["id"])["name"], "label": f"Animation key at {prm['time']:g} s", "before": None,
                              "after": f"translation ({v[0]:.3f}, {v[1]:.3f}, {v[2]:.3f}) m", "unit": ""})
            rid = (res.get("result") or {}).get("id")
            if rid and rid not in before_props and rid in set(scratch.model.query_ids()) - before_ids:
                created.append(rid)
        changes: list[dict[str, Any]] = []
        for oid, was in before_props.items():
            if E.alive(scratch, oid):
                now = scratch.properties({"model": oid})
            else:
                changes.append({"element": oid, "name": was["title"], "label": "Element", "before": "present", "after": "removed", "unit": ""})
                continue
            a, b = self._all_rows(was), self._all_rows(now)
            for key, row in b.items():
                old = a.get(key, {}).get("value")
                if old != row.get("value"):
                    changes.append({"element": oid, "name": now["title"], "key": key, "label": row["label"], "before": old, "after": row.get("value"), "unit": row.get("unit") or ""})
        changes += notes
        for v in scratch.manifest["views"]:
            if v["id"] not in views_before:
                changes.append({"element": v["id"], "name": v["name"], "label": "New view", "before": None, "after": f"{v['kind']} “{v['name']}”", "unit": ""})
        for oid in created:
            obj = scratch.model.get(oid)
            changes.append({"element": oid, "name": obj["name"], "label": "New element", "before": None, "after": E.m(obj["metadata"], "category", "element"), "unit": ""})
        after_issues = [i for i in scratch.check()["issues"] if (i["ref"], i["message"]) not in baseline]
        return {"changes": changes, "validation": {"dry_run": "every step ran on a scratch copy without error", "steps": len(steps), "new_issues": after_issues,
                                                   "checks": "reference, parameter and document-validity checks only (not an engineering or code check)"}}

    # --------------------------------------------------------------------------------------------------------------- mutations
    def _create_wall(self, project, request, t, context, mark):
        length = _attr(t, r"long|in length", r"length|long")
        thickness = _attr(t, r"thick", r"thickness|thick")
        height = _attr(t, r"high|tall", r"height|high|tall")
        pts = _points(t)
        assumptions: list[str] = []
        if len(pts) >= 2:
            a, b = pts[0], pts[1]
            length_m = math.dist(a[:2], b[:2])
            if length is not None and abs(length - length_m) > 1e-3:
                raise BadRequest(f"You gave a length of {length:g} m but the two points are {length_m:.3f} m apart")
        elif length is not None:
            a, b = [0.0, 0.0], [length, 0.0]
            assumptions.append("No position was given, so the wall starts at the origin (0, 0) and runs along +x. Say “from (x, y) to (x, y)” (metres) to place it elsewhere.")
        else:
            raise BadRequest("Say how long the wall is (for example “5 meters long”) or where it runs (“from (0, 0) to (5, 0)”)")
        if thickness is None:
            thickness = 0.2
            assumptions.append("No thickness was given; 200 mm (the new-wall default) is used.")
        if height is None:
            height = 3.0
            assumptions.append("No height was given; 3 m (the new-wall default) is used.")
        level = context["level"] or project.manifest["levels"][0]["id"]
        if not context["level"]:
            assumptions.append(f"No level is active in the view; {project.level(level)['name']} is used.")
        f = 1000.0 / MM_PER[project.manifest["unit"]]
        params = {"start": [a[0] * f, a[1] * f], "end": [b[0] * f, b[1] * f], "level": level, "thickness": thickness * 1000.0, "height": height}
        return self._propose(project, request, context, mark, intent="create_wall", title=f"Add a wall {math.dist(a[:2], b[:2]):.3g} m long, {thickness * 1000:g} mm thick, {height:g} m high",
                             explanation="Adds one wall on the level shown, with the dimensions in your request. It is added through the project's “Add wall” command, so it can be undone.",
                             steps=[{"op": "model.add_wall", "params": params}], targets=[], units="m (position) · mm (thickness) · m (height)", risk="medium", assumptions=assumptions)

    def _set_property(self, project, request, t, context, mark):
        sel = self._need_selection(context, None, "the element(s) to change")
        candidates: dict[str, str] = {}
        per_element: dict[str, dict[str, dict[str, Any]]] = {}
        for s in sel:
            rows = self._rows(project.properties({"model": s["id"]}))
            per_element[s["id"]] = rows
            for key, row in rows.items():
                for name in {key, key.replace("_", " "), str(row["label"]).lower()}:
                    candidates.setdefault(name, key)
        hit = [name for name in sorted(candidates, key=len, reverse=True) if re.search(r"\b" + re.escape(name) + r"\b", t)]
        if not hit:
            known = sorted({str(r["label"]) for rows in per_element.values() for r in rows.values()})
            raise BadRequest("I could not tell which parameter to change. The selected element(s) have: " + ", ".join(known) + ".")
        key = candidates[hit[0]]
        m = re.search(r"\bto\s+(.+)$", t)
        if not m:
            raise BadRequest("Say the new value, for example “change the selected wall height to 3.2 meters”")
        raw = m.group(1).strip().rstrip(".")
        steps, skipped, targets = [], [], []
        for s in sel:
            row = per_element[s["id"]].get(key)
            if row is None:
                skipped.append(f"{s['name']} has no “{hit[0]}” parameter, so it is left as it is.")
                continue
            value: Any
            if row["type"] == "number":
                q = re.fullmatch(QTY, raw)
                if q:
                    value = _to_unit(_metres(q.group(1), q.group(2)), q.group(2), row.get("unit") or "")
                else:
                    plain = re.fullmatch(_NUM, raw)
                    if not plain:
                        raise BadRequest(f"“{raw}” is not a number with a unit (for example 3.2 meters or 250 mm)")
                    value = _num(plain.group(1))
                value = round(value, 9)
            elif row["type"] == "choice":
                match = [c for c in row.get("choices", []) if c["label"].lower() == raw or str(c["value"]).lower() == raw]
                if not match:
                    raise BadRequest(f"“{raw}” is not one of: " + ", ".join(c["label"] for c in row.get("choices", [])))
                value = match[0]["value"]
            elif row["type"] == "text":
                value = m.group(1).strip().rstrip(".")
                original = re.search(r"\bto\s+(.+)$", request.strip().rstrip("."), re.I)
                value = original.group(1).strip() if original else value
            else:
                skipped.append(f"{s['name']}: “{row['label']}” cannot be edited.")
                continue
            steps.append({"op": "model.set_property", "params": {"id": s["id"], "key": key, "value": value}})
            targets.append(s["id"])
        if not steps:
            raise BadRequest("None of the selected elements has that editable parameter. " + " ".join(skipped))
        unit = per_element[targets[0]][key].get("unit") or ""
        assumptions = [f"“{raw}” has no unit, so it is read in {unit}."] if re.fullmatch(_NUM, raw) and unit else []
        return self._propose(project, request, context, mark, intent="set_property", title=f"Set {hit[0]} to {raw} on {len(steps)} element{'s' if len(steps) != 1 else ''}",
                             explanation="Changes one parameter through the project's “Edit element” command. Dependent figures (areas, volumes, openings) follow automatically.",
                             steps=steps, targets=targets, units=unit or "-", risk="low", assumptions=assumptions, skipped=skipped)

    def _move(self, project, request, t, context, mark):
        sel = self._need_selection(context, None, "the element(s) to move")
        d = {"x": 0.0, "y": 0.0, "z": 0.0}
        found = False
        for m in re.finditer(QTY + r"\s*(?:in|along|on|to the)?\s*(?:the\s+)?([xyz])\b(?!\w)", t):
            d[m.group(3)] += _metres(m.group(1), m.group(2))
            found = True
        if not found:
            for m in re.finditer(r"\b([xyz])\s*(?:by|of|=)?\s*" + QTY, t):
                d[m.group(1)] += _metres(m.group(2), m.group(3))
                found = True
        if not found:
            raise BadRequest("Say how far and along which axis, for example “move the selected element by 1.5 m in x”")
        steps = [{"op": "model.move", "params": {"id": s["id"], "dx": d["x"], "dy": d["y"], "dz": d["z"]}} for s in sel]
        return self._propose(project, request, context, mark, intent="move", title=f"Move {len(sel)} element{'s' if len(sel) != 1 else ''} by ({d['x']:g}, {d['y']:g}, {d['z']:g}) m",
                             explanation="Moves the elements' own position parameters, so plans, sections and schedules follow.", steps=steps, targets=[s["id"] for s in sel], units="m", risk="medium")

    def _delete(self, project, request, t, context, mark):
        sel = self._need_selection(context, None, "the element(s) to delete")
        steps = [{"op": "model.delete", "params": {"id": s["id"]}} for s in sel]
        return self._propose(project, request, context, mark, intent="delete", title=f"Delete {len(sel)} element{'s' if len(sel) != 1 else ''}",
                             explanation="Deletes the selected elements (and the doors and windows hosted by a deleted wall). It can be undone, but it is treated as high risk and needs a second confirmation.",
                             steps=steps, targets=[s["id"] for s in sel], units="-", risk="high")

    def _section(self, project, request, t, context, mark):
        sel = self._need_selection(context, None, "the elements to cut through")
        raw = project.model._model.bounds_by_object()
        boxes = [raw[s["id"]] for s in sel if s["id"] in raw]
        if not boxes:
            raise BadRequest("The selected elements have no geometry to cut through")
        x0, x1 = min(b[0][0] for b in boxes), max(b[1][0] for b in boxes)
        y0, y1 = min(b[0][1] for b in boxes), max(b[1][1] for b in boxes)
        f = 1000.0 / MM_PER[project.manifest["unit"]]
        a, b = (x0 - 1.0, y0 - 0.5), (x1 + 1.0, y0 - 0.5)
        depth = (y1 - y0) + 1.0
        params = {"a": [a[0] * f, a[1] * f], "b": [b[0] * f, b[1] * f], "depth": depth * f}
        return self._propose(project, request, context, mark, intent="section", title=f"Add a section through {len(sel)} element{'s' if len(sel) != 1 else ''}",
                             explanation=(f"A section line along +x at y = {a[1]:.3f} m, from x = {a[0]:.3f} to {b[0]:.3f} m, looking toward +y for {depth:.3f} m, so it passes just outside the "
                                          "selection's bounding box and shows all of it. The line and depth can be edited afterwards."),
                             steps=[{"op": "view.add_section", "params": params}], targets=[s["id"] for s in sel], units="m", risk="low",
                             assumptions=["The cut runs along the x axis; the selection's bounding box sets its extent."])

    def _animate(self, project, request, t, context, mark):
        sel = self._need_selection(context, 1, "one element to animate")
        oid = sel[0]["id"]
        raw = project.model._model.bounds_by_object()
        if oid not in raw:
            raise BadRequest("That element has no geometry to move")
        lo, hi = raw[oid]
        centre = [(a + b) / 2.0 for a, b in zip(lo, hi)]
        pts = _points(t)
        d = re.search(r"(?:in|over|for)\s+" + _NUM + r"\s*(?:s|sec|seconds?)\b", t)
        duration = _num(d.group(1)) if d else 2.0
        assumptions = [] if d else ["No duration was given; 2 seconds is used."]
        if len(pts) >= 2:
            a, b = pts[0], pts[1]
            start, end = [a[i] - centre[i] for i in range(3)], [b[i] - centre[i] for i in range(3)]
            title = f"Animate {sel[0]['name']} from ({a[0]:g}, {a[1]:g}, {a[2]:g}) to ({b[0]:g}, {b[1]:g}, {b[2]:g}) m over {duration:g} s"
            assumptions.append("A and B are positions of the element's centre in the model's frame, in metres; the keys store the offset from where the element is modelled.")
        else:
            raise BadRequest("Say where it moves, for example “from (0, 0, 0) to (4, 0, 0) in 3 seconds” (metres)")
        steps = [{"op": "anim.pose", "params": {"id": oid, "time": 0.0, "values": {"translation": [round(v, 6) for v in start]}, "interpolation": "smooth"}},
                 {"op": "anim.pose", "params": {"id": oid, "time": duration, "values": {"translation": [round(v, 6) for v in end]}, "interpolation": "smooth"}}]
        return self._propose(project, request, context, mark, intent="animate", title=title,
                             explanation="Sets two translation keys through the project's animation commands (the SLAI Animation Agent validates and commits them); the motion plays in the 3D view.",
                             steps=steps, targets=[oid], units="m, s", risk="medium", assumptions=assumptions)

    # ---------------------------------------------------------------------------------------------------- authorisation
    def _get(self, project: Any, pid: str) -> dict[str, Any]:
        self._bind(project)
        p = self.proposals.get(text(pid, "proposal", 80))
        if p is None:
            raise NotFound("There is no such proposal in this project")
        self._mark_stale(project)
        return p

    def reject(self, project: Any, pid: str) -> dict[str, Any]:
        with self._lock:
            p = self._get(project, pid)
            if p["status"] not in ("pending", "stale"):
                raise Conflict(f"This proposal is already {p['status']}")
            p["status"] = "rejected"
            return p

    def cancel(self, project: Any, pid: str) -> dict[str, Any]:
        """Withdraw a proposal that has not been run (the same as reject, but the person's own to withdraw)."""
        with self._lock:
            p = self._get(project, pid)
            if p["status"] not in ("pending", "stale"):
                raise Conflict(f"This proposal is already {p['status']}")
            p["status"] = "cancelled"
            return p

    def consent(self, project: Any, pid: str, *, actor: str, expected_revision: int | None = None, confirm_high_risk: bool = False) -> dict[str, Any]:
        """The person's agreement to one proposal, recorded on it. This is the only way a proposal becomes executable, and nothing here changes the
        project. Refuses a proposal that is stale, already decided, for another revision than the person reviewed, or high-risk without confirmation."""
        with self._lock:
            p = self._get(project, pid)
            if p["status"] == "stale":
                raise Conflict(p["status_note"], hint="Ask again so the proposal is made for the current project")
            if p["status"] != "pending":
                raise Conflict(f"This proposal is already {p['status']}")
            if expected_revision is not None and int(expected_revision) != p["revision"]:
                raise Conflict(f"You approved a proposal for revision {expected_revision}, but it was made for revision {p['revision']}")
            if project.revision != p["revision"]:
                p["status"] = "stale"
                raise Conflict(f"The project changed (now revision {project.revision}); this proposal was made for revision {p['revision']}", hint="Ask again")
            if p["requires_confirmation"] and not confirm_high_risk:
                raise Conflict("This is a high-risk change and needs your explicit confirmation", hint="Approve again with the confirmation ticked",
                               details={"requires_confirmation": True, "risk": p["risk"]})
            p["consent"] = {"actor": actor, "at": time.time()}
            return p

    def approve(self, project: Any, pid: str, *, expected_revision: int | None = None, confirm_high_risk: bool = False, actor: str = "direct") -> dict[str, Any]:
        """Consent and run in one step (what the interface does through the commit host, see ``commits.py``)."""
        self.consent(project, pid, actor=actor, expected_revision=expected_revision, confirm_high_risk=confirm_high_risk)
        return self.run(project, pid)

    def run(self, project: Any, pid: str) -> dict[str, Any]:
        """Execute a proposal the person consented to, through the project's own commands, all or nothing. Returns the message that reports it."""
        with self._lock:
            p = self._get(project, pid)
            if p.get("consent") is None:
                raise Conflict("This proposal has not been approved by a person, so it cannot be run")
            if p["status"] != "pending":
                raise Conflict(f"This proposal is already {p['status']}")
            if project.revision != p["revision"]:
                p["status"] = "stale"
                raise Conflict(f"The project changed (now revision {project.revision}); this proposal was made for revision {p['revision']}", hint="Ask again")
            applied = 0
            mark = len(HUB.calls)
            results = []
            try:
                for step in p["steps"]:
                    out = project.execute(step["op"], step["params"], expected_revision=project.revision)
                    applied += 1
                    results.append(out.get("result"))
            except Exception as exc:
                for _ in range(applied):                      # all or nothing: take back what was already applied
                    project.undo()
                p["status"] = "failed"
                p["status_note"] = f"Step {applied + 1} failed ({exc}); the {applied} earlier step(s) were undone."
                msg = exc.message if isinstance(exc, AppError) else str(exc)
                p["result"] = {"applied": 0, "error": msg}
                note = f"The change was not applied: {msg}"
                if applied:
                    note += f" The {applied} earlier step(s) were undone."
                return self._finish(project, p, "failed", note, mark)
            verification = self._verify(project, p, results)
            p["status"] = "executed"
            p["undo_steps"] = applied
            p["result"] = {"applied": applied, "revision": project.revision, "results": results, "verification": verification}
            ok = all(v["ok"] for v in verification)
            body = (f"{p['title']}: applied in {applied} step{'s' if applied != 1 else ''}; revision {project.revision}. "
                    + ("Verified by reading the result back from the project." if ok and verification else "Some figures did not read back as expected." if verification
                       else "The result was not independently read back; check it in the views.")
                    + f" Undo {applied} time{'s' if applied != 1 else ''} to revert.")
            return self._finish(project, p, "success", body, mark)

    def _finish(self, project: Any, p: dict[str, Any], kind: str, body: str, mark: int) -> dict[str, Any]:
        msg = self._post(project, None, kind, body, {"revision": project.revision}, mark, proposal=p)
        p["outcome_message"] = msg["id"]
        return msg

    def outcome(self, project: Any, pid: str) -> dict[str, Any]:
        """The message that reported how a proposal ended (applied or failed)."""
        with self._lock:
            p = self._get(project, pid)
            for msg in reversed(self.messages):
                if msg["id"] == p.get("outcome_message"):
                    return msg
            raise NotFound("The report of this proposal is no longer in the conversation")

    def _verify(self, project: Any, p: dict[str, Any], results: Sequence[Any]) -> list[dict[str, Any]]:
        """After running, read back from the project what can be read back and compare it with what was asked for. Steps with nothing to read back
        (moves, animation keys) are not listed, and the message says they were not independently verified."""
        checks: list[dict[str, Any]] = []
        for step, res in zip(p["steps"], results):
            op, prm = step["op"], step["params"]
            if op == "model.set_property" and prm["key"] != "name":
                row = self._rows(project.properties({"model": prm["id"]})).get(prm["key"])
                got, want = (row.get("value") if row else None), prm["value"]
                ok = math.isclose(float(got), float(want), rel_tol=1e-6, abs_tol=1e-6) if isinstance(want, (int, float)) and isinstance(got, (int, float)) else got == want
                checks.append({"label": f"{prm['key']} of {project.model.get(prm['id'])['name']}", "wanted": want, "found": got, "ok": bool(ok)})
            elif op == "model.delete":
                gone = prm["id"] not in project.model.query_ids()
                checks.append({"label": "element deleted", "wanted": "absent", "found": "absent" if gone else "present", "ok": gone})
            elif op == "model.add_wall":
                oid = (res or {}).get("id")
                rows = self._rows(project.properties({"model": oid})) if oid in project.model.query_ids() else {}
                for key, want in (("height", prm["height"]), ("thickness", prm["thickness"])):
                    got = rows.get(key, {}).get("value")
                    checks.append({"label": f"new wall {key}", "wanted": want, "found": got, "ok": isinstance(got, (int, float)) and math.isclose(float(got), float(want), rel_tol=1e-6, abs_tol=1e-6)})
            elif op == "view.add_section":
                vid = (res or {}).get("id")
                checks.append({"label": "section view exists", "wanted": "present", "found": "present" if any(v["id"] == vid for v in project.manifest["views"]) else "absent",
                               "ok": any(v["id"] == vid for v in project.manifest["views"])})
        return checks


ASSISTANT = Assistant()
