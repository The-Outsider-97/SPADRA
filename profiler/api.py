"""Profiler's HTTP API. Thin, like the rest of SPADRA's routes: unpack, call, return. The rules live in ``session``, ``sdrw_io`` and ``transfer``.

Open profiles live in memory (``session.STORE``); the file on disk changes only when the person saves. Sending to the model is one SPADRA command, so the
model either gets the whole result or is left exactly as it was.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ...tools.errors import BadRequest, Conflict, NotFound
from ...tools.server import Request, created
from ...tools.validation import boolean, choice, text
from . import sdrw_io as IO
from . import templates as TP
from . import transfer as T
from . import validate as V
from .errors import ProfilerConflict, ProfilerError, ProfilerNotFound
from .model import Sketch
from .session import OPERATIONS, STORE, Session

UNITS = ("mm", "cm", "m", "in", "ft")


def _dirs(app) -> tuple[Path, Path]:
    root = app.settings.data_dir / "profiler"
    return root / "profiles", root / "autosave"


def _resolve(app, path: str) -> str:
    """A bare or relative name lands in the profiles folder; an absolute path is used as given."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        folder = _dirs(app)[0]
        folder.mkdir(parents=True, exist_ok=True)
        p = folder / p
    return str(p)


def _saved_profiles(app) -> list[dict[str, Any]]:
    """The .sdrw files in the profiles folder, newest first: what the Open dialog offers, so nobody has to type a path."""
    folder = _dirs(app)[0]
    out = []
    if folder.is_dir():
        for f in folder.glob("*.sdrw"):
            try:
                st = f.stat()
            except OSError:
                continue
            out.append({"name": f.name, "path": str(f), "modified": st.st_mtime, "size": st.st_size})
    return sorted(out, key=lambda r: -r["modified"])


def _free_name(folder: Path, name: str) -> Path:
    stem, suffix = Path(name).stem or "Profile", ".sdrw"
    p = folder / (stem + suffix)
    n = 2
    while p.exists():
        p = folder / ("%s (%d)%s" % (stem, n, suffix))
        n += 1
    return p


