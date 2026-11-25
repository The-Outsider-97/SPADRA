"""The Profiler sketch: editable geometric entities with persistent identities. No rendering, no HTTP, no files (see ``sdrw_io``).

A sketch is plain JSON-able data (``to_dict``/``from_dict``). Geometry is stored as **vertices** and **edges** that reference vertex ids, so
connected edges share a vertex, and a vertex id survives every ordinary edit (joining, splitting, trimming and filleting create or replace edges
with an explicit old->new mapping; see ``ops``). Circles and ellipses keep their radii as *scalars* the solver can drive.

Units: coordinates are in the document unit (``unit``, millimetres by default). Display precision never rounds stored coordinates.
"""
from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from typing import Any, Iterable, Sequence

from src.utils.spatial_formats.common.ids import new_id

from . import geom as G
from . import solver as SV
from .errors import ProfilerConflict, ProfilerError, ProfilerNotFound

SCHEMA = "profiler.sketch.v1"
DEFAULT_COLOR = "#F6F6F6"            # "Traffic White": the default drafting line colour (authoritative PDF, p.3)
UNITS = ("mm", "cm", "m", "in", "ft")
MAX_ENTITIES = 20_000
EDGE_TYPES = ("line", "arc", "circle", "ellipse", "spline")
SPLINE_MODES = ("math", "max", "revit")


def _pt(v: Sequence[float], what: str = "point") -> tuple[float, float]:
    try:
        x, y = float(v[0]), float(v[1])
    except (TypeError, ValueError, IndexError):
        raise ProfilerError("%s must be [x, y]" % what)
    if not (math.isfinite(x) and math.isfinite(y)):
        raise ProfilerError("%s must be finite" % what)
    return (x, y)


