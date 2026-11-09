"""Documentation derived from the model: sections, elevations and schedules.

Nothing here is stored. A section, an elevation or a schedule is a *view definition* (where to look, or which category to list) kept in
the project manifest; what it shows is computed from the model's own meshes and parameters every time it is read, so it can never
disagree with the 3D view or the floor plans.

Orthographic projection
    A frame has a looking direction ``f`` in plan and a screen-right axis ``u = f × up``; the picture is ``(u, z)``, metres, z absolute.
    An *elevation* looks at the whole model from one side. A *section* has a cut line ``a → b``: the model is cut by the vertical plane
    through it, the half on the viewer's side is discarded, and what remains is shown beyond the plane (up to ``depth``).

Exactness
    * The cut is the exact intersection of each closed mesh with the plane, stitched into closed loops (the loops are keyed by the mesh
      edge they cross, so two triangles that share an edge always agree on the point). Filled with the even-odd rule, a cut wall shows
      its openings as holes and a cut door leaf sits inside the gap.
    * Surfaces beyond the plane are the mesh faces that face the viewer, clipped to the section's limits. Their drawn lines are the
      boundary and crease edges of those faces (edges inside one flat face are not drawn).
    * Hidden surfaces are removed *between elements* by painting them far to near; inside one element only back faces are dropped.
      This is correct for the building elements SPADRA makes (it is not a general hidden-line algorithm for arbitrary intersecting
      solids, and the documentation says so).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from ..tools.errors import BadRequest, LimitExceeded
from . import elements as E
from . import layers as LY
from . import sfb as SF
from . import geometry as G

SIDES = {"north": (0.0, -1.0), "south": (0.0, 1.0), "east": (-1.0, 0.0), "west": (1.0, 0.0)}      # looking direction of each elevation
SECTION_DEPTH = 30.0                    # default distance looked beyond the cut, metres
MAX_EDGES = 250_000
KEY_DIGITS = 6                          # coordinates are matched to a micrometre when edges are compared

Pt3 = tuple[float, float, float]

# ---------------------------------------------------------------------------------------------------------------- cut hatching (arcering)
# What a cut element is hatched with in sections and plans. The Dutch convention is NEN 47, a paid standard that SPADRA does not hold; its
# patterns are therefore NOT reproduced here and nothing below claims to be NEN 47. These are plain geometric patterns (parallel lines at an
# angle, crossed lines, dots, solid, none) that the person assigns to a material per project, so a studio can match its own office standard
# or the NEN 47 sheet it holds. Every pattern carries ``standard: None`` and the note, and the Properties panel says so.
HATCH_NOTE = "Configurable patterns, not NEN 47: SPADRA does not hold the standard, so no pattern is presented as the official arcering."
HATCH_PATTERNS: dict[str, dict[str, Any]] = {
    "none": {"label": "None (outline only)", "kind": "none", "angle": 0.0, "spacing_mm": 0.0},
    "solid": {"label": "Solid", "kind": "solid", "angle": 0.0, "spacing_mm": 0.0},
    "lines-45": {"label": "Lines 45°", "kind": "lines", "angle": 45.0, "spacing_mm": 2.2},
    "lines-135": {"label": "Lines 135°", "kind": "lines", "angle": 135.0, "spacing_mm": 2.2},
    "lines-0": {"label": "Lines horizontal", "kind": "lines", "angle": 0.0, "spacing_mm": 2.2},
    "lines-90": {"label": "Lines vertical", "kind": "lines", "angle": 90.0, "spacing_mm": 2.2},
    "cross-45": {"label": "Crossed 45°", "kind": "cross", "angle": 45.0, "spacing_mm": 2.2},
    "cross-0": {"label": "Grid", "kind": "cross", "angle": 0.0, "spacing_mm": 2.2},
    "dots": {"label": "Dots", "kind": "dots", "angle": 0.0, "spacing_mm": 2.2},
}
DEFAULT_HATCH = {"Reinforced concrete": "cross-45", "Plywood": "lines-0", "Concrete": "lines-45", "Masonry": "lines-135", "Timber": "lines-0", "Steel": "solid", "Plasterboard": "none", "Glass": "none",
                 "EPS": "cross-45", "XPS": "cross-45", "PIR": "cross-45", "Mineral wool": "cross-45", "Foam glass": "cross-45",       # insulation
                 "Sand": "dots", "Gravel": "dots", "Blinding concrete": "lines-45", "Screed": "none", "Floor finish": "none", "Damp-proof membrane": "solid"}


def hatch_pattern(project: Any, material: Any) -> dict[str, Any] | None:
    """The hatch of a material in this project: its assignment if the person made one, else the default. None for a material with neither."""
    if not isinstance(material, str):
        return None
    pid = (project.manifest.get("hatching") or {}).get(material) or DEFAULT_HATCH.get(material)
    spec = HATCH_PATTERNS.get(pid) if pid else None
    return {"pattern": pid, **spec, "standard": None} if spec else None


# --------------------------------------------------------------------------------------------------------------------- frames
@dataclass(frozen=True)
class Frame:
    ox: float
    oy: float
    fx: float                           # looking direction (unit, in plan)
    fy: float
    span: float | None = None           # section: length of the cut line
    far: float | None = None            # section: how far beyond the plane to look
    cut: bool = False

    @property
    def ux(self) -> float:              # screen right = looking × up
        return self.fy

    @property
    def uy(self) -> float:
        return -self.fx

    def depth(self, p: Pt3) -> float:
        return (p[0] - self.ox) * self.fx + (p[1] - self.oy) * self.fy

    def across(self, p: Pt3) -> float:
        return (p[0] - self.ox) * self.ux + (p[1] - self.oy) * self.uy

    def planes(self) -> list[Callable[[Pt3], float]]:
        """Half-spaces that bound what a section shows (an elevation is unbounded). Each is ≥ 0 inside."""
        if not self.cut:
            return []
        span, far = float(self.span or 0.0), float(self.far or 0.0)
        return [self.depth, lambda p: far - self.depth(p), self.across, lambda p: span - self.across(p)]


def frame_of(view: Mapping[str, Any]) -> Frame:
    kind = view.get("kind")
    if kind == "elevation":
        side = view.get("side")
        if side not in SIDES:
            raise BadRequest(f"'{side}' is not an elevation side")
        fx, fy = SIDES[side]
        return Frame(0.0, 0.0, fx, fy)
    if kind == "section":
        (ax, ay), (bx, by) = view["a"], view["b"]
        length = math.hypot(bx - ax, by - ay)
        if length < 1e-6:
            raise BadRequest("A section needs two different points")
        dx, dy = (bx - ax) / length, (by - ay) / length
        return Frame(ax, ay, -dy, dx, span=length, far=float(view.get("depth", SECTION_DEPTH)), cut=True)      # looking to the left of a → b
    raise BadRequest(f"A {kind} view has no projection")


# -------------------------------------------------------------------------------------------------------------------- meshes
def evaluated_meshes(project: Any) -> dict[str, dict[str, Any]]:
    """Resolve native non-destructive SGEO modifiers once per model revision.

    The authoritative SGEO records remain unmodified; the same derived meshes
    drive 3D display, section/elevation projection and plan cut geometry.
    The SLAI kernel owns evaluator ordering, caching and geometry diagnostics.
    """
    cache = getattr(project, "_cache", None)
    key = ("modifier-meshes", project.model.revision)
    if isinstance(cache, dict) and key in cache:
        return cache[key]
    result: dict[str, dict[str, Any]] = {}
    if project.model.modifiers():
        from src.utils.spatial_formats.sgeo.tessellation import tessellate
        for row in project.model.evaluate_modifiers():
            if row["status"] != "ok" or row["result"] is None:
                continue
            geometry = row["result"].get("geometry")
            if geometry is not None:
                result[row["item_id"]] = tessellate(geometry)
    if isinstance(cache, dict):
        cache[key] = result
    return result


# -------------------------------------------------------------------------------------------------------------------- meshes
def world_mesh(project: Any, oid: str, matrices: Any = None) -> tuple[list[Pt3], list[tuple[int, int, int]]]:
    """The element's mesh in world space. ``matrices`` (SGEO ``world_transforms()``) avoids asking the scene for every element in turn."""
    mesh = evaluated_meshes(project).get(oid)
    if mesh is None:
        mesh = project.model.tessellate(oid)
    t = matrices[oid] if matrices is not None else project.model.world_transform(oid)
    verts = [(t[0] * x + t[1] * y + t[2] * z + t[3], t[4] * x + t[5] * y + t[6] * z + t[7], t[8] * x + t[9] * y + t[10] * z + t[11]) for x, y, z in mesh["vertices"]]
    return verts, [(int(f[0]), int(f[1]), int(f[2])) for f in mesh["faces"]]