def _view(S: Session, *, full: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {"status": S.status()}
    if full:
        out["render"] = S.run("render", {})["result"]
        out["params"] = S.run("params", {})["result"]
        out["diagnostics"] = S.run("diagnostics", {})["result"]
    return out


def register(router) -> None:
    from ..app import SpadraApp

    @router.route("GET", "/api/profiler")
    def profiler_info(app: SpadraApp, req: Request) -> Any:
        with app.lock:
            profiles, auto = _dirs(app)
            return {"templates": TP.listing(), "units": list(UNITS), "operations": sorted(k for k in OPERATIONS), "kinds": list(T.KINDS), "modes": list(T.MODES),
                    "open": [s.status() for s in STORE.all()], "profiles": _saved_profiles(app), "folders": {"profiles": str(profiles), "autosave": str(auto)},
                    "recoverable": IO.recoverable(auto), "purposes": list(V.PURPOSES) if hasattr(V, "PURPOSES") else [],
                    "project": ({"open": True, "unit": app.project.manifest["unit"], "name": app.project.name,
                                 "levels": [{"id": l["id"], "name": l.get("name")} for l in app.project.manifest.get("levels", [])]} if app.project else {"open": False})}

    @router.route("POST", "/api/profiler/new")
    def profiler_new(app: SpadraApp, req: Request) -> Any:
        b = req.json()
        unit = choice(b.get("unit") or (app.project.manifest["unit"] if app.project else "mm"), UNITS, "unit")
        with app.lock:
            S = STORE.add(TP.build(choice(b.get("template", "blank"), tuple(k for k in TP.TEMPLATES), "template"), text(b.get("name", "Profile"), "name", 80), unit, b.get("values")))
            S.meta["autosave_dir"] = str(_dirs(app)[1])
            return created(_view(S))

    @router.route("POST", "/api/profiler/open")
    def profiler_open(app: SpadraApp, req: Request) -> Any:
        b = req.json()
        with app.lock:
            if b.get("recover"):
                S, rep = IO.recover(text(b["recover"], "recover", 1024))
            elif b.get("project_object"):                                        # edit the profile an object was made from
                project = app.require()
                from .. import elements as E
                oid = text(b["project_object"], "project_object", 80)
                if not E.alive(project, oid):
                    raise NotFound("Element %s does not exist" % oid)
                link = E.m(E.meta_of(project, oid), T.LINK_KEY)
                if not isinstance(link, dict):
                    raise ProfilerError("this object was not made from a Profiler profile")
                sk, info = T.source_sketch(link)
                S = Session(sk, path=link.get("file") if link.get("mode") == "linked" else None)
                S.saved_digest = sk.digest()
                S.meta["linked_object"] = oid
                rep = {"kind": "profiler", "warnings": info.get("warnings", []), "notes": [], "linked_object": oid, **info}
            else:
                S, rep = IO.open_file(_resolve(app, text(b.get("path"), "path", 1024)))
            S.meta["autosave_dir"] = str(_dirs(app)[1])
            STORE.add(S)
            return {**_view(S), "report": rep}

    @router.route("POST", "/api/profiler/import")
    def profiler_open_upload(app: SpadraApp, req: Request) -> Any:
        """Open a .sdrw the person picked on their computer with the browser's file chooser. A copy lands in the profiles folder (never overwriting a file there)."""
        raw = req.bytes(64 * 1024 * 1024)
        name = Path(text(req.query.get("name") or "Profile.sdrw", "name", 200)).name
        if Path(name).suffix.lower() != ".sdrw":
            raise ProfilerError("Profiler files use the .sdrw extension")
        if not raw:
            raise ProfilerError("that file is empty")
        with app.lock:
            folder = _dirs(app)[0]
            folder.mkdir(parents=True, exist_ok=True)
            same = next((f for f in folder.glob("*.sdrw") if f.stat().st_size == len(raw) and f.read_bytes() == raw), None)     # the same file again: open the copy already there
            dest = same or _free_name(folder, name)
            if not same:
                dest.write_bytes(raw)
            try:
                S, rep = IO.open_file(str(dest))
            except Exception:
                if not same:
                    dest.unlink(missing_ok=True)
                raise
            S.meta["autosave_dir"] = str(_dirs(app)[1])
            STORE.add(S)
            return {**_view(S), "report": rep}

    @router.route("GET", "/api/profiler/{sid}")
    def profiler_state(app: SpadraApp, req: Request) -> Any:
        with app.lock:
            return _view(STORE.get(req.params["sid"]))

    @router.route("POST", "/api/profiler/{sid}/op")
    def profiler_op(app: SpadraApp, req: Request) -> Any:
        b = req.json()
        with app.lock:
            S = STORE.get(req.params["sid"])
            name = text(b.get("op"), "op", 60)
            args = b.get("args") or {}
            if not isinstance(args, dict):
                raise BadRequest("args must be an object")
            expected = b.get("expected_revision")
            if expected is not None and S.sk.d["revision"] != int(expected):
                raise ProfilerConflict("the profile changed (now revision %d; you worked from %s)" % (S.sk.d["revision"], expected))
            out = S.run(name, args)
            if boolean(b.get("view", out.get("changed", False)), "view"):
                out.update({k: v for k, v in _view(S).items() if k != "status"})
            return out

    @router.route("POST", "/api/profiler/{sid}/undo")
    def profiler_undo(app: SpadraApp, req: Request) -> Any:
        with app.lock:
            S = STORE.get(req.params["sid"])
            return {**S.undo_last(), **_view(S)}

    @router.route("POST", "/api/profiler/{sid}/redo")
    def profiler_redo(app: SpadraApp, req: Request) -> Any:
        with app.lock:
            S = STORE.get(req.params["sid"])
            return {**S.redo_last(), **_view(S)}

    @router.route("POST", "/api/profiler/{sid}/repeat")
    def profiler_repeat(app: SpadraApp, req: Request) -> Any:
        b = req.json()
        with app.lock:
            S = STORE.get(req.params["sid"])
            out = S.repeat(b.get("overrides"))
            return {**out, **{k: v for k, v in _view(S).items() if k != "status"}}

    # ------------------------------------------------------------------------------------------------------ files
    @router.route("POST", "/api/profiler/{sid}/save")
    def profiler_save(app: SpadraApp, req: Request) -> Any:
        b = req.json()
        with app.lock:
            S = STORE.get(req.params["sid"])
            path = _resolve(app, text(b["path"], "path", 1024)) if b.get("path") else None
            res = IO.save(S, path, overwrite=b.get("overwrite"), force=boolean(b.get("force", False), "force"))
            IO.discard_autosave(S)
            return {**res, "status": S.status()}

    @router.route("POST", "/api/profiler/{sid}/autosave")
    def profiler_autosave(app: SpadraApp, req: Request) -> Any:
        with app.lock:
            S = STORE.get(req.params["sid"])
            return {"autosaved": IO.autosave(S, _dirs(app)[1]), "status": S.status()}

    @router.route("POST", "/api/profiler/{sid}/import")
    def profiler_import(app: SpadraApp, req: Request) -> Any:
        b = req.json()
        return _import_into(app, req.params["sid"], _resolve(app, text(b.get("path"), "path", 1024)), b)

    @router.route("POST", "/api/profiler/{sid}/import_upload")
    def profiler_import_upload(app: SpadraApp, req: Request) -> Any:
        """Import a .sdrw, .dxf or .svg picked with the browser's file chooser (the bytes are the request body); nothing is kept on disk."""
        import tempfile
        raw = req.bytes(64 * 1024 * 1024)
        name = Path(text(req.query.get("name") or "drawing.sdrw", "name", 200)).name
        if not raw:
            raise ProfilerError("that file is empty")
        with tempfile.TemporaryDirectory(prefix="spadra_import_") as tmp:
            f = Path(tmp) / name
            f.write_bytes(raw)
            return _import_into(app, req.params["sid"], str(f), {"insert": req.query.get("insert") == "1", "unit": req.query.get("unit") or None})

    def _import_into(app, sid: str, path: str, b: dict) -> Any:
        with app.lock:
            S = STORE.get(sid)
            sk, rep = IO.import_file(path, unit=b.get("unit"))
            if boolean(b.get("insert", False), "insert"):
                with S.lock:
                    before = S.sk.clone()
                    try:
                        info = IO.insert_sketch(S.sk, sk, tuple(b.get("at") or (0.0, 0.0)))
                        S.sk.validate_structure()
                    except BaseException:
                        S.sk.restore(before)
                        raise
                    S.sk.d["revision"] += 1
                    S.undo.append(("Import drawing", before.d))
                    S.redo.clear()
                    S.dirty = True
                return {"imported": {**rep, **info}, **_view(S)}
            new = Session(sk)
            new.dirty, new.saved_digest = True, None
            new.meta["autosave_dir"] = str(_dirs(app)[1])
            STORE.add(new)
            return {"imported": rep, **_view(new)}

    @router.route("POST", "/api/profiler/{sid}/export")
    def profiler_export(app: SpadraApp, req: Request) -> Any:
        b = req.json()
        fmt = choice(b.get("format", "sdrw"), ("sdrw", "svg", "dxf"), "format")
        with app.lock:
            S = STORE.get(req.params["sid"])
            path = _resolve(app, text(b.get("path"), "path", 1024))
            over = boolean(b.get("overwrite", False), "overwrite")
            return IO.save_copy(S, path, overwrite=over) if fmt == "sdrw" else IO.export_drawing(S, path, fmt, overwrite=over)

    @router.route("POST", "/api/profiler/{sid}/report")
    def profiler_report(app: SpadraApp, req: Request) -> Any:
        b = req.json()
        with app.lock:
            S = STORE.get(req.params["sid"])
            purpose = choice(b.get("purpose", "drafting"), tuple(getattr(V, "PURPOSES", ("drafting",))), "purpose")
            if b.get("path"):
                return IO.export_report(S, _resolve(app, text(b["path"], "path", 1024)), choice(b.get("format", "html"), ("html", "md", "json"), "format"),
                                        purpose=purpose, overwrite=boolean(b.get("overwrite", False), "overwrite"))
            return IO.report_data(S, purpose)

    @router.route("POST", "/api/profiler/{sid}/close")
    def profiler_close(app: SpadraApp, req: Request) -> Any:
        b = req.json()
        with app.lock:
            S = STORE.get(req.params["sid"])
            if S.dirty and not boolean(b.get("discard", False), "discard"):
                raise ProfilerConflict("the profile has unsaved changes", details={"unsaved": True, "name": S.sk.d["name"]})
            IO.discard_autosave(S, _dirs(app)[1])
            STORE.close(S.id)
            return {"closed": S.id}

    # ------------------------------------------------------------------------------------------------ hand-off
    def _options(b: dict, project) -> dict[str, Any]:
        o = dict(b.get("options") or {})
        o["project_unit"] = project.manifest["unit"]
        return o

    @router.route("POST", "/api/profiler/{sid}/check")
    def profiler_check(app: SpadraApp, req: Request) -> Any:
        b = req.json()
        with app.lock:
            S = STORE.get(req.params["sid"])
            return S.run("check", {"purpose": b.get("purpose", "drafting"), **{k: b[k] for k in ("axis", "gap_tol") if k in b}})["result"]

    @router.route("POST", "/api/profiler/{sid}/send")
    def profiler_send(app: SpadraApp, req: Request) -> Any:
        """Upload to SPADRA. ``close`` closes the profile afterwards - only after the model accepted it (and, if it has unsaved changes, only if they were saved)."""
        b = req.json()
        kind = choice(b.get("kind"), T.KINDS, "kind")
        close = boolean(b.get("close", False), "close")
        with app.lock:
            S = STORE.get(req.params["sid"])
            project = app.require()
            payload = T.prepare(S, kind, _options(b, project))
            expected = b.get("expected_revision")
            outcome = project.execute("profiler.send", {"payload": payload}, expected_revision=int(expected) if expected is not None else None)
            res: dict[str, Any] = {**outcome, "project": project.summary(), "warnings": payload["warnings"], "closed": False}
            if close:
                if S.dirty and payload["mode"] == "linked":
                    res["close_blocked"] = "the profile has unsaved changes"
                else:
                    IO.discard_autosave(S, _dirs(app)[1])
                    STORE.close(S.id)
                    res["closed"] = True
            return res

    @router.route("POST", "/api/profiler/resync")
    def profiler_resync(app: SpadraApp, req: Request) -> Any:
        """Bring an object made from a profile up to date: from an open profile (``sid``) or from the file / embedded copy it is linked to."""
        b = req.json()
        with app.lock:
            project = app.require()
            from .. import elements as E
            oid = text(b.get("id"), "id", 80)
            if not E.alive(project, oid):
                raise NotFound("Element %s does not exist" % oid)
            link = E.m(E.meta_of(project, oid), T.LINK_KEY)
            if not isinstance(link, dict) or link.get("tool") != "profiler":
                raise ProfilerError("this object was not made from a Profiler profile")
            if b.get("sid"):
                S = STORE.get(text(b["sid"], "sid", 64))
            else:
                sk, _ = T.source_sketch(link)
                S = Session(sk)
            opts = {"mode": link.get("mode", "embedded"), "overrides": b.get("overrides", link.get("overrides")), **(b.get("options") or {}), "project_unit": project.manifest["unit"]}
            if opts["mode"] == "linked":
                opts["mode"] = "linked" if S.path else "embedded"
            payload = T.prepare(S, link["kind"], opts)
            outcome = project.execute("profiler.resync", {"id": oid, "payload": payload})
            return {**outcome, "project": project.summary(), "warnings": payload["warnings"]}

    @router.route("GET", "/api/profiler-link")
    def profiler_linked(app: SpadraApp, req: Request) -> Any:
        """Does an object come from a profile, and has its source moved on?"""
        with app.lock:
            project = app.require()
            from .. import elements as E
            oid = text(req.query.get("id") or "", "id", 80)
            if not E.alive(project, oid):
                raise NotFound("Element %s does not exist" % oid)
            link = E.m(E.meta_of(project, oid), T.LINK_KEY)
            if not isinstance(link, dict) or link.get("tool") != "profiler":
                return {"linked": False}
            out = {"linked": True, "mode": link.get("mode"), "kind": link.get("kind"), "sketch_name": link.get("sketch_name"), "revision": link.get("revision"),
                   "overrides": link.get("overrides"), "params": link.get("params"), "file": link.get("file")}
            try:
                _, info = T.source_sketch(link)
                out["source_changed"] = bool(info.get("changed"))
            except ProfilerError as exc:
                out["source_error"] = exc.message
            return out
