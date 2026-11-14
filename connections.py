"""BIM connection diagnostics (issue sheet 11-11-2026, agent integration 8.3-8.6): rainwater drainage, roof edges and drip edges, curtain-wall corners
and hosts, analysed from the project's own geometry and parameters.

Three responsibilities are kept apart. **Analysis** (``diagnose``) reads the model and reports findings, each with the numbers it rests on and the model
revision it was made at; it changes nothing. **Proposal** (``propose_fix``) turns the corrections attached to findings into one reviewable proposal
through the assistant, which dry-runs every step on a scratch copy and binds it to the revision; a stale finding is refused. **Execution** is only ever the
assistant's approve-and-run path through SPADRA's validated commands, with undo.

The SLAI Spatial Agent is asked for *candidates* only (which drains or walls are near an element, by bounding boxes); every candidate is then confirmed
or rejected with exact geometry here, so an agent answer never becomes a finding on its own. When the agent is not available a plain scan supplies the same
candidates, and the result says which was used. Results are cached per project and revision, so asking again without an edit costs nothing."""
from __future__ import annotations

import math
import threading
import time
from typing import Any, Mapping, Sequence

from ..tools.errors import AppError, BadRequest, Conflict

from . import attachments as AT
from . import curtainwall as CW
from . import elements as E
from . import gutters as GU

DOMAINS = ("rainwater", "roof", "curtainwall")
INLET_REACH = 0.6                    # m: a drain this close (plan) under a downpipe's end could take it
WALL_REACH = 0.3                     # m: a free curtain-wall end this close to a wall could be hosted by it
SPATIAL_RADIUS = 1.5                 # m: what the Spatial Agent is asked around an element for candidates
CACHE_SIZE = 16
BASIS = ("Findings are computed from the project's own parameters and geometry at the stated revision (exact section frames, assembly rebuilds and corner "
         "solvers), not estimated. Proximity candidates may come from the SLAI Spatial Agent's bounding-box index; each is confirmed geometrically before it is reported.")


def _finding(domain: str, severity: str, ref: str, message: str, *, evidence: Mapping[str, Any] | None = None, fix: Mapping[str, Any] | None = None,
             part: str | None = None, check: str) -> dict[str, Any]:
    out: dict[str, Any] = {"domain": domain, "severity": severity, "ref": ref, "message": message, "category": "connections", "check": check,
                           "evidence": dict(evidence or {})}
    if part:
        out["part"] = part
    if fix:
        out["fix"] = dict(fix)
    return out


def _name(project: Any, oid: str) -> str:
    return project.model.get(oid)["name"] or E.category(project, oid).title()


class Candidates:
    """Proximity candidates: the Spatial Agent's within-radius query when it can be used, else a scan of element centres. Records which."""

    def __init__(self, project: Any) -> None:
        self.project = project
        self.source = "scan"
        self.agent_calls = 0
        self.reason: str | None = None
        self._spatial: Any = None
        try:
            from .agent_hub import HUB
            from .spatial_bridge import BRIDGE
            if HUB.available("spatial"):
                self._spatial = BRIDGE
                self.source = "SLAI Spatial Agent"
            else:
                self.reason = HUB.reason("spatial")
        except Exception as exc:                                    # noqa: BLE001 - an agent failure must never fail a diagnosis
            self.reason = f"{type(exc).__name__}: {exc}"

    def near(self, oid: str, categories: Sequence[str]) -> list[str]:
        if self._spatial is not None:
            try:
                out = self._spatial.nearby(self.project, oid, SPATIAL_RADIUS)
                self.agent_calls += 1
                return [r["id"] for r in out["elements"] if r["category"] in categories]
            except AppError as exc:                                  # the agent refused or failed: fall back for the rest of this diagnosis
                self._spatial, self.source, self.reason = None, "scan (the Spatial Agent failed)", exc.message
        return [o for c in categories for o in self.project.model.query_ids(metadata={f"{E.NS}:category": c}, limit=10_000) if o != oid]


