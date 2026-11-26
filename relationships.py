"""Links, chains and groups: what follows what, and what is selected together.

Two relationships, kept apart on purpose:

* **Link / chain** (a *dependency*). One element is the transform-parent of another: when the parent is moved or turned, its children follow. A child may
  itself be a parent, which is a chain. An element has at most one transform-parent, so every transformation has exactly one path and nothing is
  applied twice. Moving a child never moves its parent or its siblings.
* **Group** (an *organisation*). Elements are collected so they are selected, moved, turned and copied together. Groups nest. Membership never creates a
  dependency, and a group never merges geometry: every member keeps its id, parameters, material, classification and modifiers.

Where it is stored (the one source of truth)
    ``project.manifest["relationships"]``: ``{"links": {child_id: {"parent": id, "inherit": ["translate", "rotate"]}}, "groups": [{"id", "name",
    "parent": group id | None, "members": [element ids], "pivot": [x, y, z] | None}]}``. The manifest is part of every undo step, is saved in
    ``project.json`` and is absent (not empty) until the first relationship is made, so a project that never uses this is byte-for-byte what it was.
    No relative matrix is stored: a child's offset from its parent is read from the geometry, so it can never drift from the model.

Transformations
    An element's place is its parameters (a wall's ends, a floor's outline), so a transformation is carried out by the element's own ``move`` / ``rotate``.
    A child follows a *translation* of its parent by the same translation. It follows a *turn* according to its link's ``inherit``: with both
    ``translate`` and ``rotate`` it turns rigidly about the same centre; with ``translate`` only it is carried round the centre without turning; with
    ``rotate`` only it turns about its own anchor and stays where it is. Scaling and dimension edits never propagate (nothing here pretends to).
    Elements the person manipulates pull in their whole outermost group; elements that follow through a link do not pull in their own group-mates.
    Elements whose place is derived from another (a door in its wall, a gutter on its roof) are never moved twice: they are dropped from a
    transformation when the element they follow is in it, and they cannot be linked.
"""
from __future__ import annotations

import copy
import math
from typing import Any, Iterable, Mapping, Sequence

from ..tools.errors import BadRequest, NotFound
from ..tools.validation import new_id, text
from . import elements as E

KEY = "relationships"
INHERITABLE = ("translate", "rotate")
MAX_IDS = 5000
MAX_DEPTH = 10_000                                        # a chain cannot be deeper than this (it would be a cycle the check missed)


# ------------------------------------------------------------------------------------------------------------------------- storage
def _state(project: Any, create: bool = False) -> dict[str, Any]:
    state = project.manifest.get(KEY)
    if isinstance(state, dict):
        state.setdefault("links", {})
        state.setdefault("groups", [])
        return state
    if not create:
        return {"links": {}, "groups": []}
    return project.manifest.setdefault(KEY, {"links": {}, "groups": []})


def _tidy(project: Any) -> None:
    """Keep the manifest free of an empty relationship record, so a project that no longer uses them is as it was before."""
    state = project.manifest.get(KEY)
    if isinstance(state, dict) and not state.get("links") and not state.get("groups"):
        project.manifest.pop(KEY, None)


def in_use(project: Any) -> bool:
    state = project.manifest.get(KEY)
    return bool(isinstance(state, dict) and (state.get("links") or state.get("groups")))


def _inherit(value: Any) -> list[str]:
    if value is None:
        return list(INHERITABLE)
    if not isinstance(value, (list, tuple)) or not value:
        raise BadRequest("inherit must list what a child follows: translate, rotate")
    bad = [str(v) for v in value if v not in INHERITABLE]
    if bad:
        raise BadRequest(f"A child cannot inherit {', '.join(bad)}: only translation and rotation propagate", hint="Scaling and dimension changes never reach a child")
    return [v for v in INHERITABLE if v in value]


def _ids(value: Any, label: str = "ids", *, minimum: int = 1) -> list[str]:
    if not isinstance(value, (list, tuple)) or len(value) > MAX_IDS or not all(isinstance(v, str) and 0 < len(v) <= 80 for v in value):
        raise BadRequest(f"{label} must be a list of element ids")
    out = list(dict.fromkeys(value))
    if len(out) < minimum:
        raise BadRequest(f"{label} needs at least {minimum} element{'s' if minimum != 1 else ''}")
    return out


def _require(project: Any, oid: str, what: str = "Element") -> None:
    if not E.alive(project, oid):
        raise NotFound(f"{what} {oid} does not exist")


def _names(project: Any) -> dict[str, str]:
    raw = getattr(project.model, "_model", None)
    if raw is not None and hasattr(raw, "objects"):
        return {o["id"]: o.get("name") or o["id"] for o in raw.objects}
    return {}


def _label(project: Any, oid: str, names: Mapping[str, str] | None = None) -> str:
    names = names if names is not None else _names(project)
    return names.get(oid) or oid


