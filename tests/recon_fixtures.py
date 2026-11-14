"""Synthetic dimensioned floor plans with *known* reference dimensions, rendered as raster images and as vector PDF.

These are test instruments, not photographs of real buildings: every wall, opening and dimension is generated from a ground-truth description, so the reconstruction can be
scored against exact numbers (and so the limits of the evaluation are plain: synthetic, clean-ish drawings; see docs/RECONSTRUCTION.md). Walls are drawn as double lines with the
joints cleaned (an edge is clipped where it lies inside another wall), openings as gaps with jamb lines (windows also carry a glazing line), dimensions as a dimension line with
two extension lines, tick or arrow terminators and the value as text (rotated for vertical dimensions)."""
from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FONT = next((p for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "C:/Windows/Fonts/arial.ttf") if Path(p).exists()), None)


@dataclass
class Wall:
    p0: tuple[float, float]
    p1: tuple[float, float]
    t: float = 200.0


@dataclass
class Opening:
    wall: int
    center: float                                  # mm along the wall from p0
    width: float
    kind: str = "door"                             # door | window


@dataclass
class Dim:
    axis: str                                      # 'x' (horizontal dimension) | 'y'
    a: float                                       # coordinates (mm) of the two measured faces/lines along the axis
    b: float
    at: float                                      # position of the dimension line on the other axis (mm)
    reference: tuple[float, float] = (0.0, 0.0)    # where the extension lines start on the other axis (mm), nearest the drawing
    style: str = "tick"
    unit: str = "mm"


@dataclass
class Fixture:
    name: str
    walls: list[Wall]
    openings: list[Opening] = field(default_factory=list)
    dims: list[Dim] = field(default_factory=list)
    px_per_mm: float = 0.125                       # raster scale
    blur: float = 0.0
    noise: float = 0.0
    jpeg: int | None = None
    kind: str = "raster"                           # raster | pdf | pdf-raster
    rooms: list[float] = field(default_factory=list)   # true areas in m^2 (filled by the builders below)

    def truth(self) -> dict[str, Any]:
        return {"name": self.name, "walls": [{"p0": w.p0, "p1": w.p1, "t": w.t} for w in self.walls],
                "openings": [{"wall": o.wall, "center": o.center, "width": o.width, "kind": o.kind} for o in self.openings],
                "dims": [{"axis": d.axis, "a": d.a, "b": d.b, "value": abs(d.b - d.a)} for d in self.dims], "rooms_m2": self.rooms}


# ----------------------------------------------------------------------------------------------------------------------- geometry
def _rect(w: Wall) -> tuple[float, float, float, float]:
    (x0, y0), (x1, y1) = w.p0, w.p1
    h = w.t / 2
    if abs(y1 - y0) < 1e-9:
        return min(x0, x1) - h, y0 - h, max(x0, x1) + h, y0 + h
    return x0 - h, min(y0, y1) - h, x0 + h, max(y0, y1) + h


def _pieces(w: Wall, openings: list[Opening], idx: int) -> list[tuple[float, float]]:
    """Intervals (mm along the wall) that are solid."""
    length = math.dist(w.p0, w.p1)
    cuts = sorted((o.center - o.width / 2, o.center + o.width / 2) for o in openings if o.wall == idx)
    out, at = [], 0.0
    for a, b in cuts:
        if a > at:
            out.append((at, a))
        at = b
    if at < length:
        out.append((at, length))
    return out


