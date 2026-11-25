"""The view range of a floor plan: one place that turns a plan's stored offsets into absolute elevations, validates them, and decides what a
piece of geometry is to the plan.

A plan has five numbers, all stored on its view (metres, so nothing here knows the project's drawing unit):

=================  ==============================================================================================================
``cut_reference``  the level the cut plane is measured from (the plan's own level unless set otherwise)
``cut_offset``     the **view height**: the cut plane, above ``cut_reference``
``range_top``      the **top** of the primary range, above the cut plane
``range_bottom``   the **bottom** of the primary range, from the plan's own level (usually a little below it)
``range_depth``    the **view depth**: the lowest visible elevation, from the plan's own level (negative: below it)
=================  ==============================================================================================================

Absolute elevations follow: ``cut = reference + cut_offset``; ``top = cut + range_top``; ``bottom = level + range_bottom``;
``depth = level + range_depth``; and a valid range satisfies ``depth <= bottom <= cut <= top``. They are computed here on every call from the
current level elevations, so a level that moves, or an element that is resized, changes the plan with no stored copy to go stale.

What a piece of geometry is to the plan depends on its **actual vertical extent**, never only on the level it is assigned to:

* ``cut``        the cut plane passes through it: drawn as a cut, whoever's level it is (a tall wall of the storey below that reaches
                 through the cut plane is cut, and editable, exactly as it is in the 3D model);
* ``projected``  it belongs to the plan's level and lies, wholly or partly, in the range but not through the cut: drawn as seen beyond the
                 cut (overhead beams dashed, things below the cut plane solid);
* ``context``    it belongs to another level and lies in the range without crossing the cut plane: a reference drawn in halftone,
                 never editable, never selectable, never snapped to unless the view asks for it;
* ``hidden``     it lies wholly above the top or below the view depth.

Nothing in this module changes the model: it only reads extents and returns decisions and outlines. The module imports nothing from SPADRA, so
the same rules can serve any SLAI application that has levels and plans.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

Pt3 = tuple[float, float, float]
Pt2 = tuple[float, float]

CUT, PROJECTED, CONTEXT, HIDDEN = "cut", "projected", "context", "hidden"

DEFAULT_CUT_OFFSET = 1.5          # m above the reference level
DEFAULT_RANGE_TOP = 1.5           # m above the cut plane
DEFAULT_RANGE_BOTTOM = -0.2       # m from the plan's level
DEFAULT_RANGE_DEPTH = -1.0        # m from the plan's level
EXTENT_TOLERANCE = 1e-8           # m: a cut plane within this of a face does not "cross" the element
LIMIT = 1000.0                    # m: no offset beyond this is meaningful, and a typo does not become an enormous range


class ViewRangeError(ValueError):
    """A view range that cannot exist, with a sentence that says which relation is broken and by how much."""


def _mm(value: float) -> str:
    return f"{value * 1000:,.0f} mm"


def _signed(value: float) -> str:
    return f"{'+' if value >= 0 else '−'}{_mm(abs(value))}"


@dataclass(frozen=True)
class ViewRange:
    """A plan's range as offsets (what is stored) and absolute elevations (what is compared), in metres."""

    level_z: float                # the plan's own level
    reference_z: float            # the level the cut plane is measured from
    cut_offset: float = DEFAULT_CUT_OFFSET
    top_offset: float = DEFAULT_RANGE_TOP          # above the cut plane
    bottom_offset: float = DEFAULT_RANGE_BOTTOM    # from the plan's level
    depth_offset: float = DEFAULT_RANGE_DEPTH      # from the plan's level

    @property
    def cut_z(self) -> float:
        return self.reference_z + self.cut_offset

    @property
    def top_z(self) -> float:
        return self.cut_z + self.top_offset

    @property
    def bottom_z(self) -> float:
        return self.level_z + self.bottom_offset

    @property
    def depth_z(self) -> float:
        return self.level_z + self.depth_offset

    def problem(self) -> str | None:
        """The first relation that is broken, as a sentence for the person, or None when the range is valid."""
        for name, value in (("cut offset", self.cut_offset), ("top", self.top_offset), ("bottom", self.bottom_offset), ("depth", self.depth_offset)):
            if not math.isfinite(value) or abs(value) > LIMIT:
                return f"The view range {name} must be a number within ±{LIMIT:,.0f} m."
        if self.top_offset < -EXTENT_TOLERANCE:
            return f"The top of the view range ({_signed(self.top_offset)} from the cut plane) cannot be below the cut plane."
        if self.cut_z < self.bottom_z - EXTENT_TOLERANCE:
            return (f"The view height puts the cut plane at {_mm(self.cut_z)} (absolute), which is {_mm(self.bottom_z - self.cut_z)} below the bottom of the view range "
                    f"({_mm(self.bottom_z)}). Raise the view height or lower the bottom.")
        if self.depth_z > self.bottom_z + EXTENT_TOLERANCE:
            return (f"The view depth ({_mm(self.level_z - self.depth_z)} below the level) is shallower than the bottom of the view range "
                    f"({_mm(self.level_z - self.bottom_z)} below the level): the depth must reach at least as far down as the bottom.")
        return None

    def validate(self) -> "ViewRange":
        message = self.problem()
        if message:
            raise ViewRangeError(message)
        return self

    # ------------------------------------------------------------------------------------------------------------ what the geometry is
    def classify(self, low: float, high: float, *, on_level: bool) -> str:
        """What a piece of geometry spanning ``low .. high`` (absolute metres) is to this plan. See the module text."""
        if high < self.depth_z - EXTENT_TOLERANCE or low > self.top_z + EXTENT_TOLERANCE:
            return HIDDEN
        if low + EXTENT_TOLERANCE < self.cut_z < high - EXTENT_TOLERANCE:
            return CUT
        return PROJECTED if on_level else CONTEXT

    def describe(self, *, reference_level: str = "", level: str = "") -> dict[str, Any]:
        """What a status bar or a dialog needs, in metres: each boundary as the offset it is edited as and as the absolute elevation it is.

        ``height`` is the view height (the cut plane above its reference level) and ``depth`` the view depth as a distance below the plan's level (positive),
        the two numbers the status bar edits; ``bottom`` is likewise a distance below the level (negative when the bottom is above it)."""
        return {
            "level": level, "reference_level": reference_level or level, "level_z": self.level_z, "reference_z": self.reference_z,
            "height": self.cut_offset, "top_above_cut": self.top_offset, "bottom_offset": self.bottom_offset, "depth_offset": self.depth_offset,
            "depth": -self.depth_offset, "bottom": -self.bottom_offset,
            "cut_z": self.cut_z, "top_z": self.top_z, "bottom_z": self.bottom_z, "depth_z": self.depth_z,
            "valid": self.problem() is None,
        }


