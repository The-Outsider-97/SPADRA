"""Check Profile: structural problems, constraint health and *operation-specific suitability*, with previewable repairs.

A profile that is fine for drafting can be unusable for extrusion (open contour), for an opening (two regions), for a revolve (crosses the axis) or for a
sweep path (branches). ``check(sk, purpose)`` therefore reports one list of issues with a severity chosen for that purpose, and ``suitable`` says whether the
hand-off may proceed. Nothing here edits the sketch; ``repair(..., apply=False)`` previews on a clone and only ``apply=True`` changes it.
"""
from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from typing import Any, Iterable, Sequence

from . import geom as G
from . import ops, regions, topo
from .errors import ProfilerError

PURPOSES = ("drafting", "extrude", "void", "opening", "sweep_section", "sweep_path", "revolve")
REGION_PURPOSES = ("extrude", "void", "opening", "sweep_section", "revolve")
SEVERITIES = ("error", "warning", "info")


def _issue(code: str, severity: str, message: str, refs: Sequence[str] = (), *, repair: str | None = None, point: Sequence[float] | None = None, **extra) -> dict:
    key = hashlib.sha1((code + "|" + ",".join(sorted(refs))).encode()).hexdigest()[:10]
    out = {"id": "%s:%s" % (code, key), "code": code, "severity": severity, "message": message, "refs": list(refs), "repair": repair}
    if point is not None:
        out["point"] = [float(point[0]), float(point[1])]
    out.update(extra)
    return out


def _active_edges(sk) -> list[str]:
    return [e for e, ed in sk.edges.items() if not ed.get("construction")]


def _degrees(sk, ids: Iterable[str]) -> dict[str, list[str]]:
    deg: dict[str, list[str]] = defaultdict(list)
    for e in ids:
        for v in sk.edge_ends(e) or ():
            deg[v].append(e)
    return deg


def _diag(sk) -> float:
    b = sk.bounds()
    return G.dist((b[0], b[1]), (b[2], b[3])) if b else 1.0


# ---------------------------------------------------------------------------------------------------------- structural
def degenerate_edges(sk) -> list[str]:
    bad = []
    eps = max(sk.tol * 10, 1e-6)
    for eid, e in sk.edges.items():
        t = e["type"]
        try:
            if t == "line" and G.dist(sk.pos(e["v"][0]), sk.pos(e["v"][1])) < eps:
                bad.append(eid)
            elif t == "circle" and sk.center_radius(eid)[1] < eps:
                bad.append(eid)
            elif t == "arc":
                _, r, _, sw = sk.arc_params(eid)
                if r < eps or sw < 1e-9:
                    bad.append(eid)
            elif t == "ellipse" and min(sk.d["scalars"][e["rx"]], sk.d["scalars"][e["ry"]]) < eps:
                bad.append(eid)
            elif t == "spline":
                pl = sk.edge_polyline(eid, 0.05)
                if len(pl) < 2 or G.polyline_length(pl) < eps:
                    bad.append(eid)
        except Exception:
            bad.append(eid)
    return bad


