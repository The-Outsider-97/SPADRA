"""Layered elements: a wall, floor or roof is a stack of layers, each with a material, a thickness and its own relationship to the openings in it.

Model
    ``spadra:layers`` is an ordered list of layers (a wall: from its left face to its right; a floor or roof: from its top down). An element that has
    none is one implicit layer made of its own material and thickness, so every wall, floor and roof is already "a stack of one", and adding a layer
    simply writes that stack out. The element's ``thickness`` is always the sum of its layers: it is derived, and editing it on a stack of several
    layers is refused (edit a layer instead). Each layer is built as its own closed shell in the element's one mesh, as foundations do for their
    build-ups, so the element keeps one identity while sections and quantities can tell its layers apart.

Opening inheritance
    ``openings`` is ``all`` (the layer is cut by every door, window and void in the element, now and in future), ``none`` (the layer runs straight
    through), or ``selected`` (only the openings named in ``opening_ids``). A partial-depth opening is simply an opening that only some layers inherit.
    The relationship is stored on the layer by the opening's id, so it survives every rebuild, and ids of openings that no longer exist are dropped.

Layer edges (walls)
    ``start``/``end``/``bottom``/``top`` extend (positive) or trim (negative) a layer beyond the wall's own ends and height, in metres. A layer end that
    would run into a wall joint, past another layer's end it must meet, or leave nothing of the layer, is refused with a message that names the layer.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from ..tools.errors import BadRequest
from ..tools.validation import new_id, number, text
from . import elements as E
from . import geometry as G

MAX_LAYERS = 24
MODES = ("all", "none", "selected")
SNAP = 0.005                       # m: a layer edge this close to the wall's own edge is that edge
LAYERED = ("wall", "floor", "roof")
EDGES = ("start", "end", "bottom", "top")


def materials() -> tuple[str, ...]:
    from . import foundations as FD
    return tuple(dict.fromkeys(E.MATERIALS + FD.LAYER_MATERIALS))


def is_layered(meta: Mapping[str, Any]) -> bool:
    return bool(E.m(meta, "layers")) and len(E.m(meta, "layers")) > 1


def _full(row: Mapping[str, Any]) -> dict[str, Any]:
    out = {"id": str(row.get("id") or new_id()[:8]), "name": str(row.get("name") or row.get("material") or "Layer"), "material": str(row.get("material") or "Concrete"),
           "thickness": float(row["thickness"]), "openings": str(row.get("openings") or "all"), "opening_ids": [str(i) for i in row.get("opening_ids") or []]}
    for edge in EDGES:
        out[edge] = float(row.get(edge) or 0.0)
    return out


def stack(project: Any, oid: str) -> list[dict[str, Any]]:
    """The element's layers, each with ``offset``: how far from the first face its own first face lies (m)."""
    meta = E.meta_of(project, oid)
    raw = E.m(meta, "layers")
    rows = [_full(r) for r in raw] if raw else [_full({"id": "main", "name": str(E.m(meta, "type", "Layer")), "material": E.m(meta, "material", "Concrete"),
                                                            "thickness": float(E.m(meta, "thickness"))})]
    cursor = 0.0
    for r in rows:
        r["offset"] = cursor
        cursor += r["thickness"]
    return rows


def total(layers: Sequence[Mapping[str, Any]]) -> float:
    return sum(float(r["thickness"]) for r in layers)


def _store(project: Any, oid: str, rows: list[dict[str, Any]]) -> None:
    """Write the stack back: the element's thickness follows the layers, and its material is the first layer's."""
    E._attach(project, oid, {"layers": [{k: v for k, v in r.items() if k != "offset"} for r in rows], "thickness": total(rows), "material": rows[0]["material"]})


def _find(rows: Sequence[Mapping[str, Any]], lid: str) -> int:
    for i, r in enumerate(rows):
        if r["id"] == lid:
            return i
    raise BadRequest("That layer does not exist", hint="Layers are listed in the Properties panel")


def _check_cat(project: Any, oid: str) -> str:
    cat = E.category(project, oid)
    if cat not in LAYERED:
        raise BadRequest("Only a wall, floor or roof is built in layers")
    return cat


def _rebuild(project: Any, oid: str) -> None:
    E.regenerate(project, oid)