def _outline_segments(fx: Fixture) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Face lines of every solid piece, end caps (jambs at openings), clipped where they lie inside another wall's solid."""
    solids: list[tuple[float, float, float, float]] = []
    raw: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for i, w in enumerate(fx.walls):
        horizontal = abs(w.p1[1] - w.p0[1]) < 1e-9
        length = math.dist(w.p0, w.p1)
        sign = 1 if (w.p1[0] > w.p0[0] if horizontal else w.p1[1] > w.p0[1]) else -1
        for a, b in _pieces(w, fx.openings, i):
            ext_a = w.t / 2 if a <= 1e-9 else 0.0          # a wall end is extended to the far face of what it joins: half a thickness covers a corner
            ext_b = w.t / 2 if b >= length - 1e-9 else 0.0
            if horizontal:
                xa, xb = w.p0[0] + sign * (a - ext_a), w.p0[0] + sign * (b + ext_b)
                lo, hi = min(xa, xb), max(xa, xb)
                solids.append((lo, w.p0[1] - w.t / 2, hi, w.p0[1] + w.t / 2))
            else:
                ya, yb = w.p0[1] + sign * (a - ext_a), w.p0[1] + sign * (b + ext_b)
                lo, hi = min(ya, yb), max(ya, yb)
                solids.append((w.p0[0] - w.t / 2, lo, w.p0[0] + w.t / 2, hi))
    for (x0, y0, x1, y1) in solids:
        raw += [((x0, y0), (x1, y0)), ((x0, y1), (x1, y1)), ((x0, y0), (x0, y1)), ((x1, y0), (x1, y1))]
    out = []
    for (p, q) in raw:
        horizontal = abs(p[1] - q[1]) < 1e-9
        cuts: list[tuple[float, float]] = []
        for (x0, y0, x1, y1) in solids:
            if horizontal and y0 + 1e-6 < p[1] < y1 - 1e-6:
                cuts.append((max(x0, min(p[0], q[0])), min(x1, max(p[0], q[0]))))
            elif not horizontal and x0 + 1e-6 < p[0] < x1 - 1e-6:
                cuts.append((max(y0, min(p[1], q[1])), min(y1, max(p[1], q[1]))))
        lo, hi = (min(p[0], q[0]), max(p[0], q[0])) if horizontal else (min(p[1], q[1]), max(p[1], q[1]))
        at = lo
        for a, b in sorted(c for c in cuts if c[1] > c[0] + 1e-6):
            if a > at + 1e-6:
                out.append(((at, p[1]), (a, p[1])) if horizontal else ((p[0], at), (p[0], a)))
            at = max(at, b)
        if hi > at + 1e-6:
            out.append(((at, p[1]), (hi, p[1])) if horizontal else ((p[0], at), (p[0], hi)))
    seen, clean = set(), []
    for p, q in out:
        key = (tuple(round(v, 3) for v in sorted([p, q])[0]), tuple(round(v, 3) for v in sorted([p, q])[1]))
        if key not in seen:
            seen.add(key)
            clean.append((p, q))
    for o in fx.openings:                                                 # a window carries a thin glazing line along the middle of the gap
        if o.kind == "window":
            w = fx.walls[o.wall]
            if abs(w.p1[1] - w.p0[1]) < 1e-9:
                s = 1 if w.p1[0] > w.p0[0] else -1
                clean.append(((w.p0[0] + s * (o.center - o.width / 2), w.p0[1]), (w.p0[0] + s * (o.center + o.width / 2), w.p0[1])))
            else:
                s = 1 if w.p1[1] > w.p0[1] else -1
                clean.append(((w.p0[0], w.p0[1] + s * (o.center - o.width / 2)), (w.p0[0], w.p0[1] + s * (o.center + o.width / 2))))
    return clean


def _dimension_primitives(d: Dim, scale_text: float) -> dict[str, Any]:
    """Lines (mm) of one dimension: the dimension line, two extension lines and the terminators, plus the text and its position."""
    over = 150.0
    gap = 100.0
    lines: list[tuple[tuple[float, float], tuple[float, float]]] = []
    arrow = 220.0
    if d.axis == "x":
        y = d.at
        sgn = 1 if y >= d.reference[0] else -1
        for x in (d.a, d.b):
            lines.append(((x, d.reference[0] + sgn * gap), (x, y + sgn * over)))
        lines.append(((d.a, y), (d.b, y)))
        xa, xb = sorted((d.a, d.b))
        if d.style == "tick":
            for x in (d.a, d.b):
                lines.append(((x - 90, y - 90), (x + 90, y + 90)))
        else:
            for x, s in ((xa, 1), (xb, -1)):
                lines += [((x, y), (x + s * arrow, y + arrow / 3.5)), ((x, y), (x + s * arrow, y - arrow / 3.5))]
        center = ((d.a + d.b) / 2, y)
    else:
        x = d.at
        sgn = 1 if x >= d.reference[0] else -1
        for yv in (d.a, d.b):
            lines.append(((d.reference[0] + sgn * gap, yv), (x + sgn * over, yv)))
        lines.append(((x, d.a), (x, d.b)))
        ya, yb = sorted((d.a, d.b))
        if d.style == "tick":
            for yv in (d.a, d.b):
                lines.append(((x - 90, yv - 90), (x + 90, yv + 90)))
        else:
            for yv, s in ((ya, 1), (yb, -1)):
                lines += [((x, yv), (x + arrow / 3.5, yv + s * arrow)), ((x, yv), (x - arrow / 3.5, yv + s * arrow))]
        center = (x, (d.a + d.b) / 2)
    value = abs(d.b - d.a)
    text = f"{value / 1000:.2f}" if d.unit == "m" else f"{value:.0f}"
    return {"lines": lines, "text": text, "center": center, "axis": d.axis}