def from_view(view: Mapping[str, Any], *, level_z: float, reference_z: float | None = None) -> ViewRange:
    """The range a plan view stores. A view that stores none of it (an older project) gets the defaults, so there is nothing to migrate."""
    def number(key: str, default: float) -> float:
        value = view.get(key, default)
        try:
            value = float(value)
        except (TypeError, ValueError):
            return default
        return value if math.isfinite(value) else default
    return ViewRange(level_z=float(level_z), reference_z=float(level_z if reference_z is None else reference_z),
                     cut_offset=number("cut_offset", DEFAULT_CUT_OFFSET), top_offset=number("range_top", DEFAULT_RANGE_TOP),
                     bottom_offset=number("range_bottom", DEFAULT_RANGE_BOTTOM), depth_offset=number("range_depth", DEFAULT_RANGE_DEPTH))


def with_change(current: ViewRange, key: str, value: float) -> ViewRange:
    """``current`` with one stored number replaced, validated as a whole. ``key`` is the name the view stores it under."""
    fields = {"cut_offset": "cut_offset", "range_top": "top_offset", "range_bottom": "bottom_offset", "range_depth": "depth_offset"}
    if key not in fields:
        raise ViewRangeError(f"'{key}' is not part of a view range")
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise ViewRangeError("A view range value must be a finite number.")
    values = {name: getattr(current, name) for name in ("cut_offset", "top_offset", "bottom_offset", "depth_offset")}
    values[fields[key]] = float(value)
    return ViewRange(current.level_z, current.reference_z, **values).validate()


# ------------------------------------------------------------------------------------------------------------------------------------- extents
def extent(vertices: Sequence[Sequence[float]]) -> tuple[float, float] | None:
    """The lowest and highest elevation of a mesh's vertices, or None for an empty mesh."""
    if not vertices:
        return None
    zs = [v[2] for v in vertices]
    return min(zs), max(zs)


