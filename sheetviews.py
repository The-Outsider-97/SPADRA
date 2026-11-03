"""What a sheet can show besides plans, sections, elevations and schedules: the 3D model, a picture of the 3D view, and a report.

* **3D model** (``kind: "model"``) - the model drawn as vector shapes from a camera direction (azimuth and elevation), as a parallel (axonometric)
  projection that can be put at a drawing scale like any other view. It is read from the model each time the sheet is drawn, so it follows the model
  and takes part in "changed since issue". Hidden surfaces are removed by painting the faces that face the viewer from far to near; that is right
  for ordinary buildings and can be wrong where two surfaces pass through each other or where one large face is partly in front of and partly
  behind another. Smooth curved surfaces get no silhouette line. Voids are not drawn.
* **3D snapshot** (``kind: "snapshot"``) - the picture the 3D view shows at the moment it is taken (perspective or orthographic, with the shading
  the view uses), stored in the project as a PNG. It is a picture: it does not follow the model, which is why it exists next to the live model view.
* **Report** (``kind: "report"``) - the text and tables of a report (an SLDC document) set in a box on the sheet. A box has a fixed number of lines;
  what does not fit is replaced by a line saying how many lines are not shown, and a table cell wider than its column is cut with an ellipsis.

Nothing here is estimated: the model view is computed from the model's meshes, the snapshot is the browser's own rendering and the report text is read
back from the stored report."""
from __future__ import annotations

import base64
import binascii
import hashlib
import math
import struct
import textwrap
from datetime import datetime, timezone
from typing import Any, Mapping

from src.utils import sldc as S

from ..tools.errors import BadRequest, LimitExceeded, NotFound
from . import documentation as D
from . import elements as E
from . import reports as RP

Pt3 = tuple[float, float, float]
KINDS = ("view", "model", "snapshot", "report")
MODEL_FACE_LIMIT = 40_000                          # faces drawn in one model view: more would make the sheet too heavy to draw and to print
PNG_LIMIT = 8 * 1024 * 1024
PNG_SIDE = 8192
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
DEFAULT_AZIMUTH, DEFAULT_ELEVATION = -50.0, 30.0
CHAR_WIDTH = 0.56                                  # average character width, as a fraction of the text height (the schedule tables use the same)
LINE_HEIGHT = 1.35
REPORT_LINES = (5, 400)


def model_name(view: Mapping[str, Any]) -> str:
    """What a placed 3D view is called: the view's name, with "(3D)" added unless the name already says so."""
    return view["name"] if "3d" in view["name"].lower() else f"{view['name']} (3D)"


def cascade(sheet: Mapping[str, Any], width: float, height: float) -> tuple[float, float]:
    """Where a new viewport goes when nobody said: the usual corner, stepped by 15 mm for each viewport already there so that they do not land on one another."""
    n = len(sheet.get("viewports", [])) % 12
    return 0.06 * width + 15.0 * n, 0.18 * height + 15.0 * n


def kind_of(vp: Mapping[str, Any]) -> str:
    return str(vp.get("kind") or "view")


# ----------------------------------------------------------------------------------------------------------------------- the 3D model
def basis(azimuth: float, elevation: float) -> tuple[Pt3, Pt3, Pt3]:
    """(towards the viewer, screen right, screen up) for a viewer standing at ``azimuth`` (degrees from +X, counter-clockwise in plan) and
    ``elevation`` degrees above the horizon, looking at the model."""
    az, el = math.radians(azimuth), math.radians(elevation)
    v = (math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el))
    right = (-math.sin(az), math.cos(az), 0.0)
    up = (v[1] * right[2] - v[2] * right[1], v[2] * right[0] - v[0] * right[2], v[0] * right[1] - v[1] * right[0])      # towards × right: screen up
    return v, right, up


