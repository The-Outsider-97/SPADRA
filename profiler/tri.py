"""Triangulation of a polygon that may have holes (ear clipping after joining each hole to the outline by a bridge).

Why it exists: the end caps of a partial revolve are the profile itself, and a profile can have inner loops. SPADRA's own ``triangulate`` takes one simple
polygon. The triangles index the *input points* (a bridge simply uses a point twice), so a mesh built on them stays closed: a boundary point that lies in
the middle of a straight run is not dropped (that would leave a crack against the side faces) but kept in a zero-area triangle.
"""
from __future__ import annotations

from typing import Sequence

from .errors import ProfilerError

Pt = tuple[float, float]


class _N:
    __slots__ = ("i", "x", "y", "prev", "next")

    def __init__(self, i: int, p: Pt) -> None:
        self.i, self.x, self.y = i, p[0], p[1]
        self.prev: "_N" = self
        self.next: "_N" = self


def _turn(a: _N, b: _N, c: _N) -> float:
    return (b.x - a.x) * (c.y - b.y) - (b.y - a.y) * (c.x - b.x)


def _ring(points: Sequence[Pt], start: int, ccw: bool) -> _N:
    area = sum(points[j][0] * points[(j + 1) % len(points)][1] - points[(j + 1) % len(points)][0] * points[j][1] for j in range(len(points)))
    order = list(range(len(points)))
    if (area > 0) != ccw:
        order.reverse()
    last: _N | None = None
    first: _N | None = None
    for j in order:
        n = _N(start + j, points[j])
        if first is None:
            first = n
        else:
            n.prev, last.next = last, n          # type: ignore[union-attr]
        last = n
    last.next, first.prev = first, last           # type: ignore[union-attr]
    return first                                   # type: ignore[return-value]


def _leftmost(n: _N) -> _N:
    best = p = n
    while True:
        if p.x < best.x or (p.x == best.x and p.y < best.y):
            best = p
        p = p.next
        if p is n:
            return best


def _in_triangle(a: _N, b: _N, c: _N, p: _N) -> bool:
    return ((c.x - p.x) * (a.y - p.y) - (a.x - p.x) * (c.y - p.y) >= 0 and (a.x - p.x) * (b.y - p.y) - (b.x - p.x) * (a.y - p.y) >= 0
            and (b.x - p.x) * (c.y - p.y) - (c.x - p.x) * (b.y - p.y) >= 0)


def _inside_sector(a: _N, b: _N) -> bool:
    """Is the diagonal a-b inside the corner at ``a``?"""
    if _turn(a.prev, a, a.next) < 0:
        return _turn(a, b, a.next) >= 0 and _turn(a, a.prev, b) >= 0
    return _turn(a, b, a.prev) < 0 or _turn(a, a.next, b) < 0


def _bridge(hole: _N, outer: _N) -> _N | None:
    """The outline point to join the hole's left-most point to (a ray to the left finds the edge; of its ends and the points near it, the best seen from the hole)."""
    hx, hy = hole.x, hole.y
    qx = float("-inf")
    m: _N | None = None
    p = outer
    while True:
        if p.y >= hy >= p.next.y and p.next.y != p.y:
            x = p.x + (hy - p.y) * (p.next.x - p.x) / (p.next.y - p.y)
            if x <= hx and x > qx:
                qx = x
                m = p if p.x < p.next.x else p.next
                if x == hx:
                    return m
        p = p.next
        if p is outer:
            break
    if m is None:
        return None
    stop, tan_min = m, float("inf")
    mx, my = m.x, m.y
    corner = _N(-1, (qx, hy))
    h = _N(-1, (hx, hy))
    mm = _N(-1, (mx, my))
    p = m
    while True:
        if hx >= p.x >= mx and hx != p.x:
            tri = (h, mm, corner) if hy < my else (corner, mm, h)
            if _in_triangle(tri[0], tri[1], tri[2], p):
                tan = abs(hy - p.y) / (hx - p.x)
                if _inside_sector(p, hole) and (tan < tan_min or (tan == tan_min and p.x > m.x)):
                    m, tan_min = p, tan
        p = p.next
        if p is stop:
            return m


def _split(a: _N, b: _N) -> _N:
    a2, b2 = _N(a.i, (a.x, a.y)), _N(b.i, (b.x, b.y))
    an, bp = a.next, b.prev
    a.next, b.prev = b, a
    a2.next, an.prev = an, a2
    b2.next, a2.prev = a2, b2
    bp.next, b2.prev = b2, bp
    return b2


def triangulate(outer: Sequence[Pt], holes: Sequence[Sequence[Pt]] = ()) -> list[tuple[int, int, int]]:
    """Triangles (indices into ``outer`` followed by each hole's points, in that order), counter-clockwise, covering the outline minus the holes."""
    if len(outer) < 3:
        raise ProfilerError("an outline needs at least 3 points")
    pts = [tuple(p) for p in outer] + [tuple(p) for h in holes for p in h]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    scale = max(max(xs) - min(xs), max(ys) - min(ys), 1e-12)
    eps = 1e-12 * scale * scale
    node = _ring(pts[:len(outer)], 0, True)
    start = len(outer)
    queue = []
    for h in holes:
        if len(h) < 3:
            raise ProfilerError("an inner loop needs at least 3 points")
        queue.append(_leftmost(_ring(pts[start:start + len(h)], start, False)))
        start += len(h)
    queue.sort(key=lambda n: (n.x, n.y))
    for hole in queue:
        b = _bridge(hole, node)
        if b is None:
            raise ProfilerError("an inner loop could not be joined to the outline (is it really inside it?)")
        _split(b, hole)
    out: list[tuple[int, int, int]] = []
    ear, count = node, 0
    p = node
    while True:
        count += 1
        p = p.next
        if p is node:
            break
    guard = 0
    stalled = 0
    while count > 3:
        guard += 1
        if guard > 4 * count * count + 100:
            raise ProfilerError("this profile could not be triangulated for a partial revolve; use a full 360 degrees or simplify it")
        a, b, c = ear.prev, ear, ear.next
        ok = _turn(a, b, c) > eps
        if ok:
            q = c.next
            while q is not a:
                if not (q.x == a.x and q.y == a.y) and _in_triangle(a, b, c, q) and _turn(q.prev, q, q.next) <= eps:
                    ok = False
                    break
                q = q.next
        if ok:
            out.append((a.i, b.i, c.i))
            a.next, c.prev = c, a
            ear = c
            count -= 1
            stalled = 0
            continue
        ear = c
        stalled += 1
        if stalled > count:                            # a whole turn without an ear: a point on a straight run is in the way
            flat = ear
            for _ in range(count):
                if abs(_turn(flat.prev, flat, flat.next)) <= eps:
                    break
                flat = flat.next
            else:
                raise ProfilerError("this profile could not be triangulated for a partial revolve; use a full 360 degrees or simplify it")
            out.append((flat.prev.i, flat.i, flat.next.i))      # kept as a zero-area triangle so no crack opens against the side faces
            flat.prev.next, flat.next.prev = flat.next, flat.prev
            ear = flat.next
            count -= 1
            stalled = 0
    a, b, c = ear.prev, ear, ear.next
    out.append((a.i, b.i, c.i))
    return out
