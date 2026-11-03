"""Beam profiles: the steel sections beams can be drawn in, and the geometry and section properties that follow from them.

A beam is either **rectangular** (the original: a width and a depth) or one of these families, each with a list of nominal sizes and a *custom* size
whose dimensions can be typed in:

* wide-flange and I sections ``HEA``, ``HEB``, ``HEM``, ``IPE``, ``INP`` (h, b, web tw, flange tf);
* channels ``UPE`` (parallel flanges) and ``UNP`` (tapered flanges) (h, b, tw, tf);
* angles ``L`` equal legs (a, t) and unequal legs (a long, b short, t), named *L gel.* and *L ong.* in the Dutch tables;
* the lipped cold-formed channel ``C`` (h, b, lip c, thickness t);
* hollow sections ``CHS`` (d, t), ``SHS`` (a, t) and ``RHS`` (h, b, t).

**What the sizes are.** The nominal dimensions are typed in from the usual European tables (EN 10365 for I and channel sections, EN 10056-1 for angles,
EN 10210/10219 for hollow sections, DIN 1025/1026 for INP and UNP) and are **not checked against a supplier's current catalogue**: confirm a size against
the table of the supplier or the standard before relying on it. The cold-formed C sizes are generic and have no standard designation; use *custom*
for a manufacturer's profile. The root radii, toe radii and the taper of INP/UNP flanges (their flange thickness is the mean) are not modelled, so the
solid and the section properties computed from its outline differ a little from the tabulated ones (a few percent in I, less in the area).
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from ..tools.errors import BadRequest
from . import geometry as G

Ring = list[tuple[float, float]]
CIRCLE = 32                                         # sides of a circular hollow section


def _table(text: str, keys: Sequence[str], name: str, label: Any = None) -> dict[str, dict[str, float]]:
    """Rows of numbers ``a b c ...`` -> {designation: {key: value}}. The designation is ``name`` plus the first numbers shown in ``label`` (a callable)."""
    out: dict[str, dict[str, float]] = {}
    for line in text.strip().splitlines():
        nums = [float(x) for x in line.split()]
        dims = dict(zip(keys, nums))
        out[label(dims) if label else f"{name} {nums[0]:g}"] = dims
    return out


_I = ("h", "b", "tw", "tf")
_NUM = lambda d: f"{d['h']:g}"                                                         # noqa: E731

HEA = """96 100 5 8
114 120 5 8
133 140 5.5 8.5
152 160 6 9
171 180 6 9.5
190 200 6.5 10
210 220 7 11
230 240 7.5 12
250 260 7.5 12.5
270 280 8 13
290 300 8.5 14
310 300 9 15.5
330 300 9.5 16.5
350 300 10 17.5
390 300 11 19
440 300 11.5 21
490 300 12 23
590 300 13 25"""
HEA_SIZES = (100, 120, 140, 160, 180, 200, 220, 240, 260, 280, 300, 320, 340, 360, 400, 450, 500, 600)
HEB = """100 100 6 10
120 120 6.5 11
140 140 7 12
160 160 8 13
180 180 8.5 14
200 200 9 15
220 220 9.5 16
240 240 10 17
260 260 10 17.5
280 280 10.5 18
300 300 11 19
320 300 11.5 20.5
340 300 12 21.5
360 300 12.5 22.5
400 300 13.5 24
450 300 14 26
500 300 14.5 28
600 300 15.5 30"""
HEM = """120 106 12 20
140 126 12.5 21
160 146 13 22
180 166 14 23
200 186 14.5 24
220 206 15 25
240 226 15.5 26
270 248 18 32
290 268 18 32.5
310 288 18.5 33
340 310 21 39
359 309 21 40
377 309 21 40
395 308 21 40
432 307 21 40
478 307 21 40
524 306 21 40
620 305 21 40"""
IPE = """80 46 3.8 5.2
100 55 4.1 5.7
120 64 4.4 6.3
140 73 4.7 6.9
160 82 5 7.4
180 91 5.3 8
200 100 5.6 8.5
220 110 5.9 9.2
240 120 6.2 9.8
270 135 6.6 10.2
300 150 7.1 10.7
330 160 7.5 11.5
360 170 8 12.7
400 180 8.6 13.5
450 190 9.4 14.6
500 200 10.2 16
550 210 11.1 17.2
600 220 12 19"""
INP = """80 42 3.9 5.9
100 50 4.5 6.8
120 58 5.1 7.7
140 66 5.7 8.6
160 74 6.3 9.5
180 82 6.9 10.4
200 90 7.5 11.3
220 98 8.1 12.2
240 106 8.7 13.1
260 113 9.4 14.1
280 119 10.1 15.2
300 125 10.8 16.2
320 131 11.5 17.3
340 137 12.2 18.3
360 143 13 19.5
400 155 14.4 21.6
450 170 16.2 24.3
500 185 18 27
550 200 19 30
600 215 21.6 32.4"""
UPE = """80 50 4 7
100 55 4.5 7.5
120 60 5 8
140 65 5 9
160 70 5.5 9.5
180 75 5.5 10.5
200 80 6 11
220 85 6.5 12
240 90 7 12.5
270 95 7.5 13.5
300 100 9.5 15
330 105 11 16
360 110 12 17
400 115 13.5 18"""
UNP = """50 25 5 6
65 42 5.5 7.5
80 45 6 8
100 50 6 8.5
120 55 7 9
140 60 7 10
160 65 7.5 10.5
180 70 8 11
200 75 8.5 11.5
220 80 9 12.5
240 85 9.5 13
260 90 10 14
280 95 10 15
300 100 10 16
320 100 14 17.5
350 100 14 16
380 102 13.5 16
400 110 14 18"""
L_EQUAL = """20 3
25 3
25 4
30 3
30 4
35 4
40 4
40 5
45 5
50 5
50 6
60 6
60 8
70 7
80 8
90 9
100 10
120 12
150 15"""
L_UNEQUAL = """30 20 3
40 20 4
45 30 4
50 30 5
60 30 5
60 40 5
65 50 5
70 50 6
75 50 7
80 40 6
80 60 7
100 50 6
100 65 7
100 75 8
120 80 8
125 75 8
150 75 9
150 100 10
200 100 10
200 150 12"""
C_LIPPED = """100 50 20 2
120 50 20 2
150 60 20 2
150 65 20 2.5
200 75 25 2.5
200 75 25 3
250 75 25 3
300 85 30 3"""
CHS_ROWS = """26.9 3
33.7 3.2
42.4 3.2
48.3 3.2
60.3 3.6
76.1 3.6
88.9 4
101.6 5
114.3 5
139.7 6.3
168.3 6.3
193.7 8
219.1 8
273 10
323.9 10
355.6 12.5
406.4 12.5
508 12.5"""
SHS_ROWS = """40 3
50 3
60 4
70 4
80 4
80 5
90 5
100 5
100 6
120 6
120 8
140 8
150 8
160 8
180 10
200 10
250 10"""
RHS_ROWS = """50 30 3
60 40 3
80 40 4
100 50 4
100 60 5
120 60 5
120 80 6
140 80 6
150 100 6
160 80 6
180 100 8
200 100 8
200 120 8
250 150 10
300 200 10
400 200 12"""


def _named_i(rows: str, name: str, nominal: Sequence[int] | None = None) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for n, line in enumerate(rows.strip().splitlines()):
        d = dict(zip(_I, (float(x) for x in line.split())))
        out[f"{name} {nominal[n] if nominal else d['h']:g}"] = d
    return out


FAMILIES: dict[str, dict[str, Any]] = {
    "HEA": {"label": "HEA", "kind": "I", "sizes": _named_i(HEA, "HEA", HEA_SIZES)},
    "HEB": {"label": "HEB", "kind": "I", "sizes": _named_i(HEB, "HEB")},
    "HEM": {"label": "HEM", "kind": "I", "sizes": _named_i(HEM, "HEM", (100, 120, 140, 160, 180, 200, 220, 240, 260, 280, 300, 320, 340, 360, 400, 450, 500, 600))},
    "INP": {"label": "INP", "kind": "I", "sizes": _named_i(INP, "INP")},
    "IPE": {"label": "IPE", "kind": "I", "sizes": _named_i(IPE, "IPE")},
    "UPE": {"label": "UPE", "kind": "U", "sizes": _named_i(UPE, "UPE")},
    "UNP": {"label": "UNP", "kind": "U", "sizes": _named_i(UNP, "UNP")},
    "L_equal": {"label": "L equal legs (L gel.)", "kind": "L", "sizes": {f"L {a:g}x{a:g}x{t:g}": {"a": a, "b": a, "t": t} for a, t in (map(float, r.split()) for r in L_EQUAL.splitlines())}},
    "L_unequal": {"label": "L unequal legs (L ong.)", "kind": "L", "sizes": {f"L {a:g}x{b:g}x{t:g}": {"a": a, "b": b, "t": t} for a, b, t in (map(float, r.split()) for r in L_UNEQUAL.splitlines())}},
    "C": {"label": "C (lipped, cold-formed)", "kind": "C", "sizes": {f"C {h:g}x{b:g}x{c:g}x{t:g}": {"h": h, "b": b, "c": c, "t": t} for h, b, c, t in (map(float, r.split()) for r in C_LIPPED.splitlines())}},
    "CHS": {"label": "CHS (round tube)", "kind": "CHS", "sizes": {f"CHS {d:g}x{t:g}": {"d": d, "t": t} for d, t in (map(float, r.split()) for r in CHS_ROWS.splitlines())}},
    "SHS": {"label": "SHS (square tube)", "kind": "SHS", "sizes": {f"SHS {a:g}x{a:g}x{t:g}": {"a": a, "t": t} for a, t in (map(float, r.split()) for r in SHS_ROWS.splitlines())}},
    "RHS": {"label": "RHS (rectangular tube)", "kind": "RHS", "sizes": {f"RHS {h:g}x{b:g}x{t:g}": {"h": h, "b": b, "t": t} for h, b, t in (map(float, r.split()) for r in RHS_ROWS.splitlines())}},
}
DIMS: dict[str, list[tuple[str, str]]] = {
    "I": [("h", "Height h"), ("b", "Flange width b"), ("tw", "Web tw"), ("tf", "Flange tf")],
    "U": [("h", "Height h"), ("b", "Flange width b"), ("tw", "Web tw"), ("tf", "Flange tf")],
    "L": [("a", "Leg a"), ("b", "Leg b"), ("t", "Thickness t")],
    "C": [("h", "Height h"), ("b", "Flange width b"), ("c", "Lip c"), ("t", "Thickness t")],
    "CHS": [("d", "Diameter d"), ("t", "Wall t")],
    "SHS": [("a", "Side a"), ("t", "Wall t")],
    "RHS": [("h", "Height h"), ("b", "Width b"), ("t", "Wall t")],
}
CUSTOM = "custom"
NOTE = ("Nominal dimensions from the usual European tables, not checked against a supplier's current catalogue; root radii, toe radii and the taper of INP/UNP "
        "flanges are not modelled. Confirm a size against the supplier's table before relying on it.")


def family_dims(family: str) -> list[tuple[str, str]]:
    return DIMS[FAMILIES[family]["kind"]]


def default_size(family: str) -> str:
    """The size a family starts at when it is chosen: a common mid-range one."""
    return {"HEA": "HEA 200", "HEB": "HEB 200", "HEM": "HEM 200", "INP": "INP 200", "IPE": "IPE 200", "UPE": "UPE 200", "UNP": "UNP 200", "L_equal": "L 100x100x10",
            "L_unequal": "L 100x75x8", "C": "C 150x65x20x2.5", "CHS": "CHS 88.9x4", "SHS": "SHS 100x100x5", "RHS": "RHS 100x60x5"}[family]


def catalogue() -> dict[str, Any]:
    """Everything the client needs to offer the profiles: families, the dimensions each takes, and the nominal sizes."""
    return {"families": [{"key": k, "label": v["label"], "dims": [{"key": a, "label": b} for a, b in family_dims(k)], "default": default_size(k),
                          "sizes": [{"name": n, "dims": d} for n, d in v["sizes"].items()]} for k, v in FAMILIES.items()], "note": NOTE}


def check_family(family: Any) -> str:
    if family not in FAMILIES:
        raise BadRequest(f"profile must be 'rect' or one of {', '.join(FAMILIES)}")
    return str(family)


def dims_of(family: str, size: str, given: Mapping[str, Any] | None = None) -> dict[str, float]:
    """The dimensions (mm) of a size of a family, or the ones given when the size is ``custom``."""
    check_family(family)
    if size == CUSTOM:
        dims = {k: float(given[k]) for k, _ in family_dims(family) if given and given.get(k) is not None}
        if len(dims) != len(family_dims(family)):
            raise BadRequest(f"A custom {family} needs {', '.join(k for k, _ in family_dims(family))} (mm)")
        return validate(family, dims)
    row = FAMILIES[family]["sizes"].get(size)
    if row is None:
        raise BadRequest(f"'{size}' is not a {family} size", details={"sizes": list(FAMILIES[family]["sizes"])[:60]})
    return dict(row)


def validate(family: str, d: Mapping[str, float]) -> dict[str, float]:
    kind = FAMILIES[family]["kind"]
    d = {k: float(v) for k, v in d.items()}
    if any(not math.isfinite(v) or v <= 0 for v in d.values()):
        raise BadRequest("Every profile dimension must be a positive number")
    if max(d.values()) > 3000:
        raise BadRequest("A profile dimension over 3 m is a typo")
    if kind in ("I", "U") and not (2 * d["tf"] < d["h"] and d["tw"] < d["b"]):
        raise BadRequest("The flanges and web are too thick for the height and flange width")
    if kind == "L" and not (d["t"] < min(d["a"], d["b"])):
        raise BadRequest("The leg thickness must be less than the legs")
    if kind == "C" and not (2 * d["t"] < d["b"] and 2 * d["t"] < d["h"] and d["t"] < d["c"] < d["h"] / 2):
        raise BadRequest("The lip must be longer than the wall and less than half the height, and the wall thin enough for the flange")
    if kind == "CHS" and not (2 * d["t"] < d["d"]):
        raise BadRequest("The wall must be less than the radius")
    if kind == "SHS" and not (2 * d["t"] < d["a"]):
        raise BadRequest("The wall must be less than half the side")
    if kind == "RHS" and not (2 * d["t"] < min(d["h"], d["b"])):
        raise BadRequest("The wall must be less than half the shorter side")
    return d


# --------------------------------------------------------------------------------------------------------------------------------- outlines
def _box(w: float, h: float, x0: float = 0.0, y0: float = 0.0) -> Ring:
    return [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)]


def rings(family: str, dims: Mapping[str, float]) -> list[Ring]:
    """The section as outlines in (s across, z up), millimetres, counter-clockwise, centred on its bounding box: one ring for a solid section, the outer
    ring then the inner ring (same vertex count and order, so the two can be joined) for a hollow one."""
    kind = FAMILIES[family]["kind"]
    d = validate(family, dims)
    if kind == "I":
        h, b, tw, tf = d["h"], d["b"], d["tw"], d["tf"]
        pts = [(-b / 2, -h / 2), (b / 2, -h / 2), (b / 2, -h / 2 + tf), (tw / 2, -h / 2 + tf), (tw / 2, h / 2 - tf), (b / 2, h / 2 - tf), (b / 2, h / 2), (-b / 2, h / 2),
               (-b / 2, h / 2 - tf), (-tw / 2, h / 2 - tf), (-tw / 2, -h / 2 + tf), (-b / 2, -h / 2 + tf)]
        return [pts]
    if kind == "U":
        h, b, tw, tf = d["h"], d["b"], d["tw"], d["tf"]
        return [[(-b / 2, -h / 2), (b / 2, -h / 2), (b / 2, -h / 2 + tf), (-b / 2 + tw, -h / 2 + tf), (-b / 2 + tw, h / 2 - tf), (b / 2, h / 2 - tf), (b / 2, h / 2), (-b / 2, h / 2)]]
    if kind == "L":
        a, b, t = d["a"], d["b"], d["t"]                                                     # a: the upright leg, b: the foot
        return [[(-b / 2, -a / 2), (b / 2, -a / 2), (b / 2, -a / 2 + t), (-b / 2 + t, -a / 2 + t), (-b / 2 + t, a / 2), (-b / 2, a / 2)]]
    if kind == "C":
        h, b, c, t = d["h"], d["b"], d["c"], d["t"]
        x0, y0 = -b / 2, -h / 2
        return [[(x0, y0), (x0 + b, y0), (x0 + b, y0 + c), (x0 + b - t, y0 + c), (x0 + b - t, y0 + t), (x0 + t, y0 + t), (x0 + t, y0 + h - t), (x0 + b - t, y0 + h - t),
                 (x0 + b - t, y0 + h - c), (x0 + b, y0 + h - c), (x0 + b, y0 + h), (x0, y0 + h)]]
    if kind == "CHS":
        ro, ri = d["d"] / 2, d["d"] / 2 - d["t"]
        ring = [(math.cos(2 * math.pi * i / CIRCLE), math.sin(2 * math.pi * i / CIRCLE)) for i in range(CIRCLE)]
        return [[(ro * c, ro * s) for c, s in ring], [(ri * c, ri * s) for c, s in ring]]
    h, b = (d["a"], d["a"]) if kind == "SHS" else (d["h"], d["b"])
    t = d["t"]
    return [[(-b / 2, -h / 2), (b / 2, -h / 2), (b / 2, h / 2), (-b / 2, h / 2)], [(-b / 2 + t, -h / 2 + t), (b / 2 - t, -h / 2 + t), (b / 2 - t, h / 2 - t), (-b / 2 + t, h / 2 - t)]]


def rolled(rs: list[Ring], roll_deg: float) -> list[Ring]:
    """The rings turned by ``roll_deg`` (counter-clockwise, in the section plane), then placed so the bounding box is centred across and its top is at z = 0."""
    a = math.radians(roll_deg)
    c, s = math.cos(a), math.sin(a)
    turned = [[(x * c - z * s, x * s + z * c) for x, z in ring] for ring in rs]
    xs = [p[0] for p in turned[0]]
    zs = [p[1] for p in turned[0]]
    cx, top = (min(xs) + max(xs)) / 2, max(zs)
    return [[(x - cx, z - top) for x, z in ring] for ring in turned]


def bounds(rs: list[Ring]) -> tuple[float, float]:
    """(width across, depth up) of the outer ring."""
    xs = [p[0] for p in rs[0]]
    zs = [p[1] for p in rs[0]]
    return max(xs) - min(xs), max(zs) - min(zs)


def properties(rs: list[Ring]) -> dict[str, float]:
    """Area (mm²), centroid height, and the second moments about the horizontal and vertical centroidal axes (mm⁴) of a section from its outline: a
    hollow section's inner ring is a hole. Straight shoelace sums, exact for the polygon."""
    area = cx = cz = ixx = izz = 0.0
    sums = []
    for n, ring in enumerate(rs):
        pts = ring if n == 0 else ring[::-1]                                              # the hole runs the other way, so it counts negatively
        if G.signed_area(ring) < 0 and n == 0:
            pts = ring[::-1]
        a = sx = sz = jx = jz = 0.0
        for (x0, z0), (x1, z1) in zip(pts, pts[1:] + pts[:1]):
            cr = x0 * z1 - x1 * z0
            a += cr / 2
            sx += (x0 + x1) * cr / 6
            sz += (z0 + z1) * cr / 6
            jx += (z0 * z0 + z0 * z1 + z1 * z1) * cr / 12                                  # ∫ z² dA about the origin
            jz += (x0 * x0 + x0 * x1 + x1 * x1) * cr / 12
        sums.append((a, sx, sz, jx, jz))
    area = sum(s[0] for s in sums)
    if area <= 0:
        raise BadRequest("The section has no area")
    cx, cz = sum(s[1] for s in sums) / area, sum(s[2] for s in sums) / area
    ixx = sum(s[3] for s in sums) - area * cz * cz
    izz = sum(s[4] for s in sums) - area * cx * cx
    zs = [p[1] for p in rs[0]]
    return {"area_mm2": area, "centroid_z_mm": cz, "ix_mm4": ixx, "iz_mm4": izz, "extreme_fibre_mm": max(max(zs) - cz, cz - min(zs))}


