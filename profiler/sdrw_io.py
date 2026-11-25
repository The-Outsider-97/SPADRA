"""``.sdrw`` persistence for Profiler: atomic save, versioned schema, corruption detection, autosave / crash recovery, import / export.

The *authoritative* data is the sketch (vertices, edges, constraints, dimensions, parameters, comments ...) stored compressed in document metadata under
``profiler:sketch`` with a SHA-256 over its canonical JSON. The ordinary SDRW entities written next to it are a *derived view* so the file also opens in any SDRW
viewer; on open they are compared with what the sketch regenerates and a difference (the file was edited elsewhere) is reported, never silently merged.
All file writes go through SDRW's own atomic, verify-before-publish save (a failed save leaves the previous file untouched).
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import time
import zlib
from pathlib import Path
from typing import Any, Sequence

from src.utils.spatial_formats.common.errors import ConflictError, SpatialFormatError
from src.utils.spatial_formats.sdrw.document import DrawingDocument

from . import annotate as AN
from . import geom as G
from . import ops, regions
from .errors import ProfilerConflict, ProfilerError, ProfilerNotFound
from .model import SCHEMA, Sketch
from .session import Session

SCHEMA_VERSION = 1                  # bump when the stored sketch layout changes; ``MIGRATIONS`` upgrades older files
KEY_SKETCH = "profiler:sketch"
KEY_INFO = "profiler:info"
CHUNK = 400_000                     # characters per metadata string (the container limit is 1,000,000)
MAX_JSON = 15 * 1024 * 1024
MIGRATIONS: dict[int, Any] = {}     # from_version -> fn(sketch_dict) -> sketch_dict (none needed yet: v1 is the first schema)
AUTOSAVE_EXT = ".autosave.sdrw"


# --------------------------------------------------------------------------------------------------------- encode / decode
def canonical(d: dict) -> bytes:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def encode_sketch(sk: Sketch) -> dict[str, Any]:
    raw = canonical(sk.to_dict())
    if len(raw) > MAX_JSON:
        raise ProfilerError("the profile is too large to save (%d MB; limit %d MB)" % (len(raw) // 2**20, MAX_JSON // 2**20))
    text = base64.b64encode(zlib.compress(raw, 6)).decode("ascii")
    return {"encoding": "zlib+base64", "schema": SCHEMA, "schema_version": SCHEMA_VERSION, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            "chunks": [text[i:i + CHUNK] for i in range(0, len(text), CHUNK)]}


def decode_sketch(blob: Any) -> tuple[Sketch, list[str]]:
    """Returns the sketch and a list of notes (migrations applied). Raises ``ProfilerError`` naming the damage if the stored data fails any check."""
    if not isinstance(blob, dict) or blob.get("encoding") != "zlib+base64" or not isinstance(blob.get("chunks"), list):
        raise ProfilerError("the Profiler data in this file is damaged or not in a known encoding")
    ver = blob.get("schema_version")
    if not isinstance(ver, int):
        raise ProfilerError("the Profiler data has no schema version")
    if ver > SCHEMA_VERSION:
        raise ProfilerError("this file was written by a newer Profiler (data version %d; this one reads up to %d): update Profiler to open it" % (ver, SCHEMA_VERSION))
    try:
        raw = zlib.decompress(base64.b64decode("".join(blob["chunks"]).encode("ascii"), validate=True), 0, MAX_JSON + 1)
    except Exception as exc:
        raise ProfilerError("the Profiler data in this file is corrupt (%s)" % type(exc).__name__)
    if len(raw) > MAX_JSON:
        raise ProfilerError("the Profiler data is larger than the %d MB limit" % (MAX_JSON // 2**20))
    if hashlib.sha256(raw).hexdigest() != blob.get("sha256") or len(raw) != blob.get("bytes"):
        raise ProfilerError("the Profiler data in this file does not match its checksum: the file was modified or damaged")
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise ProfilerError("the Profiler data is not valid JSON")
    notes = []
    while ver < SCHEMA_VERSION:
        fn = MIGRATIONS.get(ver)
        if fn is None:
            raise ProfilerError("no upgrade path from Profiler data version %d" % ver)
        data = fn(data)
        ver += 1
        notes.append("upgraded stored data to version %d" % ver)
    return Sketch.from_dict(data), notes


# ------------------------------------------------------------------------------------------------------- derived entities
def _stroke(sk: Sketch, e: dict) -> str:
    return AN.effective_color(sk, e)


def curve_tolerance(sk: Sketch) -> float:
    """The largest distance (document units) a polyline may stray from a spline when an *imported* spline cannot be held exactly (see ``_import_spline``)."""
    return max(sk.tol * 100, 0.001)


def _beziers(sk: Sketch, e: dict):
    pts = [sk.pos(v) for v in e["v"]]
    hs = [(sk.pos(a), sk.pos(b)) for a, b in e["handles"]] if e["mode"] == "max" else None
    return G.spline_beziers(e["mode"], pts, degree=e.get("degree", 3), handles=hs)


def _add_spline(doc: DrawingDocument, sk: Sketch, e: dict, opts: dict) -> None:
    """A Profiler spline as one native SDRW ``spline`` entity (feature ``sdrw-curves-v1``). ``math`` is the clamped B-spline over its control polygon and is written
    as that B-spline, whatever its degree. ``max`` (Bezier chain with handles) and ``revit`` (Hermite through the fit points) are exactly piecewise cubic, so they
    are written as the equivalent cubic Bezier chain. Nothing is sampled."""
    if e["mode"] == "math":
        pts = [sk.pos(v) for v in e["v"]]
        doc.add_spline(pts, degree=min(int(e.get("degree", 3)), len(pts) - 1), **opts)
    else:
        doc.add_bezier_spline(_beziers(sk, e), **opts)


def approximated_edges(sk: Sketch, fmt: str = "sdrw") -> list[dict[str, Any]]:
    """Edges a format can only *draw* approximately although it still carries the exact curve. ``.sdrw`` and DXF hold every Profiler edge exactly. SVG has no
    primitive for a B-spline of degree above 3, so it draws a polyline that is guaranteed to stay within ``max_deviation`` (document units) of the curve; the exact
    spline stays in the element's ``data-*`` attributes and re-imports exactly."""
    if fmt != "svg":
        return []
    from src.utils.spatial_formats.sdrw import curves as C
    out = []
    for eid, e in sk.edges.items():
        if e["type"] == "spline" and e["mode"] == "math" and min(int(e.get("degree", 3)), len(e["v"]) - 1) > 3:
            pts = [sk.pos(v) for v in e["v"]]
            p = min(int(e.get("degree", 3)), len(pts) - 1)
            g = {"degree": p, "control_points": [list(q) for q in pts], "knots": C.clamped_uniform_knots(len(pts), p)}
            out.append({"edge": eid, "type": "spline", "max_deviation": C.svg_spline_tolerance(g), "unit": sk.d["unit"],
                        "reason": "SVG has no primitive for a B-spline of degree above 3: drawn as a polyline within max_deviation; the exact spline is kept in the element's data attributes and re-imported exactly"})
    return out