# ----------------------------------------------------------------------------------------------------------------------------- editing
def add_layer(project: Any, oid: str, p: Mapping[str, Any]) -> str:
    """Add a layer on a chosen physical side. The stack order determines the face it grows from."""
    cat = _check_cat(project, oid)
    rows = stack(project, oid)
    if len(rows) >= MAX_LAYERS:
        raise BadRequest(f"An element has at most {MAX_LAYERS} layers")
    material = p.get("material") or ("Plasterboard" if cat == "wall" else rows[-1]["material"])
    if material not in materials():
        raise BadRequest(f"material must be one of {', '.join(materials())}")
    thickness = number(p.get("thickness", 12.5 if cat == "wall" else 50), "thickness (mm)", 1, 5000) / 1000.0
    mode = p.get("openings", "all")
    if mode not in MODES:
        raise BadRequest(f"openings must be one of {', '.join(MODES)}")
    opening_ids = p.get("opening_ids") or []
    if mode == "selected" and (not isinstance(opening_ids, list) or any(i not in {o["id"] for o in openers(project, oid)} for i in opening_ids)):
        raise BadRequest("Selected openings must belong to the host element")
    row = _full({"name": text(p.get("name") or material, "name", 60), "material": material, "thickness": thickness,
                 "openings": mode, "opening_ids": opening_ids if mode == "selected" else []})
    side = p.get("side")
    faces = {"wall": ("left", "right"), "floor": ("top", "bottom"), "roof": ("outside", "inside")}[cat]
    if side is not None and side not in faces:
        raise BadRequest(f"{cat} layers must use side {faces[0]} or {faces[1]}")
    if side is not None and p.get("position") is not None:
        raise BadRequest("Choose a physical side or a stack position, not both")
    position = (0 if side == faces[0] else len(rows)) if side is not None else (len(rows) if p.get("position") is None else int(number(p["position"], "position", 0, len(rows))))
    if cat == "wall":
        for edge in EDGES:
            if edge in p:
                row[edge] = number(p[edge], f"{edge} (mm)", -100000, 100000) / 1000.0
    rows.insert(position, row)
    # Preserve the pre-existing assembly's world-space faces. A layer on the
    # opposite side must grow outwards, not shift the original slab/roof.
    if side is not None:
        current_offset = float(E.m(E.meta_of(project, oid), "offset", 0.0))
        roof_kind = E.m(E.meta_of(project, oid), "roof", "flat")
        delta = (thickness if cat == "floor" and side == "top" else
                 -thickness if cat == "roof" and roof_kind == "flat" and side == "inside" else
                 thickness if cat == "roof" and roof_kind != "flat" and side == "outside" else 0.0)
        if delta:
            E._attach(project, oid, {"offset": current_offset + delta})
    _store(project, oid, rows)
    _rebuild(project, oid)
    return row["id"]


def remove_layer(project: Any, oid: str, lid: str) -> None:
    _check_cat(project, oid)
    rows = stack(project, oid)
    if len(rows) < 2:
        raise BadRequest("An element needs at least one layer")
    del rows[_find(rows, lid)]
    _store(project, oid, rows)
    _rebuild(project, oid)


def move_layer(project: Any, oid: str, lid: str, to: Any) -> None:
    _check_cat(project, oid)
    rows = stack(project, oid)
    row = rows.pop(_find(rows, lid))
    rows.insert(int(number(to, "position", 0, len(rows))), row)
    _store(project, oid, rows)
    _rebuild(project, oid)


def openers(project: Any, oid: str) -> list[dict[str, str]]:
    """What can cut this element's layers: its doors and windows (walls), and the voids that cut it."""
    from . import forms as FX
    out: list[dict[str, str]] = []
    cat = E.category(project, oid)
    if cat == "wall":
        for hid in E.hosted(project, oid):
            out.append({"id": hid, "name": project.model.get(hid)["name"], "kind": str(E.m(E.meta_of(project, hid), "category"))})
    if cat in ("wall", "floor"):
        vs = FX.voids(project)
        try:
            cuts = FX._cuts(project, oid, vs)[2]
        except BadRequest:
            cuts = []
        ids = {c[4] if isinstance(c, (list, tuple)) else c["id"] for c in (cuts if cat == "wall" else list(cuts))}
        out.extend({"id": v["id"], "name": v["name"], "kind": "void"} for v in vs if v["id"] in ids)
    return out


