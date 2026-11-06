"""Array: repeat elements along one to three directions or round a point, and keep the pattern.

An array is an element of its own (a group with no geometry, category ``array``) that remembers what it repeats and how: the source elements, the axes
(a count and a step each) or the centre, angle and count of a radial array, and whether it is linked. The copies are ordinary elements made with SGEO's
own duplicate, then moved or turned through the same code as Move and Rotate, so every joint, hosted opening and void is worked out for a copy exactly as
if it had been drawn there. Item 1 (all indices zero) is the source itself.

* **Rectangular** (``mode`` "grid", also what the old "linear" means): up to three axes, each with a ``count`` and a ``step``. One axis along X is the X
  array, along Y the Y array, along Z the Z array; two or three axes make XY, XZ, YZ and XYZ arrays. Item (i, j, k) is the source moved by
  ``i * step_1 + j * step_2 + k * step_3``. A step is given as a ``spacing`` and a ``direction`` (``"x"``, ``"y"``, ``"z"``, a vector, or an angle in
  degrees in plan), or directly as a vector.
* **Radial**: item *i* is the source turned about ``centre`` by ``i`` times a step angle. ``angle`` is the angle the whole array spans: 360° divides it
  evenly round the circle (the last item does not land on the first); any other angle puts the last item on that angle.

**Linked.** The array regenerates its copies when its count, spacing, direction, centre or angle change (``set``), and, if it is linked, when the source
changes (its parameters, or the doors and windows a wall hosts), checked after every model command. A change of spacing, direction, centre or angle
moves the copies that stay in the pattern (they keep their identity); only a changed source makes them again. Changing a copy by hand makes it *independent*: it stays where it is put, the array no longer touches it, and it keeps its
place in the pattern so that regenerating does not make a second one there. Deleting a copy leaves its place empty in the same way. Lowering the count
removes the copies that are no longer in the pattern but never an independent one, which is released instead. Deleting the source dissolves the array
and keeps the copies as ordinary elements; deleting the array itself deletes the copies that still follow it.

A wall takes the doors and windows it hosts with it. Doors and windows alone, and rooms (which are made from walls), are refused: copy the wall. An
element that is already an item of an array is refused as a source: edit that array. Nothing here is partly done: a request that cannot be carried out
changes nothing (the project command rolls it back).
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
from typing import Any, Mapping, Sequence

from ..tools.errors import BadRequest, NotFound
from ..tools.validation import integer, number, text
from . import elements as E

MAX_COUNT = 500
MAX_AXES = 3
MAX_NEW = 300                           # copies one array holds: SGEO checks the whole scene on every edit, so the time grows with the square of the size (about 8 s for 196 copies)
MODES = ("grid", "radial")
STATES = ("driven", "independent", "removed")
REFUSED = {"door": "A door or window is copied with its wall: select the wall",
           "window": "A door or window is copied with its wall: select the wall",
           "room": "A room is made by the walls round it: array the walls, or draw the room again",
           "array": "An array cannot be arrayed: array its source instead",
           "grid": "A grid line is a datum, not something to repeat: draw the lines you need"}
VOLATILE = {"joins", "ends", "cut", "cut_outline", "cutflag", "array", "array_at"}          # worked out from the neighbours, or about the array itself: not what the element is
DIRECTIONS = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}
NONE = ""                                                                    # the value of ``array`` on an element that belongs to no array


# ------------------------------------------------------------------------------------------------------------------------ requests
def _vector(raw: Any, label: str, f: float) -> tuple[float, float, float]:
    if not isinstance(raw, (list, tuple)) or len(raw) not in (2, 3):
        raise BadRequest(f"{label} must be [dx, dy] or [dx, dy, dz]")
    v = [float(number(c, label, -1e9, 1e9)) * f for c in raw] + [0.0] * (3 - len(raw))
    return v[0], v[1], v[2]


def _axis(raw: Any, f: float) -> dict[str, Any]:
    """One axis of a rectangular array from a request (lengths in drawing units, ``f`` converts them to metres)."""
    if not isinstance(raw, Mapping):
        raise BadRequest("Each axis is an object with a count and a step (or a spacing and a direction)")
    count = integer(raw.get("count"), "count", 1, MAX_COUNT)
    if raw.get("step") is not None:
        step = _vector(raw["step"], "step", f)
    else:
        spacing = float(number(raw.get("spacing"), "spacing", 0.0, 1e9)) * f
        d = raw.get("direction", "x")
        if isinstance(d, str):
            if d.lower() not in DIRECTIONS:
                raise BadRequest("direction must be 'x', 'y', 'z', a vector [dx, dy, dz], or an angle in degrees in plan")
            unit: tuple[float, ...] = DIRECTIONS[d.lower()]
        elif isinstance(d, (list, tuple)):
            v = _vector(d, "direction", 1.0)
            n = math.sqrt(sum(c * c for c in v))
            if n < 1e-9:
                raise BadRequest("direction must not be zero")
            unit = tuple(c / n for c in v)
        else:
            a = math.radians(number(d, "direction", -360, 360))
            unit = (math.cos(a), math.sin(a), 0.0)
        step = (unit[0] * spacing, unit[1] * spacing, unit[2] * spacing)
    return {"count": count, "step": [round(c, 12) for c in step]}


def parse(p: Mapping[str, Any], f: float, current: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The normalised definition of an array (metres, radians left as degrees) from a request in drawing units. With ``current`` (the definition in force) a
    request may change just some of it. Returns ``{"mode", "axes" | "count", "centre", "angle", "linked"}``."""
    mode = text(p.get("mode", (current or {}).get("mode", "grid")), "mode", 20)
    mode = "grid" if mode == "linear" else mode
    if mode not in MODES:
        raise BadRequest("mode must be 'grid' (also 'linear') or 'radial'")
    out: dict[str, Any] = {"mode": mode, "linked": bool(p["linked"]) if p.get("linked") is not None else bool((current or {}).get("linked", True))}
    same = current is not None and current["mode"] == mode
    if mode == "grid":
        if p.get("axes") is not None:
            raw = p["axes"]
            if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_AXES:
                raise BadRequest(f"axes must be a list of 1 to {MAX_AXES} axes")
            out["axes"] = [_axis(a, f) for a in raw]
        elif p.get("dimension") is not None:                                        # "X", "Y", "Z", "XY", "XYZ" …: counts and spacings, one each
            dim = text(p["dimension"], "dimension", 3).lower()
            if not 1 <= len(dim) <= MAX_AXES or any(c not in DIRECTIONS for c in dim) or len(set(dim)) != len(dim):
                raise BadRequest("dimension is X, Y, Z or a combination of them, each once (XY, XYZ …)")
            counts, spacings = p.get("counts"), p.get("spacings")
            if not isinstance(counts, list) or len(counts) != len(dim) or not isinstance(spacings, list) or len(spacings) != len(dim):
                raise BadRequest(f"counts and spacings need {len(dim)} value(s), one per axis of '{dim.upper()}'")
            out["axes"] = [_axis({"count": c, "spacing": s, "direction": d}, f) for c, s, d in zip(counts, spacings, dim)]
        elif p.get("step") is not None:                                             # the old one-axis form
            out["axes"] = [_axis({"count": p.get("count"), "step": p["step"]}, f)]
        elif same and current is not None:
            out["axes"] = [dict(a) for a in current["axes"]]
            if p.get("count") is not None and len(out["axes"]) == 1:
                out["axes"][0]["count"] = integer(p["count"], "count", 1, MAX_COUNT)
        else:
            raise BadRequest("A rectangular array needs axes (a count and a spacing or step each)")
        if all(a["count"] == 1 for a in out["axes"]):
            raise BadRequest("An array needs at least two items: give one axis a count of 2 or more")
        for a in out["axes"]:
            if a["count"] > 1 and max(abs(c) for c in a["step"]) < 1e-9:
                raise BadRequest("An axis with more than one item needs a spacing: the copies would all lie on the originals")
    else:
        old = current if same and current is not None else {}
        out["count"] = integer(p["count"] if p.get("count") is not None else old.get("count"), "count", 2, MAX_COUNT)
        c = p.get("centre")
        out["centre"] = [v * f for v in _point(c, "centre")] if c is not None else list(old.get("centre") or _no("centre"))
        a = p["angle"] if p.get("angle") is not None else old.get("angle", 360.0)
        out["angle"] = float(number(a, "angle", -360, 360))
        if abs(out["angle"]) < 1e-9:
            raise BadRequest("The angle must be between -360° and 360°, and not zero")
    return out