# ------------------------------------------------------------------------------------------------------------------------------- rainwater
def _rainwater(project: Any, ids: set[str] | None, cand: Candidates) -> tuple[list[dict[str, Any]], int]:
    out: list[dict[str, Any]] = []
    checked = 0
    drains = GU.drains(project)
    pipes = list(project.model.query_ids(metadata={f"{E.NS}:category": "downpipe"}, limit=10_000))
    for pid in pipes:
        if ids is not None and pid not in ids:
            continue
        checked += 1
        name = _name(project, pid)
        try:
            pf = GU.pipe_frame(project, pid)
        except BadRequest as exc:
            out.append(_finding("rainwater", "error", pid, f"{name}: {exc.message}", check="downpipe.gutter",
                                fix={"title": f"Delete {name}, which hangs under no gutter", "steps": [{"op": "model.delete", "params": {"id": pid}}], "risk": "medium"}))
            continue
        g = pf["g"]
        lo, hi = GU.outlet_span(g["chain"], g["seg"], pf["diameter"] / 2)
        ev = {"diameter_mm": round(pf["diameter"] * 1000, 3), "gutter_width_mm": round(g["width"] * 1000, 3), "offset_m": round(pf["offset"], 6),
              "allowed_m": [round(lo, 6), round(hi, 6)], "bore_mm": round(pf["bore"] * 2000, 3)}
        try:
            GU._check_pipe(pf)                                                                   # the same rule the outlet hole is cut by
        except BadRequest as exc:
            fix = None
            if lo <= hi and not lo - 1e-9 <= pf["offset"] <= hi + 1e-9:
                to = min(max(pf["offset"], lo), hi)
                fix = {"title": f"Move {name} to {to:.3f} m along its gutter, inside the piece", "steps": [{"op": "model.set_property", "params": {"id": pid, "key": "offset", "value": round(to, 6)}}]}
            out.append(_finding("rainwater", "error", pid, f"{name}: {exc.message}", evidence=ev, fix=fix, check="downpipe.outlet"))
        try:
            a = GU.assembly(project, pid)
        except BadRequest as exc:
            out.append(_finding("rainwater", "error", pid, f"{name}: its components cannot be built ({exc.message})", evidence=ev, check="downpipe.assembly"))
            continue
        pipes_in = [p for p in a["parts"] if p.get("kind") == "pipe"]
        if len(pipes_in) > 1 and len(a["connectors"]) < len(pipes_in) - sum(1 for p in a["parts"] if p.get("kind") != "pipe"):
            out.append(_finding("rainwater", "warning", pid, f"{name}: {len(pipes_in)} sections but only {len(a['connectors'])} connector(s) join them", check="downpipe.connectors"))
        end = a["end"]
        served = None
        for did in drains:
            dm = E.meta_of(project, did)
            if E.m(dm, "downpipe") == pid:
                served = did
                break
        if served is None:
            for did in [d for d in cand.near(pid, ("drain",)) if d in drains]:
                try:
                    df = GU.drain_frame(project, did)
                except BadRequest:
                    continue
                if math.dist(df["centre"], (end[0], end[1])) <= INLET_REACH:
                    served = did
                    break
        if served is None:
            out.append(_finding("rainwater", "info", pid, f"{name} discharges freely at {end[2]:.2f} m: no ground inlet or drain stands under its end",
                                evidence={**ev, "end": [round(v, 4) for v in end]}, check="downpipe.discharge",
                                fix={"title": f"Add a ground inlet under {name}", "steps": [{"op": "model.add_drain", "params": {"kind": "inlet", "downpipe": pid}}], "risk": "low"}))
        else:
            dm = E.meta_of(project, served)
            if E.m(dm, "drain_kind") == "inlet":
                df = GU.drain_frame(project, served)
                inner = float(df["p"]["diameter"]) - 2 * float(df["p"]["wall"])
                if inner < pf["diameter"] + 0.002:
                    out.append(_finding("rainwater", "error", served, f"{_name(project, served)} ({inner * 1000:.0f} mm inside) is too narrow for the {pf['diameter'] * 1000:.0f} mm downpipe it takes",
                                        evidence={"inlet_inside_mm": round(inner * 1000, 3), "downpipe_mm": ev["diameter_mm"]}, check="inlet.size",
                                        fix={"title": "Widen the ground inlet to take the downpipe", "steps": [{"op": "model.set_property", "params": {"id": served, "key": "diameter", "value": round((pf["diameter"] + 0.002 + 2 * float(df["p"]["wall"])) * 1000 + 20, 1)}}]}))
    for did in drains:
        if ids is not None and did not in ids:
            continue
        checked += 1
        for f in GU.drain_issues(project, did, _name(project, did), E.issue):
            fix = None
            if "lost the downpipe" in f["message"]:
                fix = {"title": f"Delete {_name(project, did)}, which no longer takes a downpipe", "steps": [{"op": "model.delete", "params": {"id": did}}], "risk": "medium"}
            elif "not in a floor" in f["message"]:
                fix = {"title": f"Set {_name(project, did)} into the floor under it", "steps": [{"op": "model.set_property", "params": {"id": did, "key": "host", "value": "auto"}}]}
            out.append(_finding("rainwater", f["severity"], did, f["message"], check="drain.host", fix=fix))
    return out, checked


