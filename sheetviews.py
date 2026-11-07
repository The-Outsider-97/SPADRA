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
from . import layers as LY
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
    ``[x1, y1, x2, y2, x3, y3, shade, edges, colorRGB]``; ``colorRGB`` is an sRGB integer or -1 for an unassigned appearance.
    ``edges`` has one bit per side (side i runs from corner i to corner i+1) set where that side is a boundary or a crease."""
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
        meta = obj["metadata"]
        category = str(E.m(meta, "category", "generic"))
        if category == "void":
            continue
        verts, tris = D.world_mesh(project, oid, matrices)
        if not tris:
            continue
        # The same material precedence as the 3D viewport: element override,
        # layer override, project material appearance, built-in fallback. The
        # layer-face counts come from the actual layered mesh, not from a
        # guessed bounding-box location or a duplicate material registry.
        base_color = E.appearance_color(project, meta)
        face_colors = [int(base_color[1:], 16) if base_color else -1] * len(tris)
        counts = E.m(meta, "layer_faces") or []
        if not E.m(meta, "color") and counts and category in LY.LAYERED:
            layers = LY.stack(project, oid)
            if len(layers) == len(counts) and sum(counts) == len(tris):
                face_colors = []
                for count, layer in zip(counts, layers):
                    value = (layer.get("color") or project.manifest.get("material_colors", {}).get(layer["material"])
                             or E.MATERIAL_COLORS.get(layer["material"]))
                    face_colors.extend([int(value[1:], 16) if value else -1] * int(count))
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
            faces.append((depth, flat + [shade, drawn[n], face_colors[n]]))
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


# ----------------------------------------------------------------------------------------------------------------------------- layout
# Where things go on the paper. A viewport's (x, y) means a different corner for each kind (the drawing's lower left for a view, the model and a picture; the
# top left for a schedule and a report), so everything below works with the rectangle a viewport really covers, in millimetres with y up from the paper's
# lower edge, and converts back only when it writes a position.
FRAME = 10.0                                       # the drawing frame's distance from the paper's edge (the sheet view draws the same)
INSET = 5.0                                        # clear space kept between the frame and anything arranged inside it
GAP = 8.0                                          # clear space kept between two viewports
LABEL = 7.0                                        # the title printed above a view or schedule
TITLE_BLOCK = (180.0, 66.0)                        # width and height of the title block, bottom right
REVISION_ROW = 5.0
SNAP = 3.0                                         # mm within which a dragged viewport snaps to an edge
Rect = tuple[float, float, float, float]           # x0, y0, x1, y1


def _scale_k(project: Any, vp: Mapping[str, Any]) -> float:
    from .project import MM_PER
    num, _, den = str(vp["scale"]).partition(":")
    return MM_PER[project.manifest["unit"]] * float(num) / float(den)


def footprint(project: Any, vp: Mapping[str, Any]) -> tuple[float, float, float, float]:
    """(width, height, dx, dy) of what a viewport covers: its lower left corner is at (vp.x + dx, vp.y + dy). A view or schedule includes the title printed
    above it. Sizes are measured from the same data the sheet draws (a schedule's from the same text widths the sheet view uses, so within a character)."""
    kind = kind_of(vp)
    if kind == "snapshot":
        w, h = snapshot_size(vp)
        return w, h, 0.0, 0.0
    if kind == "report":
        box = report_box(project, vp)
        size = box["line_height"] / LINE_HEIGHT
        scale = {"h1": 1.35, "h2": 1.1, "h3": 1.0}
        y = size
        for line in box["lines"]:
            y += box["line_height"] * (0.5 if line["style"] == "gap" else scale.get(line["style"], 1.0))
        return float(vp["width"]), y, 0.0, -y
    view = project.view(vp["view_id"])
    if view["kind"] == "schedule":
        data = D.schedule_data(project, view["category"])
        size, row, pad = 2.6, 5.0, 1.6
        cell = lambda v: "" if v is None else str(v)                                   # noqa: E731
        widths = [max(len(f"{c['label']}{' (' + c['unit'] + ')' if c.get('unit') else ''}"), *(len(cell(r.get(c["key"]))) for r in data["rows"]),
                      *([len(cell(data["totals"].get(c["key"])))] if c.get("total") else [])) * size * CHAR_WIDTH + 2 * pad for c in data["columns"]]
        lines = len(data["rows"]) + 1 + (1 if data["rows"] and data["totals"] else 0)
        return sum(widths), lines * row + LABEL, 0.0, -lines * row
    k = _scale_k(project, vp)
    if kind == "model":
        bounds = model_view(project, vp["azimuth"], vp["elevation"], 1000.0 / _mm_per(project))["bounds"]
    else:
        bounds = project.drawing_data(vp["view_id"])["bounds"]
    if not bounds:
        return 0.0, 0.0, 0.0, 0.0
    (x0, y0), (x1, y1) = bounds
    ax, ay = vp["anchor"]
    return (x1 - x0) * k, (y1 - y0) * k + LABEL, (x0 - ax) * k, (y0 - ay) * k


def _mm_per(project: Any) -> float:
    from .project import MM_PER
    return MM_PER[project.manifest["unit"]]


def rect_of(project: Any, vp: Mapping[str, Any]) -> Rect:
    w, h, dx, dy = footprint(project, vp)
    return (vp["x"] + dx, vp["y"] + dy, vp["x"] + dx + w, vp["y"] + dy + h)


def _put(project: Any, vp: dict[str, Any], x0: float, y0: float) -> None:
    """Move a viewport so that the rectangle it covers has its lower left corner at (x0, y0)."""
    _, _, dx, dy = footprint(project, vp)
    vp["x"], vp["y"] = round(x0 - dx, 3), round(y0 - dy, 3)


def furniture(sheet: Mapping[str, Any], width: float, height: float) -> list[Rect]:
    """What the paper already carries: the title block and the revision table above it."""
    tw, th = TITLE_BLOCK
    left, bottom = width - FRAME - tw, FRAME
    rows = min(len(sheet.get("revisions", [])), 6)
    out = [(left, bottom, width - FRAME, bottom + th)]
    if rows:
        out.append((left, bottom + th, width - FRAME, bottom + th + REVISION_ROW * (rows + 1)))
    return out


def _hits(a: Rect, b: Rect, gap: float = 0.0) -> bool:
    return a[0] < b[2] + gap and b[0] < a[2] + gap and a[1] < b[3] + gap and b[1] < a[3] + gap


def overlapping(project: Any, sheet: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Pairs of viewports whose rectangles cover one another, as (id, id)."""
    rects = [(vp["id"], rect_of(project, vp)) for vp in sheet.get("viewports", []) if _drawn(project, vp)]
    return [(a, b) for i, (a, ra) in enumerate(rects) for b, rb in rects[i + 1:] if _hits(ra, rb)]


def _drawn(project: Any, vp: Mapping[str, Any]) -> bool:
    """Whether the sheet actually draws this viewport (a viewport whose view, picture or report is gone is reported by the model check and takes no room)."""
    kind = kind_of(vp)
    if kind == "snapshot":
        return vp["id"] in project.snapshots
    if kind == "report":
        return any(r["id"] == vp["report_id"] for r in project.manifest["reports"])
    view = next((v for v in project.manifest["views"] if v["id"] == vp.get("view_id")), None)
    return view is not None and (kind == "model" or view["kind"] == "schedule" or vp["view_id"] in project.drawings)


def _first_fit(size: tuple[float, float], area: Rect, obstacles: list[Rect]) -> tuple[float, float] | None:
    """The top-most, then left-most position where a rectangle of this size lies inside ``area``, clear of every obstacle by ``GAP``."""
    w, h = size
    xs = sorted({area[0]} | {r[2] + GAP for r in obstacles})
    ys = sorted({area[3] - h} | {r[1] - GAP - h for r in obstacles}, reverse=True)
    for y in ys:
        if y < area[1] - 1e-9:
            continue
        for x in xs:
            if x + w > area[2] + 1e-9:
                continue
            if not any(_hits((x, y, x + w, y + h), o, GAP) for o in obstacles):
                return round(x, 3), round(y, 3)
    return None


def usable(sheet: Mapping[str, Any], width: float, height: float) -> Rect:
    return (FRAME + INSET, FRAME + INSET, width - FRAME - INSET, height - FRAME - INSET)


def place_free(project: Any, sheet: dict[str, Any], vp: dict[str, Any]) -> bool:
    """Give a new viewport the first free place on the paper, moving nothing that is already there. False when nothing is free (the viewport then keeps the
    stepped-corner position it has, and the person decides: a larger paper, a smaller scale, or Arrange)."""
    from .project import paper_size
    width, height = paper_size(sheet["paper"])
    w, h, _, _ = footprint(project, vp)
    taken = furniture(sheet, width, height) + [rect_of(project, o) for o in sheet.get("viewports", []) if _drawn(project, o)]
    spot = _first_fit((w, h), usable(sheet, width, height), taken)
    if spot is None:
        return False
    _put(project, vp, *spot)
    return True


def arrange(project: Any, sheet: dict[str, Any], *, mode: str = "auto", rescale: bool = False) -> dict[str, Any]:
    """Lay the viewports out so that all of them are visible and none covers another or the title block.

    ``mode`` ``auto`` moves every viewport except those the person locked or placed by hand; ``all`` also moves the ones placed by hand; a locked viewport never
    moves. The scale of a view is never changed unless ``rescale`` is set, and then only to the nearest smaller scale that makes it fit. Viewports are placed largest
    first, each at the top-most then left-most free place. If something cannot be placed nothing is moved, and the answer says what and why."""
    from .project import SCALES, paper_size
    if mode not in ("auto", "all"):
        raise BadRequest("mode must be 'auto' or 'all'")
    width, height = paper_size(sheet["paper"])
    area = usable(sheet, width, height)
    drawn = [vp for vp in sheet.get("viewports", []) if _drawn(project, vp)]
    fixed = [vp for vp in drawn if vp.get("locked") or (mode == "auto" and vp.get("manual"))]
    moving = [vp for vp in drawn if vp not in fixed]
    placed: list[Rect] = furniture(sheet, width, height) + [rect_of(project, vp) for vp in fixed]
    plan: list[tuple[dict[str, Any], float, float, str | None]] = []
    failed: list[dict[str, Any]] = []
    for vp in sorted(moving, key=lambda v: -(lambda f: f[0] * f[1])(footprint(project, v))):
        trial, rescaled = dict(vp), None
        while True:
            w, h, _, _ = footprint(project, trial)
            spot = _first_fit((w, h), area, placed)
            if spot is not None or not rescale or kind_of(vp) not in ("view", "model") or trial["scale"] not in SCALES or trial["scale"] == SCALES[-1]:
                break
            trial["scale"] = SCALES[SCALES.index(trial["scale"]) + 1]
            rescaled = trial["scale"]
        if spot is None:
            failed.append({"id": vp["id"], "kind": kind_of(vp), "width_mm": round(w, 1), "height_mm": round(h, 1)})
            continue
        placed.append((spot[0], spot[1], spot[0] + w, spot[1] + h))
        plan.append((vp, spot[0], spot[1], rescaled))
    if failed:
        from ..tools.errors import Conflict
        raise Conflict(f"{len(failed)} viewport(s) do not fit on {sheet['paper']} without overlapping: " + ", ".join(f"{f['width_mm']:g} × {f['height_mm']:g} mm" for f in failed),
                       hint="Use a larger paper size, a smaller scale, or arrange again with 'rescale'. Nothing was moved.")
    for vp, x, y, scale in plan:
        if scale:
            vp["scale"] = scale
        _put(project, vp, x, y)
        vp.pop("manual", None)
    return {"moved": [vp["id"] for vp, *_ in plan], "kept": [vp["id"] for vp in fixed], "rescaled": {vp["id"]: s for vp, _, _, s in plan if s}}


def snap(project: Any, sheet: Mapping[str, Any], vp: Mapping[str, Any], x: float, y: float) -> tuple[float, float]:
    """Where a viewport dropped at (x, y) lands once it snaps to the nearest edge of the margin or of another viewport (within ``SNAP`` mm), horizontally and
    vertically on their own."""
    from .project import paper_size
    width, height = paper_size(sheet["paper"])
    w, h, dx, dy = footprint(project, vp)
    area = usable(sheet, width, height)
    others = [rect_of(project, o) for o in sheet.get("viewports", []) if o["id"] != vp["id"] and _drawn(project, o)] + furniture(sheet, width, height)
    xs = [area[0], area[2] - w] + [r[0] for r in others] + [r[2] for r in others] + [r[0] - w for r in others] + [r[2] - w for r in others]
    ys = [area[1], area[3] - h] + [r[1] for r in others] + [r[3] for r in others] + [r[1] - h for r in others] + [r[3] - h for r in others]
    nx, ny = x + dx, y + dy
    near = lambda value, options: min((c for c in options if abs(c - value) <= SNAP), key=lambda c: abs(c - value), default=value)      # noqa: E731
    nx, ny = near(nx, xs), near(ny, ys)
    return round(nx - dx, 3), round(ny - dy, 3)


ALIGNMENTS = ("left", "right", "top", "bottom", "centre-horizontal", "centre-vertical", "distribute-horizontal", "distribute-vertical")


def align(project: Any, sheet: dict[str, Any], ids: list[str], how: str) -> list[str]:
    """Align the chosen viewports to the first one (or, for ``distribute``, space them evenly between the outermost two). Locked viewports stay where they are."""
    if how not in ALIGNMENTS:
        raise BadRequest(f"how must be one of: {', '.join(ALIGNMENTS)}")
    chosen = [vp for vp in sheet.get("viewports", []) if vp["id"] in ids]
    if len(chosen) != len(set(ids)) or len(chosen) < 2 or (how.startswith("distribute") and len(chosen) < 3):
        raise BadRequest("Choose at least two viewports on this sheet (three to distribute)")
    ref = rect_of(project, chosen[0])
    moved = []
    if how.startswith("distribute"):
        axis = 0 if how.endswith("horizontal") else 1
        ordered = sorted(chosen, key=lambda v: rect_of(project, v)[axis])
        sizes = [rect_of(project, v)[axis + 2] - rect_of(project, v)[axis] for v in ordered]
        first, last = rect_of(project, ordered[0]), rect_of(project, ordered[-1])
        span = last[axis + 2] - first[axis]
        gap = (span - sum(sizes)) / (len(ordered) - 1)
        if gap < 0:
            raise BadRequest("The viewports are too wide to spread out without overlapping")
        cursor = first[axis]
        for vp, size in zip(ordered, sizes):
            r = rect_of(project, vp)
            if not vp.get("locked") and abs(r[axis] - cursor) > 1e-6:
                _put(project, vp, cursor if axis == 0 else r[0], cursor if axis == 1 else r[1])
                vp["manual"] = True
                moved.append(vp["id"])
            cursor += size + gap
        return moved
    for vp in chosen[1:]:
        r = rect_of(project, vp)
        x0, y0 = {"left": (ref[0], r[1]), "right": (ref[2] - (r[2] - r[0]), r[1]), "top": (r[0], ref[3] - (r[3] - r[1])), "bottom": (r[0], ref[1]),
                  "centre-horizontal": ((ref[0] + ref[2] - (r[2] - r[0])) / 2, r[1]), "centre-vertical": (r[0], (ref[1] + ref[3] - (r[3] - r[1])) / 2)}[how]
        if not vp.get("locked") and (abs(x0 - r[0]) > 1e-6 or abs(y0 - r[1]) > 1e-6):
            _put(project, vp, x0, y0)
            vp["manual"] = True
            moved.append(vp["id"])
    return moved