def _sub(a: Pt3, b: Pt3) -> Pt3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a: Pt3, b: Pt3) -> Pt3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _lerp(a: Pt3, b: Pt3, t: float) -> Pt3:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def clip_polygon(poly: list[Pt3], dist: Callable[[Pt3], float]) -> list[Pt3]:
    """The part of a convex or concave polygon with ``dist ≥ 0`` (Sutherland–Hodgman against one plane)."""
    out: list[Pt3] = []
    d = [dist(p) for p in poly]
    n = len(poly)
    for i in range(n):
        j = (i + 1) % n
        if d[i] >= 0:
            out.append(poly[i])
        if (d[i] >= 0) != (d[j] >= 0):
            out.append(_lerp(poly[i], poly[j], d[i] / (d[i] - d[j])))
    return out


def _key(p: Pt3) -> tuple[float, float, float]:
    return (round(p[0], KEY_DIGITS), round(p[1], KEY_DIGITS), round(p[2], KEY_DIGITS))


# -------------------------------------------------------------------------------------------------------------- the cut (sections)
def cut_loops(fr: Frame, verts: list[Pt3], faces: list[tuple[int, int, int]]) -> list[list[tuple[float, float]]]:
    """The exact intersection of a closed mesh with the section plane, as closed loops in (across, z), clipped to the section's width."""
    loops = _plane_cut_loops(verts, faces, fr.depth, lambda p: (fr.across(p), p[2]))
    result: list[list[tuple[float, float]]] = []
    for loop in loops:
        clipped = _clip_2d(loop, 0.0, float(fr.span or 0.0))
        if len(clipped) >= 3 and abs(_area2(clipped)) > 1e-9:
            result.append(clipped)
    return result


def horizontal_cut_loops(verts: list[Pt3], faces: list[tuple[int, int, int]], elevation: float) -> list[list[tuple[float, float]]]:
    """Cut any closed BIM mesh at an absolute Z and return true XY loops in metres.

    Reuses the same edge-stitching algorithm as building sections. A plane that
    merely touches a face does not fabricate an interior cut; callers can show
    the projected outline separately where that view's range permits it.
    """
    loops = _plane_cut_loops(verts, faces, lambda p: p[2] - elevation, lambda p: (p[0], p[1]))
    # Triangulated planar faces may put several points on one straight edge;
    # collapse those instead of drawing false corners in the plan.
    result = []
    for loop in loops:
        clean = []
        for i, point in enumerate(loop):
            before, after = loop[i - 1], loop[(i + 1) % len(loop)]
            dx, dy = point[0] - before[0], point[1] - before[1]
            ex, ey = after[0] - point[0], after[1] - point[1]
            if abs(dx * ey - dy * ex) > 1e-8 * max(1.0, math.hypot(dx, dy) * math.hypot(ex, ey)) or dx * ex + dy * ey < 0:
                clean.append(point)
        if len(clean) >= 3:
            result.append(clean)
    return result


