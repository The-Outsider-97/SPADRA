"""Hand-off: a validated profile becomes a SPADRA extrusion, void, sweep, revolve or opening profile.

Two phases, so a failure can never damage the model:

1. ``prepare`` runs entirely inside Profiler. It refuses (with the Check Profile reasons) unless the profile is suitable for the operation, instantiates parameter
   overrides on a *copy*, flattens the geometry into plain polygons/paths in the project's unit, and records provenance (which sketch edge produced which outline
   segment, namespaced parameters, a content digest). The result is a plain JSON ``payload``.
2. ``create`` / ``update`` (called from SPADRA commands, which run inside ``Project.execute`` and roll back on any exception) turn the payload into model objects.

Link modes: ``static`` keeps geometry only; ``embedded`` also stores a compressed copy of the profile inside the object (edit it later without any file);
``linked`` stores the path, digest and revision of the ``.sdrw`` file so the object can be re-synchronised when the file changes. Every object remembers the sketch
id, the revision it was built from and per-instance parameter overrides under the ``profiler`` key.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.utils.spatial_formats.common import units as U

from . import geom as G
from . import ops, regions, validate as V
from . import params as PM
from .errors import ProfilerError, ProfilerNotFound
from .model import Sketch

OPTION_KEYS = ("role", "region_edges", "region_index", "path_edges", "path_points", "closed", "pivot", "shape", "width", "height", "section", "axis", "opening", "replace",
               "clearance", "segments", "angle", "at", "void", "part_color")
KINDS = ("extrude", "void", "sweep", "revolve", "opening")
MODES = ("static", "embedded", "linked")
LINK_KEY = "profiler"
LINK_VERSION = 1
HOLE_OVERSHOOT = 0.001                 # metres each way: a hole's companion void is a hair taller than the solid it cuts, so the cut faces are not coplanar
MAX_POINTS = 1900                      # SPADRA outlines accept up to 2000


# ------------------------------------------------------------------------------------------------------------ geometry
def _diag(sk: Sketch) -> float:
    b = sk.bounds()
    return G.dist((b[0], b[1]), (b[2], b[3])) if b else 1.0


def instantiate(sk: Sketch, overrides: Mapping[str, float] | None) -> Sketch:
    """A copy of the profile with per-instance parameter values applied (the original is never touched)."""
    c = sk.clone()
    for name, val in (overrides or {}).items():
        if name not in c.d["params"]:
            raise ProfilerNotFound("the profile has no parameter %r" % name)
        if c.d["params"][name].get("expression"):
            raise ProfilerError("parameter %r is a formula: override the parameters it depends on" % name)
        PM.set_param(c, name, value=float(val))
    return c


def _owners(sk: Sketch, ring: Sequence[Sequence[float]], edge_ids: Sequence[str]) -> list[str]:
    """For each polygon segment of ``ring`` the sketch edge it was flattened from (nearest edge to the segment midpoint)."""
    polys = {e: sk.edge_polyline(e, 0.002) for e in edge_ids}
    out = []
    n = len(ring)
    for i in range(n):
        a, b = ring[i], ring[(i + 1) % n]
        m = G.mid(a, b)
        best, who = 1e18, edge_ids[0]
        for e, pl in polys.items():
            d = min(G.nearest_on_segment(m, pl[j], pl[j + 1])[1] for j in range(len(pl) - 1))
            if d < best:
                best, who = d, e
        out.append(who)
    return out


def _dedupe(ring: list[tuple[float, float]], owners: list[str] | None = None, tol: float = 1e-9):
    out, own = [], []
    for i, p in enumerate(ring):
        if out and G.dist(p, out[-1]) <= tol:
            continue
        out.append(p)
        if owners is not None:
            own.append(owners[i])
    if len(out) > 1 and G.dist(out[0], out[-1]) <= tol:
        out.pop()
        if owners is not None:
            own.pop()
    return out, own


def region_geometry(sk: Sketch, edges: Sequence[str] | None = None, region_index: int | None = None) -> dict[str, Any]:
    ana = regions.analyse(sk)
    regs = ana["regions"]
    if edges:
        reg = regions.region_containing_edges(sk, list(edges))
        if reg is None:
            raise ProfilerError("the selected edges do not bound a closed region")
    elif region_index is not None:
        if not 0 <= region_index < len(regs):
            raise ProfilerError("there is no region number %d (the profile has %d)" % (region_index + 1, len(regs)))
        reg = regs[region_index]
    else:
        top = [r for r in regs if not any(r is not o and r["outer"]["points"] and G.point_in_ring(r["outer"]["points"][0], o["outer"]["points"]) for o in regs)]
        if len(top) != 1:
            raise ProfilerError("the profile has %d separate regions: select the one to send" % len(top), details={"regions": len(top)})
        reg = top[0]
    tol = max(_diag(sk) * 2e-4, 1e-4)
    # re-flatten the outer boundary at the hand-off tolerance, keeping which edge each segment came from
    outer_ids = list(reg["outer"]["edges"])
    ring = [tuple(p) for p in reg["outer"]["points"]]
    if len(ring) > MAX_POINTS:
        raise ProfilerError("the outline has %d points: simplify the curves first (the limit is %d)" % (len(ring), MAX_POINTS))
    ring, _ = _dedupe(ring)
    if G.signed_area(ring) < 0:
        ring = ring[::-1]
    owners = _owners(sk, ring, outer_ids)
    holes = []
    for h in reg["holes"]:
        hp, _ = _dedupe([tuple(p) for p in h["points"]])
        if G.signed_area(hp) > 0:
            hp = hp[::-1]
        holes.append({"points": [list(p) for p in hp], "edges": list(h["edges"]), "owners": _owners(sk, hp, list(h["edges"]))})
    return {"outline": [list(p) for p in ring], "owners": owners, "holes": holes, "area": reg["area"], "perimeter": reg["perimeter"], "edges": list(reg["edges"]),
            "region_key": hashlib.sha1(",".join(sorted(reg["edges"])).encode()).hexdigest()[:12], "tolerance": tol}


def path_geometry(sk: Sketch, edge_ids: Sequence[str]) -> dict[str, Any]:
    from . import topo
    if not edge_ids:
        raise ProfilerError("choose the edges that form the sweep path")
    chain, closed = topo.order_chain(sk, list(edge_ids))
    pts: list[tuple[float, float]] = []
    owners: list[str] = []
    for eid, fwd in chain:
        pl = sk.edge_polyline(eid, max(_diag(sk) * 1e-5, 1e-4))        # fine enough that a rounded corner's length is right to well under a millimetre
        pl = pl if fwd else pl[::-1]
        for j, p in enumerate(pl):
            if pts and G.dist(p, pts[-1]) <= 1e-9:
                continue
            pts.append(p)
            owners.append(eid)
    if closed and len(pts) > 2 and G.dist(pts[0], pts[-1]) <= 1e-9:
        pts.pop()
        owners.pop()
    if len(pts) > 500:
        raise ProfilerError("the path has %d points; a sweep path is limited to 500" % len(pts))
    return {"points": [list(p) for p in pts], "owners": owners, "closed": closed, "edges": [e for e, _ in chain]}


# ----------------------------------------------------------------------------------------------------------------- prepare
def _line_color(sk: Sketch, edge_ids: Sequence[str]) -> str | None:
    """The colour the person gave the outline: the most common explicit colour among its lines (a line's own colour, else its layer's). None when nothing was
    coloured, so the element keeps its material colour instead of turning the default white of a drawing line."""
    layers = {l["name"]: l.get("color") for l in sk.d["layers"]}
    seen: dict[str, int] = {}
    for eid in edge_ids:
        e = sk.edge(eid)
        c = e.get("color") or layers.get(e.get("layer"))
        if isinstance(c, str) and len(c) == 7 and c.startswith("#"):
            seen[c.upper()] = seen.get(c.upper(), 0) + 1
    return max(seen, key=lambda c: seen[c]) if seen else None


def _explicit_color(sk: Sketch, eid: str) -> str | None:
    """The colour a person gave a line (its own, else its layer's); None for a line left at the default."""
    layers = {l["name"]: l.get("color") for l in sk.d["layers"]}
    e = sk.edge(eid)
    c = e.get("color") or layers.get(e.get("layer"))
    return c.upper() if isinstance(c, str) and len(c) == 7 and c.startswith("#") else None


def _segment_colors(sk: Sketch, ring: Sequence[Sequence[float]], owners: Sequence[str], scale: float = 1.0, shift: Sequence[float] = (0.0, 0.0)) -> list[dict[str, Any]]:
    """The coloured segments of a closed ring: ``{a, b, c}`` for every segment whose line was given a colour. Only these faces take the colour in the model."""
    out = []
    n = len(ring)
    for i, eid in enumerate(owners):
        c = _explicit_color(sk, eid)
        if c:
            a, b = ring[i], ring[(i + 1) % n]
            out.append({"a": [(a[0] - shift[0]) * scale, (a[1] - shift[1]) * scale], "b": [(b[0] - shift[0]) * scale, (b[1] - shift[1]) * scale], "c": c})
    return out


def parameter_info(sk: Sketch) -> list[dict[str, Any]]:
    """What SPADRA needs to show and edit the profile's parameters: name, type, unit, value, and whether a formula drives it."""
    from . import params as P
    rows = []
    for row in P.listing(sk):
        if isinstance(row.get("value"), (int, float, bool)):
            rows.append({"name": row["name"], "type": row["type"], "unit": row["unit"], "value": row["value"], "derived": bool(row["derived"]), "read_only": bool(row["read_only"]),
                         "min": row.get("min"), "max": row.get("max")})
    return rows


def exposed_parameters(sk: Sketch) -> dict[str, float]:
    """The profile's parameters under a ``profiler.`` namespace so they cannot collide with SPADRA's own (values in the document unit / degrees)."""
    from . import params as P
    out = {}
    for row in P.listing(sk):
        if isinstance(row.get("value"), (int, float, bool)):
            out["profiler." + row["name"]] = row["value"]
    return out


def prepare(S, kind: str, options: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Validate and flatten. Raises ``ProfilerError`` (with ``details['issues']``) when the profile is not suitable; never touches a model."""
    o = dict(options or {})
    if kind not in KINDS:
        raise ProfilerError("hand-off kind must be one of %s" % ", ".join(KINDS))
    mode = o.get("mode", "embedded")
    if mode not in MODES:
        raise ProfilerError("link mode must be one of %s" % ", ".join(MODES))
    if mode == "linked" and not S.path:
        raise ProfilerError("a linked hand-off needs a saved file: Save the profile first (or choose Embedded)")
    if mode == "linked" and S.dirty:
        raise ProfilerError("the profile has unsaved changes: Save it first so the link points at what you are sending")
    sk = instantiate(S.sk, o.get("overrides"))
    project_unit = o.get("project_unit", "mm")
    if project_unit not in U.METERS_PER_UNIT:
        raise ProfilerError("unknown project unit %r" % project_unit)
    k = U.METERS_PER_UNIT[sk.unit] / U.METERS_PER_UNIT[project_unit]       # sketch unit -> project drawing unit
    to_m = U.METERS_PER_UNIT[project_unit]
    purpose = {"extrude": "extrude", "void": "void", "revolve": "revolve", "opening": "opening", "sweep": "sweep_section" if o.get("role", "section") == "section" else "sweep_path"}[kind]
    axis = o.get("axis")
    rep = V.check(sk, purpose, axis=axis)
    if not rep["suitable"]:
        errs = [i for i in rep["issues"] if i["severity"] == "error"]
        raise ProfilerError("this profile is not suitable for %s: %s" % (purpose.replace("_", " "), "; ".join(i["message"] for i in errs[:4])),
                            details={"issues": errs, "purpose": purpose})
    payload: dict[str, Any] = {"kind": kind, "mode": mode, "purpose": purpose, "name": str(o.get("name") or sk.d["name"])[:80], "level": o.get("level"),
                               "sketch": {"id": sk.d["id"], "name": sk.d["name"], "revision": S.sk.d["revision"], "digest": S.sk.digest(), "unit": sk.unit},
                               "warnings": [i["message"] for i in rep["issues"] if i["severity"] == "warning"]}
    scale = lambda pts: [[p[0] * k, p[1] * k] for p in pts]
    link: dict[str, Any] = {"tool": "profiler", "version": LINK_VERSION, "mode": mode, "kind": kind, "sketch_id": sk.d["id"], "sketch_name": sk.d["name"],
                            "revision": S.sk.d["revision"], "digest": S.sk.digest(), "overrides": dict(o.get("overrides") or {}),
                            "params": exposed_parameters(sk), "source_unit": sk.unit}
    if mode == "linked":
        from . import sdrw_io as IO
        link["file"] = str(Path(S.path).absolute())
        link["file_sha256"] = S.meta.get("source_sha256")
    elif mode == "embedded":
        from . import sdrw_io as IO
        link["embedded"] = IO.encode_sketch(S.sk)
    if kind in ("extrude", "void", "opening", "revolve") or (kind == "sweep" and o.get("role", "section") == "section"):
        g = region_geometry(sk, o.get("region_edges"), o.get("region_index"))
        link["region"] = {"key": g["region_key"], "edges": g["edges"]}
        link["provenance"] = {"outline_segments": g["owners"], "hole_edges": [h["edges"] for h in g["holes"]]}
    if kind in ("extrude", "void"):
        payload["command"] = {"points": scale(g["outline"]), "void": kind == "void", "name": payload["name"], "level": o.get("level")}
        if o.get("base") is not None:
            payload["command"]["base"] = float(o["base"])
        if o.get("material"):
            payload["command"]["material"] = o["material"]
        if o.get("depth") is not None:
            payload["command"]["depth"] = float(o["depth"])
        payload["holes"] = [scale(h["points"]) for h in g["holes"]]
        if kind == "extrude":
            payload["edge_colors"] = _segment_colors(sk, g["outline"], g["owners"], k) + [c for h in g["holes"] for c in _segment_colors(sk, h["points"], h["owners"], k)]
    elif kind == "opening":
        if not o.get("opening"):
            raise ProfilerError("choose the door or window this opening profile belongs to")
        payload["command"] = {"opening": str(o["opening"]), "section": scale(g["outline"]), "replace": bool(o.get("replace", False)), "name": payload["name"],
                              "clearance": float(o.get("clearance", 3.0))}
        if o.get("material"):
            payload["command"]["material"] = o["material"]
        if g["holes"]:
            payload["warnings"].append("inner loops are ignored: an opening profile is a single closed outline")
    elif kind == "revolve":
        (ox, oy), d = V._axis(axis)
        to_rh = lambda pts: [(G.cross(d, G.sub(tuple(p), (ox, oy))) * -1.0, G.dot(G.sub(tuple(p), (ox, oy)), d)) for p in pts]
        ring = to_rh(g["outline"])
        hole_rings = [to_rh(h["points"]) for h in g["holes"]]
        if min(r for r, _ in ring) < -1e-9:                                   # the profile is on the other side of the axis: mirror it (the inner loops with it)
            ring = [(-r, h) for r, h in ring][::-1]
            hole_rings = [[(-r, h) for r, h in hr][::-1] for hr in hole_rings]
        angle = float(o.get("angle", 360.0))
        if not 0.0 < angle <= 360.0:
            raise ProfilerError("the revolve angle must be above 0 and at most 360 degrees")
        payload["command"] = {"profile": [[r * k, h * k] for r, h in ring], "holes": [[[r * k, h * k] for r, h in hr] for hr in hole_rings], "angle": angle, "segments": int(o.get("segments", 48)), "name": payload["name"], "level": o.get("level")}
        for key in ("at", "base", "material"):
            if o.get(key) is not None:
                payload["command"][key] = o[key] if key == "material" else ([float(x) for x in o[key]] if key == "at" else float(o[key]))
        link["axis"] = {"point": [ox, oy], "direction": list(d)}
    elif kind == "sweep":
        if o.get("role", "section") == "section":
            path_pts = o.get("path_points")
            if path_pts is None and o.get("path_edges"):
                pg = path_geometry(sk, o["path_edges"])
                path_pts, closed = scale(pg["points"]), pg["closed"]
                link["provenance"]["path_segments"] = pg["owners"]
            elif path_pts is not None:
                closed = bool(o.get("closed", False))
            else:
                raise ProfilerError("a sweep needs a path: choose path edges or give path points")
            # section polygon in metres, (u, v): centred on its bounding-box centre unless a pivot point is given
            piv = o.get("pivot")
            cx, cy = (float(piv[0]), float(piv[1])) if piv else ((min(p[0] for p in g["outline"]) + max(p[0] for p in g["outline"])) / 2, (min(p[1] for p in g["outline"]) + max(p[1] for p in g["outline"])) / 2)
            section = [[(p[0] - cx) * U.METERS_PER_UNIT[sk.unit], (p[1] - cy) * U.METERS_PER_UNIT[sk.unit]] for p in g["outline"]]
            payload["command"] = {"points": path_pts, "closed": closed, "section": section, "name": payload["name"], "void": bool(o.get("void", False)), "level": o.get("level")}
            if not o.get("void", False):
                payload["edge_colors"] = _segment_colors(sk, g["outline"], g["owners"], U.METERS_PER_UNIT[sk.unit], (cx, cy))
            for key in ("base", "material"):
                if o.get(key) is not None:
                    payload["command"][key] = o[key] if key == "material" else float(o[key])
            if g["holes"]:
                payload["warnings"].append("inner loops are ignored: a sweep section is a single closed outline")
        else:
            pg = path_geometry(sk, o.get("path_edges") or [e for e, ed in sk.edges.items() if not ed.get("construction")])
            link["provenance"] = {"path_segments": pg["owners"]}
            color_edges = pg["edges"]
            command = {"points": scale(pg["points"]), "closed": pg["closed"], "name": payload["name"], "void": bool(o.get("void", False)), "level": o.get("level")}
            for key in ("base", "material"):
                if o.get(key) is not None:
                    command[key] = o[key] if key == "material" else float(o[key])
            if o.get("section"):
                command["section"] = o["section"]
            else:
                command.update(shape=o.get("shape", "rectangle"), width=float(o.get("width", 200)), height=float(o.get("height", 200)))
            payload["command"] = command
    color_edges = locals().get("color_edges") or (link.get("region") or {}).get("edges") or []
    part = (sk.d.get("appearance") or {}).get("color")
    if kind == "void":
        payload["color"] = None
    elif part:
        payload["color"] = part                                                 # the profile's colour parameter
    elif kind in ("revolve", "opening") or (kind == "sweep" and o.get("role", "section") != "section"):
        payload["color"] = _line_color(sk, color_edges)                         # no faces to match the lines to: the most common line colour
    else:
        payload["color"] = None
    link["params_info"] = parameter_info(sk)
    link["options"] = {k: o[k] for k in OPTION_KEYS if o.get(k) is not None}      # what the person chose at upload, so "update" can rebuild the same thing
    if o.get("part_color"):
        payload["color"] = str(o["part_color"]).upper()
        link["params_info"] = link["params_info"] + [{"name": "colour", "type": "colour", "value": payload["color"]}]
    elif part:
        link["params_info"] = link["params_info"] + [{"name": "colour", "type": "colour", "value": part}]
    payload["link"] = link
    payload["linked_geometry_digest"] = hashlib.sha1(repr(payload.get("command")).encode()).hexdigest()[:16]
    return payload


# ---------------------------------------------------------------------------------------------------------------- revolve
def revolve_mesh(profile: Sequence[Sequence[float]], angle_deg: float, segments: int, at: Sequence[float], z0: float, to_m: float,
                 holes: Sequence[Sequence[Sequence[float]]] = ()):
    """Solid of revolution about a vertical axis through ``at``. ``profile`` is the outline [(r, h)] in project units and ``holes`` its inner loops (each a ring
    that lies inside the outline). A full turn turns every loop into a closed surface (an inner loop becomes a cavity); a partial turn also gets two flat end
    caps, which are the profile itself with its holes cut out. Returns (vertices, faces) in metres."""
    from . import tri as TRI
    if len(profile) < 3:
        raise ProfilerError("a revolve profile needs at least 3 points")
    full = angle_deg >= 360.0 - 1e-9
    steps = max(3, min(int(segments), 360))
    th = [math.radians(angle_deg) * i / steps for i in range(steps + (0 if full else 1))]

    def area(ring):
        return sum(ring[i][0] * ring[(i + 1) % len(ring)][1] - ring[(i + 1) % len(ring)][0] * ring[i][1] for i in range(len(ring)))
    outer = [(r * to_m, h * to_m) for r, h in profile]
    if area(outer) < 0:                                            # the outline counter-clockwise in (r, h), every inner loop clockwise: all faces then agree
        outer.reverse()
    inner = []
    for hole in holes:
        ring = [(r * to_m, h * to_m) for r, h in hole]
        if len(ring) < 3:
            raise ProfilerError("an inner loop needs at least 3 points")
        if area(ring) > 0:
            ring.reverse()
        inner.append(ring)
    rings = [outer] + inner
    rh = [p for ring in rings for p in ring]
    verts: list[list[float]] = []
    index: dict[tuple[int, int], int] = {}

    def vid(i: int, k: int) -> int:
        k = k % len(th) if full else k
        key = (i, k)
        if key not in index:
            r, h = rh[i]
            index[key] = len(verts)
            verts.append([at[0] * to_m + r * math.cos(th[k]), at[1] * to_m + r * math.sin(th[k]), z0 + h])
        return index[key]
    faces: list[list[int]] = []
    eps = 1e-12
    base = 0
    for ring in rings:
        m = len(ring)
        for i in range(m):
            gi, gj = base + i, base + (i + 1) % m
            for k in range(steps):
                a, b, c, d = vid(gi, k), vid(gj, k), vid(gj, k + 1), vid(gi, k + 1)
                ri, rj = rh[gi][0], rh[gj][0]
                if ri < eps and rj < eps:
                    continue
                if ri < eps:
                    faces.append([a, b, c])
                elif rj < eps:
                    faces.append([a, b, d])
                else:
                    faces += [[a, b, c], [a, c, d]]
        base += m
    if not full:
        for t in TRI.triangulate(outer, inner):
            faces.append([vid(t[0], 0), vid(t[2], 0), vid(t[1], 0)])
            faces.append([vid(t[0], len(th) - 1), vid(t[1], len(th) - 1), vid(t[2], len(th) - 1)])
    return verts, faces


def _mesh_signed_volume(mesh) -> float:
    v, f = mesh
    t = 0.0
    for a, b, c in f:
        A, B, C = v[a], v[b], v[c]
        t += A[0] * (B[1] * C[2] - B[2] * C[1]) - A[1] * (B[0] * C[2] - B[2] * C[0]) + A[2] * (B[0] * C[1] - B[1] * C[0])
    return t / 6.0


# ------------------------------------------------------------------------------------------------------ SPADRA executors
def _level(project, requested: str | None) -> str:
    if requested:
        return requested
    levels = project.manifest.get("levels") or []
    if not levels:
        raise ProfilerError("the project has no level to place the object on")
    return levels[0]["id"]


def _record(project, oid: str, payload: Mapping[str, Any], extra: Mapping[str, Any] | None = None) -> None:
    from .. import elements as E
    link = dict(payload["link"])
    link.update(extra or {})
    link["geometry_digest"] = payload["linked_geometry_digest"]
    E._attach(project, oid, {LINK_KEY: link})


def _edge_colors_for(kind: str, segments: Sequence[Mapping[str, Any]], to_m: float) -> list[dict[str, Any]]:
    """Coloured outline segments in the element's own units (metres): an extrusion outline is in the project's drawing unit, a sweep section is already metres."""
    f = to_m if kind == "extrude" else 1.0
    return [{"a": [c["a"][0] * f, c["a"][1] * f], "b": [c["b"][0] * f, c["b"][1] * f], "c": c["c"]} for c in segments]


def create(project, payload: Mapping[str, Any], to_m: float) -> dict[str, Any]:
    from .. import elements as E, forms as FX
    from ...tools.errors import BadRequest
    kind, cmd = payload["kind"], dict(payload["command"])
    cmd["level"] = _level(project, cmd.get("level"))
    out: dict[str, Any] = {"ids": [], "warnings": list(payload.get("warnings", []))}
    try:
        if kind in ("extrude", "void"):
            cmd.setdefault("depth", 1.0 if kind == "extrude" else FX.VOID_DEPTH)
            oid = FX.add_extrusion(project, cmd, to_m)
            companions = []
            for n, hole in enumerate(payload.get("holes", []), 1):
                if kind == "void":
                    out["warnings"].append("inner loops of a void are ignored (a void has no inner material)")
                    break
                solid = E.meta_of(project, oid)
                hp = {"points": hole, "void": True, "level": cmd["level"], "base": float(E.m(solid, "base")) - HOLE_OVERSHOOT, "depth": float(E.m(solid, "depth")) + 2 * HOLE_OVERSHOOT,
                      "name": "%s - hole %d" % (cmd["name"], n)}
                hid = FX.add_extrusion(project, hp, to_m)
                companions.append(hid)
                _record(project, hid, payload, {"role": "hole", "owner": oid, "hole_index": n - 1})
            _record(project, oid, payload, {"role": "solid", "companions": companions})
            out["ids"] = [oid] + companions
            out["id"] = oid
        elif kind == "sweep":
            oid = FX.add_sweep(project, cmd, to_m)
            _record(project, oid, payload, {"role": "sweep"})
            out["ids"], out["id"] = [oid], oid
        elif kind == "opening":
            from .. import elements as EL
            oid = EL.add_opening_profile(project, cmd, to_m)
            _record(project, oid, payload, {"role": "opening_profile"})
            out["ids"], out["id"] = [oid], oid
        elif kind == "revolve":
            at, base = cmd.get("at", [0.0, 0.0]), cmd.get("base", 0.0)
            mesh = revolve_mesh(cmd["profile"], cmd["angle"], cmd["segments"], at, base, to_m, cmd.get("holes", ()))
            if _mesh_signed_volume(mesh) < 0:
                mesh = (mesh[0], [[a, c, b] for a, b, c in mesh[1]])
            oid = project.model.add_box(width=1.0, depth=1.0, height=1.0, name=cmd["name"])
            E._attach(project, oid, {"category": "generic", "type": "Revolve", "level": _level(project, cmd.get("level")), "material": cmd.get("material", "Concrete"),
                                     "revolve": {"profile": cmd["profile"], "holes": cmd.get("holes", []), "angle": cmd["angle"], "segments": cmd["segments"], "at": at, "base": base}})
            E._set_mesh(project, oid, mesh)
            _record(project, oid, payload, {"role": "revolve"})
            out["ids"], out["id"] = [oid], oid
        if payload.get("color") and out.get("id"):
            E._attach(project, out["id"], {"color": E.color(payload["color"])})        # the profile's colour parameter / line colour becomes the element's colour
        if payload.get("edge_colors") and out.get("id"):
            E._attach(project, out["id"], {"edge_colors": _edge_colors_for(kind, payload["edge_colors"], to_m)})      # a coloured line colours only the faces made from it
    except BadRequest as exc:
        raise ProfilerError("SPADRA refused the profile: %s" % exc.message, details={"refused_by": "spadra"})
    return out


def update(project, oid: str, payload: Mapping[str, Any], to_m: float) -> dict[str, Any]:
    """Re-apply a profile to an existing object. If SPADRA refuses the new geometry the object is restored to what it was (and the exception still propagates so
    the surrounding command rolls everything back)."""
    from .. import elements as E, forms as FX
    meta = E.meta_of(project, oid)
    old = E.m(meta, LINK_KEY)
    if not isinstance(old, Mapping) or old.get("tool") != "profiler":
        raise ProfilerError("this object was not made from a Profiler profile")
    kind = payload["kind"]
    if old.get("kind") != kind:
        raise ProfilerError("this object was made as %s: send it again as %s, not %s" % (old.get("kind"), old.get("kind"), kind))
    cat = E.m(meta, "category")
    snapshot = dict(meta)
    cmd = payload["command"]
    try:
        if kind in ("extrude", "void"):
            profile = E._boundary(cmd["points"], to_m, kind)
            vals: dict[str, Any] = {"profile": profile}
            if cmd.get("depth") is not None:
                vals["depth"] = float(cmd["depth"])
            if "base" in cmd:
                vals["base"] = float(cmd["base"])
            E._attach(project, oid, vals)
            for c in old.get("companions", []):
                if E.alive(project, c):
                    E.remove(project, c)
            companions = []
            new_meta = E.meta_of(project, oid)
            for n, hole in enumerate(payload.get("holes", []), 1) if kind == "extrude" else []:
                hp = {"points": hole, "void": True, "level": E.m(new_meta, "level"), "base": float(E.m(new_meta, "base")) - HOLE_OVERSHOOT,
                      "depth": float(E.m(new_meta, "depth")) + 2 * HOLE_OVERSHOOT, "name": "%s - hole %d" % (E.meta_of(project, oid).get("name", cmd["name"]), n)}
                hid = FX.add_extrusion(project, hp, to_m)
                companions.append(hid)
                _record(project, hid, payload, {"role": "hole", "owner": oid, "hole_index": n - 1})
            E._attach(project, oid, {"edge_colors": _edge_colors_for("extrude", payload.get("edge_colors") or [], to_m)})
            FX.regenerate(project, oid)
            _record(project, oid, payload, {"role": "solid", "companions": companions, "overrides": old.get("overrides", {}) if payload["link"].get("overrides") is None else payload["link"]["overrides"]})
        elif kind == "sweep":
            E._attach(project, oid, {"path": FX._path(cmd["points"], to_m, bool(cmd.get("closed"))), "closed": bool(cmd.get("closed")),
                                     **({"section": FX._clean_section(cmd["section"])} if cmd.get("section") else {}),
                                     **({"edge_colors": _edge_colors_for("sweep", payload.get("edge_colors") or [], to_m)} if cmd.get("section") else {})})
            FX.regenerate_sweep(project, oid)
            _record(project, oid, payload, {"role": "sweep"})
        elif kind == "opening":
            from .. import elements as EL
            section, width, depth = EL._opening_section(cmd["section"], to_m)
            E._attach(project, oid, {"section": section, "face_width": width, "depth": depth, "depth_mode": "fixed"})
            EL.regenerate_opening_profile(project, oid)
            _record(project, oid, payload, {"role": "opening_profile"})
        elif kind == "revolve":
            prev = E.m(meta, "revolve") or {}
            at, base = cmd.get("at", prev.get("at", [0.0, 0.0])), cmd.get("base", prev.get("base", 0.0))
            mesh = revolve_mesh(cmd["profile"], cmd["angle"], cmd["segments"], at, base, to_m, cmd.get("holes", ()))
            if _mesh_signed_volume(mesh) < 0:
                mesh = (mesh[0], [[a, c, b] for a, b, c in mesh[1]])
            E._set_mesh(project, oid, mesh)
            E._attach(project, oid, {"revolve": {"profile": cmd["profile"], "holes": cmd.get("holes", []), "angle": cmd["angle"], "segments": cmd["segments"], "at": at, "base": base}})
            _record(project, oid, payload, {"role": "revolve"})
        if payload.get("color"):
            E._attach(project, oid, {"color": E.color(payload["color"])})
    except BaseException:
        try:
            project.model.attach_semantics(oid, snapshot, replace=True)
            if cat in ("extrusion", "void", "sweep"):
                FX.regenerate(project, oid)
        except BaseException:
            pass
        raise
    return {"id": oid, "ids": [oid], "warnings": list(payload.get("warnings", []))}


def source_sketch(link: Mapping[str, Any]) -> tuple[Sketch, dict[str, Any]]:
    """The profile an object is linked to: from its file (``linked``) or the copy stored in it (``embedded``). Reports whether the file moved on since the hand-off."""
    from . import sdrw_io as IO
    if link.get("tool") != "profiler":
        raise ProfilerError("this object has no Profiler link")
    mode = link.get("mode")
    info: dict[str, Any] = {"mode": mode}
    if mode == "embedded":
        sk, _ = IO.decode_sketch(link.get("embedded"))
        return sk, info
    if mode == "linked":
        path = link.get("file")
        if not path or not Path(path).is_file():
            raise ProfilerNotFound("the linked profile file is missing: %s" % path)
        S, rep = IO.open_file(path)
        info["changed"] = S.sk.digest() != link.get("digest")
        info["revision"] = S.sk.d["revision"]
        info["warnings"] = rep.get("warnings", [])
        return S.sk, info
    raise ProfilerError("a %s object has no source to re-read" % mode)


def source_session(link: Mapping[str, Any]):
    """An open-profile session for the profile an object is linked to (with its file path when linked, so a linked object stays linked)."""
    from . import sdrw_io as IO
    from .session import Session
    if link.get("tool") != "profiler":
        raise ProfilerError("this object has no Profiler link")
    if link.get("mode") == "linked":
        path = link.get("file")
        if not path or not Path(path).is_file():
            raise ProfilerNotFound("the linked profile file is missing: %s" % path)
        return IO.open_file(path)[0]
    if link.get("mode") == "embedded":
        sk, _ = IO.decode_sketch(link.get("embedded"))
        S = Session(sk)
        S.saved_digest = sk.digest()
        return S
    raise ProfilerError("this object keeps only the shape of its profile (Static): upload it again as Embedded or Linked to change the profile's parameters from here")


def resync_element(project, oid: str, *, overrides: Mapping[str, float] | None = None, options: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Rebuild an object from its profile with some parameter values changed (and remembered). Runs inside a model command, so a refusal rolls everything back."""
    from .. import elements as E
    meta = E.meta_of(project, oid)
    link = E.m(meta, LINK_KEY)
    if not isinstance(link, Mapping) or link.get("tool") != "profiler":
        raise ProfilerError("this object was not made from a Profiler profile")
    S = source_session(link)
    merged = {**(link.get("overrides") or {}), **(overrides or {})}
    opts = {**(link.get("options") or {}), **(options or {}), "mode": link.get("mode", "embedded"), "overrides": merged, "project_unit": project.manifest["unit"]}
    payload = prepare(S, link["kind"], opts)
    return update(project, oid, payload, U.METERS_PER_UNIT[project.manifest["unit"]])
