"""Array: repeat elements along a line or around a point.

An array makes real copies (``count`` is the number of items including the originals), each an ordinary element that can be edited, moved or
deleted on its own: the pattern is not kept as a live group, so changing the original later does not change the copies.

* **Linear**: item *i* is the original moved by ``i`` times a step .
* **Radial**: item *i* is the original turned about ``centre`` by ``i`` times a step angle. ``angle`` is the angle the whole array spans: 360° divides
  it evenly round the circle (the last copy does not land on the first); any other angle puts the last copy on that angle.

A wall takes the doors and windows it hosts with it. Doors and windows alone, and rooms (which are made from walls), are refused: copy the wall.
Copies are made with SGEO's own duplicate, then moved or turned through the same code as Move and Rotate, so every joint, hosted opening and
void is worked out for the copy exactly as if it had been drawn there.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from ..tools.errors import BadRequest, NotFound
from ..tools.validation import integer, number, text
from . import elements as E

MAX_COUNT = 500
MAX_NEW = 2000                          # copies made by one array
REFUSED = {"door": "A door or window is copied with its wall: select the wall",
           "window": "A door or window is copied with its wall: select the wall",
           "room": "A room is made by the walls round it: array the walls, or draw the room again"}


def _copy(project: Any, oid: str, suffix: str) -> tuple[str, list[str]]:
    """One duplicate of an element (and, for a wall, of the openings it hosts, re-hosted on the copy). Returns (copy id, ids of every new object)."""
    copy = project.model.duplicate(oid, with_children=False, name_suffix=suffix)[oid]
    made = [copy]
    if E.category(project, oid) == "wall":
        for hid in E.hosted(project, oid):
            new = project.model.duplicate(hid, with_children=False, name_suffix="")[hid]
            E._attach(project, new, {"host": copy})
            made.append(new)
    stale = {"joins": [], "ends": [[0.0, 0.0], [0.0, 0.0]], "cut": [], "cutflag": False}
    meta = E.meta_of(project, copy)
    E._attach(project, copy, {k: v for k, v in stale.items() if f"{E.NS}:{k}" in meta})
    return copy, made


def make(project: Any, ids: Sequence[str], mode: str, count: int, *, step: Sequence[float] = (0.0, 0.0, 0.0), centre: tuple[float, float] = (0.0, 0.0),
         angle: float = 360.0) -> dict[str, Any]:
    """Make ``count - 1`` copies of each element. ``step`` (m) is for a linear array, ``centre`` (m) and ``angle`` (degrees) for a radial one."""
    if mode not in ("linear", "radial"):
        raise BadRequest("mode must be 'linear' or 'radial'")
    if not 2 <= count <= MAX_COUNT:
        raise BadRequest(f"count (the number of items, originals included) must be 2 to {MAX_COUNT}")
    for oid in ids:
        if not E.alive(project, oid):
            raise NotFound(f"Element {oid} does not exist")
    ids = [oid for oid in ids if not (E.category(project, oid) in E.OPENINGS and E.m(E.meta_of(project, oid), "host") in ids)]      # they go with their wall
    for oid in ids:
        cat = E.category(project, oid)
        if cat in REFUSED:
            raise BadRequest(REFUSED[cat])
        if mode == "radial" and cat in E.OPENINGS:
            raise BadRequest(REFUSED[cat])
    if len(ids) * (count - 1) > MAX_NEW:
        raise BadRequest(f"That would make {len(ids) * (count - 1)} copies; one array makes at most {MAX_NEW}")
    if mode == "linear" and not any(abs(v) > 1e-9 for v in step):
        raise BadRequest("A linear array needs a step: the copies would all lie on the originals")
    if mode == "radial":
        if abs(angle) < 1e-9 or abs(angle) > 360 + 1e-9:
            raise BadRequest("The angle must be between -360° and 360°, and not zero")
        per = angle / count if abs(abs(angle) - 360.0) < 1e-9 else angle / (count - 1)
    new_ids: list[str] = []
    copies: list[list[str]] = []
    for i in range(1, count):
        row: list[str] = []
        for oid in ids:
            copy, made = _copy(project, oid, f" {i + 1}")
            new_ids.extend(made)
            row.append(copy)
            if mode == "linear":
                E.move(project, copy, step[0] * i, step[1] * i, step[2] * i)
            else:
                E.rotate(project, copy, math.radians(per * i), centre)
        copies.append(row)
    return {"ids": [c for row in copies for c in row], "created": len(new_ids), "count": count}
