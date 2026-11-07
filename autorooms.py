"""Rooms that make themselves: wherever the walls of a level close a space, there is a room, with no roof or floor needed.

How a space is found. The centre lines of a level's walls are cut wherever they cross or meet (an end that touches the side of another wall
cuts it too). That leaves a planar network of segments. Segments that lead nowhere (a wall that ends inside a room) are pruned, since they
do not enclose anything. Walking round the network, keeping the inside on the left, gives each enclosed face once. A room's outline is that
face moved in to the *inner faces* of its walls: every edge is offset by half the thickness of the wall that carries it.

What is kept. An automatic room is an ordinary room (name, number, height, tags, schedules all work); its outline is the one part the walls
decide. It carries ``spadra:auto`` and a ``spadra:probe`` point inside it, which is how it is recognised again after the walls change: the
room whose probe point still lies in a space keeps its name, number and height. A new space gets a new room; a space that is no longer
closed loses its room, unless the person has made it their own (named, numbered or re-heighted it): that room is kept, marked *not enclosed*
(the model check says so), with its outline as it last was, and takes its space back when the walls close again. A room the person drew by hand
is never touched, and a space that already holds a hand-drawn room is not given a second one.

Limits, stated plainly: a space with a separate closed ring of walls inside it (an island) is skipped, because a room outline here has no
holes; walls are the only boundary (no room-separation lines); and the outline of a space whose walls are thicker than the space is wide
cannot be made and is skipped.
"""
from __future__ import annotations

import hashlib
import math
from typing import Any, Mapping, Sequence

from . import elements as E
from . import geometry as G
from . import rooms as R

Pt = tuple[float, float]
interior_point = R.interior_point
TOL = E.JOIN_TOLERANCE


# ------------------------------------------------------------------------------------------------------------------------- settings
def settings(project: Any) -> dict[str, Any]:
    """The project's automatic-room setting (on unless the person turned it off) and the wall signature each level was last worked out for."""
    st = project.manifest.get("auto_rooms")
    if not isinstance(st, dict):
        st = {"on": True, "sig": {}}
        project.manifest["auto_rooms"] = st
    st.setdefault("on", True)
    st.setdefault("sig", {})
    return st


def is_on(project: Any) -> bool:
    st = project.manifest.get("auto_rooms")
    return True if not isinstance(st, dict) else bool(st.get("on", True))


# ------------------------------------------------------------------------------------------------------------------------- the network
class _Nodes:
    """Points merged when they lie within the join tolerance of one another."""

    def __init__(self, blocked: set[frozenset[str]] | None = None) -> None:
        self.pts: list[Pt] = []
        self._cells: dict[tuple[int, int], list[int]] = {}
        self._roles: list[set[str]] = []
        self.blocked = blocked or set()

    def get(self, p: Pt, role: str | None = None) -> int:
        cx, cy = math.floor(p[0] / (4 * TOL)), math.floor(p[1] / (4 * TOL))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for i in self._cells.get((cx + dx, cy + dy), ()):
                    if math.dist(self.pts[i], p) <= TOL:
                        if role and any(frozenset((role, old)) in self.blocked for old in self._roles[i]):
                            continue                            # an explicitly open corner must not close the space topologically
                        if role:
                            self._roles[i].add(role)
                        return i
        self.pts.append(p)
        self._roles.append({role} if role else set())
        self._cells.setdefault((cx, cy), []).append(len(self.pts) - 1)
        return len(self.pts) - 1


