"""Which elements a void cuts, and why it leaves the others alone.

A void (``forms``) cuts what can be cut exactly: a hole through a floor or a solid extrusion, a notch where it crosses their edge (overlapping voids act as one),
a rectangular opening through a wall. Nothing about a
void is ever written into the element it cuts: the cut is recomputed from the void's parameters every time, so changing or deleting a void, or
turning its cutting off, gives the element back whole.

This module adds the control over *which* elements a void is applied to, and the classification that says what happens to every element it meets.

* ``cut_enabled`` (the "Cut Intersecting Geometry" switch): off, and the void cuts nothing.
* ``cut_policy``: ``automatic`` (every element it can cut), ``targets`` (only the elements chosen in ``targets``), ``hosted`` (only its ``host`` element)
  or ``disabled``.

**Classification** of a void against every element: an element that the void does not reach is not listed. One it reaches is ``cut`` (the cut is made),
``unsupported`` (it is reached but this kind of cut is not computed, with the reason: a pocket that does not go through, a void that would split the
element in two, an angled wall opening, a roof, a beam ...) or ``excluded`` (it could be cut, but the policy leaves it alone). "Reached" is decided from the geometry, not from
bounding boxes: for floors, extrusions and walls from their outlines and height ranges, and for every other element from its actual triangles, which are
clipped against the void's prism (a triangle that has a point inside the prism, or a void that lies inside the solid, is a hit).
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from ..tools.errors import BadRequest, NotFound
from ..tools.validation import boolean, text
from . import elements as E

POLICIES = {"automatic": "Automatic", "targets": "Selected Targets", "hosted": "Hosted Objects", "disabled": "Disabled"}
CUTTABLE = ("floor", "extrusion", "wall")                       # what a void can actually cut
NEVER_HIT = ("void", "room", "grid")                            # voids do not act on these
TOL = 1e-6


# ------------------------------------------------------------------------------------------------------------------------------ settings
def settings(meta: Mapping[str, Any]) -> dict[str, Any]:
    """A void's cutting settings. A void made before they existed cuts everything it can."""
    enabled = E.m(meta, "cut_enabled")
    return {"enabled": True if enabled is None else bool(enabled), "policy": E.m(meta, "cut_policy") or "automatic",
            "targets": list(E.m(meta, "cut_targets") or []), "host": E.m(meta, "cut_host") or None}


def applies(v: Mapping[str, Any], oid: str) -> tuple[bool, str]:
    """Whether the void acts on this element, and if not, why."""
    if not v["enabled"]:
        return False, "Cut Intersecting Geometry is disabled on this void"
    policy = v["policy"]
    if policy == "automatic":
        return True, ""
    if policy == "targets":
        return (True, "") if oid in v["targets"] else (False, "it is not one of the void's selected targets")
    if policy == "hosted":
        if not v["host"]:
            return False, "the void has no host element chosen"
        return (True, "") if oid == v["host"] else (False, "it is not the void's host element")
    return False, "the cutting policy is Disabled"


def _element(project: Any, oid: Any, what: str) -> str:
    oid = text(oid, what, 80)
    if oid not in E.element_ids(project) or E.category(project, oid) in NEVER_HIT:
        raise NotFound(f"{oid} is not an element a void can act on")
    return oid


def set_property(project: Any, vid: str, meta: Mapping[str, Any], key: str, value: Any) -> dict[str, Any]:
    """The parameter changes for one edit of a void's cutting settings."""
    now = settings(meta)
    if key == "cut_enabled":
        if isinstance(value, str) and value.strip().lower() in ("enabled", "disabled"):
            return {"cut_enabled": value.strip().lower() == "enabled"}
        return {"cut_enabled": boolean(value, "cut_enabled")}
    if key == "cut_policy":
        if value not in POLICIES:
            raise BadRequest(f"cut_policy must be one of {', '.join(POLICIES)}")
        return {"cut_policy": value}
    if key == "target_add":
        oid = _element(project, value, "target")
        if oid == vid:
            raise BadRequest("A void cannot be its own target")
        return {"cut_targets": [*now["targets"], oid] if oid not in now["targets"] else now["targets"]}
    if key == "target_remove":
        oid = text(value, "target", 80)
        if oid not in now["targets"]:
            raise BadRequest(f"{oid} is not a target of this void")
        return {"cut_targets": [t for t in now["targets"] if t != oid]}
    if key == "host":
        if value in (None, ""):
            return {"cut_host": None}
        oid = _element(project, value, "host")
        if oid == vid:
            raise BadRequest("A void cannot be its own host")
        return {"cut_host": oid}
    raise BadRequest(f"'{key}' is not an editable cutting setting")