def _plane_cut_loops(verts: list[Pt3], faces: list[tuple[int, int, int]],
                     distance: Callable[[Pt3], float], coordinates: Callable[[Pt3], tuple[float, float]]) -> list[list[tuple[float, float]]]:
    """Stitch exact mesh/plane intersections; shared by vertical sections and horizontal plans."""
    d = [distance(v) for v in verts]
    segments: list[tuple[tuple[int, int], tuple[int, int]]] = []
    points: dict[tuple[int, int], tuple[float, float]] = {}
    for tri in faces:
        crossing = []
        for i, j in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
            if (d[i] >= 0) != (d[j] >= 0):
                lo, hi = (i, j) if i < j else (j, i)
                key = (lo, hi)
                if key not in points:
                    p = _lerp(verts[lo], verts[hi], d[lo] / (d[lo] - d[hi]))
                    points[key] = coordinates(p)
                crossing.append(key)
        if len(crossing) == 2:
            segments.append((crossing[0], crossing[1]))
    adjacent: dict[tuple[int, int], list[int]] = {}
    for n, (a, b) in enumerate(segments):
        adjacent.setdefault(a, []).append(n)
        adjacent.setdefault(b, []).append(n)
    used = [False] * len(segments)
    loops: list[list[tuple[float, float]]] = []
    for start in range(len(segments)):
        if used[start]:
            continue
        used[start] = True
        first, here = segments[start]
        chain = [first, here]
        while here != first:
            nxt = next((n for n in adjacent[here] if not used[n]), None)
            if nxt is None:
                break
            used[nxt] = True
            a, b = segments[nxt]
            here = b if a == here else a
            chain.append(here)
        if here != first:
            continue                                                 # an open chain: the mesh was not closed here; nothing honest to fill
        loop = [points[k] for k in chain[:-1]]
        dedup = [p for i, p in enumerate(loop) if i == 0 or math.dist(p, loop[i - 1]) > 1e-9]
        if len(dedup) >= 3 and abs(_area2(dedup)) > 1e-9:
            loops.append(dedup)
    return loops


def _area2(loop: list[tuple[float, float]]) -> float:
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(loop, loop[1:] + loop[:1])) / 2


def _clip_2d(loop: list[tuple[float, float]], lo: float, hi: float) -> list[tuple[float, float]]:
    """Keep ``lo ≤ x ≤ hi`` of a polygon."""
    for side in (0, 1):
        out: list[tuple[float, float]] = []
        n = len(loop)
        if n == 0:
            break
        inside = (lambda p: p[0] - lo) if side == 0 else (lambda p: hi - p[0])
        for i in range(n):
            a, b = loop[i], loop[(i + 1) % n]
            da, db = inside(a), inside(b)
            if da >= 0:
                out.append(a)
            if (da >= 0) != (db >= 0):
                t = da / (da - db)
                out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
        loop = out
    return loop


# ---------------------------------------------------------------------------------------------------------- surfaces facing the viewer
def facing_surfaces(fr: Frame, verts: list[Pt3], faces: list[tuple[int, int, int]]) -> tuple[list[list[tuple[float, float]]], list[tuple[tuple[float, float], tuple[float, float]]], float, float]:
    """(triangles in (across, z), drawn edges, weighted mean depth, area) of the faces of a mesh that face the viewer, clipped to the frame."""
    planes = fr.planes()
    tris: list[list[tuple[float, float]]] = []
    edges: dict[Any, list[Any]] = {}
    weighted, total = 0.0, 0.0
    for ia, ib, ic in faces:
        a, b, c = verts[ia], verts[ib], verts[ic]
        n = _cross(_sub(b, a), _sub(c, a))
        length = math.sqrt(n[0] * n[0] + n[1] * n[1] + n[2] * n[2])
        if length < 1e-12:
            continue
        n = (n[0] / length, n[1] / length, n[2] / length)
        if n[0] * fr.fx + n[1] * fr.fy >= -1e-9:                     # edge-on or facing away
            continue
        poly: list[Pt3] = [a, b, c]
        for plane in planes:
            poly = clip_polygon(poly, plane)
            if len(poly) < 3:
                break
        if len(poly) < 3:
            continue
        flat = [(fr.across(p), p[2]) for p in poly]
        for i in range(1, len(flat) - 1):
            t = [flat[0], flat[i], flat[i + 1]]
            area = _area2(t)
            if area > 1e-12:
                tris.append(t)
                weighted += area * sum(fr.depth(poly[k]) for k in (0, i, i + 1)) / 3
                total += area
        for i in range(len(poly)):
            p, q = poly[i], poly[(i + 1) % len(poly)]
            if any(abs(pl(p)) < 1e-7 and abs(pl(q)) < 1e-7 for pl in planes):      # an edge made by the frame's own limits is not part of the model
                continue
            kp, kq = _key(p), _key(q)
            if kp == kq:
                continue
            k = (kp, kq) if kp < kq else (kq, kp)
            slot = edges.get(k)
            if slot is None:
                edges[k] = [((fr.across(p), p[2]), (fr.across(q), q[2])), 1, n, False]
            else:
                slot[1] += 1
                if slot[2][0] * n[0] + slot[2][1] * n[1] + slot[2][2] * n[2] < 0.9998:       # more than about 1° between the two faces: a crease
                    slot[3] = True
    drawn = [s[0] for s in edges.values() if s[1] == 1 or s[1] > 2 or s[3]]
    return tris, drawn, (weighted / total if total else 0.0), total


