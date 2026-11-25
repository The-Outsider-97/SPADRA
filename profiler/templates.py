"""Starting points for a new profile. Each one is built with the same operations a user has, so it is a real parametric sketch (named parameters drive
driving dimensions), not a picture of one."""
from __future__ import annotations

from typing import Any, Callable

from .errors import ProfilerError
from .model import Sketch
from .session import Session


def _drive(S: Session, dim_type: str, refs: list[str], param: str, **kw: Any) -> None:
    did = S.run("dimension", {"type": dim_type, "refs": refs, "mode": "driving", **kw})["result"]["dimension"]
    S.run("param_link", {"dimension": did, "name": param})


def _blank(S: Session, a: dict) -> None:
    return None


def _rectangle(S: Session, a: dict) -> None:
    w, h = float(a.get("Width", 120)), float(a.get("Height", 100))
    S.run("param_add", {"name": "Width", "type": "length", "value": w})
    S.run("param_add", {"name": "Height", "type": "length", "value": h})
    e = S.run("rectangle", {"p1": [0, 0], "p2": [w, h]})["result"]["edges"]
    _drive(S, "horizontal", [e[0]], "Width", value=w)
    _drive(S, "vertical", [e[1]], "Height", value=h)
    S.run("pin", {"vertex": S.sk.d["edges"][e[0]]["v"][0]})


def _circle(S: Session, a: dict) -> None:
    d = float(a.get("Diameter", 100))
    S.run("param_add", {"name": "Diameter", "type": "length", "value": d})
    e = S.run("circle", {"center": [0, 0], "radius": d / 2})["result"]["edge"]
    _drive(S, "diameter", [e], "Diameter", value=d)
    S.run("pin", {"vertex": S.sk.d["edges"][e]["c"]})


def _angle(S: Session, a: dict) -> None:
    w, h, t = float(a.get("Width", 100)), float(a.get("Height", 150)), float(a.get("Thickness", 12))
    for n, v in (("Width", w), ("Height", h), ("Thickness", t)):
        S.run("param_add", {"name": n, "type": "length", "value": v})
    e = S.run("polyline", {"points": [[0, 0], [w, 0], [w, t], [t, t], [t, h], [0, h]], "closed": True})["result"]["edges"]
    for i in (0, 2, 4):
        S.run("constrain", {"kind": "horizontal", "refs": [e[i]]})
    for i in (1, 3, 5):
        S.run("constrain", {"kind": "vertical", "refs": [e[i]]})
    _drive(S, "horizontal", [e[0]], "Width", value=w)
    _drive(S, "vertical", [e[5]], "Height", value=h)
    _drive(S, "vertical", [e[1]], "Thickness", value=t)
    _drive(S, "horizontal", [e[4]], "Thickness", value=t)
    S.run("pin", {"vertex": S.sk.d["edges"][e[0]]["v"][0]})


TEMPLATES: dict[str, tuple[str, Callable[[Session, dict], None]]] = {
    "blank": ("Blank profile", _blank),
    "rectangle": ("Rectangle (Width, Height)", _rectangle),
    "circle": ("Circle (Diameter)", _circle),
    "angle": ("L-angle (Width, Height, Thickness)", _angle),
}


def listing() -> list[dict[str, str]]:
    return [{"id": k, "label": v[0]} for k, v in TEMPLATES.items()]


def build(template: str, name: str, unit: str = "mm", values: dict | None = None) -> Session:
    if template not in TEMPLATES:
        raise ProfilerError("unknown template %r" % template, details={"known": sorted(TEMPLATES)})
    S = Session(Sketch(name, unit=unit))
    TEMPLATES[template][1](S, dict(values or {}))
    # a template is a starting state, not an edit: clear its history so Undo cannot take the user below it
    S.undo.clear()
    S.redo.clear()
    S.last = None
    S.sk.d["revision"] = 0
    S.saved_digest = None
    S.dirty = template != "blank"
    return S