def duplicate_vertices(sk) -> list[list[str]]:
    used = ops.used_vertices(sk)
    ids = sorted(v for v in sk.vertices if v in used)
    tol = max(sk.tol, 1e-9)
    grid: dict[tuple[int, int], list[str]] = defaultdict(list)
    cell = max(tol * 4, 1e-7)
    for v in ids:
        p = sk.pos(v)
        grid[(int(p[0] // cell), int(p[1] // cell))].append(v)
    seen, groups = set(), []
    for v in ids:
        if v in seen:
            continue
        p = sk.pos(v)
        gx, gy = int(p[0] // cell), int(p[1] // cell)
        grp = [v]
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for w in grid.get((gx + dx, gy + dy), []):
                    if w != v and w not in seen and G.dist(p, sk.pos(w)) <= tol:
                        grp.append(w)
        if len(grp) > 1:
            seen.update(grp)
            groups.append(sorted(grp))
    return groups


def duplicate_edges(sk) -> list[list[str]]:
    sig: dict[Any, list[str]] = defaultdict(list)
    for eid, e in sk.edges.items():
        t = e["type"]
        try:
            if t == "line":
                sig[("line", frozenset(e["v"]))].append(eid)
            elif t == "arc":
                sig[("arc", e["c"], tuple(e["v"]))].append(eid)
            elif t == "circle":
                c, r = sk.center_radius(eid)
                sig[("circle", round(c[0], 6), round(c[1], 6), round(r, 6))].append(eid)
        except Exception:
            continue
    out = [sorted(v) for v in sig.values() if len(v) > 1]
    # overlapping collinear lines that do not share the same vertices
    lines = [(eid, [sk.pos(v) for v in e["v"]]) for eid, e in sk.edges.items() if e["type"] == "line" and not e.get("construction")]
    eps = max(sk.tol * 10, 1e-6)
    for i in range(len(lines)):
        a, (p, q) = lines[i]
        d = G.sub(q, p)
        L = G.norm(d)
        if L < eps:
            continue
        for j in range(i + 1, len(lines)):
            b, (r, s) = lines[j]
            if any(a in g and b in g for g in out):
                continue
            if abs(G.dist_point_line(r, p, q)) > eps or abs(G.dist_point_line(s, p, q)) > eps:
                continue
            u = G.mul(d, 1 / L)
            lo, hi = sorted((G.dot(G.sub(r, p), u), G.dot(G.sub(s, p), u)))
            if min(hi, L) - max(lo, 0.0) > eps:
                out.append(sorted([a, b]))
    return out


def self_intersections(sk) -> list[tuple[str, tuple[float, float]]]:
    out = []
    for eid, e in sk.edges.items():
        if e["type"] in ("spline",):
            pl = sk.edge_polyline(eid, 0.01)
            for p in G.polyline_self_intersections(pl, False):
                out.append((eid, p))
    return out


def _reference_issues(sk) -> list[dict]:
    d = sk.d
    out = []
    live = set(d["vertices"]) | set(d["edges"]) | set(d["dimensions"])
    for c in d["constraints"]:
        if c.get("state") == "unresolved":
            out.append(_issue("unresolved_constraint", "error", "A %s constraint lost its geometry (%s) and is switched off - repair or delete it." % (c["kind"], (c.get("provenance") or {}).get("reason") or "an edit replaced what it referred to"),
                              [c["id"]] + list(c["refs"]), repair="reattach"))
        elif any(r not in live for r in c["refs"]):
            out.append(_issue("broken_reference", "error", "A %s constraint refers to geometry that no longer exists." % c["kind"], [c["id"]]))
    for did, dm in d["dimensions"].items():
        if dm.get("state") == "unresolved":
            out.append(_issue("unresolved_dimension", "error", "A %s dimension lost its geometry and is not driving anything." % dm["type"], [did]))
        elif any(r not in live for r in dm["refs"]):
            out.append(_issue("broken_reference", "error", "A %s dimension refers to geometry that no longer exists." % dm["type"], [did]))
    for hid, h in d.get("hatches", {}).items():
        if regions.region_containing_edges(sk, h["edges"]) is None:
            out.append(_issue("broken_hatch", "warning", "A hatch no longer lies inside a closed region.", [hid]))
    for gid, g in d["groups"].items():
        if any(m not in live for m in g["members"]):
            out.append(_issue("broken_reference", "error", "Group %r has members that no longer exist." % g.get("name", gid[:8]), [gid]))
    for cid, c in d["comments"].items():
        a = c["anchor"]
        if a["kind"] in ("vertex", "edge") and a["id"] not in live:
            out.append(_issue("orphan_comment", "info", "A comment's geometry was deleted; it stays at its last position.", [cid]))
    for eid in d["edges"]:
        e = d["edges"][eid]
        for r in list(e.get("v", [])) + ([e["c"]] if "c" in e else []):
            if r not in d["vertices"]:
                out.append(_issue("broken_reference", "error", "An edge refers to a missing vertex.", [eid]))
                break
    return out


def _param_issues(sk) -> list[dict]:
    if not sk.d["params"]:
        return []
    from . import params as PM
    try:
        PM.evaluate(sk)
    except ProfilerError as exc:
        return [_issue("parameter_error", "error", str(exc), [])]
    return []


# ---------------------------------------------------------------------------------------------------------- suitability
def _closed_path_info(sk, ids: list[str]) -> dict[str, Any]:
    """Connectivity of the selected non-construction edges as one path."""
    deg = _degrees(sk, ids)
    branches = [v for v, es in deg.items() if len(es) > 2]
    ends = [v for v, es in deg.items() if len(es) == 1]
    # connected components over shared vertices (circles/ellipses are components of their own)
    parent = {e: e for e in ids}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for v, es in deg.items():
        for e in es[1:]:
            parent[find(e)] = find(es[0])
    comps: dict[str, list[str]] = defaultdict(list)
    for e in ids:
        comps[find(e)].append(e)
    return {"branches": branches, "ends": ends, "components": list(comps.values()), "closed": not ends and not branches}


def _tangent_breaks(sk, ids: list[str], limit_deg: float = 1.0) -> list[tuple[str, float]]:
    deg = _degrees(sk, ids)
    out = []
    for v, es in deg.items():
        if len(es) != 2:
            continue
        t1 = regions._tangent_at(sk, es[0], v)
        t2 = regions._tangent_at(sk, es[1], v)
        ang = math.degrees(math.acos(max(-1.0, min(1.0, -G.dot(t1, t2)))))      # leaving directions: smooth continuation is 180 apart
        turn = ang
        if turn > limit_deg:
            out.append((v, turn))
    return out


def _axis(axis) -> tuple[tuple[float, float], tuple[float, float]]:
    if axis is None:
        return (0.0, 0.0), (0.0, 1.0)
    p = axis.get("point", [0.0, 0.0])
    if "direction" in axis:
        d = G.unit((float(axis["direction"][0]), float(axis["direction"][1])))
    else:
        a = math.radians(float(axis.get("angle", 90.0)))
        d = (math.cos(a), math.sin(a))
    if G.norm(d) < 0.5:
        raise ProfilerError("the revolve axis needs a non-zero direction")
    return (float(p[0]), float(p[1])), d


def check(sk, purpose: str = "drafting", *, axis: dict | None = None, gap_tol: float | None = None) -> dict[str, Any]:
    if purpose not in PURPOSES:
        raise ProfilerError("purpose must be one of %s" % ", ".join(PURPOSES))
    strict = purpose in REGION_PURPOSES
    issues: list[dict] = []
    active = _active_edges(sk)
    gap = float(gap_tol) if gap_tol is not None else max(0.005 * _diag(sk), sk.tol * 100)

    deg_ids = degenerate_edges(sk)
    if deg_ids:
        issues.append(_issue("degenerate_geometry", "error" if strict else "warning", "%d edge(s) have zero (or almost zero) size." % len(deg_ids), deg_ids, repair="remove_degenerate"))
    for grp in duplicate_vertices(sk):
        issues.append(_issue("duplicate_vertex", "warning", "%d vertices sit on the same point but are not joined." % len(grp), grp, repair="weld", point=sk.pos(grp[0])))
    for grp in duplicate_edges(sk):
        issues.append(_issue("duplicate_edge", "error" if strict else "warning", "%d edges lie on top of each other." % len(grp), grp, repair="remove_duplicates"))
    for eid, p in self_intersections(sk):
        issues.append(_issue("self_intersection", "error" if strict or purpose == "sweep_path" else "warning", "A curve crosses itself.", [eid], point=p))
    cross = regions.crossings(sk)
    for c in cross:
        sev = "error" if (strict or purpose == "sweep_path") else "warning"
        what = "cross each other without a shared point" if c["kind"] == "crossing" else "meet in a T without a shared vertex"
        issues.append(_issue("edges_cross" if c["kind"] == "crossing" else "t_junction", sev, "Two edges %s." % what, c["edges"], repair="split_at_intersections", point=c["point"]))
    issues += _reference_issues(sk)
    issues += _param_issues(sk)

    diag = sk.solve()
    if diag.status in ("conflicting", "invalid") or not diag.ok:
        names = sk._names(diag.conflicting)
        issues.append(_issue("constraint_conflict", "error", "The constraints contradict each other: %s." % (", ".join(n["label"] for n in names) or diag.message), [n["id"] for n in names]))
    if diag.redundant:
        names = sk._names(diag.redundant)
        issues.append(_issue("redundant_constraint", "warning", "These constraints repeat what others already say: %s." % ", ".join(n["label"] for n in names), [n["id"] for n in names]))
    if diag.ok and diag.dof > 0:
        issues.append(_issue("under_constrained", "info", "%d degree(s) of freedom remain: the profile can still move." % diag.dof, [], free=diag.free_vertices[:50], dof=diag.dof))

    # connectivity -----------------------------------------------------------------------------------------------------
    info = _closed_path_info(sk, active)
    ana = regions.analyse(sk) if active else {"regions": [], "dangling": [], "loops": 0, "crossings": []}
    region_edges = {e for r in ana["regions"] for e in r["edges"]}
    open_edges = [e for e in active if e not in region_edges]
    ends_v = info["ends"]
    near = []
    for i in range(len(ends_v)):
        for j in range(i + 1, len(ends_v)):
            if G.dist(sk.pos(ends_v[i]), sk.pos(ends_v[j])) <= gap:
                near.append((ends_v[i], ends_v[j]))
    if purpose != "sweep_path":
        if ends_v and (strict or open_edges):
            sev = "error" if strict else "info"
            issues.append(_issue("open_contour", sev, "%d open end(s): the outline is not closed%s." % (len(ends_v), " (profiles for this operation must be closed)" if strict else ""),
                                 ends_v, repair="close_gaps" if near else None, point=sk.pos(ends_v[0]), edges=open_edges))
        for a, b in near:
            issues.append(_issue("small_gap", "error" if strict else "warning", "A gap of %.4g %s separates two ends." % (G.dist(sk.pos(a), sk.pos(b)), sk.unit), [a, b], repair="close_gaps", point=G.mid(sk.pos(a), sk.pos(b))))
    if purpose == "sweep_path":
        if len(info["components"]) > 1:
            issues.append(_issue("path_not_single", "error", "A sweep path must be one connected path, but there are %d separate pieces." % len(info["components"]), [e for c in info["components"] for e in c[:1]]))
        if info["branches"]:
            issues.append(_issue("path_branches", "error", "The path branches at %d point(s); a sweep path cannot fork." % len(info["branches"]), info["branches"], point=sk.pos(info["branches"][0])))
        if not active:
            issues.append(_issue("path_empty", "error", "There is no path geometry (construction lines are ignored).", []))
        breaks = _tangent_breaks(sk, active)
        if breaks:
            issues.append(_issue("path_corners", "warning", "%d corner(s) where the path changes direction abruptly (largest %.1f°): a sweep may need mitred or rounded joints." % (len(breaks), max(t for _, t in breaks)),
                                 [v for v, _ in breaks], point=sk.pos(breaks[0][0])))

    if strict:
        regs = ana["regions"]
        if not regs:
            issues.append(_issue("no_region", "error", "No closed region was found: close the outline first.", []))
        if purpose in ("opening", "sweep_section") and len(regs) > 1:
            issues.append(_issue("multiple_regions", "error", "This operation needs exactly one region but the profile has %d." % len(regs), [e for r in regs for e in r["edges"][:1]]))
        elif len(regs) > 1:
            def _nested(r):
                pt = r["outer"]["points"][0]
                return any(o is not r and G.point_in_ring(pt, o["outer"]["points"]) for o in regs)
            top = [r for r in regs if not _nested(r)]
            if len(top) > 1:
                issues.append(_issue("multiple_regions", "warning", "The profile has %d separate regions: choose the one to send (a region inside another is treated as its hole)." % len(top), [e for r in top for e in r["edges"][:1]]))
        tiny = [r for r in regs if r["area"] < max(sk.tol * 100, 1e-9) ** 2]
        for r in tiny:
            issues.append(_issue("zero_area_region", "error", "A region has (almost) no area.", r["edges"][:1]))
        if purpose == "opening" and any(r["holes"] for r in regs):
            issues.append(_issue("opening_with_holes", "warning", "An opening profile contains inner loops; they will be treated as islands left in the opening.", [e for r in regs for h in r["holes"] for e in h["edges"][:1]]))
        if purpose == "revolve" and regs:
            try:
                o, d = _axis(axis)
            except ProfilerError as exc:
                issues.append(_issue("axis_invalid", "error", str(exc), []))
            else:
                sides = [G.cross(d, G.sub(p, o)) for r in regs for p in r["outer"]["points"]]
                if sides and min(sides) < -max(sk.tol * 100, 1e-6) and max(sides) > max(sk.tol * 100, 1e-6):
                    issues.append(_issue("crosses_axis", "error", "The profile lies on both sides of the revolve axis; move it to one side.", [e for r in regs for e in r["edges"][:1]]))
                elif sides and max(abs(s) for s in sides) < max(sk.tol * 100, 1e-6):
                    issues.append(_issue("on_axis", "error", "The profile lies on the revolve axis and has no width.", []))
        # nested islands
        nested = [r for r in regs if any(r is not o and r["outer"]["points"] and G.point_in_ring(r["outer"]["points"][0], o["outer"]["points"]) for o in regs)]
        if nested and purpose in ("opening", "sweep_section"):
            issues.append(_issue("nested_regions", "error", "A region sits inside another (an island).", [e for r in nested for e in r["edges"][:1]]))
    if not active and purpose != "drafting":
        issues.append(_issue("empty", "error", "The profile has no (non-construction) geometry.", []))

    order = {s: i for i, s in enumerate(SEVERITIES)}
    issues.sort(key=lambda i: (order[i["severity"]], i["code"]))
    counts = {s: sum(1 for i in issues if i["severity"] == s) for s in SEVERITIES}
    status = "fail" if counts["error"] else ("warn" if counts["warning"] else "pass")
    return {"purpose": purpose, "status": status, "suitable": counts["error"] == 0, "counts": counts, "issues": issues,
            "summary": {"edges": len(active), "regions": len(ana["regions"]), "open_ends": len(ends_v), "area": round(sum(r["area"] for r in ana["regions"]), 9),
                        "dof": diag.dof if diag.ok else None, "solver": diag.status}}


# --------------------------------------------------------------------------------------------------------------- repairs
REPAIRS = ("weld", "split_at_intersections", "remove_degenerate", "remove_duplicates", "close_gaps")


def _do_repair(sk, action: str, gap_tol: float | None) -> dict[str, Any]:
    if action == "weld":
        return topo.weld(sk)
    if action == "split_at_intersections":
        return topo.split_at_intersections(sk)
    if action == "remove_degenerate":
        ids = degenerate_edges(sk)
        if not ids:
            raise ProfilerError("no degenerate edges")
        ops.delete(sk, ids, _purge=True)
        return {"removed": len(ids)}
    if action == "remove_duplicates":
        groups = duplicate_edges(sk)
        if not groups:
            raise ProfilerError("no duplicate edges")
        removed = 0
        for g in groups:
            keep, rest = g[0], g[1:]
            for r in rest:
                if r in sk.edges and keep in sk.edges:
                    topo._replace_edge_refs(sk, r, keep)
                    del sk.d["edges"][r]
                    removed += 1
        topo._drop_orphans(sk)
        return {"removed": removed}
    if action == "close_gaps":
        gap = float(gap_tol) if gap_tol is not None else max(0.005 * _diag(sk), sk.tol * 100)
        info = _closed_path_info(sk, _active_edges(sk))
        ends = list(info["ends"])
        merged, used = 0, set()
        pairs = sorted(((G.dist(sk.pos(a), sk.pos(b)), a, b) for i, a in enumerate(ends) for b in ends[i + 1:]), key=lambda t: t[0])
        for dst, a, b in pairs:
            if dst > gap or a in used or b in used or a not in sk.vertices or b not in sk.vertices:
                continue
            topo.merge_vertices(sk, [a, b], at="centroid")
            used.update((a, b))
            merged += 1
        if not merged:
            raise ProfilerError("no gap smaller than %.4g %s to close" % (gap, sk.unit))
        return {"closed": merged, "gap_tolerance": gap}
    raise ProfilerError("unknown repair %r (use %s)" % (action, ", ".join(REPAIRS)))


def repair(sk, action: str, *, purpose: str = "drafting", apply: bool = False, gap_tol: float | None = None, axis: dict | None = None) -> dict[str, Any]:
    """Preview (default) or apply a repair. The preview runs on a clone and reports which issues it would fix and which new ones it would cause."""
    before = check(sk, purpose, axis=axis, gap_tol=gap_tol)
    trial = sk.clone()
    result = _do_repair(trial, action, gap_tol)
    after = check(trial, purpose, axis=axis, gap_tol=gap_tol)
    b_ids, a_ids = {i["id"] for i in before["issues"]}, {i["id"] for i in after["issues"]}
    out = {"action": action, "applied": False, "result": result, "fixes": [i for i in before["issues"] if i["id"] not in a_ids],
           "new_issues": [i for i in after["issues"] if i["id"] not in b_ids], "before": before["counts"], "after": after["counts"],
           "geometry_changes": {"edges": [len(sk.edges), len(trial.edges)], "vertices": [len(sk.vertices), len(trial.vertices)]}}
    if apply:
        _do_repair(sk, action, gap_tol)
        out["applied"] = True
    return out