def _extent(fx: Fixture) -> tuple[float, float, float, float]:
    xs, ys = [], []
    for p, q in _outline_segments(fx):
        xs += [p[0], q[0]]
        ys += [p[1], q[1]]
    for d in fx.dims:
        for p, q in _dimension_primitives(d, 1)["lines"]:
            xs += [p[0], q[0]]
            ys += [p[1], q[1]]
        c = _dimension_primitives(d, 1)["center"]
        xs.append(c[0] - 800)
        xs.append(c[0] + 800)
        ys.append(c[1] - 600)
        ys.append(c[1] + 600)
    return min(xs) - 600, min(ys) - 600, max(xs) + 600, max(ys) + 600


# ----------------------------------------------------------------------------------------------------------------------- renderers
def render_raster(fx: Fixture) -> tuple[bytes, dict[str, Any]]:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    import numpy as np
    x0, y0, x1, y1 = _extent(fx)
    s = fx.px_per_mm
    W, H = int((x1 - x0) * s) + 1, int((y1 - y0) * s) + 1
    img = Image.new("L", (W, H), 255)
    dr = ImageDraw.Draw(img)
    px = lambda p: ((p[0] - x0) * s, (y1 - p[1]) * s)                     # plan y is up, image y is down
    width = max(2, int(round(25 * s)))
    for p, q in _outline_segments(fx):
        dr.line([px(p), px(q)], fill=0, width=width)
    thin = max(1, int(round(12 * s)))
    font = ImageFont.truetype(FONT, max(10, int(round(260 * s)))) if FONT else ImageFont.load_default()
    for d in fx.dims:
        prim = _dimension_primitives(d, s)
        for p, q in prim["lines"]:
            dr.line([px(p), px(q)], fill=0, width=thin)
        tw = dr.textbbox((0, 0), prim["text"], font=font)
        tw, th = tw[2] - tw[0], tw[3] - tw[1]
        cx, cy = px(prim["center"])
        if prim["axis"] == "x":
            dr.text((cx - tw / 2, cy - th - 70 * s), prim["text"], font=font, fill=0)
        else:
            tile = Image.new("L", (tw + 8, th + 12), 255)
            ImageDraw.Draw(tile).text((4, 0), prim["text"], font=font, fill=0)
            tile = tile.rotate(90, expand=True)
            img.paste(tile, (int(cx - tile.width - 70 * s), int(cy - tile.height / 2)))
    if fx.blur:
        img = img.filter(ImageFilter.GaussianBlur(fx.blur))
    if fx.noise:
        rng = np.random.default_rng(7)
        arr = np.asarray(img, float) + rng.normal(0, fx.noise, (H, W))
        img = Image.fromarray(np.clip(arr, 0, 255).astype("uint8"))
    buf = io.BytesIO()
    if fx.jpeg:
        img.save(buf, format="JPEG", quality=fx.jpeg)
    else:
        img.save(buf, format="PNG")
    return buf.getvalue(), {"width": W, "height": H, "origin_mm": (x0, y1), "px_per_mm": s}


def render_pdf(fx: Fixture) -> tuple[bytes, dict[str, Any]]:
    from reportlab.pdfgen import canvas
    x0, y0, x1, y1 = _extent(fx)
    k = 0.2                                                                 # PDF points per mm (arbitrary: the reconstruction must calibrate it from the dimensions)
    W, H = (x1 - x0) * k, (y1 - y0) * k
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(W, H))
    pt = lambda p: ((p[0] - x0) * k, (p[1] - y0) * k)
    c.setLineWidth(1.4)
    for p, q in _outline_segments(fx):
        a, b = pt(p), pt(q)
        c.line(a[0], a[1], b[0], b[1])
    c.setLineWidth(0.6)
    c.setFont("Helvetica", 11)
    for d in fx.dims:
        prim = _dimension_primitives(d, 1)
        for p, q in prim["lines"]:
            a, b = pt(p), pt(q)
            c.line(a[0], a[1], b[0], b[1])
        cx, cy = pt(prim["center"])
        if prim["axis"] == "x":
            c.drawCentredString(cx, cy + 5, prim["text"])
        else:
            c.saveState()
            c.translate(cx - 5, cy)
            c.rotate(90)
            c.drawCentredString(0, 0, prim["text"])
            c.restoreState()
    c.showPage()
    c.save()
    return buf.getvalue(), {"width": W, "height": H, "origin_mm": (x0, y1), "pt_per_mm": k}


