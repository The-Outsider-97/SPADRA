"""Colours given to single lines of a Profiler profile: only the faces built from that line take the colour.

An extrusion remembers its coloured outline segments in plan (metres); a sweep remembers them in its section (u, v, metres). When the scene is built every face is
matched to the segment it stands on, and consecutive faces of one colour become one run, the same ``{count, color}`` runs the layered elements already use.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from . import elements as E
from . import geometry as G

TOL = 2e-5                      # metres: a wall face lies exactly on its outline segment, this only absorbs rounding
SECTION_TOL = 1e-7


def _runs(colors: Sequence[str | None]) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for c in colors:
        if runs and runs[-1]["color"] == c:
            runs[-1]["count"] += 1
        else:
            runs.append({"count": 1, "color": c})
    return runs


def _distance(p: Sequence[float], a: Sequence[float], b: Sequence[float]) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def _extrusion(segments: Sequence[Mapping[str, Any]], verts: Sequence[Sequence[float]], faces: Sequence[Sequence[int]], base: str | None) -> list[str | None]:
    out: list[str | None] = []
    for f in faces:
        a, b, c = (verts[i] for i in f[:3])
        u, v = [b[i] - a[i] for i in range(3)], [c[i] - a[i] for i in range(3)]
        n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
        size = math.sqrt(sum(x * x for x in n))
        colour = base
        if size > 1e-12 and abs(n[2]) / size < 0.25:                                  # a wall of the outline, not a top or bottom
            mid = ((a[0] + b[0] + c[0]) / 3, (a[1] + b[1] + c[1]) / 3)
            best, found = TOL, None
            for s in segments:
                d = _distance(mid, s["a"], s["b"])
                if d <= best:
                    best, found = d, s["c"]
            colour = found or base
        out.append(colour)
    return out


def _sweep(segments: Sequence[Mapping[str, Any]], meta: Mapping[str, Any], face_count: int, base: str | None) -> list[str | None] | None:
    section = [tuple(map(float, q)) for q in (E.m(meta, "section") or [])]
    s = len(section)
    if s < 3:
        return None
    if G.signed_area(section) < 0:
        section.reverse()
    closed = bool(E.m(meta, "closed", False))
    cap = str(E.m(meta, "end_cap", "flat"))
    caps = 0 if closed or cap == "none" else (2 * (s - 2) if cap == "flat" else 4 * s + 2 * (s - 2))
    body = face_count - caps
    if body <= 0 or body % (2 * s):
        return None                                                              # not the mesh the sweep builder made (for example, cut by a void): leave it one colour
    def colour_of(k: int) -> str | None:
        a, b = section[k], section[(k + 1) % s]
        for seg in segments:
            p, q = seg["a"], seg["b"]
            if (math.dist(p, a) <= SECTION_TOL and math.dist(q, b) <= SECTION_TOL) or (math.dist(p, b) <= SECTION_TOL and math.dist(q, a) <= SECTION_TOL):
                return seg["c"]
        return None
    per = [colour_of(k) or base for k in range(s)]
    out: list[str | None] = []
    for f in range(face_count):
        out.append(per[(f % (2 * s)) // 2] if f < body else base)
    return out


def face_runs(category: str, meta: Mapping[str, Any], verts: Sequence[Sequence[float]], faces: Sequence[Sequence[int]], base: str | None) -> list[dict[str, Any]] | None:
    """Per-face colour runs for an element with coloured profile lines, or None when it has none (or the mesh is not the one the lines were matched to)."""
    segments = E.m(meta, "edge_colors")
    if not segments or category not in ("extrusion", "sweep") or not faces:
        return None
    colours = _extrusion(segments, verts, faces, base) if category == "extrusion" else _sweep(segments, meta, len(faces), base)
    if colours is None or all(c == base for c in colours):
        return None
    return _runs(colours)