def _cut_points(i: int, walls: Sequence[tuple[str, Pt, Pt, float]]) -> list[float]:
    """Distances along wall i at which it is cut: its ends, where others cross it, and where another's end touches it."""
    _, a, b, _ = walls[i]
    length = math.dist(a, b)
    u = ((b[0] - a[0]) / length, (b[1] - a[1]) / length)
    cuts = [0.0, length]
    for j, (_, c, d, _) in enumerate(walls):
        if j == i:
            continue
        for q in (c, d):                                                     # another wall's end on this wall
            t = (q[0] - a[0]) * u[0] + (q[1] - a[1]) * u[1]
            if abs(-(q[0] - a[0]) * u[1] + (q[1] - a[1]) * u[0]) <= TOL and -TOL <= t <= length + TOL:
                cuts.append(min(max(t, 0.0), length))
        m = math.dist(c, d)
        v = ((d[0] - c[0]) / m, (d[1] - c[1]) / m)
        hit = G.intersect_lines(a, u, c, v)                                  # a proper crossing
        if hit is not None and TOL < hit[0] < length - TOL and -TOL <= hit[1] <= m + TOL:
            cuts.append(hit[0])
    return sorted(cuts)


def _topological_axes(walls: Sequence[tuple[str, Pt, Pt, float]]) -> list[tuple[str, Pt, Pt, float]]:
    """Connect a partition ending *at a host wall's face* to its centre line.

    Room boundaries follow wall centre lines, but a modeller correctly draws
    a butt-joined partition to the host's near face rather than through its
    material.  That small covered interval must be part of the room *graph*
    even though the physical partition must not extend into the host core.
    Only snap an endpoint already within the actual host thickness, and only
    when the two axes intersect inside the host's span.  This never changes
    the authored wall endpoints, 3D geometry, or structural join policy.
    """
    connected = []
    for i, (oid, a, b, thick) in enumerate(walls):
        length = math.dist(a, b)
        if length <= 1e-6:
            connected.append((oid, a, b, thick))
            continue
        direction = G.unit(G.sub(b, a))
        ends = []
        for end in (a, b):
            options: list[tuple[float, str, Pt]] = []
            for j, (hid, h0, h1, host_thickness) in enumerate(walls):
                if i == j:
                    continue
                host_length = math.dist(h0, h1)
                if host_length <= 1e-6:
                    continue
                host_dir = G.unit(G.sub(h1, h0))
                sin_angle = abs(G.cross(direction, host_dir))
                if sin_angle <= 1e-6:                                 # collinear / near-parallel walls cannot provide a unique T
                    continue
                relative = G.sub(end, h0)
                host_position = G.dot(relative, host_dir)
                face_distance = abs(G.cross(host_dir, relative))
                if not (-TOL <= host_position <= host_length + TOL and face_distance <= host_thickness / 2 + TOL):
                    continue                                         # a genuine gap outside the material must remain open
                hit = G.intersect_lines(end, direction, h0, host_dir)
                if hit is None or not -TOL <= hit[1] <= host_length + TOL:
                    continue
                extension = abs(hit[0])
                if extension <= (host_thickness / 2 + TOL) / sin_angle + TOL:
                    options.append((extension, hid, G.add(end, G.mul(direction, hit[0]))))
            # A stable tie break is important at multi-wall intersections.
            ends.append(min(options, key=lambda v: (round(v[0], 9), v[1]))[2] if options else end)
        connected.append((oid, ends[0], ends[1], thick))
    return connected