class Sketch:
    """A 2D parametric sketch. Every mutating method either completes or raises and leaves the sketch exactly as it was (see ``session``)."""

    def __init__(self, name: str = "Profile", unit: str = "mm") -> None:
        if unit not in UNITS:
            raise ProfilerError("unsupported unit %r (use one of %s)" % (unit, ", ".join(UNITS)))
        self.d: dict[str, Any] = {
            "schema": SCHEMA, "id": new_id(), "name": name, "unit": unit, "precision": 3, "tolerance": 1e-6, "solver_tolerance": SV.TOL,
            "default_color": DEFAULT_COLOR, "revision": 0,
            "vertices": {}, "edges": {}, "scalars": {}, "constraints": [], "dimensions": {}, "chains": {}, "params": {}, "variants": {},
            "groups": {}, "components": {}, "comments": {}, "texts": {}, "layers": [{"name": "0", "visible": True, "locked": False, "color": None}],
            "blocks": {}, "links": [], "imports": [], "styles": {"dimension": {"default": default_dim_style()}},
            "view": {"grid": 10.0, "snap_grid": True, "rulers": True, "axes": True, "origin": [0.0, 0.0]},
        }

    # ------------------------------------------------------------------------------------------------------------ persistence
    def to_dict(self) -> dict[str, Any]:
        return deepcopy(self.d)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Sketch":
        if not isinstance(data, dict) or data.get("schema") != SCHEMA:
            raise ProfilerError("not a Profiler sketch (expected schema %s)" % SCHEMA)
        s = cls(str(data.get("name", "Profile")), data.get("unit", "mm"))
        base = s.d
        for key, val in data.items():
            base[key] = deepcopy(val)
        s.validate_structure()
        return s

    def clone(self) -> "Sketch":
        c = Sketch.__new__(Sketch)
        c.d = deepcopy(self.d)
        return c

    def restore(self, other: "Sketch") -> None:
        self.d = deepcopy(other.d)

    def digest(self) -> str:
        """Hash of the authored content (geometry, constraints, parameters ...), not of volatile fields; used to detect external edits of a file."""
        blob = {k: v for k, v in self.d.items() if k not in ("revision",)}
        return hashlib.sha256(json.dumps(blob, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def validate_structure(self) -> None:
        """Referential integrity of the stored data (a corrupt or hand-edited file is refused with the reason)."""
        d = self.d
        for key in ("vertices", "edges", "scalars", "dimensions", "params", "comments", "texts", "groups", "components", "chains"):
            if not isinstance(d.get(key), dict):
                raise ProfilerError("sketch field %r must be an object" % key)
        if not isinstance(d.get("constraints"), list):
            raise ProfilerError("sketch field 'constraints' must be a list")
        if d["unit"] not in UNITS:
            raise ProfilerError("unsupported unit %r" % d["unit"])
        if len(d["vertices"]) + len(d["edges"]) > MAX_ENTITIES:
            raise ProfilerError("sketch is larger than the %d-entity limit" % MAX_ENTITIES)
        for vid, v in d["vertices"].items():
            _pt((v.get("x"), v.get("y")), "vertex " + vid)
        for eid, e in d["edges"].items():
            if e.get("type") not in EDGE_TYPES:
                raise ProfilerError("edge %s has unsupported type %r" % (eid, e.get("type")))
            for ref in list(e.get("v", [])) + ([e["c"]] if "c" in e else []):
                if ref not in d["vertices"]:
                    raise ProfilerError("edge %s references missing vertex %s" % (eid, ref))
            for key in ("r", "rx", "ry"):
                if key in e and e[key] not in d["scalars"]:
                    raise ProfilerError("edge %s references missing scalar %s" % (eid, e[key]))
        known = set(d["vertices"]) | set(d["edges"]) | set(d["dimensions"])
        for c in d["constraints"]:
            if c.get("state") == "unresolved":
                continue
            for r in c.get("refs", []):
                if r not in known:
                    raise ProfilerError("constraint %s references missing geometry %s" % (c.get("id"), r))

    # ---------------------------------------------------------------------------------------------------------------- basics
    @property
    def unit(self) -> str:
        return self.d["unit"]

    @property
    def vertices(self) -> dict[str, dict]:
        return self.d["vertices"]

    @property
    def edges(self) -> dict[str, dict]:
        return self.d["edges"]

    @property
    def tol(self) -> float:
        return float(self.d["tolerance"])

    def bump(self) -> None:
        self.d["revision"] += 1

    def pos(self, vid: str) -> tuple[float, float]:
        try:
            v = self.d["vertices"][vid]
        except KeyError:
            raise ProfilerNotFound("no vertex %s" % vid)
        return (v["x"], v["y"])

    def edge(self, eid: str) -> dict:
        try:
            return self.d["edges"][eid]
        except KeyError:
            raise ProfilerNotFound("no edge %s" % eid)

    def layer_names(self) -> list[str]:
        return [l["name"] for l in self.d["layers"]]

    def _check_layer(self, layer: str) -> str:
        if layer not in self.layer_names():
            raise ProfilerError("no layer named %r" % layer)
        if self.layer(layer)["locked"]:
            raise ProfilerError("layer %r is locked" % layer)
        return layer

    def layer(self, name: str) -> dict:
        for l in self.d["layers"]:
            if l["name"] == name:
                return l
        raise ProfilerNotFound("no layer %r" % name)

    # -------------------------------------------------------------------------------------------------------------- creation
    def add_vertex(self, p: Sequence[float], *, reuse: bool = True) -> str:
        """Add a vertex at ``p`` (an existing vertex within tolerance is reused, which is what makes drawn edges connect)."""
        x, y = _pt(p)
        if reuse:
            for vid, v in self.d["vertices"].items():
                if abs(v["x"] - x) <= self.tol and abs(v["y"] - y) <= self.tol:
                    return vid
        vid = new_id()
        self.d["vertices"][vid] = {"x": x, "y": y}
        return vid

    def _edge(self, kind: str, *, layer: str = "0", construction: bool = False, **fields) -> str:
        self._check_layer(layer)
        eid = new_id()
        e = {"type": kind, "construction": bool(construction), "layer": layer, "color": None, "locked": False}
        e.update(fields)
        self.d["edges"][eid] = e
        return eid

    def new_scalar(self, value: float) -> str:
        sid = new_id()
        self.d["scalars"][sid] = float(value)
        return sid

    def add_line(self, a: Sequence[float], b: Sequence[float], **kw) -> str:
        va, vb = self.add_vertex(a), self.add_vertex(b)
        if va == vb:
            raise ProfilerError("a line needs two different points")
        return self._edge("line", v=[va, vb], **kw)

    def add_polyline(self, points: Sequence[Sequence[float]], *, closed: bool = False, constrain: bool = False, **kw) -> list[str]:
        pts = [_pt(p) for p in points]
        if len(pts) < 2 or (closed and len(pts) < 3):
            raise ProfilerError("a %s needs at least %d points" % ("closed path" if closed else "path", 3 if closed else 2))
        ids = [self.add_vertex(p) for p in pts]
        if closed and ids[0] == ids[-1]:
            ids = ids[:-1]
        pairs = list(zip(ids, ids[1:])) + ([(ids[-1], ids[0])] if closed else [])
        out = []
        for a, b in pairs:
            if a == b:
                raise ProfilerError("path contains a repeated point")
            out.append(self._edge("line", v=[a, b], **kw))
        if constrain:                                   # like a rectangle: a side drawn exactly horizontal or vertical is held that way
            for e, (a, b) in zip(out, pairs):
                (xa, ya), (xb, yb) = self.pos(a), self.pos(b)
                if abs(ya - yb) <= self.tol:
                    self.add_constraint("horizontal", [e], origin="auto")
                elif abs(xa - xb) <= self.tol:
                    self.add_constraint("vertical", [e], origin="auto")
        return out

    def add_rectangle(self, p1: Sequence[float], p2: Sequence[float], *, constrain: bool = True, **kw) -> list[str]:
        (x1, y1), (x2, y2) = _pt(p1), _pt(p2)
        if abs(x1 - x2) <= self.tol or abs(y1 - y2) <= self.tol:
            raise ProfilerError("a rectangle needs non-zero width and height")
        edges = self.add_polyline([(x1, y1), (x2, y1), (x2, y2), (x1, y2)], closed=True, **kw)
        if constrain:
            for i, e in enumerate(edges):
                self.add_constraint("horizontal" if i % 2 == 0 else "vertical", [e], origin="auto")
        return edges

    def add_circle(self, center: Sequence[float], radius: float, **kw) -> str:
        if radius <= G.EPS:
            raise ProfilerError("circle radius must be positive")
        return self._edge("circle", c=self.add_vertex(center), r=self.new_scalar(radius), **kw)

    def add_ellipse(self, center: Sequence[float], rx: float, ry: float, rotation: float = 0.0, **kw) -> str:
        if rx <= G.EPS or ry <= G.EPS:
            raise ProfilerError("ellipse radii must be positive")
        return self._edge("ellipse", c=self.add_vertex(center), rx=self.new_scalar(rx), ry=self.new_scalar(ry), rot=float(rotation), **kw)

    def add_arc_start_end_radius(self, start: Sequence[float], end: Sequence[float], radius: float, *, large: bool = False, ccw: bool = True, **kw) -> str:
        """The 'start-end-radius arc'. Two centres exist for each direction (minor/major arc): ``large`` picks the major one."""
        s, e = _pt(start), _pt(end)
        try:
            c = G.arc_from_start_end_radius(s, e, float(radius), large=large, ccw=ccw)
        except ValueError as exc:
            raise ProfilerError(str(exc))
        vs, ve, vc = self.add_vertex(s), self.add_vertex(e), self.add_vertex(c)
        if ccw:
            return self._edge("arc", v=[vs, ve], c=vc, **kw)
        return self._edge("arc", v=[ve, vs], c=vc, **kw)           # stored counter-clockwise: a clockwise arc s->e is a ccw arc e->s

    def add_arc_center_ends(self, center: Sequence[float], start: Sequence[float], end: Sequence[float], *, ccw: bool = True, **kw) -> str:
        """The 'centre-ends arc': radius is |centre-start|; the end point is projected onto that circle along the ray centre->end."""
        c, s, e = _pt(center), _pt(start), _pt(end)
        r = G.dist(c, s)
        if r <= G.EPS:
            raise ProfilerError("arc radius is zero: the start point is at the centre")
        if G.dist(c, e) <= G.EPS:
            raise ProfilerError("the end point is at the centre: its direction is undefined")
        e = G.polar(c, r, G.angle_of(G.sub(e, c)))
        if G.dist(s, e) <= G.EPS:
            raise ProfilerError("start and end coincide: use a circle for a full revolution")
        vc, vs, ve = self.add_vertex(c), self.add_vertex(s), self.add_vertex(e)
        return self._edge("arc", v=[vs, ve] if ccw else [ve, vs], c=vc, **kw)

    def add_spline(self, mode: str, points: Sequence[Sequence[float]], *, degree: int = 3, handles: Sequence[Sequence[Sequence[float]]] | None = None, **kw) -> str:
        """Three workflows, three *different* internal curve classes (they are not interchangeable representations):

        ``math``   clamped B-spline of ``degree`` over the control polygon (the curve does not pass through the interior control points);
        ``max``    piecewise cubic Bezier through the points with explicit out/in handles (3ds Max style); handles default to one third of the chord;
        ``revit``  interpolating cubic Hermite through every fit point (Revit style).
        """
        if mode not in SPLINE_MODES:
            raise ProfilerError("spline mode must be one of %s" % ", ".join(SPLINE_MODES))
        pts = [_pt(p) for p in points]
        if len(pts) < (4 if mode == "math" and degree >= 3 else 2):
            raise ProfilerError("this spline needs at least %d points" % (degree + 1 if mode == "math" else 2))
        ids = [self.add_vertex(p, reuse=False) for p in pts]
        fields: dict[str, Any] = {"mode": mode, "v": ids}
        if mode == "math":
            fields["degree"] = int(degree)
        if mode == "max":
            hs = []
            for i in range(len(pts) - 1):
                if handles is not None:
                    out_p, in_p = _pt(handles[i][0], "handle"), _pt(handles[i][1], "handle")
                else:
                    out_p, in_p = G.lerp(pts[i], pts[i + 1], 1 / 3), G.lerp(pts[i], pts[i + 1], 2 / 3)
                hs.append([self.add_vertex(out_p, reuse=False), self.add_vertex(in_p, reuse=False)])
            fields["handles"] = hs
        return self._edge("spline", **fields, **kw)

    def add_polygon(self, center: Sequence[float], radius: float, sides: int, *, rotation: float = 0.0, circumscribed: bool = False, **kw) -> list[str]:
        if sides < 3 or sides > 256:
            raise ProfilerError("a regular polygon has 3..256 sides")
        if radius <= G.EPS:
            raise ProfilerError("polygon radius must be positive")
        c = _pt(center)
        R = radius / math.cos(math.pi / sides) if circumscribed else radius
        pts = [G.polar(c, R, rotation + 2 * math.pi * i / sides) for i in range(sides)]
        edges = self.add_polyline(pts, closed=True, **kw)
        vc = self.add_vertex(c)
        self.d["components"].setdefault("_polygons", {})[edges[0]] = {"center": vc, "edges": edges}
        return edges

    def add_slot(self, p1: Sequence[float], p2: Sequence[float], width: float, *, rounded: bool = True, **kw) -> list[str]:
        """Straight (``rounded=False``) or rounded slot of overall ``width`` around the centre line p1-p2; always a closed contour."""
        a, b = _pt(p1), _pt(p2)
        if width <= G.EPS or G.dist(a, b) <= G.EPS:
            raise ProfilerError("a slot needs a positive width and a non-zero length")
        u = G.unit(G.sub(b, a))
        n = G.perp(u)
        h = width / 2.0
        a1, a2 = G.add(a, G.mul(n, h)), G.add(a, G.mul(n, -h))
        b1, b2 = G.add(b, G.mul(n, h)), G.add(b, G.mul(n, -h))
        if not rounded:
            return self.add_polyline([a2, b2, b1, a1], closed=True, **kw)
        out = [self.add_line(a2, b2, **kw), self.add_arc_center_ends(b, b2, b1, ccw=True, **kw), self.add_line(b1, a1, **kw),
               self.add_arc_center_ends(a, a1, a2, ccw=True, **kw)]
        return out

    # ----------------------------------------------------------------------------------------------------------- constraints
    def add_constraint(self, kind: str, refs: Sequence[str], value: float | Sequence[float] | None = None, *, origin: str = "user", **extra) -> str:
        if kind not in SV.KINDS:
            raise ProfilerError("unsupported constraint kind %r" % kind)
        if len(refs) not in SV.ARITY[kind]:
            raise ProfilerError("%s needs %s references, got %d" % (kind, "/".join(map(str, SV.ARITY[kind])), len(refs)))
        known = set(self.d["vertices"]) | set(self.d["edges"])
        for r in refs:
            if r not in known:
                raise ProfilerNotFound("constraint reference %s does not exist" % r)
        if kind == "fixed" and value is None:
            value = list(self.pos(refs[0]))
        c: dict[str, Any] = {"id": new_id(), "kind": kind, "refs": list(refs), "enabled": True, "origin": origin}
        if value is not None:
            c["value"] = value
        c.update(extra)
        if kind == "tangent":                                    # remember which side / which kind of tangency so the equation is smooth and stable
            c.update(self._tangent_hints(refs, c))
        self.d["constraints"].append(c)
        return c["id"]

    def _tangent_hints(self, refs, c) -> dict:
        ea, eb = self.edge(refs[0]), self.edge(refs[1])
        if ea["type"] == "line" and eb["type"] in ("circle", "arc") or eb["type"] == "line" and ea["type"] in ("circle", "arc"):
            line, circ = (refs[0], refs[1]) if ea["type"] == "line" else (refs[1], refs[0])
            a, b = [self.pos(v) for v in self.edge(line)["v"]]
            cen, rad = self.center_radius(circ)
            return {"side": 1.0 if G.dist_point_line(cen, a, b) >= 0 else -1.0}
        if ea["type"] in ("circle", "arc") and eb["type"] in ("circle", "arc"):
            c1, r1 = self.center_radius(refs[0])
            c2, r2 = self.center_radius(refs[1])
            d = G.dist(c1, c2)
            return {"mode": "external" if abs(d - (r1 + r2)) <= abs(d - abs(r1 - r2)) else "internal"}
        raise ProfilerError("tangent needs a line and a circle/arc, or two circles/arcs")

    def constraint(self, cid: str) -> dict:
        for c in self.d["constraints"]:
            if c["id"] == cid:
                return c
        raise ProfilerNotFound("no constraint %s" % cid)

    def remove_constraint(self, cid: str) -> None:
        self.constraint(cid)
        self.d["constraints"] = [c for c in self.d["constraints"] if c["id"] != cid]

    def pin(self, vid: str) -> str:
        return self.add_constraint("fixed", [vid], list(self.pos(vid)))

    # ------------------------------------------------------------------------------------------------------------- geometry
    def center_radius(self, eid: str) -> tuple[tuple[float, float], float]:
        e = self.edge(eid)
        if e["type"] == "circle":
            return self.pos(e["c"]), self.d["scalars"][e["r"]]
        if e["type"] == "arc":
            c = self.pos(e["c"])
            return c, G.dist(c, self.pos(e["v"][0]))
        raise ProfilerError("edge %s has no centre/radius" % eid)

    def arc_params(self, eid: str) -> tuple[tuple[float, float], float, float, float]:
        e = self.edge(eid)
        c, s, en = self.pos(e["c"]), self.pos(e["v"][0]), self.pos(e["v"][1])
        r = G.dist(c, s)
        return c, r, G.angle_of(G.sub(s, c)), G.arc_sweep(c, s, en, True)

    def edge_polyline(self, eid: str, tol: float = 0.01) -> list[tuple[float, float]]:
        """The evaluated outline of an edge within ``tol`` (maximum sagitta) in document units. The document keeps the exact definition."""
        e = self.edge(eid)
        t = e["type"]
        if t == "line":
            return [self.pos(e["v"][0]), self.pos(e["v"][1])]
        if t == "circle":
            c, r = self.center_radius(eid)
            return G.flatten_arc(c, r, 0.0, G.TAU, True, tol)
        if t == "arc":
            c, r, a0, sw = self.arc_params(eid)
            return G.flatten_arc(c, r, a0, sw, True, tol)
        if t == "ellipse":
            c = self.pos(e["c"])
            rx, ry, rot = self.d["scalars"][e["rx"]], self.d["scalars"][e["ry"]], e.get("rot", 0.0)
            n = max(48, int(math.ceil(math.pi * max(rx, ry) / max(tol * 4, 1e-6) ** 0.5 / 4)))
            n = min(n, 720)
            out = []
            for i in range(n + 1):
                a = G.TAU * i / n
                x, y = rx * math.cos(a), ry * math.sin(a)
                out.append((c[0] + x * math.cos(rot) - y * math.sin(rot), c[1] + x * math.sin(rot) + y * math.cos(rot)))
            return out
        if t == "spline":
            pts = [self.pos(v) for v in e["v"]]
            if e["mode"] == "math":
                return G.bspline_curve(pts, e.get("degree", 3), 96)
            if e["mode"] == "max":
                hs = [(self.pos(a), self.pos(b)) for a, b in e["handles"]]
                return G.bezier_chain(pts, hs, 24)
            return G.hermite_through(pts, n=24)
        raise ProfilerError("unsupported edge type %s" % t)

    def edge_is_closed(self, eid: str) -> bool:
        e = self.edge(eid)
        return e["type"] in ("circle", "ellipse")

    def edge_ends(self, eid: str) -> tuple[str, str] | None:
        e = self.edge(eid)
        if e["type"] in ("line", "arc"):
            return e["v"][0], e["v"][1]
        if e["type"] == "spline":
            return e["v"][0], e["v"][-1]
        return None

    def edge_length(self, eid: str) -> float:
        e = self.edge(eid)
        if e["type"] == "line":
            return G.dist(self.pos(e["v"][0]), self.pos(e["v"][1]))
        if e["type"] == "circle":
            return G.TAU * self.center_radius(eid)[1]
        if e["type"] == "arc":
            _, r, _, sw = self.arc_params(eid)
            return r * sw
        return G.curve_length(self.edge_polyline(eid, 1e-4)) if e["type"] != "ellipse" else G.curve_length(self.edge_polyline(eid, 1e-5))

    def bounds(self, ids: Iterable[str] | None = None) -> tuple[float, float, float, float] | None:
        eids = list(self.d["edges"]) if ids is None else [i for i in ids if i in self.d["edges"]]
        pts = []
        for eid in eids:
            pts.extend(self.edge_polyline(eid, 0.001))
        if ids is not None:
            pts.extend(self.pos(i) for i in ids if i in self.d["vertices"])
        if not pts:
            return None
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))

    # ------------------------------------------------------------------------------------------------------------------ solve
    def solver_inputs(self, extra: Sequence[dict] = (), fixed_points: Sequence[str] = ()):
        d = self.d
        points = {vid: (v["x"], v["y"]) for vid, v in d["vertices"].items()}
        edges = {eid: e for eid, e in d["edges"].items()}
        cons = [c for c in d["constraints"] if c.get("enabled", True) and c.get("state") != "unresolved"]
        cons = cons + list(extra) + self._dimension_constraints()
        return points, dict(d["scalars"]), edges, cons, list(fixed_points)

    def _dimension_constraints(self) -> list[dict]:
        from . import dims
        return dims.driving_constraints(self)

    def solve(self, extra: Sequence[dict] = (), fixed_points: Sequence[str] = (), *, analyse: bool = True) -> SV.SolveResult:
        """Solve without changing the sketch. Use :meth:`apply_solution` to adopt a successful result."""
        pts, sc, edges, cons, fixed = self.solver_inputs(extra, fixed_points)
        held = [v for v in self.feature_holds() if v in pts and v not in fixed]
        try:
            if held:
                r = SV.solve(pts, sc, edges, cons, fixed_points=fixed + held, tol=float(self.d["solver_tolerance"]), check_conflicts=False)
                if r.ok:
                    return SV.solve(pts, sc, edges, cons, fixed_points=fixed + held, tol=float(self.d["solver_tolerance"]), check_conflicts=analyse) if analyse else r
            return SV.solve(pts, sc, edges, cons, fixed_points=fixed, tol=float(self.d["solver_tolerance"]), check_conflicts=analyse)
        except SV.SolverError as exc:
            raise ProfilerError(str(exc))

    def feature_holds(self) -> list[str]:
        """Source vertices that stay put while a *feature's own* dimension is being changed (a fillet radius, a chamfer setback, an offset distance): the
        feature must move to the source, never the other way round. Tried first; if holding them makes the system unsolvable the solve falls back to no holds."""
        from . import dims
        out: list[str] = []
        for f in self.d.get("features", []):
            for did in f.get("dims", []) or []:
                dim = self.d["dimensions"].get(did)
                if not dim or dim.get("mode") not in ("driving", "formula") or dim.get("state") == "unresolved" or dim.get("value") is None:
                    continue
                try:
                    m = abs(dims.measure(self, dim)["value"])
                except Exception:
                    continue
                if abs(m - abs(dim["value"])) > 1e-7 * max(1.0, abs(dim["value"])):
                    out.extend(f.get("holds", []))
                    break
        return list(dict.fromkeys(out))

    def apply_solution(self, result: SV.SolveResult) -> None:
        if not result.ok:
            names = self._names(result.conflicting)
            msg = result.message
            if names and result.status == "conflicting":
                msg = "these constraints cannot all hold: %s; the geometry was left unchanged" % ", ".join(n["label"] for n in names)
            raise ProfilerConflict(msg, details={"conflicting": names, "residuals": result.residuals})
        for vid, (x, y) in result.points.items():
            self.d["vertices"][vid]["x"], self.d["vertices"][vid]["y"] = round(x, 10) + 0.0, round(y, 10) + 0.0       # not -5e-22 where 0 was meant
        for sid, val in result.scalars.items():
            self.d["scalars"][sid] = round(val, 10) + 0.0

    def settle(self, extra: Sequence[dict] = (), fixed_points: Sequence[str] = ()) -> SV.SolveResult:
        """Solve and adopt the result, or raise :class:`ProfilerConflict` leaving every coordinate untouched."""
        r = self.solve(extra, fixed_points)
        self.apply_solution(r)
        return r

    def _names(self, ids: Sequence[str]) -> list[dict]:
        out = []
        for i in ids:
            if i.startswith("dim:"):
                dim = self.d["dimensions"].get(i[4:])
                if dim:
                    val = dim.get("value")
                    nm = dim.get("param") or dim.get("name")
                    lab = "the %s dimension%s%s" % (dim["type"], " " + nm if nm else "", " (= %.6g)" % val if isinstance(val, (int, float)) else "")
                else:
                    lab = i
                out.append({"id": i, "label": lab})
                continue
            if i.startswith("anchor:") or i.startswith("drag:") or i.startswith("tmp:"):
                out.append({"id": i, "label": "temporary " + i.split(":")[0]})
                continue
            try:
                c = self.constraint(i)
                v = c.get("value")
                out.append({"id": i, "label": "the %s constraint%s" % (c["kind"], " (= %.6g)" % v if isinstance(v, (int, float)) else ""), "kind": c["kind"], "origin": c.get("origin")})
            except ProfilerNotFound:
                out.append({"id": i, "label": i})
        return out

    def diagnostics(self) -> dict[str, Any]:
        r = self.solve()
        out = r.to_dict()
        out["conflicting"] = self._names(r.conflicting)
        out["redundant"] = self._names(r.redundant)
        out["points"] = None
        return out


def default_dim_style() -> dict[str, Any]:
    return {"font": "Segoe UI", "text_height": 3.5, "arrow": "arrow", "arrow_size": 2.5, "color": "#F6F6F6", "ext_gap": 1.0, "ext_over": 1.5,
            "precision": 3, "decimal": ".", "unit_label": True, "text_placement": "above", "text_orientation": "aligned",
            "tolerance": None, "line_weight": 0.25}
