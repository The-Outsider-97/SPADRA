"""SPADRA's project: the one place that ties a model, drawings, levels, views and sheets together.

A project is a folder ``<name>.spadra/`` holding

* ``project.json``  - the manifest (identity, units, levels, views, sheets, and the revision and SHA-256 of every file below),
* ``model.sgeo``    - the 3D model, an SLAI ``SGEODocument`` (walls and other elements, with their semantics),
* ``drawings/<id>.sdrw`` - one SLAI ``SDRWDocument`` per drawing view (a floor plan per level, and so on).

Geometry is never stored or parsed here: SGEO and SDRW own it. This module owns only what neither format can express (the project
identity, levels, view and sheet lists) and the rules for changing the project:

* every change is a **command** (``execute``): validated, applied to a copy-on-write snapshot, all-or-nothing, one project revision;
* ``expected_revision`` rejects a change that was built against an older project (a stale UI or a stale AI proposal);
* ``undo``/``redo`` swap whole-document snapshots, so a complex edit can never be half undone;
* ``save`` writes each changed document atomically and the manifest last; ``open`` verifies the digests and says what differs.

Wall geometry is *derived*: a wall is stored as parameters (start, end, thickness, height, level, base offset) and its box and
transform are regenerated from them, so editing a parameter edits the geometry deterministically.
"""
from __future__ import annotations

import json
import math
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from ..tools.errors import BadRequest, Conflict, LimitExceeded, NotFound, StorageError
from ..tools.storage import atomic_write, atomic_write_json
from ..tools.validation import boolean, integer, new_id, number, text

from src.utils.sdrw import SDRWDocument
from src.utils.sgeo import SGEODocument
from src.utils.spatial_formats.common.errors import ResourceLimitError, SpatialFormatError
from src.utils.spatial_formats.sdrw.annotations import hatch_lines

from . import animation_authoring as AA
from . import animation_bridge as AB
from . import documentation as D
from . import interop as IO
from . import attachments as AT
from . import bimdata as BD
from . import requirements as RQ
from . import layers as LY
from . import reports as RP
from . import sheetviews as SV
from . import quantities as Q
from .utils.spadra_errors import BackupDamaged
from .utils.spadra_helpers import MM_PER, SUFFIX, UNITS, now_iso as _now, sha256_hex, slug
from . import structural as ST
from . import elements as E
from . import geometry as G
from . import annotations as AN
from . import autorooms as AR
from . import cutting as CU
from . import foundations as FD
from . import forms as FX
from . import arrays as AY
from . import datums as DT
from . import families as FM
from . import stairs as SR
from . import gutters as GU
from . import sheets as SH
from . import checks as CK

PROJECT_FORMAT = "spadra.project"
PROJECT_VERSION = 1
NS = E.NS                                      # metadata namespace for SPADRA's own element parameters
MATERIALS = E.MATERIALS
BACKUP_DIR = ".backups"                         # inside the project folder; ``open`` never reads it, so it cannot disturb a project
BACKUPS_KEPT = 5
BACKUP_MARKER = "backup.json"                   # written last: a backup folder without it was interrupted and is ignored
_BACKUP_ID = re.compile(r"^\d{8}T\d{6}Z-r\d{1,9}(-\d{1,3})?$")
NO_DRAWING = ("3d", "schedule")                 # view kinds that have no SDRW document of their own
MAX_COORD = 1.0e9                              # drawing units; anything larger is a typo, not a building
MAX_POINTS = 10_000
CATEGORIES = ("Walls", "Doors", "Windows", "Floors", "Roofs", "Gutters", "Downpipes", "Stairs", "Structural", "Rooms", "Grids", "Generic Models", "Extrusions", "Voids", "Sweeps", "Foundations", "Arrays")
CATEGORY_KEY = {"wall": "Walls", "door": "Doors", "window": "Windows", "floor": "Floors", "roof": "Roofs", "gutter": "Gutters", "downpipe": "Downpipes", "stair": "Stairs", "structural": "Structural", "room": "Rooms",
                "grid": "Grids", "generic": "Generic Models", "extrusion": "Extrusions", "void": "Voids", "sweep": "Sweeps", "foundation": "Foundations", "array": "Arrays"}


def _finite(value: Any, label: str, limit: float = MAX_COORD) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError) as exc:
        raise BadRequest(f"{label} must be a number") from exc
    if isinstance(value, bool) or not math.isfinite(v) or abs(v) > limit:
        raise BadRequest(f"{label} must be a finite number no larger than {limit:g} in magnitude")
    return v


