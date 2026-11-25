"""Profiler's 2D geometric constraint solver (numpy only).

Why this exists next to ``src.utils.spatial_formats.common.constraint_engine``: that engine is deliberately limited to points and straight
segments and to ten constraint kinds. Profiler sketches need arcs, circles, tangency, symmetry, midpoints, collinearity, radii and chain
equality, so this solver covers those. It uses the same honest contract: the solve is *local* (the solution nearest the current geometry that a
damped Gauss-Newton reaches, not a global one), and degrees of freedom are those of the Jacobian at that solution.

Model. Unknowns are the coordinates of vertices and the radii/extents of circles and ellipses ("scalars"). Fixed vertices and scalars are
constants. Every constraint is a set of scalar residual equations; a driving dimension is a constraint with a value. Arcs are a centre, a start
and an end vertex, with the implicit equation |c-start| = |c-end| added by the solver (it is not a user constraint and never listed as one).

Contract (the PDF's "solver contract"): a failed solve returns the *input* geometry untouched, with the conflicting constraint ids and their
evaluated residuals; nothing is deleted, nothing moves partially. Statuses: ``under`` (degrees of freedom remain), ``full`` (none remain),
``conflicting`` (no solution within tolerance), ``invalid`` (the only solution is degenerate), and the ``redundant`` list (constraints that add
no independent restriction to a consistent system).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np

from . import geom as G

TOL = 1e-9                      # residual tolerance, in drawing units (and radians for angles)
RANK_TOL = 1e-7                 # relative singular value below which a direction is "free"
MAX_ITER = 200
MIN_LENGTH = 1e-7               # a line or radius shorter than this is degenerate

KINDS = {
    "coincident": 2, "point_on": 1, "horizontal": 1, "vertical": 1, "parallel": 1, "perpendicular": 1, "collinear": 2, "equal": 1,
    "tangent": 1, "tangent_at": 1, "concentric": 2, "symmetric": 2, "midpoint": 2, "fixed": 2,
    "distance": 1, "hdistance": 1, "vdistance": 1, "angle": 1, "radius": 1, "diameter": 1, "point_line_distance": 1,
    "equal_distance": 1, "midpoint_at": 2, "ellipse_rx": 1, "ellipse_ry": 1,
}
ARITY = {"coincident": (2,), "point_on": (2,), "horizontal": (1, 2), "vertical": (1, 2), "parallel": (2,), "perpendicular": (2,), "collinear": (2,),
         "equal": (2,), "tangent": (2,), "tangent_at": (3,), "concentric": (2,), "symmetric": (3,), "midpoint": (2,), "fixed": (1,),
         "distance": (2,), "hdistance": (2,), "vdistance": (2,), "angle": (2,), "radius": (1,), "diameter": (1,), "point_line_distance": (2,),
         "equal_distance": (4,), "midpoint_at": (2,), "ellipse_rx": (1,), "ellipse_ry": (1,)}
NEEDS_VALUE = {"distance", "hdistance", "vdistance", "angle", "radius", "diameter", "point_line_distance", "ellipse_rx", "ellipse_ry"}


class SolverError(ValueError):
    """The constraint input itself is malformed (unknown kind, wrong arity, dangling reference)."""


@dataclass
class SolveResult:
    status: str                                  # under | full | conflicting | invalid
    converged: bool
    iterations: int
    points: dict[str, tuple[float, float]]
    scalars: dict[str, float]
    dof: int
    max_residual: float
    residuals: dict[str, float] = field(default_factory=dict)       # constraint id -> largest |residual|
    conflicting: list[str] = field(default_factory=list)
    redundant: list[str] = field(default_factory=list)
    free_vertices: list[str] = field(default_factory=list)
    invalid: list[dict] = field(default_factory=list)
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("under", "full")

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "ok": self.ok, "converged": self.converged, "iterations": self.iterations, "dof": self.dof,
                "max_residual": self.max_residual, "residuals": self.residuals, "conflicting": self.conflicting, "redundant": self.redundant,
                "free_vertices": self.free_vertices, "invalid": self.invalid, "message": self.message}


class _System:
    def __init__(self, points, scalars, edges, constraints, fixed_points, fixed_scalars):
        self.edges = edges
        self.cons = [c for c in constraints if c.get("enabled", True)]
        self.pid = sorted(points)
        self.sid = sorted(scalars)
        self.fixed_p = set(fixed_points)
        self.fixed_s = set(fixed_scalars)
        self.var_p = [p for p in self.pid if p not in self.fixed_p]
        self.var_s = [s for s in self.sid if s not in self.fixed_s]
        self.index: dict[tuple[str, str], int] = {}
        k = 0
        for p in self.var_p:
            self.index[("p", p)] = k
            k += 2
        for s in self.var_s:
            self.index[("s", s)] = k
            k += 1
        self.n = k
        self.const_p = {p: tuple(points[p]) for p in self.fixed_p if p in points}
        self.const_s = {s: scalars[s] for s in self.fixed_s if s in scalars}
        self.x0 = np.zeros(self.n)
        for p in self.var_p:
            i = self.index[("p", p)]
            self.x0[i], self.x0[i + 1] = points[p]
        for s in self.var_s:
            self.x0[self.index[("s", s)]] = scalars[s]
        self.arcs = [e for e in edges.values() if e["type"] == "arc"]
        self._validate()

    # -- accessors on a state vector ------------------------------------------------------------------------------------------
    def P(self, x, pid):
        i = self.index.get(("p", pid))
        if i is None:
            if pid in self.const_p:
                return self.const_p[pid]
            raise SolverError("unknown vertex " + str(pid))
        return (x[i], x[i + 1])

    def S(self, x, sid):
        i = self.index.get(("s", sid))
        if i is None:
            if sid in self.const_s:
                return self.const_s[sid]
            raise SolverError("unknown scalar " + str(sid))
        return x[i]

    def _validate(self):
        for c in self.cons:
            kind = c.get("kind")
            if kind not in KINDS:
                raise SolverError("unsupported constraint kind: %s" % kind)
            if len(c.get("refs", ())) not in ARITY[kind]:
                raise SolverError("constraint %s (%s) needs %s references" % (c.get("id"), kind, "/".join(map(str, ARITY[kind]))))
            if kind in NEEDS_VALUE and not isinstance(c.get("value"), (int, float)):
                raise SolverError("constraint %s (%s) needs a numeric value" % (c.get("id"), kind))
            for r in c["refs"]:
                if r not in self.edges and r not in self.const_p and ("p", r) not in self.index:
                    raise SolverError("constraint %s references unknown geometry %s" % (c.get("id"), r))

    # -- geometry helpers ----------------------------------------------------------------------------------------------------
    def center_radius(self, x, eid):
        e = self.edges[eid]
        if e["type"] == "circle":
            return self.P(x, e["c"]), self.S(x, e["r"])
        if e["type"] == "arc":
            c = self.P(x, e["c"])
            return c, G.dist(c, self.P(x, e["v"][0]))
        raise SolverError("edge %s has no centre/radius" % eid)

    def line_pts(self, x, eid):
        e = self.edges[eid]
        if e["type"] != "line":
            raise SolverError("edge %s is not a straight line" % eid)
        return self.P(x, e["v"][0]), self.P(x, e["v"][1])

    def _pair(self, x, ref_a, ref_b):
        """Two directions: edges (lines) or vertex pairs are not mixed here; refs are line edges."""
        a1, a2 = self.line_pts(x, ref_a)
        b1, b2 = self.line_pts(x, ref_b)
        return a1, a2, b1, b2

    # -- residuals --------------------------------------------------------------------------------------------------------------
    def residual_of(self, c, x) -> list[float]:
        k, r = c["kind"], c["refs"]
        P = lambda v: self.P(x, v)
        if k == "coincident":
            a, b = P(r[0]), P(r[1])
            return [a[0] - b[0], a[1] - b[1]]
        if k == "fixed":
            a = P(r[0])
            v = c.get("value") or [0.0, 0.0]
            return [a[0] - v[0], a[1] - v[1]]
        if k in ("horizontal", "vertical"):
            if len(r) == 1:
                a, b = self.line_pts(x, r[0])
            else:
                a, b = P(r[0]), P(r[1])
            return [a[1] - b[1]] if k == "horizontal" else [a[0] - b[0]]
        if k in ("parallel", "perpendicular"):
            a1, a2, b1, b2 = self._pair(x, r[0], r[1])
            d1, d2 = G.sub(a2, a1), G.sub(b2, b1)
            n = max(G.norm(d1) * G.norm(d2), 1e-12)
            return [(G.cross(d1, d2) if k == "parallel" else G.dot(d1, d2)) / n]
        if k == "collinear":
            a1, a2, b1, b2 = self._pair(x, r[0], r[1])
            d1, d2 = G.sub(a2, a1), G.sub(b2, b1)
            n = max(G.norm(d1) * G.norm(d2), 1e-12)
            return [G.cross(d1, d2) / n, G.dist_point_line(b1, a1, a2)]
        if k == "point_on":
            p, e = P(r[0]), self.edges[r[1]]
            if e["type"] == "line":
                a, b = self.line_pts(x, r[1])
                return [G.dist_point_line(p, a, b)]
            cen, rad = self.center_radius(x, r[1])
            return [G.dist(p, cen) - rad]
        if k == "equal":
            ea, eb = self.edges[r[0]], self.edges[r[1]]
            if ea["type"] == "line" and eb["type"] == "line":
                a1, a2, b1, b2 = self._pair(x, r[0], r[1])
                return [G.dist(a1, a2) - G.dist(b1, b2)]
            if ea["type"] in ("circle", "arc") and eb["type"] in ("circle", "arc"):
                return [self.center_radius(x, r[0])[1] - self.center_radius(x, r[1])[1]]
            raise SolverError("equal needs two lines or two circles/arcs")
        if k == "equal_distance":
            return [G.dist(P(r[0]), P(r[1])) - G.dist(P(r[2]), P(r[3]))]
        if k == "tangent":
            ea, eb = self.edges[r[0]], self.edges[r[1]]
            if ea["type"] == "line" and eb["type"] in ("circle", "arc"):
                a, b = self.line_pts(x, r[0])
                cen, rad = self.center_radius(x, r[1])
                return [G.dist_point_line(cen, a, b) - c.get("side", 1.0) * rad]
            if eb["type"] == "line" and ea["type"] in ("circle", "arc"):
                a, b = self.line_pts(x, r[1])
                cen, rad = self.center_radius(x, r[0])
                return [G.dist_point_line(cen, a, b) - c.get("side", 1.0) * rad]
            if ea["type"] in ("circle", "arc") and eb["type"] in ("circle", "arc"):
                c1, r1 = self.center_radius(x, r[0])
                c2, r2 = self.center_radius(x, r[1])
                d = G.dist(c1, c2)
                return [d - (r1 + r2)] if c.get("mode", "external") == "external" else [d - abs(r1 - r2)]
            raise SolverError("tangent needs a line and a circle/arc, or two circles/arcs")
        if k == "tangent_at":                      # [line|arc, arc, shared vertex]: the line is perpendicular to the radius at the vertex; two arcs have parallel radii there
            p = P(r[2])
            cen, _ = self.center_radius(x, r[1])
            rad = G.sub(p, cen)
            if self.edges[r[0]]["type"] in ("arc", "circle"):
                cen0, _ = self.center_radius(x, r[0])
                rad0 = G.sub(p, cen0)
                return [G.cross(rad0, rad) / max(G.norm(rad0) * G.norm(rad), 1e-12)]
            a, b = self.line_pts(x, r[0])
            d = G.sub(b, a)
            return [G.dot(d, rad) / max(G.norm(d) * G.norm(rad), 1e-12)]
        if k == "concentric":
            c1, c2 = self.center_radius(x, r[0])[0], self.center_radius(x, r[1])[0]
            return [c1[0] - c2[0], c1[1] - c2[1]]
        if k == "midpoint":
            p = P(r[0])
            a, b = self.line_pts(x, r[1])
            m = G.mid(a, b)
            return [p[0] - m[0], p[1] - m[1]]
        if k == "midpoint_at":
            m = G.mid(P(r[0]), P(r[1]))
            v = c.get("value") or [0.0, 0.0]
            return [m[0] - v[0], m[1] - v[1]]
        if k == "symmetric":                       # [v1, v2, axis line]
            p, q = P(r[0]), P(r[1])
            a, b = self.line_pts(x, r[2])
            dv = G.sub(b, a)
            n = max(G.norm(dv), 1e-12)
            return [G.dist_point_line(G.mid(p, q), a, b), G.dot(G.sub(q, p), dv) / n]
        if k == "distance":
            return [G.dist(P(r[0]), P(r[1])) - c["value"]]
        if k == "hdistance":
            return [P(r[1])[0] - P(r[0])[0] - c["value"]]
        if k == "vdistance":
            return [P(r[1])[1] - P(r[0])[1] - c["value"]]
        if k == "point_line_distance":
            a, b = self.line_pts(x, r[1])
            return [G.dist_point_line(P(r[0]), a, b) - c.get("side", 1.0) * c["value"]]
        if k == "angle":
            a1, a2, b1, b2 = self._pair(x, r[0], r[1])
            d1, d2 = G.sub(a2, a1), G.sub(b2, b1)
            ang = math.atan2(G.cross(d1, d2), G.dot(d1, d2))
            diff = ang - c["value"]
            return [math.atan2(math.sin(diff), math.cos(diff))]
        if k in ("radius", "diameter"):
            e = self.edges[r[0]]
            if e["type"] not in ("circle", "arc"):
                raise SolverError("radius/diameter needs a circle or arc")
            rad = self.center_radius(x, r[0])[1]
            return [rad - c["value"]] if k == "radius" else [2.0 * rad - c["value"]]
        if k in ("ellipse_rx", "ellipse_ry"):
            e = self.edges[r[0]]
            return [self.S(x, e["rx" if k == "ellipse_rx" else "ry"]) - c["value"]]
        raise SolverError("unsupported constraint kind: %s" % k)          # pragma: no cover

    def implicit(self, x) -> list[float]:
        out = []
        for e in self.arcs:
            c = self.P(x, e["c"])
            out.append(G.dist(c, self.P(x, e["v"][0])) - G.dist(c, self.P(x, e["v"][1])))
        return out

    def F(self, x) -> np.ndarray:
        out: list[float] = []
        for c in self.cons:
            out.extend(self.residual_of(c, x))
        out.extend(self.implicit(x))
        return np.array(out, dtype=float)

    def rows(self):
        """(constraint id or None for implicit, number of equations) in the order of F."""
        spans, k = [], 0
        for c in self.cons:
            n = len(self.residual_of(c, self.x0))
            spans.append((c["id"], k, k + n))
            k += n
        spans.append((None, k, k + len(self.arcs)))
        return spans

    def jac(self, x) -> np.ndarray:
        f0 = self.F(x)
        J = np.zeros((len(f0), self.n))
        for j in range(self.n):
            h = 1e-6 * max(1.0, abs(x[j]))
            xp, xm = x.copy(), x.copy()
            xp[j] += h
            xm[j] -= h
            J[:, j] = (self.F(xp) - self.F(xm)) / (2 * h)
        return J


def _lm(sys_: _System, x: np.ndarray, tol: float, max_iter: int):
    """Damped Gauss-Newton. Iterates past ``tol`` to ``tol/100`` so a symmetric least-change answer is not left a few 1e-9 off; success is ``< tol``."""
    f = sys_.F(x)
    if len(f) == 0:
        return x, True, 0, 0.0
    if sys_.n == 0:
        m = float(np.max(np.abs(f)))
        return x, m < tol, 0, m
    target = tol / 100.0
    lam, it = 1e-12, 0
    cost = float(f @ f)
    for it in range(1, max_iter + 1):
        if float(np.max(np.abs(f))) < target:
            break
        J = sys_.jac(x)
        A = np.vstack([J, math.sqrt(lam) * np.eye(sys_.n)])
        b = np.concatenate([-f, np.zeros(sys_.n)])
        step, *_ = np.linalg.lstsq(A, b, rcond=None)
        xn = x + step
        fn = sys_.F(xn)
        cn = float(fn @ fn)
        if cn < cost:
            x, f, cost, lam = xn, fn, cn, max(lam / 5.0, 1e-14)
        else:
            lam = min(lam * 10.0, 1e8)
            if float(np.max(np.abs(step))) < 1e-15:
                break
    m = float(np.max(np.abs(f)))
    return x, m < tol, it, m


def _unpack(sys_: _System, x, points, scalars):
    out_p = {p: tuple(points[p]) for p in points}
    for p in sys_.var_p:
        i = sys_.index[("p", p)]
        out_p[p] = (float(x[i]), float(x[i + 1]))
    out_s = dict(scalars)
    for s in sys_.var_s:
        out_s[s] = float(x[sys_.index[("s", s)]])
    return out_p, out_s


def solve(points: Mapping[str, Sequence[float]], scalars: Mapping[str, float], edges: Mapping[str, Mapping[str, Any]],
          constraints: Sequence[Mapping[str, Any]], *, fixed_points: Sequence[str] = (), fixed_scalars: Sequence[str] = (),
          tol: float = TOL, max_iter: int = MAX_ITER, check_conflicts: bool = True) -> SolveResult:
    """Solve and analyse. Never mutates its inputs; on failure ``points``/``scalars`` of the result equal the inputs."""
    pts0 = {k: (float(v[0]), float(v[1])) for k, v in points.items()}
    sc0 = {k: float(v) for k, v in scalars.items()}
    sys_ = _System(pts0, sc0, {k: dict(v) for k, v in edges.items()}, [dict(c) for c in constraints], fixed_points, fixed_scalars)
    x, ok, iters, maxres = _lm(sys_, sys_.x0.copy(), tol, max_iter)
    new_p, new_s = _unpack(sys_, x, pts0, sc0)

    def per_constraint(xv):
        res = {}
        for cid, a, b in sys_.rows()[:-1]:
            f = sys_.F(xv)
            res[cid] = float(np.max(np.abs(f[a:b]))) if b > a else 0.0
        return res

    residuals = per_constraint(x)
    if not ok:
        bad = sorted((cid for cid, v in residuals.items() if v > tol), key=lambda i: -residuals[i])
        return SolveResult("conflicting", False, iters, pts0, sc0, 0, maxres, residuals, bad, [], [], [], 
                           "no solution within tolerance %.1e: the constraints %s cannot all hold; geometry left unchanged" % (tol, ", ".join(bad) or "?"))
    # Degenerate result check
    invalid = []
    for eid, e in sys_.edges.items():
        if e["type"] == "line":
            a, b = new_p[e["v"][0]], new_p[e["v"][1]]
            if G.dist(a, b) < MIN_LENGTH:
                invalid.append({"edge": eid, "problem": "zero-length line"})
        elif e["type"] == "circle" and new_s[e["r"]] < MIN_LENGTH:
            invalid.append({"edge": eid, "problem": "non-positive radius"})
        elif e["type"] == "arc" and G.dist(new_p[e["c"]], new_p[e["v"][0]]) < MIN_LENGTH:
            invalid.append({"edge": eid, "problem": "zero-radius arc"})
    if invalid:
        return SolveResult("invalid", True, iters, pts0, sc0, 0, maxres, residuals, [], [], [], invalid,
                           "the only solution is degenerate (%s); geometry left unchanged" % invalid[0]["problem"])
    # Analysis at the solution
    dof, free, redundant = _analyse(sys_, x, tol) if check_conflicts else (0, [], [])
    status = "full" if dof == 0 else "under"
    return SolveResult(status, True, iters, new_p, new_s, dof, maxres, residuals, [], redundant, free, [],
                       "fully constrained" if dof == 0 else "%d degree%s of freedom remain" % (dof, "" if dof == 1 else "s"))


def _analyse(sys_: _System, x, tol):
    if sys_.n == 0:
        return 0, [], []
    J = sys_.jac(x)
    if J.shape[0] == 0:
        return sys_.n, [p for p in sys_.var_p], []
    U, s, Vt = np.linalg.svd(J, full_matrices=True)
    cutoff = RANK_TOL * max(1.0, float(s[0]) if len(s) else 1.0)
    rank = int(np.sum(s > cutoff))
    dof = sys_.n - rank
    null = Vt[rank:]
    free = []
    if dof:
        for p in sys_.var_p:
            i = sys_.index[("p", p)]
            if float(np.linalg.norm(null[:, i:i + 2])) > 1e-6:
                free.append(p)
    # Redundant: a constraint whose rows add no rank to those before it (system is consistent here by construction)
    redundant, acc, acc_rank = [], np.zeros((0, sys_.n)), 0
    for cid, a, b in sys_.rows()[:-1]:
        block = J[a:b]
        trial = np.vstack([acc, block])
        r = int(np.linalg.matrix_rank(trial, tol=cutoff)) if trial.size else 0
        if r == acc_rank and b > a:
            redundant.append(cid)
        else:
            acc, acc_rank = trial, r
    # the implicit arc rows count toward earlier rank so account for them first
    return dof, free, redundant
