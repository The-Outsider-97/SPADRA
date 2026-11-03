"""Import and export through SLAI's own spatial-format adapters. SPADRA parses no foreign format itself: SGEO, SDRW, GLB, STL, OBJ, IFC, SVG, DXF
and DWG are read and written by ``src/utils/spatial_formats`` and every conversion comes back as one **conversion report** that says what was
converted, lost, approximated and unsupported, and what could not be done.

What a format can do is decided by what is installed: the adapters raise a dependency error when a library (ezdxf for DXF, IfcOpenShell for IFC,
an ODA converter for DWG, Open CASCADE for STEP) is missing, and ``availability`` reports exactly that, with the adapter's own limitations. Nothing
is simulated when one is absent.

A file is **inspected before it is imported** (``inspect_file``): its real type is read from its content, it is parsed by the same adapter that
would import it, and what it holds (objects, geometry, size, declared unit, version) and anything suspicious about it is reported without touching
the project. Importing runs the same checks again, so the answer of the inspection and the result of the import cannot differ.

Native files are not "converted": an SGEO file brings in objects with their identity, parameters, hierarchy, families and metadata (levels are
matched by name), and an SDRW file brings in entities with their layers, line types and blocks."""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import tempfile
import threading
import zipfile
from collections import OrderedDict
from pathlib import Path
from typing import Any, Mapping

from ..tools.errors import AppError, BadRequest, DependencyMissing, LimitExceeded
from src.utils.sdrw import MIMETYPE as SDRW_MIME
from src.utils.sdrw import SDRWDocument
from src.utils.sgeo import MIMETYPE as SGEO_MIME
from src.utils.sgeo import SGEODocument
from src.utils.sldc import MIMETYPE as SLDC_MIME
from src.utils.spatial_formats.adapters import capabilities
from src.utils.spatial_formats.common import new_id
from src.utils.spatial_formats.common.errors import DependencyUnavailableError, SpatialFormatError

from . import elements as E

MODEL_IMPORT = ("sgeo", "glb", "stl", "obj", "ifc")
MODEL_EXPORT = ("sgeo", "glb", "stl", "obj")
DRAWING_IMPORT = ("sdrw", "svg", "dxf")
DRAWING_EXPORT = ("sdrw", "svg", "dxf")
MAX_IMPORT_BYTES = 64 * 1024 * 1024
MAX_IMPORT_OBJECTS = 20_000
MAX_MODEL_OBJECTS = 100_000
MAX_IMPORT_ENTITIES = 200_000
SANE_EXTENT_M = (1e-4, 1e5)                       # a model whose overall size is outside 0.1 mm .. 100 km is probably in the wrong unit
SCENE_KEY = "spadra:project"                      # scene metadata a SPADRA export carries: the levels its elements stand on
UNIT_M = {"mm": 0.001, "cm": 0.01, "m": 1.0, "in": 0.0254, "ft": 0.3048}
MIME_OF = {"sgeo": SGEO_MIME, "sdrw": SDRW_MIME, "sldc": SLDC_MIME}
EXTENSION = {"sgeo": "sgeo", "sdrw": "sdrw", "glb": "glb", "stl": "stl", "obj": "obj", "ifc": "ifc", "svg": "svg", "dxf": "dxf", "dwg": "dwg"}
MEDIA = {"sgeo": "application/octet-stream", "sdrw": "application/octet-stream", "glb": "model/gltf-binary", "stl": "model/stl", "obj": "text/plain; charset=utf-8",
         "svg": "image/svg+xml", "dxf": "application/dxf"}


# ----------------------------------------------------------------------------------------------------------------------- availability
def availability() -> dict[str, Any]:
    """Which conversions work in this installation, and why not where they do not. Limits are the adapters' own."""
    caps = capabilities()

    def entry(fmt: str, **extra: Any) -> dict[str, Any]:
        c = caps.get(fmt)
        out: dict[str, Any] = {"available": bool(c["available"]) if c else True}
        if c and not c["available"]:
            out["reason"] = f"{c['backend']} is not installed" if fmt != "dwg" else "DWG needs a separately licensed ODA converter; none is configured"
        if c and c.get("limitations"):
            out["limits"] = list(c["limitations"])
        out.update(extra)
        return out

    native = {"available": True, "native": True}
    return {
        "importModel": {"sgeo": native, "glb": entry("glb"), "stl": entry("stl"), "obj": entry("obj"), "ifc": entry("ifc")},
        "exportModel": {"sgeo": native, "glb": entry("glb"), "stl": entry("stl"), "obj": entry("obj")},
        "importDrawing": {"sdrw": native, "svg": entry("svg", note="plain SVG as SLAI writes it (lines, circles, polylines, polygons, arcs); anything else is refused"), "dxf": entry("dxf")},
        "exportDrawing": {"sdrw": native, "svg": entry("svg"), "dxf": entry("dxf"), "dwg": entry("dwg")},
        "investigated": {
            "ifcExport": {"available": False, "reason": "The IFC adapter reads IFC only; it has no IFC writer"},
            "step": {"available": False, "reason": ("STEP needs Open CASCADE (OCP), which is not installed" if not caps["step"]["available"] else
                                                   "The STEP adapter retains source files and writes one box; it does not convert a building model")},
            "gltf": {"available": False, "reason": "Only the single-file binary form (GLB) is read and written; separate .gltf files with external buffers are not"},
            "pdf": {"available": False, "reason": "No PDF writer is installed on the server; a sheet is printed to PDF from the browser's print dialog"},
        },
        "report": {"sldc": native},
    }


