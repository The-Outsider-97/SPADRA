"""Dimension-aware 2D-to-BIM reconstruction.

From a dimensioned plan (a raster image, a vector PDF or an existing SDRW drawing) to walls, openings and spaces whose sizes are *the dimensions written on the drawing*,
not the pixels: the printed values are read (OCR for rasters, text objects for PDFs), associated with the dimension lines that carry them, used to calibrate the scale, and
finally imposed on the recovered geometry as constraints solved by the shared constraint engine. Everything the pipeline decides carries its evidence and a confidence, and
every step can be corrected by the person and re-run (:func:`run` takes ``corrections``); nothing becomes a BIM element until :func:`build` is executed as a command.

Stages (each returns plain data, so each can be tested and shown on its own):
  1. acquire        raster / PDF / SDRW  ->  line segments, numeric text, page size
  2. dimensions     text + dimension line (+ extension lines, ticks or arrowheads)  ->  a measured length in drawing units
  3. calibrate      value / length per dimension  ->  scale (median, outliers by median absolute deviation) and its spread
  4. walls          pairs of parallel lines at a wall-like distance  ->  centre lines and thickness; gaps  ->  openings
  5. align          printed dimensions as constraints (``offset`` between parallel walls, face or centre) solved with the constraint engine; conflicts localised
  6. spaces         closed regions between the walls
  7. build          SDRW lines and/or SPADRA walls, doors, windows and rooms, with provenance and confidence on every element

Honest limits (also in ``capabilities()`` and the report): walls are recovered as straight double lines; dimension alignment covers axis-aligned walls (oblique walls are
scaled but not dimension-solved); a curved wall, a door swing, hatching and solid-filled (poché) walls are not recognised; confidence is a transparent evidence score, *not* a
probability (its calibration against known errors is measured by ``tests/evaluate_reconstruction.py``); OCR depends on Tesseract being installed.
"""
from __future__ import annotations

import io
import math
import re
import statistics
from typing import Any, Mapping, Sequence

from ..tools.errors import BadRequest
from src.utils.spatial_formats.common import constraint_engine as CE
from src.utils.spatial_formats.common.errors import SpatialFormatError

from . import elements as E
from .utils.spadra_helpers import MM_PER

DEFAULTS: dict[str, Any] = {
    "unit": "auto",                  # unit of the printed numbers: "mm", "cm", "m", or "auto" (a number with a decimal point below 100 is metres, otherwise millimetres)
    "wall_thickness_mm": (50.0, 600.0),   # the gap between two parallel lines that makes them a wall
    "min_opening_mm": 450.0,         # a gap in a wall narrower than this is a joint, not an opening
    "max_opening_mm": 4500.0,
    "outlier_mads": 3.0,             # a dimension whose scale differs from the median by more than this many robust sigmas is an outlier
    "min_room_m2": 1.0,
    "ocr": True,
    "hough_min_len_px": 12, "hough_gap_px": 4, "merge_gap_px": 6.0,
    "snap_px": 4.0,                  # how far (pixels) two lines may be apart and still be the same line / the same point
    "corrections": {},
}
ANG_TOL = math.radians(1.5)
NUMBER = re.compile(r"^\d{1,6}([.,]\d{1,3})?$")


# ----------------------------------------------------------------------------------------------------------------------- capabilities
def capabilities() -> dict[str, Any]:
    out: dict[str, Any] = {"sources": {}, "limits": [
        "walls are straight double lines; curved walls, door swings, hatching and solid-filled walls are not recognised",
        "dimension alignment solves axis-aligned walls; oblique walls are scaled by the calibrated scale but not dimension-solved",
        "openings are classified from evidence only: a glazing line across the gap makes a window, otherwise a door is assumed (and the confidence says so)",
        "confidence is an evidence score, not a probability; see tests/evaluate_reconstruction.py for its measured calibration"]}
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
        import skimage  # noqa: F401
        raster = {"available": True}
    except ImportError as exc:
        raster = {"available": False, "reason": f"{exc.name or exc} is not installed (needs numpy, OpenCV and scikit-image)"}
    try:
        pytesseract = _pytesseract()
        pytesseract.get_tesseract_version()
        ocr = {"available": True}
    except Exception as exc:
        ocr = {"available": False, "reason": "Tesseract OCR is not available: printed dimensions in a raster image cannot be read (a vector PDF or an SDRW drawing does not need it)"}
        ocr["detail"] = str(exc)[:120]
    try:
        import pdfplumber  # noqa: F401
        pdf = {"available": True}
    except ImportError:
        pdf = {"available": False, "reason": "pdfplumber is not installed"}
    out["sources"] = {"raster": {**raster, "needs_ocr": ocr}, "pdf": pdf, "sdrw": {"available": True}}
    out["ocr"] = ocr
    return out


def _pytesseract() -> Any:
    """pytesseract, pointed at the Tesseract program: ``TESSERACT_CMD`` if set, else the program on PATH, else the usual Windows install folders (the installer does not add itself to PATH)."""
    import os
    import shutil
    import pytesseract
    chosen = os.environ.get("TESSERACT_CMD")
    if not chosen and not shutil.which("tesseract"):
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
            candidate = os.path.join(base, "Tesseract-OCR", "tesseract.exe") if base else ""
            if candidate and os.path.isfile(candidate):
                chosen = candidate
                break
    if chosen:
        pytesseract.pytesseract.tesseract_cmd = chosen
    return pytesseract


# ----------------------------------------------------------------------------------------------------------------------- geometry helpers
def _len(a: Sequence[float], b: Sequence[float]) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _theta(a: Sequence[float], b: Sequence[float]) -> float:
    t = math.atan2(b[1] - a[1], b[0] - a[0]) % math.pi
    return 0.0 if t > math.pi - 1e-12 else t


def _rho(p: Sequence[float], theta: float) -> float:
    return -p[0] * math.sin(theta) + p[1] * math.cos(theta)


def _along(p: Sequence[float], theta: float) -> float:
    return p[0] * math.cos(theta) + p[1] * math.sin(theta)


def _angdiff(a: float, b: float) -> float:
    d = abs(a - b) % math.pi
    return min(d, math.pi - d)


def merge_segments(segs: Sequence[Sequence[Sequence[float]]], *, ang_tol: float = ANG_TOL, off_tol: float = 2.5, gap: float = 6.0) -> list[dict[str, Any]]:
    """Merge collinear, overlapping or nearly touching segments into one. Near-axis lines are snapped exactly onto the axis."""
    items = []
    for a, b in segs:
        if _len(a, b) < 1e-9:
            continue
        t = _theta(a, b)
        items.append((t, a, b))
    items.sort(key=lambda s: s[0])
    clusters: list[list[tuple[float, Any, Any]]] = []
    for it in items:                                                         # cluster by angle (wrapping at pi)
        if clusters and _angdiff(it[0], clusters[-1][0][0]) <= ang_tol:
            clusters[-1].append(it)
        else:
            clusters.append([it])
    if len(clusters) > 1 and _angdiff(clusters[0][0][0], clusters[-1][-1][0]) <= ang_tol:
        clusters[0].extend(clusters.pop())
    out: list[dict[str, Any]] = []
    for cl in clusters:
        theta = _circular_mean([c[0] for c in cl], [_len(c[1], c[2]) for c in cl])
        for axis in (0.0, math.pi / 2):
            if _angdiff(theta, axis) <= ANG_TOL:
                theta = axis
        by_rho = sorted(cl, key=lambda c: _rho(c[1], theta) + _rho(c[2], theta))
        groups: list[list[Any]] = []
        for c in by_rho:
            r = (_rho(c[1], theta) + _rho(c[2], theta)) / 2
            if groups and abs(r - groups[-1][0]) <= off_tol:
                groups[-1][1].append(c)
                groups[-1][0] = sum((_rho(x[1], theta) + _rho(x[2], theta)) / 2 for x in groups[-1][1]) / len(groups[-1][1])
            else:
                groups.append([r, [c]])
        for rho_c, members in groups:
            spans = sorted(sorted([_along(m[1], theta), _along(m[2], theta)]) for m in members)
            merged: list[list[float]] = []
            for lo, hi in spans:
                if merged and lo - merged[-1][1] <= gap:
                    merged[-1][1] = max(merged[-1][1], hi)
                else:
                    merged.append([lo, hi])
            for lo, hi in merged:
                a = (lo * math.cos(theta) - rho_c * math.sin(theta), lo * math.sin(theta) + rho_c * math.cos(theta))
                b = (hi * math.cos(theta) - rho_c * math.sin(theta), hi * math.sin(theta) + rho_c * math.cos(theta))
                out.append({"a": [a[0], a[1]], "b": [b[0], b[1]], "theta": theta, "rho": rho_c, "lo": lo, "hi": hi, "length": hi - lo})
    out.sort(key=lambda s: (round(s["theta"], 3), round(s["rho"], 1), s["lo"]))
    for i, s in enumerate(out):
        s["id"] = f"s{i}"
    return out


def _circular_mean(angles: Sequence[float], weights: Sequence[float]) -> float:
    x = sum(w * math.cos(2 * a) for a, w in zip(angles, weights))
    y = sum(w * math.sin(2 * a) for a, w in zip(angles, weights))
    return (math.atan2(y, x) / 2) % math.pi


# ----------------------------------------------------------------------------------------------------------------------- 1. acquisition
def _number(token: str) -> float | None:
    if not NUMBER.match(token):
        return None
    return float(token.replace(",", "."))


def ocr_numbers(gray: Any, *, min_conf: float = 0.0) -> list[dict[str, Any]]:
    """Numeric tokens in a grayscale image with their boxes, read upright and rotated a quarter turn (vertical dimensions are usually written along their line)."""
    import cv2
    pytesseract = _pytesseract()
    H, W = gray.shape
    found: list[dict[str, Any]] = []
    config = "--psm 11"                                                      # no character whitelist: it makes the LSTM engine return nothing on small drawing text
    for rotation in (0, 90):
        img = gray if rotation == 0 else cv2.rotate(gray, cv2.ROTATE_90_CLOCKWISE)
        for zoom in (1.0, 1.5):                                              # native size first; a 1.5x pass catches small text the first one misses
            up = img if zoom == 1.0 else cv2.resize(img, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_CUBIC)
            data = pytesseract.image_to_data(up, config=config, output_type=pytesseract.Output.DICT)
            for i, token in enumerate(data["text"]):
                token = token.strip()
                value = _number(token)
                conf = float(data["conf"][i])
                if value is None or conf < max(0.0, min_conf * 100):
                    continue
                x, y, w, h = (data["left"][i] / zoom, data["top"][i] / zoom, data["width"][i] / zoom, data["height"][i] / zoom)
                short, long_ = (min(w, h), max(w, h))
                if short < 5 or long_ > (1.1 * len(token) + 1.5) * short:       # not the shape of printed digits: a stray line or a speck read as a number
                    continue
                if rotation == 0:
                    box = [x, y, x + w, y + h]
                else:                                                        # (x', y') in the rotated image  ->  x = y', y = H - 1 - x'
                    box = [y, H - 1 - (x + w), y + h, H - 1 - x]
                found.append({"text": token, "value": value, "bbox": [round(v, 2) for v in box], "conf": conf / 100.0, "rotation": rotation, "source": "ocr"})
    found.sort(key=lambda t: -t["conf"])
    kept: list[dict[str, Any]] = []
    for t in found:                                                           # the same number seen in both passes (or twice): keep the surer reading
        cx, cy = (t["bbox"][0] + t["bbox"][2]) / 2, (t["bbox"][1] + t["bbox"][3]) / 2
        if any(k["bbox"][0] - 2 <= cx <= k["bbox"][2] + 2 and k["bbox"][1] - 2 <= cy <= k["bbox"][3] + 2 for k in kept):
            continue
        kept.append(t)
    for i, t in enumerate(sorted(kept, key=lambda t: (t["bbox"][1], t["bbox"][0]))):
        t["id"] = f"t{i}"
    return sorted(kept, key=lambda t: t["id"])


