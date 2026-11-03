"""SPADRA's application object, settings and start-up. The shared mechanics (loopback server, token handshake, config, storage, SLAI
lookup) come from ``applications.tools``; only what is specific to a CAD/BIM project is here and in ``project.py``."""
from __future__ import annotations

import argparse
import json
import secrets
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..tools import slai
from ..tools.errors import AppError, BadRequest, Conflict
from ..tools.server import Identity, LocalServer, Router, ServerOptions, add_server_arguments, refuse_remote_host, serve
from ..tools.storage import atomic_write_json, default_data_dir
from . import __version__
from . import startup as ST
from .animation_bridge import BRIDGE
from .commits import SpadraCommits
from .diagnostics import Diagnostics, CATEGORIES
from .jobs import RUNNERS as JOB_KINDS, JobManager
from .engineering_bridge import BRIDGE as ENGINEERING
from .optimization_bridge import status as OPTIMIZATION_STATUS
from .simulation_bridge import status as SIMULATION_STATUS
from .spatial_bridge import BRIDGE as SPATIAL
from .project import SUFFIX, Project, slug
from .utils.spadra_errors import StartupError
from .utils.config_loader import DEFAULT_PORT, Plan, builtin_plan, config_file, load_settings_values

SLUG, TITLE = "spadra", "SPADRA"
HERE = Path(__file__).resolve().parent


@dataclass
class Settings:
    port: int
    max_request_bytes: int
    undo_depth: int
    max_scene_vertices: int
    recent_projects: int
    data_dir: Path
    config_path: Path
    note: str | None = None
    backups_kept: int = 5
    plan: Plan = field(default_factory=builtin_plan)


def load_settings(config: str | None = None, data_dir: str | None = None, port: int | None = None, *, plan: str | None = None,
                  plans: str | None = None) -> Settings:
    """Settings from ``spadra_config.yaml`` with the chosen resource plan from ``spadra_plans.yaml`` applied (see utils/config_loader.py)."""
    loaded = load_settings_values(config, plans=plans, port=port, plan=plan)
    values = {k: v for k, v in loaded.values.items() if k != "plan"}
    return Settings(data_dir=Path(data_dir) if data_dir else default_data_dir(SLUG, TITLE), config_path=config_file(config),
                    note=" ".join(loaded.notes) or None, plan=loaded.plan, **values)