# ------------------------------------------------------------------------------------------------------------------------- derived
def follows(project: Any, oid: str) -> list[str]:
    """The elements whose motion this one already follows by construction (its host wall, its roof): it is never moved on top of them."""
    meta = E.meta_of(project, oid)
    cat = E.m(meta, "category")
    out: list[str] = []
    if cat in E.OPENINGS and E.m(meta, "host"):
        out.append(str(E.m(meta, "host")))
    elif cat == "roof_window" and E.m(meta, "host"):
        out.append(str(E.m(meta, "host")))
    elif cat in ("gutter", "downpipe"):
        from . import gutters as GU
        out.extend(GU.hosts_of(project, oid))
    elif cat == "drain" and E.m(meta, "downpipe"):
        out.append(str(E.m(meta, "downpipe")))
    if E.m(meta, "roof_host"):
        out.append(str(E.m(meta, "roof_host")))
    return [o for o in dict.fromkeys(out) if E.alive(project, o)]


def derived_reason(project: Any, oid: str) -> str | None:
    """Why this element cannot be a link end (its place is derived), or ``None``."""
    meta = E.meta_of(project, oid)
    cat = str(E.m(meta, "category", "generic"))
    if cat in E.OPENINGS:
        return f"a {cat} moves and turns with its host wall: link or group the wall"
    if cat == "roof_window":
        return "a roof window sits in its roof: link the roof"
    if cat in ("gutter", "downpipe"):
        return f"a {cat} follows its roof: link the roof"
    if cat == "array":
        return "an array has no place of its own: link its source"
    if E.m(meta, "roof_host"):
        return "this accessory follows its roof: link the roof"
    if cat == "drain" and E.m(meta, "downpipe"):
        return "this inlet stands under its downpipe"
    if cat == "generic" and project.model.get(oid).get("family_id") is None and E.m(meta, "procedural"):
        return "this element is generated by a procedural group and follows its wall"
    return None


# ------------------------------------------------------------------------------------------------------------------------- queries
def parent_of(project: Any, oid: str) -> str | None:
    link = _state(project)["links"].get(oid)
    return link["parent"] if link else None


