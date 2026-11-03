"""Model coordination checks that look across elements: duplicates, annotation associations and geometry conflicts.

The single-element checks (missing level or host, openings that do not fit, invalid parameters) live with the elements themselves
(``elements.check``); the document checks (a view that lost its drawing, a sheet that places a deleted view) with the project. What is here
needs the whole model at once.

Every finding says what kind of problem it is (``category``): references, hosting, parameters, consistency, associations, geometry or
documentation. Geometry conflicts are **candidates**: the test is whether two elements' axis-aligned bounding boxes overlap by more than a
few millimetres in all three directions. That finds real clashes (a table standing in a wall) and also reports boxes that merely enclose each
other (a table in a doorway, in the box of the wall that has the door in it), so the result says so and the person decides. Walls that meet at a
corner overlap by design and are not tested against each other; there is no exact solid intersection here, and none is claimed."""
from __future__ import annotations

import math
from typing import Any

from . import annotations as AN
from . import elements as E

PENETRATION = 0.005                      # metres a bounding box must overlap in every direction to count
MAX_PAIRS = 50_000
MAX_REPORTED = 100
CLASHING = {"generic": ("wall", "structural", "stair", "floor", "roof", "generic"), "stair": ("wall", "structural", "floor", "roof")}
LIMITATIONS = [
    "Geometry conflicts are bounding-box candidates, not exact solid intersections: a box around an element with an opening also covers the opening.",
    "Walls that meet at corners overlap by design, so walls are not tested against each other; clashes are tested for generic elements and stairs against walls, structure, floors and roofs.",
    "These are consistency checks of the model's own data, not a code-compliance or engineering check.",
]


def _signature(project: Any, oid: str) -> tuple | None:
    meta = E.meta_of(project, oid)
    cat = E.m(meta, "category")
    r = lambda v: round(float(v), 4)                                                                       # noqa: E731
    if cat == "wall":
        return (cat, E.m(meta, "level"), tuple(map(r, E.m(meta, "start"))), tuple(map(r, E.m(meta, "end"))))
    if cat == "structural" and E.is_column(meta):
        return ("column", E.m(meta, "level"), tuple(map(r, E.m(meta, "point"))))
    if cat == "structural":
        return ("beam", E.m(meta, "level"), tuple(map(r, E.m(meta, "start"))), tuple(map(r, E.m(meta, "end"))))
    if cat == "generic":
        return (cat, project.model.get(oid).get("family_id"), E.m(meta, "level"), tuple(map(r, E.m(meta, "point"))), r(E.m(meta, "base_offset", 0.0)))
    if cat in E.OPENINGS:
        return (cat, E.m(meta, "host"), r(E.m(meta, "offset")), r(E.m(meta, "sill", 0.0)))
    if cat == "grid":
        return (cat, tuple(map(r, E.m(meta, "start"))), tuple(map(r, E.m(meta, "end"))))
    return None


def duplicates(project: Any) -> list[dict[str, str]]:
    seen: dict[tuple, str] = {}
    out = []
    for oid in E.element_ids(project):
        sig = _signature(project, oid)
        if sig is None:
            continue
        if sig in seen:
            name = project.model.get(oid)["name"] or sig[0]
            out.append(E.issue("warning", oid, f"{name} sits exactly on top of {project.model.get(seen[sig])['name'] or sig[0]}: one of them is a duplicate", "consistency"))
        else:
            seen[sig] = oid
    return out


def overlapping_openings(project: Any) -> list[dict[str, str]]:
    out = []
    for wid in E.element_ids(project):
        if E.category(project, wid) != "wall":
            continue
        spans = [(E.opening_span(project.model.get(h)["metadata"]), h) for h in E.hosted(project, wid)]
        for i, (a, ha) in enumerate(spans):
            for b, hb in spans[i + 1:]:
                if a[0] < b[1] - 1e-9 and b[0] < a[1] - 1e-9 and a[2] < b[3] - 1e-9 and b[2] < a[3] - 1e-9:
                    out.append(E.issue("error", ha, f"{project.model.get(ha)['name']} and {project.model.get(hb)['name']} overlap in the same wall", "hosting"))
    return out


def associations(project: Any) -> list[dict[str, str]]:
    out = []
    for tag in AN.all_tags(project):
        if tag["detached"]:
            out.append(E.issue("error", tag["entity"], f"A tag in {tag['view_name']} ('{tag['text'].splitlines()[0] if tag['text'] else ''}') points at an element that no longer exists", "associations"))
    return out


def clashes(project: Any) -> list[dict[str, str]]:
    """Bounding-box candidates between generic elements / stairs and what they should not stand inside."""
    kinds = {oid: E.category(project, oid) for oid in E.element_ids(project)}
    wanted = {oid: c for oid, c in kinds.items() if c in CLASHING or any(c in v for v in CLASHING.values())}
    if len(wanted) < 2 or not any(c in CLASHING for c in wanted.values()):
        return []
    boxes = project.model._model.bounds_by_object()
    items = sorted(((boxes[o][0][0], o) for o in wanted if o in boxes), key=lambda t: t[0])
    out: list[dict[str, str]] = []
    checked = 0
    for i, (_, a) in enumerate(items):
        lo_a, hi_a = boxes[a]
        for _, b in items[i + 1:]:
            lo_b, hi_b = boxes[b]
            if lo_b[0] > hi_a[0] - PENETRATION:
                break                                                       # sorted by minimum x: nothing further along can overlap
            checked += 1
            if checked > MAX_PAIRS:
                out.append(E.issue("info", a, f"Clash checking stopped after {MAX_PAIRS} candidate pairs; the model is large", "geometry"))
                return out
            ca, cb = kinds[a], kinds[b]
            if not (cb in CLASHING.get(ca, ()) or ca in CLASHING.get(cb, ())):
                continue
            depth = min(min(hi_a[k], hi_b[k]) - max(lo_a[k], lo_b[k]) for k in range(3))
            if depth > PENETRATION:
                na, nb = project.model.get(a)["name"] or ca, project.model.get(b)["name"] or cb
                out.append(E.issue("warning", a, f"{na} and {nb} may clash: their bounding boxes overlap by at least {depth * 1000:.0f} mm in every direction", "geometry"))
                if len(out) >= MAX_REPORTED:
                    out.append(E.issue("info", a, f"Only the first {MAX_REPORTED} candidate clashes are listed", "geometry"))
                    return out
    return out


def coordination(project: Any) -> list[dict[str, str]]:
    return duplicates(project) + overlapping_openings(project) + associations(project) + clashes(project)
