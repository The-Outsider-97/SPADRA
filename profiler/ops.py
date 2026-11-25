"""Transforms, copies, arrays, groups and deletion. Every function mutates the sketch passed in and either completes or raises; the session wraps each
call in a transaction that restores the sketch on failure, so a half-applied edit never survives.

Moving geometry is *constraint-aware*: the moved vertices are placed exactly (held fixed for the solve) and everything connected to them by constraints
follows by the solver's least-change solution. If a pinned vertex, a driving dimension or another lock forbids it, the edit is refused with the
conflicting constraints named. Rigid-group members always move as a unit.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

from src.utils.spatial_formats.common.ids import new_id

from . import geom as G
from .errors import ProfilerConflict, ProfilerError, ProfilerNotFound

ORIENTATION = {"horizontal", "vertical", "parallel", "perpendicular", "collinear", "point_on", "tangent", "tangent_at", "symmetric", "midpoint"}


# ------------------------------------------------------------------------------------------------------------- selection
def edge_vertices(sk, eid: str) -> list[str]:
    e = sk.edge(eid)
    vs = list(e.get("v", []))
    if "c" in e:
        vs.append(e["c"])
    for h in e.get("handles", []):
        vs.extend(h)
    return vs


def resolve(sk, ids: Iterable[str]) -> tuple[set[str], set[str]]:
    """(vertices, edges) named by ``ids``: vertices, edges (with all their vertices), or group/component ids (with all members)."""
    vs: set[str] = set()
    es: set[str] = set()
    for i in ids:
        if i in sk.vertices:
            vs.add(i)
        elif i in sk.edges:
            es.add(i)
            vs.update(edge_vertices(sk, i))
        elif i in sk.d["groups"]:
            v2, e2 = resolve(sk, sk.d["groups"][i]["members"])
            vs |= v2
            es |= e2
        elif i in sk.d["components"] and isinstance(sk.d["components"][i], dict) and "members" in sk.d["components"][i]:
            v2, e2 = resolve(sk, sk.d["components"][i]["members"])
            vs |= v2
            es |= e2
        else:
            raise ProfilerNotFound("nothing with id %s" % i)
    return vs, es


def rigid_expand(sk, vs: set[str]) -> set[str]:
    """Rigid groups move as one: any member vertex pulls in the rest of its group."""
    out = set(vs)
    changed = True
    while changed:
        changed = False
        for g in sk.d["groups"].values():
            if g["kind"] != "rigid":
                continue
            gv, _ = resolve(sk, g["members"])
            if gv & out and not gv <= out:
                out |= gv
                changed = True
    return out


def _check_unlocked(sk, es: Iterable[str]) -> None:
    for e in es:
        ed = sk.edge(e)
        if ed.get("locked") or sk.layer(ed["layer"])["locked"]:
            raise ProfilerError("that line's layer is locked: unlock the layer to edit it" if sk.layer(ed["layer"])["locked"] else "that line is locked: untick Locked in Properties to edit it")


def _apply_positions(sk, new_pos: dict[str, tuple[float, float]], scale_circles: tuple[set[str], float] | None = None) -> dict:
    for v, p in new_pos.items():
        sk.vertices[v]["x"], sk.vertices[v]["y"] = p
    if scale_circles:
        eids, k = scale_circles
        for e in eids:
            ed = sk.edge(e)
            for key in ("r", "rx", "ry"):
                if key in ed:
                    sk.d["scalars"][ed[key]] *= k
    sk.settle(fixed_points=list(new_pos))                     # the moved vertices are placed exactly; the rest follows its constraints
    return sk.solve().to_dict()                               # and the status reported is that of the real constraint system (nothing held fixed)


# ------------------------------------------------------------------------------------------------------------ transforms
def translate(sk, ids: Sequence[str], dx: float, dy: float) -> dict:
    vs, es = resolve(sk, ids)
    vs = rigid_expand(sk, vs)
    _check_unlocked(sk, es)
    return _apply_positions(sk, {v: (sk.pos(v)[0] + dx, sk.pos(v)[1] + dy) for v in vs})


def drag_vertex(sk, vid: str, to: Sequence[float]) -> dict:
    """Drag one vertex to ``to``. A free vertex goes there and connected geometry follows its constraints; a vertex in a rigid group moves the whole group by the
    same offset (a rigid group is never deformed by a drag); a pinned or over-constrained vertex refuses the drag."""
    if vid not in sk.vertices:
        raise ProfilerNotFound("no vertex %s" % vid)
    p = sk.pos(vid)
    tx, ty = float(to[0]), float(to[1])
    members = rigid_expand(sk, {vid})
    if len(members) > 1:
        return _apply_positions(sk, {v: (sk.pos(v)[0] + tx - p[0], sk.pos(v)[1] + ty - p[1]) for v in members})
    return _apply_positions(sk, {vid: (tx, ty)})


def release_orientation(sk, vs: set[str]) -> list[dict]:
    """Switch off what pins a shape to the sketch's own axes, for the shapes wholly inside ``vs``: horizontal/vertical constraints and pins are *disabled*
    (never deleted: they stay in the list and can be switched back on) and horizontal/vertical dimensions become aligned ones with the same value.
    Returns what was changed, so the caller can tell the person."""
    released: list[dict] = []
    horizontal: list[str] = []
    vertical: list[str] = []
    for c in sk.d["constraints"]:
        if not c.get("enabled", True) or c["kind"] not in ("horizontal", "vertical", "fixed"):
            continue
        members: set[str] = set()
        for r in c["refs"]:
            members |= set(edge_vertices(sk, r)) if r in sk.edges else {r}
        if members and members <= vs:
            c["enabled"] = False
            released.append({"what": "constraint", "id": c["id"], "kind": c["kind"]})
            if c["kind"] in ("horizontal", "vertical") and c["refs"][0] in sk.edges:
                (horizontal if c["kind"] == "horizontal" else vertical).append(c["refs"][0])
    for did, d in sk.d["dimensions"].items():
        if d["type"] in ("horizontal", "vertical") and set(d["refs"]) <= vs:
            released.append({"what": "dimension", "id": did, "kind": d["type"], "now": "aligned"})
            d["type"] = "aligned"
    # what made the shape square to the axes also made its lines parallel and square to each other: keep that, so a rotated rectangle is still a rectangle
    for group in (horizontal, vertical):
        for e in group[1:]:
            released.append({"what": "added", "id": sk.add_constraint("parallel", [group[0], e], origin="release"), "kind": "parallel"})
    if horizontal and vertical:
        released.append({"what": "added", "id": sk.add_constraint("perpendicular", [horizontal[0], vertical[0]], origin="release"), "kind": "perpendicular"})
    return released


def rotate(sk, ids: Sequence[str], angle: float, about: Sequence[float] = (0.0, 0.0), *, degrees: bool = True, release: bool = False) -> dict:
    """Rotate. Constraints that fix a direction or position in the sketch frame (horizontal, vertical, pins) cannot survive a turn: without ``release`` the
    turn is refused naming them; with it they are switched off for the rotated shapes and listed in the result (``released``)."""
    ang = math.radians(angle) if degrees else angle
    vs, es = resolve(sk, ids)
    vs = rigid_expand(sk, vs)
    _check_unlocked(sk, es)
    c = (float(about[0]), float(about[1]))
    released = release_orientation(sk, vs) if release else []
    out = _apply_positions(sk, {v: G.rotate_pt(sk.pos(v), c, ang) for v in vs})
    out["released"] = released
    return out


def scale(sk, ids: Sequence[str], factor: float, about: Sequence[float] = (0.0, 0.0)) -> dict:
    """Resize. Driving distance/radius dimensions and pins of the selection are scaled with it (their *relationships* are kept); angular dimensions are not touched.
    A rigid group rejects scaling (authoritative: scale is rejected by default); a dimension driven by a parameter refuses: edit the parameter instead."""
    if factor <= 0:
        raise ProfilerError("the scale factor must be positive")
    vs, es = resolve(sk, ids)
    for g in sk.d["groups"].values():
        if g["kind"] == "rigid" and resolve(sk, g["members"])[0] & vs:
            raise ProfilerError("a rigid group cannot be scaled (it would deform it); ungroup it or use a parametric component")
    _check_unlocked(sk, es)
    c = (float(about[0]), float(about[1]))
    for did, d in sk.d["dimensions"].items():
        if d["mode"] in ("driving", "formula") and d.get("value") is not None and d["type"] != "angular" and set(d["refs"]) & (vs | es):
            if d.get("param"):
                raise ProfilerError("dimension %s is driven by parameter %s: change the parameter instead of scaling" % (did[:8], d["param"]))
            d["value"] *= factor
    for con in sk.d["constraints"]:
        if con["kind"] == "fixed" and con["refs"][0] in vs:
            p = sk.pos(con["refs"][0])
            con["value"] = [c[0] + (p[0] - c[0]) * factor, c[1] + (p[1] - c[1]) * factor]
        elif con["kind"] in ("distance", "hdistance", "vdistance", "radius", "diameter", "point_line_distance") and "value" in con and set(con["refs"]) & (vs | es):
            con["value"] *= factor
    new = {v: (c[0] + (sk.pos(v)[0] - c[0]) * factor, c[1] + (sk.pos(v)[1] - c[1]) * factor) for v in vs}
    return _apply_positions(sk, new, (es, factor))


def mirror(sk, ids: Sequence[str], axis_a: Sequence[float], axis_b: Sequence[float], *, copy: bool = True, constrain: bool = True) -> dict:
    """Mirror about the line through ``axis_a``-``axis_b``. ``copy`` keeps the originals (a mirrored copy); ``constrain`` ties each pair of mirrored vertices with
    a *symmetric* constraint about a construction axis line, so the copy stays a mirror image when the original is edited."""
    a, b = (float(axis_a[0]), float(axis_a[1])), (float(axis_b[0]), float(axis_b[1]))
    if G.dist(a, b) < G.EPS:
        raise ProfilerError("the mirror axis needs two different points")
    vs, es = resolve(sk, ids)
    _check_unlocked(sk, es)
    if not copy:
        return _apply_positions(sk, {v: G.mirror_pt(sk.pos(v), a, b) for v in vs}) | {"mirrored_in_place": True}
    mp, me, mv = _clone(sk, vs, es, lambda p: G.mirror_pt(p, a, b), flip_arcs=True)
    out: dict[str, Any] = {"vertex_map": mp, "edge_map": me}
    if constrain:
        axis = sk.add_line(a, b, construction=True)
        for v, nv in mp.items():
            if G.dist(sk.pos(v), sk.pos(nv)) > 1e-9:
                sk.add_constraint("symmetric", [v, nv, axis], origin="mirror")
        out["axis"] = axis
    sk.settle()
    return out


def _clone(sk, vs: set[str], es: set[str], f, flip_arcs: bool = False, copy_constraints: bool = True) -> tuple[dict, dict, dict]:
    vmap: dict[str, str] = {}
    for v in sorted(vs):
        p = f(sk.pos(v))
        nv = new_id()
        sk.vertices[nv] = {"x": p[0], "y": p[1]}
        vmap[v] = nv
    smap: dict[str, str] = {}
    emap: dict[str, str] = {}
    for eid in sorted(es):
        e = sk.edge(eid)
        n = {k: (list(val) if isinstance(val, list) else val) for k, val in e.items()}
        n["v"] = [vmap[v] for v in e.get("v", [])]
        if "c" in e:
            n["c"] = vmap[e["c"]]
        if "handles" in e:
            n["handles"] = [[vmap[a], vmap[b]] for a, b in e["handles"]]
        for key in ("r", "rx", "ry"):
            if key in e:
                sid = new_id()
                sk.d["scalars"][sid] = sk.d["scalars"][e[key]]
                n[key] = sid
        if flip_arcs and e["type"] == "arc":
            n["v"] = [n["v"][1], n["v"][0]]
        nid = new_id()
        sk.d["edges"][nid] = n
        emap[eid] = nid
    if copy_constraints:
        ref_map = {**vmap, **emap}
        for c in list(sk.d["constraints"]):
            if c["kind"] != "fixed" and c.get("origin") != "mirror" and c["refs"] and all(r in ref_map for r in c["refs"]):
                nc = {k: (list(v) if isinstance(v, list) else v) for k, v in c.items()}
                nc["id"], nc["refs"], nc["origin"] = new_id(), [ref_map[r] for r in c["refs"]], "copy"
                if flip_arcs and nc["kind"] in ("tangent_at",):
                    continue
                sk.d["constraints"].append(nc)
    return vmap, emap, smap


def copy(sk, ids: Sequence[str], dx: float = 0.0, dy: float = 0.0) -> dict:
    """Copy/duplicate with the constraints among the copied entities (dimensions and pins are not copied)."""
    vs, es = resolve(sk, ids)
    if not es and vs:                       # vertices only: copy the edges they fully contain
        es = {e for e in sk.edges if set(edge_vertices(sk, e)) <= vs}
    vs = vs | {v for e in es for v in edge_vertices(sk, e)}
    vm, em, _ = _clone(sk, vs, es, lambda p: (p[0] + dx, p[1] + dy))
    sk.settle()
    return {"vertex_map": vm, "edge_map": em}


def align(sk, ids: Sequence[str], how: str, target: float | Sequence[float]) -> dict:
    """Align the selection's bounding box to a coordinate/point: ``left|right|center_x`` (target x) or ``bottom|top|center_y`` (target y)."""
    vs, es = resolve(sk, ids)
    b = sk.bounds(list(es | vs)) if (es or vs) else None
    if b is None:
        raise ProfilerError("nothing to align")
    t = float(target if not isinstance(target, (list, tuple)) else (target[0] if how in ("left", "right", "center_x") else target[1]))
    dx = dy = 0.0
    if how == "left": dx = t - b[0]
    elif how == "right": dx = t - b[2]
    elif how == "center_x": dx = t - (b[0] + b[2]) / 2
    elif how == "bottom": dy = t - b[1]
    elif how == "top": dy = t - b[3]
    elif how == "center_y": dy = t - (b[1] + b[3]) / 2
    else:
        raise ProfilerError("align to left, right, center_x, bottom, top or center_y")
    return translate(sk, list(ids), dx, dy)