def report_dict(report: Any) -> dict[str, Any]:
    """A library conversion report as plain data."""
    data = report.as_dict() if hasattr(report, "as_dict") else {"text": str(report)}
    return {key: data.get(key) for key in ("adapter", "accuracy_class", "dropped", "approximations", "assumptions", "warnings", "object_mapping") if key in data}


def conversion_report(*, direction: str, fmt: str, name: str, size: int | None, sha256: str | None, target: str, adapter: Mapping[str, Any] | None = None,
                      converted: Mapping[str, Any] | None = None, unsupported: list[str] | None = None, errors: list[str] | None = None,
                      warnings: list[str] | None = None, lost: list[str] | None = None, approximated: list[str] | None = None,
                      assumptions: list[str] | None = None) -> dict[str, Any]:
    """The one report every conversion returns: source, destination, what succeeded, what was lost, approximated or unsupported, errors and warnings.
    ``status`` is *complete* only when nothing was lost, approximated or unsupported, *partial* when something was, and *failed* on any error."""
    a = adapter or {}
    lost = list(lost if lost is not None else a.get("dropped") or [])
    approximated = list(approximated if approximated is not None else a.get("approximations") or [])
    unsupported, errors = list(unsupported or []), list(errors or [])
    status = "failed" if errors else "partial" if (lost or approximated or unsupported) else "complete"
    return {"direction": direction, "status": status, "adapter": a.get("adapter", f"native-{fmt}"), "accuracy_class": a.get("accuracy_class", "exact"),
            "source": {"format": fmt if direction == "import" else "spadra", "name": name, "bytes": size, "sha256": sha256},
            "destination": {"format": "spadra" if direction == "import" else fmt, "target": target},
            "converted": dict(converted or {}), "lost": lost, "approximated": approximated, "unsupported": unsupported, "errors": errors,
            "warnings": list(warnings if warnings is not None else a.get("warnings") or []), "assumptions": list(assumptions if assumptions is not None else a.get("assumptions") or [])}


# ------------------------------------------------------------------------------------------------------------------------- reading
def decode(data: Any, *, label: str = "file") -> bytes:
    if not isinstance(data, str) or not data:
        raise BadRequest(f"The {label} is empty")
    if len(data) > MAX_IMPORT_BYTES * 4 // 3 + 8:
        raise LimitExceeded(f"The {label} is larger than {MAX_IMPORT_BYTES // (1024 * 1024)} MB")
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise BadRequest(f"The {label} was not sent as base64") from exc
    if len(raw) > MAX_IMPORT_BYTES:
        raise LimitExceeded(f"The {label} is larger than {MAX_IMPORT_BYTES // (1024 * 1024)} MB")
    return raw


def _refused(exc: Exception) -> Exception:
    """What an adapter's failure means to the person: a missing library, or a file that is damaged or not what it says. Application errors pass through."""
    if isinstance(exc, DependencyUnavailableError):
        return DependencyMissing(str(exc))
    if isinstance(exc, AppError):
        return exc
    if isinstance(exc, SpatialFormatError):
        return BadRequest(str(exc) or "The file could not be read")
    return BadRequest(f"The file is damaged or is not valid ({type(exc).__name__}: {exc})")