def creation_values(project: Any, p: Mapping[str, Any]) -> dict[str, Any]:
    """Cutting settings given when a void is made: ``cut_enabled``, ``cut_policy``, ``targets`` (element ids) and ``host``."""
    out: dict[str, Any] = {}
    for key in ("cut_enabled", "cut_policy", "host"):
        if p.get(key) is not None:
            out.update(set_property(project, "", {}, key, p[key]))
    if p.get("targets") is not None:
        if not isinstance(p["targets"], list):
            raise BadRequest("targets must be a list of element ids")
        out["cut_targets"] = list(dict.fromkeys(_element(project, t, "target") for t in p["targets"]))
    return out


# ------------------------------------------------------------------------------------------------------------------------- intersection
def _clip_to_triangle(poly: Sequence[tuple[float, float]], tri: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """``poly`` (any simple polygon) cut by the convex triangle ``tri`` (Sutherland–Hodgman against its three sides)."""
    area = (tri[1][0] - tri[0][0]) * (tri[2][1] - tri[0][1]) - (tri[1][1] - tri[0][1]) * (tri[2][0] - tri[0][0])
    sign = 1.0 if area > 0 else -1.0
    out = list(poly)
    for i in range(3):
        a, b = tri[i], tri[(i + 1) % 3]
        if not out:
            break
        inside = lambda q: sign * ((b[0] - a[0]) * (q[1] - a[1]) - (b[1] - a[1]) * (q[0] - a[0])) >= -1e-12          # noqa: E731
        nxt: list[tuple[float, float]] = []
        for j, p in enumerate(out):
            q = out[(j + 1) % len(out)]
            ip, iq = inside(p), inside(q)
            if ip:
                nxt.append(p)
            if ip != iq:
                dp = sign * ((b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]))
                dq = sign * ((b[0] - a[0]) * (q[1] - a[1]) - (b[1] - a[1]) * (q[0] - a[0]))
                t = dp / (dp - dq)
                nxt.append((p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t))
        out = nxt
    return out


def _poly_area(poly: Sequence[tuple[float, float]]) -> float:
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(poly, list(poly[1:]) + list(poly[:1]))) / 2)


def mesh_meets_prism(verts: Sequence[Sequence[float]], faces: Sequence[Sequence[int]], poly: Sequence[tuple[float, float]], z0: float, z1: float) -> bool:
    """Whether a triangle mesh and the vertical prism over ``poly`` between ``z0`` and ``z1`` share volume or surface. Exact for any simple ``poly``:
    each triangle is clipped to the prism's plan, and the triangle's height over what is left is compared with the prism's height range. A prism that lies
    wholly inside a closed solid, touching no face, is found by a vertical ray from a point inside it."""
    for a, b, c in faces:
        A, B, C = verts[a], verts[b], verts[c]
        if max(A[2], B[2], C[2]) < z0 + TOL or min(A[2], B[2], C[2]) > z1 - TOL:
            continue
        tri = [(A[0], A[1]), (B[0], B[1]), (C[0], C[1])]
        det = (B[0] - A[0]) * (C[1] - A[1]) - (B[1] - A[1]) * (C[0] - A[0])
        if abs(det) < 1e-12:
            if _vertical_face_meets(A, B, C, poly, z0, z1):
                return True
            continue
        part = _clip_to_triangle(poly, tri)
        if len(part) < 3 or _poly_area(part) < 1e-9:
            continue
        zs = []
        for x, y in part:                                                     # the triangle's height at each corner of what is left (it is a plane)
            u = ((x - A[0]) * (C[1] - A[1]) - (y - A[1]) * (C[0] - A[0])) / det
            v = ((B[0] - A[0]) * (y - A[1]) - (B[1] - A[1]) * (x - A[0])) / det
            zs.append(A[2] + u * (B[2] - A[2]) + v * (C[2] - A[2]))
        if max(zs) > z0 + TOL and min(zs) < z1 - TOL:
            return True
    return _inside_solid(verts, faces, poly, z0, z1)