def _faces(walls: Sequence[tuple[str, Pt, Pt, float]], *, blocked: set[frozenset[str]] | None = None) -> list[dict[str, Any]]:
    """Enclosed faces of the wall network, including their actual bounding wall identities."""
    nodes = _Nodes(blocked)
    thickness: dict[tuple[int, int], float] = {}
    owners: dict[tuple[int, int], set[str]] = {}
    for i, (wid, a, b, t) in enumerate(walls):
        length = math.dist(a, b)
        u = ((b[0] - a[0]) / length, (b[1] - a[1]) / length)
        ids = [nodes.get((a[0] + u[0] * s, a[1] + u[1] * s),
                         f"{wid}:start" if abs(s) <= TOL else f"{wid}:end" if abs(s - length) <= TOL else None)
               for s in _cut_points(i, walls)]
        for p, q in zip(ids, ids[1:]):
            if p != q:
                key = (min(p, q), max(p, q))
                thickness[key] = max(thickness.get(key, 0.0), t)
                owners.setdefault(key, set()).add(wid)
    adj: dict[int, set[int]] = {}
    for p, q in thickness:
        adj.setdefault(p, set()).add(q)
        adj.setdefault(q, set()).add(p)
    pruned = True
    while pruned:                                                            # a wall that ends in the open encloses nothing
        pruned = False
        for n in [n for n, nb in adj.items() if len(nb) <= 1]:
            for m in adj.pop(n):
                adj[m].discard(n)
                key = (min(n, m), max(n, m))
                thickness.pop(key, None)
                owners.pop(key, None)
            pruned = True
    P = nodes.pts
    out_edges = {n: sorted(nb, key=lambda m, n=n: math.atan2(P[m][1] - P[n][1], P[m][0] - P[n][0])) for n, nb in adj.items()}
    parent = {n: n for n in adj}

    def find(n: int) -> int:
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    for p, q in thickness:
        parent[find(p)] = find(q)
    seen: set[tuple[int, int]] = set()
    faces: list[dict[str, Any]] = []
    for n in sorted(adj):
        for m in out_edges[n]:
            if (n, m) in seen:
                continue
            loop, edge = [], (n, m)
            while edge not in seen:
                seen.add(edge)
                loop.append(edge)
                u, v = edge
                around = out_edges[v]
                edge = (v, around[(around.index(u) - 1) % len(around)])      # the first edge clockwise from the one we came by: the inside stays on the left
            if edge != (n, m):
                continue                                                     # not a closed walk (cannot happen in a pruned network, but never loop forever)
            ring = [e[0] for e in loop]
            pts = [P[i] for i in ring]
            if G.signed_area(pts) <= 1e-9:                                   # the outside of a group of walls
                continue
            if len({frozenset(e) for e in loop}) != len(loop):               # a wall walked twice: a bridge between two spaces, not a boundary
                continue
            faces.append({"nodes": ring, "points": pts, "thick": [thickness[(min(e), max(e))] for e in loop],
                          "wall_ids": sorted({wid for e in loop for wid in owners[(min(e), max(e))]}), "component": find(n)})
    return faces


def _inset(points: Sequence[Pt], thick: Sequence[float]) -> list[Pt] | None:
    """The face moved in to the inner faces of its walls (each edge by half its wall's thickness), or None when that does not make a room."""
    n = len(points)
    lines = []
    for k in range(n):
        a, b = points[k], points[(k + 1) % n]
        d = G.unit(G.sub(b, a))
        normal = G.left(d)
        lines.append((G.add(a, G.mul(normal, thick[k] / 2)), d, normal, thick[k] / 2))
    out: list[Pt] = []
    for k in range(n):
        prev, cur = lines[k - 1], lines[k]
        hit = G.intersect_lines(prev[0], prev[1], cur[0], cur[1])
        if hit is None:                                                      # collinear: a step where the thickness changes, otherwise the one point
            base = points[k]
            first, second = G.add(base, G.mul(prev[2], prev[3])), G.add(base, G.mul(cur[2], cur[3]))
            out.extend([first] if math.dist(first, second) < 1e-9 else [first, second])
        else:
            out.append(G.add(prev[0], G.mul(prev[1], hit[0])))
    cleaned: list[Pt] = []
    for p in out:
        if not cleaned or math.dist(p, cleaned[-1]) > 1e-9:
            cleaned.append(p)
    if len(cleaned) > 1 and math.dist(cleaned[0], cleaned[-1]) <= 1e-9:
        cleaned.pop()
    kept = [p for i, p in enumerate(cleaned) if abs(G.cross(G.sub(p, cleaned[i - 1]), G.sub(cleaned[(i + 1) % len(cleaned)], p))) > 1e-9 * max(1.0, math.dist(p, cleaned[i - 1]))]
    if len(kept) < 3 or not G.is_simple_polygon(kept):
        return None
    area = G.signed_area(kept)
    return kept if 1e-4 < area < G.signed_area(points) else None