def set_field(project: Any, oid: str, key: str, value: Any) -> None:
    """Edit one thing about one layer. ``key`` is ``layer:<id>:<field>``: name, material, thickness (mm), openings (all/none/selected),
    start/end/bottom/top (mm; walls) or ``cut:<opening id>`` (yes/no: whether a selected layer is cut by that opening)."""
    cat = _check_cat(project, oid)
    parts = key.split(":")
    if len(parts) < 3:
        raise BadRequest("A layer property is layer:<id>:<field>")
    rows = stack(project, oid)
    row = rows[_find(rows, parts[1])]
    field = parts[2]
    if field == "name":
        row["name"] = text(value, "name", 60)
    elif field == "material":
        if value not in materials():
            raise BadRequest(f"material must be one of {', '.join(materials())}")
        row["material"] = str(value)
    elif field == "thickness":
        row["thickness"] = number(value, "thickness (mm)", 1, 5000) / 1000.0
    elif field == "openings":
        if value not in MODES:
            raise BadRequest(f"openings must be one of {', '.join(MODES)}")
        if value == "selected" and row["openings"] != "selected":
            row["opening_ids"] = [o["id"] for o in openers(project, oid) if row["openings"] == "all"]     # start from what it inherits now, then the person narrows it
        row["openings"] = str(value)
    elif field in EDGES:
        if cat != "wall":
            raise BadRequest("Only the layers of a wall can be extended or trimmed")
        row[field] = number(value, f"{field} (mm)", -100000, 100000) / 1000.0
    elif field == "cut" and len(parts) == 4:
        if row["openings"] != "selected":
            raise BadRequest("Set the layer's openings to 'selected' first")
        if parts[3] not in {o["id"] for o in openers(project, oid)}:
            raise BadRequest("That opening is not in this element")
        wanted = value in (True, "yes", "cut", 1)
        ids = [i for i in row["opening_ids"] if i != parts[3]] + ([parts[3]] if wanted else [])
        row["opening_ids"] = ids
    else:
        raise BadRequest(f"'{field}' is not a layer property")
    _store(project, oid, rows)
    _rebuild(project, oid)


def inherited(layer: Mapping[str, Any], opening_ids: Sequence[str]) -> list[str]:
    """Which of these openings cut this layer."""
    if layer["openings"] == "none":
        return []
    if layer["openings"] == "selected":
        return [i for i in opening_ids if i in layer["opening_ids"]]
    return list(opening_ids)


def prune(project: Any, oid: str) -> None:
    """Forget the selection of openings that no longer exist (a deleted door, a removed void), so a stored relationship never dangles."""
    rows = stack(project, oid)
    live = {o["id"] for o in openers(project, oid)}
    changed = False
    for r in rows:
        keep = [i for i in r["opening_ids"] if i in live]
        if keep != r["opening_ids"]:
            r["opening_ids"], changed = keep, True
    if changed and E.m(E.meta_of(project, oid), "layers"):
        E._attach(project, oid, {"layers": [{k: v for k, v in r.items() if k != "offset"} for r in rows]})


def preview(project: Any, oid: str, lid: str, mode: str, opening_ids: Sequence[str] = ()) -> dict[str, Any]:
    """What one layer would be cut by if its openings were set like this — without changing anything: the openings that cut it and those that
    do not, and for a wall the area they take out of that layer."""
    if mode not in MODES:
        raise BadRequest(f"openings must be one of {', '.join(MODES)}")
    rows = stack(project, oid)
    row = dict(rows[_find(rows, lid)], openings=mode, opening_ids=list(opening_ids))
    every = openers(project, oid)
    live = {o["id"] for o in every}
    cut = inherited(row, [o["id"] for o in every])
    area = 0.0
    if E.category(project, oid) == "wall":
        for o in every:
            if o["id"] in cut and o["kind"] in E.OPENINGS:
                w, h = float(E.m(E.meta_of(project, o["id"]), "width")), float(E.m(E.meta_of(project, o["id"]), "height"))
                area += w * h
    return {"layer": row["name"], "cut_by": [o for o in every if o["id"] in cut], "not_cut_by": [o for o in every if o["id"] not in cut],
            "unknown": [i for i in opening_ids if i not in live], "removed_area_m2": area}