def sniff(raw: bytes) -> str | None:
    """What a file really is, from its content: ``sgeo``, ``sdrw``, ``sldc``, ``glb``, ``stl``, ``stl-ascii``, ``obj``, ``ifc``, ``step``, ``svg``, ``dxf``,
    ``dwg``, ``pdf`` or None. The file name is never trusted."""
    head = raw[:4096]
    if head[:4] == b"glTF":
        return "glb"
    if head[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                mime = z.read("mimetype") if "mimetype" in z.namelist() else b""
        except (zipfile.BadZipFile, OSError, RuntimeError, ValueError):
            return None
        for kind, expected in MIME_OF.items():
            if mime.strip() == (expected if isinstance(expected, bytes) else expected.encode()):
                return kind
        return None
    if head[:4] == b"%PDF":
        return "pdf"
    if head[:4] == b"AC10":
        return "dwg"
    text = head.decode("utf-8", "ignore").lstrip("﻿ \t\r\n")
    if text.startswith("ISO-10303-21"):
        return "ifc" if "IFC" in raw[:200_000].decode("utf-8", "ignore").upper().split("FILE_SCHEMA", 1)[-1][:200] else "step"
    if text.startswith("<?xml") or text.startswith("<svg") or text.startswith("<!DOCTYPE svg"):
        return "svg" if "<svg" in text or b"<svg" in raw[:65536] else None
    if text.startswith("0\nSECTION") or text.replace("\r", "").startswith("0\nSECTION") or text.replace("\r", "").lstrip().startswith("999\n"):
        return "dxf"
    if len(raw) >= 84 and int.from_bytes(raw[80:84], "little") * 50 + 84 == len(raw):
        return "stl"
    if text.startswith("solid") and "facet" in text:
        return "stl-ascii"
    if any(line.split(None, 1)[:1] in (["v"], ["f"], ["o"]) for line in text.splitlines()[:200] if line.strip() and not line.lstrip().startswith("#")):
        return "obj"
    return None


# A file is parsed once however many times it is looked at: the inspection, the import job and the import command all read the same bytes. The last
# few parsed sources are kept (by content hash, format, name and unit) and a caller always gets its own copy, so one can never alter another's.
_PARSED: "OrderedDict[tuple, tuple[Any, dict[str, Any]]]" = OrderedDict()
_PARSED_LOCK = threading.Lock()
PARSED_ENTRIES = 3
PARSED_MAX_BYTES = 32 * 1024 * 1024


def _parsed(kind: str, fmt: str, raw: bytes, name: str, unit: str | None, build: Any) -> tuple[Any, dict[str, Any]]:
    if len(raw) > PARSED_MAX_BYTES:
        return build()
    key = (kind, fmt, hashlib.sha256(raw).hexdigest(), name, unit)
    with _PARSED_LOCK:
        hit = _PARSED.get(key)
        if hit is not None:
            _PARSED.move_to_end(key)
    if hit is not None:
        return hit[0].copy(fresh_id=False), dict(hit[1])
    doc, report = build()
    with _PARSED_LOCK:
        _PARSED[key] = (doc.copy(fresh_id=False), dict(report))
        while len(_PARSED) > PARSED_ENTRIES:
            _PARSED.popitem(last=False)
    return doc, report


def _read_model(fmt: str, raw: bytes, *, name: str, unit: str) -> tuple[SGEODocument, dict[str, Any]]:
    """The file as an SGEO document in its own unit, with the adapter's report (a native file has none: it is read, not converted)."""
    return _parsed("model", fmt, raw, name, unit, lambda: _parse_model(fmt, raw, name=name, unit=unit))


def _parse_model(fmt: str, raw: bytes, *, name: str, unit: str) -> tuple[SGEODocument, dict[str, Any]]:
    try:
        if fmt == "sgeo":
            return SGEODocument.from_bytes(raw, mode="read"), {}
        if fmt == "glb":
            doc, report = SGEODocument.from_glb(raw, name=name)
        elif fmt == "stl":
            doc, report = SGEODocument.from_stl(raw, name=name, unit=unit)
        elif fmt == "obj":
            doc, report = SGEODocument.from_obj(raw, name=name, unit=unit)
        elif fmt == "ifc":
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "import.ifc"
                path.write_bytes(raw)
                doc, report = SGEODocument.import_ifc(path, name=name)
        else:
            raise BadRequest(f"Cannot import '{fmt}' into the model", details={"formats": list(MODEL_IMPORT)})
        return doc, report_dict(report)
    except Exception as exc:
        raise _refused(exc) from exc


def _read_drawing(fmt: str, raw: bytes, *, name: str, unit: str | None) -> tuple[SDRWDocument, dict[str, Any]]:
    return _parsed("drawing", fmt, raw, name, unit, lambda: _parse_drawing(fmt, raw, name=name, unit=unit))


def _parse_drawing(fmt: str, raw: bytes, *, name: str, unit: str | None) -> tuple[SDRWDocument, dict[str, Any]]:
    try:
        if fmt == "sdrw":
            return SDRWDocument.from_bytes(raw, mode="read"), {}
        if fmt == "svg":
            doc, report = SDRWDocument.from_svg(raw.decode("utf-8-sig"), unit=unit, name=name)
        elif fmt == "dxf":
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "import.dxf"
                path.write_bytes(raw)
                doc, report = SDRWDocument.import_dxf(path, unit=unit, name=name)
        else:
            raise BadRequest(f"Cannot import '{fmt}' into a drawing", details={"formats": list(DRAWING_IMPORT)})
        return doc, report_dict(report)
    except UnicodeDecodeError as exc:
        raise BadRequest("The SVG is not UTF-8 text") from exc
    except Exception as exc:
        raise _refused(exc) from exc


def _page_ids(doc: Any) -> list[str]:
    return E.all_ids(doc)


def _model_counts(doc: SGEODocument) -> dict[str, Any]:
    info = doc.inspect()
    ids = _page_ids(doc)
    vertices = faces = 0
    for oid in ids:
        g = doc.get(oid).get("geometry")
        if g and g["kind"] == "mesh":
            vertices += len(g["vertices"])
            faces += len(g["faces"])
    return {"objects": len(ids), "with_geometry": sum(1 for i in ids if doc.get(i).get("geometry")), "geometry_kinds": info.get("geometry_kinds", {}),
            "mesh_vertices": vertices, "mesh_triangles": faces, "families": info.get("family_count", 0), "materials": info.get("material_count", 0)}


def _extent(doc: SGEODocument) -> tuple[list[float] | None, float | None]:
    box = doc.bounds()
    if box is None:                                                    # an empty model has no extent
        return None, None
    lo, hi = box
    size = [hi[i] - lo[i] for i in range(3)]
    return size, max(size) * UNIT_M.get(doc.unit, 1.0)


def inspect_file(target: str, fmt: str, raw: bytes, *, name: str, unit: str | None = None) -> dict[str, Any]:
    """Look into a file without importing it: its real type, integrity (it must parse), version, unit, what it contains and anything suspicious.
    ``target`` is ``model`` or ``drawing``. The result's ``valid`` is true only when the same adapter an import would use accepts the file."""
    fmt = (fmt or "").lower()
    kinds = MODEL_IMPORT if target == "model" else DRAWING_IMPORT
    out: dict[str, Any] = {"target": target, "format": fmt, "name": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "valid": False,
                           "version": None, "unit": None, "counts": {}, "issues": [], "report": None}
    issue = lambda severity, message: out["issues"].append({"severity": severity, "message": message})        # noqa: E731
    if target not in ("model", "drawing"):
        raise BadRequest("target must be 'model' or 'drawing'")
    if len(raw) > MAX_IMPORT_BYTES:
        issue("error", f"The file is larger than the {MAX_IMPORT_BYTES // (1024 * 1024)} MB limit")
        return out
    detected = sniff(raw)
    out["detected"] = detected
    if fmt not in kinds:
        issue("error", f"'{fmt}' cannot be imported into a {target}; use one of {', '.join(kinds)}")
        return out
    if detected != fmt and not (fmt == "ifc" and detected == "step" and b"FILE_SCHEMA" not in raw[:200_000]):      # a Part 21 file that names no schema may be IFC; the adapter decides
        what = {"stl-ascii": "an ASCII STL, which is not supported (only binary STL is)", None: "not recognised as any format SPADRA reads"}.get(detected, f"a {detected.upper()} file" if detected else "")
        issue("error", f"The file is called {fmt.upper()} but its content is {what}")
        return out
    try:
        if target == "model":
            declared = fmt in ("sgeo", "glb", "ifc")
            doc, adapter = _read_model(fmt, raw, name=name, unit=unit or "mm")
            out["unit"] = {"value": doc.unit, "declared": declared}
            if not declared:
                issue("warning", f"{fmt.upper()} files do not state a unit; {doc.unit} was assumed. If the model looks the wrong size, choose the unit it was made in")
            counts = _model_counts(doc)
            if counts["objects"] > MAX_IMPORT_OBJECTS:
                issue("error", f"The file holds {counts['objects']} objects; the limit is {MAX_IMPORT_OBJECTS}")
            if not counts["with_geometry"]:
                issue("warning", "The file holds no geometry")
            size, metres = _extent(doc)
            if metres is not None:
                out["extent"] = {"size": [round(s, 6) for s in size], "unit": doc.unit, "metres": round(metres, 6)}
                if not SANE_EXTENT_M[0] <= metres <= SANE_EXTENT_M[1] and counts["with_geometry"]:
                    issue("warning", f"Overall size {metres:g} m is outside {SANE_EXTENT_M[0]:g} m .. {SANE_EXTENT_M[1]:g} m: the unit may be wrong")
            if counts["materials"]:
                issue("info", f"{counts['materials']} material(s) are not imported")
            info = doc.inspect()
            out["version"] = str(info.get("source_version") or info.get("format_version") or ("2.0" if fmt == "glb" else "")) or None
            meta = doc._model.metadata
            out["metadata"] = {"keys": sorted(meta), "spadra_project": SCENE_KEY in meta, "animation": any(k.startswith("slai.animation") for k in meta)}
            if out["metadata"]["animation"]:
                issue("info", "The animation in the file is not imported")
            out["counts"] = counts
        else:
            doc, adapter = _read_drawing(fmt, raw, name=name, unit=unit)
            info = doc.inspect()
            out["unit"] = {"value": doc.unit, "declared": fmt != "svg" or bool(unit) or b"data-unit" in raw}
            ids = _page_ids(doc)
            kinds_count: dict[str, int] = {}
            for eid in ids:
                k = doc.get(eid)["kind"]
                kinds_count[k] = kinds_count.get(k, 0) + 1
            out["counts"] = {"entities": len(ids), "kinds": kinds_count, "layers": len(doc.layers()), "blocks": len(doc.blocks())}
            out["version"] = str(info.get("source_version") or info.get("format_version") or "") or None
            if len(ids) > MAX_IMPORT_ENTITIES:
                issue("error", f"The file holds {len(ids)} entities; the limit is {MAX_IMPORT_ENTITIES}")
            if not ids:
                issue("warning", "The file holds no entities")
            if not out["unit"]["declared"]:
                issue("warning", f"The file states no unit; {doc.unit} was assumed")
        out["accuracy_class"] = adapter.get("accuracy_class", "exact")
        out["valid"] = not any(i["severity"] == "error" for i in out["issues"])
        out["report"] = conversion_report(direction="import", fmt=fmt, name=name, size=len(raw), sha256=out["sha256"], target=target, adapter=adapter,
                                          converted={k: v for k, v in out["counts"].items() if isinstance(v, int)})
    except (BadRequest, DependencyMissing) as exc:
        issue("error", exc.message if hasattr(exc, "message") else str(exc))
        out["dependency_missing"] = isinstance(exc, DependencyMissing)                     # not a fault in the file: something has to be installed
    return out


def _require_valid(check: Mapping[str, Any]) -> None:
    """Raise the refusal an import owes when the inspection found an error: the missing-dependency kind where that is the cause."""
    errors = [i["message"] for i in check["issues"] if i["severity"] == "error"]
    if errors:
        if check.get("dependency_missing"):
            raise DependencyMissing(errors[0])
        raise BadRequest(errors[0], details={"issues": check["issues"]})


# --------------------------------------------------------------------------------------------------------------------------- model in
def import_model(project: Any, fmt: str, raw: bytes, *, name: str, unit: str = "mm") -> dict[str, Any]:
    """Add the contents of a model file to the project's model. Runs inside the ``model.import`` command, so it is one revision and one undo step,
    and a refusal (bad file, limits, an element that does not regenerate) leaves the model as it was."""
    model = project.model
    check = inspect_file("model", fmt, raw, name=name, unit=unit)
    _require_valid(check)
    source, adapter = _read_model(fmt, raw, name=name, unit=unit)
    if source.unit != model.unit:
        source = source.copy(fresh_id=False)
        source.convert_unit(model.unit)
    if len(_page_ids(model)) + len(_page_ids(source)) > MAX_MODEL_OBJECTS:
        raise LimitExceeded(f"The model would hold more than {MAX_MODEL_OBJECTS} objects")
    if fmt == "sgeo":
        counts, lost, warnings, assumptions = _merge_native(project, source, name)
    else:
        counts, lost, warnings, assumptions = _merge_foreign(model, source, name), [], [], []
    report = conversion_report(direction="import", fmt=fmt, name=name, size=len(raw), sha256=check["sha256"], target="model", adapter=adapter, converted=counts,
                               lost=(adapter.get("dropped") or []) + lost if adapter or lost else lost, warnings=(adapter.get("warnings") or []) + warnings + [i["message"] for i in check["issues"] if i["severity"] == "warning"],
                               assumptions=(adapter.get("assumptions") or []) + assumptions)
    return {"objects": counts["objects"], "report": report}


def _hierarchy_order(source: SGEODocument, ids: list[str]) -> list[str]:
    """Parents before children, whatever order the file lists them in."""
    known, order, pending = set(ids), [], list(ids)
    placed: set[str] = set()
    while pending:
        progressed = False
        for oid in list(pending):
            parent = source.get(oid).get("parent")
            if parent is not None and parent in known and parent not in placed:
                continue
            order.append(oid)
            placed.add(oid)
            pending.remove(oid)
            progressed = True
        if not progressed:
            raise BadRequest("The file's object hierarchy refers to a parent that is not in the file")
    return order


def _merge_foreign(model: SGEODocument, source: SGEODocument, label: str) -> dict[str, int]:
    """Copy every object of a converted file into the model with new ids, keeping hierarchy, transforms, geometry and the file's own metadata."""
    ids = _page_ids(source)
    mapped: dict[str, str] = {}
    for oid in _hierarchy_order(source, ids):
        obj = source.get(oid)
        meta = {**{k: v for k, v in (obj.get("metadata") or {}).items() if not k.startswith(f"{E.NS}:")}, f"{E.NS}:category": "imported", f"{E.NS}:source": label}
        options = {"name": obj.get("name") or label, "parent": mapped.get(obj.get("parent")), "transform": obj["transform"], "metadata": meta}
        geometry = obj.get("geometry")
        mapped[oid] = model.add_group(**options) if geometry is None else model.add_object(geometry, **options)
    return {"objects": len(mapped), "with_geometry": sum(1 for i in ids if source.get(i).get("geometry"))}


# ----------------------------------------------------------------------------------------------------------------------- native SGEO
LEVEL_KEYS = ("level", "top_level")


def _map_levels(project: Any, source: SGEODocument, warnings: list[str]) -> tuple[dict[str, str], int]:
    """Match the file's levels to the project's by name (and elevation); a level the project lacks is created, with its floor plan."""
    from . import datums as DT
    from .project import SDRWDocument as _Doc
    meta = source._model.metadata.get(SCENE_KEY) or {}
    mapped: dict[str, str] = {}
    created = 0
    for lv in meta.get("levels", []):
        have = next((l for l in project.manifest["levels"] if l["name"].lower() == str(lv["name"]).lower()), None)
        elevation = float(lv["elevation"])
        if have is not None and abs(float(have["elevation"]) - elevation) < 1e-6:
            mapped[lv["id"]] = have["id"]
            continue
        if have is not None:
            warnings.append(f"Level '{lv['name']}' is at {elevation:g} m in the file but {have['elevation']:g} m here; the file's level was added as a separate level")
        name = lv["name"] if have is None else f"{lv['name']} (imported)"
        new = DT.add_level(project, {"name": name, "elevation": elevation}, lambda n: _Doc.new(n, unit=project.manifest["unit"]))
        mapped[lv["id"]] = new["id"]
        created += 1
    return mapped, created


def _merge_native(project: Any, source: SGEODocument, label: str) -> tuple[dict[str, int], list[str], list[str], list[str]]:
    """Bring in an SGEO file. Objects keep their ids where those are free (a clash gets a new id and every reference is rewritten), their
    parameters, hierarchy, transforms, family instances and metadata; the levels they stand on are matched by name. A file that did not come from
    SPADRA is brought in as plain imported objects. Materials, textures and animation are not brought in, and the report says so."""
    model = project.model
    lost: list[str] = []
    warnings: list[str] = []
    assumptions: list[str] = []
    ids = _page_ids(source)
    native = SCENE_KEY in source._model.metadata or any(f"{E.NS}:category" in (source.get(i).get("metadata") or {}) for i in ids)
    if not native:
        counts = _merge_foreign(model, source, label)
        assumptions.append("The file did not come from SPADRA, so its objects are plain imported objects")
    else:
        levels, created = _map_levels(project, source, warnings)
        taken = set(_page_ids(model))
        mapped_ids = {oid: (oid if oid not in taken else new_id()) for oid in ids}
        renamed = sum(1 for o, n in mapped_ids.items() if o != n)
        if renamed:
            warnings.append(f"{renamed} object id(s) were already in use here and were given new ids; references between them were rewritten")
        families: dict[str, str] = {}
        have = {f["id"]: f for f in model.families()}
        for fam in source.families():
            fam = {k: v for k, v in fam.items() if k != "instance_count"}
            existing = have.get(fam["id"])
            if existing is not None and {k: v for k, v in existing.items() if k != "instance_count"} == fam:
                families[fam["id"]] = fam["id"]
                continue
            used = {f["name"].lower() for f in model.families()}
            name, n = fam["name"], 1
            while name.lower() in used:
                n += 1
                name = f"{fam['name']} {n}"
            families[fam["id"]] = model.define_family(name, fam["parameters"], fam["generator"]["kind"], family_id=fam["id"] if fam["id"] not in have else None)
        missing_level = 0
        for oid in _hierarchy_order(source, ids):
            obj = source.get(oid)
            meta = dict(obj.get("metadata") or {})
            for key in LEVEL_KEYS:
                v = meta.get(f"{E.NS}:{key}")
                if v is not None:
                    if v not in levels:
                        missing_level += 1
                        meta.pop(f"{E.NS}:{key}")
                    else:
                        meta[f"{E.NS}:{key}"] = levels[v]
            if meta.get(f"{E.NS}:host") in mapped_ids:
                meta[f"{E.NS}:host"] = mapped_ids[meta[f"{E.NS}:host"]]
            if meta.get(f"{E.NS}:joins"):
                meta[f"{E.NS}:joins"] = [mapped_ids[j] for j in meta[f"{E.NS}:joins"] if j in mapped_ids]
            options = {"name": obj.get("name") or label, "parent": mapped_ids.get(obj.get("parent")), "transform": obj["transform"], "metadata": meta, "object_id": mapped_ids[oid]}
            if "family_id" in obj:
                model.instantiate(families[obj["family_id"]], obj.get("parameters") or {}, **options)
            elif obj.get("geometry") is None:
                model.add_group(**options)
            else:
                model.add_object(obj["geometry"], **options)
        if missing_level:
            warnings.append(f"{missing_level} level reference(s) pointed at levels the file does not describe and were removed; those elements need a level")
        E.regenerate_all(project)                              # the elements' geometry is rebuilt from the parameters they arrived with, here, against this project's levels
        counts = {"objects": len(ids), "with_geometry": sum(1 for i in ids if source.get(i).get("geometry")), "levels_created": created, "families": len(families)}
    if source.materials():
        lost.append(f"{len(source.materials())} material(s) and their textures")
    if any(k.startswith("slai.animation") for k in source._model.metadata):
        lost.append("the animation stored in the file")
    return counts, lost, warnings, assumptions


# ---------------------------------------------------------------------------------------------------------------------------- model out
def export_model(project: Any, fmt: str, object_ids: list[str] | None = None) -> tuple[bytes, dict[str, Any]]:
    """The model as ``fmt`` bytes with its conversion report. ``sgeo`` is native and carries everything: the elements' parameters, families, hierarchy
    and the levels they stand on. GLB, STL and OBJ are triangle surfaces in world coordinates; the report lists what that loses."""
    if fmt not in MODEL_EXPORT:
        raise BadRequest(f"Cannot export the model as '{fmt}'", details={"formats": list(MODEL_EXPORT)})
    model = project.model
    ids = _page_ids(model) if object_ids is None else list(dict.fromkeys(object_ids))
    unknown = [i for i in ids if not E.alive(project, i)]
    if unknown:
        raise BadRequest("Some of the selected elements do not exist", details={"ids": unknown[:10]})
    if not any(model.get(i).get("geometry") for i in ids):
        raise BadRequest("There is nothing with geometry to export")
    counts = {"objects": len(ids), "with_geometry": sum(1 for i in ids if model.get(i).get("geometry"))}
    try:
        if fmt == "sgeo":
            if object_ids is not None:
                raise BadRequest("A native SGEO export is the whole model; select nothing to export it")
            scene = model.snapshot()
            scene.metadata[SCENE_KEY] = {"name": project.name, "unit": project.manifest["unit"], "levels": [dict(l) for l in project.manifest["levels"]]}
            doc = SGEODocument.from_scene(scene, copy=False)
            data = doc.dumps()
            adapter: dict[str, Any] = {}
            counts.update(families=len(model.families()), levels=len(project.manifest["levels"]))
        else:
            cutters = [i for i in ids if E.m(model.get(i)["metadata"], "category") == "void"]            # a void removes material: it is not a surface of the building
            selected = None if object_ids is None and not cutters else [i for i in ids if i not in cutters]
            source = model
            if cutters and fmt == "glb":                                                                  # GLB takes the whole scene: leave the voids out of a copy
                source = model.copy(fresh_id=False)
                for i in cutters:
                    source.remove(i)
            data, report = {"glb": lambda: source.to_glb(), "stl": lambda: model.to_stl(selected), "obj": lambda: model.to_obj(selected)}[fmt]()
            if cutters:
                counts["voids_left_out"] = len(cutters)
            adapter = report_dict(report)
    except Exception as exc:
        raise _refused(exc) from exc
    unsupported = []
    if fmt != "sgeo":
        nogeo: dict[str, int] = {}
        for i in ids:
            if not model.get(i).get("geometry"):
                cat = str(E.m(model.get(i)["metadata"], "category", "group"))
                nogeo[cat] = nogeo.get(cat, 0) + 1
        unsupported = [f"{n} {cat}(s) carry no geometry (levels, rooms and grids are data, not surfaces) and are not in the {fmt.upper()} file" for cat, n in sorted(nogeo.items())]
    return data, conversion_report(direction="export", fmt=fmt, name=project.name, size=len(data), sha256=hashlib.sha256(data).hexdigest(), target="file", adapter=adapter,
                                   converted=counts, unsupported=unsupported, lost=adapter.get("dropped") if fmt != "sgeo" else [])


# --------------------------------------------------------------------------------------------------------------------------- drawing in
def import_drawing(drawing: SDRWDocument, fmt: str, raw: bytes, *, name: str, unit: str | None = None) -> dict[str, Any]:
    """Add the entities of an SDRW, SVG or DXF file (with their layers, line types and blocks) to a drawing, converted to the drawing's unit. ``unit``
    overrides the unit the file declares; without it the file's own declaration is used."""
    check = inspect_file("drawing", fmt, raw, name=name, unit=unit)
    _require_valid(check)
    source, adapter = _read_drawing(fmt, raw, name=name, unit=unit)
    try:
        if source.unit != drawing.unit:
            source = source.copy(fresh_id=False)
            source.convert_unit(drawing.unit)
        added = _merge_entities(drawing, source)
    except Exception as exc:
        raise _refused(exc) from exc
    report = conversion_report(direction="import", fmt=fmt, name=name, size=len(raw), sha256=check["sha256"], target="drawing", adapter=adapter, converted={"entities": added},
                               warnings=(adapter.get("warnings") or []) + [i["message"] for i in check["issues"] if i["severity"] == "warning"])
    return {"entities": added, "report": report}


def _merge_entities(drawing: SDRWDocument, source: SDRWDocument) -> int:
    have_layers = {layer["name"] for layer in drawing.layers()}
    have_linetypes = {lt["name"] for lt in drawing.linetypes()}
    have_blocks = {b["name"] for b in drawing.blocks()}
    ops: list[dict[str, Any]] = []
    for layer in source.layers():
        if layer["name"] not in have_layers:
            ops.append({"op": "add_layer", "name": layer["name"], "value": layer.get("visible", True), **({"style": layer["style"]} if layer.get("style") else {})})
    for lt in source.linetypes():
        if lt["name"] not in have_linetypes:
            ops.append({"op": "define_linetype", "name": lt["name"], "pattern": lt["pattern"], "description": lt.get("description")})
    for block in source.blocks():
        if block["name"] not in have_blocks:
            full = source.get_block(block["name"])
            ops.append({"op": "define_block", "name": full["name"], "base_point": full.get("base_point", [0.0, 0.0]),
                        "entities": [_spec(e) for e in full["entities"]], "description": full.get("description")})
    entity_ids = _page_ids(source)
    for eid in entity_ids:
        ops.append({"op": "add", **_spec(source.get(eid)), "create_layer": True})
    if not entity_ids:
        return 0
    for start in range(0, len(ops), 1000):                              # a batch is at most 1000 operations; the chunks follow dependency order
        drawing.apply(ops[start:start + 1000])
    return len(entity_ids)


def _spec(entity: Mapping[str, Any]) -> dict[str, Any]:
    return {key: entity[key] for key in ("kind", "geometry", "layer", "name", "metadata", "style") if entity.get(key) not in (None, {}, "")}


# -------------------------------------------------------------------------------------------------------------------------- drawing out
def export_drawing(drawing: SDRWDocument, fmt: str, *, name: str) -> tuple[bytes, dict[str, Any]]:
    """The drawing as SDRW (native, complete), SVG or DXF bytes with its conversion report. DXF needs ezdxf."""
    if fmt not in DRAWING_EXPORT:
        raise BadRequest(f"Cannot export a drawing as '{fmt}'", details={"formats": list(DRAWING_EXPORT)})
    try:
        if fmt == "sdrw":
            data, adapter = drawing.dumps(), {}
        elif fmt == "svg":
            payload, report = drawing.to_svg()
            data, adapter = (payload if isinstance(payload, bytes) else str(payload).encode("utf-8")), report_dict(report)
        else:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "export.dxf"
                adapter = report_dict(drawing.export_dxf(path, overwrite=True))
                data = path.read_bytes()
    except Exception as exc:
        raise _refused(exc) from exc
    return data, conversion_report(direction="export", fmt=fmt, name=name, size=len(data), sha256=hashlib.sha256(data).hexdigest(), target="file", adapter=adapter,
                                   converted={"entities": len(_page_ids(drawing))})


def export_dxf(drawing: SDRWDocument) -> tuple[bytes, dict[str, Any]]:
    """The drawing as DXF bytes, with the conversion report."""
    return export_drawing(drawing, "dxf", name=drawing.name)