def _point(raw: Any, label: str) -> list[float]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        raise BadRequest(f"{label} must be a point [x, y]")
    return [float(number(c, label, -1e9, 1e9)) for c in raw]


def _no(label: str) -> Any:
    raise BadRequest(f"A radial array needs a {label}")


# ------------------------------------------------------------------------------------------------------------------------ slots
def slots_of(spec: Mapping[str, Any]) -> list[tuple[tuple[int, ...], tuple[float, float, float] | float]]:
    """Every place in the pattern except the source's own, as (index, offset). The offset is a vector (m) for a rectangular array and an angle (degrees)
    for a radial one."""
    if spec["mode"] == "grid":
        axes = spec["axes"]
        out = []
        for at in itertools.product(*(range(a["count"]) for a in axes)):
            if not any(at):
                continue
            out.append((at, tuple(sum(i * a["step"][c] for i, a in zip(at, axes)) for c in range(3))))
        return out
    n, angle = spec["count"], spec["angle"]
    per = angle / n if abs(abs(angle) - 360.0) < 1e-9 else angle / (n - 1)
    return [((i,), per * i) for i in range(1, n)]


def total(spec: Mapping[str, Any]) -> int:
    """Items in the pattern, the source included."""
    return math.prod(a["count"] for a in spec["axes"]) if spec["mode"] == "grid" else int(spec["count"])


