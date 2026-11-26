"""The Element Browser: every model element as one hierarchy, built from what the project really stores.

Nothing here is kept. The browser never owns a relationship, a name or a flag: it reads the single source of truth (``relationships`` for links, chains and
groups, element metadata for hosts and void cutting settings) and answers with a tree. Every change a person makes in the browser is one of the existing
commands (``rel.link``, ``rel.attach``, ``rel.group``, ``rel.detach`` ...), so there is no second implementation that could disagree with them. ``plan_drop``
only says which of those commands a drag would run, after checking what that command would refuse.

One element, one place
    An element can take part in several relationships at once (a column in a group that also follows another column; a window in a wall that a void is
    hosted on). The tree shows it once, under its *primary* parent, and lists the rest on the node (``group``, ``follows``, ``hosts``, ``cut_by``,
    ``excluded_by``) so no underlying object is ever duplicated. The primary parent is chosen by one fixed rule:

    1. A member of a group sits in that group, unless the element it follows is in the same group: then it sits under that element, so a chain inside a
       group keeps its depth.
    2. Otherwise an element that follows another (a link or chain) sits under the element it follows.
    3. Otherwise an element placed by a host (a door in its wall, a gutter on its roof, a void whose cutting host is set) sits under its host.
    4. Otherwise it sits under the project.

    ``relation`` on each node says which rule put it there (``group``, ``link``, ``host`` or ``root``), so a group membership is never presented as a
    transformation dependency, and a hosted element is never presented as a linked one.

The result is flat (``nodes`` by id and ``order``, the children of each node) rather than nested, so a client can keep what it already shows, redraw one
branch, and draw only the rows in view.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from ..tools.errors import BadRequest, NotFound
from . import cutting as CU
from . import elements as E
from . import relationships as RS

ROOT = "project"
SORTS = ("name", "category", "level", "model")
DROP_MODES = ("link", "group", "attach", "detach")


def _natural(text: str) -> list[Any]:
    """A sort key that puts 'Wall 2' before 'Wall 10'."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", text.lower())]


def _level_names(project: Any) -> tuple[dict[str, str], dict[str, int]]:
    levels = sorted(project.manifest.get("levels", []), key=lambda l: l.get("elevation", 0))
    return {l["id"]: l.get("name", l["id"]) for l in levels}, {l["id"]: i for i, l in enumerate(levels)}