def _open_corner_roles(project: Any, walls: Sequence[tuple[str, Pt, Pt, float]]) -> set[frozenset[str]]:
    """Room-graph exclusions for persisted, deliberately non-watertight corner modes."""
    listed = project.manifest.get("wall_corners") or {}
    if not isinstance(listed, dict):
        return set()
    ends = {f"{wid}:start": a for wid, a, _, _ in walls}
    ends.update({f"{wid}:end": b for wid, _, b, _ in walls})
    excluded: set[frozenset[str]] = set()
    for key, mode in listed.items():
        if mode != "stepped_open" or not isinstance(key, str):
            continue
        roles = key.split("|")
        if len(roles) == 2 and roles[0] in ends and roles[1] in ends and math.dist(ends[roles[0]], ends[roles[1]]) <= TOL:
            excluded.add(frozenset(roles))
    return excluded


def spaces(walls: Sequence[tuple[str, Pt, Pt, float]], *, project: Any | None = None) -> list[dict[str, Any]]:
    """The rooms a set of walls (id, start, end, thickness; metres) encloses: ``{"outline": [...], "probe": (x, y)}`` each, in a stable order."""
    walls = [w for w in walls if math.dist(w[1], w[2]) > 1e-6]
    if len(walls) < 3:
        return []
    faces = _faces(_topological_axes(walls), blocked=_open_corner_roles(project, walls) if project is not None else None)
    out = []
    for f in faces:
        others = [P for g in faces if g["component"] != f["component"] for P in g["points"]]
        if any(R.contains(f["points"], q) and not _on_edge(f["points"], q) for q in others):
            continue                                                         # a separate ring of walls inside: the room would need a hole
        outline = _inset(f["points"], f["thick"])
        if outline is not None:
            out.append({"outline": outline, "probe": interior_point(outline), "wall_ids": f["wall_ids"]})
    out.sort(key=lambda s: (round(min(q[1] for q in s["outline"]), 6), round(min(q[0] for q in s["outline"]), 6)))
    return out


def _on_edge(poly: Sequence[Pt], q: Pt) -> bool:
    return any(abs(G.cross(G.sub(b, a), G.sub(q, a))) < 1e-9 * max(1.0, math.dist(a, b)) and G.dot(G.sub(q, a), G.sub(q, b)) <= 1e-9 for a, b in zip(poly, list(poly[1:]) + [poly[0]]))


# ----------------------------------------------------------------------------------------------------------------------- reconcile
def _walls_on(project: Any, level_id: str) -> list[tuple[str, Pt, Pt, float]]:
    out = []
    for oid in E.element_ids(project):
        meta = E.meta_of(project, oid)
        if E.m(meta, "category") == "wall" and E.m(meta, "level") == level_id:
            out.append((oid, tuple(E.m(meta, "start")), tuple(E.m(meta, "end")), float(E.m(meta, "thickness"))))
    return out


def signature(walls: Sequence[tuple[str, Pt, Pt, float]]) -> str:
    h = hashlib.sha1()
    for oid, a, b, t in sorted(walls):
        h.update(f"{oid}|{a[0]:.6f},{a[1]:.6f}|{b[0]:.6f},{b[1]:.6f}|{t:.6f};".encode())
    return h.hexdigest()


def _rooms_on(project: Any, level_id: str) -> tuple[list[str], list[str]]:
    """(automatic, hand-drawn) rooms of a level."""
    auto, manual = [], []
    for oid in E.element_ids(project):
        meta = E.meta_of(project, oid)
        if E.m(meta, "category") == "room" and E.m(meta, "level") == level_id:
            (auto if E.m(meta, "auto") else manual).append(oid)
    return auto, manual