# ------------------------------------------------------------------------------------------------------------------------------ walls
def _faces_shear(frame: Mapping[str, Any], y: float) -> tuple[float, float]:
    """How far a wall's end faces reach past its ends at depth ``y`` (m from its centre line, + toward the left face), from the faces' own offsets."""
    (sl, sr), (el, er) = frame["ends"]
    t = frame["thickness"]
    k = (y + t / 2) / t
    return sr + (sl - sr) * k, er + (el - er) * k


def edge_conflicts(frame: Mapping[str, Any], layers: Sequence[Mapping[str, Any]]) -> list[str]:
    """Every reason a layer's edges cannot be built, as sentences naming the layer."""
    problems: list[str] = []
    (sl, sr), (el, er) = frame["ends"]
    cut_start, cut_end = max(0.0, -sl, -sr), max(0.0, -el, -er)
    length, height = frame["length"], frame["height"]
    for lay in layers:
        n = lay["name"]
        if length + lay["start"] + lay["end"] < 0.01:
            problems.append(f"{n}: trimmed to nothing along the wall")
        if height + lay["top"] + lay["bottom"] < 0.01:
            problems.append(f"{n}: trimmed to nothing in height")
        if lay["start"] > SNAP and (sl, sr) != (0.0, 0.0):
            problems.append(f"{n}: cannot extend past the start of the wall, where it is joined to another wall")
        if lay["end"] > SNAP and (el, er) != (0.0, 0.0):
            problems.append(f"{n}: cannot extend past the end of the wall, where it is joined to another wall")
        if lay["start"] < -SNAP and -lay["start"] < cut_start - 1e-9:
            problems.append(f"{n}: its start would lie inside the corner joint ({cut_start * 1000:.0f} mm); trim by at least that much, or not at all")
        if lay["end"] < -SNAP and -lay["end"] < cut_end - 1e-9:
            problems.append(f"{n}: its end would lie inside the corner joint ({cut_end * 1000:.0f} mm); trim by at least that much, or not at all")
    return problems


def snapped(value: float, others: Sequence[float]) -> float:
    """A layer edge within ``SNAP`` of the wall's own edge (0) or of another layer's edge is that edge."""
    for target in (0.0, *others):
        if abs(value - target) <= SNAP:
            return target
    return value


def wall_mesh(project: Any, oid: str, frame: Mapping[str, Any], every: Sequence[Sequence[Any]]) -> G.Mesh:
    """The wall as one closed shell per layer, in the wall's own frame (centred on its mid point at mid height, x along it, y through it, z up).
    ``every`` is its openings as (x0, x1, z0, z1, id) from its start and base. Each layer gets the openings it inherits."""
    layers = stack(project, oid)
    problems = edge_conflicts(frame, layers)
    if problems:
        raise BadRequest("; ".join(problems), hint="Change the layers' start, end, top or bottom")
    length, height, thick = frame["length"], frame["height"], frame["thickness"]
    if abs(total(layers) - thick) > 1e-6:
        raise BadRequest("The layers do not add up to the wall's thickness")
    ids = [str(o[4]) for o in every]
    verts: list[list[float]] = []
    faces: list[list[int]] = []
    counts: list[int] = []
    y_left = thick / 2
    for lay in layers:
        t = lay["thickness"]
        y_right = y_left - t
        sl_i, sr_i = _faces_shear(frame, y_left)[0], _faces_shear(frame, y_right)[0]
        el_i, er_i = _faces_shear(frame, y_left)[1], _faces_shear(frame, y_right)[1]
        start, end, bottom, top = (snapped(lay[k], []) for k in EDGES)
        ends = ((sl_i if start == 0.0 else 0.0, sr_i if start == 0.0 else 0.0), (el_i if end == 0.0 else 0.0, er_i if end == 0.0 else 0.0))
        len_i, h_i = length + start + end, height + top + bottom
        cx, cz = (end - start) / 2, (top - bottom) / 2
        hits = set(inherited(lay, ids))
        local = []
        for x0, x1, z0, z1, oid_ in every:
            if str(oid_) not in hits:
                continue
            a0, a1 = max(x0, -start) - length / 2 - cx, min(x1, length + end) - length / 2 - cx          # an opening is clipped to what this layer covers
            b0, b1 = max(z0, -bottom) - height / 2 - cz, min(z1, height + top) - height / 2 - cz
            if a1 - a0 > 1e-9 and b1 - b0 > 1e-9:
                local.append((a0, a1, b0, b1))
        try:
            top_i = [(s_ - length / 2 - cx, h_ + top - height / 2 - cz) for s_, h_ in frame["top"]] or None        # the roof line, lifted by the layer's own top edge
            v, f = G.wall_mesh(len_i, t, h_i, local, ends, top_i)
        except G.GeometryError as exc:
            raise BadRequest(f"Layer '{lay['name']}': {exc}") from exc
        base = len(verts)
        verts += [[p[0] + cx, p[1] + y_left - t / 2, p[2] + cz] for p in v]
        faces += [[a + base, b + base, c + base] for a, b, c in f]
        counts.append(len(f))
        y_left = y_right
    record(project, oid, counts)
    return E._positive_volume((verts, faces))