# ------------------------------------------------------------------------------------------------------------------ projection
def project_view(project: Any, view: Mapping[str, Any], f: float) -> dict[str, Any]:
    """Everything a section or an elevation shows, in the drawing's unit (``f`` converts metres), ordered far to near."""
    fr = frame_of(view)
    show_opening = bool(view.get("show_opening_direction", True))
    show_hatching = bool(view.get("show_material_hatching", True))
    items: list[dict[str, Any]] = []
    edge_count = 0
    from .project import all_ids
    matrices = project.model._model.world_transforms()                       # one pass for every element (see world_mesh)
    for oid in all_ids(project.model, has_geometry=True):
        obj = project.model.get(oid)
        meta = obj["metadata"]
        category = str(E.m(meta, "category", "generic"))
        if category == "void":
            continue                                                         # a void is not drawn in a section or elevation: it is not part of the building
        verts, faces = world_mesh(project, oid, matrices)
        if not faces:
            continue
        tris, edges, depth, area = facing_surfaces(fr, verts, faces)
        loops = cut_loops(fr, verts, faces) if fr.cut else []
        if not tris and not loops:
            continue
        edge_count += len(edges)
        if edge_count > MAX_EDGES:
            raise LimitExceeded(f"This view has more than {MAX_EDGES} lines to draw", hint="Hide or simplify some of the model")
        item = {"id": oid, "category": category, "name": obj["name"], "depth": depth,
                "tris": [[round(c * f, 4) for p in t for c in p] for t in tris],
                "edges": [[round(c * f, 4) for p in e for c in p] for e in edges],
                "cut": [[[round(x * f, 4), round(y * f, 4)] for x, y in loop] for loop in loops]}
        if category == "roof":
            ridge = E.roof_ridge(meta)
            if ridge:
                # A ridge can only be repositioned horizontally from a 2D
                # projection that actually resolves its transverse movement.
                # Views parallel to the ridge expose no meaningful drag axis.
                transverse = E._rectangle([list(q) for q in E.m(meta, "boundary")])[2]
                factor = transverse[0] * fr.ux + transverse[1] * fr.uy
                if abs(factor) >= 0.2:
                    x, y = ((ridge[0][axis] + ridge[1][axis]) / 2 for axis in (0, 1))
                    top = (E.level_elevation(project, E.m(meta, "level")) + float(E.m(meta, "offset", 0.0)) + E.roof_rise(meta))
                    item["ridge_handle"] = {"point": [round(fr.across((x, y, top)) * f, 4), round(top * f, 4)],
                                            "transverse_dot": round(factor, 10),
                                            "offset_mm": round(float(E.m(meta, "ridge_offset", 0.0)) * 1000, 4)}
        palette = project.manifest.get("material_colors", {})
        # Drawing sheets use their traditional monochrome hatching until the
        # person explicitly assigns a color; the material override is then
        # preserved in the section/elevation's native geometric data.
        appearance = E.m(meta, "color") or palette.get(E.m(meta, "material"))
        if appearance:
            item["appearance_color"] = appearance
        if loops and category in LY.LAYERED and LY.is_layered(obj["metadata"]):
            # a stack of layers: every loop of the cut is one layer, named and (unless the view hides hatching) hatched by its own material
            item["cut_layers"] = LY.loop_layers(project, oid, loops, fr)
            by_id = {lay["id"]: lay for lay in LY.stack(project, oid)}
            item["cut_colors"] = [E.m(meta, "color") or by_id[row["layer"]].get("color") or palette.get(row["material"])
                                  for row in item["cut_layers"]]
            if show_hatching:
                item["cut_hatches"] = [hatch_pattern(project, row["material"]) for row in item["cut_layers"]]
        elif loops and show_hatching and category != "foundation":
            item["cut_hatch"] = hatch_pattern(project, E.m(obj["metadata"], "material"))
        if category in E.OPENINGS and show_opening:
            item["opening"] = _opening_lines(project, oid, fr, f)                # None for a fixed window or a wall seen edge-on
        if category == "foundation" and loops and fr.cut:
            from . import foundations as FD
            item["cut_material"] = FD.loop_materials(project, obj["metadata"], loops)           # each layer of a build-up is hatched as its own material
            item["cut_colors"] = [E.m(meta, "color") or palette.get(mat) for mat in item["cut_material"]]
        items.append(item)
    items.sort(key=lambda i: -i["depth"])                            # far first, so nearer elements paint over what they hide
    xs: list[float] = []
    ys: list[float] = []
    for it in items:
        for t in it["tris"]:
            xs += t[0::2]
            ys += t[1::2]
        for loop in it["cut"]:
            xs += [p[0] for p in loop]
            ys += [p[1] for p in loop]
    levels = [{"name": l["name"], "z": round(float(l["elevation"]) * f, 4)} for l in project.manifest["levels"]]
    out: dict[str, Any] = {"kind": view["kind"], "items": items, "levels": levels,
                           "bounds": [[min(xs), min(ys)], [max(xs), max(ys)]] if xs else None}
    from . import datums as DT
    out["grid_marks"] = DT.grid_marks(project, fr, f)                        # the grids that meet this picture, so a change to a grid reaches every view
    if fr.cut:
        out["span"] = round(float(fr.span or 0) * f, 4)
        out["far"] = round(float(fr.far or 0) * f, 4)
    return out


def _opening_lines(project: Any, oid: str, fr: Frame, f: float) -> dict[str, Any] | None:
    """The opening-direction symbol of a door or window as two lines in the picture's (across, z), in the drawing's unit. Solid when the leaf
    opens toward the viewer, dashed when it opens away (``toward``). A symbol that the frame would cut (a section's limits) is left out
    whole, rather than drawn as half a symbol."""
    sym = E.elevation_symbol(project, oid, (fr.fx, fr.fy))
    if sym is None:
        return None

    def lines(one: dict[str, Any]) -> dict[str, Any] | None:
        pts = [tuple(one[k]) for k in ("apex", "head", "sill")]
        if any(plane(p) < -1e-9 for p in pts for plane in fr.planes()):
            return None
        a, h, s = ((round(fr.across(p) * f, 4), round(p[2] * f, 4)) for p in pts)
        return {"kind": one["kind"], "toward": one["toward"], "lines": [[a[0], a[1], h[0], h[1]], [a[0], a[1], s[0], s[1]]]}

    main = lines(sym)
    more = [x for x in (lines(o) for o in sym.get("more", ())) if x]
    if main is None:
        if not more:
            return None
        main, more = more[0], more[1:]
    return {**main, **({"more": [{"toward": x["toward"], "lines": x["lines"]} for x in more]} if more else {})}


def compass(dx: float, dy: float) -> str:
    """The nearest of eight compass points for a plan direction (north is +Y)."""
    names = ("east", "north-east", "north", "north-west", "west", "south-west", "south", "south-east")
    return names[int(round(math.degrees(math.atan2(dy, dx)) / 45.0)) % 8]