def stretch(sk, window: Sequence[float], dx: float, dy: float) -> dict:
    """Move every vertex inside the window ``[x0, y0, x1, y1]``; edges attached to them stretch (CAD 'stretch')."""
    x0, y0, x1, y1 = sorted((window[0], window[2]))[0], sorted((window[1], window[3]))[0], sorted((window[0], window[2]))[1], sorted((window[1], window[3]))[1]
    inside = {v for v, p in sk.vertices.items() if x0 <= p["x"] <= x1 and y0 <= p["y"] <= y1}
    if not inside:
        raise ProfilerError("the window contains no vertex")
    return _apply_positions(sk, {v: (sk.pos(v)[0] + dx, sk.pos(v)[1] + dy) for v in inside})


# ---------------------------------------------------------------------------------------------------------------- arrays
def _resolve_number(sk, v):
    if isinstance(v, str):
        from . import params as PM
        ev = PM.evaluate(sk)
        if v not in ev:
            raise ProfilerError("pattern refers to unknown parameter %r" % v)
        return ev[v]["value"]
    return v


def array(sk, ids: Sequence[str], kind: str, *, linked: bool = False, **spec) -> dict:
    """Rectangular (``count_x, count_y, dx, dy``), polar (``count, center, angle``) or path (``path_edge, count`` or ``spacing``) array. Numbers may be parameter
    names (length/integer parameters), which makes the pattern parametric; ``linked`` keeps the pattern so it regenerates when the source or a parameter changes."""
    vs, es = resolve(sk, ids)
    if kind not in ("rect", "polar", "path"):
        raise ProfilerError("array kind is rect, polar or path")
    pid = new_id()
    rec = {"id": pid, "kind": kind, "source": list(ids), "spec": dict(spec), "instances": []} if linked else None
    out = _build_array(sk, vs, es, kind, spec, rec)
    if linked:
        sk.d.setdefault("features", []).append({"type": "pattern", **rec})
    return out | {"pattern": pid if linked else None}