# ---------------------------------------------------------------------------------------------------------------------------------- roofs
def _roof(project: Any, ids: set[str] | None) -> tuple[list[dict[str, Any]], int]:
    out: list[dict[str, Any]] = []
    checked = 0
    for oid in E.element_ids(project):
        meta = E.meta_of(project, oid)
        kind = E.m(meta, "accessory_kind")
        if kind not in ("roof_edge", "drip_edge") or (ids is not None and oid not in ids and E.m(meta, "roof_host") not in ids):
            continue
        checked += 1
        name, rid, edge = _name(project, oid), E.m(meta, "roof_host"), int(E.m(meta, "edge", -1))
        if not rid or not E.alive(project, rid):
            out.append(_finding("roof", "error", oid, f"{name} lost its roof", check="roof.host",
                                fix={"title": f"Delete {name}", "steps": [{"op": "model.delete", "params": {"id": oid}}], "risk": "medium"}))
            continue
        rm = E.meta_of(project, rid)
        eb = E.effective_boundary(project, rm)
        if not 0 <= edge < len(eb):
            out.append(_finding("roof", "error", oid, f"{name} is on edge {edge + 1}, but the roof now has {len(eb)} edges", check="roof.edge",
                                fix={"title": f"Delete {name}", "steps": [{"op": "model.delete", "params": {"id": oid}}], "risk": "medium"}))
            continue
        try:
            fr = E.edge_section_frame(project, rm, edge)
        except BadRequest as exc:
            out.append(_finding("roof", "error", oid, f"{name}: {exc.message}", check="roof.frame"))
            continue
        du = fr["du"]
        if kind == "roof_edge":
            sec = E.fascia_section(project, rm, edge, float(E.m(meta, "width")), float(E.m(meta, "depth")), 0.0, 0.0)
            inner = (sec[0][0] - sec[3][0], sec[0][1] - sec[3][1])
            deviation = abs(math.degrees(math.atan2(inner[0] * du[1] - inner[1] * du[0], inner[0] * du[0] + inner[1] * du[1])))
            deviation = min(deviation, 180 - deviation)
            on_cut = max(abs(du[0] * sec[i][1] - du[1] * sec[i][0]) for i in (0, 3))
            ev = {"cut": fr["kind"], "deviation_deg": round(deviation, 9), "inner_face_off_cut_mm": round(on_cut * 1000, 6)}
            if deviation > 1e-6 or on_cut > 1e-6:
                out.append(_finding("roof", "error", oid, f"{name} is not parallel to the {fr['kind']} roof end (off by {deviation:.3f}°)", evidence=ev, check="roof_edge.parallel",
                                    fix={"title": f"Rebuild {name} on the roof end", "steps": [{"op": "model.set_property", "params": {"id": oid, "key": "width", "value": float(E.m(meta, "width")) * 1000}}]}))
        else:
            sec = E.drip_section(project, rm, edge, meta)
            residual = max(abs(sec[i][1] - fr["g"] * sec[i][0]) for i in (0, 1))
            face = (sec[1][0] - sec[2][0], sec[1][1] - sec[2][1])
            dc = fr["dcut"]
            tilt = abs(math.degrees(math.atan2(face[0] * dc[1] - face[1] * dc[0], face[0] * dc[0] + face[1] * dc[1])))
            tilt = min(tilt, 180 - tilt)
            member = next((o for o in E.element_ids(project) if E.m(E.meta_of(project, o), "roof_host") == rid
                           and E.m(E.meta_of(project, o), "accessory_kind") == "roof_edge" and int(E.m(E.meta_of(project, o), "edge", -1)) == edge), None)
            ev = {"cut": fr["kind"], "flange_off_top_face_mm": round(residual * 1000, 6), "face_vs_cut_deg": round(tilt, 9), "wraps": member}
            if residual > 1e-6:
                out.append(_finding("roof", "error", oid, f"{name} does not lie on the roof's top face ({residual * 1000:.2f} mm off)", evidence=ev, check="drip.top_face"))
            if tilt > 1e-6:
                out.append(_finding("roof", "warning", oid, f"{name}'s face is not parallel to the roof end ({tilt:.2f}°)", evidence=ev, check="drip.parallel"))
            gutter = next((g for g in GU.gutters_of(project, rid) if int(E.m(E.meta_of(project, g), "edge", -2)) == edge), None)
            if gutter is not None and member is None and fr["kind"] != "plumb":
                out.append(_finding("roof", "info", oid, f"{name} turns down over a {fr['kind']} roof end that carries a gutter but no edge member: check that it drips into the gutter",
                                    evidence=ev, check="drip.gutter"))
    return out, checked