def _pristine(project: Any, rid: str) -> bool:
    """Whether nobody has made this automatic room their own: still named "Room" with the number and height it was given."""
    meta = E.meta_of(project, rid)
    return (project.model.get(rid)["name"] == "Room" and E.m(meta, "number") == E.m(meta, "auto_number")
            and abs(float(E.m(meta, "height", R.DEFAULT_HEIGHT)) - R.DEFAULT_HEIGHT) < 1e-9)


def _overlap_area(a: Sequence[Pt], b: Sequence[Pt]) -> float:
    """Actual 2D overlap; touching at an edge is not enough to identify rooms."""
    if max(p[0] for p in a) <= min(q[0] for q in b) or min(p[0] for p in a) >= max(q[0] for q in b) or \
       max(p[1] for p in a) <= min(q[1] for q in b) or min(p[1] for p in a) >= max(q[1] for q in b):
        return 0.0
    from . import planar as P
    try:
        regions = P.intersection(a, b)
        return sum(R.metrics(outer)["area"] - sum(R.metrics(h)["area"] for h in holes) for outer, holes in regions)
    except P.PlanarError:
        return 0.0                                                  # use persistent wall identities or probes as fallback


def _claim_existing(project: Any, auto: Sequence[str], found: Sequence[dict[str, Any]], probes: Mapping[str, Pt]) -> dict[int, str]:
    """Deterministically match rooms by their wall identities and actual spatial overlap.

    A probe point can cross a moved partition even when the *same* two rooms
    still exist on its two sides. First-fit point matching then destroys one
    room and gives its number to a new one. Shared boundary-wall identities
    preserve each room's structural side; area overlap and probe containment
    handle old projects without these stored references.
    """
    candidate: list[tuple[float, float, int, float, str, int]] = []
    for rid in auto:
        meta = E.meta_of(project, rid)
        prev = R.boundary_of(meta)
        old_walls = set(E.m(meta, "room_walls") or (w["id"] for w in R.bounding_walls(project, rid)))
        for i, new in enumerate(found):
            wall_ids = set(new["wall_ids"])
            common = len(old_walls & wall_ids)
            wall_score = common / len(old_walls | wall_ids) if old_walls else 0.0
            overlap = _overlap_area(prev, new["outline"])
            new_area = R.metrics(new["outline"])["area"]
            old_area = R.metrics(prev)["area"]
            coverage = overlap / max(min(old_area, new_area), 1e-12)
            has_probe = R.contains(new["outline"], probes[rid])
            if not common and overlap <= 1e-8 and not has_probe:
                continue
            candidate.append((-wall_score, -coverage, -int(has_probe), -overlap, rid, i))
    claimed: dict[int, str] = {}
    assigned: set[str] = set()
    for _wall, _coverage, _probe, _area, rid, i in sorted(candidate):
        if i not in claimed and rid not in assigned:
            claimed[i] = rid
            assigned.add(rid)
    return claimed