def _dot(a: Pt3, b: Pt3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def check_camera(azimuth: Any, elevation: Any) -> tuple[float, float]:
    try:
        az, el = float(azimuth), float(elevation)
    except (TypeError, ValueError):
        raise BadRequest("azimuth and elevation must be numbers (degrees)") from None
    if not (math.isfinite(az) and math.isfinite(el)):
        raise BadRequest("azimuth and elevation must be numbers (degrees)")
    if not -89.0 <= el <= 89.0:
        raise BadRequest("The elevation must be between -89 and 89 degrees")
    return ((az + 180.0) % 360.0) - 180.0, el


def model_view(project: Any, azimuth: float, elevation: float, f: float) -> dict[str, Any]:
    """The model's faces that face the viewer, flattened to the screen in the drawing's unit (``f`` converts metres), far to near. Each face is
    ``[x1, y1, x2, y2, x3, y3, shade, edges]``; ``edges`` has one bit per side (side i runs from corner i to corner i+1) set where that side is a
    boundary of the surface or a crease."""
    key = ("model-view", round(azimuth, 6), round(elevation, 6), f)
    cached = project._cache.get(key)
    if cached is not None:
        return cached
    v, right, up = basis(azimuth, elevation)
    light = (0.6 * v[0] - 0.5 * right[0] + 0.7 * up[0], 0.6 * v[1] - 0.5 * right[1] + 0.7 * up[1], 0.6 * v[2] - 0.5 * right[2] + 0.7 * up[2])
    ln = math.sqrt(_dot(light, light))
    light = (light[0] / ln, light[1] / ln, light[2] / ln)
    from .project import all_ids
    matrices = project.model._model.world_transforms()
    faces: list[tuple[float, list[float]]] = []
    for oid in all_ids(project.model, has_geometry=True):
        obj = project.model.get(oid)
        if str(E.m(obj["metadata"], "category", "generic")) == "void":
            continue
        verts, tris = D.world_mesh(project, oid, matrices)
        if not tris:
            continue
        normals: list[Pt3 | None] = []
        sides: dict[Any, list[Any]] = {}
        for n, (ia, ib, ic) in enumerate(tris):
            c = D._cross(D._sub(verts[ib], verts[ia]), D._sub(verts[ic], verts[ia]))
            length = math.sqrt(_dot(c, c))
            normals.append(None if length < 1e-12 else (c[0] / length, c[1] / length, c[2] / length))
            for side, (p, q) in enumerate(((ia, ib), (ib, ic), (ic, ia))):
                kp, kq = D._key(verts[p]), D._key(verts[q])
                if kp != kq:
                    sides.setdefault((kp, kq) if kp < kq else (kq, kp), []).append((n, side))
        drawn = [0] * len(tris)
        for users in sides.values():
            edge = len(users) == 1 or len(users) > 2
            if not edge:
                a, b = normals[users[0][0]], normals[users[1][0]]
                edge = a is None or b is None or _dot(a, b) < 0.9998           # more than about 1 degree between the two faces: a crease
            if edge:
                for n, side in users:
                    drawn[n] |= 1 << side
        for n, (ia, ib, ic) in enumerate(tris):
            nrm = normals[n]
            if nrm is None or _dot(nrm, v) <= 1e-9:                             # edge-on or facing away
                continue
            pts = [verts[ia], verts[ib], verts[ic]]
            flat = [c for p in pts for c in (round(_dot(p, right) * f, 4), round(_dot(p, up) * f, 4))]
            depth = sum(_dot(p, v) for p in pts) / 3
            shade = round(0.62 + 0.35 * max(0.0, _dot(nrm, light)), 3)
            faces.append((depth, flat + [shade, drawn[n]]))
            if len(faces) > MODEL_FACE_LIMIT:
                raise LimitExceeded(f"This 3D view has more than {MODEL_FACE_LIMIT} faces to draw", hint="Place a plan, section or elevation instead, or simplify the model")
    faces.sort(key=lambda item: item[0])                                        # far first, so that nearer faces paint over what they hide
    xs = [c for _, fc in faces for c in fc[0:6:2]]
    ys = [c for _, fc in faces for c in fc[1:6:2]]
    out = {"faces": [fc for _, fc in faces], "bounds": [[min(xs), min(ys)], [max(xs), max(ys)]] if xs else None}
    project._cache[key] = out
    return out


def model_digest(project: Any, azimuth: float, elevation: float) -> str:
    key = ("model-digest", round(azimuth, 6), round(elevation, 6))
    if key not in project._cache:
        data = model_view(project, azimuth, elevation, 1000.0)
        project._cache[key] = hashlib.sha256(repr(data["faces"]).encode()).hexdigest()[:16]
    return project._cache[key]


# ------------------------------------------------------------------------------------------------------------------------ snapshots
def png_size(data: bytes) -> tuple[int, int]:
    """The pixel size of a PNG, after checking that it is one (signature and a first IHDR chunk) and not unreasonably large."""
    if len(data) > PNG_LIMIT:
        raise LimitExceeded(f"A snapshot is at most {PNG_LIMIT // (1024 * 1024)} MB")
    if len(data) < 33 or not data.startswith(PNG_MAGIC) or data[12:16] != b"IHDR":
        raise BadRequest("The snapshot is not a PNG picture")
    width, height = struct.unpack(">II", data[16:24])
    if not (0 < width <= PNG_SIDE and 0 < height <= PNG_SIDE):
        raise BadRequest(f"A snapshot is at most {PNG_SIDE} pixels on a side")
    return width, height


def decode_png(value: Any) -> tuple[bytes, int, int]:
    if not isinstance(value, str) or not value:
        raise BadRequest("The snapshot picture is missing")
    if value.startswith("data:"):
        value = value.split(",", 1)[-1]
    try:
        data = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise BadRequest("The snapshot picture is not valid base64") from None
    width, height = png_size(data)
    return data, width, height


def image_entry(data: bytes, width: int, height: int, camera: Mapping[str, Any], revision: int) -> dict[str, Any]:
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "width_px": width, "height_px": height, "camera": dict(camera), "model_revision": revision,
            "taken": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def snapshot_data(project: Any, vp: Mapping[str, Any]) -> dict[str, Any]:
    data = project.snapshots.get(vp["id"])
    if data is None:
        raise NotFound("The snapshot's picture is missing from the project")
    return {"href": "data:image/png;base64," + base64.b64encode(data).decode("ascii"), **vp["image"]}


def snapshot_size(vp: Mapping[str, Any]) -> tuple[float, float]:
    """Width and height on the paper, in millimetres."""
    image = vp["image"]
    width = float(vp["width"])
    return width, width * image["height_px"] / image["width_px"]


# -------------------------------------------------------------------------------------------------------------------------- reports
def report_box(project: Any, vp: Mapping[str, Any]) -> dict[str, Any]:
    """The report's text laid out for a box ``vp['width']`` mm wide with text ``vp['size']`` mm high: lines of text and table rows, at most ``vp['lines']``."""
    entry = RP.find(project, vp["report_id"])
    data, _ = RP.read(project, entry["id"])
    try:
        blocks = S.loads_package(data).document["blocks"]
    except S.SLDCError as exc:
        raise BadRequest(f"The stored report cannot be read: {exc}") from exc
    size, width = float(vp["size"]), float(vp["width"])
    chars = max(8, int(width / (size * CHAR_WIDTH)))
    lines: list[dict[str, Any]] = []
    cut = False
    for block in blocks:
        kind = block.get("type")
        text = str(block.get("text", ""))
        if kind == "heading":
            level = int((block.get("attributes") or {}).get("level", 1))
            lines += [{"style": f"h{min(level, 3)}", "text": t} for t in textwrap.wrap(text, max(8, int(chars / (1.35 if level == 1 else 1.1)))) or [""]]
            if level == 1:
                lines.append({"style": "gap"})
        elif kind == "table":
            rows = [[str(c.get("text", "")) for c in row] for row in block["table"]["rows"]]
            heads = int(block["table"].get("header_rows", 0))
            if not rows:
                continue
            columns = len(rows[0])
            natural = [max(len(r[i]) if i < len(r) else 0 for r in rows) + 2 for i in range(columns)]
            if sum(natural) > chars:                                           # every column gets its share of the box's width, at least four characters
                total = sum(natural)
                natural = [max(4, int(w * chars / total)) for w in natural]
            offsets = [sum(natural[:i]) for i in range(columns)]
            for n, row in enumerate(rows):
                cells = []
                for i, cell in enumerate(row[:columns]):
                    room = natural[i] - 2
                    if len(cell) > room:
                        cell, cut = cell[: max(1, room - 1)] + "…", True
                    cells.append([offsets[i], cell])
                lines.append({"style": "head" if n < heads else "row", "cells": cells, "span": sum(natural)})
            lines.append({"style": "gap"})
        elif kind == "list_item":
            wrapped = textwrap.wrap(text, max(8, chars - 2)) or [""]
            lines += [{"style": "li", "text": ("• " if i == 0 else "  ") + t} for i, t in enumerate(wrapped)]
        else:
            lines += [{"style": "p", "text": t} for t in textwrap.wrap(text, chars) or [""]]
            lines.append({"style": "gap"})
    while lines and lines[-1]["style"] == "gap":
        lines.pop()
    shown = int(vp["lines"])
    hidden = max(0, len(lines) - shown)
    if hidden:
        lines = lines[: shown - 1] + [{"style": "p", "text": f"… {hidden + 1} more line(s) are in the report"}]
    return {"name": entry["name"], "lines": lines, "line_height": round(size * LINE_HEIGHT, 3), "char_width": round(size * CHAR_WIDTH, 3), "hidden": hidden, "cut": cut,
            "revision": entry["revision"]}


def check_report_box(width: Any, size: Any, lines: Any, paper_width: float) -> tuple[float, float, int]:
    from ..tools.validation import number
    w = number(width, "width", 20.0, paper_width)
    sz = number(size, "size", 1.2, 10.0)
    n = number(lines, "lines", float(REPORT_LINES[0]), float(REPORT_LINES[1]))
    return float(w), float(sz), int(n)