def record(project: Any, oid: str, counts: Sequence[int]) -> None:
    """Remember how many faces each layer's shell has in the element's mesh (they are written in layer order), so each layer can be measured."""
    E._attach(project, oid, {"layer_faces": [int(c) for c in counts]})


def layer_volumes(project: Any, oid: str) -> list[dict[str, Any]]:
    """The volume of each layer (m³), measured on its own shell of the element's mesh, for quantities by material. Empty if the element is not layered
    or its shells cannot be told apart (then the element's total is all that can honestly be said)."""
    meta = E.meta_of(project, oid)
    counts = E.m(meta, "layer_faces")
    layers = stack(project, oid)
    if not is_layered(meta) or not counts or len(counts) != len(layers):
        return []
    mesh = project.model.tessellate(oid)
    verts, faces = mesh["vertices"], mesh["faces"]
    if sum(counts) != len(faces):
        return []
    out, at = [], 0
    for lay, n in zip(layers, counts):
        v = 0.0
        for a, b, c in faces[at:at + n]:
            p, q, r = verts[int(a)], verts[int(b)], verts[int(c)]
            v += (p[0] * (q[1] * r[2] - q[2] * r[1]) - p[1] * (q[0] * r[2] - q[2] * r[0]) + p[2] * (q[0] * r[1] - q[1] * r[0])) / 6.0
        out.append({"id": lay["id"], "name": lay["name"], "material": lay["material"], "volume_m3": abs(v)})
        at += n
    return out


def plan_bands(project: Any, oid: str, frame: Mapping[str, Any], cut_openings: Sequence[Mapping[str, Any]], f: float) -> list[dict[str, Any]]:
    """A layered wall in plan: for each layer, the pieces of its band that are cut at plan height (around the openings it inherits), as polygons in
    drawing units. ``cut_openings`` are those openings, each ``{"id", "x0", "x1"}`` along the wall."""
    layers = stack(project, oid)
    if len(layers) < 2:
        return []
    ids = [o["id"] for o in cut_openings]
    c, s = math.cos(frame["angle"]), math.sin(frame["angle"])
    thick, length = frame["thickness"], frame["length"]
    out = []
    y_left = thick / 2
    for lay in layers:
        y_right = y_left - lay["thickness"]
        hit = set(inherited(lay, ids))
        spans = sorted((o["x0"], o["x1"]) for o in cut_openings if o["id"] in hit)
        x_lo, x_hi = -lay["start"], length + lay["end"]
        pieces: list[tuple[float, float]] = []
        cursor = x_lo
        for x0, x1 in spans + [(x_hi, x_hi)]:
            if x0 - cursor > 1e-9:
                pieces.append((cursor, min(x0, x_hi)))
            cursor = max(cursor, x1)
        polys = []
        for a, b in pieces:
            corners = [(a, y_left), (b, y_left), (b, y_right), (a, y_right)]
            polys.append([[(frame["start"][0] + c * x - s * y) * f, (frame["start"][1] + s * x + c * y) * f] for x, y in corners])
        out.append({"layer": lay["id"], "name": lay["name"], "material": lay["material"], "polygons": polys})
        y_left = y_right
    return out