class SpadraApp:
    """What every request handler needs. One project is open at a time; ``lock`` serialises every read and change of it."""

    def __init__(self, settings: Settings, root: Path | None) -> None:
        self.settings = settings
        self.root = root
        self.project: Project | None = None
        self.lock = threading.RLock()
        self.projects_dir = settings.data_dir / "projects"
        self.recent_path = settings.data_dir / "recent.json"
        self.diagnostics = Diagnostics(settings.data_dir / "diagnostics.log")
        self.jobs = JobManager(self, self.diagnostics)
        self.commits = SpadraCommits(self)

    # ------------------------------------------------------------------------------------------------------------- lifecycle
    def _adopt(self, project: Project) -> Project:
        """Give a project the settings that are not part of its file."""
        project.backups_kept = self.settings.backups_kept
        return project

    def require(self) -> Project:
        if self.project is None:
            raise BadRequest("No project is open", hint="Create or open a project first")
        return self.project

    def new_project(self, name: str, unit: str, *, discard: bool = False) -> Project:
        if not discard:
            self._refuse_if_unsaved()
        self.project = self._adopt(Project.create(name, unit=unit, undo_depth=self.settings.undo_depth))
        return self.project

    def open_project(self, path: str, *, discard: bool = False) -> Project:
        if not discard:
            self._refuse_if_unsaved()
        folder = self._resolve(path)
        self.project = self._adopt(Project.open(folder, undo_depth=self.settings.undo_depth))
        self.remember(folder)
        return self.project

    def recover_project(self, backup_id: str, *, discard: bool = False) -> Project:
        """Open one of the open project's backups as a new unsaved copy. The project it came from is not touched."""
        if not discard:
            self._refuse_if_unsaved()
        source = self.require().root
        if source is None:
            raise BadRequest("This project has not been saved, so it has no backups")
        self.project = self._adopt(Project.recover(source, backup_id, undo_depth=self.settings.undo_depth))
        return self.project

    def save_project(self, path: str | None, name: str | None, *, save_as: bool = False) -> dict[str, Any]:
        project = self.require()
        if save_as:                                                    # a copy in a new folder; the original is left as it is
            if not (path or name):
                raise BadRequest("Give the copy a name or a folder")
            target = self._resolve(path) if path else self._unique_folder(name or project.name)
            result = project.save_as(target, name or Path(target).stem)
        else:
            target = None
            if path or name or project.root is None:
                target = self._resolve(path) if path else self._unique_folder(name or project.name)
            result = project.save(target)
        self.remember(Path(result["path"]))
        return result

    def close_project(self, *, discard: bool) -> None:
        if self.project is not None and self.project.dirty and not discard:
            raise Conflict("The project has unsaved changes", hint="Save it, or close and discard the changes")
        self.project = None

    def _refuse_if_unsaved(self) -> None:
        if self.project is not None and self.project.dirty:
            raise Conflict("The open project has unsaved changes", hint="Save it or close it (discarding the changes) first")

    def _resolve(self, value: str) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = self.projects_dir / path
        if path.suffix != SUFFIX:
            raise BadRequest(f"A SPADRA project folder name ends in {SUFFIX}")
        return path

    def _unique_folder(self, name: str) -> Path:
        base = slug(name)
        candidate = self.projects_dir / f"{base}{SUFFIX}"
        n = 2
        while candidate.exists():
            candidate = self.projects_dir / f"{base} ({n}){SUFFIX}"
            n += 1
        return candidate

    # --------------------------------------------------------------------------------------------------------------- recents
    def recents(self) -> list[dict[str, str]]:
        try:
            data = json.loads(self.recent_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        items = data if isinstance(data, list) else []
        return [i for i in items if isinstance(i, dict) and isinstance(i.get("path"), str) and Path(i["path"]).is_dir()][: self.settings.recent_projects]

    def remember(self, folder: Path) -> None:
        if self.settings.recent_projects <= 0:
            return
        entry = {"path": str(folder), "name": folder.stem}
        items = [entry] + [i for i in self.recents() if i["path"] != entry["path"]]
        try:
            atomic_write_json(self.recent_path, items[: self.settings.recent_projects])
        except OSError:
            pass                      # a list of recent projects is a convenience: failing to keep it must never fail an open or a save

    # --------------------------------------------------------------------------------------------------------------- bootstrap
    def bootstrap(self) -> dict[str, Any]:
        return {
            "app": {"name": TITLE, "version": __version__, "slai": slai.status(self.root)},
            "project": self.project.summary() if self.project else None, "recent": self.recents(),
            "units": ["mm", "cm", "m"],
            # Only what works today. A capability turns true when it is implemented and tested, never before.
            "capabilities": {"drawing2d": True, "model3d": True, "walls": True, "sheets": "manifest", "exportSvg": True, "exportGlb": True,
                             "exportStl": True, "sections": True, "elevations": True, "schedules": True, "animation": BRIDGE.available, "engineering": ENGINEERING.available, "assistant": True,
                             "simulation": SIMULATION_STATUS()["available"], "optimization": OPTIMIZATION_STATUS()["available"], "spatial": SPATIAL.status()["available"],
                             "calculator": ENGINEERING.available},
            "storage": {"dataDir": str(self.settings.data_dir), "projectsDir": str(self.projects_dir)},
            "plan": self.settings.plan.to_dict(),
            "errors": {code: dict(info) for code, info in CATEGORIES.items()},
            "jobs": sorted(JOB_KINDS),
        }


def build_server(settings: Settings, token: str, *, root: Path | None, client_dir: Path | None = None, host: str = "127.0.0.1") -> tuple[LocalServer, SpadraApp]:
    from .routes import router

    app = SpadraApp(settings, root)
    options = ServerOptions(token=token, client_dir=client_dir or HERE / "dist", host=host, port=settings.port,
                            tools_web_dir=slai.applications_dir() / "tools" / "web",
                            asset_dirs=(HERE / "assets", slai.shared_assets_dir()), max_body_bytes=settings.max_request_bytes)
    return LocalServer(app, Identity(SLUG, TITLE), router, options), app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m applications.SPADRA", description=f"{TITLE}: CAD, BIM and parametric design on SLAI.")
    add_server_arguments(parser, title=TITLE, default_port=DEFAULT_PORT)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true", help="report whether this installation can run SPADRA (Python, packages, web client, agents) and exit; 0 when it can")
    modes.add_argument("--build", action="store_true", help="build the web client (npm run build) and exit")
    modes.add_argument("--dev", action="store_true", help="development start: rebuild the web client first when it is missing or older than its sources")
    parser.add_argument("--plan", help="resource plan from spadra_plans.yaml (overrides 'plan' in spadra_config.yaml)")
    parser.add_argument("--plans", help="path to a plans file (default: configs/spadra_plans.yaml)")
    args = parser.parse_args(argv)
    if refuse_remote_host(args.host, TITLE, "your projects"):
        return 2
    root = slai.activate(slai.find_slai_root(args.slai_root))
    client_dir = Path(args.client_dir) if args.client_dir else HERE / "dist"
    try:
        if args.check:
            findings = ST.check_environment(root, client_dir)
            print(f"{TITLE} {__version__} on this installation:\n{ST.render(findings)}")
            return 1 if ST.blocking(findings) else 0
        if args.build or (args.dev and ST.check_client(HERE, client_dir).status != "ok"):
            print(f"Building the web client in {HERE} ...", flush=True)
            ST.build_client(HERE)
            print("Built.", flush=True)
            if args.build:
                return 0
        problems = ST.blocking(ST.check_environment(root, client_dir, agents=False))
        if problems:
            raise StartupError("; ".join(f"{p.name}: {p.detail}" for p in problems), hint=" ".join(p.fix for p in problems if p.fix) or None,
                               details={"check": "python -m applications.SPADRA --check"})
        settings = load_settings(args.config, args.data_dir, args.port, plan=args.plan, plans=args.plans)
        token = args.token or secrets.token_urlsafe(32)
        server, app = build_server(settings, token, root=root, client_dir=client_dir, host=args.host)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    except AppError as exc:
        print(f"{exc.message} {exc.hint or ''}".strip(), file=sys.stderr)
        if exc.details.get("output"):
            print(exc.details["output"], file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"{TITLE} could not start: {exc.strerror or exc}. Is another copy already running? Try --port.", file=sys.stderr)
        return 1
    notes = [settings.note] if settings.note else []
    stale = ST.check_client(HERE, client_dir)
    if stale.status == "warn":
        notes.append(f"{stale.detail}. {stale.fix}")
    details = [("Data", str(settings.data_dir)), ("SLAI", str(root) if root else "not found"), ("Config", str(settings.config_path)), ("Plan", settings.plan.display_name)]
    return serve(server, title=TITLE, version=__version__, url=f"http://127.0.0.1:{server.port}/?token={token}", details=details,
                 notes=notes, open_browser=not args.no_browser)