# ---------------------------------------------------------------------------------------------------------------------------- curtain walls
def _inside(ring: Sequence[Sequence[float]], x: float, y: float) -> bool:
    inside = False
    for i in range(len(ring)):
        (x1, y1), (x2, y2) = ring[i][:2], ring[(i + 1) % len(ring)][:2]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _curtainwalls(project: Any, ids: set[str] | None, cand: Candidates) -> tuple[list[dict[str, Any]], int]:
    out: list[dict[str, Any]] = []
    checked = 0
    seen: set[tuple[str, str]] = set()
    for oid in CW.curtainwalls(project):
        if ids is not None and oid not in ids:
            continue
        checked += 1
        meta, name = E.meta_of(project, oid), _name(project, oid)
        sp = CW.spec(meta)
        for end in ("start", "end"):
            j = CW._junction(project, oid, end)
            if j is None:
                lk = CW.links_of(meta).get(end)
                if lk:
                    out.append(_finding("curtainwall", "warning", oid, f"{name}'s {end} was joined to a curtain wall that is gone or has moved away", check="cw.link"))
                continue
            key = tuple(sorted(((oid, end), (j["other"], j["other_end"]))))[0]
            if key in seen:
                continue
            seen.add(key)
            kind = CW.corner_type(j)
            ev = {"angle_deg": round(j["angle"], 4), "corner": kind, "other": j["other"]}
            try:
                CW.corner_post(j)
            except BadRequest as exc:
                out.append(_finding("curtainwall", "error", oid, f"{name} ({end}): {exc.message}", evidence=ev, part=f"corner:{end}", check="cw.corner",
                                    fix={"title": f"Let the {end} corner of {name} choose the type that suits {j['angle']:.0f}°",
                                         "steps": [{"op": "model.set_curtainwall_corner", "params": {"id": oid, "end": end, "type": "auto"}}]}))
                continue
            if kind == "none":
                out.append(_finding("curtainwall", "warning", oid, f"{name} meets {_name(project, j['other'])} at {j['angle']:.1f}°, where no corner type can be built and the walls do not run in line",
                                    evidence=ev, check="cw.corner"))
        # base on a floor, top under a roof
        a, b = tuple(E.m(meta, "start")), tuple(E.m(meta, "end"))
        if AT.base_attachment(meta) is None:
            floor = AT.find_floor(project, E.m(meta, "level"), [a, b])
            if floor:
                gap = AT.floor_top(project, floor) - E.level_elevation(project, E.m(meta, "level"))
                out.append(_finding("curtainwall", "warning" if abs(gap) > 1e-6 else "info", oid,
                                    f"{name} stands on its level, not on {_name(project, floor)}'s top surface" + (f" ({gap * 1000:.0f} mm above the level)" if abs(gap) > 1e-6 else ""),
                                    evidence={"floor": floor, "floor_top_above_level_mm": round(gap * 1000, 3)}, check="cw.base",
                                    fix={"title": f"Stand {name} on the floor's top surface", "steps": [{"op": "model.attach_wall", "params": {"ids": [oid], "to": "floor", "floor": floor}}]}))
        if not AT.is_attached(project, oid):
            level_z = E.level_elevation(project, E.m(meta, "level"))
            for rid in project.model.query_ids(metadata={f"{E.NS}:category": "roof"}, limit=10_000):
                rm = E.meta_of(project, rid)
                if E.level_elevation(project, E.m(rm, "level")) <= level_z + 1e-6:
                    continue
                ring = E.effective_boundary(project, rm)
                if _inside(ring, *a) and _inside(ring, *b):
                    out.append(_finding("curtainwall", "info", oid, f"{name} stands under {_name(project, rid)} but its top is not attached to it", evidence={"roof": rid}, check="cw.top",
                                        fix={"title": f"Attach the top of {name} to {_name(project, rid)}", "steps": [{"op": "model.attach_wall", "params": {"ids": [oid], "to": "roof", "roof": rid}}]}))
                    break
        # free ends near a wall that does not host them
        hosts = E.m(meta, "end_hosts") or {}
        walls = [w for w in cand.near(oid, ("wall",)) if E.category(project, w) == "wall"]
        for end, p in (("start", a), ("end", b)):
            if CW.links_of(meta).get(end) or (isinstance(hosts, Mapping) and hosts.get(end)):
                continue
            for wid in walls:
                wm = E.meta_of(project, wid)
                s, e = tuple(E.m(wm, "start")), tuple(E.m(wm, "end"))
                L = math.dist(s, e)
                if L < 1e-9 or E.m(wm, "level") != E.m(meta, "level"):
                    continue
                u = ((e[0] - s[0]) / L, (e[1] - s[1]) / L)
                along = (p[0] - s[0]) * u[0] + (p[1] - s[1]) * u[1]
                across = abs(-(p[0] - s[0]) * u[1] + (p[1] - s[1]) * u[0])
                reach = float(E.m(wm, "thickness", 0.2)) / 2 + WALL_REACH
                if -WALL_REACH <= along <= L + WALL_REACH and across <= reach:
                    out.append(_finding("curtainwall", "info", oid, f"{name}'s {end} is {max(0.0, across - float(E.m(wm, 'thickness', 0.2)) / 2) * 1000:.0f} mm from {_name(project, wid)} but not hosted by it",
                                        evidence={"wall": wid, "across_mm": round(across * 1000, 3)}, check="cw.host"))
                    break
        # panel and profile compatibility
        reach = sp["frame_depth"] / 2
        stick = abs(sp["panel_offset"]) + sp["panel_thickness"] / 2
        if stick > reach + 1e-9:
            out.append(_finding("curtainwall", "warning", oid, f"{name}'s panels ({sp['panel_thickness'] * 1000:.0f} mm, offset {sp['panel_offset'] * 1000:.0f} mm) stand {(stick - reach) * 1000:.0f} mm out of the "
                                f"{sp['frame_depth'] * 1000:.0f} mm frame", evidence={"panel_mm": sp["panel_thickness"] * 1000, "offset_mm": sp["panel_offset"] * 1000, "frame_depth_mm": sp["frame_depth"] * 1000},
                                check="cw.panel",
                                fix={"title": f"Centre {name}'s panels in the frame", "steps": [{"op": "model.set_property", "params": {"id": oid, "key": "panel_offset", "value": 0}}]}
                                if sp["panel_thickness"] / 2 <= reach else None))
    return out, checked