def _build_array(sk, vs, es, kind, spec, rec) -> dict:
    created = {"edges": [], "vertices": []}
    def place(f, tag):
        vm, em, _ = _clone(sk, vs, es, f, copy_constraints=True)
        created["edges"].extend(em.values())
        created["vertices"].extend(vm.values())
        if rec is not None:
            rec["instances"].append({"vertices": list(vm.values()), "edges": list(em.values()), "tag": tag})
    n = lambda k, default=None: _resolve_number(sk, spec.get(k, default))
    if kind == "rect":
        nx, ny = int(n("count_x", 1)), int(n("count_y", 1))
        dx, dy = float(n("dx", 0.0)), float(n("dy", 0.0))
        if nx < 1 or ny < 1 or nx * ny > 2500 or nx * ny == 1:
            raise ProfilerError("a rectangular array needs 2..2500 items (count_x * count_y)")
        for i in range(nx):
            for j in range(ny):
                if i or j:
                    place(lambda p, i=i, j=j: (p[0] + i * dx, p[1] + j * dy), (i, j))
    elif kind == "polar":
        count = int(n("count", 0))
        center = spec.get("center", (0.0, 0.0))
        total = float(n("angle", 360.0))
        if count < 2 or count > 2500:
            raise ProfilerError("a polar array needs 2..2500 items")
        step = total / count if abs(total - 360.0) < 1e-9 else total / (count - 1)
        for i in range(1, count):
            place(lambda p, i=i: G.rotate_pt(p, (float(center[0]), float(center[1])), math.radians(step * i)), i)
    else:
        path = spec.get("path_edge")
        if path not in sk.edges:
            raise ProfilerError("path array needs path_edge (an existing edge id)")
        pts = sk.edge_polyline(path, 0.001)
        total = G.polyline_length(pts)
        count = int(n("count", 0)) if spec.get("count") is not None else 0
        spacing = float(n("spacing", 0.0)) if spec.get("spacing") is not None else 0.0
        if count < 2 and spacing <= 0:
            raise ProfilerError("give a count (>= 2) or a spacing for a path array")
        if count < 2:
            count = int(total // spacing) + 1
        else:
            spacing = total / (count - 1)
        ref = G.ring_centroid([sk.pos(v) for v in vs]) if vs else pts[0]
        for i in range(1, count):
            target = _point_along(pts, spacing * i)
            place(lambda p, t=target: (p[0] + t[0] - pts[0][0], p[1] + t[1] - pts[0][1]), i)
    return created


def _point_along(pts, s):
    for i in range(len(pts) - 1):
        l = G.dist(pts[i], pts[i + 1])
        if s <= l + 1e-12:
            return G.lerp(pts[i], pts[i + 1], s / l if l else 0.0)
        s -= l
    return pts[-1]


def regenerate_patterns(sk) -> int:
    """Rebuild every linked pattern from its (possibly changed) source and parameters; old instances are removed first. Returns how many were rebuilt."""
    n = 0
    for f in sk.d.get("features", []):
        if f["type"] != "pattern":
            continue
        for inst in f["instances"]:
            delete(sk, inst["edges"], _purge=True)
            for v in inst["vertices"]:
                sk.vertices.pop(v, None)
        f["instances"] = []
        vs, es = resolve(sk, f["source"])
        _build_array(sk, vs, es, f["kind"], f["spec"], f)
        n += 1
    return n


# ---------------------------------------------------------------------------------------------------------------- delete
def used_vertices(sk) -> set[str]:
    used = set()
    for eid in sk.edges:
        used.update(edge_vertices(sk, eid))
    for c in sk.d["constraints"]:
        used.update(r for r in c["refs"] if r in sk.vertices)
    for d in sk.d["dimensions"].values():
        used.update(r for r in d["refs"] if r in sk.vertices)
    return used


def delete(sk, ids: Sequence[str], *, _purge: bool = False) -> dict:
    """Delete vertices/edges/dimensions/comments/texts. Constraints and dimensions that referenced a deleted entity are *kept* but flagged unresolved (and are not
    solved) so nothing driving is silently dropped; delete them explicitly. Vertices left unused by deleted edges are removed."""
    removed = {"edges": [], "vertices": [], "dimensions": [], "other": []}
    for i in ids:
        if i in sk.edges:
            _check_unlocked(sk, [i])
            del sk.d["edges"][i]
            removed["edges"].append(i)
        elif i in sk.d["dimensions"]:
            del sk.d["dimensions"][i]
            removed["dimensions"].append(i)
        elif i in sk.d["comments"]:
            del sk.d["comments"][i]; removed["other"].append(i)
        elif i in sk.d["texts"]:
            del sk.d["texts"][i]; removed["other"].append(i)
        elif i in sk.d["groups"]:
            del sk.d["groups"][i]; removed["other"].append(i)
        elif i in sk.vertices:
            if any(i in edge_vertices(sk, e) for e in sk.edges):
                raise ProfilerError("vertex %s is used by an edge: delete the edge" % i[:8])
            del sk.d["vertices"][i]
            removed["vertices"].append(i)
        else:
            raise ProfilerNotFound("nothing with id %s" % i)
    # orphan vertices (unused by any edge, constraint or dimension) go
    used = used_vertices(sk)
    protected = {c["target"] for c in sk.d["comments"].values() if "target" in c}
    for v in list(sk.vertices):
        if v not in used and v not in protected and v not in ids:
            del sk.d["vertices"][v]
            removed["vertices"].append(v)
    live = set(sk.vertices) | set(sk.edges) | set(sk.d["dimensions"])
    flagged = []
    for c in sk.d["constraints"]:
        if c.get("state") != "unresolved" and any(r not in live for r in c["refs"]):
            if _purge or c.get("origin") in ("auto", "mirror", "copy", "eq") and not any(r in live for r in c["refs"]):
                c["_drop"] = True
            else:
                c["state"] = "unresolved"
                c["provenance"] = {"missing": [r for r in c["refs"] if r not in live]}
                flagged.append(c["id"])
    sk.d["constraints"] = [c for c in sk.d["constraints"] if not c.get("_drop")]
    from . import dims
    unresolved_dims = dims.refresh_unresolved(sk)
    for g in sk.d["groups"].values():
        g["members"] = [m for m in g["members"] if m in live or m in sk.d["groups"]]
    for c in list(sk.d["comments"].values()):
        if c.get("target") and c["target"] not in live:
            c["state"] = "orphaned"
    return {"removed": removed, "unresolved_constraints": flagged, "unresolved_dimensions": unresolved_dims}


# ---------------------------------------------------------------------------------------------------------------- groups
def make_group(sk, ids: Sequence[str], kind: str = "rigid", name: str | None = None) -> str:
    """``rigid``: members translate/rotate together and reject scaling. ``component``: a parametric component (see :func:`make_component`).
    A *selection group* is temporary UI state and has no representation in the document."""
    if kind == "selection":
        raise ProfilerError("a selection group is a temporary selection, not a document object; use rigid or component to keep one")
    if kind == "component":
        return make_component(sk, ids, name or "Component")
    if kind != "rigid":
        raise ProfilerError("group kind is rigid or component")
    resolve(sk, ids)
    gid = new_id()
    sk.d["groups"][gid] = {"id": gid, "kind": "rigid", "name": name or "Group", "members": list(ids)}
    return gid


def ungroup(sk, gid: str) -> None:
    if gid in sk.d["groups"]:
        del sk.d["groups"][gid]
    elif gid in sk.d["components"]:
        del sk.d["components"][gid]
    else:
        raise ProfilerNotFound("no group %s" % gid)


def make_component(sk, ids: Sequence[str], name: str, *, origin: Sequence[float] | None = None, params: Sequence[str] = ()) -> str:
    """A parametric component: editable local geometry, its own internal constraints/dimensions and the parameters it exposes, with a local origin vertex."""
    vs, es = resolve(sk, ids)
    if not vs:
        raise ProfilerError("a component needs geometry")
    if origin is None:
        b = sk.bounds(list(es | vs))
        origin = (b[0], b[1])
    ov = sk.add_vertex(origin)
    cid = new_id()
    sk.d["components"][cid] = {"id": cid, "kind": "component", "name": name, "members": list(ids), "origin": ov, "params": list(params), "type_id": cid, "instances": []}
    return cid


def instantiate_component(sk, cid: str, at: Sequence[float]) -> dict:
    """Place another instance. Internal constraints and the type's parameter links are copied, so a *type* parameter change reshapes every instance (an instance
    override would be a separate parameter)."""
    comp = sk.d["components"].get(cid)
    if not comp or "members" not in comp:
        raise ProfilerNotFound("no component %s" % cid)
    vs, es = resolve(sk, comp["members"])
    o = sk.pos(comp["origin"])
    dx, dy = float(at[0]) - o[0], float(at[1]) - o[1]
    vm, em, _ = _clone(sk, vs, es, lambda p: (p[0] + dx, p[1] + dy))
    ref_map = {**vm, **em}
    for did, d in list(sk.d["dimensions"].items()):
        if d["refs"] and all(r in ref_map for r in d["refs"]):
            nd = {k: (list(v) if isinstance(v, list) else v) for k, v in d.items()}
            nd["id"], nd["refs"] = new_id(), [ref_map[r] for r in d["refs"]]
            sk.d["dimensions"][nd["id"]] = nd
    iid = new_id()
    comp["instances"].append({"id": iid, "origin": [at[0], at[1]], "vertices": list(vm.values()), "edges": list(em.values())})
    sk.settle()
    return {"instance": iid, "vertex_map": vm, "edge_map": em}