def _vertical_face_meets(A: Sequence[float], B: Sequence[float], C: Sequence[float], poly: Sequence[tuple[float, float]], z0: float, z1: float) -> bool:
    """A face that stands vertically has no plan area, so it is tested in the vertical plane through it: the stretch of its plan line that runs through the
    inside of ``poly`` is cut out of the face, and what is left is compared with the height range. A face lying along the prism's side only touches it."""
    pts = [A, B, C]
    far = max(((p, q) for p in pts for q in pts), key=lambda pq: (pq[0][0] - pq[1][0]) ** 2 + (pq[0][1] - pq[1][1]) ** 2)
    (x0, y0, _), (x1, y1, _) = far
    length = math.hypot(x1 - x0, y1 - y0)
    if length < 1e-9:
        return False
    ux, uy = (x1 - x0) / length, (y1 - y0) / length
    cuts = [0.0, length]
    for i, c in enumerate(poly):                                              # where the line crosses the outline, along the line
        d = poly[(i + 1) % len(poly)]
        ex, ey = d[0] - c[0], d[1] - c[1]
        den = ux * ey - uy * ex
        if abs(den) < 1e-12:
            continue
        s = ((c[0] - x0) * ey - (c[1] - y0) * ex) / den
        t = ((c[0] - x0) * uy - (c[1] - y0) * ux) / den
        if 0.0 < s < length and 0.0 <= t <= 1.0:
            cuts.append(s)
    cuts.sort()
    face = [((p[0] - x0) * ux + (p[1] - y0) * uy, p[2]) for p in pts]          # the face in its own plane: (distance along the line, height)
    from . import rooms as R
    for sa, sb in zip(cuts, cuts[1:]):
        if sb - sa < 1e-9:
            continue
        mid = (x0 + ux * (sa + sb) / 2, y0 + uy * (sa + sb) / 2)
        if any(_near_edge(mid, c, poly[(i + 1) % len(poly)]) for i, c in enumerate(poly)) or not R.contains(poly, mid):
            continue                                                          # outside, or running along the prism's side
        shape = list(face)
        for keep in (lambda p: p[0] - sa, lambda p: sb - p[0], lambda p: p[1] - z0, lambda p: z1 - p[1]):
            shape = _half_plane(shape, keep)
            if len(shape) < 3:
                break
        if len(shape) >= 3 and _poly_area(shape) > 1e-9:
            return True
    return False


def _near_edge(p: tuple[float, float], a: Sequence[float], b: Sequence[float]) -> bool:
    ex, ey = b[0] - a[0], b[1] - a[1]
    n = math.hypot(ex, ey)
    if n < 1e-12:
        return True
    return abs((p[0] - a[0]) * ey - (p[1] - a[1]) * ex) / n < 1e-9


def _half_plane(poly: Sequence[tuple[float, float]], dist: Any) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for i, p in enumerate(poly):
        q = poly[(i + 1) % len(poly)]
        dp, dq = dist(p), dist(q)
        if dp >= 0:
            out.append(p)
        if (dp >= 0) != (dq >= 0):
            t = dp / (dp - dq)
            out.append((p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t))
    return out


def _inside_solid(verts: Sequence[Sequence[float]], faces: Sequence[Sequence[int]], poly: Sequence[tuple[float, float]], z0: float, z1: float) -> bool:
    from . import geometry as G
    tris = G.triangulate([tuple(q) for q in poly])
    if not tris:
        return False
    a, b, c = (poly[i] for i in tris[0])
    x, y, z = (a[0] + b[0] + c[0]) / 3 + 1.2345e-7, (a[1] + b[1] + c[1]) / 3 + 2.3456e-7, (z0 + z1) / 2       # off the diagonals the solid's triangles share
    hits = 0
    for i, j, k in faces:
        A, B, C = verts[i], verts[j], verts[k]
        det = (B[0] - A[0]) * (C[1] - A[1]) - (B[1] - A[1]) * (C[0] - A[0])
        if abs(det) < 1e-12:
            continue
        u = ((x - A[0]) * (C[1] - A[1]) - (y - A[1]) * (C[0] - A[0])) / det
        v = ((B[0] - A[0]) * (y - A[1]) - (B[1] - A[1]) * (x - A[0])) / det
        if u < -1e-12 or v < -1e-12 or u + v > 1 + 1e-12:
            continue
        if A[2] + u * (B[2] - A[2]) + v * (C[2] - A[2]) > z:
            hits += 1
    return hits % 2 == 1


