"""Planar boolean operations on simple polygons, in pure Python, for the cuts a void makes in a floor or an extrusion.

The result of ``difference`` / ``union`` / ``combine`` is a list of **regions**, each ``(outer, holes)``: ``outer`` runs counter-clockwise and every hole clockwise,
so the material always lies to the left of the direction of travel.

How it works (an arrangement, not a clipping walk, so shared and overlapping edges need no special case):

1. Every vertex is snapped to a grid of ``SNAP`` metres (a tenth of a micrometre), which is far below anything SPADRA draws (``forms.TOL`` is a micrometre).
2. Every edge is split wherever another polygon's edge crosses it, or another polygon's vertex lies on it. After that two edges either coincide or meet only at
   end points.
3. Each piece is classified by the two sides of its middle point, ``SIDE`` metres to its left and right, asking each polygon whether that point is inside it. A piece
   is on the boundary of the result when the wanted combination is true on one side and false on the other, and it is directed so that the result lies to its left.
   A piece that two polygons share is therefore kept once (or not at all), whichever way the two run.
4. The pieces are chained into rings. A point where more than two pieces meet (two outlines touching at a corner) has no single answer, so ``PlanarError`` says so
   rather than guessing a ring.

Nothing here is approximate beyond the snapping: areas add up (``area(A - B) + area(A & B) == area(A)``) to rounding, and the property tests check that against
an independent point-sampling count. A feature smaller than ``2 * SIDE`` (two micrometres) is below what the classification can see.
"""
from __future__ import annotations

import math
from typing import Callable, Sequence

Pt = tuple[float, float]
Ring = list[Pt]
Region = tuple[Ring, list[Ring]]

SNAP_DIGITS = 7
SNAP = 10.0 ** -SNAP_DIGITS
SIDE = 1e-6
COLLINEAR = 1e-9


class PlanarError(ValueError):
    """The operation has no single exact answer in this form; the message says why, in words a drawing user can act on."""


def _snap(p: Sequence[float]) -> Pt:
    return (round(float(p[0]), SNAP_DIGITS) + 0.0, round(float(p[1]), SNAP_DIGITS) + 0.0)


def signed_area(ring: Sequence[Pt]) -> float:
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, list(ring[1:]) + list(ring[:1]))) / 2.0


def inside(ring: Sequence[Pt], p: Pt) -> bool:
    """Whether ``p`` is inside the ring by the even-odd rule. A point on the boundary is not asked about: callers sample off it."""
    x, y = p
    hit = False
    n = len(ring)
    for i in range(n):
        (x1, y1), (x2, y2) = ring[i], ring[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


def clean(ring: Sequence[Sequence[float]]) -> Ring:
    """The ring snapped, with repeated and collinear points taken out. Fewer than three points left, or no area, is an error."""
    pts: Ring = []
    for q in ring:
        p = _snap(q)
        if not pts or p != pts[-1]:
            pts.append(p)
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts.pop()
    changed = True
    while changed and len(pts) >= 3:                                         # a point on the straight line between its neighbours carries no information
        changed = False
        for i in range(len(pts)):
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % len(pts)]
            if abs((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])) < COLLINEAR and (b[0] - a[0]) * (c[0] - b[0]) + (b[1] - a[1]) * (c[1] - b[1]) > 0:
                del pts[i]
                changed = True
                break
    if len(pts) < 3 or abs(signed_area(pts)) < 1e-9:
        raise PlanarError("an outline has no area")
    return pts