def loop_layers(project: Any, oid: str, loops: Sequence[Sequence[tuple[float, float]]], frame: Any) -> list[dict[str, Any]]:
    """Which layer each loop of a section's cut belongs to. ``loops`` are in the section's own (across, z) metres; ``frame`` is its ``Frame``. A wall's
    layers are told apart by where the loop lies through the wall's thickness, a floor's or roof's by its height."""
    layers = stack(project, oid)
    meta = E.meta_of(project, oid)
    cat = E.m(meta, "category")
    out = []
    for loop in loops:
        mid_u = sum(p[0] for p in loop) / len(loop)
        z_lo, z_hi = min(p[1] for p in loop), max(p[1] for p in loop)
        if cat == "wall":
            fr = E.wall_frame(project, oid)
            px, py = frame.ox + frame.ux * mid_u, frame.oy + frame.uy * mid_u
            y = -(px - fr["start"][0]) * math.sin(fr["angle"]) + (py - fr["start"][1]) * math.cos(fr["angle"])
            pos = fr["thickness"] / 2 - y                                  # distance from the left face
        elif cat == "floor":
            pos = E.level_elevation(project, E.m(meta, "level")) + float(E.m(meta, "offset", 0.0)) - (z_lo + z_hi) / 2
        else:                                                               # a roof's layers hang below its top surface, which slopes: read it where the cut is
            from . import rooms as R
            px, py = frame.ox + frame.ux * mid_u, frame.oy + frame.uy * mid_u
            facets = E.roof_facets(project, meta)
            ring, plane = next((fc for fc in facets if R.contains(fc[0], (px, py))), facets[0])
            pos = plane[0] * px + plane[1] * py + plane[2] + total(layers) - (z_lo + z_hi) / 2
        hit = next((lay for lay in layers if lay["offset"] - 1e-6 <= pos <= lay["offset"] + lay["thickness"] + 1e-6), layers[-1])
        out.append({"layer": hit["id"], "material": hit["material"], "name": hit["name"]})
    return out


# --------------------------------------------------------------------------------------------------------------------------- properties
def rows(project: Any, oid: str) -> dict[str, Any] | None:
    """The Layers group of the Properties panel: one block per layer, and the "+" that adds one."""
    cat = E.category(project, oid)
    if cat not in LAYERED:
        return None
    layers = stack(project, oid)
    every = openers(project, oid)
    mats = [{"value": v, "label": v} for v in materials()]
    modes = [{"value": "all", "label": "Cut by all openings"}, {"value": "none", "label": "Not cut"}, {"value": "selected", "label": "Cut by selected"}]
    out: list[dict[str, Any]] = []
    for n, lay in enumerate(layers, 1):
        key = lambda field: f"layer:{lay['id']}:{field}"                                            # noqa: E731
        block: list[dict[str, Any]] = [
            {"key": key("name"), "label": f"{n}. Name", "type": "text", "value": lay["name"], "removable": len(layers) > 1},
            {"key": key("material"), "label": f"{n}. Material", "type": "choice", "value": lay["material"], "choices": mats},
            {"key": key("thickness"), "label": f"{n}. Thickness", "type": "number", "unit": "mm", "value": round(lay["thickness"] * 1000, 6)}]
        if cat != "roof" and every:
            block.append({"key": key("openings"), "label": f"{n}. Openings", "type": "choice", "value": lay["openings"], "choices": modes})
            if lay["openings"] == "selected":
                block += [{"key": key(f"cut:{o['id']}"), "label": f"{n}.   {o['name'] or o['kind'].title()}", "type": "choice", "value": "yes" if o["id"] in lay["opening_ids"] else "no",
                           "choices": [{"value": "yes", "label": "Cut"}, {"value": "no", "label": "Not cut"}]} for o in every]
        if cat == "wall" and len(layers) > 1:
            block += [{"key": key(e), "label": f"{n}. {e.title()} edge", "type": "number", "unit": "mm", "value": round(lay[e] * 1000, 6)} for e in EDGES]
        out += block
    out.append({"label": "Total thickness", "type": "readonly", "value": f"{total(layers) * 1000:g} mm"})
    return {"title": f"Layers ({len(layers)})", "rows": out}