def render(fx: Fixture) -> tuple[bytes, str, dict[str, Any]]:
    if fx.kind == "pdf":
        data, meta = render_pdf(fx)
        return data, "pdf", meta
    if fx.kind == "pdf-raster":
        import subprocess
        import tempfile
        pdf, meta = render_pdf(fx)
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "a.pdf").write_bytes(pdf)
            subprocess.run(["pdftoppm", "-r", "100", "-png", "-singlefile", str(Path(tmp, "a.pdf")), str(Path(tmp, "a"))], check=True, capture_output=True)
            return Path(tmp, "a.png").read_bytes(), "png", {**meta, "note": "PDF rasterised at 100 dpi"}
    data, meta = render_raster(fx)
    return data, "jpeg" if fx.jpeg else "png", meta


# ----------------------------------------------------------------------------------------------------------------------- the fixture set
def _box_walls(x0: float, y0: float, x1: float, y1: float, t: float = 200.0) -> list[Wall]:
    """Four walls around the rectangle whose *centre lines* run through the given corners (so the outer faces are t/2 outside)."""
    return [Wall((x0, y0), (x1, y0), t), Wall((x1, y0), (x1, y1), t), Wall((x1, y1), (x0, y1), t), Wall((x0, y1), (x0, y0), t)]


def fixtures() -> list[Fixture]:
    out: list[Fixture] = []

    def faces(w: Wall) -> tuple[float, float]:
        return (w.p0[0] - w.t / 2, w.p1[0] + w.t / 2) if abs(w.p1[1] - w.p0[1]) < 1e-9 else (w.p0[1] - w.t / 2, w.p1[1] + w.t / 2)

    # F01 single room, tick dims on outer faces
    w = _box_walls(0, 0, 5000, 4000)
    out.append(Fixture("single-room", w, [Opening(0, 1500, 900), Opening(1, 2000, 1200, "window")],
                       [Dim("x", -100, 5100, -900, (-100, 0)), Dim("y", -100, 4100, 5900, (5100, 0))]))
    # F02 two rooms, partition, chain dims along the bottom
    w = _box_walls(0, 0, 8000, 4000) + [Wall((3500, 0), (3500, 4000), 150)]
    out.append(Fixture("two-rooms", w, [Opening(0, 1700, 900), Opening(4, 2000, 800), Opening(2, 6000, 1500, "window")],
                       [Dim("x", -100, 3425, -900, (-100, 0)), Dim("x", 3425, 8100, -900, (-100, 0)), Dim("x", -100, 8100, -1700, (-100, 0)), Dim("y", -100, 4100, 8900, (8100, 0))]))
    # F03 L-shaped plan
    w = [Wall((0, 0), (6000, 0)), Wall((6000, 0), (6000, 2500)), Wall((6000, 2500), (2500, 2500)), Wall((2500, 2500), (2500, 5000)), Wall((2500, 5000), (0, 5000)), Wall((0, 5000), (0, 0))]
    out.append(Fixture("l-shape", w, [Opening(0, 3000, 1000), Opening(5, 2500, 1200, "window")],
                       [Dim("x", -100, 6100, -900, (-100, 0)), Dim("y", -100, 5100, -1000, (-100, 0)), Dim("y", -100, 2600, 7000, (6100, 0))]))
    # F04 three rooms in a row, dimensions in metres
    w = _box_walls(0, 0, 9000, 3600) + [Wall((3000, 0), (3000, 3600), 150), Wall((6000, 0), (6000, 3600), 150)]
    out.append(Fixture("three-rooms-m", w, [Opening(0, 1500, 900), Opening(4, 1800, 800), Opening(5, 1800, 800), Opening(2, 7500, 1200, "window")],
                       [Dim("x", -100, 3075, -900, (-100, 0), unit="m"), Dim("x", 3075, 6075, -900, (-100, 0), unit="m"), Dim("x", 6075, 9100, -900, (-100, 0), unit="m"),
                        Dim("x", -100, 9100, -1700, (-100, 0), unit="m"), Dim("y", -100, 3700, 10000, (9100, 0), unit="m")], px_per_mm=0.14))
    # F05 noisy raster
    w = _box_walls(0, 0, 6000, 4500)
    out.append(Fixture("noisy-raster", w, [Opening(0, 2000, 900)], [Dim("x", -100, 6100, -900, (-100, 0)), Dim("y", -100, 4600, 6900, (6100, 0))], blur=0.8, noise=14.0))
    # F06 vector PDF of the two-room plan
    f2 = fixtures_two_room()
    out.append(Fixture("two-rooms-pdf", f2.walls, f2.openings, f2.dims, kind="pdf"))
    # F07 PDF rasterised
    out.append(Fixture("three-rooms-pdf-raster", list(w), [Opening(0, 2000, 900)], [Dim("x", -100, 6100, -900, (-100, 0)), Dim("y", -100, 4600, 6900, (6100, 0))], kind="pdf-raster"))
    # F08 arrowheads instead of ticks
    w = _box_walls(0, 0, 7000, 3200)
    out.append(Fixture("arrow-dims", w, [Opening(0, 1800, 1000), Opening(2, 4500, 1400, "window")],
                       [Dim("x", -100, 7100, -900, (-100, 0), style="arrow"), Dim("y", -100, 3300, 7900, (7100, 0), style="arrow")], px_per_mm=0.15))
    # F09 mixed thickness (250 external, 150 partitions), two partitions
    w = _box_walls(0, 0, 8400, 5000, 250) + [Wall((2800, 0), (2800, 5000), 150), Wall((2800, 2500), (8400, 2500), 150)]
    out.append(Fixture("mixed-thickness", w, [Opening(0, 1400, 900), Opening(4, 1200, 800), Opening(5, 4000, 800), Opening(1, 1200, 1200, "window")],
                       [Dim("x", -125, 8525, -1000, (-125, 0)), Dim("y", -125, 5125, 9400, (8525, 0)), Dim("x", 2725, 8525, -1900, (-125, 0))]))
    # F10 JPEG-compressed
    w = _box_walls(0, 0, 5500, 3800)
    out.append(Fixture("jpeg", w, [Opening(0, 1200, 900), Opening(1, 1900, 1200, "window")], [Dim("x", -100, 5600, -900, (-100, 0)), Dim("y", -100, 3900, 6400, (5600, 0))], jpeg=45, px_per_mm=0.13))
    # F11 larger plan: four rooms
    w = _box_walls(0, 0, 9000, 7000) + [Wall((4500, 0), (4500, 7000), 150), Wall((0, 3500), (9000, 3500), 150)]
    out.append(Fixture("four-rooms", w, [Opening(0, 2000, 900), Opening(4, 1700, 800), Opening(5, 1700, 800), Opening(1, 1500, 1200, "window"), Opening(2, 6500, 1400, "window")],
                       [Dim("x", -100, 9100, -900, (-100, 0)), Dim("y", -100, 7100, 10000, (9100, 0)), Dim("x", -100, 4575, -1700, (-100, 0)), Dim("y", -100, 3575, -1000, (-100, 0))], px_per_mm=0.1))
    # F12 small room, higher resolution
    w = _box_walls(0, 0, 3600, 2800)
    out.append(Fixture("small-room", w, [Opening(0, 1200, 800)], [Dim("x", -100, 3700, -800, (-100, 0)), Dim("y", -100, 2900, 4500, (3700, 0))], px_per_mm=0.2))
    for fx in out:
        fx.rooms = _room_areas(fx)
    return out


