"""The two ribbons beyond the project ones: **Plug-ins** (Profiler, Modeller, CADCon, ModelPainter) and **Editor** (Animation, Presentator).

Every entry reports what it really is in *this* installation, found by looking - a package that imports, a folder that exists, an agent that loads - and
never what the product is hoped to become. A tool that is not here says so, with the reason; nothing opens a placeholder editor.

``state`` is one of
``ready``      integrated and usable from SPADRA now;
``separate``   installed as its own SLAI application; SPADRA can name it but does not embed it;
``unavailable`` integrated in SPADRA but a dependency is missing (``reason`` says which);
``missing``    not part of this installation.
"""
from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
APPS = HERE.parent


def _spec(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _profiler() -> dict[str, Any]:
    try:
        importlib.import_module(__package__ + ".profiler.session")
        importlib.import_module(__package__ + ".profiler.transfer")
    except Exception as exc:                                                       # a missing dependency or a broken install
        return {"state": "unavailable", "reason": "%s: %s" % (type(exc).__name__, exc)}
    dxf = _spec("ezdxf")
    out: dict[str, Any] = {"state": "ready", "detail": "Parametric 2D profile editor: draw, constrain, dimension, check and send to the model.",
                           "exchange": {"sdrw": True, "svg": True, "dxf": dxf}}
    if not dxf:
        out["notes"] = ["DXF import and export need the 'ezdxf' package, which is not installed here."]
    return out


def _separate(app_dir: str, label: str, run: str) -> dict[str, Any]:
    folder = APPS / app_dir
    if not folder.is_dir() or not (folder / "__init__.py").is_file():
        return {"state": "missing", "reason": "%s is not part of this installation (no applications/%s folder)." % (label, app_dir)}
    return {"state": "separate", "detail": "%s is installed as its own SLAI application. SPADRA does not embed it." % label, "run": run}


def _animation() -> dict[str, Any]:
    from .animation_bridge import BRIDGE
    if BRIDGE.available:
        return {"state": "ready", "detail": "Animation timeline, driven by the SLAI Animation Agent.", "opens": "timeline"}
    return {"state": "unavailable", "reason": BRIDGE.reason or "The SLAI Animation Agent is not available."}


def registry() -> dict[str, Any]:
    items: list[dict[str, Any]] = []

    def add(ribbon: str, key: str, label: str, info: dict[str, Any]) -> None:
        items.append({"ribbon": ribbon, "id": key, "label": label, **info})

    add("Plug-ins", "profiler", "Profiler", _profiler())
    for key, label in (("modeller", "Modeller"), ("modelpainter", "ModelPainter")):
        add("Plug-ins", key, label, _separate({"modeller": "Modeller", "modelpainter": "ModelPainter"}[key], label, "python -m applications.%s" % label)
            if (APPS / label).is_dir() else {"state": "missing", "reason": "%s is not part of this installation (no applications/%s folder)." % (label, label)})
    add("Plug-ins", "cadcon", "CADCon", _separate("CADCon", "CADCon", "python -m applications.CADCon"))
    add("Editor", "animation", "Animation", _animation())
    add("Editor", "presentator", "Presentator",
        {"state": "missing", "reason": "Presentator is not part of this installation (no application or module of that name was found)."})
    return {"plugins": items, "ribbons": {"Plug-ins": ["profiler", "modeller", "cadcon", "modelpainter"], "Editor": ["animation", "presentator"]}}