def to_document(sk: Sketch) -> DrawingDocument:
    """The derived SDRW view of a sketch (also what the SVG/DXF exports use). Every edge type is a native SDRW entity: line, circle, arc, ``ellipse`` and
    ``spline`` (the last two need SDRW's ``sdrw-curves-v1`` feature, which the file then declares). Nothing is approximated. The embedded sketch stays the
    authoritative copy."""
    layers = [{"name": l["name"], "visible": bool(l["visible"]), **({"stroke": l["color"]} if l.get("color") else {})} for l in sk.d["layers"]]
    names = {l["name"] for l in layers}
    for extra in ("annotation",):
        if extra not in names:
            layers.append({"name": extra, "visible": True})
            names.add(extra)
    doc = DrawingDocument.new(sk.d["name"], unit=sk.d["unit"], layers=layers, document_id=sk.d["id"])
    for eid, e in sk.edges.items():
        opts = {"layer": e["layer"], "entity_id": eid, "metadata": {"profiler:edge": eid, "profiler:construction": bool(e.get("construction"))},
                "style": {"stroke": _stroke(sk, e)}}
        if e.get("construction"):
            opts["style"]["stroke_width"] = 0.0
        try:
            t = e["type"]
            if t == "line":
                a, b = [sk.pos(v) for v in e["v"]]
                if G.dist(a, b) > 0:
                    doc.add_line(a, b, **opts)
            elif t == "circle":
                c, r = sk.center_radius(eid)
                doc.add_circle(c, r, **opts)
            elif t == "arc":
                c, r, a0, sw = sk.arc_params(eid)
                doc.add_arc(c, r, a0, a0 + sw, **opts)
            elif t == "ellipse":
                doc.add_ellipse(sk.pos(e["c"]), sk.d["scalars"][e["rx"]], sk.d["scalars"][e["ry"]], rotation=e.get("rot", 0.0), **opts)
            elif t == "spline":
                _add_spline(doc, sk, e, opts)
        except Exception:                                           # a degenerate edge is reported by Check Profile; it must not block saving
            continue
    for tid, t in sk.d["texts"].items():
        try:
            doc.add_text(t["xy"], t["text"], t["height"], rotation=t.get("rotation", 0.0), degrees=True, layer=t["layer"] if t["layer"] in names else "annotation",
                         entity_id=tid, metadata={"profiler:text": tid}, **({"style": {"stroke": t["color"]}} if t.get("color") else {}))
        except Exception:
            continue
    return doc