def _cut_halfspace(polygon: list[Pt3], inside: Any) -> list[Pt3]:
    """The part of a convex polygon on the side where ``inside(point) >= 0`` (Sutherland–Hodgman against one plane)."""
    out: list[Pt3] = []
    n = len(polygon)
    for i in range(n):
        a, b = polygon[i], polygon[(i + 1) % n]
        da, db = inside(a), inside(b)
        if da >= 0:
            out.append(a)
        if (da >= 0) != (db >= 0):
            t = da / (da - db)
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t))
    return out


def clip_triangles(vertices: Sequence[Sequence[float]], faces: Sequence[Sequence[int]], z_low: float, z_high: float) -> list[tuple[Pt3, ...]]:
    """The triangles of a mesh restricted to the slab ``z_low <= z <= z_high``, as convex polygons (a clipped triangle has at most five corners).

    This is a true geometric clip: a sloped roof that rises through the top of the range is cut there, so what remains is the roof's footprint *within the
    range* and not its whole footprint. Triangles wholly outside the slab vanish."""
    out: list[tuple[Pt3, ...]] = []
    for face in faces:
        try:
            tri = [tuple(float(c) for c in vertices[i][:3]) for i in face[:3]]
        except (IndexError, TypeError):
            continue
        if len(tri) != 3:
            continue
        polygon: list[Pt3] = tri  # type: ignore[assignment]
        if min(p[2] for p in polygon) < z_low:
            polygon = _cut_halfspace(polygon, lambda p: p[2] - z_low)
        if polygon and max(p[2] for p in polygon) > z_high:
            polygon = _cut_halfspace(polygon, lambda p: z_high - p[2])
        if len(polygon) >= 3:
            out.append(tuple(polygon))
    return out


QUANTUM = 1e-6        # m: points closer than this are one point when pieces are joined up
MIN_AREA = 1e-9       # m²: smaller pieces of footprint are slivers of rounding, not geometry
SIGN_EPS = 1e-6       # a face whose normal's vertical part is smaller than this is vertical


def _area(loop: Sequence[Pt2]) -> float:
    return sum(loop[i][0] * loop[(i + 1) % len(loop)][1] - loop[(i + 1) % len(loop)][0] * loop[i][1] for i in range(len(loop))) / 2


def _tidy(loop: list[Pt2]) -> list[Pt2]:
    """Drop repeated points and points in a straight run (a triangulated edge has many), keeping real corners."""
    points = [p for i, p in enumerate(loop) if p != loop[i - 1]]
    changed = True
    while changed and len(points) > 3:
        changed = False
        for i in range(len(points)):
            a, b, c = points[i - 1], points[i], points[(i + 1) % len(points)]
            cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
            along = (b[0] - a[0]) * (c[0] - b[0]) + (b[1] - a[1]) * (c[1] - b[1])
            if abs(cross) <= 1e-9 * max(1.0, math.hypot(b[0] - a[0], b[1] - a[1]) * math.hypot(c[0] - b[0], c[1] - b[1])) and along > 0:
                del points[i]
                changed = True
                break
    return points


def _q3(p: Sequence[float]) -> tuple[int, int, int]:
    return (round(p[0] / QUANTUM), round(p[1] / QUANTUM), round(p[2] / QUANTUM))


def _q2(p: Sequence[float]) -> tuple[int, int]:
    return (round(p[0] / QUANTUM), round(p[1] / QUANTUM))