# ------------------------------------------------------------------------------------------------------------------------------ diagnosis
class Diagnoser:
    """Caches diagnoses per (project, revision, scope); a model edit changes the revision, so nothing stale is ever served."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[tuple, dict[str, Any]] = {}
        self.hits = 0

    def diagnose(self, project: Any, ids: Sequence[str] | None = None, domains: Sequence[str] | None = None) -> dict[str, Any]:
        wanted = tuple(d for d in (domains or DOMAINS))
        for d in wanted:
            if d not in DOMAINS:
                raise BadRequest(f"A connection domain is one of: {', '.join(DOMAINS)}")
        scope = None if not ids else set(i for i in ids if E.alive(project, i))
        key = (project.manifest["id"], project.revision, tuple(sorted(scope)) if scope is not None else None, wanted)
        with self._lock:
            if key in self._cache:
                self.hits += 1
                return {**self._cache[key], "cached": True}
        started = time.perf_counter()
        cand = Candidates(project)
        findings: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        if "rainwater" in wanted:
            f, counts["rainwater"] = _rainwater(project, scope, cand)
            findings += f
        if "roof" in wanted:
            f, counts["roof"] = _roof(project, scope)
            findings += f
        if "curtainwall" in wanted:
            f, counts["curtainwall"] = _curtainwalls(project, scope, cand)
            findings += f
        order = {"error": 0, "warning": 1, "info": 2}
        findings.sort(key=lambda x: (order.get(x["severity"], 3), x["domain"], x["ref"]))
        for i, f in enumerate(findings, 1):
            f["id"] = f"c{i}"
        result = {"project": project.manifest["id"], "revision": project.revision, "scope": sorted(scope) if scope is not None else "model", "domains": list(wanted),
                  "findings": findings, "checked": counts, "fixable": sum(1 for f in findings if f.get("fix")),
                  "candidates": {"source": cand.source, "agent_calls": cand.agent_calls, **({"reason": cand.reason} if cand.reason else {})},
                  "basis": BASIS, "elapsed_ms": round((time.perf_counter() - started) * 1000, 3), "cached": False}
        with self._lock:
            self._cache[key] = result
            while len(self._cache) > CACHE_SIZE:
                self._cache.pop(next(iter(self._cache)))
        return result

    def propose_fix(self, project: Any, revision: Any, finding_ids: Sequence[str], ids: Sequence[str] | None = None, domains: Sequence[str] | None = None) -> dict[str, Any]:
        """One reviewable proposal from the corrections of the chosen findings. The findings must be from the project's current revision (a stale one is
        refused: diagnose again). The assistant dry-runs the steps on a scratch copy before it offers them, and binds the proposal to the revision."""
        from .assistant import ASSISTANT
        if revision is None or int(revision) != project.revision:
            raise Conflict(f"These findings were made at revision {revision}; the project is now at revision {project.revision}", hint="Run the connection check again")
        result = self.diagnose(project, ids, domains)
        chosen = [f for f in result["findings"] if f["id"] in set(finding_ids) and f.get("fix")]
        if not chosen:
            raise BadRequest("None of those findings has a correction to propose")
        steps = [s for f in chosen for s in f["fix"]["steps"]]
        targets = list(dict.fromkeys([f["ref"] for f in chosen] + [s["params"]["id"] for s in steps if isinstance(s["params"].get("id"), str)]))
        risk = "high" if any(f["fix"].get("risk") == "high" for f in chosen) else "medium" if any(f["fix"].get("risk") == "medium" or s["op"] == "model.delete" for f in chosen for s in f["fix"]["steps"]) else "low"
        title = chosen[0]["fix"]["title"] if len(chosen) == 1 else f"Correct {len(chosen)} connection findings"
        explanation = ("Proposed from the connection check at revision {rev}: ".format(rev=project.revision) + "; ".join(f"{f['message']} → {f['fix']['title']}" for f in chosen)
                       + ". Every step is an ordinary SPADRA command, dry-run on a copy first; approving applies them together and Undo takes them back.")
        return ASSISTANT.propose_commands(project, source="connection check", title=title, explanation=explanation, steps=steps, targets=targets,
                                          evidence={"revision": project.revision, "findings": [{k: f[k] for k in ("id", "domain", "check", "message", "evidence")} for f in chosen]},
                                          evidence_kind="connections", risk=risk)


DIAGNOSER = Diagnoser()