def fixtures_two_room() -> Fixture:
    w = _box_walls(0, 0, 8000, 4000) + [Wall((3500, 0), (3500, 4000), 150)]
    return Fixture("tmp", w, [Opening(0, 1700, 900), Opening(4, 2000, 800), Opening(2, 6000, 1500, "window")],
                   [Dim("x", -100, 3425, -900, (-100, 0)), Dim("x", 3425, 8100, -900, (-100, 0)), Dim("x", -100, 8100, -1700, (-100, 0)), Dim("y", -100, 4100, 8900, (8100, 0))])


def _room_areas(fx: Fixture) -> list[float]:
    """Interior areas by rasterising the solid walls at 5 mm and counting the closed free regions (the gaps of openings are closed across the wall thickness)."""
    import numpy as np
    from scipy import ndimage
    xs = [v for w in fx.walls for v in (w.p0[0], w.p1[0])]
    ys = [v for w in fx.walls for v in (w.p0[1], w.p1[1])]
    t = max(w.t for w in fx.walls)
    x0, y0, x1, y1 = min(xs) - t, min(ys) - t, max(xs) + t, max(ys) + t
    cell = 5.0
    W, H = int((x1 - x0) / cell) + 1, int((y1 - y0) / cell) + 1
    grid = np.zeros((H, W), bool)
    for w in fx.walls:
        a, b, c, d = _rect(w)
        grid[round((b - y0) / cell):round((d - y0) / cell), round((a - x0) / cell):round((c - x0) / cell)] = True
    free, n = ndimage.label(~grid)
    outside = {free[0, 0]}
    areas = []
    for k in range(1, n + 1):
        if k in outside:
            continue
        areas.append(round(float((free == k).sum()) * cell * cell / 1e6, 3))
    return sorted(areas)