def _split_points(a: Pt, b: Pt, others: Sequence[tuple[Pt, Pt]]) -> list[Pt]:
    """Where other edges cross or touch segment ``ab`` in its interior, in order along it, snapped and without repeats."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    found: dict[Pt, float] = {}

    def add(p: Pt) -> None:
        p = _snap(p)
        if p != a and p != b:
            found[p] = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2

    for c, d in others:
        ex, ey = d[0] - c[0], d[1] - c[1]
        den = dx * ey - dy * ex
        if abs(den) > 1e-12:
            t = ((c[0] - a[0]) * ey - (c[1] - a[1]) * ex) / den
            u = ((c[0] - a[0]) * dy - (c[1] - a[1]) * dx) / den
            if -1e-9 <= t <= 1 + 1e-9 and -1e-9 <= u <= 1 + 1e-9:
                add((a[0] + t * dx, a[1] + t * dy))
        for q in (c, d):                                                     # an end point of the other edge that lies on this one (also the collinear overlaps)
            t = ((q[0] - a[0]) * dx + (q[1] - a[1]) * dy) / length2
            if 0.0 < t < 1.0 and math.hypot(a[0] + t * dx - q[0], a[1] + t * dy - q[1]) < SNAP:
                add(q)
    return [p for p, _ in sorted(found.items(), key=lambda kv: kv[1])]


def combine(rings: Sequence[Sequence[Sequence[float]]], wanted: Callable[[Sequence[bool]], bool]) -> list[Region]:
    """The regions where ``wanted`` is true, given for each point whether it is inside each ring (in the order of ``rings``)."""
    polygons = [clean(r) for r in rings]
    edges = [(i, p[j], p[(j + 1) % len(p)]) for i, p in enumerate(polygons) for j in range(len(p))]
    pieces: dict[tuple[Pt, Pt], None] = {}
    for i, a, b in edges:
        cuts = _split_points(a, b, [(c, d) for k, c, d in edges if k != i])
        chain = [a, *cuts, b]
        for p, q in zip(chain, chain[1:]):
            if p != q:
                pieces[(p, q) if p <= q else (q, p)] = None
    directed: list[tuple[Pt, Pt]] = []
    for p, q in pieces:
        length = math.hypot(q[0] - p[0], q[1] - p[1])
        mid = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
        nx, ny = -(q[1] - p[1]) / length, (q[0] - p[0]) / length
        left = wanted([inside(r, (mid[0] + SIDE * nx, mid[1] + SIDE * ny)) for r in polygons])
        right = wanted([inside(r, (mid[0] - SIDE * nx, mid[1] - SIDE * ny)) for r in polygons])
        if left != right:
            directed.append((p, q) if left else (q, p))
    return _regions(directed)


def _regions(directed: Sequence[tuple[Pt, Pt]]) -> list[Region]:
    if not directed:
        return []
    onward: dict[Pt, Pt] = {}
    arrivals: dict[Pt, int] = {}
    for p, q in directed:
        if p in onward:
            raise PlanarError("two outlines touch at a single point, which leaves no single outline")
        onward[p] = q
        arrivals[q] = arrivals.get(q, 0) + 1
    if any(n != 1 for n in arrivals.values()) or set(onward) != set(arrivals):
        raise PlanarError("two outlines touch at a single point, which leaves no single outline")
    rings: list[Ring] = []
    seen: set[Pt] = set()
    for start in onward:
        if start in seen:
            continue
        ring, p = [], start
        while p not in seen:
            seen.add(p)
            ring.append(p)
            p = onward[p]
        if p != start:
            raise PlanarError("the edges of the result do not close")
        rings.append(clean(ring))
    outers = sorted((r for r in rings if signed_area(r) > 0), key=signed_area)
    holes = [r for r in rings if signed_area(r) < 0]
    regions: list[Region] = [(o, []) for o in outers]
    for h in holes:
        owner = next((reg for reg in regions if inside(reg[0], h[0])), None)           # the smallest outer that holds it
        if owner is None:
            raise PlanarError("a hole lies outside every outline")
        owner[1].append(h)
    return regions[::-1]


def difference(subject: Sequence[Sequence[float]], clips: Sequence[Sequence[Sequence[float]]]) -> list[Region]:
    """``subject`` with every ring of ``clips`` taken away (the clips may overlap each other)."""
    if not clips:
        return combine([subject], lambda f: f[0])
    return combine([subject, *clips], lambda f: f[0] and not any(f[1:]))


def union(rings: Sequence[Sequence[Sequence[float]]]) -> list[Region]:
    return combine(rings, lambda f: any(f))


def intersection(a: Sequence[Sequence[float]], b: Sequence[Sequence[float]]) -> list[Region]:
    return combine([a, b], lambda f: f[0] and f[1])


def region_area(region: Region) -> float:
    return signed_area(region[0]) + sum(signed_area(h) for h in region[1])