def _void_settings(project: Any, ids: Sequence[str], categories: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    """The cutting settings of every void (a void extrusion, or a sweep made a void), by id."""
    out: dict[str, dict[str, Any]] = {}
    for oid in ids:
        meta = E.meta_of(project, oid)
        if categories[oid] == "void" or E.m(meta, "void_mode"):
            out[oid] = CU.settings(meta)
    return out


def build(project: Any, *, search: str = "", categories: Sequence[str] | None = None, kinds: Sequence[str] | None = None,
          sort: str = "name", descending: bool = False) -> dict[str, Any]:
    """The hierarchy. ``search`` matches name, category, type, level, id and group name (case-insensitive, every word must match); ``categories`` and ``kinds``
    (``element`` / ``group`` / ``void``) narrow it. A branch that has a match inside it stays so the match is shown where it lives; ``match`` marks the nodes
    that matched themselves."""
    if sort not in SORTS:
        raise BadRequest(f"sort must be one of {', '.join(SORTS)}")
    snap = RS.structure(project)
    level_name, level_rank = _level_names(project)
    ids = E.element_ids(project)
    alive = set(ids)
    cats = {oid: E.category(project, oid) for oid in ids}
    voids = _void_settings(project, ids, cats)
    model_order = {oid: i for i, oid in enumerate(ids)}

    nodes: dict[str, dict[str, Any]] = {ROOT: {"id": ROOT, "kind": "project", "name": project.manifest.get("name") or "Project", "parent": None,
                                                "relation": None, "children": 0}}
    for oid in ids:
        meta = E.meta_of(project, oid)
        level = E.m(meta, "level")
        follows = snap["links"].get(oid)
        hosts = [h for h in RS.follows(project, oid) if h != (follows or {}).get("parent")]
        cut = voids.get(oid)
        if cut and cut["host"] and E.alive(project, cut["host"]) and cut["host"] not in hosts:
            hosts.append(cut["host"])
        nodes[oid] = {"id": oid, "kind": "void" if oid in voids else "element", "name": snap["names"].get(oid, oid), "category": cats[oid],
                      "type": E.m(meta, "type") or None, "level": level_name.get(level) if level else None, "level_id": level or None,
                      "group": snap["member"].get(oid), "follows": follows["parent"] if follows else None,
                      "inherit": follows["inherit"] if follows else None, "hosts": hosts, "cuts": [], "cut_by": [], "excluded_by": [],
                      "parent": None, "relation": None, "children": 0}
    for gid, g in snap["groups"].items():
        nodes[gid] = {"id": gid, "kind": "group", "name": g["name"], "category": "group", "type": None, "level": None, "level_id": None,
                      "group": g.get("parent"), "follows": None, "inherit": None, "hosts": [], "cuts": [], "cut_by": [], "excluded_by": [],
                      "parent": None, "relation": None, "children": 0, "members": len(g["members"])}

    # What each void does and what is done to each element. Only the settings are read (which elements a void names, hosts or leaves alone), so this
    # stays linear; the full per-element result of a cut is ``inspect``.
    for vid, v in voids.items():
        if v["policy"] == "targets":
            nodes[vid]["cuts"] = [t for t in v["targets"] if t in alive]
        elif v["policy"] == "hosted" and v["host"] in alive:
            nodes[vid]["cuts"] = [v["host"]]
        for oid in nodes[vid]["cuts"]:
            nodes[oid]["cut_by"].append(vid)
        for oid in v["excluded"]:
            if oid in alive:
                nodes[oid]["excluded_by"].append(vid)
        if v["excluded_categories"]:
            for oid in ids:
                if cats[oid] in v["excluded_categories"] and vid not in nodes[oid]["excluded_by"] and oid != vid:
                    nodes[oid]["excluded_by"].append(vid)
    for oid in ids:
        for vid in (E.m(E.meta_of(project, oid), "void_boolean_modifiers") or {}):
            if vid in voids and vid not in nodes[oid]["cut_by"]:
                nodes[oid]["cut_by"].append(vid)                                  # a 3D Boolean cut this element carries now

    issues: list[dict[str, str]] = []
    for nid, node in nodes.items():
        if nid == ROOT:
            continue
        node["parent"], node["relation"] = _primary_parent(nid, node, nodes, snap)

    for nid in list(nodes):                                                      # a cycle should be impossible (links and groups refuse them); never draw one
        if nid == ROOT:
            continue
        seen, cur = {nid}, nodes[nid]["parent"]
        while cur and cur != ROOT:
            if cur in seen:
                issues.append({"severity": "error", "ref": nid, "message": f"{nodes[nid]['name']} is part of a circular hierarchy; it is shown under the project"})
                nodes[nid]["parent"], nodes[nid]["relation"] = ROOT, "root"
                break
            seen.add(cur)
            cur = nodes[cur]["parent"]

    words = [w for w in re.split(r"\s+", search.strip().lower()) if w]
    want_cats, want_kinds = set(categories or ()), set(kinds or ())

    def own_match(node: Mapping[str, Any]) -> bool:
        if want_cats and node["category"] not in want_cats:
            return False
        if want_kinds and node["kind"] not in want_kinds:
            return False
        if not words:
            return True
        group = snap["groups"].get(node["group"] or "", {}).get("name", "")
        hay = " ".join(str(x or "") for x in (node["name"], node["category"], node["type"], node["level"], node["id"], group)).lower()
        return all(w in hay for w in words)

    filtering = bool(words or want_cats or want_kinds)
    for nid, node in nodes.items():
        if nid != ROOT:
            node["match"] = own_match(node)
    shown = {ROOT}
    for nid, node in nodes.items():
        if nid != ROOT and (not filtering or node["match"]):
            cur = nid
            while cur and cur not in shown:
                shown.add(cur)
                cur = nodes[cur]["parent"]

    def key(nid: str) -> tuple[Any, ...]:
        n = nodes[nid]
        name = _natural(n["name"])
        if sort == "category":
            primary: Any = n["category"]
        elif sort == "level":
            primary = level_rank.get(n["level_id"] or "", -1)
        elif sort == "model":
            primary = model_order.get(nid, -1)
            name = []
        else:
            primary = 0
        return (0 if n["kind"] == "group" else 1, primary, name, nid)               # groups first, then the rest; ties settled by id so the order never varies

    order: dict[str, list[str]] = {nid: [] for nid in shown}
    for nid in shown:
        if nid != ROOT:
            order[nodes[nid]["parent"]].append(nid)
    for nid, kids in order.items():
        kids.sort(key=key, reverse=descending)
        nodes[nid]["children"] = len(kids)
    out_nodes = {nid: nodes[nid] for nid in shown}
    return {"root": ROOT, "nodes": out_nodes, "order": order, "sort": sort, "descending": descending, "search": search, "revision": project.revision,
            "counts": {"elements": len(ids), "groups": len(snap["groups"]), "voids": len(voids), "shown": len(shown) - 1}, "issues": issues}


def _primary_parent(oid: str, node: Mapping[str, Any], nodes: Mapping[str, Mapping[str, Any]], snap: Mapping[str, Any]) -> tuple[str, str]:
    """(parent id, relation) by the rule in the module docstring."""
    if node["kind"] == "group":
        parent = node["group"]
        return (parent, "group") if parent in nodes else (ROOT, "root")
    group, follows = node["group"], node["follows"]
    follows = follows if follows in nodes and nodes[follows]["kind"] != "group" else None
    if group in nodes:
        if follows and nodes[follows]["group"] == group:
            return follows, "link"
        return group, "group"
    if follows:
        return follows, "link"
    for host in node["hosts"]:
        if host in nodes and host != oid:
            return host, "host"
    return ROOT, "root"


def inspect(project: Any, oid: str) -> dict[str, Any]:
    """Everything the browser can say about one element's relationships: where it sits and why, what it follows and hosts, and its cutting relationships.

    For a void this includes ``cutting.elements`` (the same classification the void's Properties show: what is cut, what is not supported, what is left
    alone and why). For any other element ``voids`` lists each void's policy towards it, by the same ``cutting.applies`` rule that decides the cut."""
    snap = RS.structure(project)
    if oid in snap["groups"]:
        g = snap["groups"][oid]
        return {"id": oid, "kind": "group", "name": g["name"], "parent": g["parent"], "members": [{"id": m, "name": snap["names"].get(m, m)} for m in g["members"]],
                "subgroups": [x["id"] for x in snap["groups"].values() if x.get("parent") == oid]}
    if not E.alive(project, oid):
        raise NotFound(f"{oid} is not an element or a group")
    names = snap["names"]
    ref = lambda i: {"id": i, "name": names.get(i, i), "category": E.category(project, i)}                    # noqa: E731
    meta = E.meta_of(project, oid)
    category = E.category(project, oid)
    page = build(project)
    node = page["nodes"][oid]
    here = {"id": oid, "kind": node["kind"], "name": node["name"], "category": category,
            "parent": node["parent"], "relation": node["relation"],
            "group": [ref_group(snap, g) for g in RS._group_chain(snap["groups"], node["group"])][::-1] if node["group"] else [],
            "follows": ref(node["follows"]) if node["follows"] else None, "inherit": node["inherit"],
            "children": [{**ref(c), "relation": page["nodes"][c]["relation"]} for c in page["order"].get(oid, []) if c in names],
            "hosts": [ref(h) for h in node["hosts"]],
            "hosted": [ref(c) for c in page["order"].get(oid, []) if c in names and page["nodes"][c]["relation"] == "host"],
            "chain_depth": len(RS.ancestors(project, oid))}
    if node["kind"] == "void":
        rows = CU.classify(project, oid)
        here["cutting"] = {"settings": CU.settings(meta), "summary": CU.summary(rows), "elements": rows, "missing": CU.stale(project, _void_for(project, oid))}
    else:
        towards = []
        for vid, settings in _void_settings(project, E.element_ids(project), {i: E.category(project, i) for i in E.element_ids(project)}).items():
            yes, why = CU.applies(settings, oid, category)
            if yes and settings["policy"] == "automatic":
                state = "may cut it where it reaches it"
            elif yes:
                state = "is chosen to cut it"
            else:
                state = "leaves it alone"
            towards.append({**ref(vid), "state": state, "applies": yes, "reason": why or None})
        here["voids"] = towards
        here["cut_by"] = [ref(v) for v in node["cut_by"]]
        here["excluded_by"] = [ref(v) for v in node["excluded_by"]]
    return here


def ref_group(snap: Mapping[str, Any], gid: str) -> dict[str, str]:
    return {"id": gid, "name": snap["groups"][gid]["name"]}


def _void_for(project: Any, vid: str) -> Mapping[str, Any]:
    from . import forms as FX
    return next(v for v in FX.voids(project) if v["id"] == vid)


def plan_drop(project: Any, ids: Sequence[str], target: str | None, mode: str) -> dict[str, Any]:
    """What dragging ``ids`` onto ``target`` would do, for the ``mode`` the person chose, as the existing command to run and its parameters.

    The mode is never guessed from the drop: dropping onto a row means link, group, attach or detach only because the person said which. Everything that
    command would refuse (a missing or derived end, itself, a circular dependency) is refused here, with the same message, before anything changes. Placement
    is never changed by any of them: a child's offset from its parent is read from the geometry, so linking, grouping and detaching keep the world position."""
    if mode not in DROP_MODES:
        raise BadRequest(f"mode must be one of {', '.join(DROP_MODES)}", hint="Say whether the drop links, groups, attaches or detaches: it is never guessed")
    picked = RS._ids(list(ids))
    snap = RS.structure(project)
    for oid in picked:
        if oid not in snap["groups"] and not E.alive(project, oid):
            raise NotFound(f"{oid} does not exist")
    if mode == "detach":
        groups = [i for i in picked if i in snap["groups"]]
        if groups:
            return {"op": "rel.detach", "params": {"groups": groups}, "summary": "Take the group out of the group it is in; its members stay in it", "world_placement": "unchanged"}
        return {"op": "rel.detach", "params": {"ids": picked, "scope": "both", "descendants": "branch"},
                "summary": "Take the elements out of their group and let them stop following their parent; whatever follows them stays with them", "world_placement": "unchanged"}
    if mode in ("link", "attach") and not target:
        raise BadRequest("Choose where to drop them")
    if mode == "link":
        if target in snap["groups"]:
            raise BadRequest("A group is not an element: to put elements in a group, attach them to it")
        elements = [i for i in picked if i not in snap["groups"]]
        RS.check_link(project, elements, target)
        return {"op": "rel.link", "params": {"ids": elements, "parent": target},
                "summary": f"{len(elements)} element(s) will follow {snap['names'].get(target, target)}: moving it moves them, moving them does not move it",
                "world_placement": "unchanged"}
    if mode == "attach":
        if target not in snap["groups"]:
            raise BadRequest("Attach puts elements in a group: drop on a group, or choose link to make them follow an element")
        if any(i in snap["groups"] for i in picked):
            raise BadRequest("Drop a group on another group by grouping it inside it (group mode with 'within')")
        for oid in picked:
            RS._require(project, oid)
        return {"op": "rel.attach", "params": {"ids": picked, "group": target},
                "summary": f"{len(picked)} element(s) join the group {snap['groups'][target]['name']}; they keep their own links and parameters", "world_placement": "unchanged"}
    elements = [i for i in picked if i not in snap["groups"]]                        # group
    if len(elements) < 1:
        raise BadRequest("Choose at least one element to group")
    params: dict[str, Any] = {"ids": elements}
    if target in snap["groups"]:
        params["within"] = target
    return {"op": "rel.group", "params": params, "summary": "Collect the elements in a new group" + (f" inside {snap['groups'][target]['name']}" if target in snap["groups"] else ""),
            "world_placement": "unchanged"}