def _label(at: Sequence[int]) -> str:
    return " " + ".".join(str(i + 1) for i in at)


# ------------------------------------------------------------------------------------------------------------------------ elements
def signature(project: Any, oid: str) -> str:
    """What an element is, as a digest, so that a change can be told from no change: its parameters (not the joints and cuts worked out from its
    neighbours) and, for a wall, the doors and windows it hosts. Two elements with the same signature are the same element, wherever they stand."""
    def body(i: str) -> dict[str, Any]:
        meta = E.meta_of(project, i)
        return {k: v for k, v in meta.items() if not (k.startswith(f"{E.NS}:") and k[len(E.NS) + 1:] in VOLATILE)}
    parts: list[Any] = [body(oid)]
    if E.category(project, oid) == "wall":
        openings = []
        for hid in E.hosted(project, oid):
            b = body(hid)
            b.pop(f"{E.NS}:host", None)
            openings.append(json.dumps(b, sort_keys=True, default=str))
        parts.append(sorted(openings))
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def _copy(project: Any, oid: str, suffix: str) -> tuple[str, list[str]]:
    """One duplicate of an element (and, for a wall, of the openings it hosts, re-hosted on the copy). Returns (copy id, ids of every new object)."""
    copy = project.model.duplicate(oid, with_children=False, name_suffix=suffix)[oid]
    made = [copy]
    if E.category(project, oid) == "wall":
        for hid in E.hosted(project, oid):
            new = project.model.duplicate(hid, with_children=False, name_suffix="")[hid]
            E._attach(project, new, {"host": copy})
            made.append(new)
    stale = {"joins": [], "ends": [[0.0, 0.0], [0.0, 0.0]], "cut": [], "cut_outline": [], "cutflag": False}
    meta = E.meta_of(project, copy)
    reset = {k: v for k, v in stale.items() if f"{E.NS}:{k}" in meta}
    if reset:
        E._attach(project, copy, reset)
    if E.category(project, copy) == "foundation":
        from . import foundations as FD
        FD.detach(project, copy)                                                      # a copy stands where it is put, it does not snap back to the original's support
    return copy, made


def _place(project: Any, copy: str, spec: Mapping[str, Any], offset: Any) -> None:
    if spec["mode"] == "grid":
        E.move(project, copy, *offset)
    else:
        E.rotate(project, copy, math.radians(offset), tuple(spec["centre"]))


def _make_slot(project: Any, rid: str, sources: Sequence[str], spec: Mapping[str, Any], at: Sequence[int], offset: Any) -> dict[str, Any]:
    ids = []
    for oid in sources:
        copy, _ = _copy(project, oid, _label(at))
        _place(project, copy, spec, offset)
        E._attach(project, copy, {"array": rid, "array_at": list(at)})
        ids.append(copy)
    return {"at": list(at), "ids": ids, "state": "driven", "sig": [signature(project, i) for i in ids]}