def section_marks(project: Any, f: float) -> list[dict[str, Any]]:
    """The cut lines of every section, for drawing on the plans."""
    marks = []
    for v in project.manifest["views"]:
        if v["kind"] != "section":
            continue
        fr = frame_of(v)
        marks.append({"id": v["id"], "name": v["name"], "a": [round(v["a"][0] * f, 4), round(v["a"][1] * f, 4)], "b": [round(v["b"][0] * f, 4), round(v["b"][1] * f, 4)],
                      "look": [fr.fx, fr.fy], "looking": compass(fr.fx, fr.fy), "depth": round(float(v.get("depth", SECTION_DEPTH)) * f, 4)})
    return marks


# ------------------------------------------------------------------------------------------------------------------- schedules
SCHEDULES: dict[str, dict[str, Any]] = {
    "door": {"title": "Door Schedule", "noun": "Door"},
    "window": {"title": "Window Schedule", "noun": "Window"},
    "wall": {"title": "Wall Schedule", "noun": "Wall"},
    "floor": {"title": "Floor Schedule", "noun": "Floor"},
    "roof": {"title": "Roof Schedule", "noun": "Roof"},
    "gutter": {"title": "Gutter Schedule", "noun": "Gutter"},
    "downpipe": {"title": "Downpipe Schedule", "noun": "Downpipe"},
    "structural": {"title": "Beam Schedule", "noun": "Beam"},
    "room": {"title": "Room Schedule", "noun": "Room"},
    "stair": {"title": "Stair Schedule", "noun": "Stair"},
    "generic": {"title": "Generic Model Schedule", "noun": "Generic model"},
    "foundation": {"title": "Foundation Schedule", "noun": "Foundation"},
    "count": {"title": "Element Count", "noun": "Category", "summary": True},                       # one row per category, over the whole model
    "material": {"title": "Material Quantities", "noun": "Material", "summary": True},            # one row per material, from the quantity take-off
}


def _col(key: str, label: str, unit: str = "", *, total: bool = False, text: bool = False) -> dict[str, Any]:
    return {"key": key, "label": label, "unit": unit, "total": total, "align": "left" if text else "right"}


COLUMNS: dict[str, list[dict[str, Any]]] = {
    "door": [_col("no", "No.", text=True), _col("name", "Name", text=True), _col("level", "Level", text=True), _col("host", "Host wall", text=True),
             _col("width", "Width", "mm"), _col("height", "Height", "mm"), _col("area", "Area", "m²", total=True), _col("material", "Material", text=True),
             _col("hinge", "Hinge", text=True), _col("swing", "Swing", text=True)],
    "window": [_col("no", "No.", text=True), _col("name", "Name", text=True), _col("level", "Level", text=True), _col("host", "Host wall", text=True),
               _col("width", "Width", "mm"), _col("height", "Height", "mm"), _col("sill", "Sill", "mm"), _col("head", "Head", "mm"),
               _col("area", "Area", "m²", total=True), _col("material", "Material", text=True), _col("operation", "Operation", text=True),
               _col("swing", "Swing", text=True)],
    "wall": [_col("no", "No.", text=True), _col("name", "Name", text=True), _col("level", "Level", text=True), _col("length", "Length", "m", total=True),
             _col("height", "Height", "m"), _col("thickness", "Thickness", "mm"), _col("gross", "Gross area", "m²", total=True),
             _col("openings", "Openings", "m²", total=True), _col("net", "Net area", "m²", total=True), _col("volume", "Volume", "m³", total=True),
             _col("material", "Material", text=True), _col("doors", "Doors", total=True), _col("windows", "Windows", total=True)],
    "floor": [_col("no", "No.", text=True), _col("name", "Name", text=True), _col("level", "Level", text=True), _col("area", "Area", "m²", total=True),
              _col("perimeter", "Perimeter", "m", total=True), _col("thickness", "Thickness", "mm"), _col("volume", "Volume", "m³", total=True), _col("material", "Material", text=True)],
    "roof": [_col("no", "No.", text=True), _col("name", "Name", text=True), _col("type", "Type", text=True), _col("level", "Level", text=True),
             _col("pitch", "Pitch", "°"), _col("slope", "Slope", "%"), _col("area", "Plan area", "m²", total=True), _col("thickness", "Thickness", "mm"),
             _col("volume", "Volume", "m³", total=True), _col("material", "Material", text=True)],
    "gutter": [_col("no", "No.", text=True), _col("name", "Name", text=True), _col("type", "Type", text=True), _col("roof", "Roof", text=True), _col("level", "Level", text=True),
               _col("length", "Length", "m", total=True), _col("width", "Width", "mm"), _col("depth", "Depth", "mm"), _col("fall", "Fall", "mm/m"),
               _col("catchment", "Roof area drained", "m²", total=True), _col("flow", "Design flow", "l/s", total=True), _col("downpipes", "Downpipes", total=True),
               _col("volume", "Volume", "m³", total=True), _col("material", "Material", text=True)],
    "downpipe": [_col("no", "No.", text=True), _col("name", "Name", text=True), _col("gutter", "Gutter", text=True), _col("level", "Level", text=True),
                 _col("diameter", "Diameter", "mm"), _col("length", "Length", "m", total=True), _col("bottom", "Discharge height", "m"), _col("flow", "Design flow", "l/s", total=True),
                 _col("volume", "Volume", "m³", total=True), _col("material", "Material", text=True)],
    "structural": [_col("no", "No.", text=True), _col("name", "Name", text=True), _col("type", "Type", text=True), _col("profile", "Profile", text=True), _col("level", "Level", text=True),
                   _col("length", "Length / height", "m", total=True), _col("width", "Width", "mm"), _col("depth", "Depth", "mm"), _col("top", "Top above level", "m"),
                   _col("volume", "Volume", "m³", total=True), _col("material", "Material", text=True)],
    "room": [_col("number", "Number", text=True), _col("name", "Name", text=True), _col("level", "Level", text=True), _col("area", "Area", "m²", total=True),
             _col("perimeter", "Perimeter", "m", total=True), _col("height", "Height", "m"), _col("volume", "Volume", "m³", total=True),
             _col("walls", "Bounding walls", total=True), _col("doors", "Doors", total=True), _col("windows", "Windows", total=True), _col("columns", "Columns", total=True)],
    "stair": [_col("no", "No.", text=True), _col("name", "Name", text=True), _col("level", "Base level", text=True), _col("top_level", "Top level", text=True),
              _col("width", "Width", "mm"), _col("risers", "Risers", total=True), _col("riser", "Riser height", "mm"), _col("tread", "Tread", "mm"),
              _col("rise", "Rise", "m", total=True), _col("run", "Run", "m", total=True), _col("volume", "Volume", "m³", total=True), _col("material", "Material", text=True)],
    "generic": [_col("no", "No.", text=True), _col("name", "Name", text=True), _col("family", "Family", text=True), _col("level", "Level", text=True),
                _col("size_x", "Size X", "mm"), _col("size_y", "Size Y", "mm"), _col("size_z", "Size Z", "mm"), _col("volume", "Volume", "m³", total=True),
                _col("material", "Material", text=True)],
    "foundation": [_col("no", "No.", text=True), _col("name", "Name", text=True), _col("type", "Type", text=True), _col("level", "Level", text=True), _col("stands_on", "Stands on", text=True),
                   _col("length", "Length", "m", total=True), _col("width", "Width", "mm"), _col("height", "Height / thickness", "mm"), _col("area", "Plan area", "m²", total=True),
                   _col("concrete", "Concrete", "m³", total=True), _col("formwork", "Formwork (edges)", "m²", total=True), _col("layers", "Layers", text=True),
                   _col("r_value", "R (build-up)", "m²K/W"), _col("class", "Concrete class", text=True), _col("bars", "Bars (indicative)", total=True),
                   _col("steel_kg", "Steel (indicative)", "kg", total=True), _col("material", "Material", text=True)],
    "count": [_col("category", "Category", text=True), _col("count", "Elements", total=True), _col("detail", "Counted as", text=True)],
    "material": [_col("material", "Material", text=True), _col("count", "Elements", total=True), _col("volume", "Volume", "m³", total=True),
                 _col("area", "Surface area", "m²", total=True), _col("mass", "Mass", "kg", total=True)],
}
for _category, _columns in COLUMNS.items():                                                       # every schedule of elements ends with the element's NL-SfB code
    if not SCHEDULES[_category].get("summary"):
        _columns.extend([_col("assembly_code", "Assembly Code (UniFormat)", text=True),
                         _col("omniclass_21", "OmniClass T21", text=True),
                         _col("omniclass_23", "OmniClass T23", text=True),
                         _col("ifc_type", "IFC Type Product", text=True),
                         _col("ifc_predefined_type", "IFC PredefinedType", text=True),
                         _col("sfb", "NL-SfB", text=True)])