# ------------------------------------------------------------------------------------------------------------------------------------ mesh
def prism(rs: list[Ring], length: float) -> G.Mesh:
    """The section extruded from t = 0 to ``length``, as vertices ``[s, z, t]`` (the units of the rings), outward-facing."""
    outer = rs[0]
    if G.signed_area(outer) < 0:
        outer = outer[::-1]
        rs = [outer] + [r[::-1] for r in rs[1:]]
    try:
        if len(rs) == 1:
            return G.prism(outer, 0.0, length)
        inner = rs[1]
        n = len(outer)
        if len(inner) != n:
            raise BadRequest("A hollow section's inner and outer outlines must have the same number of corners")
        verts = [[x, z, 0.0] for x, z in outer] + [[x, z, length] for x, z in outer] + [[x, z, 0.0] for x, z in inner] + [[x, z, length] for x, z in inner]
        faces: list[list[int]] = []
        for i in range(n):
            j = (i + 1) % n
            o0, o1, oj0, oj1 = i, n + i, j, n + j
            i0, i1, ij0, ij1 = 2 * n + i, 3 * n + i, 2 * n + j, 3 * n + j
            faces += [[o0, oj0, oj1], [o0, oj1, o1],                                         # outside
                      [i0, i1, ij1], [i0, ij1, ij0],                                         # inside, facing the hole
                      [o1, oj1, ij1], [o1, ij1, i1],                                         # the end at t = length
                      [o0, ij0, oj0], [o0, i0, ij0]]                                         # the end at t = 0
        return verts, faces
    except G.GeometryError as exc:
        raise BadRequest(str(exc)) from exc