def _axis_strokes(bw: Any, minlen: int) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Horizontal and vertical strokes of a binary image at least ``minlen`` pixels long: an opening with a one-pixel-wide bar keeps exactly the pixels on such a run; each connected
    stroke is a line through the middle of its thickness. A stroke much thicker than a drawn line (a filled area) is not a line and is skipped."""
    import cv2
    import numpy as np
    out = []
    closed = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))   # one-pixel breaks of scan noise
    for horizontal in (True, False):
        k = cv2.getStructuringElement(cv2.MORPH_RECT, (minlen, 1) if horizontal else (1, minlen))
        op = cv2.morphologyEx(closed, cv2.MORPH_OPEN, k)
        n, _lab, st, _ = cv2.connectedComponentsWithStats((op > 0).astype(np.uint8), connectivity=8)
        for i in range(1, n):
            x, y, w, h = (int(st[i, c]) for c in (cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP, cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT))
            thick = h if horizontal else w
            if thick > 10:
                continue
            if horizontal:
                out.append(((float(x), y + (h - 1) / 2.0), (float(x + w - 1), y + (h - 1) / 2.0)))
            else:
                out.append(((x + (w - 1) / 2.0, float(y)), (x + (w - 1) / 2.0, float(y + h - 1))))
    return out


def acquire_raster(data: bytes, opts: Mapping[str, Any]) -> dict[str, Any]:
    import cv2
    import numpy as np
    from skimage.morphology import skeletonize
    arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
    if arr is None:
        raise BadRequest("The image could not be decoded", hint="use PNG, JPEG or TIFF")
    H, W = arr.shape
    if H * W > 60_000_000:
        raise BadRequest("The image is too large to process", hint="reduce it below 60 megapixels")
    diags: list[dict[str, Any]] = []
    texts: list[dict[str, Any]] = []
    smooth = cv2.medianBlur(arr, 3)                                          # grain and speckle (scans, JPEG) before the binarisation
    if opts["ocr"]:
        if capabilities()["ocr"]["available"]:
            texts = ocr_numbers(arr)
            if len(texts) < 2:                                               # nothing legible in the raw pixels: read a cleaned, binarised copy
                cleaned = 255 - cv2.threshold(smooth, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
                texts = ocr_numbers(cleaned)
        else:
            diags.append({"code": "ocr.unavailable", "severity": "error", "message": capabilities()["ocr"]["reason"], "hint": "attach a vector PDF, or give the scale with a correction"})
    bw = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    for t in texts:                                                           # numbers are not line-work
        x0, y0, x1, y1 = (int(v) for v in t["bbox"])
        pad = 1
        bw[max(0, y0 - pad):min(H, y1 + pad), max(0, x0 - pad):min(W, x1 + pad)] = 0
    n, labels, stats, _ = cv2.connectedComponentsWithStats((bw > 0).astype(np.uint8), connectivity=8)
    for k in range(1, n):
        if stats[k, cv2.CC_STAT_AREA] < 12:
            bw[labels == k] = 0
    minlen = int(opts["hough_min_len_px"])
    raw = _axis_strokes(bw, minlen)                                          # horizontal and vertical lines: exact, from run lengths (the probabilistic Hough transform fragments them)
    skeleton = (skeletonize(bw > 0) * 255).astype(np.uint8)
    lines = cv2.HoughLinesP(skeleton, 1, math.pi / 720, threshold=minlen, minLineLength=minlen, maxLineGap=int(opts["hough_gap_px"]))
    for l in (np.asarray(lines).reshape(-1, 4) if lines is not None else []):
        a, b = (float(l[0]), float(l[1])), (float(l[2]), float(l[3]))
        if _angdiff(_theta(a, b), 0.0) > math.radians(3) and _angdiff(_theta(a, b), math.pi / 2) > math.radians(3):
            raw.append((a, b))                                               # oblique lines (ticks, arrowheads, angled walls)
    segs = merge_segments(raw, gap=float(opts["merge_gap_px"]), off_tol=float(opts["snap_px"]) * 0.6)
    return {"kind": "raster", "width": W, "height": H, "unit": "px", "y_down": True, "segments": segs, "texts": texts, "diagnostics": diags}


def acquire_pdf(data: bytes, opts: Mapping[str, Any]) -> dict[str, Any]:
    import pdfplumber
    segs: list[tuple[tuple[float, float], tuple[float, float]]] = []
    texts: list[dict[str, Any]] = []
    diags: list[dict[str, Any]] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        if not pdf.pages:
            raise BadRequest("The PDF has no pages")
        page = pdf.pages[0]
        if len(pdf.pages) > 1:
            diags.append({"code": "pdf.pages", "severity": "warning", "message": f"the PDF has {len(pdf.pages)} pages; only the first is read"})
        for ln in page.lines:
            segs.append(((float(ln["x0"]), float(ln["top"])), (float(ln["x1"]), float(ln["bottom"]))))
        for r in page.rects:
            x0, y0, x1, y1 = float(r["x0"]), float(r["top"]), float(r["x1"]), float(r["bottom"])
            segs += [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]
        for c in page.curves:
            pts = c.get("pts") or []
            segs += [((float(a[0]), float(a[1])), (float(b[0]), float(b[1]))) for a, b in zip(pts, pts[1:])]
        W, H = float(page.width), float(page.height)
        upright = [c for c in page.chars if c.get("upright", True)]
        words = page.extract_words(keep_blank_chars=False, use_text_flow=False)
        for w in words:
            value = _number(w["text"].strip())
            if value is not None and all(c.get("upright", True) for c in page.crop((w["x0"], w["top"], w["x1"], w["bottom"])).chars):
                texts.append({"text": w["text"].strip(), "value": value, "bbox": [float(w["x0"]), float(w["top"]), float(w["x1"]), float(w["bottom"])], "conf": 1.0, "rotation": 0, "source": "pdf"})
        rotated = [c for c in page.chars if not c.get("upright", True)]
        rotated.sort(key=lambda c: (round(c["x0"] / 2), -c["top"]))              # text running upwards: reading order is decreasing 'top'
        run: list[Any] = []
        for c in rotated + [None]:
            if run and (c is None or abs(c["x0"] - run[-1]["x0"]) > 2.0):
                s = "".join(r["text"] for r in run).strip()
                value = _number(s)
                if value is not None:
                    texts.append({"text": s, "value": value, "bbox": [min(r["x0"] for r in run), min(r["top"] for r in run), max(r["x1"] for r in run), max(r["bottom"] for r in run)],
                                  "conf": 1.0, "rotation": 90, "source": "pdf"})
                run = []
            if c is not None:
                run.append(c)
        del upright
    if not segs:
        diags.append({"code": "pdf.no_vectors", "severity": "error", "message": "the PDF page has no vector line-work (it may be a scanned image)", "hint": "export the scan as an image and use the raster route"})
    for i, t in enumerate(sorted(texts, key=lambda t: (t["bbox"][1], t["bbox"][0]))):
        t["id"] = f"t{i}"
    merged = merge_segments(segs, off_tol=0.6, gap=1.0)
    return {"kind": "pdf", "width": W, "height": H, "unit": "pt", "y_down": True, "segments": merged, "texts": sorted(texts, key=lambda t: t["id"]), "diagnostics": diags}


def acquire_sdrw(project: Any, view: str) -> dict[str, Any]:
    """Line-work of an existing SDRW drawing (converted to millimetres, y flipped to the pipeline's y-down), its numeric text entities (read exactly, no OCR) and its linear
    dimension entities (whose value is their own measured length)."""
    doc = project.drawing(view)
    f = MM_PER[project.manifest["unit"]]
    segs: list[Any] = []
    texts: list[dict[str, Any]] = []
    sdims: list[dict[str, Any]] = []
    ids = doc.query_ids(limit=10000)
    diags = [{"code": "sdrw.truncated", "severity": "warning", "message": "the drawing has 10000 or more entities; only the first 10000 are read"}] if len(ids) >= 10000 else []
    for eid in ids:
        e = doc.get(eid)
        g = e["geometry"]
        if e["kind"] == "line":
            segs.append(((g["start"][0] * f, -g["start"][1] * f), (g["end"][0] * f, -g["end"][1] * f)))
        elif e["kind"] == "polyline":
            pts = [[p[0] * f, -p[1] * f] for p in g["points"]]
            loop = pts + ([pts[0]] if g.get("closed") else [])
            segs += list(zip(loop, loop[1:]))
        elif e["kind"] == "text":
            raw = str(g.get("text", "")).strip()
            value = _number(raw)
            if value is None:
                continue
            h = float(g.get("height", 0)) * f
            w = 0.6 * h * len(raw)
            x, y = g["position"][0] * f, -g["position"][1] * f
            if g.get("halign") == "center":
                x -= w / 2 if abs(math.sin(float(g.get("rotation", 0)))) < 0.5 else 0
            quarter = round(float(g.get("rotation", 0.0)) / (math.pi / 2)) % 4
            if quarter == 0:
                box, rot = [x, y - h, x + w, y], 0
            elif quarter == 1:
                box, rot = [x - h, y - w, x, y], 90
            else:
                continue                                                      # upside-down or reading downwards: not read
            texts.append({"text": raw, "value": value, "bbox": [round(v, 3) for v in box], "conf": 1.0, "rotation": rot, "source": "sdrw", "entity": eid})
        elif e["kind"] == "dimension" and g.get("dim_type") in ("linear", "aligned"):
            sdims.append({"entity": eid, "p": [g["p1"][0] * f, -g["p1"][1] * f], "q": [g["p2"][0] * f, -g["p2"][1] * f]})
    for i, t in enumerate(sorted(texts, key=lambda t: (t["bbox"][1], t["bbox"][0]))):
        t["id"] = f"t{i}"
    return {"kind": "sdrw", "width": None, "height": None, "unit": "mm", "y_down": True, "segments": merge_segments(segs, off_tol=0.5, gap=1.0),
            "texts": sorted(texts, key=lambda t: t["id"]), "sdrw_dimensions": sdims, "diagnostics": diags}


# ----------------------------------------------------------------------------------------------------------------------- 2. dimensions
def _point_on(seg: Mapping[str, Any], t: float) -> tuple[float, float]:
    return (t * math.cos(seg["theta"]) - seg["rho"] * math.sin(seg["theta"]), t * math.sin(seg["theta"]) + seg["rho"] * math.cos(seg["theta"]))


def _dist_to_seg(p: Sequence[float], o: Mapping[str, Any]) -> tuple[float, float]:
    t = max(o["lo"], min(o["hi"], _along(p, o["theta"])))
    return math.dist(p, _point_on(o, t)), t


def dimension_stops(line: Mapping[str, Any], segments: Sequence[Mapping[str, Any]], tol: float) -> list[dict[str, Any]]:
    """Positions along a candidate dimension line where something closes a measurement: an extension line crossing or touching it, a 45-degree tick, or an arrowhead (two short
    segments meeting on the line). Several marks at the same position (a tick on an extension line) are one stop."""
    th = line["theta"]
    stops: list[dict[str, Any]] = []
    short_arrows: list[tuple[float, Mapping[str, Any]]] = []
    for o in segments:
        if o["id"] == line["id"]:
            continue
        d = _angdiff(o["theta"], th)
        # the point of o closest to the dimension line
        ends = [o["a"], o["b"]]
        best = None
        for pt in ends + [_point_on(o, (o["lo"] + o["hi"]) / 2)]:
            dist = abs(_rho(pt, th) - line["rho"])
            if best is None or dist < best[0]:
                best = (dist, pt)
        if d > math.radians(80):                                             # extension line: perpendicular, must reach the line
            # crossing point of the two infinite lines
            x = _cross_param(o, line)
            if x is None:
                continue
            reach = min(abs(_rho(o["a"], th) - line["rho"]), abs(_rho(o["b"], th) - line["rho"]))
            straddles = (_rho(o["a"], th) - line["rho"]) * (_rho(o["b"], th) - line["rho"]) <= 0
            if (straddles or reach <= tol) and line["lo"] - tol <= x <= line["hi"] + tol and o["length"] > 2 * tol:
                da, db = _rho(o["a"], th) - line["rho"], _rho(o["b"], th) - line["rho"]
                # an extension line that ends at (or a little past) the dimension line closes the measurement; one that runs far across it is another dimension's line
                stops.append({"pos": x, "kind": "ext", "id": o["id"], "w": 3, "rank": 2 if straddles else 1, "closing": min(abs(da), abs(db)) <= 3.5 * tol})
        elif math.radians(25) <= d <= math.radians(65) and o["length"] <= 0.5 * line["length"] + 1e-9:
            x = _cross_param(o, line)
            straddles = (_rho(o["a"], th) - line["rho"]) * (_rho(o["b"], th) - line["rho"]) <= 0
            if x is not None and straddles and line["lo"] - tol <= x <= line["hi"] + tol:
                stops.append({"pos": x, "kind": "tick", "id": o["id"], "w": 2, "rank": 3, "closing": True})
        elif math.radians(6) <= d <= math.radians(35) and o["length"] <= 0.25 * line["length"] + 1e-9 and best[0] <= tol:
            short_arrows.append((_along(best[1], th), o))
    short_arrows.sort(key=lambda x: x[0])
    used: set[str] = set()
    for i, (pa, oa) in enumerate(short_arrows):                              # arrowhead = two short segments meeting at one point of the line
        for pb, ob in short_arrows[i + 1:]:
            if pb - pa > tol:
                break
            if oa["id"] not in used and ob["id"] not in used:
                used |= {oa["id"], ob["id"]}
                stops.append({"pos": (pa + pb) / 2, "kind": "arrow", "id": f"{oa['id']}+{ob['id']}", "parts": [oa["id"], ob["id"]], "w": 1, "rank": 3, "closing": True})
    stops.sort(key=lambda s: s["pos"])
    merged: list[dict[str, Any]] = []
    for st in stops:
        if merged and st["pos"] - merged[-1]["pos"] <= 1.5 * tol:
            m = merged[-1]
            m["members"].append(st)
            if st["w"] > m["w"]:
                m["pos"], m["w"] = st["pos"], st["w"]
        else:
            merged.append({"pos": st["pos"], "w": st["w"], "members": [st]})
    for m in merged:
        m["closing"] = any(x["closing"] for x in m["members"])
        m["rank"] = max(x["rank"] for x in m["members"] if x["closing"]) if m["closing"] else 0
    return [m for m in merged if m["closing"]]


def _cross_param(o: Mapping[str, Any], line: Mapping[str, Any]) -> float | None:
    """Coordinate along ``line`` where the infinite line ``o`` crosses it."""
    A = [[-math.sin(o["theta"]), math.cos(o["theta"])], [-math.sin(line["theta"]), math.cos(line["theta"])]]
    det = A[0][0] * A[1][1] - A[0][1] * A[1][0]
    if abs(det) < 1e-9:
        return None
    b = [o["rho"], line["rho"]]
    x = (b[0] * A[1][1] - A[0][1] * b[1]) / det
    y = (A[0][0] * b[1] - b[0] * A[1][0]) / det
    return _along((x, y), line["theta"])


def _bridge_text(segments: Sequence[Mapping[str, Any]], theta: float, ua: float, ub: float, tol: float) -> list[dict[str, Any]]:
    """The dimension line is interrupted where its text sits (the text is removed from the line-work first). Join collinear segments that end at the two sides of a text span
    [ua, ub] along its direction; other segments are returned unchanged."""
    out = list(segments)
    cand = [s for s in segments if _angdiff(s["theta"], theta) <= math.radians(1.0)]
    for a in cand:
        if not (ua - 3 * tol <= a["hi"] <= ub):
            continue
        for b in cand:
            if b is a or abs(b["rho"] - a["rho"]) > 1.5 or not (ua <= b["lo"] <= ub + 3 * tol):
                continue
            lo, hi = a["lo"], b["hi"]
            th, rho = a["theta"], (a["rho"] + b["rho"]) / 2
            virt = {"id": a["id"], "theta": th, "rho": rho, "lo": lo, "hi": hi, "length": hi - lo, "bridged": [a["id"], b["id"]],
                    "a": [lo * math.cos(th) - rho * math.sin(th), lo * math.sin(th) + rho * math.cos(th)], "b": [hi * math.cos(th) - rho * math.sin(th), hi * math.sin(th) + rho * math.cos(th)]}
            out = [x for x in out if x["id"] not in (a["id"], b["id"])] + [virt]
            return out
    return out


def find_dimensions(segments: Sequence[Mapping[str, Any]], texts: Sequence[Mapping[str, Any]], tol: float) -> list[dict[str, Any]]:
    """Associate each numeric text with the dimension line that carries it and measure between the two closing marks on either side of the text (chained dimensions share one
    line; each text measures only its own span). A dimension line is long, parallel to the text, close to it and closed on both sides by an extension line, tick or arrowhead."""
    found: list[dict[str, Any]] = []
    used_spans: set[tuple[str, float, float]] = set()
    for t in sorted(texts, key=lambda t: -t["conf"]):
        x0, y0, x1, y1 = t["bbox"]
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        h = (y1 - y0) if t["rotation"] == 0 else (x1 - x0)
        want = 0.0 if t["rotation"] == 0 else math.pi / 2
        best = None
        ua, ub = sorted((_along((x0, y0), want), _along((x1, y1), want)))
        for s in _bridge_text(segments, want, ua, ub, tol):
            if _angdiff(s["theta"], want) > math.radians(5) or s["length"] < 3.0 * h:
                continue
            perp = abs(_rho((cx, cy), s["theta"]) - s["rho"])
            u = _along((cx, cy), s["theta"])
            if perp > 3.0 * h or not (s["lo"] <= u <= s["hi"]):
                continue
            stops = dimension_stops(s, segments, tol)
            left, right = ([st for st in stops if (st["pos"] <= u if side == 0 else st["pos"] >= u)] for side in (0, 1))
            if left:                                                         # the strongest kind of closing mark on each side wins (tick/arrowhead > crossing extension line > touching one); nearest within it
                best_rank = max(st["rank"] for st in left)
                left = [st for st in left if st["rank"] == best_rank]
            if right:
                best_rank = max(st["rank"] for st in right)
                right = [st for st in right if st["rank"] == best_rank]
            if not left or not right:
                continue
            L, Rt = left[-1], right[0]
            if Rt["pos"] - L["pos"] < 4 * tol:
                continue
            score = (L["w"] + Rt["w"]) - 0.01 * perp / max(h, 1)
            if best is None or score > best[0]:
                best = (score, s, L, Rt, perp)
        if best is None:
            continue
        _, s, L, Rt, perp = best
        key = (s["id"], round(L["pos"]), round(Rt["pos"]))
        if key in used_spans:
            continue
        used_spans.add(key)
        p, q = _point_on(s, L["pos"]), _point_on(s, Rt["pos"])
        ids = [s["id"]] + [m["id"] for st in (L, Rt) for m in st["members"]]
        found.append({"text_id": t["id"], "text": t["text"], "raw_value": t["value"], "ocr_conf": t["conf"], "line": s["id"], "p": list(p), "q": list(q), "length_px": Rt["pos"] - L["pos"],
                      "axis": "x" if _angdiff(s["theta"], 0.0) < ANG_TOL else "y" if _angdiff(s["theta"], math.pi / 2) < ANG_TOL else "oblique",
                      "terminators": {"extension_lines": sum(any(m["kind"] == "ext" for m in st["members"]) for st in (L, Rt)),
                                      "marks": sum(any(m["kind"] in ("tick", "arrow") for m in st["members"]) for st in (L, Rt))},
                      "parts": ids, "text_offset_px": round(perp, 2)})
    for i, d in enumerate(sorted(found, key=lambda d: (d["axis"], d["p"][0], d["p"][1]))):
        d["id"] = f"d{i}"
    return sorted(found, key=lambda d: d["id"])


def to_mm(raw: float, text: str, unit: str) -> tuple[float, str]:
    if unit == "m":
        return raw * 1000.0, "m"
    if unit == "cm":
        return raw * 10.0, "cm"
    if unit == "mm":
        return raw, "mm"
    return (raw * 1000.0, "m") if ("." in text or "," in text) and raw < 100 else (raw, "mm")


# ----------------------------------------------------------------------------------------------------------------------- 3. calibration
def calibrate(dims: list[dict[str, Any]], opts: Mapping[str, Any], fixed_scale: float | None = None) -> dict[str, Any]:
    """Scale (mm per drawing unit) from the printed dimensions: the median of value/length, outliers by median absolute deviation, then the ratio of sums over the inliers."""
    diags: list[dict[str, Any]] = []
    for d in dims:
        d["value_mm"], d["unit_read"] = (d["value_override"], "corrected") if d.get("value_override") else to_mm(d["raw_value"], d["text"], opts["unit"])
        d["scale"] = d["value_mm"] / d["length_px"] if d["length_px"] > 0 else None
        d.setdefault("status", "candidate")
    usable = [d for d in dims if d["scale"] and d["status"] != "ignored"]
    if fixed_scale is not None:
        for d in usable:
            d["status"] = "used"
            d["scale_deviation"] = d["scale"] / fixed_scale - 1.0
        return {"mm_per_unit": fixed_scale, "method": "given by the user", "n_dimensions": len(dims), "n_inliers": len(usable), "relative_spread": None, "diagnostics": diags}
    if not usable:
        diags.append({"code": "scale.none", "severity": "error", "message": "no dimension could be read, so the scale is unknown", "hint": "give the scale as a correction (mm per pixel), or fix the dimension values"})
        return {"mm_per_unit": None, "method": "none", "n_dimensions": 0, "n_inliers": 0, "relative_spread": None, "diagnostics": diags}
    med = statistics.median(d["scale"] for d in usable)
    rel = [d["scale"] / med - 1.0 for d in usable]
    centre = statistics.median(rel)
    mad = statistics.median(abs(r - centre) for r in rel)
    sigma = 1.4826 * mad
    inliers = []
    for d, r in zip(usable, rel):
        quant = 2.0 / max(d["length_px"], 1.0)                                  # two pixels of line-position uncertainty
        limit = max(float(opts["outlier_mads"]) * sigma, quant)
        d["scale_deviation"] = r - centre
        d["scale_sigma"] = max(sigma, quant)
        if abs(r - centre) <= limit:
            inliers.append(d)
            d["status"] = "used"
        else:
            d["status"] = "outlier"
    if len(usable) < 3:
        diags.append({"code": "scale.few", "severity": "warning", "message": f"only {len(usable)} dimension(s) were read: the scale has no independent cross-check", "hint": "a wrong reading would not be noticed"})
    scale = sum(d["value_mm"] for d in inliers) / sum(d["length_px"] for d in inliers)
    spread = statistics.pstdev([d["scale"] / scale - 1.0 for d in inliers]) if len(inliers) > 1 else None
    for d in dims:
        if d.get("status") == "outlier":
            diags.append({"code": "dimension.outlier", "severity": "warning", "dimension": d["id"],
                          "message": f"dimension '{d['text']}' implies a scale {100 * (d['scale'] / scale - 1):+.1f} % from the others: it is not used for calibration", "hint": "check the OCR reading or correct its value"})
    return {"mm_per_unit": scale, "method": "median / MAD over %d dimension(s)" % len(inliers), "n_dimensions": len(dims), "n_inliers": len(inliers), "relative_spread": spread, "diagnostics": diags}


# ----------------------------------------------------------------------------------------------------------------------- 4. walls and openings
def _cover(used: list[tuple[float, float]], lo: float, hi: float) -> float:
    """Length of [lo, hi] already inside the used intervals."""
    got = 0.0
    for a, b in sorted(used):
        got += max(0.0, min(hi, b) - max(lo, a))
    return got


def pair_walls(segs: Sequence[Mapping[str, Any]], ignore: set[str], scale: float, opts: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Wall pieces from pairs of parallel lines a wall thickness apart that overlap. The longest overlaps are taken first and a stretch of a line already used by a pair is not
    used again, so a glazing line next to a wall face is not mistaken for a thin wall."""
    tmin, tmax = (float(v) / scale for v in opts["wall_thickness_mm"])
    minlen = float(opts.get("min_wall_mm", 300.0)) / scale
    cand = [s for s in segs if s["id"] not in ignore and s["length"] >= minlen]
    pairs = []
    for i, a in enumerate(cand):
        for b in cand[i + 1:]:
            if _angdiff(a["theta"], b["theta"]) > 1e-6:
                continue
            gap = abs(b["rho"] - a["rho"])
            if not (tmin <= gap <= tmax):
                continue
            lo, hi = max(a["lo"], b["lo"]), min(a["hi"], b["hi"])
            if hi - lo >= minlen:
                pairs.append((hi - lo, gap, a, b, lo, hi))
    pairs.sort(key=lambda p: (-p[0], p[1], p[2]["id"], p[3]["id"]))
    used: dict[str, list[tuple[float, float]]] = {}
    pieces = []
    for ov, gap, a, b, lo, hi in pairs:
        if any(_cover(used.get(s["id"], []), lo, hi) > 0.3 * ov for s in (a, b)):
            continue
        for s in (a, b):
            used.setdefault(s["id"], []).append((lo, hi))
        ulo, uhi = min(a["lo"], b["lo"]), max(a["hi"], b["hi"])
        # the part of the faces not shared (a wall end where only one face goes on) is kept only up to the shorter face plus a thickness: longer than that is another wall
        ulo, uhi = max(ulo, lo - gap), min(uhi, hi + gap)
        pieces.append({"theta": a["theta"], "rho": (a["rho"] + b["rho"]) / 2, "t": gap, "lo": ulo, "hi": uhi, "faces": [a["id"], b["id"]], "overlap": ov})
    return pieces


def _jambs(piece_theta: float, rho: float, t: float, pos: float, segs: Sequence[Mapping[str, Any]], tol: float, ignore: set[str]) -> list[str]:
    """Short perpendicular lines closing the wall at ``pos`` (the two sides of a doorway are usually drawn like that)."""
    found = []
    for s in segs:
        if s["id"] in ignore or abs(_angdiff(s["theta"], piece_theta) - math.pi / 2) > math.radians(8):
            continue
        if not (0.6 * t <= s["length"] <= 1.4 * t + tol):
            continue
        mid = ((s["a"][0] + s["b"][0]) / 2, (s["a"][1] + s["b"][1]) / 2)
        if abs(_along(mid, piece_theta) - pos) <= tol and abs(_rho(mid, piece_theta) - rho) <= 0.5 * t + tol:
            found.append(s["id"])
    return found


def build_walls(pieces: list[dict[str, Any]], segs: Sequence[Mapping[str, Any]], ignore: set[str], scale: float, opts: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Join collinear pieces of one wall (same line and thickness). A gap narrower than ``min_opening_mm`` is a joint (a crossing wall); a gap up to ``max_opening_mm`` is an opening
    (window if a glazing line spans it, else a door that is only *assumed*); a wider gap separates two walls."""
    tol = float(opts["snap_px"])
    min_open, max_open = float(opts["min_opening_mm"]) / scale, float(opts["max_opening_mm"]) / scale
    groups: list[list[dict[str, Any]]] = []
    for p in sorted(pieces, key=lambda p: (round(p["theta"], 6), p["rho"], p["lo"])):
        placed = False
        for g in groups:
            q = g[0]
            if abs(q["theta"] - p["theta"]) < 1e-6 and abs(q["rho"] - p["rho"]) <= tol and abs(q["t"] - p["t"]) <= tol:
                g.append(p)
                placed = True
                break
        if not placed:
            groups.append([p])
    walls: list[dict[str, Any]] = []
    for g in groups:
        g.sort(key=lambda p: p["lo"])
        runs: list[dict[str, Any]] = [{"pieces": [g[0]], "lo": g[0]["lo"], "hi": g[0]["hi"], "openings": []}]
        for p in g[1:]:
            run = runs[-1]
            gap = p["lo"] - run["hi"]
            if gap <= min_open:
                run["pieces"].append(p)
                run["hi"] = max(run["hi"], p["hi"])
            elif gap <= max_open:
                run["openings"].append({"lo": run["hi"], "hi": p["lo"]})
                run["pieces"].append(p)
                run["hi"] = p["hi"]
            else:
                runs.append({"pieces": [p], "lo": p["lo"], "hi": p["hi"], "openings": []})
        for run in runs:
            th = run["pieces"][0]["theta"]
            rho = sum(p["rho"] * (p["hi"] - p["lo"]) for p in run["pieces"]) / sum(p["hi"] - p["lo"] for p in run["pieces"])
            t = sum(p["t"] * (p["hi"] - p["lo"]) for p in run["pieces"]) / sum(p["hi"] - p["lo"] for p in run["pieces"])
            faces = sorted({f for p in run["pieces"] for f in p["faces"]})
            ops = []
            for g_ in run["openings"]:
                width_mm = (g_["hi"] - g_["lo"]) * scale
                gl = [s for s in segs if s["id"] not in ignore and s["id"] not in faces and _angdiff(s["theta"], th) < 1e-6 and abs(s["rho"] - rho) <= max(tol, 0.4 * t)
                      and min(s["hi"], g_["hi"] + tol) - max(s["lo"], g_["lo"] - tol) >= 0.6 * (g_["hi"] - g_["lo"])]
                jam = _jambs(th, rho, t, g_["lo"], segs, tol * 1.5, ignore) + _jambs(th, rho, t, g_["hi"], segs, tol * 1.5, ignore)
                leaf = [s["id"] for s in segs if s["id"] not in ignore and s["id"] not in faces and abs(_angdiff(s["theta"], th) - math.pi / 2) < math.radians(8)
                        and 0.7 * (g_["hi"] - g_["lo"]) <= s["length"] <= 1.15 * (g_["hi"] - g_["lo"]) + tol
                        and any(abs(_along(pt, th) - e) <= tol * 1.5 and abs(_rho(pt, th) - (rho - t / 2)) <= t + tol or abs(_rho(pt, th) - (rho + t / 2)) <= t + tol and abs(_along(pt, th) - e) <= tol * 1.5
                                for pt in (s["a"], s["b"]) for e in (g_["lo"], g_["hi"]))]
                if gl:
                    kind, ev, conf = "window", {"glazing_line": gl[0]}, 0.85
                elif leaf:
                    kind, ev, conf = "door", {"door_leaf": leaf[0]}, 0.8
                else:
                    kind, ev, conf = "door", {"assumed": "a gap in the wall with no glazing line or door leaf drawn: a door is assumed"}, 0.55
                if jam:
                    ev["jambs"] = jam
                else:
                    conf *= 0.8
                    ev["no_jamb_lines"] = True
                if not (min_open * scale <= width_mm <= max_open * scale):
                    conf *= 0.5
                ops.append({"lo": g_["lo"], "hi": g_["hi"], "kind": kind, "evidence": ev, "confidence": round(conf, 3)})
            covered = sum(p["hi"] - p["lo"] for p in run["pieces"])
            span = run["hi"] - run["lo"] - sum(o["hi"] - o["lo"] for o in ops)
            walls.append({"theta": th, "rho": rho, "t": t, "lo": run["lo"], "hi": run["hi"], "openings": ops, "faces": faces,
                          "pair_overlap": sum(p["overlap"] for p in run["pieces"]) / max(covered, 1e-9), "pieces": len(run["pieces"]), "solid_px": span})
    walls = [w for w in walls if w["hi"] - w["lo"] - sum(o["hi"] - o["lo"] for o in w["openings"]) >= 0.5 * float(opts.get("min_wall_mm", 300.0)) / scale]
    walls.sort(key=lambda w: (round(w["theta"], 3), round(w["rho"], 1), w["lo"]))
    for i, w in enumerate(walls):
        w["id"] = f"w{i}"
        for j, o in enumerate(w["openings"]):
            o["id"] = f"{w['id']}.o{j}"
    return walls


def wall_point(w: Mapping[str, Any], pos: float) -> tuple[float, float]:
    return (pos * math.cos(w["theta"]) - w["rho"] * math.sin(w["theta"]), pos * math.sin(w["theta"]) + w["rho"] * math.cos(w["theta"]))


def join_corners(walls: list[dict[str, Any]], tol: float) -> None:
    """Close the ends of walls onto the centre lines of the walls they meet (corner, T) so the centre lines cross exactly where the walls do. The wall met is recorded as ``join``."""
    for w in walls:
        w["join"] = {"lo": None, "hi": None}
    for w in walls:
        for end in ("lo", "hi"):
            best = None
            for v in walls:
                if v is w or abs(_angdiff(v["theta"], w["theta"]) - math.pi / 2) > math.radians(6):
                    continue
                xa = _cross_param(v, {"theta": w["theta"], "rho": w["rho"]})                      # where v's centre line crosses w's, along w
                if xa is None:
                    continue
                xv = _cross_param(w, {"theta": v["theta"], "rho": v["rho"]})                      # ... and along v
                if not (v["lo"] - w["t"] / 2 - tol * 1.5 <= xv <= v["hi"] + w["t"] / 2 + tol * 1.5):
                    continue
                d = abs(xa - w[end])
                if d <= v["t"] / 2 + tol * 1.5 + 0.0 and (best is None or d < best[0]):
                    best = (d, xa, v["id"])
            if best:
                w[end] = best[1]
                w["join"][end] = best[2]
    for w in walls:
        if w["hi"] < w["lo"]:
            w["lo"], w["hi"] = w["hi"], w["lo"]
    # openings must stay inside the wall after the ends moved
    for w in walls:
        w["openings"] = [o for o in w["openings"] if o["lo"] >= w["lo"] - tol and o["hi"] <= w["hi"] + tol]


# ----------------------------------------------------------------------------------------------------------------------- 5. alignment of text and geometry
def anchor_candidates(axis: str, e: float, walls: Sequence[Mapping[str, Any]], tol: float) -> list[dict[str, Any]]:
    """What a dimension end at image coordinate ``e`` (x for an ``x`` dimension, y for ``y``) can be attached to: a face or the centre line of a wall running across the
    measured direction, or the edge of an opening in a wall running along it."""
    out = []
    for w in walls:
        if _angdiff(w["theta"], math.pi / 2 if axis == "x" else 0.0) < ANG_TOL:                  # a wall across the measuring direction: its position is its centre line
            c = -w["rho"] if axis == "x" else w["rho"]
            for off, name in ((-w["t"] / 2, "face-"), (0.0, "centre"), (w["t"] / 2, "face+")):
                pos = c + off
                out.append({"type": "wall", "wall": w["id"], "line": name, "offset_px": off, "coord": pos, "d": abs(pos - e)})
        elif _angdiff(w["theta"], 0.0 if axis == "x" else math.pi / 2) < ANG_TOL:                # a wall along it: openings' edges are positions along the wall
            for o in w["openings"]:
                for side in ("lo", "hi"):
                    out.append({"type": "opening", "wall": w["id"], "opening": o["id"], "line": side, "offset_px": 0.0, "coord": o[side], "d": abs(o[side] - e)})
    out = [c for c in out if c["d"] <= tol]
    # faces are what dimensions are usually drawn to; between equals the closer one wins, then the wall's own face, then the longer wall
    rank = {"face-": 0, "face+": 0, "centre": 1, "lo": 0, "hi": 0}
    out.sort(key=lambda c: (round(c["d"] / max(tol / 4, 1e-9)), rank[c["line"]], c["wall"]))
    return out


def dimension_confidence(d: Mapping[str, Any]) -> float:
    """Evidence score in [0, 1] (not a probability): the OCR confidence, how well the dimension line is closed at both ends, how closely its implied scale agrees with the others
    and how many of its two ends were attached to the geometry."""
    term = d["terminators"]
    closure = (term["extension_lines"] + term["marks"]) / 4.0
    term_f = 0.6 + 0.4 * min(1.0, closure)
    dev = d.get("scale_deviation")
    scale_f = 0.3 if d.get("status") == "outlier" else (1.0 if dev is None else math.exp(-((dev / 0.01) ** 2)))
    n = sum(1 for a in d.get("anchors", {}).values() if a)
    assoc_f = (0.2, 0.55, 1.0)[n]
    return round(max(0.0, min(1.0, d["ocr_conf"] * term_f * scale_f * assoc_f)), 3)


def align(dims: list[dict[str, Any]], walls: list[dict[str, Any]], scale: float, opts: Mapping[str, Any]) -> dict[str, Any]:
    """Impose the printed dimensions on the geometry. Every parallel family of wall centre lines (and every attached opening edge) becomes a 1-D position; each dimension that lands
    on two of them becomes ``distance`` between their points, solved by the shared constraint engine. Conflicts (the engine reports which constraints cannot hold together) are
    resolved by dropping the least trustworthy dimension in the conflict, and said so. Walls and openings are then moved to the solved positions."""
    tol = float(opts["snap_px"]) * 2.0
    diags: list[dict[str, Any]] = []
    report: dict[str, Any] = {"axes": {}, "dropped": [], "thickness": []}
    # gridlines: centre lines of axis-aligned walls grouped within a pixel or two
    grid: dict[str, list[dict[str, Any]]] = {"x": [], "y": []}
    for w in sorted(walls, key=lambda w: w["id"]):
        axis = "x" if _angdiff(w["theta"], math.pi / 2) < ANG_TOL else "y" if _angdiff(w["theta"], 0.0) < ANG_TOL else None
        if axis is None:
            continue
        c = -w["rho"] if axis == "x" else w["rho"]
        for g in grid[axis]:
            if abs(g["c"] - c) <= opts["snap_px"]:
                g["walls"].append(w["id"])
                g["c"] = sum(next(x for x in walls if x["id"] == i)["rho"] * (-1 if axis == "x" else 1) for i in g["walls"]) / len(g["walls"])
                break
        else:
            grid[axis].append({"c": c, "walls": [w["id"]]})
    for axis in grid:
        grid[axis].sort(key=lambda g: g["c"])
        for i, g in enumerate(grid[axis]):
            g["id"] = f"g{axis}{i}"
            g["c_mm"] = g["c"] * scale
    by_wall = {w["id"]: (axis, g) for axis in grid for g in grid[axis] for w in walls if w["id"] in g["walls"]}
    wall_by = {w["id"]: w for w in walls}
    opening_by = {o["id"]: (w, o) for w in walls for o in w["openings"]}
    # attach each dimension end
    for d in dims:
        d["anchors"] = {"p": None, "q": None}
        if d["axis"] not in ("x", "y") or d["status"] != "used":
            continue
        k = 0 if d["axis"] == "x" else 1
        for end in ("p", "q"):
            cands = anchor_candidates(d["axis"], d[end][k], walls, tol)
            if cands:
                c = cands[0]
                var = by_wall[c["wall"]][1]["id"] if c["type"] == "wall" else f"j:{c['opening']}:{c['line']}"
                d["anchors"][end] = {"var": var, "type": c["type"], "wall": c["wall"], "line": c["line"], "offset_mm": c["offset_px"] * scale, "distance_px": round(c["d"], 2), "opening": c.get("opening")}
    # thickness dimensions (both ends on the two faces of one wall)
    for d in dims:
        a, b = d["anchors"]["p"], d["anchors"]["q"]
        if a and b and a["type"] == b["type"] == "wall" and a["wall"] == b["wall"] and {a["line"], b["line"]} == {"face-", "face+"}:
            w = wall_by[a["wall"]]
            report["thickness"].append({"wall": w["id"], "dimension": d["id"], "from_mm": w["t"] * scale, "to_mm": d["value_mm"]})
            w["t"] = d["value_mm"] / scale
            d["status"] = "thickness"
            d["anchors"] = {"p": a, "q": b}
    # variables and constraints per axis, in metres
    values: dict[str, float] = {}
    for axis in grid:
        for g in grid[axis]:
            values[g["id"]] = g["c_mm"] / 1000.0
    for w, o in opening_by.values():
        values[f"j:{o['id']}:lo"] = o["lo"] * scale / 1000.0
        values[f"j:{o['id']}:hi"] = o["hi"] * scale / 1000.0
    solved = dict(values)
    for axis in ("x", "y"):
        active = []
        for d in dims:
            a, b = d["anchors"]["p"], d["anchors"]["q"]
            if d["axis"] != axis or d["status"] not in ("used",) or not (a and b):
                continue
            ia, ib = values[a["var"]], values[b["var"]]
            if a["var"] == b["var"]:
                diags.append({"code": "dimension.degenerate", "severity": "warning", "dimension": d["id"], "message": f"dimension '{d['text']}' starts and ends on the same line and is not used"})
                d["status"] = "ignored"
                continue
            lo_i, hi_i = (a, b) if ia <= ib else (b, a)
            d_m = (d["value_mm"] - hi_i["offset_mm"] + lo_i["offset_mm"]) / 1000.0
            if d_m <= 0:
                diags.append({"code": "dimension.contradiction", "severity": "warning", "dimension": d["id"], "message": f"dimension '{d['text']}' is smaller than the wall thicknesses at its ends and is not used"})
                d["status"] = "ignored"
                continue
            d["_ends"] = (lo_i["var"], hi_i["var"], d_m)
            active.append(d)
        info = {"variables": sum(1 for v in values if (v.startswith(f"g{axis}") or (v.startswith("j:") and _opening_axis(v, opening_by, wall_by) == axis))), "dimensions": len(active)}
        iteration = 0
        while True:
            used_vars = sorted({v for d in active for v in d["_ends"][:2]})
            if not active:
                info.update({"status": "no_dimensions", "dof": None, "components": 0})
                break
            parent = {v: v for v in used_vars}

            def find(v: str) -> str:
                while parent[v] != v:
                    parent[v] = parent[parent[v]]
                    v = parent[v]
                return v
            for d in active:
                parent[find(d["_ends"][0])] = find(d["_ends"][1])
            datum = {}
            for v in used_vars:
                r = find(v)
                if r not in datum or values[v] < values[datum[r]]:
                    datum[r] = v
            fixed = sorted(set(datum.values()) | {"axis"})
            entities = {"axis": {"kind": "segment", "coords": [-1e4, 0.0, 1e4, 0.0]}}
            for v in used_vars:
                entities[v] = {"kind": "point", "coords": [values[v], 0.0]}
            cons = []
            for v in used_vars:
                if v not in datum.values():
                    cons.append({"kind": "point_on_line", "refs": [v, "axis"]})
            owner = {}
            for d in active:
                owner[len(cons)] = d
                cons.append({"kind": "distance", "refs": [d["_ends"][0], d["_ends"][1]], "value": d["_ends"][2]})
            res = CE.analyze(entities, cons, fixed=fixed, tolerance=1e-9)
            if res["status"] != "conflicting" or iteration > len(active):
                info.update({"status": res["status"], "dof": res["dof"], "components": len(datum), "redundant": len(res["redundant"]), "max_residual_mm": res["max_residual"] * 1000.0})
                for v, c in res["solution"].items():
                    if v != "axis":
                        solved[v] = c[0]
                break
            bad = [owner[i] for i in res["conflicting"] if i in owner]
            if not bad:                                                    # the engine found a conflict none of the dimensions is part of: cannot localise
                info.update({"status": "conflicting", "dof": res["dof"], "components": len(datum)})
                diags.append({"code": "alignment.unlocalised", "severity": "error", "message": f"the {axis}-dimensions conflict but the engine could not name which one"})
                break
            drop = min(bad, key=lambda d: (d["confidence_pre"], -d["value_mm"]))
            residual = next(c["residual"] for c in res["constraints"] if c["index"] == next(i for i, dd in owner.items() if dd is drop)) * 1000.0
            drop["status"] = "dropped"
            drop["dropped_reason"] = f"conflicts with other dimensions by up to {residual:.1f} mm; the least trusted of {len(bad)} in the conflict is dropped"
            report["dropped"].append({"dimension": drop["id"], "text": drop["text"], "residual_mm": round(residual, 2), "among": sorted(d["id"] for d in bad)})
            diags.append({"code": "dimension.conflict", "severity": "warning", "dimension": drop["id"],
                          "message": f"dimension '{drop['text']}' disagrees with others it is combined with by {residual:.1f} mm and is not enforced (printed rounding or a misread value?)",
                          "hint": "correct its value, or ignore the dimension"})
            active.remove(drop)
            iteration += 1
        report["axes"][axis] = info
    # move the geometry to the solved positions
    for axis in grid:
        for g in grid[axis]:
            c_px = solved[g["id"]] * 1000.0 / scale
            for wid in g["walls"]:
                w = wall_by[wid]
                w["rho"] = -c_px if axis == "x" else c_px
                w["constrained"] = g["id"] in {d["anchors"][e]["var"] for d in dims if d["status"] == "used" for e in ("p", "q") if d["anchors"].get(e)}
    for w, o in opening_by.values():
        for side in ("lo", "hi"):
            key = f"j:{o['id']}:{side}"
            o[side] = solved[key] * 1000.0 / scale
    for w in walls:                                                        # ends follow the walls they join
        for end in ("lo", "hi"):
            j = w["join"].get(end)
            if j:
                v = wall_by[j]
                xa = _cross_param(v, {"theta": w["theta"], "rho": w["rho"]})
                if xa is not None:
                    w[end] = xa
    for d in dims:
        d.pop("_ends", None)
    return {"grid": grid, "report": report, "diagnostics": diags}


def _opening_axis(var: str, opening_by: Mapping[str, Any], wall_by: Mapping[str, Any]) -> str:
    oid = var.split(":")[1]
    w = opening_by[oid][0]
    return "x" if _angdiff(w["theta"], 0.0) < ANG_TOL else "y"


# ----------------------------------------------------------------------------------------------------------------------- 6. spaces
def _poly_area(poly: Sequence[Sequence[float]]) -> float:
    return 0.5 * sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly)))


def find_spaces(walls: Sequence[Mapping[str, Any]], scale: float, opts: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Closed regions between the walls (openings count as closed, so a doorway does not merge two rooms). Found on a raster of the wall plan, then every axis-aligned edge of
    the outline is moved onto the wall face it follows, so a rectangular room has exactly the area its faces enclose. Walls are in image pixels (y down); the result is in mm."""
    import cv2
    import numpy as np
    if not walls:
        return []
    mm = []
    for w in walls:
        a, b = wall_point(w, w["lo"]), wall_point(w, w["hi"])
        mm.append((w, (a[0] * scale, a[1] * scale), (b[0] * scale, b[1] * scale), w["t"] * scale))
    pad = 2 * max(t for *_, t in mm) + 100.0
    xs = [p[0] for _, a, b, t in mm for p in (a, b)]
    ys = [p[1] for _, a, b, t in mm for p in (a, b)]
    x0, y0, x1, y1 = min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad
    res = max(5.0, max(x1 - x0, y1 - y0) / 3500.0)
    W, H = int(math.ceil((x1 - x0) / res)) + 1, int(math.ceil((y1 - y0) / res)) + 1
    img = np.zeros((H, W), np.uint8)
    for w, a, b, t in mm:
        u = (b[0] - a[0], b[1] - a[1])
        L = math.hypot(*u)
        if L < 1e-9:
            continue
        u = (u[0] / L, u[1] / L)
        n = (-u[1], u[0])
        ext = {e: (t / 2 if w["join"][e] else 0.0) for e in ("lo", "hi")}      # a joined end runs into the wall it meets
        p0 = (a[0] - u[0] * ext["lo"], a[1] - u[1] * ext["lo"])
        p1 = (b[0] + u[0] * ext["hi"], b[1] + u[1] * ext["hi"])
        quad = np.array([[p0[0] + n[0] * t / 2, p0[1] + n[1] * t / 2], [p1[0] + n[0] * t / 2, p1[1] + n[1] * t / 2],
                         [p1[0] - n[0] * t / 2, p1[1] - n[1] * t / 2], [p0[0] - n[0] * t / 2, p0[1] - n[1] * t / 2]])
        quad = np.round((quad - [x0, y0]) / res).astype(np.int32)
        cv2.fillPoly(img, [quad], 255)
    free = (img == 0).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(free, connectivity=4)
    outside = {int(labels[0, 0]), int(labels[0, -1]), int(labels[-1, 0]), int(labels[-1, -1])}
    faces_x = sorted({(-w["rho"] + off) * scale for w in walls if _angdiff(w["theta"], math.pi / 2) < ANG_TOL for off in (-w["t"] / 2, w["t"] / 2)})
    faces_y = sorted({(w["rho"] + off) * scale for w in walls if _angdiff(w["theta"], 0.0) < ANG_TOL for off in (-w["t"] / 2, w["t"] / 2)})
    rooms = []
    for k in range(1, n):
        if k in outside:
            continue
        area_px = int(stats[k, cv2.CC_STAT_AREA])
        if area_px * res * res / 1e6 < float(opts["min_room_m2"]):
            continue
        mask = (labels == k).astype(np.uint8)
        cs, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        c = max(cs, key=cv2.contourArea)
        ap = cv2.approxPolyDP(c, 1.5, True).reshape(-1, 2)
        poly = [[x0 + (px + 0.5) * res, y0 + (py + 0.5) * res] for px, py in ap]
        snapped = _snap_outline(poly, faces_x, faces_y, res)
        poly_out = snapped if snapped else [[x0 + px * res, y0 + py * res] for px, py in ap]
        rooms.append({"polygon_px_mm": poly_out, "exact": bool(snapped), "area_m2": abs(_poly_area(poly_out)) / 1e6 if snapped else area_px * res * res / 1e6, "resolution_mm": res})
    rooms.sort(key=lambda r: (min(p[1] for p in r["polygon_px_mm"]), min(p[0] for p in r["polygon_px_mm"])))
    return rooms


def _snap_outline(poly: list[list[float]], faces_x: Sequence[float], faces_y: Sequence[float], res: float) -> list[list[float]] | None:
    """Rebuild an all-axis-aligned outline from the wall faces its edges lie on. None when an edge is oblique or no face is close enough (the raw outline is kept then)."""
    n = len(poly)
    if n < 4:
        return None
    edges = []
    for i in range(n):
        p, q = poly[i], poly[(i + 1) % n]
        dx, dy = q[0] - p[0], q[1] - p[1]
        if abs(dx) <= 2.5 * res and abs(dy) > abs(dx):
            edges.append(("v", (p[0] + q[0]) / 2))
        elif abs(dy) <= 2.5 * res and abs(dx) > abs(dy):
            edges.append(("h", (p[1] + q[1]) / 2))
        else:
            return None
    out = []
    for kind, v in edges:
        pool = faces_x if kind == "v" else faces_y
        near = min(pool, key=lambda f: abs(f - v)) if pool else None
        if near is None or abs(near - v) > 3.0 * res:
            return None
        out.append((kind, near))
    pts = []
    for i in range(n):
        a, b = out[i - 1], out[i]
        if a[0] == b[0]:                                                          # two edges in a row along the same direction: not a corner
            continue
        pts.append([a[1], b[1]] if a[0] == "v" else [b[1], a[1]])
    return pts if len(pts) >= 4 else None


# ----------------------------------------------------------------------------------------------------------------------- run
OPTION_KEYS = set(DEFAULTS) | {"min_wall_mm"}
SOURCE_SNAP = {"raster": 4.0, "pdf": 1.5, "sdrw": 4.0}      # the tolerance that makes two lines the same line, in the source's own units (pixels, points, millimetres)


def _options(user: Mapping[str, Any] | None, kind: str) -> dict[str, Any]:
    opts = dict(DEFAULTS)
    opts["snap_px"] = SOURCE_SNAP[kind]
    unknown = set(user or {}) - OPTION_KEYS
    if unknown:
        raise BadRequest("Unknown reconstruction option(s): " + ", ".join(sorted(unknown)), hint="options are " + ", ".join(sorted(OPTION_KEYS)))
    for k, v in (user or {}).items():
        if k == "wall_thickness_mm":
            if not (isinstance(v, (list, tuple)) and len(v) == 2 and 0 < float(v[0]) < float(v[1])):
                raise BadRequest("wall_thickness_mm must be [min, max] with 0 < min < max")
            v = (float(v[0]), float(v[1]))
        elif k == "unit":
            if v not in ("auto", "mm", "cm", "m"):
                raise BadRequest("unit must be auto, mm, cm or m")
        elif k == "ocr":
            v = bool(v)
        elif k == "corrections":
            continue
        else:
            v = float(v)
            if not math.isfinite(v) or v <= 0:
                raise BadRequest(f"{k} must be a positive number")
        opts[k] = v
    return opts


def _check_corrections(c: Any) -> dict[str, Any]:
    c = dict(c or {})
    allowed = {"scale_mm_per_px", "unit", "dimensions", "add_dimensions", "remove", "walls", "openings", "add_walls"}
    unknown = set(c) - allowed
    if unknown:
        raise BadRequest("Unknown correction(s): " + ", ".join(sorted(unknown)), hint="corrections are " + ", ".join(sorted(allowed)))
    if c.get("scale_mm_per_px") is not None:
        v = float(c["scale_mm_per_px"])
        if not math.isfinite(v) or v <= 0:
            raise BadRequest("scale_mm_per_px must be a positive number")
    for key in ("dimensions", "walls", "openings"):
        if key in c and not isinstance(c[key], Mapping):
            raise BadRequest(f"corrections.{key} must be an object keyed by id")
    for key in ("remove", "add_walls", "add_dimensions"):
        if key in c and not isinstance(c[key], list):
            raise BadRequest(f"corrections.{key} must be a list")
    return c


def run(source: Mapping[str, Any], options: Mapping[str, Any] | None = None, corrections: Mapping[str, Any] | None = None, *, project: Any = None) -> dict[str, Any]:
    """The whole pipeline, deterministic: the same source, options and corrections give the same model. ``source`` is ``{"kind": "raster"|"pdf", "data": bytes}`` or
    ``{"kind": "sdrw", "drawing": view}`` (needs ``project``). Returns the model (walls, openings, spaces, dimensions, constraint report, diagnostics) and an ``overlay`` of what
    was found in source coordinates for the person to check; nothing is created in the project."""
    kind = source.get("kind")
    if kind not in ("raster", "pdf", "sdrw"):
        raise BadRequest("source.kind must be raster, pdf or sdrw")
    opts = _options(options, kind)
    corr = _check_corrections(corrections if corrections is not None else (options or {}).get("corrections"))
    if kind == "sdrw":
        if project is None:
            raise BadRequest("An SDRW source needs a project")
        acq = acquire_sdrw(project, str(source.get("drawing")))
    else:
        data = source.get("data")
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise BadRequest("source.data is empty")
        caps = capabilities()["sources"]
        need = "raster" if kind == "raster" else "pdf"
        if not caps[need]["available"]:
            raise BadRequest("Reconstruction from this source is not available", hint=caps[need]["reason"])
        acq = acquire_raster(bytes(data), opts) if kind == "raster" else acquire_pdf(bytes(data), opts)
    diags: list[dict[str, Any]] = list(acq["diagnostics"])
    segs, texts = acq["segments"], acq["texts"]
    tol = float(opts["snap_px"]) * 2.0
    dims = find_dimensions(segs, texts, tol)
    for sd in acq.get("sdrw_dimensions", []):                                       # a dimension entity of a drawing: measured exactly, no text to read
        k = 0 if abs(sd["q"][0] - sd["p"][0]) >= abs(sd["q"][1] - sd["p"][1]) else 1
        p, q = sorted((sd["p"], sd["q"]), key=lambda v: v[k])
        dims.append({"text_id": None, "text": f"{abs(q[k] - p[k]):g}", "raw_value": abs(q[k] - p[k]), "ocr_conf": 1.0, "line": None, "p": p, "q": q, "length_px": abs(q[k] - p[k]),
                     "axis": "x" if k == 0 else "y", "terminators": {"extension_lines": 2, "marks": 2}, "parts": [], "text_offset_px": 0.0, "source": "sdrw-dimension", "entity": sd["entity"]})
    for i, d in enumerate(dims):
        d["id"] = f"d{i}"
    for m in corr.get("add_dimensions", []):                                        # a dimension the person measured by hand: two image points and the value
        try:
            p, q, v = [float(x) for x in m["p_px"]], [float(x) for x in m["q_px"]], float(m["value_mm"])
        except (KeyError, TypeError, ValueError):
            raise BadRequest("add_dimensions entries need p_px, q_px (two image points) and value_mm")
        k = 0 if abs(q[0] - p[0]) >= abs(q[1] - p[1]) else 1
        p, q = sorted((p, q), key=lambda a: a[k])
        dims.append({"id": f"u{len([d for d in dims if d['id'].startswith('u')])}", "text_id": None, "text": f"{v:g}", "raw_value": v, "value_override": v, "ocr_conf": 1.0, "line": None, "p": p, "q": q,
                     "length_px": abs(q[k] - p[k]), "axis": "x" if k == 0 else "y", "terminators": {"extension_lines": 2, "marks": 2}, "parts": [], "text_offset_px": 0.0, "source": "user"})
    for did, c in (corr.get("dimensions") or {}).items():
        d = next((x for x in dims if x["id"] == did), None)
        if d is None:
            diags.append({"code": "correction.unknown", "severity": "warning", "message": f"correction for dimension '{did}' matches nothing in this run"})
            continue
        if c.get("ignore"):
            d["status"] = "ignored"
        if c.get("value_mm") is not None:
            d["value_override"] = float(c["value_mm"])
    if corr.get("unit"):
        opts["unit"] = corr["unit"]
    fixed = float(corr["scale_mm_per_px"]) if corr.get("scale_mm_per_px") else None
    cal = calibrate(dims, opts, fixed)
    diags += cal.pop("diagnostics")
    scale = cal["mm_per_unit"]
    result: dict[str, Any] = {"version": "spadra.reconstruction.v1", "source": {"kind": kind, "width": acq["width"], "height": acq["height"], "unit": acq["unit"]}, "scale": cal,
                              "options": {k: v for k, v in opts.items() if k != "corrections"}, "corrections": corr, "capabilities": capabilities()}
    ignore = {pid for d in dims for pid in d["parts"]}
    result["overlay"] = {"segments": [{"id": s["id"], "a": [round(v, 2) for v in s["a"]], "b": [round(v, 2) for v in s["b"]], "role": "dimension" if s["id"] in ignore else "line"} for s in segs],
                         "texts": [{"id": t["id"], "text": t["text"], "bbox": t["bbox"], "confidence": round(t["conf"], 3), "rotation": t["rotation"]} for t in texts]}
    if not scale:
        result.update({"walls": [], "spaces": [], "dimensions": _dims_out(dims), "constraints": {}, "frame": None, "diagnostics": diags, "ok": False, "summary": _summary([], [], dims, diags)})
        return result
    pieces = pair_walls(segs, ignore, scale, opts)
    walls = build_walls(pieces, segs, ignore, scale, opts)
    join_corners(walls, float(opts["snap_px"]))
    walls = _apply_wall_corrections(walls, corr, scale, opts, diags)
    for d in dims:
        d["confidence_pre"] = dimension_confidence({**d, "anchors": {"p": True, "q": True}})
    al = align(dims, walls, scale, opts)
    diags += al["diagnostics"]
    for d in dims:
        d["confidence"] = dimension_confidence(d) if d["status"] in ("used", "thickness") else round(d["confidence_pre"] * 0.3, 3)
    spaces = find_spaces(walls, scale, opts)
    result.update(_to_plan(walls, spaces, dims, scale, al, diags, acq))
    result["ok"] = not any(d["severity"] == "error" for d in diags)
    result["diagnostics"] = diags
    result["summary"] = _summary(result["walls"], result["spaces"], dims, diags)
    return result


def _apply_wall_corrections(walls: list[dict[str, Any]], corr: Mapping[str, Any], scale: float, opts: Mapping[str, Any], diags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    remove = set(corr.get("remove") or [])
    known = {w["id"] for w in walls} | {o["id"] for w in walls for o in w["openings"]}
    for r in sorted(remove - known):
        diags.append({"code": "correction.unknown", "severity": "warning", "message": f"'{r}' to remove matches nothing in this run"})
    walls = [w for w in walls if w["id"] not in remove]
    for w in walls:
        w["openings"] = [o for o in w["openings"] if o["id"] not in remove]
        c = (corr.get("walls") or {}).get(w["id"])
        if c and c.get("thickness_mm"):
            w["t"] = float(c["thickness_mm"]) / scale
            w["user_thickness"] = True
        for o in w["openings"]:
            oc = (corr.get("openings") or {}).get(o["id"])
            if oc:
                if oc.get("kind") in ("door", "window"):
                    o["kind"], o["confidence"], o["evidence"] = oc["kind"], 1.0, {"user": "kind set by the person"}
                if oc.get("width_mm"):
                    mid = (o["lo"] + o["hi"]) / 2
                    o["lo"], o["hi"] = mid - float(oc["width_mm"]) / scale / 2, mid + float(oc["width_mm"]) / scale / 2
    for i, a in enumerate(corr.get("add_walls") or []):
        try:
            s, e = [float(v) for v in a["start_px"]], [float(v) for v in a["end_px"]]
            t = float(a.get("thickness_mm", 200.0))
        except (KeyError, TypeError, ValueError):
            raise BadRequest("add_walls entries need start_px, end_px (image points) and optionally thickness_mm")
        th = _theta(s, e)
        for axis in (0.0, math.pi / 2):
            if _angdiff(th, axis) <= ANG_TOL:
                th = axis
        rho = (_rho(s, th) + _rho(e, th)) / 2
        lo, hi = sorted((_along(s, th), _along(e, th)))
        walls.append({"theta": th, "rho": rho, "t": t / scale, "lo": lo, "hi": hi, "openings": [], "faces": [], "pair_overlap": 1.0, "pieces": 1, "solid_px": hi - lo, "id": f"u{i}", "user": True,
                      "join": {"lo": None, "hi": None}})
    join_corners(walls, float(opts["snap_px"]))
    return walls


def wall_confidence(w: Mapping[str, Any]) -> float:
    """Evidence score in [0, 1] (not a probability): 60 % how fully the two faces overlap, 20 % how many ends close on another wall, 20 % whether a printed dimension fixes its
    position. A wall the person added is 1.0."""
    if w.get("user"):
        return 1.0
    joined = sum(1 for e in ("lo", "hi") if w["join"].get(e)) / 2.0
    return round(0.6 * min(1.0, w["pair_overlap"]) + 0.2 * (0.5 + 0.5 * joined) + 0.2 * (1.0 if w.get("constrained") else 0.4), 3)


def _to_plan(walls: list[dict[str, Any]], spaces: list[dict[str, Any]], dims: list[dict[str, Any]], scale: float, al: Mapping[str, Any], diags: list[dict[str, Any]], acq: Mapping[str, Any]) -> dict[str, Any]:
    """Plan coordinates: millimetres, y up, the lower-left corner of the walls at the origin. ``frame`` maps back to image pixels: px = ((x + ox) / scale, (oy - y) / scale)."""
    corners = []
    for w in walls:
        for pos in (w["lo"], w["hi"]):
            x, y = wall_point(w, pos)
            nx, ny = -math.sin(w["theta"]), math.cos(w["theta"])
            for s in (-1, 1):
                corners.append(((x + s * nx * w["t"] / 2) * scale, (y + s * ny * w["t"] / 2) * scale))
    if not corners:
        return {"walls": [], "spaces": [], "dimensions": _dims_out(dims), "constraints": al["report"], "frame": {"scale": scale, "ox": 0.0, "oy": 0.0}}
    ox, oy = min(c[0] for c in corners), max(c[1] for c in corners)
    plan = lambda x, y: [round(x * scale - ox, 2), round(oy - y * scale, 2)]
    out_walls = []
    for w in walls:
        a, b = wall_point(w, w["lo"]), wall_point(w, w["hi"])
        conf = wall_confidence(w)
        ops = []
        for o in w["openings"]:
            ops.append({"id": o["id"], "kind": o["kind"], "offset_mm": round(((o["lo"] + o["hi"]) / 2 - w["lo"]) * scale, 2), "width_mm": round((o["hi"] - o["lo"]) * scale, 2),
                        "confidence": o["confidence"], "evidence": o["evidence"], "ifc": _ifc_of(o["kind"])})
        out_walls.append({"id": w["id"], "start": plan(*a), "end": plan(*b), "thickness_mm": round(w["t"] * scale, 2), "length_mm": round((w["hi"] - w["lo"]) * scale, 2), "openings": ops, "confidence": conf,
                          "ifc": "IfcWall", "evidence": {"faces": w.get("faces", []), "pieces": w.get("pieces", 1), "joins": w["join"], "dimension_constrained": bool(w.get("constrained")),
                                                          "user": bool(w.get("user")), "axis_aligned": _angdiff(w["theta"], 0.0) < ANG_TOL or _angdiff(w["theta"], math.pi / 2) < ANG_TOL}})
    out_spaces = []
    for i, r in enumerate(spaces):
        poly = [[round(x - ox, 2), round(oy - y, 2)] for x, y in r["polygon_px_mm"]]
        near = [w["confidence"] for w in out_walls if _near(poly, w)]
        conf = round(min(near) * (1.0 if r["exact"] else 0.7), 3) if near else 0.3
        out_spaces.append({"id": f"r{i}", "polygon": poly, "area_m2": round(r["area_m2"], 4), "confidence": conf, "outline": "exact (faces)" if r["exact"] else "raster (approximate)",
                           "resolution_mm": round(r["resolution_mm"], 2), "ifc": "IfcSpace"})
    return {"walls": out_walls, "spaces": out_spaces, "dimensions": _dims_out(dims), "constraints": {**al["report"], "grid": {a: [{"id": g["id"], "coordinate_mm": round(g["c"] * scale - (ox if a == "x" else 0.0), 2) if a == "x" else round(oy - g["c"] * scale, 2), "walls": g["walls"]} for g in al["grid"][a]] for a in al["grid"]}},
            "frame": {"scale": scale, "ox": ox, "oy": oy, "px": "px = ((x + ox) / scale, (oy - y) / scale)"}}


def _near(poly: Sequence[Sequence[float]], w: Mapping[str, Any]) -> bool:
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    t = w["thickness_mm"]
    mx, my = (w["start"][0] + w["end"][0]) / 2, (w["start"][1] + w["end"][1]) / 2
    return min(xs) - t <= mx <= max(xs) + t and min(ys) - t <= my <= max(ys) + t


def _ifc_of(kind: str) -> str:
    return {"door": "IfcDoor", "window": "IfcWindow"}[kind]


def _dims_out(dims: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for d in dims:
        out.append({"id": d["id"], "text": d["text"], "value_mm": round(d.get("value_mm", 0.0), 3), "unit_read": d.get("unit_read"), "axis": d["axis"], "status": d.get("status", "candidate"),
                    "length_px": round(d["length_px"], 2), "p": [round(v, 2) for v in d["p"]], "q": [round(v, 2) for v in d["q"]], "confidence": d.get("confidence", 0.0), "ocr_confidence": round(d["ocr_conf"], 3),
                    "scale_deviation": None if d.get("scale_deviation") is None else round(d["scale_deviation"], 5), "text_id": d.get("text_id"), "source": d.get("source", "text"),
                    "anchors": {k: v for k, v in (d.get("anchors") or {}).items() if v}, "dropped_reason": d.get("dropped_reason")})
    return out


def _summary(walls: Sequence[Any], spaces: Sequence[Any], dims: Sequence[Mapping[str, Any]], diags: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {"walls": len(walls), "openings": sum(len(w["openings"]) for w in walls), "spaces": len(spaces), "dimensions_found": len(dims),
            "dimensions_used": sum(1 for d in dims if d.get("status") in ("used", "thickness")), "errors": sum(1 for d in diags if d["severity"] == "error"),
            "warnings": sum(1 for d in diags if d["severity"] == "warning")}


# ----------------------------------------------------------------------------------------------------------------------- 7. into the project
MAX_WALLS, MAX_SPACES = 2000, 500
LAYERS = {"wall": "A-WALL", "opening": "A-OPEN", "room": "A-ROOM"}


def _finite(v: Any, label: str, lo: float = -1e9, hi: float = 1e9) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise BadRequest(f"{label} must be a number")
    if not math.isfinite(x) or not lo <= x <= hi:
        raise BadRequest(f"{label} must be a number between {lo:g} and {hi:g}")
    return x


def validate_model(model: Any) -> dict[str, Any]:
    """The model handed to draw or build is data like any command's parameters: check its shape and bounds, and that it is a reconstruction (``version``). Nothing it contains is trusted."""
    if not isinstance(model, Mapping) or model.get("version") != "spadra.reconstruction.v1":
        raise BadRequest("model is not a reconstruction result", hint="pass the result of reconstruct.run (spadra.reconstruction.v1)")
    walls, spaces = model.get("walls"), model.get("spaces")
    if not isinstance(walls, list) or not isinstance(spaces, list):
        raise BadRequest("model.walls and model.spaces must be lists")
    if len(walls) > MAX_WALLS or len(spaces) > MAX_SPACES:
        raise BadRequest(f"A reconstruction is limited to {MAX_WALLS} walls and {MAX_SPACES} spaces")
    ids: set[str] = set()
    for w in walls:
        if not isinstance(w, Mapping) or not isinstance(w.get("id"), str) or w["id"] in ids:
            raise BadRequest("every wall needs a unique id")
        ids.add(w["id"])
        for k in ("start", "end"):
            p = w.get(k)
            if not (isinstance(p, (list, tuple)) and len(p) == 2):
                raise BadRequest(f"wall {w['id']}: {k} must be [x, y] in mm")
            _finite(p[0], f"wall {w['id']} {k}.x"), _finite(p[1], f"wall {w['id']} {k}.y")
        length = _len(w["start"], w["end"])
        if length < 50:
            raise BadRequest(f"wall {w['id']} is shorter than 50 mm")
        _finite(w.get("thickness_mm"), f"wall {w['id']} thickness_mm", 10, 5000)
        for o in w.get("openings") or []:
            if o.get("kind") not in ("door", "window"):
                raise BadRequest(f"opening {o.get('id')}: kind must be door or window")
            _finite(o.get("offset_mm"), f"opening {o.get('id')} offset_mm", 0, length)
            _finite(o.get("width_mm"), f"opening {o.get('id')} width_mm", 100, length)
    for r in spaces:
        pts = r.get("polygon")
        if not (isinstance(pts, list) and 3 <= len(pts) <= 2000):
            raise BadRequest("every space needs a polygon of 3 or more points")
        for q in pts:
            if not (isinstance(q, (list, tuple)) and len(q) == 2):
                raise BadRequest("space polygon points must be [x, y]")
            _finite(q[0], "space x"), _finite(q[1], "space y")
    return dict(model)


def provenance(model: Mapping[str, Any], what: str, confidence: Any, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    src = model.get("source") or {}
    return {"source": src.get("kind"), "scale_mm_per_unit": (model.get("scale") or {}).get("mm_per_unit"), "id": what, "confidence": confidence, **(extra or {})}


def draw_plan(doc: Any, model: Mapping[str, Any], mm_per_unit: float, origin_mm: Sequence[float] = (0.0, 0.0)) -> dict[str, Any]:
    """Write the reconstruction into an SDRW drawing as plain geometry, one layer per kind: wall solids (a closed rectangle for every stretch between openings) on A-WALL, openings
    (a line across the gap) on A-OPEN, space outlines on A-ROOM. ``mm_per_unit`` is the drawing's unit in millimetres. Entities carry their id, confidence and kind as metadata."""
    ox, oy = float(origin_mm[0]), float(origin_mm[1])
    pt = lambda p: [(p[0] + ox) / mm_per_unit, (p[1] + oy) / mm_per_unit]
    made: dict[str, list[str]] = {"walls": [], "openings": [], "spaces": []}
    for w in model["walls"]:
        a, b = w["start"], w["end"]
        L = _len(a, b)
        u = ((b[0] - a[0]) / L, (b[1] - a[1]) / L)
        n = (-u[1], u[0])
        h = w["thickness_mm"] / 2
        cuts = sorted((o["offset_mm"] - o["width_mm"] / 2, o["offset_mm"] + o["width_mm"] / 2, o) for o in (w.get("openings") or []))
        stretch, at = [], 0.0
        for lo, hi, _o in cuts:
            stretch.append((at, lo))
            at = hi
        stretch.append((at, L))
        for i, (s, e) in enumerate(stretch):
            if e - s < 1.0:
                continue
            quad = [[a[0] + u[0] * s + n[0] * h, a[1] + u[1] * s + n[1] * h], [a[0] + u[0] * e + n[0] * h, a[1] + u[1] * e + n[1] * h],
                    [a[0] + u[0] * e - n[0] * h, a[1] + u[1] * e - n[1] * h], [a[0] + u[0] * s - n[0] * h, a[1] + u[1] * s - n[1] * h]]
            made["walls"].append(doc.add_polyline([pt(q) for q in quad], closed=True, layer=LAYERS["wall"], create_layer=LAYERS["wall"] not in {l["name"] for l in doc.layers()},
                                                  metadata={"spadra:recon": provenance(model, w["id"], w["confidence"], {"ifc": "IfcWall", "part": i})}))
        for lo, hi, o in cuts:
            p0 = [a[0] + u[0] * lo, a[1] + u[1] * lo]
            p1 = [a[0] + u[0] * hi, a[1] + u[1] * hi]
            made["openings"].append(doc.add_line(pt(p0), pt(p1), layer=LAYERS["opening"], create_layer=LAYERS["opening"] not in {l["name"] for l in doc.layers()},
                                                 metadata={"spadra:recon": provenance(model, o["id"], o["confidence"], {"kind": o["kind"], "ifc": o.get("ifc"), "host": w["id"], "evidence": o.get("evidence")})}))
    for r in model["spaces"]:
        made["spaces"].append(doc.add_polyline([pt(q) for q in r["polygon"]], closed=True, layer=LAYERS["room"], create_layer=LAYERS["room"] not in {l["name"] for l in doc.layers()},
                                               metadata={"spadra:recon": provenance(model, r["id"], r["confidence"], {"area_m2": r["area_m2"], "ifc": "IfcSpace"})}))
    return made


def build_plan(project: Any, model: Mapping[str, Any], p: Mapping[str, Any], add_wall: Any, f_m: float) -> dict[str, Any]:
    """Create SPADRA walls, doors, windows and rooms from the reconstruction (in the caller's transaction). ``add_wall`` is the project's own wall command, so every wall is made the
    way a hand-drawn one is (corner joins, base on the floor below); openings and rooms use the same element functions. Every element gets ``spadra:recon`` with its confidence and
    the evidence behind it, and the returned ``ifc`` lists the IFC class each created element maps to."""
    from . import bimdata, rooms as RM
    level = project.level(p["level"]) if p.get("level") else project.manifest["levels"][0]
    f = 1.0 / (MM_PER[project.manifest["unit"]])                               # mm -> drawing units
    ox, oy = (float(v) for v in p.get("origin_mm", (0.0, 0.0)))
    pt = lambda q: [(q[0] + ox) * f, (q[1] + oy) * f]
    height = _finite(p.get("height", 3.0), "height", 0.1, 100)
    min_conf = _finite(p.get("min_confidence", 0.0), "min_confidence", 0, 1)
    made: dict[str, Any] = {"walls": {}, "openings": {}, "spaces": {}, "skipped": []}
    for w in model["walls"]:
        if w["confidence"] < min_conf:
            made["skipped"].append({"id": w["id"], "reason": f"confidence {w['confidence']} is below min_confidence {min_conf}"})
            continue
        oid = add_wall(project, {"start": pt(w["start"]), "end": pt(w["end"]), "thickness": w["thickness_mm"], "height": height, "level": level["id"]})["id"]
        E._attach(project, oid, {"recon": provenance(model, w["id"], w["confidence"], {"evidence": w.get("evidence")})})
        made["walls"][w["id"]] = oid
    for w in model["walls"]:
        host = made["walls"].get(w["id"])
        if host is None:
            continue
        for o in w.get("openings") or []:
            if o["confidence"] < min_conf:
                made["skipped"].append({"id": o["id"], "reason": f"confidence {o['confidence']} is below min_confidence {min_conf}"})
                continue
            q = {"wall": host, "offset": o["offset_mm"] / 1000.0, "width": o["width_mm"]}
            oid = E.add_opening(project, o["kind"], q, f_m)
            E._attach(project, oid, {"recon": provenance(model, o["id"], o["confidence"], {"evidence": o.get("evidence"), "host": w["id"]})})
            made["openings"][o["id"]] = oid
    for r in model["spaces"]:
        if r["confidence"] < min_conf:
            made["skipped"].append({"id": r["id"], "reason": f"confidence {r['confidence']} is below min_confidence {min_conf}"})
            continue
        rid = RM.add_room(project, {"level": level["id"], "points": [pt(q) for q in r["polygon"]], "name": p.get("room_name", "Room")}, MM_PER[project.manifest["unit"]] / 1000.0)
        E._attach(project, rid, {"recon": provenance(model, r["id"], r["confidence"], {"area_m2": r["area_m2"], "outline": r.get("outline")})})
        made["spaces"][r["id"]] = rid
    made["ifc"] = {oid: bimdata.entity_of(E.meta_of(project, oid)) for group in ("walls", "openings", "spaces") for oid in made[group].values()}
    return made