def _drop(project: Any, ids: Sequence[str]) -> None:
    """Delete elements the array made. The animation of a deleted element goes with it, as it does for any delete."""
    ids = [i for i in ids if E.alive(project, i)]
    if not ids:
        return
    project.forget_animation({*ids, *(h for i in ids if E.category(project, i) == "wall" for h in E.hosted(project, i))})
    for i in ids:
        if E.alive(project, i):
            E.remove(project, i)


def _release(project: Any, ids: Sequence[str]) -> None:
    for i in ids:
        if E.alive(project, i):
            E._attach(project, i, {"array": NONE, "array_at": []})


# ------------------------------------------------------------------------------------------------------------------------ the record
def array_ids(project: Any) -> list[str]:
    return list(project.model.query_ids(metadata={f"{E.NS}:category": "array"}, limit=10_000))


def of(project: Any, oid: str) -> str | None:
    """The array an element is an item of (or None)."""
    rid = E.m(E.meta_of(project, oid), "array")
    return rid if rid and E.alive(project, rid) else None


def require(project: Any, rid: str) -> dict[str, Any]:
    if not E.alive(project, rid) or E.category(project, rid) != "array":
        raise NotFound(f"{rid} is not an array")
    return E.meta_of(project, rid)


def spec_of(meta: Mapping[str, Any]) -> dict[str, Any]:
    spec: dict[str, Any] = {"mode": E.m(meta, "mode"), "linked": bool(E.m(meta, "linked", True))}
    if spec["mode"] == "grid":
        spec["axes"] = [dict(a) for a in E.m(meta, "axes")]
    else:
        spec.update(count=int(E.m(meta, "count")), centre=list(E.m(meta, "centre")), angle=float(E.m(meta, "angle")))
    return spec


def _store(project: Any, rid: str, spec: Mapping[str, Any], sources: Sequence[str], slots: list[dict[str, Any]]) -> None:
    values: dict[str, Any] = {"mode": spec["mode"], "linked": bool(spec["linked"]), "source": list(sources), "slots": slots,
                              "source_sig": [signature(project, s) for s in sources]}
    if spec["mode"] == "grid":
        values["axes"] = [dict(a) for a in spec["axes"]]
    else:
        values.update(count=int(spec["count"]), centre=list(spec["centre"]), angle=float(spec["angle"]))
    E._attach(project, rid, values)


def make(project: Any, ids: Sequence[str], spec: Mapping[str, Any]) -> dict[str, Any]:
    """Make an array of ``ids`` as defined by ``spec`` (see ``parse``). Returns the array's id, the ids of the copies (one per source and place) and the
    number of objects made."""
    for oid in ids:
        if not E.alive(project, oid):
            raise NotFound(f"Element {oid} does not exist")
    ids = [oid for oid in dict.fromkeys(ids) if not (E.category(project, oid) in E.OPENINGS and E.m(E.meta_of(project, oid), "host") in ids)]      # they go with their wall
    for oid in ids:
        cat = E.category(project, oid)
        if cat in REFUSED:
            raise BadRequest(REFUSED[cat])
        if spec["mode"] == "radial" and cat in E.OPENINGS:
            raise BadRequest(REFUSED["door"])
        if of(project, oid):
            raise BadRequest(f"{project.model.get(oid)['name']} is already an item of an array: change that array, or make it independent first")
    slots_ = slots_of(spec)
    if len(ids) * len(slots_) > MAX_NEW:
        raise BadRequest(f"That would make {len(ids) * len(slots_)} copies; one array makes at most {MAX_NEW}")
    name = _array_name(project, ids, spec)
    rid = project.model.add_group(name=name)
    E._attach(project, rid, {"category": "array", "type": "Array"})
    made = [_make_slot(project, rid, ids, spec, at, off) for at, off in slots_]
    _store(project, rid, spec, ids, made)
    return {"array": rid, "ids": [i for s in made for i in s["ids"]], "created": len(made) * len(ids), "count": total(spec)}