def _point(value: Any, label: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise BadRequest(f"{label} must be a point [x, y]")
    return (_finite(value[0], f"{label}.x"), _finite(value[1], f"{label}.y"))


def _ids(value: Any, label: str = "ids") -> list[str]:
    if not isinstance(value, (list, tuple)) or not value or len(value) > 5000 or not all(isinstance(v, str) and 0 < len(v) <= 80 for v in value):
        raise BadRequest(f"{label} must be a non-empty list of ids")
    return list(value)


@dataclass
class Undo:
    label: str
    key: str                                   # "model" or "drawing:<id>"
    before: Any
    manifest: Any = None                       # the project structure (sheets, levels, views) as it was, so a sheet can be undone too
    extra: dict = field(default_factory=dict)  # other documents the same command changed (key -> the document as it was): a tag follows its element, a level takes its plan


@dataclass
class Project:
    root: Path | None
    manifest: dict[str, Any]
    model: SGEODocument
    drawings: dict[str, SDRWDocument]
    undo_depth: int = 100
    backups_kept: int = BACKUPS_KEPT
    dirty: bool = False
    undo_stack: list[Undo] = field(default_factory=list)
    redo_stack: list[Undo] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    _commands: dict[str, Callable[..., Any]] = field(default_factory=dict, repr=False)
    _touched: dict[str, Any] = field(default_factory=dict, repr=False)     # copies of the documents the running command has changed besides its own
    reports: dict[str, bytes] = field(default_factory=dict, repr=False)    # generated project reports (SLDC), written to reports/ on save
    snapshots: dict[str, bytes] = field(default_factory=dict, repr=False)  # the pictures of 3D snapshots placed on sheets (PNG, by viewport id), written to snapshots/ on save
    _cache: dict = field(default_factory=dict, repr=False)                  # derived data keyed by revision (cleared whenever the revision moves)

    # ------------------------------------------------------------------------------------------------------------ lifecycle
    @classmethod
    def create(cls, name: str, *, unit: str = "mm", root: Path | None = None, undo_depth: int = 100) -> "Project":
        name = text(name, "name", 80)
        if unit not in UNITS:
            raise BadRequest(f"unit must be one of {', '.join(UNITS)}")
        level_ids = [new_id() for _ in range(3)]
        levels = [{"id": level_ids[0], "name": "Level 01", "elevation": 0.0}, {"id": level_ids[1], "name": "Level 02", "elevation": 3.2},
                  {"id": level_ids[2], "name": "Roof Level", "elevation": 6.4}]
        views = [{"id": new_id(), "kind": "3d", "name": "3D Model"}]
        drawings: dict[str, SDRWDocument] = {}
        for level in levels:                                       # every level, including the roof, has an openable plan
            vid = new_id()
            views.append({"id": vid, "kind": "plan", "name": level["name"], "level_id": level["id"]})
            drawings[vid] = SDRWDocument.new(level["name"], unit=unit)
        for side in ("east", "north", "south", "west"):             # the four elevations, as a new project in a drawing office starts with
            vid = new_id()
            views.append({"id": vid, "kind": "elevation", "name": side.title(), "side": side})
            drawings[vid] = SDRWDocument.new(side.title(), unit=unit)
        manifest = {"format": PROJECT_FORMAT, "version": PROJECT_VERSION, "id": new_id(), "name": name, "unit": unit, "revision": 0,
                    "created": _now(), "modified": _now(), "levels": levels, "views": views, "sheets": [], "reports": [], "files": {}}
        project = cls(root, manifest, SGEODocument.new(name, unit="m"), drawings, undo_depth=undo_depth, dirty=True)
        project._register()
        return project

    @classmethod
    def open(cls, folder: Path | str, *, undo_depth: int = 100) -> "Project":
        import json
        root = Path(folder)
        manifest_path = root / "project.json"
        if not manifest_path.is_file():
            raise NotFound(f"{root} is not a SPADRA project (no project.json)")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BadRequest(f"project.json cannot be read: {exc}") from exc
        if not isinstance(manifest, dict) or manifest.get("format") != PROJECT_FORMAT:
            raise BadRequest("project.json is not a SPADRA project manifest")
        if int(manifest.get("version", 0)) > PROJECT_VERSION:
            raise BadRequest(f"This project was saved by a newer SPADRA (format {manifest.get('version')}); this version reads up to {PROJECT_VERSION}")
        for key in ("id", "name", "unit", "levels", "views", "sheets"):
            if key not in manifest:
                raise BadRequest(f"project.json is missing '{key}'")
        warnings: list[str] = []
        manifest.setdefault("reports", [])                          # projects saved before reports existed
        files = manifest.get("files") or {}

        def load(rel: str, opener: Callable[..., Any]) -> Any:
            path = root / rel
            if not path.is_file():
                raise NotFound(f"The project file {rel} is missing")
            recorded = files.get(rel, {}).get("sha256")
            if recorded and sha256_hex(path.read_bytes()) != recorded:
                warnings.append(f"{rel} was changed outside SPADRA since the project was last saved")
            return opener(path, mode="write")

        model = load("model.sgeo", SGEODocument.open)
        drawings = {v["id"]: load(f"drawings/{v['id']}.sdrw", SDRWDocument.open) for v in manifest["views"] if v.get("kind") not in NO_DRAWING}
        project = cls(root, manifest, model, drawings, undo_depth=undo_depth, warnings=warnings)
        for entry in list(manifest["reports"]):
            path = root / "reports" / f"{entry['id']}.sldc"
            try:
                data = path.read_bytes()
                if entry.get("sha256") and sha256_hex(data) != entry["sha256"]:
                    warnings.append(f"The report '{entry['name']}' was changed outside SPADRA since it was saved")
                project.reports[entry["id"]] = data
            except OSError:
                manifest["reports"].remove(entry)
                warnings.append(f"The report '{entry['name']}' is missing from the project folder and was dropped from the list")
        for sheet in manifest["sheets"]:
            for vp in list(sheet.get("viewports", [])):
                if SV.kind_of(vp) != "snapshot":
                    continue
                try:
                    data = (root / "snapshots" / f"{vp['id']}.png").read_bytes()
                    SV.png_size(data)
                    if vp["image"].get("sha256") and sha256_hex(data) != vp["image"]["sha256"]:
                        warnings.append(f"The 3D snapshot on sheet {sheet['number']} was changed outside SPADRA since it was saved")
                    project.snapshots[vp["id"]] = data
                except (OSError, BadRequest, LimitExceeded):
                    sheet["viewports"].remove(vp)
                    warnings.append(f"The 3D snapshot on sheet {sheet['number']} is missing or damaged in the project folder and was removed from the sheet")
        project._register()
        return project

    def save(self, folder: Path | str | None = None) -> dict[str, Any]:
        """Write the project. ``folder`` (Save As) must not already hold a project. Documents first, the manifest last."""
        target = Path(folder) if folder is not None else self.root
        if target is None:
            raise BadRequest("This project has not been saved yet: choose where to save it")
        if folder is not None and (target / "project.json").exists() and self.root != target:
            raise Conflict(f"{target} already holds a project; choose another name", hint="SPADRA never overwrites another project")
        if target.suffix != SUFFIX:
            raise BadRequest(f"A project folder name must end in {SUFFIX}")
        try:
            (target / "drawings").mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            from ..tools.errors import classify_oserror
            raise classify_oserror(exc, "save the project") from exc
        backup, backup_warning = None, None
        if target == self.root and (target / "project.json").is_file():       # saving over what is on disk: keep the previous version first
            try:
                backup = self._backup(target, self.backups_kept)
            except (OSError, ValueError) as exc:
                backup_warning = f"The previous version could not be backed up ({exc}); the project was saved anyway"
        files: dict[str, Any] = {}
        pairs: list[tuple[str, Any]] = [("model.sgeo", self.model)] + [(f"drawings/{k}.sdrw", d) for k, d in self.drawings.items() if k in {v["id"] for v in self.manifest["views"]}]
        for rel, doc in pairs:
            # The document serialises itself (verified package, exact bytes); the file is written through the tools layer, which falls back to
            # writing in place where Windows refuses to rename inside a redirected folder (WinError 17), as SPlanner and SciCal rely on.
            revision = doc.revision
            data = doc.dumps()
            atomic_write(target / rel, data)
            files[rel] = {"sha256": sha256_hex(data), "revision": revision}
        listed = {f"reports/{e['id']}.sldc": e for e in self.manifest["reports"]}
        if listed:
            (target / "reports").mkdir(exist_ok=True)
        for rel, entry in listed.items():
            data = self.reports[entry["id"]]
            atomic_write(target / rel, data)
            files[rel] = {"sha256": sha256_hex(data), "revision": entry["revision"]}
        placed = {f"snapshots/{vp['id']}.png": vp for sheet in self.manifest["sheets"] for vp in sheet.get("viewports", []) if SV.kind_of(vp) == "snapshot"}
        if placed:
            (target / "snapshots").mkdir(exist_ok=True)
        for rel, vp in placed.items():
            data = self.snapshots[vp["id"]]
            atomic_write(target / rel, data)
            files[rel] = {"sha256": sha256_hex(data), "revision": self.revision}
        self.manifest["files"] = files
        self.manifest["modified"] = _now()
        atomic_write_json(target / "project.json", self.manifest)
        for old in (target / "reports").glob("*.sldc") if (target / "reports").is_dir() else ():          # a deleted report's file goes once the manifest no longer lists it
            if f"reports/{old.name}" not in listed:
                old.unlink(missing_ok=True)
        for old in (target / "snapshots").glob("*.png") if (target / "snapshots").is_dir() else ():       # a removed snapshot's picture goes once no sheet shows it
            if f"snapshots/{old.name}" not in placed:
                old.unlink(missing_ok=True)
        self.root, self.dirty = target, False
        return {"path": str(target), "files": sorted(files), "revision": self.revision, "backup": backup, "backup_warning": backup_warning}

    # ------------------------------------------------------------------------------------------------------------- backups
    @staticmethod
    def _backup(root: Path, keep: int = BACKUPS_KEPT) -> str:
        """Copy what is on disk (not what is open) into ``.backups/<id>/`` before it is replaced. A backup is a complete project folder, so
        it opens like any other; its marker file is written last, and only a folder with a marker counts as a backup. The oldest are removed
        beyond ``BACKUPS_KEPT``."""
        manifest = json.loads((root / "project.json").read_text(encoding="utf-8"))
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        base = f"{stamp}-r{int(manifest.get('revision', 0))}"
        name, n = base, 1
        while (root / BACKUP_DIR / name).exists():
            n += 1
            name = f"{base}-{n}"
        folder = root / BACKUP_DIR / name
        (folder / "drawings").mkdir(parents=True)
        copied: dict[str, str] = {}
        (folder / "reports").mkdir()
        (folder / "snapshots").mkdir()
        for rel in (["project.json", "model.sgeo"] + sorted(p.relative_to(root).as_posix() for p in (root / "drawings").glob("*.sdrw"))
                    + sorted(p.relative_to(root).as_posix() for p in (root / "reports").glob("*.sldc"))
                    + sorted(p.relative_to(root).as_posix() for p in (root / "snapshots").glob("*.png"))):
            data = (root / rel).read_bytes()
            atomic_write(folder / rel, data)
            copied[rel] = sha256_hex(data)
        atomic_write_json(folder / BACKUP_MARKER, {"created": _now(), "name": manifest.get("name"), "revision": manifest.get("revision", 0), "files": copied})
        kept = Project.list_backups(root)
        for old in kept[keep:]:
            shutil.rmtree(root / BACKUP_DIR / old["id"], ignore_errors=True)
        return name

    @staticmethod
    def list_backups(root: Path | str | None) -> list[dict[str, Any]]:
        """The complete backups of a project folder, newest first."""
        if root is None:
            return []
        out: list[dict[str, Any]] = []
        base = Path(root) / BACKUP_DIR
        if not base.is_dir():
            return out
        for folder in base.iterdir():
            if not _BACKUP_ID.match(folder.name) or not (folder / BACKUP_MARKER).is_file():
                continue
            try:
                info = json.loads((folder / BACKUP_MARKER).read_text(encoding="utf-8"))
                size = sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())
                out.append({"id": folder.name, "created": str(info["created"]), "name": str(info.get("name", "")), "revision": int(info.get("revision", 0)), "bytes": size})
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return sorted(out, key=lambda b: (b["created"], b["id"]), reverse=True)

    @classmethod
    def recover(cls, root: Path | str, backup_id: str, *, undo_depth: int = 100) -> "Project":
        """Open a backup as a new, unsaved project (a copy: new id, never saved over the backup or the project it came from). Every file is
        checked against the hashes recorded when the backup was made; a backup that does not match is refused rather than opened."""
        if not _BACKUP_ID.match(str(backup_id)):
            raise BadRequest("That is not a backup name")
        folder = Path(root) / BACKUP_DIR / backup_id
        marker = folder / BACKUP_MARKER
        if not marker.is_file():
            raise NotFound("There is no complete backup with that name")
        try:
            recorded = json.loads(marker.read_text(encoding="utf-8"))["files"]
        except (OSError, ValueError, KeyError) as exc:
            raise BadRequest(f"The backup's record cannot be read: {exc}") from exc
        for rel, digest in recorded.items():
            path = folder / rel
            if not path.is_file() or sha256_hex(path.read_bytes()) != digest:
                raise BackupDamaged(f"The backup is damaged: {rel} does not match what was backed up", hint="Try an older backup")
        project = cls.open(folder, undo_depth=undo_depth)
        project.root = None
        project.manifest["id"] = new_id()
        project.manifest["commits"] = {}
        if not project.name.endswith("(recovered)"):
            project.manifest["name"] = f"{project.name[:66]} (recovered)"
        project.manifest["files"] = {}
        project.dirty = True
        return project

    def save_as(self, folder: Path | str, name: str | None = None) -> dict[str, Any]:
        """Write a copy of the project to a new folder and continue working on the copy. The copy is a different project (new id, and the
        new name if one is given); the original folder is never touched. If the write fails the open project keeps its own name and id."""
        target = Path(folder)
        if target.exists() and any(target.iterdir()):
            raise Conflict(f"{target} already exists", hint="Save As never overwrites another folder; choose another name")
        before = (self.manifest["id"], self.manifest["name"], self.root, self.dirty, self.manifest.get("files"), self.manifest.get("modified"), self.manifest.get("commits"))
        self.manifest["id"] = new_id()
        self.manifest["commits"] = {}                                # the copy is a different project; commits of this one were decided for this one
        if name:
            self.manifest["name"] = text(name, "name", 80)
        try:
            return self.save(target)
        except BaseException:
            self.manifest["id"], self.manifest["name"], self.root, self.dirty = before[:4]
            self.manifest["commits"] = before[6] or {}
            if before[4] is None:
                self.manifest.pop("files", None)
            else:
                self.manifest["files"] = before[4]
            if before[5] is not None:
                self.manifest["modified"] = before[5]
            raise

    def commit_ledger(self) -> "ProjectCommitLedger":
        """Decided commits (see ``commits.py``), kept in the manifest so they are saved with the project and not undone with its content."""
        return ProjectCommitLedger(self)

    def scratch(self) -> "Project":
        """A throw-away copy of the project in memory, for trying a change out (the assistant's preview). It has its own history, no folder, and is
        never saved or shown; nothing done to it can reach this project."""
        copy = Project.create(self.name, unit=self.manifest["unit"], undo_depth=self.undo_depth)
        copy.manifest = _clone(self.manifest)
        copy.model = self.model.copy(fresh_id=False)
        copy.drawings = {vid: doc.copy(fresh_id=False) for vid, doc in self.drawings.items()}
        copy.reports = dict(self.reports)
        copy.snapshots = dict(self.snapshots)
        copy.root = None
        return copy

    # ---------------------------------------------------------------------------------------------------------------- state
    @property
    def revision(self) -> int:
        return int(self.manifest["revision"])

    @property
    def name(self) -> str:
        return str(self.manifest["name"])

    def summary(self) -> dict[str, Any]:
        return {"id": self.manifest["id"], "name": self.name, "unit": self.manifest["unit"], "revision": self.revision, "dirty": self.dirty,
                "path": str(self.root) if self.root else None, "can_undo": bool(self.undo_stack), "can_redo": bool(self.redo_stack),
                "undo_label": self.undo_stack[-1].label if self.undo_stack else None, "warnings": list(self.warnings), "auto_rooms": AR.is_on(self)}

    def level(self, level_id: str) -> dict[str, Any]:
        for level in self.manifest["levels"]:
            if level["id"] == level_id:
                return level
        raise NotFound(f"Level {level_id} does not exist")

    def view(self, view_id: str) -> dict[str, Any]:
        for view in self.manifest["views"]:
            if view["id"] == view_id:
                return view
        raise NotFound(f"View {view_id} does not exist")

    def drawing(self, view_id: str) -> SDRWDocument:
        try:
            return self.drawings[view_id]
        except KeyError:
            raise NotFound(f"View {view_id} has no drawing") from None

    # ---------------------------------------------------------------------------------------------------------------- commands
    def execute(self, op: str, params: Mapping[str, Any] | None = None, *, expected_revision: int | None = None) -> dict[str, Any]:
        """Apply one command atomically. Raises ``Conflict`` for a stale revision, ``BadRequest``/``NotFound`` for a bad one;
        on any failure the project is exactly as it was."""
        params = dict(params or {})
        handler = self._commands.get(op)
        if handler is None:
            raise BadRequest(f"Unknown command '{op}'", details={"commands": sorted(self._commands)})
        if expected_revision is not None and int(expected_revision) != self.revision:
            raise Conflict(f"The project changed (now revision {self.revision}; this change was made for {expected_revision})",
                           hint="Review the current project and try again")
        key, label = handler.target(self, params)                       # type: ignore[attr-defined]
        doc = self.model if key == "model" else self.drawing(key.split(":", 1)[1])
        before = doc.copy(fresh_id=False)
        manifest_before = _clone(self.manifest)
        self._touched = {}
        try:
            result = handler(self, params)
            if key.startswith("drawing:"):
                E.refresh_opening_profiles_for_drawing(self, key.split(":", 1)[1],
                                                       MM_PER[self.manifest["unit"]] / 1000.0)
            if key == "model" and op.split(".")[0] not in ("anim", "project", "report", "bim"):
                FD.reconcile(self)                                      # foundations follow the walls and columns they stand on, and strips re-cut their joints
                AY.reconcile(self)                                      # linked arrays follow their sources; items a person edited or deleted are let go
                FX.reconcile(self)                                      # solids follow the voids that cut them
                AT.reconcile(self)                                      # walls attached to a roof or extended to a level follow it
                AR.reconcile(self)                                      # rooms follow the walls that enclose them, inside the same command
                AN.sync_tags(self)                                      # tags follow their elements inside the same command
        except Exception as exc:
            self._rollback(key, before, manifest_before)
            if isinstance(exc, (G.GeometryError, SpatialFormatError)):   # the document or the geometry refused: a bad request, not a crash
                raise BadRequest(str(exc)) from exc
            raise
        extra, self._touched = self._touched, {}
        self.manifest["revision"] = self.revision + 1
        self.undo_stack.append(Undo(label, key, before, manifest_before, extra))
        del self.undo_stack[:-self.undo_depth]
        self.redo_stack.clear()
        self.dirty = True
        self._cache.clear()
        return {"revision": self.revision, "result": result}

    def touch(self, key: str) -> None:
        """Say that the running command is about to change a document other than its own (``"model"`` or ``"drawing:<view id>"``), so Undo and a
        failed command put it back together with the rest."""
        if key not in self._touched:
            doc = self.model if key == "model" else self.drawing(key.split(":", 1)[1])
            self._touched[key] = doc.copy(fresh_id=False)

    def _rollback(self, key: str, before: Any, manifest_before: Any) -> None:
        self._restore(key, before)
        for k, doc in self._touched.items():
            self._restore(k, doc)
        self._touched = {}
        self.manifest = manifest_before

    def _restore(self, key: str, doc: Any) -> None:
        if key == "model":
            self.model = doc
        else:
            self.drawings[key.split(":", 1)[1]] = doc

    def _current(self, key: str) -> Any:
        return self.model if key == "model" else self.drawings[key.split(":", 1)[1]]

    def _swap(self, entry: Undo) -> Undo:
        current = self._current(entry.key)
        current_extra = {k: self._current(k) for k in entry.extra}
        current_manifest = _clone(self.manifest)
        self._restore(entry.key, entry.before)
        for k, doc in entry.extra.items():
            self._restore(k, doc)
        revision, commits = self.manifest["revision"], self.manifest.get("commits", {})
        self.manifest = _clone(entry.manifest)                       # structure goes back with the document; the revision only ever moves forward
        self.manifest["revision"] = revision
        self.manifest["commits"] = commits                           # and so do the receipts of commits that happened: undoing a change must not forget it was committed
        self._cache.clear()
        return Undo(entry.label, entry.key, current, current_manifest, current_extra)

    def undo(self) -> dict[str, Any]:
        if not self.undo_stack:
            raise BadRequest("Nothing to undo")
        entry = self.undo_stack.pop()
        self.redo_stack.append(self._swap(entry))
        self.manifest["revision"] = self.revision + 1
        self.dirty = True
        return {"revision": self.revision, "label": entry.label}

    def redo(self) -> dict[str, Any]:
        if not self.redo_stack:
            raise BadRequest("Nothing to redo")
        entry = self.redo_stack.pop()
        self.undo_stack.append(self._swap(entry))
        self.manifest["revision"] = self.revision + 1
        self.dirty = True
        return {"revision": self.revision, "label": entry.label}

    def _register(self) -> None:
        for name in dir(_Commands):
            fn = getattr(_Commands, name)
            if callable(fn) and hasattr(fn, "target"):
                self._commands[name.replace("__", ".")] = fn

    # ------------------------------------------------------------------------------------------------------------------ reads
    def tree(self) -> dict[str, Any]:
        """The Project Browser, built from what is actually in the project: views, the SGEO model and SDRW drawings, levels, the model's
        families, element categories, sheets, the animation scene and its assets, and the generated reports."""
        views = self.manifest["views"]

        def node(kind: str, view: Mapping[str, Any]) -> dict[str, Any]:
            return {"id": view["id"], "label": view["name"], "view_kind": kind, "open": {"kind": kind if kind in ("3d", "schedule") else "drawing", "id": view["id"]}}
        groups = {"3d": [], "plan": [], "section": [], "elevation": [], "schedule": []}
        for v in views:
            groups.setdefault(v["kind"], []).append(node(v["kind"], v))
        elements: dict[str, list[dict[str, Any]]] = {c: [] for c in CATEGORIES}
        by_family: dict[str, list[dict[str, Any]]] = {}
        for oid in all_ids(self.model):
            obj = self.model.get(oid)
            cat = CATEGORY_KEY.get(str(obj["metadata"].get(f"{NS}:category", "")))
            if cat:
                item = {"id": oid, "label": obj["name"] or cat[:-1], "select": {"model": oid}}
                elements[cat].append(item)
                if "family_id" in obj:
                    by_family.setdefault(obj["family_id"], []).append(item)
        sheets = []
        for s in self.manifest["sheets"]:
            state = SH.status(self, s)
            sheets.append({"id": s["id"], "label": f"{s['number']} - {s['name']}" if s["name"] else s["number"], "open": {"kind": "sheet", "id": s["id"]},
                           "detail": "changed since issue" if state["changed"] else f"rev {s['revision']}", "flag": "changed" if state["changed"] else None})
        drawings = [{"id": f"drawing-{v['id']}", "label": f"{v['name']}.sdrw", "detail": f"{self.drawings[v['id']].count()} items",
                     "open": {"kind": "drawing", "id": v["id"]}} for v in views if v["id"] in self.drawings and v["kind"] != "3d"]
        spec = self.model.animation()
        tracks = (spec or {}).get("tracks", [])
        scenes = [{"id": "scene-animation", "label": "Animation", "detail": f"{float(spec['duration']):g} s" if spec else "none yet", "open": {"kind": "3d", "id": groups["3d"][0]["id"], "timeline": True}}] if groups["3d"] else []
        names = {t["object_id"]: self.model.get(t["object_id"])["name"] for t in tracks if E.alive(self, t["object_id"])}
        assets = [{"id": f"track-{oid}", "label": names[oid], "detail": f"{sum(1 for t in tracks if t['object_id'] == oid)} track(s)", "select": {"model": oid}}
                  for oid in dict.fromkeys(t["object_id"] for t in tracks) if oid in names]
        assets += [{"id": f"marker-{e['id']}", "label": e["name"], "detail": f"marker at {float(e['time']):g} s"} for e in (spec or {}).get("events", [])]
        model_counts = sum(len(v) for v in elements.values())
        return {"sections": [
            {"id": "views", "label": "Views", "children": [
                {"id": "views-3d", "label": "3D Views", "children": groups["3d"]},
                {"id": "views-plan", "label": "Floor Plans", "children": groups["plan"]},
                {"id": "views-section", "label": "Sections", "children": groups["section"]},
                {"id": "views-elevation", "label": "Elevations", "children": groups["elevation"]},
                {"id": "views-schedule", "label": "Schedules", "children": groups["schedule"]}]},
            {"id": "models", "label": "Models", "children": [{"id": "model-sgeo", "label": "model.sgeo", "detail": f"{model_counts} elements", "open": {"kind": "3d", "id": groups["3d"][0]["id"]}}] if groups["3d"] else []},
            {"id": "drawings", "label": "Drawings", "children": drawings},
            {"id": "levels", "label": "Levels", "children": [{"id": l["id"], "label": l["name"], "detail": f"{l['elevation']:g} m",
                                                              **({"open": {"kind": "drawing", "id": pv["id"]}} if (pv := DT.plan_view(self, l["id"])) else {})} for l in self.manifest["levels"]]},
            {"id": "families", "label": "Families", "children": [
                {"id": f"family-{f['id']}", "label": f["name"], "detail": f"{f.get('instance_count', 0)} placed", "dialog": "families", "ref": f["id"],
                 "children": by_family.get(f["id"], [])} for f in FM.families(self)]},
            {"id": "categories", "label": "Categories", "children": [{"id": f"cat-{c}", "label": c, "children": elements[c]} for c in CATEGORIES]},
            {"id": "sheets", "label": "Sheets", "children": sheets},
            {"id": "scenes", "label": "Scenes", "children": scenes},
            {"id": "animation-assets", "label": "Animation assets", "children": assets},
            {"id": "reports", "label": "Reports", "children": [{"id": f"report-{r['id']}", "label": r["name"], "detail": f"revision {r['revision']}", "report": r["id"]} for r in self.manifest["reports"]]}]}

    def drawing_data(self, view_id: str) -> dict[str, Any]:
        doc = self.drawing(view_id)
        entities = []
        for eid in all_ids(doc):
            e = doc.get(eid)
            item: dict[str, Any] = {"id": e["id"], "kind": e["kind"], "layer": e["layer"], "geometry": e["geometry"]}
            if e["kind"] == "dimension":
                item["primitives"] = doc.dimension_primitives(eid)
                item["text"] = doc.dimension_text(eid)
            elif e["kind"] == "hatch":
                item["strokes"] = hatch_strokes(e["geometry"])
            entities.append(item)
        overlay = self.plan_overlay(view_id)
        view = self.view(view_id)
        f = 1000.0 / MM_PER[self.manifest["unit"]]
        projection = D.project_view(self, view, f) if view["kind"] in ("section", "elevation") else None
        bounds = [list(b) for b in doc.bounds()] if entities else None
        if projection and projection["bounds"]:
            lo, hi = projection["bounds"]
            bounds = [[min(lo[0], bounds[0][0]), min(lo[1], bounds[0][1])], [max(hi[0], bounds[1][0]), max(hi[1], bounds[1][1])]] if bounds else [lo, hi]
        marks = D.section_marks(self, f) if view["kind"] == "plan" else []
        if overlay or marks:
            xs = [p[0] for w in overlay for p in w["polygon"]] + [q[0] for m in marks for q in (m["a"], m["b"])] + ([bounds[0][0], bounds[1][0]] if bounds else [])
            ys = [p[1] for w in overlay for p in w["polygon"]] + [q[1] for m in marks for q in (m["a"], m["b"])] + ([bounds[0][1], bounds[1][1]] if bounds else [])
            bounds = [[min(xs), min(ys)], [max(xs), max(ys)]]
        return {"view": self.view(view_id), "unit": doc.unit, "revision": self.revision, "doc_revision": doc.revision, "layers": doc.layers(),
                "entities": entities, "model": overlay, "bounds": bounds, "projection": projection,
                "section_marks": marks}

    def schedule(self, view_id: str) -> dict[str, Any]:
        view = self.view(view_id)
        if view["kind"] != "schedule":
            raise NotFound(f"'{view['name']}' is not a schedule")
        return {"view": view, "revision": self.revision, **D.schedule_data(self, view["category"])}

    def plan_overlay(self, view_id: str) -> list[dict[str, Any]]:
        """What the model contributes to a floor plan: walls (cut around their openings), doors and windows with their symbols, floors and
        beams on the view's level, in the drawing's unit. Read from the elements' own parameters each time, so the plan can never disagree
        with the 3D model."""
        view = self.view(view_id)
        if view["kind"] != "plan" or not view.get("level_id"):
            return []
        reference = view.get("cut_reference_level") or view["level_id"]
        cut_elevation = self.level(reference)["elevation"] + float(view.get("cut_offset", 1.5))
        base_elevation = self.level(view["level_id"])["elevation"]
        return E.plan_items(self, view["level_id"], 1000.0 / MM_PER[self.manifest["unit"]], cut_elevation=cut_elevation, show_opening=bool(view.get("show_opening_direction", True)),
                            show_hatching=bool(view.get("show_material_hatching", True)),
                            top_elevation=cut_elevation + float(view.get("range_top", 1.5)),
                            bottom_elevation=base_elevation + float(view.get("range_bottom", -0.2)),
                            depth_elevation=base_elevation + float(view.get("range_depth", -1.0)))

    def scene_data(self, max_vertices: int = 400_000, time: float | None = None) -> dict[str, Any]:
        """Every drawable object with world-space vertices. With ``time`` (seconds) the scene is the animation evaluated at that time
        by the SLAI Animation Agent; without it, the model as it is built."""
        if time is not None and self.model.animation() is not None:
            return self._animated_scene(max_vertices, float(time))
        objects, total = [], 0
        evaluated = D.evaluated_meshes(self)
        raw = self.model._model                                                # the native SGEO scene: one pass answers for every object
        matrices = raw.world_transforms()                                      # (asking per object re-validates the whole scene each time)
        for oid in all_ids(self.model, has_geometry=True):
            obj = self.model.get(oid)
            mesh = evaluated.get(oid) or self.model.tessellate(oid)
            t = matrices[oid]
            verts = [[t[0] * x + t[1] * y + t[2] * z + t[3], t[4] * x + t[5] * y + t[6] * z + t[7], t[8] * x + t[9] * y + t[10] * z + t[11]]
                     for x, y, z in mesh["vertices"]]
            total += len(verts)
            if total > max_vertices:
                raise LimitExceeded(f"The model has more than {max_vertices} vertices to draw", hint="Hide or simplify objects")
            meta = obj["metadata"]
            category = meta.get(f"{NS}:category", "generic")
            counts = E.m(meta, "layer_faces") or []
            layers = LY.stack(self, oid) if counts and category in LY.LAYERED else []
            layer_colors = ([{"count": int(n), "color": E.m(meta, "color") or lay.get("color") or
                             self.manifest.get("material_colors", {}).get(lay["material"]) or E.MATERIAL_COLORS.get(lay["material"])}
                            for n, lay in zip(counts, layers)] if len(counts) == len(layers) and sum(counts) == len(mesh["faces"]) else [])
            ridge = E.roof_ridge(meta) if category == "roof" else None
            ridge_handle = None
            if ridge:
                mid = [(ridge[0][i] + ridge[1][i]) / 2 for i in range(2)]
                across = E._rectangle([list(q) for q in E.m(meta, "boundary")])[2]
                z = E.level_elevation(self, E.m(meta, "level")) + float(E.m(meta, "offset", 0.0)) + E.roof_rise(meta)
                ridge_handle = {"point": [*mid, z], "across": list(across), "offset_mm": float(E.m(meta, "ridge_offset", 0.0)) * 1000}
            objects.append({"id": oid, "name": obj["name"], "category": category,
                            "material": meta.get(f"{NS}:material"), "color": E.appearance_color(self, meta), "vertices": verts, "faces": mesh["faces"],
                            "layer_colors": layer_colors,
                            "ridge_handle": ridge_handle,
                            "layer_points": self._layer_points(category, meta, verts, mesh["faces"])})
        # Derived geometry, not the source's unmodified bounding box,
        # defines camera fit and zoom extent.
        points = [vertex for item in objects for vertex in item["vertices"]]
        bounds = None
        if points:
            lo = [min(v[i] for v in points) for i in range(3)]
            hi = [max(v[i] for v in points) for i in range(3)]
            bounds = [lo, hi]
        return {"unit": "m", "revision": self.revision, "objects": objects, "bounds": bounds}

    @staticmethod
    def _layer_points(category: str, meta: Mapping[str, Any], verts: list[list[float]], faces: list[list[int]]) -> list[dict[str, Any]]:
        """World-space handles derived from real host geometry, never guessed screen offsets."""
        if not verts or category not in ("wall", "floor", "roof"):
            return []
        if category == "wall":
            a, b = meta.get(f"{NS}:start"), meta.get(f"{NS}:end")
            if not a or not b:
                return []
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = (dx * dx + dy * dy) ** 0.5
            if length < 1e-9:
                return []
            nx, ny = -dy / length, dx / length
            z = (min(v[2] for v in verts) + max(v[2] for v in verts)) / 2
            mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, z]
            thick = float(meta.get(f"{NS}:thickness", 0.2))
            return [{"side": side, "point": [mid[0] + sign * nx * (thick / 2 + 0.05), mid[1] + sign * ny * (thick / 2 + 0.05), z]}
                    for side, sign in (("left", 1), ("right", -1))]
        # The largest outward-facing surface on each side is a stable place for a
        # layer control, including pitched roofs. Its offset follows its normal.
        best: dict[str, tuple[float, list[float]]] = {}
        names = ("top", "bottom") if category == "floor" else ("outside", "inside")
        for face in faces:
            if len(face) < 3:
                continue
            try:
                a, b, c = (verts[face[i]] for i in range(3))
            except (IndexError, TypeError):
                continue
            u, v = [b[i] - a[i] for i in range(3)], [c[i] - a[i] for i in range(3)]
            n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]]
            area = sum(x * x for x in n) ** 0.5
            if area < 1e-9 or abs(n[2]) < area * 0.2:
                continue
            side = names[0 if n[2] > 0 else 1]
            point = [sum(p[i] for p in (a, b, c)) / 3 + n[i] * 0.05 / area for i in range(3)]
            if side not in best or area > best[side][0]:
                best[side] = (area, point)
        return [{"side": side, "point": best[side][1]} for side in names if side in best]

    def _animated_scene(self, max_vertices: int, time: float) -> dict[str, Any]:
        from src.utils.spatial_formats.sgeo.tessellation import tessellate
        evaluated = AB.BRIDGE.evaluate(self.model, time)
        objects, total = [], 0
        for oid in evaluated.geometric_ids():                                    # visible objects that have geometry, at this time
            state = evaluated.get_object_state(oid)
            obj, t = state["object"], state["world_transform"]
            mesh = tessellate(obj["geometry"])
            verts = [[t[0] * x + t[1] * y + t[2] * z + t[3], t[4] * x + t[5] * y + t[6] * z + t[7], t[8] * x + t[9] * y + t[10] * z + t[11]]
                     for x, y, z in mesh["vertices"]]
            total += len(verts)
            if total > max_vertices:
                raise LimitExceeded(f"The model has more than {max_vertices} vertices to draw", hint="Hide or simplify objects")
            meta = obj["metadata"]
            objects.append({"id": oid, "name": obj["name"], "category": meta.get(f"{NS}:category", "generic"),
                            "material": meta.get(f"{NS}:material"), "color": E.appearance_color(self, meta), "vertices": verts, "faces": mesh["faces"]})
        bounds = None
        if objects:
            lo = [min(v[i] for o in objects for v in o["vertices"]) for i in range(3)]
            hi = [max(v[i] for o in objects for v in o["vertices"]) for i in range(3)]
            bounds = [lo, hi]
        return {"unit": "m", "revision": self.revision, "objects": objects, "bounds": bounds, "time": evaluated.time}

    # -- animation: reading ------------------------------------------------------------------------------------------------
    def animation_settings(self) -> dict[str, Any]:
        """Playback settings kept with the project (SGEO's animation document has no frame rate or loop flag). ``duration`` is the length a
        new animation starts with; once an animation exists its own duration is the one in the SGEO scene."""
        s = self.manifest.get("animation") or {}
        spec = self.model.animation()
        return {"fps": int(s.get("fps", AA.DEFAULT_FPS)), "loop": bool(s.get("loop", False)),
                "duration": float(spec["duration"]) if spec else float(s.get("duration", 5.0))}

    def animation_data(self) -> dict[str, Any]:
        """The model's animation as stored in its SGEO scene (``animation``), the same tracks in the units the UI works in (``lanes``), the
        markers, the settings, and what the SLAI Animation Agent can do."""
        status = AB.BRIDGE.status()
        animation = self.model.animation()
        tracks = (animation or {}).get("tracks", [])
        names = {o: self.model.get(o)["name"] for o in {t["object_id"] for t in tracks} if E.alive(self, o)}
        lanes = []
        for t in tracks:
            ui = AA.ui_property(t["property"])
            lanes.append({"object_id": t["object_id"], "property": ui or t["property"], "editable": ui is not None,
                          "interpolation": AA.interpolation_name(t), "keys": [{"time": k["time"], "value": AA.ui_value(ui, k["value"]) if ui else k["value"]} for k in t["keyframes"]]})
        markers = [{"id": e["id"], "name": e["name"], "time": e["time"], "object_id": e.get("object_id")} for e in (animation or {}).get("events", [])]
        return {"agent": status, "animation": animation, "names": names, "lanes": lanes, "markers": markers, "settings": self.animation_settings(),
                "properties": list(AA.PROPERTIES), "interpolations": list(AA.INTERPOLATIONS), "presets": AA.PRESETS, "revision": self.revision}

    def animation_pose(self, oid: str, seconds: float) -> dict[str, Any]:
        """What the element looks like at ``seconds``: the animated part of the pose the Animation Agent evaluates, relative to the element as
        built, and which properties have a key exactly at that frame."""
        if not E.alive(self, oid):
            raise NotFound("That element does not exist")
        settings = self.animation_settings()
        spec = self.model.animation()
        duration = float(spec["duration"]) if spec else settings["duration"]
        t = AA.frame_time(seconds, settings["fps"], duration)
        pose: dict[str, Any] = {"translation": [0.0, 0.0, 0.0], "turn": 0.0, "scale": [1.0, 1.0, 1.0], "visibility": True}
        keyed: list[str] = []
        animated: list[str] = []
        if spec is not None:
            for track in spec["tracks"]:
                if track["object_id"] != oid:
                    continue
                ui = AA.ui_property(track["property"])
                if ui is None:
                    continue
                animated.append(ui)
                if any(abs(k["time"] - t) < 1e-9 for k in track["keyframes"]):
                    keyed.append(ui)
            if animated:
                state = AB.BRIDGE.evaluate(self.model, t).get_object_state(oid)
                decoded = AA.decode_pose(self.model.world_transform(oid), state["world_transform"])
                pose = {"translation": decoded["translation"], "scale": decoded["scale"], "visibility": bool(state["visible"]),
                        "turn": AA.unwrap_turn(decoded["turn"], spec, oid, t)}
        return {"id": oid, "name": self.model.get(oid)["name"], "time": t, "pose": pose, "keyed": keyed, "animated": animated, "revision": self.revision}

    def _animatable(self, oid: Any) -> str:
        oid = text(oid, "id", 80)
        if oid not in set(all_ids(self.model, has_geometry=True)):
            raise BadRequest("Only elements with geometry can be animated")
        return oid

    def _authoring_spec(self, create: bool) -> tuple[dict[str, Any], int]:
        """The animation as an editable copy, and the frame rate. ``create`` starts a new animation of the settings' length if there is none."""
        settings = self.animation_settings()
        spec = self.model.animation()
        if spec is None and not create:
            raise NotFound("The model has no animation yet")
        return AA.clean_spec(spec, settings["duration"]), settings["fps"]

    def forget_animation(self, going: set[str]) -> None:
        """Take the tracks and events of elements that are about to be deleted out of the animation (the model refuses an animation of something that is not there)."""
        spec = self.model.animation()
        if spec is not None and (any(t["object_id"] in going for t in spec["tracks"]) or any(e.get("object_id") in going for e in spec.get("events", []))):
            spec = AA.clean_spec(spec)
            spec["tracks"] = [t for t in spec["tracks"] if t["object_id"] not in going]
            if spec.get("events"):
                spec["events"] = [e for e in spec["events"] if e.get("object_id") not in going]
            self._commit_animation(spec)

    def _commit_animation(self, spec: dict[str, Any]) -> None:
        if not AB.BRIDGE.available:
            raise BadRequest("The SLAI Animation Agent is not available, so the animation cannot be edited", details={"reason": AB.BRIDGE.reason})
        AB.BRIDGE.request(self.model, "set_animation_v2", {"animation": spec})

    def analyze_beam(self, oid: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Beam response from the SLAI STEM Agent for one beam and a load the person gives. Read-only."""
        return ST.analyze_beam(self, oid, payload)

    def project_properties(self) -> dict[str, Any]:
        """What the project is: identity, units, counts, the files it saves and their recorded hashes, and its backups. Read-only."""
        counts: dict[str, int] = {}
        for oid in all_ids(self.model):
            c = E.category(self, oid)
            counts[c] = counts.get(c, 0) + 1
        files = {}
        for rel, info in (self.manifest.get("files") or {}).items():
            size = None
            if self.root is not None:
                try:
                    size = (self.root / rel).stat().st_size
                except OSError:
                    size = None
            files[rel] = {"sha256": info.get("sha256"), "revision": info.get("revision"), "bytes": size}
        return {"id": self.manifest["id"], "name": self.name, "description": str(self.manifest.get("description", "")), "unit": self.manifest["unit"],
                "format": f"{self.manifest['format']} v{self.manifest['version']}", "created": self.manifest.get("created"), "modified": self.manifest.get("modified"),
                "path": str(self.root) if self.root else None, "revision": self.revision, "dirty": self.dirty,
                "counts": {"levels": len(self.manifest["levels"]), "views": len(self.manifest["views"]), "sheets": len(self.manifest["sheets"]), "elements": counts},
                "files": files, "backups": Project.list_backups(self.root), "backups_kept": self.backups_kept, "model_unit": "m"}

    def quantities(self, ids: Any = None, densities: Any = None) -> dict[str, Any]:
        """Volume, area, size, mass and centre of the given elements (every element when none are given). Read-only."""
        if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, str) for i in ids)):
            raise BadRequest("ids must be a list of element ids")
        if densities is not None and not isinstance(densities, dict):
            raise BadRequest("densities must map a material name to kg/m³")
        return Q.take_off(self, ids or None, densities)

    def drainage(self, roof: Any = None) -> dict[str, Any]:
        """The rain each roof sends to its gutters and downpipes. Read-only."""
        if roof is not None and not isinstance(roof, str):
            raise BadRequest("roof must be an element id")
        return GU.drainage(self, roof or None)

    def trace_drainage(self, gutter: Any, offset: Any = 0.0) -> dict[str, Any]:
        """Where the rain that falls on a gutter, ``offset`` metres along it, goes: down the run to its low point and out of the downpipe there. Read-only."""
        if not isinstance(gutter, str):
            raise BadRequest("gutter must be an element id")
        if isinstance(offset, bool) or not isinstance(offset, (int, float)):
            raise BadRequest("offset must be a number of metres")
        if not E.alive(self, gutter) or E.category(self, gutter) != "gutter":
            raise NotFound(f"Gutter {gutter} does not exist")
        return GU.trace(self, gutter, float(offset))

    def foundation_report(self, oid: Any) -> dict[str, Any]:
        """Quantities, build-up, thermal resistance and indicative bars of one foundation. Read-only; nothing is checked structurally."""
        if not isinstance(oid, str):
            raise BadRequest("id must be an element id")
        if not E.alive(self, oid) or E.category(self, oid) != "foundation":
            raise NotFound(f"Foundation {oid} does not exist")
        return {"id": oid, **FD.quantities(self, oid)}

    def cutting_report(self, void: Any) -> dict[str, Any]:
        """What a void does to every element it meets (cut, not supported, or left alone by its policy), with the reasons. Read-only."""
        if not isinstance(void, str):
            raise BadRequest("void must be an element id")
        if not E.alive(self, void) or E.category(self, void) != "void":
            raise NotFound(f"Void {void} does not exist")
        v = next(x for x in FX.voids(self) if x["id"] == void)
        return {"void": void, "settings": {"enabled": v["enabled"], "policy": v["policy"], "targets": v["targets"], "host": v["host"]},
                "elements": CU.classify(self, void), "missing": CU.stale(self, v)}

    def preview_gutters(self, params: Mapping[str, Any]) -> dict[str, Any]:
        """What ``model.add_gutters`` would make for the same parameters, as plan outlines, without making it. Read-only."""
        return GU.preview_gutters(self, params, MM_PER[self.manifest["unit"]] / 1000.0)

    def properties(self, ref: Mapping[str, Any], relations: bool = False) -> dict[str, Any]:
        """The Properties panel for a selection: ``{"model": id}``, ``{"drawing": view_id, "entity": id}`` or ``{"sheet": id}``. ``relations``
        adds the links from a model element to what it is connected to (the panel asks for them; the assistant and the calculator do not)."""
        if "model" in ref:
            props = E.properties(self, str(ref["model"]), relations)
            modifiers = [m for m in self.model.modifiers()
                         if m["target"]["item_id"] == str(ref["model"])]
            if modifiers:
                rows = []
                for item in modifiers:
                    mid = item["id"]
                    name = item["kind"].split(".", 1)[-1].title()
                    rows.append({"label": name + " · " + mid[:8],
                                 "type": "readonly", "value": "Non-destructive SGEO modifier"})
                    for field, value in item["parameters"].items():
                        if isinstance(value, (int, float)) and not isinstance(value, bool):
                            rows.append({"key": f"modifier:{mid}:{field}",
                                         "label": f"{name} · {field.replace('_', ' ').title()}",
                                         "type": "number", "value": (
                                             round(math.degrees(value), 6) if field == "angle" else value),
                                         "unit": "°" if field == "angle" else
                                                 ("m" if field in ("amount", "depth", "start", "end", "max_edge") else "")})
                        elif isinstance(value, str):
                            choices = {"side": ("positive", "negative"),
                                       "axis": ("x", "y", "z"),
                                       "operation": ("union", "subtract", "intersect")}.get(field)
                            if choices:
                                rows.append({"key": f"modifier:{mid}:{field}",
                                             "label": f"{name} · {field.title()}",
                                             "type": "choice", "value": value,
                                             "choices": [{"value": v, "label": v.title()} for v in choices]})
                        elif isinstance(value, list):
                            for n, part in enumerate(value):
                                rows.append({"key": f"modifier:{mid}:{field}:{n}",
                                             "label": f"{name} · {field.title()} {n + 1}",
                                             "type": "number", "value": part})
                props.setdefault("groups", []).append({"title": "Native modifiers", "rows": rows})
            return props
        if "level" in ref:
            return _level_properties(self, str(ref["level"]))
        if "entity" in ref:
            return _entity_properties(self, str(ref.get("drawing")), str(ref["entity"]))
        if "sheet" in ref:
            return _sheet_properties(self, str(ref["sheet"]))
        if "view" in ref:
            return _view_properties(self, str(ref["view"]))
        raise BadRequest("Select a model element, a drawing entity, a sheet or a view")

    def layer_preview(self, oid: str, layer: str, mode: str, opening_ids: Any = ()) -> dict[str, Any]:
        """What a layer would be cut by if its openings were set to ``mode`` (and these ids): nothing is changed. Read-only."""
        if not E.alive(self, oid):
            raise NotFound(f"Element {oid} does not exist")
        return LY.preview(self, oid, layer, mode, [str(i) for i in (opening_ids or ())])

    def sheet_data(self, sheet_id: str) -> dict[str, Any]:
        """Everything needed to draw a sheet: its paper, title block fields and each viewport with the drawing it shows."""
        sheet = SH.find(self, sheet_id)
        width, height = paper_size(sheet["paper"])
        viewports = []
        f = 1000.0 / MM_PER[self.manifest["unit"]]
        for vp in sheet.get("viewports", []):
            kind = SV.kind_of(vp)
            # A deleted/missing view remains a model-check finding, not a reason
            # the rest of the sheet becomes impossible to display or export.
            if kind == "snapshot" and vp["id"] not in self.snapshots:
                continue
            if kind == "report" and not any(r["id"] == vp["report_id"] for r in self.manifest["reports"]):
                continue
            if kind not in ("snapshot", "report") and not any(v["id"] == vp.get("view_id") for v in self.manifest["views"]):
                continue
            placement = {"bounds_mm": list(SV.rect_of(self, vp))}
            if kind == "snapshot":
                if vp["id"] in self.snapshots:
                    w, h = SV.snapshot_size(vp)
                    viewports.append({**vp, **placement, "image": SV.snapshot_data(self, vp), "paper_width": w, "paper_height": h, "view": {"name": vp.get("title") or "3D snapshot"}})
                continue
            if kind == "report":
                if any(r["id"] == vp["report_id"] for r in self.manifest["reports"]):
                    viewports.append({**vp, **placement, "report": SV.report_box(self, vp), "view": {"name": vp.get("title") or "Report"}})
                continue
            view = next((v for v in self.manifest["views"] if v["id"] == vp["view_id"]), None)
            if view is not None and kind == "model":
                viewports.append({**vp, **placement, "view": view, "mm_per_unit": MM_PER[self.manifest["unit"]], "unit": self.manifest["unit"], "layers": [], "entities": [], "model": [],
                                  "projection": None, "section_marks": [], "solid": SV.model_view(self, vp["azimuth"], vp["elevation"], f)})
                continue
            if view is not None and view["kind"] == "schedule":
                viewports.append({**vp, **placement, "view": view, "schedule": D.schedule_data(self, view["category"])})
                continue
            if vp["view_id"] not in self.drawings or view is None:
                continue                                              # reported by check(); there is nothing to draw
            data = self.drawing_data(vp["view_id"])
            viewports.append({**vp, **placement, "view": data["view"], "unit": data["unit"], "mm_per_unit": MM_PER[data["unit"]], "layers": data["layers"], "entities": data["entities"],
                              "model": data["model"], "projection": data["projection"], "section_marks": data["section_marks"]})
        return {"id": sheet["id"], "number": sheet["number"], "name": sheet["name"], "paper": sheet["paper"], "scale": sheet["scale"], "revision": sheet["revision"],
                "width": width, "height": height, "project": self.name, "viewports": viewports, "revision_number": self.revision,
                "title_block": SH.title_block(self, sheet), "notes": sheet["notes"], "revisions": sheet["revisions"], "status": SH.status(self, sheet)}

    def sync_status(self) -> dict[str, Any]:
        """How the documentation stands against the model: each sheet's state since its last issue, and the tags that lost their element."""
        sheets = []
        for sheet in self.manifest["sheets"]:
            st = SH.status(self, SH.find(self, sheet["id"]))
            sheets.append({"id": sheet["id"], "number": sheet["number"], "name": sheet["name"], "state": st["state"], "revision": st["revision"], "changed": st["changed"]})
        tags = AN.all_tags(self)
        return {"model_revision": self.revision, "sheets": sheets, "tags": len(tags), "detached_tags": [t for t in tags if t["detached"]],
                "stale_sheets": [s["number"] for s in sheets if s["state"] == "changed"]}

    def check(self) -> dict[str, Any]:
        """Deterministic consistency checks over real project data: references, hosting, parameters, associations, geometry candidates and
        documentation. Nothing here is a regulatory or structural verification."""
        issues: list[dict[str, str]] = []
        level_ids = {l["id"] for l in self.manifest["levels"]}
        view_ids = {v["id"] for v in self.manifest["views"]}
        issues.extend(E.check(self))
        issues.extend(CK.coordination(self))
        issues.extend(BD.issues(self))
        issues.extend(RQ.issues(self))
        for view in self.manifest["views"]:
            if view["kind"] not in NO_DRAWING and view["id"] not in self.drawings:
                issues.append(E.issue("error", view["id"], f"View '{view['name']}' has lost its drawing", "references"))
            if view.get("level_id") and view["level_id"] not in level_ids:
                issues.append(E.issue("error", view["id"], f"View '{view['name']}' refers to a missing level", "references"))
        for sheet in self.manifest["sheets"]:
            for vp in sheet.get("viewports", []):
                kind = SV.kind_of(vp)
                if kind == "snapshot":
                    if vp["id"] not in self.snapshots:
                        issues.append(E.issue("error", sheet["id"], f"Sheet {sheet['number']} has a 3D snapshot whose picture is missing", "references"))
                elif kind == "report":
                    if not any(r["id"] == vp["report_id"] for r in self.manifest["reports"]):
                        issues.append(E.issue("error", sheet["id"], f"Sheet {sheet['number']} places a report that no longer exists", "references"))
                elif vp.get("view_id") not in view_ids:
                    issues.append(E.issue("error", sheet["id"], f"Sheet {sheet['number']} places a view that no longer exists", "references"))
        issues.extend(SH.issues_of(self))
        for label, doc in [("Model", self.model)] + [(v["name"], self.drawings[v["id"]]) for v in self.manifest["views"] if v["id"] in self.drawings]:       # a deleted view's drawing is only kept for undo
            report = doc.validate()
            for item in getattr(report, "errors", []) or []:
                issues.append(E.issue("error", label, f"{label}: {getattr(item, 'message', item)}", "documentation"))
        return {"revision": self.revision, "issues": issues, "ok": not any(i["severity"] == "error" for i in issues),
                "counts": {sev: sum(1 for i in issues if i["severity"] == sev) for sev in ("error", "warning", "info")},
                "scope": "Reference, hosting, parameter, association and document-validity checks, plus bounding-box clash candidates; this is not an engineering or code-compliance check.",
                "limitations": CK.LIMITATIONS}


def hatch_strokes(geometry: Mapping[str, Any]) -> list[list[list[float]]]:
    """The straight strokes of a lines/cross hatch, clipped to its outline, as [[x1, y1], [x2, y2]] pairs. A solid hatch has none.
    The clipping is the SDRW library's own (the one its SVG export uses), so every outline kind it supports is hatched and nothing is duplicated here."""
    try:
        return [[list(a), list(b)] for a, b in hatch_lines(geometry)]
    except ResourceLimitError as exc:
        raise BadRequest("That spacing would draw too many hatch lines for this outline; use a larger spacing") from exc


all_ids = E.all_ids


def _clone(value: Any) -> Any:
    import copy
    return copy.deepcopy(value)


# ------------------------------------------------------------------------------------------------------------ wall regeneration
regenerate_wall = E.regenerate_wall


def _entity_properties(project: Project, view_id: str, eid: str) -> dict[str, Any]:
    doc = project.drawing(view_id)
    try:
        e = doc.get(eid)
        m = doc.measure(eid)
    except SpatialFormatError as exc:
        raise NotFound(f"Entity {eid} does not exist in this drawing") from exc
    unit, g = doc.unit, e["geometry"]
    rows: list[dict[str, Any]] = []
    if e["kind"] == "line":
        (sx, sy), (ex, ey) = g["start"], g["end"]
        rows = [{"label": "Start Point", "type": "readonly", "value": f"X: {sx:,.0f} {unit}  Y: {sy:,.0f} {unit}"},
                {"label": "End Point", "type": "readonly", "value": f"X: {ex:,.0f} {unit}  Y: {ey:,.0f} {unit}"},
                {"label": "Length", "type": "readonly", "value": f"{m['length']:,.1f} {unit}"},
                {"label": "Angle", "type": "readonly", "value": f"{math.degrees(math.atan2(ey - sy, ex - sx)):.2f}°"}]
    elif e["kind"] == "circle":
        rows = [{"label": "Center", "type": "readonly", "value": f"X: {g['center'][0]:,.0f} {unit}  Y: {g['center'][1]:,.0f} {unit}"},
                {"label": "Radius", "type": "readonly", "value": f"{g['radius']:,.1f} {unit}"},
                {"label": "Area", "type": "readonly", "value": f"{m['area']:,.0f} {unit}²"}]
    else:
        if m.get("length") is not None:
            rows.append({"label": "Length", "type": "readonly", "value": f"{m['length']:,.1f} {unit}"})
        if m.get("area") is not None:
            rows.append({"label": "Area", "type": "readonly", "value": f"{m['area']:,.0f} {unit}²"})
    layers = [{"value": l["name"], "label": l["name"]} for l in doc.layers()]
    groups: list[dict[str, Any]] = [{"title": "Geometry", "rows": rows}]
    tagged = (e.get("metadata") or {}).get(AN.KEY_OF)
    if tagged:                                                  # a tag is its element's data on the drawing: follow it back, or change what it shows
        alive = E.alive(project, tagged)
        md = e["metadata"]
        groups.append({"title": "Tag", "rows": [
            {"label": "Tags", "type": "link", "value": project.model.get(tagged)["name"] or tagged, "target": {"select": {"model": tagged}}} if alive
            else {"label": "Tags", "type": "readonly", "value": "(deleted)"},
            {"key": "tag_field", "label": "Shows", "type": "choice", "value": md.get(AN.KEY_FIELD), "choices": [{"value": k, "label": v} for k, v in AN.FIELDS.items()]} if alive
            else {"label": "Status", "type": "readonly", "value": "The element was deleted; the tag keeps its last text"}]})
    return {"ref": {"drawing": view_id, "entity": eid}, "title": "Tag" if tagged else e["kind"].title(), "category": "tag" if tagged else e["kind"], "kind": "entity",
            "top": [{"key": "layer", "label": "Layer", "type": "choice", "value": e["layer"], "choices": layers}],
            "groups": groups}


VIEW_OPTIONS = {"opening_direction": "show_opening_direction", "material_hatching": "show_material_hatching"}


def _editable_view(project: Project, vid: Any) -> dict[str, Any]:
    view = project.view(text(vid, "id", 80))
    if view["kind"] not in ("section", "elevation", "schedule"):
        raise BadRequest(f"'{view['name']}' is a {view['kind']} view; it is made from the levels and cannot be edited or deleted here")
    return view


def _unique_view_name(project: Project, name: str, ignore: str | None = None) -> None:
    if any(v["name"].lower() == name.lower() and v["id"] != ignore for v in project.manifest["views"]):
        raise Conflict(f"A view named '{name}' already exists", hint="Choose another name")


def _next_section_name(project: Project) -> str:
    taken = {v["name"] for v in project.manifest["views"]}
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if f"Section {letter}-{letter}" not in taken:
            return f"Section {letter}-{letter}"
    n = 1
    while f"Section {n}" in taken:
        n += 1
    return f"Section {n}"


def _view_properties(project: Project, vid: str) -> dict[str, Any]:
    view = project.view(vid)
    kind = view["kind"]
    label = {"section": "Section", "elevation": "Elevation", "schedule": "Schedule", "plan": "Floor Plan", "3d": "3D View"}[kind]
    top: list[dict[str, Any]] = [{"label": "Type", "type": "readonly", "value": label}]
    top.append({"key": "name", "label": "Name", "type": "text", "value": view["name"]} if kind in ("section", "elevation", "schedule")
               else {"label": "Name", "type": "readonly", "value": view["name"]})
    groups: list[dict[str, Any]] = []
    if kind == "plan":
        reference = view.get("cut_reference_level") or view["level_id"]
        offset = float(view.get("cut_offset", 1.5))
        groups.append({"title": "Plan cut plane", "rows": [
            {"key": "cut_reference", "label": "Height reference", "type": "choice", "value": reference,
             "choices": [{"value": level["id"], "label": level["name"]} for level in project.manifest["levels"]]},
            {"key": "cut_offset", "label": "Offset", "type": "number", "unit": "mm", "value": offset * 1000},
            {"label": "Absolute cut height", "type": "readonly", "value": f"{(project.level(reference)['elevation'] + offset) * 1000:g} mm"}]})
        groups.append({"title": "View range", "rows": [
            {"key": "range_top", "label": "Above cut plane", "type": "number", "unit": "mm", "value": float(view.get("range_top", 1.5)) * 1000},
            {"key": "range_bottom", "label": "Bottom from plan level", "type": "number", "unit": "mm", "value": float(view.get("range_bottom", -0.2)) * 1000},
            {"key": "range_depth", "label": "Depth from plan level", "type": "number", "unit": "mm", "value": float(view.get("range_depth", -1.0)) * 1000}]})
    if kind == "section":
        fr = D.frame_of(view)
        here, there = D.compass(fr.fx, fr.fy), D.compass(-fr.fx, -fr.fy)
        mm = lambda v: f"{v * 1000:,.0f} mm"                                                  # noqa: E731
        groups.append({"title": "Cut line", "rows": [
            {"key": "looking", "label": "Looking", "type": "choice", "value": "current",
             "choices": [{"value": "current", "label": f"Toward {here}"}, {"value": "flip", "label": f"Toward {there}"}]},
            {"key": "depth", "label": "Depth beyond cut", "type": "number", "unit": "m", "value": float(view.get("depth", D.SECTION_DEPTH))},
            {"key": "angle", "label": "Section angle", "type": "number", "unit": "°",
             "value": round(math.degrees(math.atan2(view["b"][1] - view["a"][1],
                                                     view["b"][0] - view["a"][0])), 6)},
            {"label": "Start", "type": "readonly", "value": f"X: {mm(view['a'][0])}  Y: {mm(view['a'][1])}"},
            {"label": "End", "type": "readonly", "value": f"X: {mm(view['b'][0])}  Y: {mm(view['b'][1])}"},
            {"label": "Length", "type": "readonly", "value": f"{fr.span:.3f} m"}]})
    elif kind == "elevation":
        groups.append({"title": "View", "rows": [{"label": "Looking", "type": "readonly", "value": f"toward {D.compass(*D.SIDES[view['side']])}, from the {view['side']}"}]})
    elif kind == "schedule":
        groups.append({"title": "Schedule", "rows": [{"label": "Lists", "type": "readonly", "value": view["category"] + "s"},
                                                      {"label": "Rows", "type": "readonly", "value": str(D.schedule_data(project, view["category"])["count"])}]})
    if kind in ("plan", "section", "elevation"):
        shown = [{"value": "shown", "label": "Shown"}, {"value": "hidden", "label": "Hidden"}]
        groups.append({"title": "Annotation", "rows": [
            {"key": "opening_direction", "label": "Opening direction", "type": "choice", "value": "shown" if view.get("show_opening_direction", True) else "hidden", "choices": shown},
            {"key": "material_hatching", "label": "Material hatching", "type": "choice", "value": "shown" if view.get("show_material_hatching", True) else "hidden", "choices": shown}]})
        patterns = [{"value": pid, "label": spec["label"]} for pid, spec in D.HATCH_PATTERNS.items()]
        groups.append({"title": "Cut hatching (all views)", "rows": [
            {"key": f"hatch:{material}", "label": material, "type": "choice", "value": (D.hatch_pattern(project, material) or {}).get("pattern", "none"), "choices": patterns}
            for material in D.DEFAULT_HATCH] + [{"label": "Note", "type": "readonly", "value": D.HATCH_NOTE}]})
    return {"ref": {"view": vid}, "title": view["name"], "category": kind, "kind": "view", "top": top, "groups": groups}


def _level_properties(project: Project, level_id: str) -> dict[str, Any]:
    """Expose existing level constraints as editable, derived Properties rows."""
    level = project.level(level_id)
    height = DT.height_to_next(project, level_id)
    rows = [{"key": "elevation", "label": "Absolute elevation", "type": "number",
             "unit": "m", "value": level["elevation"]}]
    if height is not None:
        rows.append({"key": "height", "label": "Height to next level", "type": "number",
                     "unit": "m", "value": round(height, 6)})
    else:
        rows.append({"label": "Height to next level", "type": "readonly", "value": "Topmost level"})
    return {"ref": {"level": level_id}, "kind": "level", "category": "level",
            "title": level["name"], "top": [
            {"key": "name", "label": "Name", "type": "text", "value": level["name"]},
            {"key": "description", "label": "Description", "type": "multiline", "value": level.get("description", "")}],
            "groups": [{"title": "Level constraints", "rows": rows}]}


def _sheet_properties(project: Project, sid: str) -> dict[str, Any]:
    sheet = SH.find(project, sid)
    state = SH.status(project, sheet)
    tb = sheet["titleblock"]
    text_row = lambda key, label, value: {"key": key, "label": label, "type": "text", "value": value}          # noqa: E731
    revision = ({"label": "Revision", "type": "readonly", "value": f"{sheet['revision']} (issued {sheet['revisions'][-1]['date']})"} if sheet["revisions"]
                else text_row("revision", "Revision", sheet["revision"]))
    status_text = {"not issued": "Not issued yet", "current": f"Unchanged since revision {state['revision']}",
                   "changed": f"Changed since revision {state['revision']}: " + ", ".join(state["changed"])}[state["state"]]
    return {"ref": {"sheet": sid}, "title": f"Sheet {sheet['number']}", "category": "sheet", "kind": "sheet",
            "top": [text_row("number", "Sheet Number", sheet["number"]), text_row("name", "Sheet Name", sheet["name"]),
                    {"key": "paper", "label": "Paper Size", "type": "choice", "value": sheet["paper"], "choices": [{"value": p, "label": p} for p in PAPERS]
                     + ([{"value": sheet["paper"], "label": sheet["paper"]}] if sheet["paper"].startswith("Custom ") else [])
                     + [{"value": "custom", "label": "Custom dimensions…"}]},
                    {"key": "scale", "label": "Scale", "type": "choice", "value": sheet["scale"], "choices": [{"value": s, "label": s} for s in SCALES]},
                    revision],
            "groups": [
                {"title": "Title block", "rows": [
                    {"label": "Project", "type": "readonly", "value": project.name},
                    {"label": "Client", "type": "readonly", "value": str(project.manifest.get("client", "")) or "(set in Project properties)"},
                    text_row("drawn_by", "Drawn by", tb["drawn_by"]), text_row("checked_by", "Checked by", tb["checked_by"]), text_row("date", "Date (YYYY-MM-DD)", tb["date"]),
                    {"key": "status", "label": "Status", "type": "choice", "value": tb["status"], "choices": [{"value": s, "label": s} for s in SH.STATUSES]}]},
                {"title": "Revisions", "rows": [{"label": r["rev"], "type": "readonly", "value": f"{r['date']}  {r['description']}" + (f"  ({r['by']})" if r["by"] else "")}
                                                  for r in reversed(sheet["revisions"])] or [{"label": "Issued", "type": "readonly", "value": "none yet"}],
                 "action": {"label": "Issue revision…", "dialog": "issue"}},
                {"title": "Status", "rows": [{"label": "Compared with the last issue", "type": "readonly", "value": status_text}]},
                {"title": f"Viewports ({len(sheet.get('viewports', []))})", "rows": [
                    row for v in sheet.get("viewports", []) for row in _viewport_rows(project, v)],
                 **({"action": {"label": "Arrange viewports", "dialog": "arrange"}} if sheet.get("viewports") else {})},
                {"title": f"Notes ({len(sheet['notes'])})", "rows": [{"key": f"note:{n['id']}", "label": f"({n['x']:g}, {n['y']:g})", "type": "text", "value": n["text"], "removable": True}
                                                                    for n in sheet["notes"]], "add": "note"}]}


PAPERS = tuple(f"A{i} {orientation}" for i in range(5) for orientation in ("Landscape", "Portrait"))
SCALES = ("1:1", "1:2", "1:5", "1:10", "1:20", "1:50", "1:100", "1:200", "1:500")
PAPER_MM = {"A0": (841.0, 1189.0), "A1": (594.0, 841.0), "A2": (420.0, 594.0), "A3": (297.0, 420.0), "A4": (210.0, 297.0)}


def paper_size(paper: str) -> tuple[float, float]:
    """Width and height in mm of a paper name such as 'A1 Landscape'."""
    custom = re.fullmatch(r"Custom ([0-9]+(?:\.[0-9]+)?)x([0-9]+(?:\.[0-9]+)?) mm", paper)
    if custom:
        return float(custom.group(1)), float(custom.group(2))
    size, _, orientation = paper.partition(" ")
    if size not in PAPER_MM or orientation not in ("Portrait", "Landscape"):
        raise BadRequest("Unknown sheet format", hint="Select an A0–A4 paper size or enter valid custom dimensions")
    short, long_ = sorted(PAPER_MM[size])
    return (long_, short) if orientation == "Landscape" else (short, long_)


# --------------------------------------------------------------------------------------------------------------------- commands
def command(target: Callable[[Project, dict[str, Any]], tuple[str, str]]) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
        fn.target = target                     # type: ignore[attr-defined]
        return fn
    return wrap


def _drawing_target(label: str) -> Callable[[Project, dict[str, Any]], tuple[str, str]]:
    def target(project: Project, p: dict[str, Any]) -> tuple[str, str]:
        view = text(p.get("drawing"), "drawing", 80)
        project.drawing(view)
        return f"drawing:{view}", label
    return target


def _model_target(label: str) -> Callable[[Project, dict[str, Any]], tuple[str, str]]:
    return lambda project, p: ("model", label)


def _layer_opts(doc: SDRWDocument, p: Mapping[str, Any]) -> dict[str, Any]:
    layer = p.get("layer")
    if layer is None:
        return {}
    layer = text(layer, "layer", 64)
    return {"layer": layer, "create_layer": layer not in {l["name"] for l in doc.layers()}}


class _Commands:
    """Every way the project can change. Names use ``__`` for ``.`` (``draw__line`` is the command ``draw.line``)."""

    # -- 2D ----------------------------------------------------------------------------------------------------------------
    @staticmethod
    @command(_drawing_target("Draw line"))
    def draw__line(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        doc = project.drawing(p["drawing"])
        a, b = _point(p.get("start"), "start"), _point(p.get("end"), "end")
        if a == b:
            raise BadRequest("A line needs two different points")
        return {"id": doc.add_line(a, b, **_layer_opts(doc, p))}

    @staticmethod
    @command(_drawing_target("Draw polyline"))
    def draw__polyline(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        doc = project.drawing(p["drawing"])
        pts = p.get("points")
        if not isinstance(pts, list) or not 2 <= len(pts) <= MAX_POINTS:
            raise BadRequest(f"A polyline needs 2 to {MAX_POINTS} points")
        return {"id": doc.add_polyline([_point(q, "point") for q in pts], closed=bool(p.get("closed", False)), **_layer_opts(doc, p))}

    @staticmethod
    @command(_drawing_target("Draw rectangle"))
    def draw__rectangle(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        doc = project.drawing(p["drawing"])
        w, h = _finite(p.get("width"), "width"), _finite(p.get("height"), "height")
        if w == 0 or h == 0:
            raise BadRequest("A rectangle needs a non-zero width and height")
        return {"id": doc.add_rectangle(_point(p.get("corner"), "corner"), w, h, **_layer_opts(doc, p))}

    @staticmethod
    @command(_drawing_target("Draw circle"))
    def draw__circle(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        doc = project.drawing(p["drawing"])
        r = _finite(p.get("radius"), "radius")
        if r <= 0:
            raise BadRequest("A circle needs a positive radius")
        return {"id": doc.add_circle(_point(p.get("center"), "center"), r, **_layer_opts(doc, p))}

    @staticmethod
    @command(_drawing_target("Add dimension"))
    def draw__dimension(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        doc = project.drawing(p["drawing"])
        a, b, at = _point(p.get("p1"), "p1"), _point(p.get("p2"), "p2"), _point(p.get("dimline_point"), "dimline_point")
        if a == b:
            raise BadRequest("A dimension needs two different points")
        return {"id": doc.add_dimension_aligned(a, b, at, **_layer_opts(doc, p))}

    @staticmethod
    @command(_drawing_target("Add text"))
    def draw__text(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        doc = project.drawing(p["drawing"])
        return {"id": doc.add_text(_point(p.get("position"), "position"), text(p.get("text"), "text", 500), _finite(p.get("height", 250), "height", 1e6),
                                   **_layer_opts(doc, p))}

    @staticmethod
    @command(_drawing_target("Move"))
    def draw__move(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        project.drawing(p["drawing"]).move(_ids(p.get("ids")), _finite(p.get("dx"), "dx"), _finite(p.get("dy"), "dy"))
        return {"moved": len(p["ids"])}

    @staticmethod
    @command(_drawing_target("Copy"))
    def draw__copy(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        return {"ids": project.drawing(p["drawing"]).duplicate(_ids(p.get("ids")), dx=_finite(p.get("dx"), "dx"), dy=_finite(p.get("dy"), "dy"))}

    @staticmethod
    @command(_drawing_target("Rotate"))
    def draw__rotate(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        project.drawing(p["drawing"]).rotate(_ids(p.get("ids")), _finite(p.get("degrees"), "degrees", 360), about=_point(p.get("about", [0, 0]), "about"), degrees=True)
        return {"rotated": len(p["ids"])}

    @staticmethod
    @command(_drawing_target("Scale"))
    def draw__scale(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        f = _finite(p.get("factor"), "factor", 1e6)
        if f <= 0:
            raise BadRequest("The scale factor must be positive")
        project.drawing(p["drawing"]).scale(_ids(p.get("ids")), f, about=_point(p.get("about", [0, 0]), "about"))
        return {"scaled": len(p["ids"])}

    @staticmethod
    @command(_drawing_target("Delete"))
    def draw__delete(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        project.drawing(p["drawing"]).remove(_ids(p.get("ids")))
        return {"deleted": len(p["ids"])}

    @staticmethod
    @command(_drawing_target("Change layer"))
    def draw__set_layer(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        doc = project.drawing(p["drawing"])
        opts = _layer_opts(doc, p)
        if not opts:
            raise BadRequest("layer is required")
        if opts["create_layer"]:
            doc.add_layer(opts["layer"])
        for eid in _ids(p.get("ids")):
            doc.update(eid, layer=opts["layer"])
        return {"layer": opts["layer"]}

    # -- 2D modify ---------------------------------------------------------------------------------------------------------
    @staticmethod
    @command(_drawing_target("Offset"))
    def draw__offset(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """A parallel copy of a line, polyline, rectangle or circle, on the side of ``side`` (a point the user clicked)."""
        doc = project.drawing(p["drawing"])
        e = _curve(doc, p.get("id"), ("line", "polyline", "circle"), "Offset")
        distance = number(p.get("distance"), "distance", 1e-6, MAX_COORD)
        side = _point(p.get("side"), "side")
        g, opts = e["geometry"], {"layer": e["layer"]}
        if e["kind"] == "circle":
            r = float(g["radius"])
            radius = r + distance if math.dist(side, g["center"]) > r else r - distance
            if radius <= 0:
                raise BadRequest("That offset distance is larger than the circle")
            return {"id": doc.add_circle(tuple(g["center"]), radius, **opts)}
        pts, closed = _chain_of(e)
        out = G.offset_chain(pts, closed, G.side_of(pts, closed, side) * distance)
        if e["kind"] == "line":
            return {"id": doc.add_line(out[0], out[-1], **opts)}
        return {"id": doc.add_polyline(out, closed=closed, **opts)}

    @staticmethod
    @command(_drawing_target("Trim"))
    def draw__trim(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Remove the part of a line or polyline under ``pick`` between the nearest cuts. ``edges`` default to every other curve."""
        doc = project.drawing(p["drawing"])
        e = _curve(doc, p.get("id"), ("line", "polyline"), "Trim")
        pts, closed = _chain_of(e)
        pieces = G.trim_chain(pts, closed, _point(p.get("pick"), "pick"), _cutters(doc, e["id"], p.get("edges")))
        ids = _write_pieces(doc, e, pieces)
        return {"ids": ids, "removed": not pieces}

    @staticmethod
    @command(_drawing_target("Extend"))
    def draw__extend(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Lengthen the end of a line or open polyline nearest ``pick`` until it meets the nearest boundary edge."""
        doc = project.drawing(p["drawing"])
        e = _curve(doc, p.get("id"), ("line", "polyline"), "Extend")
        pts, closed = _chain_of(e)
        if closed:
            raise BadRequest("A closed shape has no ends to extend")
        out = G.extend_chain(pts, _point(p.get("pick"), "pick"), _cutters(doc, e["id"], p.get("edges")))
        doc.update(e["id"], geometry=_chain_geometry(e, out, False))
        return {"id": e["id"]}

    @staticmethod
    @command(_drawing_target("Fillet"))
    def draw__fillet(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Round the corner between two lines with an arc of ``radius``, or square it off when the radius is 0."""
        doc = project.drawing(p["drawing"])
        ids, picks = p.get("ids"), p.get("picks")
        if not isinstance(ids, list) or len(ids) != 2 or ids[0] == ids[1] or not isinstance(picks, list) or len(picks) != 2:
            raise BadRequest("Fillet needs two different lines and a point on each")
        a, b = (_curve(doc, i, ("line",), "Fillet") for i in ids)
        radius = number(p.get("radius", 0), "radius", 0, MAX_COORD)
        l1, l2, arc = G.fillet_lines((tuple(a["geometry"]["start"]), tuple(a["geometry"]["end"])), (tuple(b["geometry"]["start"]), tuple(b["geometry"]["end"])),
                                     _point(picks[0], "pick 1"), _point(picks[1], "pick 2"), radius)
        doc.update(a["id"], geometry={"start": list(l1[0]), "end": list(l1[1])})
        doc.update(b["id"], geometry={"start": list(l2[0]), "end": list(l2[1])})
        out: dict[str, Any] = {"ids": [a["id"], b["id"]]}
        if arc:
            out["arc"] = doc.add_arc(arc[0], arc[1], arc[2], arc[3], layer=a["layer"])
        return out

    @staticmethod
    @command(_drawing_target("Apply SDRW modifier"))
    def draw__modifier(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Evaluate a native SDRW operation and commit the derived drawing geometry.

        This is deliberately destructive: SDRW kind changes (line -> polyline,
        closed region -> hatch) cannot be saved with update() on the same kind.
        The project transaction supplies undo/redo and preserves source
        snapshots. Native evaluation refuses invalid or unsupported operands.
        """
        doc = project.drawing(p["drawing"])
        operation = choice_of(p.get("operation"), ("boolean", "chamfer", "bend"), "modifier")
        oid = text(p.get("id"), "id", 80)
        source = doc.get(oid)
        parameters = p.get("parameters")
        if not isinstance(parameters, dict):
            raise BadRequest("Modifier parameters must be an object")
        operands = [text(p.get("operand"), "operand", 80)] if operation == "boolean" else []
        if operands and (operands[0] == oid or doc.get(operands[0]) is None):
            raise BadRequest("The Boolean operand must be another drawing entity")
        policy = p.get("policy", "tolerance_controlled" if operation == "boolean" else
                       "display_only" if operation == "bend" else "exact")
        evaluation_policy = {"mode": policy}
        if policy == "tolerance_controlled":
            evaluation_policy["absolute"] = 1e-6
        mid = doc.add_modifier(oid, kind="sdrw." + operation, operands=operands,
                               parameters=parameters, evaluation_policy=evaluation_policy)
        report = next(row for row in doc.evaluate_modifiers() if row["modifier_id"] == mid)
        if report["status"] != "ok" or report["result"] is None:
            raise BadRequest("The modifier produced empty geometry; nothing was changed")
        output = report["result"]
        doc.remove_modifier(mid)
        if output["kind"] == source["kind"]:
            doc.update(oid, geometry=output["geometry"])
            result_id = oid
        elif output["kind"] == "hatch":
            result_id = doc.add_hatch(output["geometry"]["boundary"], pattern="solid", layer=source["layer"])
            doc.remove([oid])  # Replace, do not leave the source region superposed on the Boolean result.
        elif output["kind"] == "polyline":
            result_id = doc.add_polyline(output["geometry"]["points"], closed=output["geometry"].get("closed", False),
                                         layer=source["layer"])
            doc.remove([oid])
        else:
            raise BadRequest(f"Cannot store a {output['kind']} as editable SDRW geometry")
        return {"id": result_id, "source": oid, "modifier": operation,
                "mode": "baked", "diagnostics": report.get("diagnostics", {})}

    @staticmethod
    @command(_drawing_target("Mirror"))
    def draw__mirror(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Reflect entities across the line through ``p1`` and ``p2``; ``copy`` keeps the originals (the default)."""
        doc = project.drawing(p["drawing"])
        ids = _ids(p.get("ids"))
        a, b = _point(p.get("p1"), "p1"), _point(p.get("p2"), "p2")
        if a == b:
            raise BadRequest("The mirror line needs two different points")
        keep = bool(p.get("copy", True))
        new_geometry = {i: _mirrored(doc.get(i), a, b) for i in ids}          # all validated before anything changes
        targets = doc.duplicate(ids, dx=0.0, dy=0.0) if keep else ids
        for src, dst in zip(ids, targets):
            doc.update(dst, geometry=new_geometry[src])
        return {"ids": list(targets)}

    @staticmethod
    @command(_drawing_target("Hatch"))
    def draw__hatch(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Fill closed shapes (rectangles, closed polylines, circles) with a solid, line or cross hatch. The hatch keeps a copy of the outline."""
        doc = project.drawing(p["drawing"])
        ids = _ids(p.get("ids"))
        shapes = [_curve(doc, i, ("polyline", "circle"), "Hatch") for i in ids]
        for s in shapes:
            if s["kind"] == "polyline" and not s["geometry"].get("closed"):
                raise BadRequest("A hatch needs closed shapes: close the polyline or use a rectangle")
        pattern = choice_of(p.get("pattern", "lines"), ("solid", "lines", "cross"), "pattern")
        angle = number(p.get("angle", 45), "angle", -360, 360)
        spacing = None
        if pattern != "solid":
            xs = [q for s in shapes for q in (G.polygon_of(s["kind"], s["geometry"]))]
            span = max(max(x for x, _ in xs) - min(x for x, _ in xs), max(y for _, y in xs) - min(y for _, y in xs))
            spacing = number(p["spacing"], "spacing", 1e-6, MAX_COORD) if p.get("spacing") is not None else max(span / 20.0, 1e-6)
        hatch = doc.add_hatch(ids, pattern=pattern, angle=angle, degrees=True, spacing=spacing, layer=shapes[0]["layer"])
        hatch_strokes(doc.get(hatch)["geometry"])                      # refuse here, and roll back, a spacing the drawing could never show
        return {"id": hatch}

    @staticmethod
    @command(_drawing_target("Edit geometry"))
    def draw__set_geometry(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Move the handles (grips) of one entity: a line's ends, a polyline's vertices, a circle's centre or radius, a text's position."""
        doc = project.drawing(p["drawing"])
        e = doc.get(text(p.get("id"), "id", 80))
        raw, g = p.get("geometry"), dict(e["geometry"])
        if not isinstance(raw, dict):
            raise BadRequest("geometry must be an object")
        kind = e["kind"]
        if kind == "line":
            start, end = _point(raw.get("start"), "start"), _point(raw.get("end"), "end")
            if start == end:
                raise BadRequest("A line needs two different points")
            g["start"], g["end"] = list(start), list(end)
        elif kind == "polyline":
            pts = raw.get("points")
            if not isinstance(pts, list) or not 2 <= len(pts) <= MAX_POINTS:
                raise BadRequest(f"A polyline needs 2 to {MAX_POINTS} points")
            g["points"] = [list(_point(q, "point")) for q in pts]
        elif kind == "circle":
            g["center"] = list(_point(raw.get("center"), "center"))
            g["radius"] = number(raw.get("radius"), "radius", 1e-9, MAX_COORD)
        elif kind == "text":
            g["position"] = list(_point(raw.get("position"), "position"))
        elif kind == "dimension" and g.get("dim_type") == "aligned":
            for key in ("p1", "p2", "dimline_point"):
                g[key] = list(_point(raw.get(key), key))
        else:
            raise BadRequest(f"A {kind} has no handles to edit")
        doc.update(e["id"], geometry=g)
        return {"id": e["id"]}

    # -- 3D / BIM ----------------------------------------------------------------------------------------------------------
    @staticmethod
    @command(_model_target("Add wall"))
    def model__add_wall(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Start and end are in the project's drawing unit (so 2D and 3D agree); thickness in mm; height in m."""
        f = MM_PER[project.manifest["unit"]] / 1000.0
        start = tuple(c * f for c in _point(p.get("start"), "start"))
        end = tuple(c * f for c in _point(p.get("end"), "end"))
        level = project.level(text(p.get("level"), "level", 80))
        thickness = number(p.get("thickness", 200), "thickness", 1, 5000) / 1000.0
        height = number(p.get("height", 3.0), "height", 0.01, 500)
        material = p.get("material", "Concrete")
        if material not in MATERIALS:
            raise BadRequest(f"material must be one of {', '.join(MATERIALS)}")
        name = text(p.get("name") or "Basic Wall", "name", 80)
        oid = project.model.add_box(width=1.0, depth=1.0, height=1.0, name=name)
        project.model.attach_semantics(oid, {"category": "wall", "type": "Basic Wall", "level": level["id"], "start": list(start), "end": list(end),
                                             "thickness": thickness, "height": height, "base_offset": 0.0, "material": material}, namespace=NS)
        regenerate_wall(project, oid)
        return {"id": oid}

    @staticmethod
    @command(_model_target("Add layer"))
    def model__add_layer(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Add a layer to a wall, floor or roof (``material``, ``thickness`` in mm, ``name``, ``position``: its place in the stack, default last)."""
        oid = text(p.get("id"), "id", 80)
        if not E.alive(project, oid):
            raise NotFound(f"Element {oid} does not exist")
        return {"id": oid, "layer": LY.add_layer(project, oid, p)}

    @staticmethod
    @command(_model_target("Remove layer"))
    def model__remove_layer(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        oid = text(p.get("id"), "id", 80)
        if not E.alive(project, oid):
            raise NotFound(f"Element {oid} does not exist")
        LY.remove_layer(project, oid, text(p.get("layer"), "layer", 40))
        return {"id": oid}

    @staticmethod
    @command(_model_target("Add door panel"))
    def model__add_panel(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Split the widest panel of a door in two, so the door can be edited panel by panel."""
        oid = text(p.get("id"), "id", 80)
        if not E.alive(project, oid):
            raise NotFound(f"Element {oid} does not exist")
        return {"id": oid, "panel": E.add_panel(project, oid)}

    @staticmethod
    @command(_model_target("Set IFC property"))
    def model__set_pset(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Set (empty value: clear) one official property of an element (``level`` occurrence) or of its type (``level`` type)."""
        oid = text(p.get("id"), "id", 80)
        if not E.alive(project, oid):
            raise NotFound(f"Element {oid} does not exist")
        BD.set_value(project, oid, text(p.get("set"), "set", 80), text(p.get("property"), "property", 80), p.get("value"), str(p.get("level") or "occurrence"))
        return {"id": oid}

    @staticmethod
    @command(_model_target("Set IFC quantity"))
    def model__set_quantity(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Store an imported (with its source) or overridden quantity; ``mode`` null goes back to the calculated value."""
        oid = text(p.get("id"), "id", 80)
        if not E.alive(project, oid):
            raise NotFound(f"Element {oid} does not exist")
        BD.set_quantity(project, oid, text(p.get("set"), "set", 80), text(p.get("name"), "name", 80), p.get("value"), p.get("mode"), str(p.get("source") or ""))
        return {"id": oid}

    @staticmethod
    @command(_model_target("Assign bSDD class"))
    def model__assign_bsdd(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        oid = text(p.get("id"), "id", 80)
        if not E.alive(project, oid):
            raise NotFound(f"Element {oid} does not exist")
        return {"id": oid, "reference": BD.assign(project, oid, text(p.get("uri"), "uri", 400), origin=str(p.get("origin") or "manual"),
                                                    confirm=bool(p.get("confirm", False)), offline=bool(p.get("offline", False)))}

    @staticmethod
    @command(_model_target("Remove bSDD class"))
    def model__remove_bsdd(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        oid = text(p.get("id"), "id", 80)
        if not E.alive(project, oid):
            raise NotFound(f"Element {oid} does not exist")
        BD.unassign(project, oid, text(p.get("uri"), "uri", 400))
        return {"id": oid}

    @staticmethod
    @command(_model_target("Add IDS file"))
    def bim__add_ids(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Store an IDS (Information Delivery Specification) file, given as text, in the project."""
        xml = p.get("xml")
        if not isinstance(xml, str):
            raise BadRequest("xml must be the text of the .ids file")
        return RQ.add_ids(project, xml.encode("utf-8"), str(p.get("filename") or "requirements.ids"))

    @staticmethod
    @command(_model_target("Remove IDS file"))
    def bim__remove_ids(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        RQ.remove_ids(project, text(p.get("id"), "id", 40))
        return {}

    @staticmethod
    @command(_model_target("Validate IDS"))
    def bim__validate_ids(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Evaluate an IDS file against the model and keep the result as evidence in the project."""
        return RQ.validate_ids(project, text(p.get("id"), "id", 40))["summary"]

    @staticmethod
    @command(_model_target("Set ILS options"))
    def bim__set_ils(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        return RQ.set_ils(project, p)

    @staticmethod
    @command(_model_target("Add project requirement"))
    def bim__add_ils_rule(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        return RQ.add_project_rule(project, p)

    @staticmethod
    @command(_model_target("Remove project requirement"))
    def bim__remove_ils_rule(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        RQ.remove_project_rule(project, text(p.get("id"), "id", 40))
        return {}

    @staticmethod
    @command(_model_target("Remove door panel"))
    def model__remove_panel(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        oid = text(p.get("id"), "id", 80)
        if not E.alive(project, oid):
            raise NotFound(f"Element {oid} does not exist")
        E.remove_panel(project, oid, text(p.get("panel"), "panel", 40))
        return {"id": oid}

    @staticmethod
    @command(_model_target("Attach walls to roof"))
    def model__attach_wall(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Attach the top of walls to a roof (``to: "roof"``, ``ids`` with the roof among them or ``roof``, ``offset`` in mm) or extend them to a level
        (``to: "level"``, ``level`` default the next one up). Their height is then derived and follows the roof or level."""
        ids = [text(i, "id", 80) for i in (p.get("ids") or [])]
        to = text(p.get("to") or "roof", "to", 10)
        if to == "roof":
            return AT.attach_to_roof(project, ids, p.get("roof"), p.get("offset", 0))
        if to == "level":
            return AT.extend_to_level(project, ids, p.get("level"), p.get("offset", 0))
        raise BadRequest("'to' is 'roof' or 'level'")

    @staticmethod
    @command(_model_target("Detach walls"))
    def model__detach_wall(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Free the top of walls attached to a roof (``from: "roof"``) or extended to a level (``from: "level"``); either if not given."""
        ids = [text(i, "id", 80) for i in (p.get("ids") or [])]
        kind = p.get("from")
        return AT.detach(project, ids, None if kind is None else text(kind, "from", 10))

    @staticmethod
    @command(_model_target("Move layer"))
    def model__move_layer(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        oid = text(p.get("id"), "id", 80)
        if not E.alive(project, oid):
            raise NotFound(f"Element {oid} does not exist")
        LY.move_layer(project, oid, text(p.get("layer"), "layer", 40), p.get("position"))
        return {"id": oid}

    @staticmethod
    @command(_model_target("Edit element"))
    def model__set_property(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        oid, key = text(p.get("id"), "id", 80), text(p.get("key"), "key", 60)
        if not E.alive(project, oid):
            raise NotFound(f"Element {oid} does not exist")
        if key == "name" and E.category(project, oid) == "grid":
            DT.edit_grid(project, oid, {"label": p.get("value")}, 1.0)               # a grid label is unique
        elif key == "name":
            project.model.rename_object(oid, text(p.get("value"), "name", 80))
        else:
            E.set_property(project, oid, key, p.get("value"))
        return {"id": oid}

    @staticmethod
    @command(_model_target("Move element"))
    def model__move(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        oid = text(p.get("id"), "id", 80)
        if not E.alive(project, oid):
            raise NotFound(f"Element {oid} does not exist")
        dx, dy, dz = (_finite(p.get(k, 0.0), k, 1e6) for k in ("dx", "dy", "dz"))
        E.move(project, oid, dx, dy, dz)                         # an element's position is its parameters, so they move, not just the geometry
        return {"id": oid}

    @staticmethod
    @command(_model_target("Stretch wall"))
    def model__set_wall_ends(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Move a wall's start and/or end (drawing units) in one step, so its joints and hosted openings are rebuilt once."""
        oid = text(p.get("id"), "id", 80)
        if not E.alive(project, oid):
            raise NotFound(f"Element {oid} does not exist")
        f = MM_PER[project.manifest["unit"]] / 1000.0
        ends = {k: tuple(c * f for c in _point(p[k], k)) if p.get(k) is not None else None for k in ("start", "end")}
        E.stretch_wall(project, oid, ends["start"], ends["end"])
        return {"id": oid}

    @staticmethod
    @command(_model_target("Stretch outline"))
    def model__set_boundary(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Set the outline of a floor or roof (``points``, drawing units): the corner and edge grips of a selected floor send this."""
        oid = text(p.get("id"), "id", 80)
        if not E.alive(project, oid):
            raise NotFound(f"Element {oid} does not exist")
        E.set_boundary(project, oid, p.get("points"), MM_PER[project.manifest["unit"]] / 1000.0)
        return {"id": oid}

    @staticmethod
    @command(_model_target("Automatic rooms"))
    def model__set_auto_rooms(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Turn automatic rooms on (rooms are made at once for every space the walls already close) or off (the rooms stay, as ordinary rooms)."""
        on = boolean(p.get("on"), "on")
        return {"on": on, "rooms": AR.set_on(project, on)}

    @staticmethod
    @command(_model_target("Update rooms"))
    def model__update_rooms(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Work the automatic rooms out again from the walls, on every level."""
        if not AR.is_on(project):
            raise BadRequest("Automatic rooms are off", hint="Turn them on first")
        return {"rooms": AR.reconcile(project, force=True)}

    @staticmethod
    @command(_model_target("Rotate elements"))
    def model__rotate(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Turn one or more elements about ``centre`` (drawing units) by ``angle`` degrees, counter-clockwise; all or none."""
        ids = p.get("ids")
        if not isinstance(ids, list) or not ids or len(ids) > 500:
            raise BadRequest("ids must be a list of 1 to 500 elements")
        ids = [text(i, "id", 80) for i in ids]
        for oid in ids:
            if not E.alive(project, oid):
                raise NotFound(f"Element {oid} does not exist")
        f = MM_PER[project.manifest["unit"]] / 1000.0
        centre = tuple(c * f for c in _point(p.get("centre"), "centre"))
        angle = math.radians(number(p.get("angle"), "angle", -360, 360))
        E.rotate_many(project, ids, angle, centre)
        return {"ids": ids}

    @staticmethod
    @command(_model_target("Delete element"))
    def model__delete(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        oid = text(p.get("id"), "id", 80)
        if not E.alive(project, oid):
            raise NotFound(f"Element {oid} does not exist")
        going = {oid, *(E.hosted(project, oid) if E.m(E.meta_of(project, oid), "category") == "wall" else []), *GU.dependents(project, oid)}
        project.forget_animation(going)
        E.remove(project, oid)
        return {"deleted": oid}

    @staticmethod
    @command(_model_target("Add door"))
    def model__add_door(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """A door in a wall: ``wall`` plus ``point`` (drawing units, projected onto the wall) or ``offset`` (m from the wall's start)."""
        return {"id": E.add_opening(project, "door", p, MM_PER[project.manifest["unit"]] / 1000.0)}

    @staticmethod
    @command(_model_target("Add window"))
    def model__add_window(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        return {"id": E.add_opening(project, "window", p, MM_PER[project.manifest["unit"]] / 1000.0)}

    @staticmethod
    @command(_model_target("Add opening profile"))
    def model__add_opening_profile(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Create a real frame around a door/window rough opening; defaults to three sides for doors and four for windows."""
        return {"id": E.add_opening_profile(project, p, MM_PER[project.manifest["unit"]] / 1000.0)}

    @staticmethod
    @command(_model_target("Remove opening profile"))
    def model__remove_opening_profile(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        opening = text(p.get("opening"), "opening", 80)
        return {"removed": E.remove_opening_profile(project, opening)}

    @staticmethod
    @command(_model_target("Add floor"))
    def model__add_floor(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """A floor slab whose top is at the level: ``points`` are its corners in drawing units, ``thickness`` in mm."""
        return {"id": E.add_floor(project, p, MM_PER[project.manifest["unit"]] / 1000.0)}

    @staticmethod
    @command(_model_target("Add roof"))
    def model__add_roof(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """A flat roof over any outline, or a gable/shed roof over a rectangular one (``pitch`` in degrees)."""
        return {"id": E.add_roof(project, p, MM_PER[project.manifest["unit"]] / 1000.0)}

    @staticmethod
    @command(_model_target("Add roof accessory"))
    def model__add_roof_accessory(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Add an independently selectable ridge beam, ridge strap or roof-edge member to the selected roof."""
        ids = E.add_roof_accessory(project, p)
        return {"ids": ids, "id": ids[0]}

    @staticmethod
    @command(_model_target("Add beam"))
    def model__add_beam(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """A horizontal beam between two points whose top is ``top`` metres above the level."""
        return {"id": E.add_beam(project, p, MM_PER[project.manifest["unit"]] / 1000.0)}

    @staticmethod
    @command(_model_target("Add column"))
    def model__add_column(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """A vertical column centred on ``point`` (drawing units), from ``base`` to ``top`` metres above the level."""
        return {"id": E.add_column(project, p, MM_PER[project.manifest["unit"]] / 1000.0)}

    @staticmethod
    @command(_model_target("Add extrusion"))
    def model__add_extrusion(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """An extrusion of the outline ``points`` (drawing units) from ``base`` for ``depth`` (m). With ``void`` true it is a void that cuts instead."""
        return {"id": FX.add_extrusion(project, p, MM_PER[project.manifest["unit"]] / 1000.0)}

    @staticmethod
    @command(_model_target("Make void"))
    def model__make_void(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Turn extrusions into voids (``void`` true, the default) or back into solid extrusions; all or none."""
        ids = p.get("ids")
        if not isinstance(ids, list) or not ids or len(ids) > 500:
            raise BadRequest("ids must be a list of 1 to 500 extrusions")
        void = boolean(p["void"], "void") if p.get("void") is not None else True
        changed = []
        for oid in [text(i, "id", 80) for i in ids]:
            if not E.alive(project, oid):
                raise NotFound(f"Element {oid} does not exist")
            if FX.make_void(project, oid, void):
                changed.append(oid)
        if not changed:
            raise BadRequest("Already a void" if void else "Already a solid extrusion")
        return {"ids": changed, "void": void}

    @staticmethod
    @command(_model_target("Add sweep"))
    def model__add_sweep(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """A profile swept along the plan path ``points`` (drawing units): ``shape`` with ``width``/``height`` in mm, or a ``section`` of [u, v] metres."""
        return {"id": FX.add_sweep(project, p, MM_PER[project.manifest["unit"]] / 1000.0)}

    @staticmethod
    @command(_model_target("Apply SGEO modifier"))
    def model__modifier(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Use the SLAI SGEO modifier kernel, preserving a non-destructive DAG.

        Semantic BIM hosts still require their dedicated parametric editing
        commands. Independent generic *family instances* may use non-destructive
        modifiers: the family definition, parameter formulas, and generated
        base geometry remain authoritative and editable. Baked mesh edits are
        reserved for independent non-family objects.
        """
        operation = choice_of(p.get("operation"),
                              ("boolean", "chamfer", "bend", "split", "weld", "subdivision"),
                              "modifier")
        oid = text(p.get("id"), "id", 80)
        source = project.model.get(oid)
        meta = source.get("metadata", {})
        cat = E.m(meta, "category")
        # Family instances have native, parameter-generated SGEO geometry.
        # They do not lose their family link when a modifier DAG is layered
        # over that geometry; only destructive baking would break that link.
        if cat not in (None, "generic"):
            raise BadRequest("This BIM host has construction or attachment semantics",
                             hint="Use the BIM element's parametric Properties for host-aware geometry. Non-destructive modifiers are available on generic family instances.")
        if source.get("geometry") is None:
            raise BadRequest("Choose a geometric object or a family instance with generated geometry")
        family_instance = "family_id" in source
        if family_instance and operation in ("weld", "subdivision"):
            raise BadRequest("Weld and Subdivision would overwrite this family's generated geometry",
                             hint="Use Split, Boolean, Chamfer or Bend as a non-destructive modifier, or make an independent copy before baking.")
        parameters = p.get("parameters")
        if not isinstance(parameters, dict):
            raise BadRequest("Modifier parameters must be an object")
        if operation in ("weld", "subdivision"):
            from src.utils.spatial_formats.sgeo.meshing import weld_vertices
            from src.utils.spatial_formats.sgeo.modifier_ops import _subdivide_conforming
            geometry = source["geometry"]
            if operation == "weld":
                if geometry["kind"] != "mesh":
                    raise BadRequest("Weld requires an indexed triangle mesh")
                decimals = parameters.get("decimals")
                if decimals is not None and (type(decimals) is not int or not 0 <= decimals <= 15):
                    raise BadRequest("Weld decimals must be an integer between 0 and 15")
                result, dropped = weld_vertices(geometry, decimals=decimals)
                diagnostics = {"backend": "SGEO vertex welding", "dropped_faces": dropped, "approximate": decimals is not None}
            else:
                size = number(parameters.get("max_edge"), "maximum edge length (m)", 1e-5, 10000)
                mesh = project.model.tessellate(oid)
                vertices, faces = _subdivide_conforming(mesh["vertices"], mesh["faces"], max_edge=size)
                result = {"kind": "mesh", "vertices": [list(v) for v in vertices],
                          "faces": [list(f) for f in faces]}
                diagnostics = {"backend": "conforming edge subdivision", "approximate": False,
                               "vertices": len(vertices), "faces": len(faces)}
            project.model.set_geometry(oid, result)
            return {"id": oid, "operation": operation, "mode": "baked", "diagnostics": diagnostics}
        operands = [text(p.get("operand"), "operand", 80)] if operation == "boolean" else []
        if operands:
            operand = project.model.get(operands[0])
            if operands[0] == oid or operand.get("geometry") is None:
                raise BadRequest("Select a distinct geometric Boolean operand")
            other_meta = operand.get("metadata", {})
            if E.m(other_meta, "category") not in (None, "generic"):
                raise BadRequest("A Boolean operand must be independent generic geometry or a generic family instance",
                                 hint="Edit hosted BIM solids through their parametric construction commands.")
        policy = p.get("policy", "display_only" if operation == "bend" else "tolerance_controlled")
        evaluation_policy = {"mode": policy}
        if policy == "tolerance_controlled":
            evaluation_policy["absolute"] = 1e-6
        mid = project.model.add_modifier(oid, kind="sgeo." + operation,
                                         operands=operands, parameters=parameters,
                                         evaluation_policy=evaluation_policy)
        result = next(item for item in project.model.evaluate_modifiers() if item["modifier_id"] == mid)
        if result["status"] != "ok" or not result["result"] or result["result"].get("geometry") is None:
            raise BadRequest("The modifier produced empty geometry; no change was committed")
        return {"id": oid, "modifier_id": mid, "operation": operation, "mode": "non_destructive",
                "diagnostics": result.get("diagnostics", {})}

    @staticmethod
    @command(_model_target("Edit SGEO modifier"))
    def model__set_modifier(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        oid = text(p.get("id"), "id", 80)
        mid = text(p.get("modifier"), "modifier", 80)
        current = next((m for m in project.model.modifiers()
                        if m["id"] == mid and m["target"]["item_id"] == oid), None)
        if not current:
            raise BadRequest("The selected SGEO modifier does not belong to this model element")
        key = text(p.get("key"), "parameter", 60)
        value = p.get("value")
        params = dict(current["parameters"])
        parts = key.split(":")
        field = parts[0]
        if field not in params:
            raise BadRequest(f"'{field}' is not a parameter of this modifier")
        if len(parts) == 2:
            if not isinstance(params[field], list):
                raise BadRequest("Only a geometric vector supports indexed components")
            if not parts[1].isdigit():
                raise BadRequest("Modifier vector component must be a nonnegative index")
            idx = integer(int(parts[1]), "vector component", 0, len(params[field]) - 1)
            params[field] = [*params[field]]
            params[field][idx] = number(value, key, -1e9, 1e9)
        elif len(parts) == 1:
            old = params[field]
            if type(old) is int:
                params[field] = integer(value, field, 1, 256)
            elif isinstance(old, (int, float)):
                val = number(value, field, -1e9, 1e9)
                params[field] = math.radians(val) if field == "angle" else val
            elif isinstance(old, str):
                params[field] = text(value, field, 60)
            else:
                raise BadRequest("This modifier parameter cannot be edited in Properties")
        else:
            raise BadRequest("Modifier parameter key has too many components")
        project.model.update_modifier(mid, parameters=params)
        report = next(item for item in project.model.evaluate_modifiers() if item["modifier_id"] == mid)
        if report["status"] != "ok" or report["result"] is None:
            raise BadRequest("The changed modifier produces invalid or empty geometry")
        return {"id": oid, "modifier_id": mid, "parameters": params}

    @staticmethod
    @command(_model_target("Array elements"))
    def model__array(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Repeat elements and keep the pattern (an ``array`` element that regenerates its copies). ``count`` is the number of items including the originals.
        Rectangular (``mode`` "grid", or "linear"): ``axes`` is up to three of {count, spacing, direction ("x", "y", "z", a vector or an angle in plan)} or
        {count, step [dx, dy, dz]}; or ``dimension`` "X", "Y", "Z", "XY", "XYZ" … with ``counts`` and ``spacings``; or the one-axis form ``count`` + ``step``.
        Radial: ``centre`` and ``angle`` in degrees. Lengths are in drawing units. ``linked`` (default true) makes the copies follow the source when it changes.
        ``persistent`` false makes plain copies with no array to edit."""
        ids = p.get("ids")
        if not isinstance(ids, list) or not ids or len(ids) > 500:
            raise BadRequest("ids must be a list of 1 to 500 elements")
        spec = AY.parse(p, MM_PER[project.manifest["unit"]] / 1000.0)
        result = AY.make(project, [text(i, "id", 80) for i in ids], spec)
        if p.get("persistent") is False:
            AY.release(project, result["array"])
            result["array"] = None
        return result

    @staticmethod
    @command(_model_target("Edit array"))
    def model__set_array(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Change an array: any of ``axes`` / ``dimension`` + ``counts`` + ``spacings`` / ``count`` (one axis, or radial), ``centre``, ``angle``, ``linked``
        (turning it on makes the copies follow the source again). The copies are regenerated; those a person has edited are left alone."""
        rid = text(p.get("id"), "id", 80)
        meta = AY.require(project, rid)
        spec = AY.parse(p, MM_PER[project.manifest["unit"]] / 1000.0, AY.spec_of(meta))
        return AY.set_parameters(project, rid, spec)

    @staticmethod
    @command(_model_target("Release array"))
    def model__release_array(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Dissolve an array (``id``, or any item of it) and keep every item as an ordinary element."""
        oid = text(p.get("id"), "id", 80)
        if not E.alive(project, oid):
            raise NotFound(f"Element {oid} does not exist")
        rid = oid if E.category(project, oid) == "array" else AY.of(project, oid)
        if rid is None:
            raise BadRequest("That is not an array, and not an item of one")
        return AY.release(project, rid)

    @staticmethod
    @command(_model_target("Make array item independent"))
    def model__detach_array_item(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Let one item of an array go its own way: it stays as it is, and the array no longer changes it (it keeps its place in the pattern)."""
        oid = text(p.get("id"), "id", 80)
        if not E.alive(project, oid):
            raise NotFound(f"Element {oid} does not exist")
        return {"array": AY.make_independent(project, oid)}

    @staticmethod
    @command(_model_target("Add room"))
    def model__add_room(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """A room on a level: ``points`` are its outline in drawing units. It has no solid; its area comes from the outline."""
        from . import rooms as R
        return {"id": R.add_room(project, p, MM_PER[project.manifest["unit"]] / 1000.0)}

    @staticmethod
    @command(_model_target("Add gutters"))
    def model__add_gutters(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Gutters on the eaves of a roof (``roof``; ``edges`` picks outline edges, default every eave). Edges that follow one another become one run
        with mitred corners (``connect`` false keeps them apart; ``run`` is a gutter id to add to an existing run). Each run falls towards ``fall_to``
        (end, start, middle or corners) and gets a downpipe at each low point unless ``downpipe`` is false. ``gutter`` is the type: half_round, ogee, box
        or fascia; width, depth, thickness, fall (mm/m), material, rainfall, gap (mm out from the eave line), drop (mm below the eave), ext_start,
        ext_end (mm, free ends only) and end_start, end_end (cap or open) are optional. ``model.preview_gutters`` shows what a request would make."""
        made = GU.add_gutters(project, p, MM_PER[project.manifest["unit"]] / 1000.0)
        return {"ids": made, "gutters": [i for i in made if E.category(project, i) == "gutter"]}

    @staticmethod
    @command(_model_target("Add foundation"))
    def model__add_foundation(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """A foundation of ``kind``: poer (pad: ``point`` or a column in ``support``; ``width``, ``length``, ``height`` mm; ``shape`` rect or round), strook (strip:
        a wall in ``support``, or ``start`` and ``end``; ``width``, ``height``, ``offset`` mm), funderingsbalk (beam between the two columns in ``support``, or
        ``start`` and ``end``), plaat (raft) or vloer (ground-bearing slab: ``points`` and ``thickness``, with ``above`` and ``below`` layers of
        ``{name, material, thickness (mm), conductivity (W/m·K)}``). ``top`` (m above the level) defaults to the base of what it stands on. Reinforcement is
        declared (``bars``, ``cover``, ``concrete_class``, ``steel``), never designed."""
        return {"id": FD.add_foundation(project, p, MM_PER[project.manifest["unit"]] / 1000.0)}

    @staticmethod
    @command(_model_target("Add foundations under supports"))
    def model__add_foundations_under(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """A strip under each wall and a pad under each column in ``supports``, with the same sizes (``width``, ``height`` mm, ``top``, ...); all or none."""
        made = FD.add_under(project, p, MM_PER[project.manifest["unit"]] / 1000.0)
        return {"ids": made}

    @staticmethod
    @command(_model_target("Edit foundation"))
    def model__set_foundation_data(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Replace the layers (``above``, ``below``) and/or reinforcement (``bars``, ``cover``, ``concrete_class``, ``steel``) of the foundation ``id``."""
        FD.set_data(project, text(p.get("id"), "id", 80), p)
        return {"id": p["id"]}

    @staticmethod
    @command(_model_target("Add downpipe"))
    def model__add_downpipe(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """A downpipe under ``gutter``, ``offset`` m along it, of ``diameter`` mm, down to ``bottom`` (m, default a little above the lowest level)."""
        return {"id": GU.add_downpipe(project, p, MM_PER[project.manifest["unit"]] / 1000.0)}

    @staticmethod
    @command(_model_target("Add stair"))
    def model__add_stair(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """A straight stair from ``level`` to ``top_level`` (default: the next level up) along the line start → end (drawing units)."""
        return {"id": SR.add_stair(project, p, MM_PER[project.manifest["unit"]] / 1000.0)}

    @staticmethod
    @command(_model_target("Add grid"))
    def model__add_grid(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """A named grid line between two points (drawing units); it is shown on every floor plan."""
        return {"id": DT.add_grid(project, p, MM_PER[project.manifest["unit"]] / 1000.0)}

    @staticmethod
    @command(_model_target("Edit grid"))
    def model__set_grid(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Change a grid line in one step: ``start`` / ``end`` (drawing units), or ``length`` (drawing units) and ``angle`` (degrees) from its start
        (``hold`` "end" or "middle" keeps that point), ``label``, ``bubble`` (start, end, both, none). Every view shows the change."""
        oid = text(p.get("id"), "id", 80)
        if not E.alive(project, oid):
            raise NotFound(f"Element {oid} does not exist")
        DT.edit_grid(project, oid, p, MM_PER[project.manifest["unit"]] / 1000.0)
        return {"id": oid}

    # -- levels --------------------------------------------------------------------------------------------------------------
    @staticmethod
    @command(_model_target("Add level"))
    def level__add(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        return DT.add_level(project, p, lambda name: SDRWDocument.new(name, unit=project.manifest["unit"]))

    @staticmethod
    @command(_model_target("Edit level"))
    def level__set(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        return DT.set_level(project, p)

    @staticmethod
    @command(_model_target("Delete level"))
    def level__delete(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        return DT.delete_level(project, p)

    # -- families --------------------------------------------------------------------------------------------------------------
    @staticmethod
    @command(_model_target("Define family"))
    def family__define(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """A parametric family on SGEO's own family mechanism: ``shape`` (box, cylinder, sphere) and its ``parameters`` (lengths in mm)."""
        return {"id": FM.define(project, p)}

    @staticmethod
    @command(_model_target("Edit family"))
    def family__update(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Change a family's name or parameters; every element made from it is regenerated, or the whole change is refused."""
        return FM.update(project, p)

    @staticmethod
    @command(_model_target("Delete family"))
    def family__delete(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        FM.delete(project, p.get("id"))
        return {"removed": p.get("id")}

    @staticmethod
    @command(_model_target("Place family"))
    def family__place(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Place an element of a family at ``point`` (drawing units) on ``level``; ``parameters`` override the family's values for this one."""
        return {"id": FM.place(project, p, MM_PER[project.manifest["unit"]] / 1000.0)}

    @staticmethod
    @command(_model_target("Rebuild model"))
    def model__rebuild(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Rebuild every element's geometry from its parameters. Refused, with the element named, if one no longer fits its level or host."""
        E.regenerate_all(project)
        return {"rebuilt": len(E.element_ids(project))}

    # -- reports -------------------------------------------------------------------------------------------------------------
    @staticmethod
    @command(_model_target("Make report"))
    def report__create(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Make a project report: an SLDC document of the project's details, schedules, sheets and model check, as they are now."""
        return RP.create(project, p)

    @staticmethod
    @command(_model_target("Delete report"))
    def report__delete(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        entry = RP.find(project, p.get("id"))
        for sheet in project.manifest["sheets"]:
            if any(vp.get("report_id") == entry["id"] for vp in sheet.get("viewports", [])):
                raise Conflict(f"The report '{entry['name']}' is placed on sheet {sheet['number']}", hint="Remove its viewport from the sheet first")
        RP.delete(project, p.get("id"))
        return {"removed": p.get("id")}

    # -- annotations tied to the model -------------------------------------------------------------------------------------------
    @staticmethod
    @command(_drawing_target("Tag element"))
    def draw__tag(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """A tag in a drawing that shows data of a model element (mark, name, type, size) and follows it when the model changes."""
        return AN.add_tag(project, p["drawing"], p, MM_PER[project.manifest["unit"]])

    @staticmethod
    @command(_drawing_target("Change tag"))
    def draw__tag_field(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        AN.set_field(project, p["drawing"], text(p.get("id"), "id", 80), text(p.get("field"), "field", 20))
        return {"id": p.get("id")}

    # -- import --------------------------------------------------------------------------------------------------------------
    @staticmethod
    @command(_model_target("Import into model"))
    def model__import(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Add the objects of an SGEO, GLB, STL, OBJ or IFC file (``data`` is base64) to the model. ``unit`` is the file's length unit where the
        format does not say (STL, OBJ). The conversion report comes back with the count."""
        fmt = text(p.get("format"), "format", 8).lower()
        name = text(p.get("name") or "Imported file", "name", 120)
        return IO.import_model(project, fmt, IO.decode(p.get("data")), name=name, unit=choice_of(p.get("unit") or "mm", tuple(IO.UNIT_M), "unit"))

    @staticmethod
    @command(_drawing_target("Import into drawing"))
    def draw__import(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Add the entities of an SDRW, SVG or DXF file (``data`` is base64) to a drawing, converted from the file's unit to the drawing's."""
        fmt = text(p.get("format"), "format", 8).lower()
        doc = project.drawing(p["drawing"])
        name = text(p.get("name") or "Imported file", "name", 120)
        unit = text(p["unit"], "unit", 8) if p.get("unit") else None        # without one the file's own declaration applies
        return IO.import_drawing(doc, fmt, IO.decode(p.get("data")), name=name, unit=unit)

    # -- animation ---------------------------------------------------------------------------------------------------------
    @staticmethod
    @command(_model_target("Edit animation"))
    def anim__edit(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """One edit of the model's animation, carried out by the SLAI Animation Agent (``operation`` is one of its edit operations,
        ``payload`` its arguments). It is a project command like any other: one revision, undoable, and rolled back if the agent or SGEO refuses."""
        operation = text(p.get("operation"), "operation", 40)
        payload = p.get("payload", {})
        if not isinstance(payload, dict):
            raise BadRequest("payload must be an object")
        allowed = AB.edit_operations(AB.BRIDGE)
        if not allowed:
            raise BadRequest("The SLAI Animation Agent is not available, so the animation cannot be edited", details={"reason": AB.BRIDGE.reason})
        if operation not in allowed:
            raise BadRequest(f"'{operation}' is not an animation edit", details={"operations": allowed})
        result = AB.BRIDGE.request(project.model, operation, payload)
        return {"operation": operation, "scene_revision": result.get("revision")}

    # -- animation: authoring ----------------------------------------------------------------------------------------------
    # Each command reads the model's animation, changes it with ``animation_authoring`` and hands the whole document to the Animation Agent
    # (``set_animation_v2``), which has SGEO validate and commit it. One command is one revision and one undo step.
    @staticmethod
    @command(_model_target("Set key"))
    def anim__key(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """A key for one property of one element at a time (a key already at that frame is replaced). ``value`` is in UI units: metres for
        ``translation`` and ``scale`` factors (three numbers), degrees for ``turn``, true / false for ``visibility``."""
        prop = choice_of(p.get("property"), AA.PROPERTIES, "property")
        oid = project._animatable(p.get("id"))
        spec, fps = project._authoring_spec(True)
        notes = AA.set_key(spec, oid, prop, _finite(p.get("time"), "time"), AA.spec_value(prop, p.get("value")), fps=fps,
                           interpolation=choice_of(p.get("interpolation") or "smooth", AA.INTERPOLATIONS, "interpolation"))
        project._commit_animation(spec)
        return {"notes": notes}

    @staticmethod
    @command(_model_target("Set pose"))
    def anim__pose(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Keys for several properties of one element at one time, in one step (what the Pose panel and dragging in the 3D view do)."""
        oid = project._animatable(p.get("id"))
        values = p.get("values")
        if not isinstance(values, dict) or not values:
            raise BadRequest("values must name at least one property")
        spec, fps = project._authoring_spec(True)
        notes: list[str] = []
        for prop in values:
            choice_of(prop, AA.PROPERTIES, "property")
        for prop, value in values.items():
            notes += AA.set_key(spec, oid, prop, _finite(p.get("time"), "time"), AA.spec_value(prop, value), fps=fps,
                                interpolation=choice_of(p.get("interpolation") or "smooth", AA.INTERPOLATIONS, "interpolation"))
        project._commit_animation(spec)
        return {"notes": notes}

    @staticmethod
    @command(_model_target("Delete key"))
    def anim__delete_key(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        prop = choice_of(p.get("property"), AA.PROPERTIES, "property")
        spec, fps = project._authoring_spec(False)
        if not AA.delete_key(spec, text(p.get("id"), "id", 80), prop, _finite(p.get("time"), "time"), fps=fps):
            raise NotFound("There is no key there")
        project._commit_animation(spec)
        return {}

    @staticmethod
    @command(_model_target("Move key"))
    def anim__move_key(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        prop = choice_of(p.get("property"), AA.PROPERTIES, "property")
        spec, fps = project._authoring_spec(False)
        to = AA.move_key(spec, text(p.get("id"), "id", 80), prop, _finite(p.get("time"), "time"), _finite(p.get("to"), "to"), fps=fps)
        project._commit_animation(spec)
        return {"time": to}

    @staticmethod
    @command(_model_target("Change interpolation"))
    def anim__interpolation(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        prop = choice_of(p.get("property"), AA.PROPERTIES, "property")
        spec, _ = project._authoring_spec(False)
        AA.set_interpolation(spec, text(p.get("id"), "id", 80), prop, choice_of(p.get("interpolation"), AA.INTERPOLATIONS, "interpolation"))
        project._commit_animation(spec)
        return {}

    @staticmethod
    @command(_model_target("Delete track"))
    def anim__delete_track(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        prop = choice_of(p.get("property"), AA.PROPERTIES, "property")
        spec, _ = project._authoring_spec(False)
        AA.delete_track(spec, text(p.get("id"), "id", 80), prop)
        project._commit_animation(spec)
        return {}

    @staticmethod
    @command(_model_target("Add marker"))
    def anim__marker(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        spec, fps = project._authoring_spec(True)
        mid = AA.add_marker(spec, _finite(p.get("time"), "time"), text(p.get("name") or "Marker", "name", 80), fps=fps)
        project._commit_animation(spec)
        return {"id": mid}

    @staticmethod
    @command(_model_target("Delete marker"))
    def anim__delete_marker(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        spec, _ = project._authoring_spec(False)
        AA.delete_marker(spec, text(p.get("marker"), "marker", 80))
        project._commit_animation(spec)
        return {}

    @staticmethod
    @command(_model_target("Animation preset"))
    def anim__preset(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """A common motion in one step: slide, spin, grow, appear or disappear between ``start`` and ``end`` seconds."""
        oid = project._animatable(p.get("id"))
        spec, fps = project._authoring_spec(True)
        start = AA.frame_time(_finite(p.get("start", 0.0), "start"), fps, spec["duration"])
        end = AA.frame_time(_finite(p.get("end", spec["duration"]), "end"), fps, spec["duration"])
        params = p.get("params") or {}
        if not isinstance(params, dict):
            raise BadRequest("params must be an object")
        notes = AA.apply_preset(spec, oid, choice_of(p.get("kind"), tuple(AA.PRESETS), "kind"), params, fps=fps, start=start, end=end)
        project._commit_animation(spec)
        return {"notes": notes}

    @staticmethod
    @command(_model_target("Animation settings"))
    def anim__settings(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Frame rate and loop (kept in the project manifest) and the length of the animation (kept in the SGEO animation)."""
        settings = dict(project.manifest.get("animation") or {})
        if p.get("fps") is not None:
            fps = p["fps"]
            if isinstance(fps, bool) or not isinstance(fps, int) or not AA.FPS_RANGE[0] <= fps <= AA.FPS_RANGE[1]:
                raise BadRequest(f"fps must be a whole number from {AA.FPS_RANGE[0]} to {AA.FPS_RANGE[1]}")
            settings["fps"] = fps
        if p.get("loop") is not None:
            settings["loop"] = boolean(p["loop"], "loop")
        if p.get("duration") is not None:
            duration = number(p["duration"], "duration", 0.1, 3600.0)
            settings["duration"] = duration
            spec = project.model.animation()
            if spec is not None:
                spec = AA.clean_spec(spec)
                spec["duration"] = duration
                last = max((k["time"] for t in spec["tracks"] for k in t["keyframes"]), default=0.0)
                if duration < last - 1e-9:
                    raise BadRequest(f"The animation cannot be shorter than its last key ({last:g} s)", hint="Move or delete the later keys first")
                project._commit_animation(spec)
        project.manifest["animation"] = settings
        return {"settings": project.animation_settings()}

    # -- project properties ------------------------------------------------------------------------------------------------
    @staticmethod
    @command(_model_target("Edit project properties"))
    def project__set(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """The project's name and description (the unit is fixed once drawings exist in it)."""
        if not any(k in p for k in ("name", "description", "client", "number")):
            raise BadRequest("Nothing to change: give a name, a description, a client or a project number")
        for key, limit in (("client", 80), ("number", 40)):
            if key in p:
                project.manifest[key] = text(p[key], key, limit, empty=True)                 # printed in every sheet's title block
        if "name" in p:
            project.manifest["name"] = text(p["name"], "name", 80)
        if "description" in p:
            project.manifest["description"] = text(p["description"], "description", 2000, empty=True, multiline=True)
        return {"name": project.name}

    # -- project structure -------------------------------------------------------------------------------------------------
    # -- documentation views -----------------------------------------------------------------------------------------------
    @staticmethod
    @command(_model_target("Add section"))
    def view__add_section(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """A section along the line a → b (drawing units), looking to the left of that line, ``depth`` beyond it."""
        to_m = MM_PER[project.manifest["unit"]] / 1000.0
        a, b = _point(p.get("a"), "a"), _point(p.get("b"), "b")
        if math.hypot(b[0] - a[0], b[1] - a[1]) * to_m < 0.01:
            raise BadRequest("A section line needs two points at least 10 mm apart")
        depth = number(p["depth"], "depth", 1e-6, 1e9) * to_m if p.get("depth") is not None else D.SECTION_DEPTH
        name = text(p["name"], "name", 80) if p.get("name") else _next_section_name(project)
        _unique_view_name(project, name)
        vid = new_id()
        project.manifest["views"].append({"id": vid, "kind": "section", "name": name, "a": [a[0] * to_m, a[1] * to_m], "b": [b[0] * to_m, b[1] * to_m], "depth": depth})
        project.drawings[vid] = SDRWDocument.new(name, unit=project.manifest["unit"])
        return {"id": vid, "name": name}

    @staticmethod
    @command(_model_target("Add elevation"))
    def view__add_elevation(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        side = choice_of(p.get("side"), tuple(D.SIDES), "side")
        if any(v["kind"] == "elevation" and v["side"] == side for v in project.manifest["views"]):
            raise Conflict(f"The {side} elevation already exists")
        name = side.title()
        _unique_view_name(project, name)
        vid = new_id()
        project.manifest["views"].append({"id": vid, "kind": "elevation", "name": name, "side": side})
        project.drawings[vid] = SDRWDocument.new(name, unit=project.manifest["unit"])
        return {"id": vid, "name": name}

    @staticmethod
    @command(_model_target("Add schedule"))
    def view__add_schedule(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        category = choice_of(p.get("category"), tuple(D.SCHEDULES), "category")
        if any(v["kind"] == "schedule" and v["category"] == category for v in project.manifest["views"]):
            raise Conflict(f"The {D.SCHEDULES[category]['title'].lower()} already exists")
        name = D.SCHEDULES[category]["title"]
        _unique_view_name(project, name)
        vid = new_id()
        project.manifest["views"].append({"id": vid, "kind": "schedule", "name": name, "category": category})
        return {"id": vid, "name": name}

    @staticmethod
    @command(_model_target("Rename view"))
    def view__rename(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        view = _editable_view(project, p.get("id"))
        name = text(p.get("name"), "name", 80)
        _unique_view_name(project, name, ignore=view["id"])
        view["name"] = name
        return {"id": view["id"]}

    @staticmethod
    @command(_model_target("Edit section"))
    def view__set_section(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        view = _editable_view(project, p.get("id"))
        if view["kind"] != "section":
            raise BadRequest("Only a section has a cut line and a depth")
        to_m = MM_PER[project.manifest["unit"]] / 1000.0
        if p.get("depth") is not None:
            view["depth"] = number(p["depth"], "depth", 0.01, 1e5)                      # metres: the panel shows metres
        for key in ("a", "b"):
            if p.get(key) is not None:
                x, y = _point(p[key], key)
                view[key] = [x * to_m, y * to_m]
        if p.get("angle") is not None:
            angle = math.radians(number(p["angle"], "section angle (degrees)", -36000, 36000))
            span = math.dist(view["a"], view["b"])
            cx = (view["a"][0] + view["b"][0]) / 2
            cy = (view["a"][1] + view["b"][1]) / 2
            vx, vy = math.cos(angle) * span / 2, math.sin(angle) * span / 2
            view["a"], view["b"] = [cx - vx, cy - vy], [cx + vx, cy + vy]
        if math.dist(view["a"], view["b"]) < 0.01:
            raise BadRequest("A section line needs two points at least 10 mm apart")
        return {"id": view["id"]}

    @staticmethod
    @command(_model_target("Open level plan"))
    def level__ensure_plan(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Create the missing plan of an existing level once, including legacy projects without a roof plan."""
        level = project.level(text(p.get("id"), "id", 80))
        existing = DT.plan_view(project, level["id"])
        if existing:
            return {"id": existing["id"], "name": existing["name"], "created": False}
        name = level["name"]
        taken = {v["name"].lower() for v in project.manifest["views"]}
        if name.lower() in taken:
            name += " (plan)"
        vid = new_id()
        project.manifest["views"].append({"id": vid, "kind": "plan", "name": name, "level_id": level["id"]})
        project.drawings[vid] = SDRWDocument.new(name, unit=project.manifest["unit"])
        return {"id": vid, "name": name, "created": True}

    @staticmethod
    @command(_model_target("Set plan cut plane"))
    def view__set_cut_plane(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        view = project.view(text(p.get("id"), "id", 80))
        if view["kind"] != "plan":
            raise BadRequest("Only a floor plan has a plan cut plane")
        key = choice_of(p.get("key"), ("cut_reference", "cut_offset", "range_top", "range_bottom", "range_depth"), "key")
        if key == "cut_reference":
            reference = text(p.get("value"), "reference level", 80)
            project.level(reference)
            view["cut_reference_level"] = reference
            view["cut_offset"] = 0.0 if reference != view["level_id"] else 1.5
        elif key == "cut_offset":
            view["cut_offset"] = number(p.get("value"), "cut offset (mm)", -1000000, 1000000) / 1000.0
        else:
            value = number(p.get("value"), f"{key} (mm)", -1000000, 1000000) / 1000.0
            if key == "range_top" and value < 0:
                raise BadRequest("Top of view range must be at or above the cut plane")
            if key == "range_bottom" and value > 0:
                raise BadRequest("Bottom of view range cannot exceed the associated plan level")
            if key == "range_depth" and value > float(view.get("range_bottom", -0.2)):
                raise BadRequest("View depth cannot be above the bottom of the view range")
            if key == "range_bottom" and value < float(view.get("range_depth", -1.0)):
                raise BadRequest("View range bottom cannot be below the view depth")
            view[key] = value
        # Reject an impossible range immediately, while command rollback still
        # protects the project, rather than failing on the next viewport load.
        ref = view.get("cut_reference_level") or view["level_id"]
        cut_z = project.level(ref)["elevation"] + float(view.get("cut_offset", 1.5))
        base_z = project.level(view["level_id"])["elevation"]
        bottom_z = base_z + float(view.get("range_bottom", -0.2))
        depth_z = base_z + float(view.get("range_depth", -1.0))
        if not (depth_z <= bottom_z <= cut_z):
            raise BadRequest("Plan view range must satisfy view depth ≤ bottom ≤ cut plane")
        return {"id": view["id"], "reference": ref, "offset": view.get("cut_offset", 1.5)}

    @staticmethod
    @command(_model_target("View option"))
    def view__set_option(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Per-view drawing options, for floor plans, sections and elevations. ``opening_direction``: whether the doors' and windows'
        opening-direction symbols are drawn (plan arcs, or the solid/dashed lines of a section or elevation). ``material_hatching``: whether
        cut elements are hatched by their material (off: plain filled cut)."""
        view = project.view(text(p.get("id"), "id", 80))
        if view["kind"] not in ("plan", "section", "elevation"):
            raise BadRequest(f"'{view['name']}' is a {view['kind']} view; it draws no door or window symbols")
        key = text(p.get("key"), "key", 40)
        field = VIEW_OPTIONS.get(key)
        if field is None:
            raise BadRequest(f"'{key}' is not a view option", hint="The options are " + ", ".join(VIEW_OPTIONS))
        value = p.get("value")
        view[field] = boolean(value, "value") if not isinstance(value, str) else choice_of(value, ("shown", "hidden"), "value") == "shown"
        return {"id": view["id"], field: view[field]}

    @staticmethod
    @command(_model_target("Cut hatching"))
    def hatch__set(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Assign a hatch pattern to a material for the whole project (every section and plan). ``pattern`` null returns the material to its
        default. The patterns are SPADRA's configurable ones, not NEN 47 (see ``documentation.HATCH_NOTE``)."""
        material = choice_of(p.get("material"), tuple(D.DEFAULT_HATCH), "material")
        table = project.manifest.setdefault("hatching", {})
        pattern = p.get("pattern")
        if pattern in (None, "", "default"):
            table.pop(material, None)
        else:
            table[material] = choice_of(pattern, tuple(D.HATCH_PATTERNS), "pattern")
        return {"material": material, "pattern": (D.hatch_pattern(project, material) or {}).get("pattern")}

    @staticmethod
    @command(_model_target("Flip section"))
    def view__flip_section(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        view = _editable_view(project, p.get("id"))
        if view["kind"] != "section":
            raise BadRequest("Only a section can be flipped")
        view["a"], view["b"] = view["b"], view["a"]
        return {"id": view["id"]}

    @staticmethod
    @command(_model_target("Delete view"))
    def view__delete(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        view = _editable_view(project, p.get("id"))
        for sheet in project.manifest["sheets"]:
            if any(vp.get("view_id") == view["id"] for vp in sheet.get("viewports", [])):
                raise Conflict(f"{view['name']} is placed on sheet {sheet['number']}", hint="Remove its viewport from the sheet first")
        project.manifest["views"].remove(view)                       # its drawing stays in memory so that Undo brings the whole view back
        return {"removed": view["id"]}

    @staticmethod
    @command(_model_target("Add sheet"))
    def sheet__create(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        number_ = text(p.get("number") or f"A{101 + len(project.manifest['sheets'])}", "number", 20)
        if any(s["number"] == number_ for s in project.manifest["sheets"]):
            raise Conflict(f"A sheet numbered {number_} already exists")
        sheet = {"id": new_id(), "number": number_, "name": text(p.get("name", ""), "name", 80, empty=True), "paper": "A1 Landscape",
                 "scale": "1:100", "revision": "01", "viewports": [], "titleblock": SH.blank_titleblock(), "notes": [], "revisions": []}
        project.manifest["sheets"].append(sheet)
        return {"id": sheet["id"]}

    @staticmethod
    @command(_model_target("Edit sheet"))
    def sheet__set(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        sheet = SH.find(project, p.get("id"))
        key, value = text(p.get("key"), "key", 20), p.get("value")
        if key == "number":
            value = text(value, "number", 20)
            if any(s["number"] == value and s is not sheet for s in project.manifest["sheets"]):
                raise Conflict(f"A sheet numbered {value} already exists")
        elif key == "name":
            value = text(value, "name", 80, empty=True)
        elif key == "paper":
            if isinstance(value, str) and value.startswith("Custom "):
                width, height = paper_size(value)
                if not 210 <= width <= 5000 or not 210 <= height <= 5000:
                    raise BadRequest("Custom sheet dimensions must be 210–5000 mm on each side")
            else:
                value = choice_of(value, PAPERS, "paper")
        elif key == "scale":
            value = choice_of(value, SCALES, "scale")
        elif key == "revision":
            if sheet["revisions"]:
                raise Conflict("This sheet has an issued revision history, so its revision follows it", hint="Issue a new revision instead")
            value = text(value, "revision", 20)
        else:
            SH.set_field(project, sheet, key, value)
            return {"id": sheet["id"]}
        sheet[key] = value
        return {"id": sheet["id"]}

    @staticmethod
    @command(_model_target("Delete sheet"))
    def sheet__delete(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        sheet = SH.find(project, p.get("id"))
        if sheet["revisions"] and not p.get("confirm"):
            raise Conflict(f"Sheet {sheet['number']} has {len(sheet['revisions'])} issued revision(s)", hint="Confirm to delete it with its history")
        project.manifest["sheets"].remove(sheet)
        return {"removed": sheet["id"]}

    @staticmethod
    @command(_model_target("Issue revision"))
    def sheet__issue(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Issue a revision of a sheet: record its label, description, author and date, and fingerprint what the sheet shows."""
        return SH.issue(project, SH.find(project, p.get("id")), p)

    @staticmethod
    @command(_model_target("Add sheet note"))
    def sheet__add_note(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        sheet = SH.find(project, p.get("id"))
        return {"note": SH.add_note(sheet, p, *paper_size(sheet["paper"]))}

    @staticmethod
    @command(_model_target("Edit sheet note"))
    def sheet__set_note(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        sheet = SH.find(project, p.get("id"))
        SH.set_note(sheet, p, *paper_size(sheet["paper"]))
        return {"note": p.get("note")}

    @staticmethod
    @command(_model_target("Remove sheet note"))
    def sheet__remove_note(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        sheet = SH.find(project, p.get("id"))
        sheet["notes"].remove(SH.note_of(sheet, p.get("note")))
        return {"removed": p.get("note")}

    @staticmethod
    @command(_model_target("Place view"))
    def sheet__place_view(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Put a drawing, a schedule or the 3D model on a sheet. The viewport remembers which drawing point sits at (x, y) on the paper, so it stays put as
        the drawing grows. A 3D view is placed as the model seen from ``azimuth`` and ``elevation`` (degrees), drawn as vector shapes at the viewport's scale."""
        sheet = _sheet(project, p.get("id"))
        view_id = text(p.get("view_id"), "view_id", 80)
        view = project.view(view_id)
        width, height = paper_size(sheet["paper"])
        dx, dy = SV.cascade(sheet, width, height)
        vp = {"id": new_id(), "view_id": view_id, "x": number(p.get("x", dx), "x", -1e5, 1e5), "y": number(p.get("y", dy), "y", -1e5, 1e5),
              "scale": choice_of(p.get("scale", sheet["scale"]), SCALES, "scale")}
        if "x" in p or "y" in p:
            vp["manual"] = True
        if view["kind"] == "3d":
            az, el = SV.check_camera(p.get("azimuth", SV.DEFAULT_AZIMUTH), p.get("elevation", SV.DEFAULT_ELEVATION))
            shown = SV.model_view(project, az, el, 1000.0 / MM_PER[project.manifest["unit"]])
            if not shown["bounds"]:
                raise BadRequest("The model has nothing to show yet")
            vp.update({"kind": "model", "azimuth": az, "elevation": el, "anchor": [float(shown["bounds"][0][0]), float(shown["bounds"][0][1])]})
        elif view["kind"] == "schedule":
            vp["anchor"] = [0.0, 0.0]
        else:
            data = project.drawing_data(view_id)                      # the extent of what the view shows, drawing and model together
            vp["anchor"] = [float(data["bounds"][0][0]), float(data["bounds"][0][1])] if data["bounds"] else [0.0, 0.0]
        crowded = "x" not in p and "y" not in p and not SV.place_free(project, sheet, vp)
        sheet.setdefault("viewports", []).append(vp)
        return {"id": vp["id"], "crowded": crowded}

    @staticmethod
    @command(_model_target("Place 3D snapshot"))
    def sheet__place_snapshot(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Put a picture of the 3D view on a sheet. ``data`` is the PNG (base64) the browser drew; ``camera`` records where it was taken from. The picture is
        stored in the project and does not change when the model does."""
        sheet = _sheet(project, p.get("id"))
        data, w_px, h_px = SV.decode_png(p.get("data"))
        camera = p.get("camera") if isinstance(p.get("camera"), dict) else {}
        shot = {k: (number(camera[k], k, -1e6, 1e6) if k in camera else None) for k in ("azimuth", "elevation")}
        paper_w, paper_h = paper_size(sheet["paper"])
        wide = number(p.get("width", min(0.4 * paper_w, 0.7 * paper_h * w_px / h_px)), "width", 10, paper_w)
        dx, dy = SV.cascade(sheet, paper_w, paper_h)
        vp = {"id": new_id(), "kind": "snapshot", "x": number(p.get("x", dx), "x", -1e5, 1e5), "y": number(p.get("y", dy), "y", -1e5, 1e5),
              "width": wide, "title": text(p.get("title", ""), "title", 80, empty=True), "image": SV.image_entry(data, w_px, h_px, shot, project.revision)}
        project.snapshots[vp["id"]] = data
        crowded = False
        if "x" in p or "y" in p:
            vp["manual"] = True
        else:
            crowded = not SV.place_free(project, sheet, vp)
        sheet.setdefault("viewports", []).append(vp)
        return {"id": vp["id"], "bytes": len(data), "crowded": crowded}

    @staticmethod
    @command(_model_target("Place report"))
    def sheet__place_report(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Put the text and tables of a report on a sheet, in a box ``width`` mm wide with text ``size`` mm high and at most ``lines`` lines."""
        sheet = _sheet(project, p.get("id"))
        entry = RP.find(project, p.get("report"))
        paper_w, paper_h = paper_size(sheet["paper"])
        w, size, lines = SV.check_report_box(p.get("width", 0.4 * paper_w), p.get("size", 2.6), p.get("lines", 60), paper_w)
        dx, dy = SV.cascade(sheet, paper_w, paper_h)
        vp = {"id": new_id(), "kind": "report", "report_id": entry["id"], "x": number(p.get("x", dx), "x", -1e5, 1e5), "y": number(p.get("y", dy), "y", -1e5, 1e5),
              "width": w, "size": size, "lines": lines, "title": text(p.get("title", ""), "title", 80, empty=True)}
        SV.report_box(project, vp)                                    # read it back now: a report that cannot be laid out is refused here, not when the sheet is drawn
        crowded = False
        if "x" in p or "y" in p:
            vp["manual"] = True
        else:
            crowded = not SV.place_free(project, sheet, vp)
        sheet.setdefault("viewports", []).append(vp)
        return {"id": vp["id"], "crowded": crowded}

    @staticmethod
    @command(_model_target("Edit viewport"))
    def sheet__set_viewport(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        sheet = _sheet(project, p.get("id"))
        vp = _viewport(sheet, p.get("viewport"))
        kind = SV.kind_of(vp)
        key, value = text(p.get("key"), "key", 20), p.get("value")
        paper_w, _ = paper_size(sheet["paper"])
        if key == "scale" and kind in ("view", "model"):
            vp["scale"] = choice_of(value, SCALES, "scale")
        elif key == "title":
            vp["title"] = text(value, "title", 80, empty=True)                        # printed under the view; empty uses the view's name
        elif key in ("x", "y"):
            if vp.get("locked"):
                raise Conflict("That viewport is locked", hint="Unlock it to move it")
            vp[key] = number(value, key, -1e5, 1e5)
            vp["manual"] = True                                                        # placed by hand: Arrange leaves it where it is unless asked to move everything
        elif key == "position":                                                        # a drop: [x, y], snapping to edges when asked
            if not isinstance(value, (list, tuple)) or len(value) != 2:
                raise BadRequest("position is [x, y]")
            x, y = number(value[0], "x", -1e5, 1e5), number(value[1], "y", -1e5, 1e5)
            if vp.get("locked"):
                raise Conflict("That viewport is locked", hint="Unlock it to move it")
            vp["x"], vp["y"] = SV.snap(project, sheet, vp, x, y) if p.get("snap") else (x, y)
            vp["manual"] = True
        elif key == "locked":
            if value not in (True, False, "locked", "free"):
                raise BadRequest("locked must be true, false, 'locked' or 'free'")
            vp["locked"] = value is True or value == "locked"
        elif kind == "model" and key in ("azimuth", "elevation"):
            az, el = SV.check_camera(value if key == "azimuth" else vp["azimuth"], value if key == "elevation" else vp["elevation"])
            shown = SV.model_view(project, az, el, 1000.0 / MM_PER[project.manifest["unit"]])
            if shown["bounds"]:
                vp["anchor"] = [float(shown["bounds"][0][0]), float(shown["bounds"][0][1])]
            vp["azimuth"], vp["elevation"] = az, el
        elif kind == "snapshot" and key == "width":
            vp["width"] = number(value, "width", 10, paper_w)
        elif kind == "report" and key in ("width", "size", "lines"):
            vp["width"], vp["size"], vp["lines"] = SV.check_report_box(value if key == "width" else vp["width"], value if key == "size" else vp["size"], value if key == "lines" else vp["lines"], paper_w)
            SV.report_box(project, vp)
        else:
            raise BadRequest(f"'{key}' is not an editable property of this viewport")
        return {"id": vp["id"]}

    @staticmethod
    @command(_model_target("Import sheet"))
    def sheet__import(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Make a new sheet from a ``.sldc`` sheet file (``data`` is the file, base64). Viewports whose views are not in this project are listed, not guessed."""
        import base64
        import binascii
        try:
            raw = base64.b64decode(str(p.get("data", "")), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise BadRequest("data must be the .sldc file, base64 encoded") from exc
        return SH.import_sldc(project, raw)

    @staticmethod
    @command(_model_target("Arrange sheet"))
    def sheet__arrange(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Lay the sheet's viewports out so that all are visible and none overlaps another or the title block. ``mode`` ``auto`` (default) leaves locked viewports and
        those placed by hand where they are, ``all`` moves everything that is not locked; ``rescale`` lets a view that cannot otherwise fit take a smaller scale."""
        sheet = _sheet(project, p.get("id"))
        return SV.arrange(project, sheet, mode=str(p.get("mode", "auto")), rescale=boolean(p.get("rescale", False), "rescale"))

    @staticmethod
    @command(_model_target("Align viewports"))
    def sheet__align(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        sheet = _sheet(project, p.get("id"))
        ids = p.get("viewports")
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise BadRequest("viewports must be a list of viewport ids")
        return {"moved": SV.align(project, sheet, ids, str(p.get("how", "")))}

    @staticmethod
    @command(_model_target("Reorder viewport"))
    def sheet__order_viewport(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        """Change what is drawn on top: ``to`` is front, back, forward or backward."""
        sheet = _sheet(project, p.get("id"))
        vp = _viewport(sheet, p.get("viewport"))
        items = sheet["viewports"]
        i = items.index(vp)
        j = {"front": len(items) - 1, "back": 0, "forward": min(i + 1, len(items) - 1), "backward": max(i - 1, 0)}.get(str(p.get("to")))
        if j is None:
            raise BadRequest("to must be front, back, forward or backward")
        items.insert(j, items.pop(i))
        return {"order": [v["id"] for v in items]}

    @staticmethod
    @command(_model_target("Remove viewport"))
    def sheet__remove_viewport(project: Project, p: dict[str, Any]) -> dict[str, Any]:
        sheet = _sheet(project, p.get("id"))
        vp = _viewport(sheet, p.get("viewport"))
        sheet["viewports"].remove(vp)
        return {"removed": vp["id"]}


def _curve(doc: SDRWDocument, eid: Any, kinds: tuple[str, ...], verb: str) -> dict[str, Any]:
    """An entity of one of ``kinds`` or a plain-language refusal."""
    eid = text(eid, "id", 80)
    try:
        e = doc.get(eid)
    except SpatialFormatError as exc:
        raise NotFound(f"Entity {eid} does not exist in this drawing") from exc
    if e["kind"] not in kinds:
        names = ", ".join(k + "s" for k in kinds)
        raise BadRequest(f"{verb} works on {names}, not on a {e['kind']}")
    return e


def _chain_of(e: Mapping[str, Any]) -> tuple[list[tuple[float, float]], bool]:
    g = e["geometry"]
    if e["kind"] == "line":
        return [tuple(g["start"]), tuple(g["end"])], False        # type: ignore[list-item]
    return [tuple(q) for q in g["points"]], bool(g.get("closed"))  # type: ignore[misc]


def _chain_geometry(e: Mapping[str, Any], pts: list[tuple[float, float]], closed: bool) -> dict[str, Any]:
    if e["kind"] == "line":
        return {"start": list(pts[0]), "end": list(pts[-1])}
    return {"points": [list(q) for q in pts], "closed": closed}


def _cutters(doc: SDRWDocument, target: str, edges: Any) -> list[tuple]:
    """Cutting/boundary edges for trim and extend: the ones named, or - like most CAD programs - every other curve in the drawing."""
    ids = _ids(edges, "edges") if edges else [i for i in all_ids(doc) if i != target]
    out: list[tuple] = []
    for i in ids:
        if i == target:
            continue
        e = doc.get(i)
        out += G.cutters_for(e["kind"], e["geometry"])
    return out


def _write_pieces(doc: SDRWDocument, e: Mapping[str, Any], pieces: list[list[tuple[float, float]]]) -> list[str]:
    """Replace a trimmed entity by what is left of it: the first piece keeps its id, a second becomes a new entity on the same layer."""
    if not pieces:
        doc.remove([e["id"]])
        return []
    doc.update(e["id"], geometry=_chain_geometry(e, pieces[0], False))
    ids = [e["id"]]
    for piece in pieces[1:]:
        if e["kind"] == "line":
            ids.append(doc.add_line(piece[0], piece[-1], layer=e["layer"]))
        else:
            ids.append(doc.add_polyline(piece, closed=False, layer=e["layer"]))
    return ids


def _mirrored(e: Mapping[str, Any], a: tuple[float, float], b: tuple[float, float]) -> dict[str, Any]:
    """The geometry of ``e`` reflected across the line ab. Text keeps reading left to right, as in other CAD programs."""
    kind, g = e["kind"], dict(e["geometry"])
    r = lambda q: list(G.reflect(tuple(q), a, b))                       # noqa: E731
    phi = G.axis_angle(a, b)
    if kind == "line":
        g["start"], g["end"] = r(g["start"]), r(g["end"])
    elif kind == "polyline":
        g["points"] = [r(q) for q in g["points"]]
    elif kind == "circle":
        g["center"] = r(g["center"])
    elif kind == "arc":
        g["center"] = r(g["center"])
        start, end = 2 * phi - g["end_angle"], 2 * phi - g["start_angle"]
        while end <= start:
            end += 2 * math.pi
        g["start_angle"], g["end_angle"] = start, end
    elif kind == "text":
        g["position"] = r(g["position"])
    elif kind == "dimension" and g.get("dim_type") == "aligned":
        for key in ("p1", "p2", "dimline_point"):
            g[key] = r(g[key])
    elif kind == "hatch":
        loops = []
        for loop in g["boundary"]:
            loop = dict(loop)
            if loop["kind"] == "polygon":
                loop["points"] = [r(q) for q in loop["points"]]
            elif loop["kind"] == "circle":
                loop["center"] = r(loop["center"])
            else:
                raise BadRequest("Only hatches with polygon or circle outlines can be mirrored")
            loops.append(loop)
        g["boundary"] = loops
        if "angle" in g:
            g["angle"] = 2 * phi - g["angle"]
    else:
        raise BadRequest(f"A {kind} cannot be mirrored")
    return g


def _viewport_rows(project: Project, vp: dict[str, Any]) -> list[dict[str, Any]]:
    """The Properties rows of one viewport on a sheet, by what it shows, then where it is on the paper and whether it is locked there."""
    return _viewport_content_rows(project, vp) + _viewport_place_rows(project, vp)


def _viewport_place_rows(project: Project, vp: dict[str, Any]) -> list[dict[str, Any]]:
    kind = SV.kind_of(vp)
    if kind == "snapshot":
        name = vp.get("title") or "3D snapshot"
    elif kind == "report":
        name = next((r["name"] for r in project.manifest["reports"] if r["id"] == vp["report_id"]), "(missing report)")
    else:
        name = project.view(vp["view_id"])["name"]
        name = SV.model_name({"name": name}) if kind == "model" else name
    key = lambda field: f"vp:{vp['id']}:{field}"
    return [{"key": key("x"), "label": f"{name}: x", "type": "number", "unit": "mm", "value": vp["x"]},
            {"key": key("y"), "label": f"{name}: y", "type": "number", "unit": "mm", "value": vp["y"]},
            {"key": key("locked"), "label": f"{name}: position", "type": "choice", "value": "locked" if vp.get("locked") else "free",
             "choices": [{"value": "free", "label": "Free to move"}, {"value": "locked", "label": "Locked"}]}]


def _viewport_content_rows(project: Project, vp: dict[str, Any]) -> list[dict[str, Any]]:
    kind = SV.kind_of(vp)
    key = lambda field: f"vp:{vp['id']}:{field}"
    scale = {"key": key("scale"), "type": "choice", "value": vp.get("scale"), "choices": [{"value": c, "label": c} for c in SCALES]}
    if kind == "snapshot":
        return [{"key": key("width"), "label": f"{vp.get('title') or '3D snapshot'}: width", "type": "number", "unit": "mm", "value": vp["width"]}]
    if kind == "report":
        entry = next((r for r in project.manifest["reports"] if r["id"] == vp["report_id"]), None)
        label = entry["name"] if entry else "(missing report)"
        return [{"key": key("width"), "label": f"{label}: width", "type": "number", "unit": "mm", "value": vp["width"]},
                {"key": key("size"), "label": f"{label}: text height", "type": "number", "unit": "mm", "value": vp["size"]},
                {"key": key("lines"), "label": f"{label}: lines", "type": "number", "value": vp["lines"]}]
    name = project.view(vp["view_id"])["name"]
    if kind == "model":
        name = SV.model_name({"name": name})
        return [{**scale, "label": f"{name}: scale"},
                {"key": key("azimuth"), "label": f"{name}: azimuth", "type": "number", "unit": "deg", "value": vp["azimuth"]},
                {"key": key("elevation"), "label": f"{name}: elevation", "type": "number", "unit": "deg", "value": vp["elevation"]}]
    return [{**scale, "label": name}]


def _sheet(project: Project, sheet_id: Any) -> dict[str, Any]:
    return SH.find(project, sheet_id)


def _viewport(sheet: dict[str, Any], vid: Any) -> dict[str, Any]:
    vp = next((v for v in sheet.get("viewports", []) if v["id"] == vid), None)
    if vp is None:
        raise NotFound("That viewport is not on this sheet")
    return vp


def choice_of(value: Any, allowed: Iterable[str], label: str) -> str:
    if value not in tuple(allowed):
        raise BadRequest(f"{label} must be one of {', '.join(allowed)}")
    return str(value)


class ProjectCommitLedger:
    """``applications.tools.commit_host.CommitLedger`` over a project's manifest."""

    def __init__(self, project: Project) -> None:
        self.project = project

    def get(self, key: str) -> dict[str, Any] | None:
        entry = self.project.manifest.get("commits", {}).get(key)
        return _clone(entry) if entry is not None else None

    def put(self, key: str, entry: dict[str, Any]) -> None:
        self.project.manifest.setdefault("commits", {})[key] = _clone(entry)
        self.project.dirty = True                                    # a decision was made; it is saved with the project