def section(vertices: Sequence[Sequence[float]], faces: Sequence[Sequence[int]], z: float) -> tuple[list[list[Pt2]], list[tuple[Pt2, Pt2]]]:
    """The mesh cut by the horizontal plane at ``z``: ``(rings, open_segments)``. Rings are closed outlines (a closed solid gives rings only); a surface that
    is not closed (a roof that is one sheet) gives its cut as segments that never join into a ring. Faces lying in the plane are not part of the cut."""
    tol = EXTENT_TOLERANCE
    pairs: dict[tuple[tuple[int, int], tuple[int, int]], tuple[Pt2, Pt2]] = {}
    for face in faces:
        try:
            pts = [vertices[i] for i in face[:3]]
        except (IndexError, TypeError):
            continue
        if len(pts) != 3:
            continue
        d = [p[2] - z for p in pts]
        if all(x > tol for x in d) or all(x < -tol for x in d) or all(abs(x) <= tol for x in d):
            continue
        found: dict[tuple[int, int], Pt2] = {}
        for i in range(3):
            j = (i + 1) % 3
            if abs(d[i]) <= tol:
                found[_q2(pts[i])] = (float(pts[i][0]), float(pts[i][1]))
            elif (d[i] > tol and d[j] < -tol) or (d[i] < -tol and d[j] > tol):
                t = d[i] / (d[i] - d[j])
                x, y = pts[i][0] + (pts[j][0] - pts[i][0]) * t, pts[i][1] + (pts[j][1] - pts[i][1]) * t
                found[_q2((x, y))] = (float(x), float(y))
        if len(found) == 2:
            (ka, a), (kb, b) = found.items()
            pairs[(ka, kb) if ka < kb else (kb, ka)] = (a, b) if ka < kb else (b, a)
    adjacency: dict[tuple[int, int], list[tuple[int, int]]] = {}
    coords: dict[tuple[int, int], Pt2] = {}
    for (ka, kb), (a, b) in pairs.items():
        adjacency.setdefault(ka, []).append(kb)
        adjacency.setdefault(kb, []).append(ka)
        coords[ka], coords[kb] = a, b
    used: set[tuple[tuple[int, int], tuple[int, int]]] = set()
    edge = lambda a, b: (a, b) if a < b else (b, a)        # noqa: E731
    rings: list[list[Pt2]] = []
    open_segments: list[tuple[Pt2, Pt2]] = []
    for first in list(pairs):
        if first in used:
            continue
        used.add(first)
        chain = [first[0], first[1]]
        closed = False
        for direction in (0, 1):                                    # on to the end, then (for an open run that began in its middle) the other way
            while True:
                tail = chain[-1]
                step = next((n for n in adjacency.get(tail, ()) if edge(tail, n) not in used), None)
                if step is None:
                    break
                used.add(edge(tail, step))
                if step == chain[0]:
                    closed = True
                    break
                chain.append(step)
            if closed:
                break
            chain.reverse()
        if closed and len(chain) >= 3:
            ring = _tidy([coords[k] for k in chain])
            if len(ring) >= 3 and abs(_area(ring)) >= MIN_AREA:
                rings.append(ring)
        else:
            points = [coords[k] for k in chain]
            straight = [points[0]]
            for a, b, c in zip(points, points[1:], points[2:]):         # a triangulated sheet splits one straight cut at every diagonal: keep the corners
                if abs((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])) > 1e-9 * max(1.0, math.dist(a, b) * math.dist(b, c)):
                    straight.append(b)
            straight.append(points[-1])
            open_segments += [(straight[i], straight[i + 1]) for i in range(len(straight) - 1)]
    return rings, open_segments


@dataclass(frozen=True)
class Footprint:
    """What a mesh covers in plan within a range of elevations, in metres: filled ``triangles`` (faces inside the range, seen from above), filled ``rings``
    (the cross sections at the range's own planes, filled even-odd so a courtyard stays open), and the ``lines`` that outline it (silhouette and boundary
    edges within the range, and the contours where the range's planes cut an open surface). Overlap does not matter: everything is painted one colour."""

    triangles: tuple[tuple[Pt2, Pt2, Pt2], ...] = ()
    rings: tuple[tuple[Pt2, ...], ...] = ()
    lines: tuple[tuple[Pt2, Pt2], ...] = ()

    def __bool__(self) -> bool:
        return bool(self.triangles or self.rings or self.lines)

    def payload(self, factor: float) -> dict[str, Any]:
        """The footprint as the client draws it, scaled by ``factor`` (metres to the drawing's unit)."""
        r = lambda v: round(v * factor, 6)      # noqa: E731
        return {"tris": [[r(c) for p in t for c in p] for t in self.triangles], "rings": [[[r(p[0]), r(p[1])] for p in ring] for ring in self.rings],
                "lines": [[r(a[0]), r(a[1]), r(b[0]), r(b[1])] for a, b in self.lines]}


def _sign(nz: float) -> int:
    return 1 if nz > SIGN_EPS else -1 if nz < -SIGN_EPS else 0