def _array_name(project: Any, ids: Sequence[str], spec: Mapping[str, Any]) -> str:
    kind = "Radial" if spec["mode"] == "radial" else "Array " + "×".join(str(a["count"]) for a in spec["axes"])
    first = project.model.get(ids[0])["name"] if ids else ""
    return f"{kind} of {first}" + (f" and {len(ids) - 1} more" if len(ids) > 1 else "")


def regenerate(project: Any, rid: str, spec: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Bring the copies in line with the definition (a new ``spec``, or the stored one after the source changed). A copy whose place is unchanged is left
    alone, one whose place moved is moved there, and when the source changed every copy that follows it is made again. Independent copies are never touched
    or deleted."""
    meta = require(project, rid)
    sources = list(E.m(meta, "source"))
    spec = dict(spec) if spec is not None else spec_of(meta)
    for s in sources:
        if not E.alive(project, s):
            raise NotFound("The source of this array no longer exists")
    old_spec = spec_of(meta)
    wanted = {tuple(at): off for at, off in slots_of(spec)}
    old = {tuple(s["at"]): s for s in E.m(meta, "slots")}
    before = {tuple(at): off for at, off in slots_of(old_spec)} if old_spec["mode"] == spec["mode"] else {}
    stale = spec["linked"] and (not old_spec["linked"] or list(E.m(meta, "source_sig") or []) != [signature(project, s) for s in sources])      # turning "linked" on re-syncs
    if len(sources) * len(wanted) > MAX_NEW:
        raise BadRequest(f"That would make {len(sources) * len(wanted)} copies; one array makes at most {MAX_NEW}")
    new_slots: list[dict[str, Any]] = []
    kept = removed = 0
    for at in sorted(set(wanted) | set(old)):
        slot = old.get(at)
        if at not in wanted:                                                       # no longer in the pattern
            if slot and slot["state"] == "driven":
                _drop(project, slot["ids"]); removed += 1
            elif slot and slot["state"] == "independent":
                kept += 1                                                           # a copy the person has edited is theirs: released, never deleted
            continue
        off = wanted[at]
        if slot and slot["state"] != "driven":
            new_slots.append(slot)
            continue
        if slot and not stale and at in before and all(E.alive(project, i) for i in slot["ids"]):
            if _reposition(project, slot, old_spec, spec, before[at], off):          # the items stay the same elements and are moved to their new places
                slot = {**slot, "sig": [signature(project, i) for i in slot["ids"]]}
            new_slots.append(slot)
            continue
        if slot:
            _drop(project, slot["ids"])
        new_slots.append(_make_slot(project, rid, sources, spec, at, off))
    _store(project, rid, spec, sources, new_slots)
    project.model.rename_object(rid, _array_name(project, sources, spec))
    return {"array": rid, "kept_independent": kept, "removed": removed, "count": total(spec)}


def _reposition(project: Any, slot: Mapping[str, Any], old_spec: Mapping[str, Any], spec: Mapping[str, Any], was: Any, now: Any) -> bool:
    """Move the items of a place from where the old definition put them to where the new one does. Returns whether anything moved."""
    if spec["mode"] == "grid":
        delta = tuple(b - a for a, b in zip(was, now))
        if all(abs(d) < 1e-12 for d in delta):
            return False
        for i in slot["ids"]:
            E.move(project, i, *delta)
        return True
    same_centre = all(abs(a - b) < 1e-12 for a, b in zip(old_spec["centre"], spec["centre"]))
    if same_centre and abs(was - now) < 1e-12:
        return False
    for i in slot["ids"]:
        if same_centre:
            E.rotate(project, i, math.radians(now - was), tuple(spec["centre"]))
        else:
            E.rotate(project, i, math.radians(-was), tuple(old_spec["centre"]))     # back to the source's place, then out to the new one
            E.rotate(project, i, math.radians(now), tuple(spec["centre"]))
    return True


def reconcile(project: Any) -> None:
    """After a model command: notice what a person did to the items, then keep the linked arrays in line with their sources. A deleted item leaves its
    place empty; an item edited by hand becomes independent; a changed source regenerates every item that still follows it; a deleted source dissolves
    its array and keeps the items as ordinary elements."""
    for rid in array_ids(project):
        meta = E.meta_of(project, rid)
        sources = list(E.m(meta, "source"))
        slots = [dict(s) for s in E.m(meta, "slots")]
        if not all(E.alive(project, s) for s in sources):
            dissolve(project, rid)
            continue
        changed = False
        for slot in slots:
            if slot["state"] != "driven":
                continue
            if not all(E.alive(project, i) for i in slot["ids"]):
                _drop(project, slot["ids"])                                         # a source of the place was deleted: the place is empty
                slot["state"], changed = "removed", True
            elif [signature(project, i) for i in slot["ids"]] != slot["sig"]:
                _release(project, slot["ids"])
                slot["state"], changed = "independent", True
        spec = spec_of(meta)
        if spec["linked"] and list(E.m(meta, "source_sig") or []) != [signature(project, s) for s in sources]:
            E._attach(project, rid, {"slots": slots})
            regenerate(project, rid)
        elif changed:
            E._attach(project, rid, {"slots": slots})


def set_parameters(project: Any, rid: str, spec: Mapping[str, Any]) -> dict[str, Any]:
    require(project, rid)
    return regenerate(project, rid, spec)


def release(project: Any, rid: str) -> dict[str, Any]:
    """Dissolve an array and keep every item as an ordinary element."""
    meta = require(project, rid)
    n = sum(len(s["ids"]) for s in E.m(meta, "slots") if s["state"] != "removed")
    dissolve(project, rid)
    return {"released": n}


def dissolve(project: Any, rid: str) -> None:
    for s in E.m(E.meta_of(project, rid), "slots"):
        _release(project, s["ids"])
    project.model.remove(rid)


def remove(project: Any, rid: str) -> None:
    """Delete an array: the items that still follow it go with it; the source and any independent item stay."""
    meta = require(project, rid)
    for s in E.m(meta, "slots"):
        if s["state"] == "driven":
            _drop(project, s["ids"])
        else:
            _release(project, s["ids"])
    project.model.remove(rid)


def make_independent(project: Any, oid: str) -> str:
    """Let one item go its own way: it stays as it is and the array no longer changes it. Returns the array's id."""
    rid = of(project, oid)
    if rid is None:
        raise BadRequest("That element is not an item of an array")
    meta = E.meta_of(project, rid)
    slots = [dict(s) for s in E.m(meta, "slots")]
    for s in slots:
        if oid in s["ids"] and s["state"] == "driven":
            _release(project, s["ids"])
            s["state"] = "independent"
    E._attach(project, rid, {"slots": slots})
    return rid


# ------------------------------------------------------------------------------------------------------------------------ Properties
def axis_view(a: Mapping[str, Any]) -> dict[str, Any]:
    s = a["step"]
    spacing = math.sqrt(sum(c * c for c in s))
    vertical = abs(s[0]) < 1e-12 and abs(s[1]) < 1e-12
    angle = None if vertical else math.degrees(math.atan2(s[1], s[0]))
    return {"count": a["count"], "spacing": spacing, "angle": angle, "vertical": vertical}


def set_property(project: Any, rid: str, key: str, value: Any) -> None:
    """Edit one parameter from Properties: ``count`` / ``spacing_N`` (mm) / ``angle_N`` for axis N, ``count_N``, and for a radial array ``count``,
    ``angle``, ``centre_x``, ``centre_y``. ``linked`` is yes or no."""
    meta = require(project, rid)
    spec = spec_of(meta)
    if key == "linked":
        spec["linked"] = str(value).lower() in ("true", "yes", "linked", "1")
    elif spec["mode"] == "radial":
        if key == "count":
            spec["count"] = integer(value, "count", 2, MAX_COUNT)
        elif key == "angle":
            spec["angle"] = float(number(value, "angle", -360, 360))
        elif key in ("centre_x", "centre_y"):
            spec["centre"][0 if key.endswith("x") else 1] = float(number(value, key, -1e9, 1e9)) / 1000.0
        else:
            raise BadRequest(f"'{key}' is not an editable array property")
    else:
        part, _, n = key.rpartition("_")
        i = int(n) - 1 if n.isdigit() else -1
        if part not in ("count", "spacing", "angle") or not 0 <= i < len(spec["axes"]):
            raise BadRequest(f"'{key}' is not an editable array property")
        a = spec["axes"][i]
        if part == "count":
            a["count"] = integer(value, "count", 1, MAX_COUNT)
        else:
            s = a["step"]
            plan, length = math.hypot(s[0], s[1]), math.sqrt(sum(c * c for c in s))
            if part == "spacing":
                new_len = float(number(value, "spacing", 0.0, 1e9)) / 1000.0
                a["step"] = [c * new_len / length for c in s] if length > 1e-12 else [new_len, 0.0, 0.0]
            else:
                if plan < 1e-12:
                    raise BadRequest("A vertical axis has no direction in plan")
                ang = math.radians(float(number(value, "angle", -360, 360)))
                a["step"] = [plan * math.cos(ang), plan * math.sin(ang), s[2]]
            a["step"] = [round(c, 12) + 0.0 for c in a["step"]]                         # a quarter turn is exactly along Y, not 6e-17 off it
    regenerate(project, rid, parse({}, 1.0, spec))


def properties(project: Any, rid: str, out: dict[str, Any]) -> dict[str, Any]:
    meta = E.meta_of(project, rid)
    spec = spec_of(meta)
    slots = E.m(meta, "slots")
    states = {s: sum(1 for x in slots if x["state"] == s) for s in STATES}
    sources = [project.model.get(s)["name"] if E.alive(project, s) else "(missing)" for s in E.m(meta, "source")]
    out["top"] = [{"label": "Type", "type": "readonly", "value": "Array"},
                  {"label": "Kind", "type": "readonly", "value": "Radial" if spec["mode"] == "radial" else "Rectangular"},
                  {"key": "linked", "label": "Linked to source", "type": "choice", "value": "yes" if spec["linked"] else "no",
                   "choices": [{"value": "yes", "label": "Yes: follows the source"}, {"value": "no", "label": "No: only its own count and spacing"}]}]
    rows: list[dict[str, Any]] = []
    if spec["mode"] == "grid":
        for n, a in enumerate(spec["axes"], 1):
            v = axis_view(a)
            rows.append({"key": f"count_{n}", "label": f"Count {n}", "type": "number", "value": v["count"]})
            rows.append({"key": f"spacing_{n}", "label": f"Spacing {n}", "type": "number", "unit": "mm", "value": round(v["spacing"] * 1000, 6)})
            rows.append({"key": f"angle_{n}", "label": f"Direction {n}", "type": "number", "unit": "°", "value": round(v["angle"], 6)} if v["angle"] is not None
                         else {"label": f"Direction {n}", "type": "readonly", "value": "Vertical (Z)"})
    else:
        rows += [{"key": "count", "label": "Count", "type": "number", "value": spec["count"]},
                 {"key": "angle", "label": "Angle", "type": "number", "unit": "°", "value": spec["angle"]},
                 {"key": "centre_x", "label": "Centre X", "type": "number", "unit": "mm", "value": round(spec["centre"][0] * 1000, 6)},
                 {"key": "centre_y", "label": "Centre Y", "type": "number", "unit": "mm", "value": round(spec["centre"][1] * 1000, 6)}]
    out["groups"] = [
        {"title": "Pattern", "rows": rows},
        {"title": "Items", "rows": [{"label": "Items", "type": "readonly", "value": str(total(spec))},
                                     {"label": "Follow the array", "type": "readonly", "value": str(states["driven"])},
                                     {"label": "Independent", "type": "readonly", "value": str(states["independent"])},
                                     {"label": "Deleted places", "type": "readonly", "value": str(states["removed"])},
                                     {"label": "Source", "type": "readonly", "value": ", ".join(sources)}]},
        {"title": "Identity", "rows": [{"key": "name", "label": "Name", "type": "text", "value": project.model.get(rid)["name"]}]}]
    return out


def item_rows(project: Any, oid: str) -> list[dict[str, Any]]:
    """The rows an item shows in its own Properties: which array it is in and where."""
    meta = E.meta_of(project, oid)
    rid = E.m(meta, "array")
    if not rid or not E.alive(project, rid):
        return []
    return [{"label": "Array", "type": "link", "value": project.model.get(rid)["name"], "target": {"model": rid}},
            {"label": "Place in array", "type": "readonly", "value": ".".join(str(i + 1) for i in E.m(meta, "array_at", []))}]