def _reconcile_level(project: Any, level_id: str) -> dict[str, int]:
    found = spaces(_walls_on(project, level_id), project=project)
    auto, manual = _rooms_on(project, level_id)
    probes = {rid: tuple(E.m(E.meta_of(project, rid), "probe") or R.metrics(R.boundary_of(E.meta_of(project, rid)))["centre"]) for rid in auto}
    claimed = _claim_existing(project, auto, found, probes)
    # When a damaged/removed partition suddenly makes one face encompass two
    # previously distinct rooms, do not silently give their combined area to
    # whichever probe wins the match and delete the other room. Keep both
    # identities and their previous outlines, clearly marked unresolved.
    # Deliberate room merging can be completed by removing the obsolete room.
    ambiguous: dict[int, set[str]] = {}
    for i, s in enumerate(found):
        absorbed = {rid for rid in auto
                    if _overlap_area(R.boundary_of(E.meta_of(project, rid)), s["outline"]) /
                    max(R.metrics(R.boundary_of(E.meta_of(project, rid)))["area"], 1e-12) > 0.90}
        if len(absorbed) > 1:
            ambiguous[i] = absorbed
            claimed.pop(i, None)
    ambiguous_ids = set().union(*ambiguous.values()) if ambiguous else set()
    claimed = {i: rid for i, rid in claimed.items() if rid not in ambiguous_ids}
    counts = {"added": 0, "updated": 0, "removed": 0, "not_enclosed": 0}
    for rid in auto:
        if rid in claimed.values():
            continue
        if rid in ambiguous_ids:
            counts["not_enclosed"] += 1
            if not E.m(E.meta_of(project, rid), "open") or E.m(E.meta_of(project, rid), "room_issue") != "merged":
                E._attach(project, rid, {"open": True, "room_issue": "merged"})
        elif _pristine(project, rid):
            project.model.remove(rid)
            counts["removed"] += 1
        else:
            counts["not_enclosed"] += 1
            if not E.m(E.meta_of(project, rid), "open"):
                E._attach(project, rid, {"open": True})
    manual_probes = [R.metrics(R.boundary_of(E.meta_of(project, rid)))["centre"] for rid in manual]
    manual_points = [interior_point(R.boundary_of(E.meta_of(project, rid))) for rid in manual]
    for i, s in enumerate(found):
        if i in ambiguous:
            continue
        outline = [[q[0], q[1]] for q in s["outline"]]
        if i in claimed:
            rid = claimed[i]
            meta = E.meta_of(project, rid)
            old = E.m(meta, "probe")
            probe = tuple(old) if old and R.contains(s["outline"], tuple(old)) else s["probe"]
            wall_ids = s["wall_ids"]
            if outline != [list(q) for q in E.m(meta, "boundary")] or not old or tuple(old) != probe or E.m(meta, "open") or E.m(meta, "room_walls") != wall_ids or E.m(meta, "room_issue"):
                E._attach(project, rid, {"boundary": outline, "probe": [probe[0], probe[1]], "room_walls": wall_ids, "open": False, "room_issue": None})
                counts["updated"] += 1
        elif not any(R.contains(s["outline"], q) for q in manual_points + manual_probes):
            rid = R.create(project, level_id, outline, auto=True, probe=s["probe"])
            E._attach(project, rid, {"room_walls": s["wall_ids"]})
            counts["added"] += 1
    return counts


def reconcile(project: Any, *, force: bool = False) -> dict[str, int] | None:
    """Bring the automatic rooms in line with the walls, for the levels whose walls changed. Runs inside the command that changed them, so one
    Undo takes the walls and the rooms back together. None when the setting is off."""
    st = project.manifest.get("auto_rooms")
    if isinstance(st, dict) and not st.get("on", True):
        return None
    st = settings(project)
    total = {"added": 0, "updated": 0, "removed": 0, "not_enclosed": 0}
    for level in project.manifest["levels"]:
        walls = _walls_on(project, level["id"])
        sig = signature(walls)
        open_corners = _open_corner_roles(project, walls)
        if open_corners:
            sig += "|open:" + ";".join(sorted("|".join(sorted(roles)) for roles in open_corners))
        if not force and st["sig"].get(level["id"], "") == sig:
            continue
        if not walls and not st["sig"].get(level["id"]) and not force:
            continue                                                         # a level that never had a wall has nothing to work out
        for key, n in _reconcile_level(project, level["id"]).items():
            total[key] += n
        st["sig"][level["id"]] = sig
    return total


def set_on(project: Any, on: bool) -> dict[str, int] | None:
    """Turn automatic rooms on (working out every level now) or off (the rooms stay, as ordinary rooms that no longer follow the walls)."""
    st = settings(project)
    st["on"] = bool(on)
    if on:
        st["sig"] = {}
        return reconcile(project, force=True)
    for oid in E.element_ids(project):
        meta = E.meta_of(project, oid)
        if E.m(meta, "category") == "room" and E.m(meta, "auto"):
            project.model.attach_semantics(oid, {k: v for k, v in meta.items() if k not in ("spadra:auto", "spadra:probe", "spadra:open", "spadra:auto_number")}, replace=True)
    return None