def footprint(vertices: Sequence[Sequence[float]], faces: Sequence[Sequence[int]], z_low: float, z_high: float) -> Footprint:
    """The plan footprint of a mesh within ``z_low .. z_high``.

    The mesh is clipped to the slab and seen from above. Faces that are inside the slab cover what they cover. A wall of the storey below, whose own top
    and bottom are outside the slab, has no horizontal face in it at all, so the cross sections at the slab's two planes are added: for any solid the
    region swept out between two heights is the faces in between together with the cross section at each end, so nothing is lost and nothing is invented.
    The outline is made of the edges that can be seen as a boundary from above (where an up-facing face meets a down-facing or vertical one, and open
    edges), cut to the slab, and of the contours of the two planes. No ring is joined up from loose triangles, so a coarse or imperfect tessellation cannot
    produce a wrong outline."""
    verts = [tuple(float(c) for c in v[:3]) for v in vertices]
    clean = [tuple(int(i) for i in f[:3]) for f in faces if len(f) >= 3 and all(0 <= int(i) < len(verts) for i in f[:3])]
    if not verts or not clean or z_high < z_low:
        return Footprint()
    triangles: dict[tuple[tuple[int, int], ...], tuple[Pt2, Pt2, Pt2]] = {}
    for polygon in clip_triangles(verts, clean, z_low, z_high):
        flat = [(p[0], p[1]) for p in polygon]
        for k in range(1, len(flat) - 1):
            tri = (flat[0], flat[k], flat[k + 1])
            area = _area(tri)
            if abs(area) < MIN_AREA:
                continue                                        # a vertical face seen from above is a line, not an area
            tri = tri if area > 0 else (tri[0], tri[2], tri[1])
            triangles.setdefault(tuple(sorted(_q2(p) for p in tri)), tri)
    low, high = min(v[2] for v in verts), max(v[2] for v in verts)
    rings: list[list[Pt2]] = []
    lines: dict[tuple[tuple[int, int], tuple[int, int]], tuple[Pt2, Pt2]] = {}

    def add_line(a: Pt2, b: Pt2) -> None:
        ka, kb = _q2(a), _q2(b)
        if ka != kb:
            lines.setdefault((ka, kb) if ka < kb else (kb, ka), (a, b))

    planes = [z_low] if abs(z_high - z_low) <= EXTENT_TOLERANCE else [z_low, z_high]
    for plane in planes:
        if low + EXTENT_TOLERANCE < plane < high - EXTENT_TOLERANCE:
            found, loose = section(verts, clean, plane)
            for ring in found:
                if not any(len(ring) == len(old) and {_q2(p) for p in ring} == {_q2(p) for p in old} for old in rings):
                    rings.append(ring)
            for a, b in loose:
                add_line(a, b)
    # Silhouette and boundary edges: one pass over the faces, edges keyed by position so a mesh that repeats vertices still matches up.
    edges: dict[tuple[tuple[int, int, int], tuple[int, int, int]], tuple[Pt3, Pt3, list[int]]] = {}
    for i, j, k in clean:
        a, b, c = verts[i], verts[j], verts[k]
        ux, uy, uz, vx, vy, vz = b[0] - a[0], b[1] - a[1], b[2] - a[2], c[0] - a[0], c[1] - a[1], c[2] - a[2]
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        length = math.sqrt(nx * nx + ny * ny + nz * nz)
        if length < 1e-12:
            continue
        sign = _sign(nz / length)
        for p, q in ((a, b), (b, c), (c, a)):
            kp, kq = _q3(p), _q3(q)
            if kp == kq:
                continue
            key = (kp, kq) if kp < kq else (kq, kp)
            if key in edges:
                edges[key][2].append(sign)
            else:
                edges[key] = (p, q, [sign])
    for p, q, signs in edges.values():
        boundary = len(signs) == 1
        silhouette = any(s1 * s2 < 0 or ((s1 == 0) != (s2 == 0)) for n, s1 in enumerate(signs) for s2 in signs[n + 1:])
        if not (boundary or silhouette):
            continue
        dz = q[2] - p[2]
        if abs(dz) < 1e-12:
            if z_low - EXTENT_TOLERANCE <= p[2] <= z_high + EXTENT_TOLERANCE:
                add_line((p[0], p[1]), (q[0], q[1]))
            continue
        t_a, t_b = (z_low - p[2]) / dz, (z_high - p[2]) / dz
        t0, t1 = max(0.0, min(t_a, t_b)), min(1.0, max(t_a, t_b))
        if t1 - t0 <= 1e-12:
            continue
        add_line((p[0] + (q[0] - p[0]) * t0, p[1] + (q[1] - p[1]) * t0), (p[0] + (q[0] - p[0]) * t1, p[1] + (q[1] - p[1]) * t1))
    return Footprint(tuple(triangles.values()), tuple(tuple(r) for r in rings), tuple(lines.values()))