COUNT_AS = {"wall": "Walls", "door": "Doors", "window": "Windows", "floor": "Floors", "roof": "Roofs", "gutter": "Gutters", "downpipe": "Downpipes", "structural": "Columns and beams", "room": "Rooms",
            "stair": "Stairs", "generic": "Generic models", "grid": "Grids", "foundation": "Foundations"}


def _r(value: float, digits: int = 3) -> float:
    return round(value + 0.0, digits)


def _summary(project: Any, category: str) -> dict[str, Any]:
    """The two whole-model schedules: element counts by category, and material quantities (volume and surface area measured by SGEO, mass only
    where a density is known). They list what the model holds; a material or category with nothing in it has no row."""
    rows: list[dict[str, Any]] = []
    if category == "count":
        counts: dict[str, int] = {}
        columns = 0
        for oid in E.element_ids(project):
            meta = project.model.get(oid)["metadata"]
            cat = str(E.m(meta, "category", "generic"))
            counts[cat] = counts.get(cat, 0) + 1
            columns += cat == "structural" and E.is_column(meta)
        for cat in sorted(counts, key=lambda c: COUNT_AS.get(c, c)):
            rows.append({"id": f"count:{cat}", "category": COUNT_AS.get(cat, cat.title()), "count": counts[cat],
                         "detail": f"{columns} columns, {counts[cat] - columns} beams" if cat == "structural" else ""})
    else:
        from . import quantities as Q
        if project.model.query_ids(has_geometry=True, limit=1):
            for b in Q.take_off(project, None)["by_material"]:
                rows.append({"id": f"material:{b['material']}", "material": b["material"], "count": b["count"], "volume": _r(b["volume_m3"]), "area": _r(b["surface_area_m2"]),
                             "mass": _r(b["mass_kg"], 1) if b["mass_kg"] is not None else "-"})
    columns = COLUMNS[category]
    totals = {c["key"]: _r(sum(float(r.get(c["key"], 0) or 0) if r.get(c["key"]) != "-" else 0.0 for r in rows)) for c in columns if c["total"]}
    note = ("Every element of the model, by category." if category == "count" else
            "Measured by SGEO on the modelled geometry; mass only where the material has a density (see Quantities).")
    return {"category": category, "title": SCHEDULES[category]["title"], "columns": columns, "rows": rows, "totals": totals, "count": len(rows), "note": note,
            "summary": True}                                                                    # its rows are groups, not elements: the client does not select them


def view_schedule(project: Any, view: Mapping[str, Any]) -> dict[str, Any]:
    """The data of a schedule *view*: a category schedule, or (``category: "family"``) the parametric schedule of one family (see ``family_schedule``)."""
    if view.get("category") == "family":
        from . import family_schedule as FS
        return FS.data(project, view.get("family"))
    return schedule_data(project, view["category"])


def is_summary_view(view: Mapping[str, Any]) -> bool:
    return view.get("category") != "family" and bool(SCHEDULES[view["category"]].get("summary"))