def build_document(sk: Sketch, *, info: dict | None = None) -> DrawingDocument:
    doc = to_document(sk)
    meta = {KEY_SKETCH: encode_sketch(sk), KEY_INFO: {"app": "SPADRA Profiler", "schema": SCHEMA, "schema_version": SCHEMA_VERSION, "unit": sk.d["unit"],
                                                       "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "digest": sk.digest(), **(info or {})}}
    doc.set_metadata(meta)
    return doc


# ------------------------------------------------------------------------------------------------------------- save / open
def _check_path(path: str | os.PathLike, *, must_exist: bool = False) -> Path:
    p = Path(path)
    if not str(path).strip():
        raise ProfilerError("choose a file name")
    if p.suffix.lower() != ".sdrw":
        raise ProfilerError("Profiler files use the .sdrw extension")
    if p.exists() and p.is_dir():
        raise ProfilerError("%s is a folder" % p)
    if must_exist and not p.is_file():
        raise ProfilerNotFound("file not found: %s" % p)
    if not p.parent.exists():
        raise ProfilerError("the folder %s does not exist" % p.parent)
    return p


def save(S: Session, path: str | None = None, *, overwrite: bool | None = None, force: bool = False) -> dict[str, Any]:
    """Save / Save As. Atomic and verified: the complete package is rebuilt and re-read before it replaces anything. Saving over a file that changed on disk
    since it was opened/saved raises a conflict (``force`` overrides) instead of silently losing the other writer's work."""
    with S.lock:
        target = _check_path(path or S.path or "")
        if path is None and S.path is None:
            raise ProfilerError("this profile has not been saved yet: choose a file name (Save As)")
        same = S.path is not None and Path(S.path).absolute() == target.absolute()
        if overwrite is None:
            overwrite = same
        if target.exists() and not overwrite:
            raise ProfilerConflict("%s already exists: choose another name or confirm replacing it" % target.name, details={"exists": True, "path": str(target)})
        doc = build_document(S.sk)
        kw: dict[str, Any] = {"overwrite": bool(overwrite), "force": force}
        if same and S.meta.get("source_sha256") and not force:
            kw["expected_sha256"] = S.meta["source_sha256"]
        if target.exists() and not same:
            kw["force"] = True                                       # an explicit overwrite of a different file: the person confirmed it
        try:
            res = doc.save(target, **kw)
        except ConflictError as exc:
            raise ProfilerConflict("the file changed on disk since you opened it (%s). Save As a new name, or reload it." % (exc.args[0] if exc.args else "conflict"),
                                   details={"path": str(target)})
        except SpatialFormatError as exc:
            raise ProfilerError("the file could not be saved: %s" % (exc.args[0] if exc.args else exc))
        except OSError as exc:
            raise ProfilerError("the file could not be written: %s" % exc.strerror)
        S.path = str(res.path)
        S.meta["source_sha256"] = res.sha256
        S.mark_saved()
        discard_autosave(S)
        return {"path": S.path, "bytes": res.size, "sha256": res.sha256, "replaced": res.replaced, "status": S.status()}


def open_file(path: str, *, user: str = "user") -> tuple[Session, dict[str, Any]]:
    """Open a ``.sdrw``. Profiler files restore the exact sketch; plain SDRW drawings are imported (see :func:`import_drawing`)."""
    p = _check_path(path, must_exist=True)
    try:
        doc = DrawingDocument.open(p, mode="read")
    except SpatialFormatError as exc:
        raise ProfilerError("%s is not a readable SDRW file: %s" % (p.name, exc.args[0] if exc.args else exc))
    meta = doc.metadata
    report: dict[str, Any] = {"path": str(p), "kind": "profiler", "warnings": [], "notes": []}
    if KEY_SKETCH in meta:
        sk, notes = decode_sketch(meta[KEY_SKETCH])
        report["notes"] += notes
        report["warnings"] += external_edit_warnings(sk, doc)
        sk.d["id"] = doc.document_id if sk.d.get("id") != doc.document_id else sk.d["id"]
    else:
        sk, rep = import_drawing(doc)
        report["kind"] = "imported"
        report.update(rep)
    S = Session(sk, path=str(p), user=user)
    S.meta["source_sha256"] = doc.source_sha256
    S.saved_digest = sk.digest()
    if report["kind"] == "imported":
        S.dirty = True                                              # an import is not yet a Profiler file
        S.saved_digest = None
    S.meta["opened_revision"] = doc.revision
    return S, report


def external_edit_warnings(sk: Sketch, doc: DrawingDocument) -> list[str]:
    """Compare the file's derived entities with what the stored sketch regenerates. The sketch stays authoritative; a mismatch means another program edited the file."""
    out: list[str] = []
    fresh = to_document(sk)
    have, want = {e["id"]: e for e in doc.model.entities if "profiler:edge" in (e.get("metadata") or {})}, {e["id"]: e for e in fresh.model.entities if "profiler:edge" in (e.get("metadata") or {})}
    gone, added = sorted(set(want) - set(have)), sorted(set(have) - set(want))
    if gone:
        out.append("%d drawing entit%s in the file were removed outside Profiler; Profiler's own data was kept." % (len(gone), "y" if len(gone) == 1 else "ies"))
    if added:
        out.append("%d entit%s in the file were added outside Profiler and are ignored (use Import to bring them in)." % (len(added), "y" if len(added) == 1 else "ies"))
    moved = [i for i in set(have) & set(want) if have[i]["geometry"] != want[i]["geometry"] and _differs(have[i]["geometry"], want[i]["geometry"])]
    if moved:
        out.append("%d entit%s changed outside Profiler; Profiler's own geometry was kept." % (len(moved), "y was" if len(moved) == 1 else "ies were"))
    return out


def _differs(a: Any, b: Any, tol: float = 1e-6) -> bool:
    if isinstance(a, dict):
        return set(a) != set(b) or any(_differs(a[k], b[k], tol) for k in a)
    if isinstance(a, (list, tuple)):
        return len(a) != len(b) or any(_differs(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) > tol * max(1.0, abs(a))
    return a != b


# ------------------------------------------------------------------------------------------------------------ import
def import_drawing(doc: DrawingDocument) -> tuple[Sketch, dict[str, Any]]:
    """A plain SDRW drawing (or DXF/SVG already converted to one) becomes a sketch: lines, polylines, circles, arcs, text. Insert/hatch/dimension entities that
    Profiler cannot represent faithfully are listed in ``skipped`` - nothing is dropped silently."""
    sk = Sketch(doc.name or "Imported", doc.unit)
    skipped: dict[str, int] = {}
    approximated: list[str] = []
    n = 0
    for lay in doc.model.layers:
        if lay["name"] != "0" and lay["name"] not in sk.layer_names():
            AN.add_layer(sk, lay["name"], color=(lay.get("stroke") if isinstance(lay.get("stroke"), str) and AN.HEX.match(lay["stroke"]) else None))
            sk.layer(lay["name"])["visible"] = lay.get("visible", True)
    for ent in doc.model.entities:
        k, g = ent["kind"], ent["geometry"]
        kw: dict[str, Any] = {"layer": ent.get("layer", "0")}
        col = (ent.get("style") or {}).get("stroke")
        if isinstance(col, str) and AN.HEX.match(col):
            kw["color"] = col.upper() if col.upper() != sk.d["default_color"] else None
        try:
            if k == "line":
                sk.add_line(g["start"], g["end"], **kw)
            elif k == "polyline":
                sk.add_polyline(g["points"], closed=bool(g.get("closed")), **kw)
            elif k == "circle":
                sk.add_circle(g["center"], g["radius"], **kw)
            elif k == "arc":
                c, r, a0, a1 = g["center"], g["radius"], g["start_angle"], g["end_angle"]
                if a1 - a0 >= 2 * math.pi - 1e-9:
                    sk.add_circle(c, r, **kw)
                else:
                    sk.add_arc_center_ends(c, G.polar(tuple(c), r, a0), G.polar(tuple(c), r, a1), ccw=True, **kw)
            elif k == "ellipse":
                sk.add_ellipse(g["center"], g["rx"], g["ry"], g.get("rotation", 0.0), **kw)
            elif k == "spline":
                note = _import_spline(sk, g, kw)
                if note:
                    approximated.append(note)
            elif k == "text":
                AN.add_text(sk, g["position"], g["text"], height=g["height"], rotation=math.degrees(g.get("rotation", 0.0)), layer=kw["layer"])
            elif k == "point":
                sk.add_vertex(g["position"], reuse=True)
                skipped["point"] = skipped.get("point", 0) + 1
                continue
            else:
                skipped[k] = skipped.get(k, 0) + 1
                continue
            n += 1
        except ProfilerError:
            skipped[k + " (invalid)"] = skipped.get(k + " (invalid)", 0) + 1
    from . import topo
    topo.weld(sk)
    warnings = ["%d %s entit%s could not be imported" % (c, k, "y" if c == 1 else "ies") for k, c in skipped.items()] + approximated
    return sk, {"imported": n, "skipped": skipped, "approximated": approximated, "warnings": warnings}


def _is_uniform(knots: Sequence[float], n: int, p: int) -> bool:
    """Clamped with evenly spaced interior knots (the Profiler ``math`` spline), whatever the parameter range."""
    lo, hi = knots[0], knots[-1]
    inner = knots[p + 1: n]
    return all(abs(k - (lo + (hi - lo) * (i + 1) / (n - p))) <= 1e-9 * max(1.0, abs(hi - lo)) for i, k in enumerate(inner))


def _import_spline(sk: Sketch, g: dict, kw: dict) -> str | None:
    """Bring an SDRW spline in as an exact Profiler spline where one exists, else as a polyline within ``curve_tolerance`` (the returned note says so).

    * clamped, evenly spaced knots -> ``math`` spline of that degree (the very same curve);
    * any other spline of degree up to 3 -> ``max`` spline: its exact cubic Bezier form, with the Bezier handles as handles. The shape is identical; only the way
      it is edited changes;
    * degree above 3 with other knots has no Profiler equivalent: a polyline."""
    from src.utils.spatial_formats.sdrw import curves as C
    pts, p, knots = [tuple(q) for q in g["control_points"]], int(g["degree"]), list(g["knots"])
    if _is_uniform(knots, len(pts), p):
        sk.add_spline("math", pts, degree=p, **kw)
        return None
    segs = C.cubic_segments(g)
    if segs is not None:
        sk.add_spline("max", [segs[0][0]] + [sg[3] for sg in segs], handles=[(sg[1], sg[2]) for sg in segs], **kw)
        return None
    tol = curve_tolerance(sk)
    sk.add_polyline(C.flatten_spline(g, tol), closed=False, **kw)
    return "a degree-%d spline with non-uniform knots has no Profiler equivalent: imported as a polyline within %g %s" % (p, tol, sk.d["unit"])


def import_file(path: str, *, unit: str | None = None) -> tuple[Sketch, dict[str, Any]]:
    """Import ``.sdrw`` (Profiler or plain), ``.dxf`` or ``.svg`` into a new sketch."""
    p = Path(path)
    ext = p.suffix.lower()
    if not p.is_file():
        raise ProfilerNotFound("file not found: %s" % p)
    try:
        if ext == ".sdrw":
            S, rep = open_file(str(p))
            return S.sk, rep
        if ext == ".dxf":
            doc, conv = DrawingDocument.import_dxf(str(p), unit=unit, skip_unsupported=True)
        elif ext == ".svg":
            doc, conv = DrawingDocument.import_svg(str(p), unit=unit or "mm")
        else:
            raise ProfilerError("import supports .sdrw, .dxf and .svg files")
    except SpatialFormatError as exc:
        raise ProfilerError("%s could not be imported: %s" % (p.name, exc.args[0] if exc.args else exc))
    sk, rep = import_drawing(doc)
    rep["source"] = ext[1:]
    rep["conversion_notes"] = [str(x) for x in getattr(conv, "dropped", [])][:50]
    return sk, rep


def sketch_to_block(other: Sketch) -> dict:
    """A block-shaped snapshot of a whole sketch (for inserting an imported / opened profile into the current one)."""
    b = other.bounds() or (0.0, 0.0, 0.0, 0.0)
    base = (b[0], b[1])
    vs = {v: {"x": p["x"] - base[0], "y": p["y"] - base[1]} for v, p in other.d["vertices"].items()}
    scalars = dict(other.d["scalars"])
    edges = {e: {k: (list(v) if isinstance(v, list) else v) for k, v in ed.items()} for e, ed in other.d["edges"].items()}
    cons = [{k: (list(v) if isinstance(v, list) else v) for k, v in c.items()} for c in other.d["constraints"] if c.get("state") != "unresolved"]
    return {"name": other.d["name"], "vertices": vs, "edges": edges, "scalars": scalars, "constraints": cons, "instances": []}


def insert_sketch(sk: Sketch, other: Sketch, at: Sequence[float] = (0.0, 0.0)) -> dict:
    if other.unit != sk.unit:
        other = other.clone()
        AN.set_unit(other, sk.unit, convert=True)
    return AN.place_block(sk, sketch_to_block(other), other.d["name"], at, group=False, record=False)


# ------------------------------------------------------------------------------------------------------------- autosave
def _autosave_path(S: Session, folder: str | os.PathLike) -> Path:
    return Path(folder) / (S.sk.d["id"] + AUTOSAVE_EXT)


def autosave(S: Session, folder: str | os.PathLike) -> dict[str, Any] | None:
    """Write a recovery copy when there are unsaved changes (cheap no-op otherwise). Never touches the person's own file."""
    with S.lock:
        if not S.dirty:
            return None
        dg = S.sk.digest()
        if S.autosave_digest == dg:
            return None
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        target = _autosave_path(S, folder)
        doc = build_document(S.sk, info={"autosave": True, "origin_path": S.path, "origin_name": S.sk.d["name"]})
        res = doc.save(target, overwrite=True, force=True)
        S.autosave_digest = dg
        return {"path": str(target), "bytes": res.size, "revision": S.sk.d["revision"]}


def discard_autosave(S: Session, folder: str | os.PathLike | None = None) -> None:
    folder = folder or S.meta.get("autosave_dir")
    if not folder:
        return
    try:
        _autosave_path(S, folder).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass
    S.autosave_digest = None


def recoverable(folder: str | os.PathLike) -> list[dict[str, Any]]:
    """Autosave files left behind by a crash or a close without saving, newest first, each with what it was and whether it is newer than the original file."""
    out = []
    f = Path(folder)
    if not f.is_dir():
        return out
    for p in f.glob("*" + AUTOSAVE_EXT):
        try:
            doc = DrawingDocument.open(p, mode="read")
            info = doc.metadata.get(KEY_INFO) or {}
            origin = info.get("origin_path")
            out.append({"path": str(p), "name": info.get("origin_name") or doc.name, "origin_path": origin, "saved_at": info.get("saved_at"), "mtime": p.stat().st_mtime,
                        "newer_than_original": bool(origin and Path(origin).exists() and p.stat().st_mtime > Path(origin).stat().st_mtime) or not origin or not Path(origin).exists()})
        except Exception as exc:
            out.append({"path": str(p), "damaged": True, "reason": str(exc)[:200], "mtime": p.stat().st_mtime})
    out.sort(key=lambda r: -r["mtime"])
    return out


def recover(path: str, *, user: str = "user") -> tuple[Session, dict[str, Any]]:
    """Open an autosave as an unsaved session bound to the original file name (so Save writes the person's file, after the usual conflict check)."""
    S, rep = open_file(path, user=user)
    doc = DrawingDocument.open(path, mode="read")
    info = doc.metadata.get(KEY_INFO) or {}
    S.path = info.get("origin_path") if info.get("origin_path") and Path(info["origin_path"]).parent.exists() else None
    S.meta["source_sha256"] = None
    S.meta["recovered_from"] = str(path)
    S.dirty, S.saved_digest = True, None
    rep["recovered"] = True
    return S, rep


# -------------------------------------------------------------------------------------------------------- export / reports
def save_copy(S: Session, path: str, *, overwrite: bool = False) -> dict[str, Any]:
    """Export project: a complete, self-contained copy (does not rebind the session to the new file)."""
    with S.lock:
        p = _check_path(path)
        if p.exists() and not overwrite:
            raise ProfilerConflict("%s already exists" % p.name, details={"exists": True, "path": str(p)})
        res = build_document(S.sk, info={"exported_copy": True}).save(p, overwrite=True, force=True)
        return {"path": str(p), "bytes": res.size, "sha256": res.sha256}


def export_drawing(S: Session, path: str, fmt: str, *, overwrite: bool = False) -> dict[str, Any]:
    """Export the derived drawing as ``dxf`` or ``svg`` through the shared SDRW exporters. Ellipses and splines are written as true curves in both (DXF ``ELLIPSE`` /
    ``SPLINE``, SVG ``<ellipse>`` / Bezier ``<path>``). ``approximated`` lists edges a format can only draw approximately (SVG, B-spline degree above 3); anything a
    format cannot hold is listed in ``dropped``."""
    with S.lock:
        p = Path(path)
        if not p.parent.exists():
            raise ProfilerError("the folder %s does not exist" % p.parent)
        if p.exists() and not overwrite:
            raise ProfilerConflict("%s already exists" % p.name, details={"exists": True, "path": str(p)})
        doc = to_document(S.sk)
        try:
            if fmt == "dxf":
                rep = doc.export_dxf(p, overwrite=True)
            elif fmt == "svg":
                rep = doc.export_svg(p, overwrite=True)
            else:
                raise ProfilerError("export format is dxf or svg")
        except SpatialFormatError as exc:
            raise ProfilerError("export failed: %s" % (exc.args[0] if exc.args else exc))
        return {"path": str(p), "format": fmt, "dropped": [str(x) for x in getattr(rep, "dropped", [])][:50], "approximated": approximated_edges(S.sk, fmt), "bytes": p.stat().st_size}


def report_data(S: Session, purpose: str = "drafting") -> dict[str, Any]:
    from . import dims, params as PM, validate
    sk = S.sk
    chk = validate.check(sk, purpose)
    ana = regions.analyse(sk)
    by_type: dict[str, int] = {}
    for e in sk.edges.values():
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1
    dim_rows = []
    for did, d in sk.d["dimensions"].items():
        try:
            m = dims.measure(sk, d)
        except Exception:
            m = {"value": None}
        dim_rows.append({"id": did[:8], "type": d["type"], "mode": d["mode"], "value": m.get("value"), "parameter": d.get("param"), "state": d.get("state", "ok")})
    diag = sk.diagnostics()
    return {"name": sk.d["name"], "unit": sk.d["unit"], "revision": sk.d["revision"], "file": S.path, "generated": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "bounds": sk.bounds(), "edge_counts": by_type, "layers": [{"name": l["name"], "visible": l["visible"], "locked": l["locked"],
                                                                         "edges": sum(1 for e in sk.edges.values() if e["layer"] == l["name"])} for l in sk.d["layers"]],
            "regions": [{"area": r["area"], "perimeter": r["perimeter"], "holes": len(r["holes"])} for r in ana["regions"]],
            "total_area": sum(r["area"] for r in ana["regions"]), "parameters": PM.listing(sk), "dimensions": dim_rows,
            "constraints": {"count": len(sk.d["constraints"]), "unresolved": sum(1 for c in sk.d["constraints"] if c.get("state") == "unresolved")},
            "solver": {"status": diag["status"], "dof": diag.get("dof")}, "check": {"purpose": purpose, "status": chk["status"], "counts": chk["counts"], "issues": chk["issues"]},
            "comments": [{"text": c["text"], "author": c["author"], "resolved": c["resolved"], "replies": len(c["replies"])} for c in sk.d["comments"].values()]}


def export_report(S: Session, path: str, fmt: str = "html", *, purpose: str = "drafting", overwrite: bool = False) -> dict[str, Any]:
    import html as _h
    with S.lock:
        p = Path(path)
        if not p.parent.exists():
            raise ProfilerError("the folder %s does not exist" % p.parent)
        if p.exists() and not overwrite:
            raise ProfilerConflict("%s already exists" % p.name, details={"exists": True, "path": str(p)})
        r = report_data(S, purpose)
        if fmt == "json":
            text = json.dumps(r, indent=2, default=str)
        elif fmt in ("md", "html"):
            u = r["unit"]
            lines = ["# Profile report: %s" % r["name"], "", "Generated %s - unit %s - revision %d" % (r["generated"], u, r["revision"]), "",
                     "## Geometry", "", "Edges: " + (", ".join("%d %s" % (n, t) for t, n in sorted(r["edge_counts"].items())) or "none"),
                     "Regions: %d, total area %.6g %s²" % (len(r["regions"]), r["total_area"], u), ""]
            for i, g in enumerate(r["regions"], 1):
                lines.append("- Region %d: area %.6g %s², perimeter %.6g %s, %d hole(s)" % (i, g["area"], u, g["perimeter"], u, g["holes"]))
            lines += ["", "## Parameters", ""] + (["| Name | Type | Value | Formula |", "|---|---|---|---|"] + ["| %s | %s | %s | %s |" % (x.get("name"), x.get("type"), x.get("value"), x.get("expression") or "") for x in r["parameters"]] if r["parameters"] else ["None."])
            lines += ["", "## Dimensions", ""] + (["| Type | Mode | Value | Parameter |", "|---|---|---|---|"] + ["| %s | %s | %s | %s |" % (x["type"], x["mode"], "%.6g" % x["value"] if isinstance(x["value"], (int, float)) else "-", x["parameter"] or "") for x in r["dimensions"]] if r["dimensions"] else ["None."])
            lines += ["", "## Constraints", "", "%d constraint(s), %d unresolved. Solver: %s, degrees of freedom: %s." % (r["constraints"]["count"], r["constraints"]["unresolved"], r["solver"]["status"], r["solver"]["dof"]),
                      "", "## Check Profile (%s): %s" % (r["check"]["purpose"], r["check"]["status"].upper()), ""]
            lines += ["- [%s] %s" % (i["severity"], i["message"]) for i in r["check"]["issues"]] or ["No issues."]
            if r["comments"]:
                lines += ["", "## Comments", ""] + ["- %s%s - %s" % ("(resolved) " if c["resolved"] else "", c["text"], c["author"]) for c in r["comments"]]
            text = "\n".join(lines) + "\n"
            if fmt == "html":
                body = "".join("<p>%s</p>" % _h.escape(l) if l and not l.startswith("#") and not l.startswith("|") and not l.startswith("- ") else "" for l in lines)
                text = ("<!doctype html><meta charset=utf-8><title>%s</title><style>body{font:14px/1.5 system-ui,sans-serif;max-width:50rem;margin:2rem auto;padding:0 1rem}"
                        "pre{white-space:pre-wrap}</style><pre>%s</pre>" % (_h.escape(r["name"]), _h.escape("\n".join(lines))))
        else:
            raise ProfilerError("report format is html, md or json")
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, p)
        return {"path": str(p), "format": fmt, "bytes": p.stat().st_size, "check": r["check"]["status"]}