def _children_map(state: Mapping[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for child, link in state["links"].items():
        out.setdefault(link["parent"], []).append(child)
    for kids in out.values():
        kids.sort()                                         # JSON objects have no order: sorting keeps every evaluation identical before and after a save
    return out


def children_of(project: Any, oid: str) -> list[str]:
    return _children_map(_state(project)).get(oid, [])


def ancestors(project: Any, oid: str) -> list[str]:
    """Nearest parent first, root last."""
    links = _state(project)["links"]
    out: list[str] = []
    seen = {oid}
    cur = links.get(oid)
    while cur and len(out) < MAX_DEPTH:
        p = cur["parent"]
        if p in seen:
            break
        out.append(p)
        seen.add(p)
        cur = links.get(p)
    return out


def descendants(project: Any, oid: str) -> list[str]:
    """Every element below ``oid``, depth first in link order (deterministic)."""
    return _descend(_children_map(_state(project)), [oid])


def _descend(children: Mapping[str, Sequence[str]], roots: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen = set(roots)
    stack = [c for r in reversed(list(roots)) for c in reversed(children.get(r, ()))]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        out.append(cur)
        stack.extend(reversed(children.get(cur, ())))
    return out


def root_of(project: Any, oid: str) -> str:
    chain = ancestors(project, oid)
    return chain[-1] if chain else oid


def depth_of(project: Any, oid: str) -> int:
    return len(ancestors(project, oid))


def _group_index(state: Mapping[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    groups = {g["id"]: g for g in state["groups"]}
    member: dict[str, str] = {}
    for g in state["groups"]:
        for oid in g["members"]:
            member[oid] = g["id"]
    return groups, member


def _group_chain(groups: Mapping[str, Mapping[str, Any]], gid: str | None) -> list[str]:
    """``gid`` then its enclosing groups, outermost last."""
    out: list[str] = []
    while gid and gid in groups and gid not in out and len(out) < MAX_DEPTH:
        out.append(gid)
        gid = groups[gid].get("parent")
    return out


def group_of(project: Any, oid: str) -> str | None:
    """The innermost group an element is a direct member of."""
    return _group_index(_state(project))[1].get(oid)


def _leaves(groups: Mapping[str, Mapping[str, Any]], gid: str) -> list[str]:
    """Every element in a group and its sub-groups, in a stable order."""
    sub: dict[str, list[str]] = {}
    for g in groups.values():
        if g.get("parent"):
            sub.setdefault(g["parent"], []).append(g["id"])
    out: list[str] = []
    stack = [gid]
    seen: set[str] = set()
    while stack:
        cur = stack.pop()
        if cur in seen or cur not in groups:
            continue
        seen.add(cur)
        out.extend(o for o in groups[cur]["members"] if o not in out)
        stack.extend(reversed(sub.get(cur, [])))
    return out


def group_members(project: Any, gid: str, *, deep: bool = True) -> list[str]:
    groups, _ = _group_index(_state(project))
    if gid not in groups:
        raise NotFound(f"Group {gid} does not exist")
    return _leaves(groups, gid) if deep else list(groups[gid]["members"])


def outermost_group(project: Any, oid: str) -> str | None:
    groups, member = _group_index(_state(project))
    chain = _group_chain(groups, member.get(oid))
    return chain[-1] if chain else None


# ------------------------------------------------------------------------------------------------------------------------- link / chain
def _check_end(project: Any, oid: str, role: str) -> None:
    _require(project, oid, role.capitalize())
    why = derived_reason(project, oid)
    if why:
        raise BadRequest(f"{oid} cannot be the {role} of a link: {why}")


def check_link(project: Any, child_ids: Sequence[str], parent_id: str) -> list[str]:
    """Everything ``link`` refuses, without changing anything: a missing or derived end, linking to itself, a circular dependency. Returns the children."""
    children = _ids(child_ids, "children")
    _check_end(project, parent_id, "parent")
    for c in children:
        if c == parent_id:
            raise BadRequest("An element cannot be linked to itself")
        _check_end(project, c, "child")
    kids = _children_map(_state(project))
    below = set(_descend(kids, children))
    for c in children:
        if parent_id in below or parent_id in kids.get(c, ()) or c in ancestors(project, parent_id):
            names = _names(project)
            raise BadRequest(f"Linking {_label(project, c, names)} under {_label(project, parent_id, names)} would make a circular dependency: "
                             f"{_label(project, parent_id, names)} already follows {_label(project, c, names)}")
    return children


def link(project: Any, child_ids: Sequence[str], parent_id: str, inherit: Any = None) -> dict[str, Any]:
    """Make ``parent_id`` the transform-parent of every child. A child that already has a parent is re-parented. Nothing moves. All or nothing."""
    inh = _inherit(inherit)
    children = check_link(project, child_ids, parent_id)
    state = _state(project, True)
    changed, kept, replaced = [], [], {}
    for c in children:
        old = state["links"].get(c)
        if old and old["parent"] == parent_id and old["inherit"] == inh:
            kept.append(c)
            continue
        if old and old["parent"] != parent_id:
            replaced[c] = old["parent"]
        state["links"][c] = {"parent": parent_id, "inherit": inh}
        changed.append(c)
    return {"parent": parent_id, "linked": changed, "unchanged": kept, "reparented_from": replaced, "inherit": inh}


def chain(project: Any, ordered_ids: Sequence[str], inherit: Any = None) -> dict[str, Any]:
    """Link each element to the one before it: ``[A, B, C]`` makes A the parent of B and B the parent of C. All or nothing."""
    if isinstance(ordered_ids, (list, tuple)) and len(set(ordered_ids)) != len(ordered_ids):
        raise BadRequest("An element can appear only once in a chain: it would follow itself")
    ids = _ids(ordered_ids, "ids", minimum=2)
    inh = _inherit(inherit)
    saved = copy.deepcopy(project.manifest.get(KEY))
    try:
        results = [link(project, [child], parent, inh) for parent, child in zip(ids, ids[1:])]
    except Exception:
        _restore(project, saved)
        raise
    return {"chain": ids, "linked": [c for r in results for c in r["linked"]], "inherit": inh}


def _restore(project: Any, saved: Any) -> None:
    if saved is None:
        project.manifest.pop(KEY, None)
    else:
        project.manifest[KEY] = saved


def set_inherit(project: Any, ids: Sequence[str], inherit: Any) -> dict[str, Any]:
    ids = _ids(ids)
    inh = _inherit(inherit)
    state = _state(project)
    missing = [i for i in ids if i not in state["links"]]
    if missing:
        raise BadRequest(f"{len(missing)} of the selected elements {'is' if len(missing) == 1 else 'are'} not linked to a parent")
    for i in ids:
        state["links"][i]["inherit"] = inh
    return {"ids": ids, "inherit": inh}


def _release(state: dict[str, Any], oid: str, descendants_mode: str, notes: list[str], names: Mapping[str, str]) -> dict[str, Any] | None:
    """Remove ``oid``'s own link and settle its children as asked. Returns the removed link (None if it had none)."""
    old = state["links"].pop(oid, None)
    kids = [c for c, l in state["links"].items() if l["parent"] == oid]
    if descendants_mode == "reparent":
        for c in kids:
            if old:
                state["links"][c]["parent"] = old["parent"]
            else:
                del state["links"][c]
        if kids:
            notes.append(f"{len(kids)} child{'ren' if len(kids) != 1 else ''} of {names.get(oid, oid)} now follow "
                         + (names.get(old["parent"], old["parent"]) if old else "nothing"))
    elif descendants_mode == "release":
        for c in kids:
            del state["links"][c]
        if kids:
            notes.append(f"{len(kids)} child{'ren' if len(kids) != 1 else ''} of {names.get(oid, oid)} no longer follow it")
    elif kids:
        notes.append(f"{len(kids)} child{'ren' if len(kids) != 1 else ''} of {names.get(oid, oid)} keep following it")
    return old


def unlink(project: Any, ids: Sequence[str], descendants_mode: str = "branch", parent: str | None = None) -> dict[str, Any]:
    """Remove the link between each element and its parent. World position, orientation, geometry and properties are untouched.

    ``descendants_mode``: ``branch`` (the element keeps its own children: the whole branch leaves), ``reparent`` (its children are given its former parent,
    so the chain closes up) or ``release`` (its children are freed too). With ``parent``, only links to that parent are removed."""
    if descendants_mode not in ("branch", "reparent", "release"):
        raise BadRequest("descendants must be branch, reparent or release")
    targets = _ids(ids)
    state = _state(project, True)
    names = _names(project)
    notes: list[str] = []
    removed, skipped = [], []
    for oid in targets:
        link_ = state["links"].get(oid)
        if not link_ or (parent is not None and link_["parent"] != parent):
            skipped.append(oid)
            continue
        _release(state, oid, descendants_mode, notes, names)
        removed.append(oid)
    if not removed:
        _tidy(project)
        raise BadRequest("None of the selected elements is linked" + (f" to {names.get(parent, parent)}" if parent else " to a parent"))
    _tidy(project)
    return {"unlinked": removed, "not_linked": skipped, "notes": notes}


def insert(project: Any, oid: str, after: str, adopt: Sequence[str] = (), inherit: Any = None) -> dict[str, Any]:
    """Put ``oid`` into a chain directly under ``after``; the listed children of ``after`` are moved under ``oid``. Nothing moves."""
    adopt = list(dict.fromkeys(adopt or ()))
    state = _state(project, True)
    kids = _children_map(state).get(after, [])
    stray = [a for a in adopt if a not in kids]
    if stray:
        raise BadRequest(f"{len(stray)} of the elements to adopt {'is' if len(stray) == 1 else 'are'} not a child of {_label(project, after)}")
    if oid in adopt:
        raise BadRequest("An element cannot adopt itself")
    saved = copy.deepcopy(project.manifest.get(KEY))
    try:
        first = link(project, [oid], after, inherit)
        for a in adopt:
            inh = state["links"][a]["inherit"]
            link(project, [a], oid, inh)
    except Exception:
        _restore(project, saved)
        raise
    return {"inserted": oid, "after": after, "adopted": adopt, "inherit": first["inherit"]}


# ------------------------------------------------------------------------------------------------------------------------- copies
def clone_structure(project: Any, mapping: Mapping[str, str]) -> dict[str, Any]:
    """Give a set of copies the links and groups the originals have *among themselves* (``mapping`` is original id -> copy id).

    A link is copied when both of its ends were copied: the copy of a child follows the copy of its parent. A group is copied when every element in it
    (and in its sub-groups) was copied, and sub-groups stay nested. A copy is never linked to, or grouped with, anything that was not copied with it: it
    does not follow the original's parent and joins none of the original's groups. Nothing about the originals changes."""
    state = _state(project)
    if not state["links"] and not state["groups"]:
        return {"links": 0, "groups": []}
    st = _state(project, True)
    linked = 0
    for child, spec in list(st["links"].items()):
        parent = spec["parent"]
        if child in mapping and parent in mapping and mapping[child] not in st["links"]:
            st["links"][mapping[child]] = {"parent": mapping[parent], "inherit": list(spec["inherit"])}
            linked += 1
    groups, _ = _group_index(st)
    fresh: dict[str, str] = {}
    made: list[dict[str, str]] = []
    for gid in sorted(groups, key=lambda g: len(_group_chain(groups, g))):          # an enclosing group before the groups inside it
        leaves = _leaves(groups, gid)
        if not leaves or any(o not in mapping for o in leaves):
            continue
        parent = groups[gid].get("parent")
        entry = {"id": new_id(), "name": _copy_name(st, groups[gid]["name"]), "parent": fresh.get(parent) if parent else None,
                 "members": [mapping[o] for o in groups[gid]["members"] if o in mapping], "pivot": None}
        st["groups"].append(entry)
        fresh[gid] = entry["id"]
        made.append({"id": entry["id"], "name": entry["name"], "copy_of": gid})
    return {"links": linked, "groups": made}


def _copy_name(state: Mapping[str, Any], name: str) -> str:
    used = {g["name"] for g in state["groups"]}
    base = f"{name} copy"
    if base not in used:
        return base
    n = 2
    while f"{base} {n}" in used:
        n += 1
    return f"{base} {n}"


# ------------------------------------------------------------------------------------------------------------------------- groups
def _unit(groups: Mapping[str, Mapping[str, Any]], member: Mapping[str, str], oid: str, within: str | None) -> tuple[str, str]:
    """What selecting ``oid`` means: the whole outermost group it is in, or (inside ``within``) the child group or the element itself."""
    chain = _group_chain(groups, member.get(oid))
    if within:
        if within not in chain:
            raise BadRequest(f"{oid} is not inside the group being edited")
        i = chain.index(within)
        return ("group", chain[i - 1]) if i > 0 else ("element", oid)
    return ("group", chain[-1]) if chain else ("element", oid)


def group(project: Any, ids: Sequence[str], name: str | None = None, within: str | None = None) -> dict[str, Any]:
    """Collect two or more elements (or groups, by selecting one of their members) into a new group. Nothing about the elements changes.

    With ``within`` (group isolation) the selection is read inside that group, and the new group nests in it."""
    members = _ids(ids)
    for oid in members:
        _require(project, oid)
    state = _state(project, True)
    groups, member = _group_index(state)
    if within and within not in groups:
        raise NotFound(f"Group {within} does not exist")
    units = list(dict.fromkeys(_unit(groups, member, oid, within) for oid in members))
    if len(units) < 2:
        raise BadRequest("A group needs at least two elements" if not units or units[0][0] == "element" else
                         "Those elements already make up one group: select more elements, or enter the group to group within it")
    outer = {groups[u]["parent"] if k == "group" else member.get(u) for k, u in units}
    if len(outer) > 1:
        raise BadRequest("The selection spans elements at different group levels", hint="Select elements from one level, or ungroup first")
    enclosing = outer.pop() if outer else None
    if within and enclosing != within:
        enclosing = within
    gid = new_id()
    entry = {"id": gid, "name": _group_name(state, name), "parent": enclosing, "members": [u for k, u in units if k == "element"], "pivot": None}
    for k, u in units:
        if k == "group":
            groups[u]["parent"] = gid
        elif enclosing:
            groups[enclosing]["members"].remove(u)
    state["groups"].append(entry)
    return {"group": gid, "name": entry["name"], "parent": enclosing, "members": group_members(project, gid)}


def _group_name(state: Mapping[str, Any], name: str | None) -> str:
    if name is not None:
        return text(name, "name", 80)
    used = {g["name"] for g in state["groups"]}
    n = 1
    while f"Group {n}" in used:
        n += 1
    return f"Group {n}"


def ungroup(project: Any, group_ids: Sequence[str]) -> dict[str, Any]:
    """Dissolve groups (one level each). Their members and sub-groups move up into the enclosing group, or become ungrouped. Geometry is untouched."""
    gids = _ids(group_ids, "groups")
    state = _state(project, True)
    groups, _ = _group_index(state)
    missing = [g for g in gids if g not in groups]
    if missing:
        raise NotFound(f"Group {missing[0]} does not exist")
    freed: list[str] = []
    for gid in gids:
        g = groups[gid]
        up = g.get("parent") if g.get("parent") in groups and g.get("parent") not in gids else None
        for sub in state["groups"]:
            if sub.get("parent") == gid:
                sub["parent"] = up
        if up:
            groups[up]["members"].extend(m for m in g["members"] if m not in groups[up]["members"])
        freed.extend(g["members"])
    state["groups"] = [g for g in state["groups"] if g["id"] not in gids]
    _tidy(project)
    return {"ungrouped": gids, "freed": freed}


def attach_to_group(project: Any, gid: str, ids: Sequence[str]) -> dict[str, Any]:
    """Add elements to an existing group. An element belongs to one group at a time, so it leaves the group it was in (reported)."""
    members = _ids(ids)
    state = _state(project, True)
    groups, member = _group_index(state)
    if gid not in groups:
        raise NotFound(f"Group {gid} does not exist")
    inside = set(_leaves(groups, gid))
    moved_from: dict[str, str] = {}
    added: list[str] = []
    for oid in members:
        _require(project, oid)
        old = member.get(oid)
        if oid in groups[gid]["members"]:
            continue
        if oid in inside:                                              # already in a sub-group of this one: leave it there
            continue
        if old:
            groups[old]["members"].remove(oid)
            moved_from[oid] = old
        groups[gid]["members"].append(oid)
        added.append(oid)
    return {"group": gid, "added": added, "left_group": moved_from}


def detach_from_group(project: Any, ids: Sequence[str]) -> dict[str, Any]:
    """Take elements out of the group they are in; they join its enclosing group, or become ungrouped. Their links are untouched."""
    members = _ids(ids)
    state = _state(project, True)
    groups, member = _group_index(state)
    out, skipped = [], []
    for oid in members:
        gid = member.get(oid)
        if not gid:
            skipped.append(oid)
            continue
        groups[gid]["members"].remove(oid)
        up = groups[gid].get("parent")
        if up in groups:
            groups[up]["members"].append(oid)
        out.append(oid)
    if not out:
        _tidy(project)
        raise BadRequest("None of the selected elements is in a group")
    _drop_empty_groups(state)
    _tidy(project)
    return {"detached": out, "not_grouped": skipped}


def detach_group(project: Any, gid: str) -> dict[str, Any]:
    """Take a sub-group out of its enclosing group (it stays a group)."""
    state = _state(project)
    groups, _ = _group_index(state)
    if gid not in groups:
        raise NotFound(f"Group {gid} does not exist")
    if not groups[gid].get("parent"):
        raise BadRequest("That group is not inside another group")
    groups[gid]["parent"] = groups[groups[gid]["parent"]].get("parent")
    return {"detached": gid}


def set_group(project: Any, gid: str, name: Any = None, pivot: Any = "keep") -> dict[str, Any]:
    state = _state(project)
    groups, _ = _group_index(state)
    if gid not in groups:
        raise NotFound(f"Group {gid} does not exist")
    g = groups[gid]
    if name is not None:
        g["name"] = text(name, "name", 80)
    if pivot != "keep":
        if pivot is None:
            g["pivot"] = None
        else:
            if not isinstance(pivot, (list, tuple)) or len(pivot) != 3 or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in pivot):
                raise BadRequest("pivot must be a point [x, y, z] in metres, or null for the automatic centre")
            g["pivot"] = [float(v) for v in pivot]
    return {"group": gid, "name": g["name"], "pivot": group_pivot(project, gid)}


def group_pivot(project: Any, gid: str) -> list[float] | None:
    """The reference point of a group: the one a person set, otherwise the centre of its members' bounding boxes (metres)."""
    state = _state(project)
    groups, _ = _group_index(state)
    g = groups.get(gid)
    if g is None:
        raise NotFound(f"Group {gid} does not exist")
    if g.get("pivot"):
        return list(g["pivot"])
    lo, hi = None, None
    for oid in _leaves(groups, gid):
        box = _bounds(project, oid)
        if box is None:
            continue
        lo = list(box[0]) if lo is None else [min(a, b) for a, b in zip(lo, box[0])]
        hi = list(box[1]) if hi is None else [max(a, b) for a, b in zip(hi, box[1])]
    return None if lo is None else [(a + b) / 2 for a, b in zip(lo, hi)]


def _bounds(project: Any, oid: str) -> tuple[Sequence[float], Sequence[float]] | None:
    try:
        return project.model.world_bounds(oid)
    except Exception:                                         # an element with no geometry of its own (a grid, an outline-only room)
        return None


def _drop_empty_groups(state: dict[str, Any]) -> None:
    while True:
        parents = {g.get("parent") for g in state["groups"]}
        empty = [g["id"] for g in state["groups"] if not g["members"] and g["id"] not in parents]
        if not empty:
            return
        state["groups"] = [g for g in state["groups"] if g["id"] not in empty]


def detach(project: Any, ids: Sequence[str], scope: str = "both", descendants_mode: str = "branch") -> dict[str, Any]:
    """The one Detach: ``scope`` is ``group`` (leave the group, keep the links), ``link`` (leave the parent, keep the group) or ``both``."""
    if scope not in ("group", "link", "both"):
        raise BadRequest("scope must be group, link or both")
    targets = _ids(ids)
    out: dict[str, Any] = {"scope": scope}
    state = _state(project, True)
    if scope in ("link", "both"):
        linked = [i for i in targets if i in state["links"]]
        if linked:
            out["link"] = unlink(project, linked, descendants_mode)
        elif scope == "link":
            raise BadRequest("None of the selected elements is linked to a parent")
    if scope in ("group", "both"):
        _, member = _group_index(_state(project))
        grouped = [i for i in targets if i in member]
        if grouped:
            out["group"] = detach_from_group(project, grouped)
        elif scope == "group":
            raise BadRequest("None of the selected elements is in a group")
    if "link" not in out and "group" not in out:
        raise BadRequest("The selected elements are neither linked nor grouped")
    _tidy(project)
    return out


# ------------------------------------------------------------------------------------------------------------------------- transformations
Spec = tuple  # ("move", dx, dy, dz) | ("turn", angle, cx, cy)


def _anchor(project: Any, oid: str) -> tuple[float, float] | None:
    box = _bounds(project, oid)
    return None if box is None else ((box[0][0] + box[1][0]) / 2, (box[0][1] + box[1][1]) / 2)


def _turn_point(p: Sequence[float], angle: float, centre: Sequence[float]) -> tuple[float, float]:
    c, s = math.cos(angle), math.sin(angle)
    dx, dy = p[0] - centre[0], p[1] - centre[1]
    return (centre[0] + dx * c - dy * s, centre[1] + dx * s + dy * c)


def _inherited(project: Any, link_: Mapping[str, Any], oid: str, parent_spec: Spec) -> Spec | None:
    """What a child does when its parent does ``parent_spec`` (None: nothing)."""
    inherit = link_["inherit"]
    if parent_spec[0] == "move":
        return parent_spec if "translate" in inherit else None
    angle, cx, cy = parent_spec[1:]
    if "translate" in inherit and "rotate" in inherit:
        return parent_spec
    if "rotate" in inherit:                                                      # turns about its own anchor: stays where it is
        anchor = _anchor(project, oid)
        return ("turn", angle, anchor[0], anchor[1]) if anchor else None
    anchor = _anchor(project, oid)                                               # carried round the centre without turning
    if anchor is None:
        return None
    x, y = _turn_point(anchor, angle, (cx, cy))
    return ("move", x - anchor[0], y - anchor[1], 0.0)


def plan(project: Any, ids: Sequence[str], spec: Spec, *, groups: bool = True, links: bool = True) -> list[tuple[str, Spec]]:
    """Who is transformed, and how, when ``ids`` are: each element at most once, parents before children, in a deterministic order."""
    chosen = _ids(ids)
    state = _state(project)
    result: dict[str, Spec] = {}
    if groups and state["groups"]:
        gmap, member = _group_index(state)
        for oid in chosen:
            gid = member.get(oid)
            top = _group_chain(gmap, gid)[-1] if gid else None
            for o in (_leaves(gmap, top) if top else [oid]):
                result.setdefault(o, spec)
    else:
        for oid in chosen:
            result.setdefault(oid, spec)
    if links and state["links"]:
        kids = _children_map(state)
        for root in list(result):
            stack: list[tuple[str, Spec]] = [(c, result[root]) for c in reversed(kids.get(root, ()))]
            while stack:
                cur, pspec = stack.pop()
                if cur in result:                                                # already moved on its own (a rigid member): never twice
                    continue
                mine = _inherited(project, state["links"][cur], cur, pspec)
                if mine is None:
                    continue                                                      # it does not follow this: nor do the elements below it
                result[cur] = mine
                stack.extend((c, mine) for c in reversed(kids.get(cur, ())))
    present = set(result)
    return [(o, s) for o, s in result.items() if not any(f in present for f in follows(project, o))]


def expand(project: Any, ids: Sequence[str], *, groups: bool = True, links: bool = False) -> list[str]:
    """``ids`` plus what goes with them (the rest of each outermost group; with ``links`` the elements that follow them), each once, in a stable order."""
    return [oid for oid, _ in plan(project, ids, ("move", 0.0, 0.0, 0.0), groups=groups, links=links)]


def apply_move(project: Any, ids: Sequence[str], dx: float, dy: float, dz: float, *, groups: bool = True) -> list[str]:
    done = plan(project, ids, ("move", dx, dy, dz), groups=groups)
    for oid, spec in done:
        E.move(project, oid, spec[1], spec[2], spec[3])
    return [o for o, _ in done]


def apply_turn(project: Any, ids: Sequence[str], angle: float, centre: tuple[float, float], *, groups: bool = True) -> list[str]:
    done = plan(project, ids, ("turn", angle, centre[0], centre[1]), groups=groups)
    turned = [(o, s) for o, s in done if s[0] == "turn"]
    for oid, spec in turned:
        E.rotate(project, oid, spec[1], (spec[2], spec[3]), rebuild=False)           # every parameter first, then every rebuild: walls that turn together join as they end up
    for oid, spec in done:
        if spec[0] == "move":
            E.move(project, oid, spec[1], spec[2], spec[3])
    for oid, _ in turned:
        if E.m(E.meta_of(project, oid), "category") != "grid":
            E.regenerate(project, oid)
    return [o for o, _ in done]


# ------------------------------------------------------------------------------------------------------------------------- upkeep
def on_delete(project: Any, oid: str) -> list[str]:
    """An element is about to be deleted: its children are given its parent (nothing below it is deleted or left dangling)."""
    state = _state(project)
    if oid not in state["links"] and not any(l["parent"] == oid for l in state["links"].values()):
        for g in state["groups"]:
            if oid in g["members"]:
                break
        else:
            return []
    notes: list[str] = []
    names = _names(project)
    _release(state, oid, "reparent", notes, names)
    for g in state["groups"]:
        if oid in g["members"]:
            g["members"].remove(oid)
    _drop_empty_groups(state)
    _tidy(project)
    return notes


def reconcile(project: Any) -> list[str]:
    """After every command: drop what the command left dangling (an element deleted by another route, a group emptied) and refuse nothing silently:
    what was dropped is returned. Costs nothing when relationships are not used."""
    state = project.manifest.get(KEY)
    if not isinstance(state, dict):
        return []
    notes: list[str] = []
    links = state.setdefault("links", {})
    for child in list(links):
        parent = links[child]["parent"]
        if not E.alive(project, child) or not E.alive(project, parent):
            del links[child]
            notes.append(f"A link to a deleted element was removed ({child})")
    for child in list(links):                                                    # a cycle can only come from a hand-edited file: break it at the entry point
        seen, cur = {child}, links.get(child)
        while cur:
            if cur["parent"] in seen:
                del links[child]
                notes.append(f"A circular dependency was broken at {child}")
                break
            seen.add(cur["parent"])
            cur = links.get(cur["parent"])
    gs = state.setdefault("groups", [])
    ids = {g["id"] for g in gs}
    for g in gs:
        g["members"] = [o for o in dict.fromkeys(g["members"]) if E.alive(project, o)]
        if g.get("parent") not in ids or g["parent"] == g["id"]:
            g["parent"] = None
    seen_members: set[str] = set()
    for g in gs:                                                                 # an element belongs to one group: the first claim stands
        g["members"] = [o for o in g["members"] if not (o in seen_members or seen_members.add(o))]
    gmap = {g["id"]: g for g in gs}
    for g in gs:
        if g["id"] in _group_chain(gmap, g.get("parent")):
            g["parent"] = None
    _drop_empty_groups(state)
    _tidy(project)
    return notes


def validate(project: Any) -> list[dict[str, Any]]:
    """Problems in the stored relationships, for the model check (an empty list for a sound model)."""
    state = project.manifest.get(KEY)
    out: list[dict[str, Any]] = []
    if not isinstance(state, dict):
        return out
    for child, l in (state.get("links") or {}).items():
        for oid, role in ((child, "child"), (l["parent"], "parent")):
            if not E.alive(project, oid):
                out.append({"kind": "dangling_link", "element": oid, "message": f"The {role} of a link no longer exists"})
            elif derived_reason(project, oid):
                out.append({"kind": "derived_link", "element": oid, "message": f"A link end whose place is derived: {derived_reason(project, oid)}"})
    return out


# ------------------------------------------------------------------------------------------------------------------------- reports
def scene_flags(project: Any) -> dict[str, dict[str, Any]]:
    """``{element: {"parent": id, "group": id}}`` for the 3D scene, so the viewport can select a group as one and tint a hierarchy. Empty when unused."""
    state = project.manifest.get(KEY)
    if not isinstance(state, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for child, link_ in (state.get("links") or {}).items():
        out.setdefault(child, {})["parent"] = link_["parent"]
    gmap, member = _group_index(_state(project))
    for oid, gid in member.items():
        chain = _group_chain(gmap, gid)
        out.setdefault(oid, {})["group"] = chain[-1]                      # the outermost group: what a click selects
    return out


def highlight(project: Any, ids: Sequence[str]) -> dict[str, Any]:
    """What the viewports tint for a selection: every related element with its role, the parent→child arrows and the groups in play."""
    chosen = _ids(ids)
    state = _state(project)
    kids = _children_map(state)
    gmap, member = _group_index(state)
    roles: dict[str, str] = {}
    edges: list[list[str]] = []
    for oid in chosen:
        for p in ancestors(project, oid):
            roles.setdefault(p, "ancestor")
        for d in _descend(kids, [oid]):
            roles.setdefault(d, "descendant")
        for c in kids.get(oid, ()):
            roles[c] = "child"
        parent = parent_of(project, oid)
        if parent:
            roles[parent] = "parent"
    for oid in chosen:
        roles[oid] = "selected"
    involved = set(roles)
    for child, l in state["links"].items():
        if child in involved and l["parent"] in involved:
            edges.append([l["parent"], child])
    shown: dict[str, Any] = {}
    for oid in chosen:
        gid = member.get(oid)
        for g in _group_chain(gmap, gid):
            shown[g] = {"id": g, "name": gmap[g]["name"], "parent": gmap[g].get("parent"), "members": _leaves(gmap, g)}
    for g in shown.values():
        for o in g["members"]:
            roles.setdefault(o, "group")
    return {"roles": roles, "edges": edges, "groups": list(shown.values())}


def structure(project: Any) -> dict[str, Any]:
    """A read-only picture of the relationships for browsers and reports: ``links`` (child -> {parent, inherit}), ``groups`` (id -> {id, name, parent, members}),
    ``member`` (element -> its innermost group) and ``names`` (element id -> name). Nothing in it is shared with the project."""
    state = _state(project)
    groups, member = _group_index(state)
    return {"links": {c: {"parent": l["parent"], "inherit": list(l["inherit"])} for c, l in state["links"].items()},
            "groups": {gid: {"id": gid, "name": g["name"], "parent": g.get("parent"), "members": list(g["members"])} for gid, g in groups.items()},
            "member": dict(member), "names": _names(project)}


def tree(project: Any) -> dict[str, Any]:
    """The whole structure for the Relationships panel: link trees (roots first) and the group hierarchy."""
    state = _state(project)
    names = _names(project)
    kids = _children_map(state)
    roots = [p for p in kids if p not in state["links"] and p in names]

    def node(oid: str, depth: int = 0) -> dict[str, Any]:
        link_ = state["links"].get(oid)
        return {"id": oid, "name": names.get(oid, oid), "inherit": link_["inherit"] if link_ else None,
                "children": [node(c, depth + 1) for c in kids.get(oid, ())] if depth < 500 else []}
    gmap, _ = _group_index(state)
    return {"chains": [node(r) for r in roots],
            "groups": [{"id": g["id"], "name": g["name"], "parent": g.get("parent"), "members": [{"id": m, "name": names.get(m, m)} for m in g["members"]],
                        "pivot": group_pivot(project, g["id"])} for g in state["groups"]],
            "links": len(state["links"]), "issues": validate(project)}


def rows(project: Any, oid: str) -> list[dict[str, Any]]:
    """The relationship rows of the Properties panel (links a person can follow)."""
    state = _state(project)
    if not state["links"] and not state["groups"]:
        return []
    names = _names(project)
    out: list[dict[str, Any]] = []

    def link_row(label: str, target: str) -> dict[str, Any]:
        return {"label": label, "type": "link", "value": names.get(target, target), "target": {"select": {"model": target}}}
    parent = state["links"].get(oid)
    if parent:
        out.append(link_row("Follows", parent["parent"]))
        out.append({"label": "Inherits", "type": "readonly", "value": " and ".join(parent["inherit"])})
    kids = _children_map(state).get(oid, [])
    if kids:
        out.append({"label": "Children", "type": "readonly", "value": f"{len(kids)} direct, {len(_descend(_children_map(state), [oid]))} in all"})
        out.extend(link_row("Child", c) for c in kids[:12])
    gmap, member = _group_index(state)
    gid = member.get(oid)
    if gid:
        path = " › ".join(gmap[g]["name"] for g in reversed(_group_chain(gmap, gid)))
        out.append({"label": "Group", "type": "readonly", "value": f"{path} ({len(_leaves(gmap, _group_chain(gmap, gid)[-1]))} elements)"})
    return out