def touches(project: Any, oid: str, v: Mapping[str, Any]) -> bool:
    """Whether an element, from its actual mesh, meets the void's prism."""
    from . import documentation as D
    try:
        lo, hi = project.model.world_bounds(oid)
    except Exception:                                                         # a group or anything without geometry has nothing to meet
        return False
    xs = [p[0] for p in v["poly"]]
    ys = [p[1] for p in v["poly"]]
    if hi[0] < min(xs) - TOL or lo[0] > max(xs) + TOL or hi[1] < min(ys) - TOL or lo[1] > max(ys) + TOL or hi[2] < v["z0"] + TOL or lo[2] > v["z1"] - TOL:
        return False                                                          # the boxes are apart, so the shapes are: only ever used to skip work
    verts, faces = D.world_mesh(project, oid)
    return mesh_meets_prism(verts, faces, v["poly"], v["z0"], v["z1"])


# ---------------------------------------------------------------------------------------------------------------------- classification
def classify(project: Any, vid: str) -> list[dict[str, Any]]:
    """What the void does to every element it meets: ``cut`` (with what kind of cut), ``unsupported`` (with the reason) or ``excluded`` (with the policy
    that leaves it alone). Read-only; elements the void does not reach are not listed."""
    from . import forms as FX
    v = next((x for x in FX.voids(project) if x["id"] == vid), None)
    if v is None:
        raise NotFound(f"Void {vid} does not exist")
    rows: list[dict[str, Any]] = []
    for oid in E.element_ids(project):
        cat = E.category(project, oid)
        if cat in NEVER_HIT or oid == vid:
            continue
        name = project.model.get(oid)["name"] or str(cat).title()
        yes, why = applies(v, oid)
        if cat in CUTTABLE:
            try:
                _, notes, cuts = FX._cuts(project, oid, [v], policy=False)
            except BadRequest as exc:
                rows.append({"id": oid, "name": name, "category": cat, "state": "unsupported", "reason": exc.message})
                continue
            mine = [n for who, n in notes if who == vid]
            made = any((c["id"] if isinstance(c, dict) else c[4]) == vid for c in cuts)
            if not made and not mine:
                continue                                                      # apart from it
            kind = "opening" if cat == "wall" else "notch" if getattr(cuts, "outline", None) else "hole"
            if not yes:
                rows.append({"id": oid, "name": name, "category": cat, "state": "excluded", "reason": why[0].upper() + why[1:], "would": kind if made else None})
            elif made:
                rows.append({"id": oid, "name": name, "category": cat, "state": "cut", "reason": f"a {kind} is cut", "kind": kind})
            else:
                rows.append({"id": oid, "name": name, "category": cat, "state": "unsupported", "reason": mine[0]})
        elif touches(project, oid, v):
            rows.append({"id": oid, "name": name, "category": cat, "state": "excluded" if not yes else "unsupported",
                         "reason": (why[0].upper() + why[1:]) if not yes else f"cutting a {cat} is not supported: it is left whole"})
    return rows


def stale(project: Any, v: Mapping[str, Any]) -> list[str]:
    """Selected targets or a host that no longer exist."""
    ids = [*v["targets"], *([v["host"]] if v["host"] else [])]
    return [i for i in ids if not E.alive(project, i)]


def summary(rows: Sequence[Mapping[str, Any]]) -> str:
    n = {s: sum(1 for r in rows if r["state"] == s) for s in ("cut", "unsupported", "excluded")}
    return f"{n['cut']} cut, {n['unsupported']} not supported, {n['excluded']} left alone by the policy" if rows else "reaches no element"