def schedule_data(project: Any, category: str) -> dict[str, Any]:
    """The rows of one schedule, read from the elements' parameters and exact geometry. Rows are ordered by level, then position, so the
    numbering is the same however the elements were created."""
    if category not in SCHEDULES:
        raise BadRequest(f"There is no schedule of '{category}'", details={"categories": sorted(SCHEDULES)})
    if SCHEDULES[category].get("summary"):
        return _summary(project, category)
    elevations = {l["id"]: float(l["elevation"]) for l in project.manifest["levels"]}
    rows: list[dict[str, Any]] = []
    for oid in E.element_ids(project):
        obj = project.model.get(oid)
        meta = obj["metadata"]
        if E.m(meta, "category") != category:
            continue
        name = obj["name"] or SCHEDULES[category]["noun"]
        solid_volume = E.solid_status(project, oid).volume
        volume = _r(solid_volume) if category != "room" and solid_volume is not None else None                   # a room has no solid; a missing volume stays missing (None), it is not zero
        row: dict[str, Any] = {"id": oid, "name": name, "sfb": SF.read(meta, "sfb") or "-",
                               **{field: str(E.m(meta, field) or "-") for field in
                                  ("assembly_code", "omniclass_21", "omniclass_23", "ifc_type", "ifc_predefined_type")}}
        host = E.m(meta, "host")
        level_id = E.m(meta, "level") if category not in E.OPENINGS else (E.m(E.meta_of(project, host), "level") if host and E.alive(project, host) else None)
        row["level"] = project.level(level_id)["name"] if level_id in elevations else "-"
        order = (elevations.get(level_id, 0.0), 0.0, 0.0)
        if category in E.OPENINGS:
            w, h, sill = float(E.m(meta, "width")), float(E.m(meta, "height")), float(E.m(meta, "sill", 0.0))
            row.update(host=project.model.get(host)["name"] if host and E.alive(project, host) else "(missing)", width=_r(w * 1000, 1), height=_r(h * 1000, 1),
                       area=_r(w * h), material=E.m(meta, "material", "-"))
            if category == "door":
                row.update(hinge=str(E.m(meta, "hinge", "start")), swing="left" if int(E.m(meta, "swing", 1)) > 0 else "right")
            else:
                row.update(sill=_r(sill * 1000, 1), head=_r((sill + h) * 1000, 1), operation=str(E.m(meta, "operation", "fixed")),
                           swing=("left" if int(E.m(meta, "swing", 1)) > 0 else "right") if E.opens(meta) else "-")
            if host and E.alive(project, host):
                fr = E.wall_frame(project, host)
                order = (order[0], fr["mid"][0], fr["mid"][1] + float(E.m(meta, "offset", 0.0)) * 1e-3)
        elif category == "wall":
            fr = E.wall_frame(project, oid)
            kids = E.hosted(project, oid)
            opening_area = sum((s[1] - s[0]) * (s[3] - s[2]) for s in (E.opening_span(project.model.get(k)["metadata"]) for k in kids))
            gross = fr["length"] * fr["height"]
            row.update(length=_r(fr["length"]), height=_r(fr["height"]), thickness=_r(fr["thickness"] * 1000, 1), gross=_r(gross), openings=_r(opening_area),
                       net=_r(gross - opening_area), volume=volume, material=E.m(meta, "material", "-"),
                       doors=sum(1 for k in kids if E.m(project.model.get(k)["metadata"], "category") == "door"),
                       windows=sum(1 for k in kids if E.m(project.model.get(k)["metadata"], "category") == "window"))
            order = (order[0], fr["mid"][0], fr["mid"][1])
        elif category in ("floor", "roof"):
            boundary = [tuple(q) for q in E.m(meta, "boundary")]
            area = abs(G.signed_area(boundary))
            row.update(area=_r(area), thickness=_r(float(E.m(meta, "thickness")) * 1000, 1), volume=volume, material=E.m(meta, "material", "-"))
            if category == "floor":
                row["perimeter"] = _r(sum(math.dist(a, b) for a, b in zip(boundary, boundary[1:] + boundary[:1])))
            else:
                kind = str(E.m(meta, "roof", "flat"))
                pitch = float(E.m(meta, "pitch", 0.0)) if kind != "flat" else 0.0
                row.update(type=E.ROOF_TYPES.get(kind, kind), pitch=_r(pitch, 2), slope=_r(math.tan(math.radians(pitch)) * 100, 1))
            cx = sum(p[0] for p in boundary) / len(boundary)
            cy = sum(p[1] for p in boundary) / len(boundary)
            order = (order[0], cx, cy)
        elif category == "room":
            from . import rooms as R
            d = R.data(project, oid)
            status = R.enclosure_status(project, oid) if E.m(meta, "auto") else {"valid": True}
            room_valid = bool(status["valid"])
            # Undefined measurements must be omitted from totals and CSV,
            # not exported as a stale outline's apparently precise values.
            row.update(number=d["number"], area=_r(d["area"]) if room_valid else None,
                       perimeter=_r(d["perimeter"]) if room_valid else None,
                       height=_r(d["height"]) if room_valid else None,
                       volume=_r(d["volume"]) if room_valid else None,
                       walls=len(d["walls"]), doors=len(d["doors"]), windows=len(d["windows"]), columns=len(d["columns"]))
            if not room_valid:
                row["measurement_warning"] = status["message"]
            order = (order[0], str(d["number"]).zfill(12), 0.0)
        elif category == "gutter":
            from . import gutters as GU
            fr = GU.frame(project, oid)
            c = GU.catchment(project, oid)
            row.update(type=str(E.m(meta, "type", "")), roof=project.model.get(fr["roof"])["name"], length=_r(fr["length"]), width=_r(fr["width"] * 1000, 1), depth=_r(fr["depth"] * 1000, 1),
                       fall=_r(fr["fall"] * 1000, 2), catchment=_r(c), flow=_r(c * float(E.m(meta, "rainfall", GU.RAINFALL_DEFAULT)), 3), downpipes=len(GU.downpipes_of(project, oid)),
                       volume=volume, material=E.m(meta, "material", "-"))
            order = (order[0], fr["a"][0], fr["a"][1])
        elif category == "downpipe":
            from . import gutters as GU
            pf = GU.pipe_frame(project, oid)
            row.update(gutter=project.model.get(pf["gutter"])["name"], diameter=_r(pf["diameter"] * 1000, 1), length=_r(pf["top"] - pf["bottom"]), bottom=_r(pf["bottom"]),
                       flow=_r(GU.pipe_flow(project, oid), 3), volume=volume, material=E.m(meta, "material", "-"))
            order = (order[0], pf["at"][0], pf["at"][1])
        elif category == "stair":
            from . import stairs as SR
            fr = SR.frame(project, oid)
            row.update(top_level=project.level(E.m(meta, "top_level"))["name"], width=_r(fr["width"] * 1000, 1), risers=fr["risers"], riser=_r(fr["riser"] * 1000, 1),
                       tread=_r(fr["tread"] * 1000, 1), rise=_r(fr["height"]), run=_r(fr["run"]), volume=volume, material=E.m(meta, "material", "-"))
            order = (order[0], fr["start"][0], fr["start"][1])
        elif category == "foundation":
            from . import foundations as FD
            q = FD.quantities(project, oid)
            kind = FD.kind_of(meta)
            ids = FD.support_ids(meta)
            r = q.get("thermal")
            rf = q["reinforcement"]
            row.update(type=FD.KINDS[kind]["name"].split(" (")[0], stands_on=", ".join(project.model.get(s)["name"] for s in ids if E.alive(project, s)) or "-",
                       area=_r(q["plan_area_m2"]), height=_r(q["depth_m"] * 1000, 1),
                       concrete=_r(q["concrete_m3"]), formwork=_r(q["formwork_m2"]),
                       layers="; ".join(f"{l['name']} {l['thickness_m'] * 1000:.0f}" for l in q["layers"] if not l["concrete"]) or "-",
                       r_value=(_r(r["r_m2k_w"], 2) if r["complete"] else f"{_r(r['r_m2k_w'], 2)}+") if r else "-", **{"class": q["concrete_class"] or "-"},
                       bars=rf["bars"] if rf else 0, volume=volume, material=E.m(meta, "material", "-"))
            if kind in ("poer", *FD.LINES):
                row["width"] = _r(float(E.m(meta, "width")) * 1000, 1)
            if "length_m" in q:                                                  # numbers that do not apply are left out of the row, not written as dashes: the totals add numbers
                row["length"] = _r(q["length_m"])
            if rf and rf["mass_kg"] is not None:
                row["steel_kg"] = _r(rf["mass_kg"], 1)
            ctr = project.model.world_bounds(oid)
            order = (order[0], (ctr[0][0] + ctr[1][0]) / 2, (ctr[0][1] + ctr[1][1]) / 2)
        elif category == "generic":
            lo, hi = project.model.world_bounds(oid)
            from . import families as FM
            row.update(family=FM.family(project, obj["family_id"])["name"] if "family_id" in obj else str(E.m(obj["metadata"], "type", "Generated")), size_x=_r((hi[0] - lo[0]) * 1000, 1), size_y=_r((hi[1] - lo[1]) * 1000, 1),
                       size_z=_r((hi[2] - lo[2]) * 1000, 1), volume=volume, material=E.m(meta, "material", "-"))
            x, y = E.m(meta, "point") or ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2)          # generated cladding has no point of its own: its centre orders it
            order = (order[0], x, y)
        elif E.is_column(meta):                                                 # structural: column
            x, y = E.m(meta, "point")
            row.update(type="Column", profile="Rectangular", length=_r(float(E.m(meta, "top")) - float(E.m(meta, "base", 0.0))), width=_r(float(E.m(meta, "width")) * 1000, 1),
                       depth=_r(float(E.m(meta, "depth")) * 1000, 1), top=_r(float(E.m(meta, "top")), 3), volume=volume, material=E.m(meta, "material", "-"))
            order = (order[0], x, y)
        else:                                                                   # structural: beam
            s, e = E.m(meta, "start"), E.m(meta, "end")
            row.update(type="Beam", profile=E.beam_section(meta)["size"], length=_r(math.dist(s, e)), width=_r(float(E.m(meta, "width")) * 1000, 1), depth=_r(float(E.m(meta, "depth")) * 1000, 1),
                       top=_r(float(E.m(meta, "top")), 3), volume=volume, material=E.m(meta, "material", "-"))
            order = (order[0], (s[0] + e[0]) / 2, (s[1] + e[1]) / 2)
        rows.append({"_order": order, **row})
    rows.sort(key=lambda r: r["_order"])
    prefix = {"door": "D", "window": "W", "wall": "WL", "floor": "F", "roof": "R", "gutter": "GT", "downpipe": "DP", "structural": "S", "room": "RM", "stair": "ST", "generic": "G", "foundation": "FD"}[category]
    for n, r in enumerate(rows, 1):
        r.pop("_order")
        r["no"] = f"{prefix}{n:02d}"
    columns = COLUMNS[category]
    totals = {c["key"]: _r(sum(float(r.get(c["key"], 0) or 0) for r in rows)) for c in columns if c["total"]}
    if category == "room":
        for column in columns:
            key = column["key"]
            if column["total"] and not any(isinstance(row.get(key), (int, float)) for row in rows):
                totals[key] = None  # no valid measured space is not a measured zero
    invalid_rooms = sum(bool(row.get("measurement_warning")) for row in rows) if category == "room" else 0
    note = "Read from the elements' parameters and exact geometry; numbered by level, then position."
    if invalid_rooms:
        note += f" {invalid_rooms} room(s) have unresolved boundaries; their area, volume and height are omitted from totals and CSV."
    return {"category": category, "title": SCHEDULES[category]["title"], "columns": columns, "rows": rows, "totals": totals, "count": len(rows),
            "note": note}


def schedule_csv(data: Mapping[str, Any]) -> str:
    """The schedule as CSV (RFC 4180), with units in the headers and the totals as the last row."""
    def cell(v: Any) -> str:
        s = "" if v is None else str(v)
        return '"' + s.replace('"', '""') + '"' if any(ch in s for ch in ',"\n\r') else s
    cols = data["columns"]
    lines = [",".join(cell(f"{c['label']} ({c['unit']})" if c["unit"] else c["label"]) for c in cols)]
    for r in data["rows"]:
        lines.append(",".join(cell(r.get(c["key"], "")) for c in cols))
    if data["rows"] and data["totals"]:
        lines.append(",".join(cell("Total" if i == 0 else data["totals"].get(c["key"], "")) for i, c in enumerate(cols)))
    return "\r\n".join(lines) + "\r\n"
