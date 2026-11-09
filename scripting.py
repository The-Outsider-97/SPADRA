"""Python computation in SPADRA (P0-02): versioned scripts, a typed read-only model API, typed validated outputs, and an isolated runner.

The rules this module keeps:
  * A script never touches the project. It runs in a separate process (``scripting_runner.py``) on a *copy* of the model (the snapshot) and returns
    values. Those values are *proposals*: a run only produces a record; ``script.apply`` is the one command that changes the project, it checks the
    project is still at the revision the run saw, validates the values against the script's declared outputs, and is one undoable transaction.
  * Isolation is stated, never assumed. ``isolation()`` actually tries the forbidden operations from inside the sandbox on this machine and reports
    what it found. Only when the operating system itself refuses them all (Linux: Landlock file and TCP denial, seccomp for sockets, new processes and
    exec, resource limits) is *untrusted* source allowed to run. Anywhere else (no Windows hardening exists here) a version runs only after a person
    approved that exact source hash, and the result says ``process-isolated``, which is not a sandbox.
  * Scripts are versioned: every edit adds a version (source hash, declared inputs and outputs); an approval belongs to one hash; nothing runs "latest" implicitly
    beyond the current version of the script the caller named.
  * A run record keeps the script version and hash, the inputs, the model revision, the seed and the result hash, so the same inputs give the same
    hash (scripts have no clock and no entropy of their own: ``random`` is seeded from the run's seed).
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from src.utils.spatial_formats.common import units as U

from ..tools.errors import BadRequest, Conflict, NotFound
from ..tools.validation import text
from . import elements as E
from . import families as FM

RUNNER = Path(__file__).with_name("scripting_runner.py")
MAX_SOURCE = 64_000
MAX_VERSIONS = 50
MAX_SCRIPTS = 200
MAX_RUNS = 20
MAX_OUTPUT_BYTES = 1_000_000
MAX_LIST = 10_000
READ_SCOPES = ("levels", "elements", "bounds", "families")      # what a script version may be allowed to read; it declares the ones it needs
API_VERSION = "spadra.script_api/1"
RUN_SCHEMA = "spadra.script_run/1"
FRAME = {"coordinates": "project coordinates, right-handed, +Z up", "length": "m", "angle": "rad",
         "bounds": "world-axis-aligned boxes {min, max} in project coordinates", "levels": "elevation in m above the project origin"}
DEFAULT_LIMITS = {"timeout_s": 10.0, "cpu_seconds": 8, "memory_bytes": 512 * 1024 * 1024}
SCALAR_TYPES = ("number", "integer", "boolean", "text", "numbers")
ALLOWED_IMPORTS = ("math", "cmath", "statistics", "fractions", "decimal", "itertools", "functools", "collections", "random", "operator", "bisect",
                   "heapq", "json", "re", "string", "typing", "dataclasses", "enum", "numbers", "copy")
BANNED_NAMES = {"eval", "exec", "compile", "open", "globals", "locals", "vars", "input", "breakpoint", "memoryview", "__import__", "__builtins__",
                "exit", "quit", "help"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------------------------------------------------------- the source
def check_source(source: Any) -> dict[str, Any]:
    """Syntax and a conservative scan. Returns ``{"ok", "problems": [{"line", "message"}]}``. The scan is a convenience for the author and a second
    layer for the runner; the operating-system sandbox is the boundary."""
    if not isinstance(source, str) or not source.strip():
        return {"ok": False, "problems": [{"line": 0, "message": "The script is empty"}]}
    if len(source) > MAX_SOURCE:
        return {"ok": False, "problems": [{"line": 0, "message": f"The script is longer than {MAX_SOURCE} characters"}]}
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return {"ok": False, "problems": [{"line": exc.lineno or 0, "message": f"Syntax error: {exc.msg}"}]}
    problems: list[dict[str, Any]] = []

    def add(node: ast.AST, message: str) -> None:
        problems.append({"line": getattr(node, "lineno", 0), "message": message})

    has_run = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] not in ALLOWED_IMPORTS:
                    add(node, f"'{a.name}' cannot be imported; allowed: {', '.join(ALLOWED_IMPORTS)}")
        elif isinstance(node, ast.ImportFrom):
            if node.level or (node.module or "").split(".")[0] not in ALLOWED_IMPORTS:
                add(node, f"'{node.module}' cannot be imported; allowed: {', '.join(ALLOWED_IMPORTS)}")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            add(node, f"attribute '{node.attr}' is not available (names that start with an underscore are refused)")
        elif isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            add(node, f"'{node.id}' is not available in a script")
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "run":
            has_run = True
            if [a.arg for a in node.args.args] != ["model", "params"]:
                add(node, "run must be declared as run(model, params)")
    if not has_run:
        problems.append({"line": 0, "message": "The script must define run(model, params) and return a dict of its declared outputs"})
    return {"ok": not problems, "problems": problems}


# ------------------------------------------------------------------------------------------------------------------- typed values
def _quantity_types() -> tuple[str, ...]:
    return tuple(U.QUANTITY_UNITS)


def _declared(raw: Any, label: str, *, inputs: bool) -> dict[str, dict[str, Any]]:
    if raw in (None, {}):
        return {}
    if not isinstance(raw, Mapping) or len(raw) > 100:
        raise BadRequest(f"{label} must map a name to its declaration (at most 100)")
    out: dict[str, dict[str, Any]] = {}
    for name, spec in raw.items():
        if not isinstance(name, str) or not name.isidentifier() or name.startswith("_"):
            raise BadRequest(f"'{name}' is not a valid {label[:-1]} name")
        spec = {"type": spec} if isinstance(spec, str) else spec
        if not isinstance(spec, Mapping):
            raise BadRequest(f"{label[:-1].capitalize()} '{name}' must be declared with a type")
        kind = spec.get("type")
        if kind not in SCALAR_TYPES and kind not in _quantity_types():
            raise BadRequest(f"{label[:-1].capitalize()} '{name}' has unknown type '{kind}'", details={"types": list(SCALAR_TYPES) + list(_quantity_types())})
        clean: dict[str, Any] = {"type": kind}
        for key in ("min", "max"):
            if spec.get(key) is not None:
                if kind not in _quantity_types() and kind not in ("number", "integer", "numbers"):
                    raise BadRequest(f"{label[:-1].capitalize()} '{name}': {key} applies to numeric types only")
                clean[key] = _finite(spec[key], f"{name}.{key}")
        if inputs:
            if "default" in spec:
                clean["default"] = spec["default"]
            clean["required"] = bool(spec.get("required", "default" not in spec))
        if spec.get("description"):
            clean["description"] = text(spec["description"], "description", 300)
        out[name] = clean
    return out


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise BadRequest(f"{label} must be a finite number")
    return float(value) if not isinstance(value, int) else value


def _number(kind: str, value: Any, label: str) -> float:
    """A number in SI, or ``{"value", "unit"}`` converted through the unit table."""
    if isinstance(value, Mapping):
        if set(value) - {"value", "unit"} or "value" not in value or "unit" not in value:
            raise BadRequest(f"{label}: a quantity is {{\"value\": number, \"unit\": symbol}}")
        if kind not in U.QUANTITY_UNITS:
            raise BadRequest(f"{label}: type '{kind}' has no units")
        try:
            return U.to_si(kind, _finite(value["value"], label), str(value["unit"]))
        except Exception as exc:
            raise BadRequest(f"{label}: {exc}") from exc
    return _finite(value, label)


def typed(kind: str, spec: Mapping[str, Any], value: Any, label: str) -> Any:
    """Validate one value against its declaration. Numbers and quantities come back in SI."""
    if kind == "boolean":
        if not isinstance(value, bool):
            raise BadRequest(f"{label} must be true or false")
        return value
    if kind == "text":
        if not isinstance(value, str) or len(value) > 2000:
            raise BadRequest(f"{label} must be text of at most 2000 characters")
        return value
    if kind == "numbers":
        if not isinstance(value, list) or len(value) > MAX_LIST:
            raise BadRequest(f"{label} must be a list of at most {MAX_LIST} numbers")
        return [_bounded(spec, _finite(v, f"{label}[{i}]"), f"{label}[{i}]") for i, v in enumerate(value)]
    v = _number(kind, value, label)
    if kind == "integer":
        if isinstance(v, float) and not v.is_integer():
            raise BadRequest(f"{label} must be a whole number")
        v = int(v)
    return _bounded(spec, v, label)


def _bounded(spec: Mapping[str, Any], v: Any, label: str) -> Any:
    if "min" in spec and v < spec["min"]:
        raise BadRequest(f"{label} = {v:g} is below its minimum {spec['min']:g}")
    if "max" in spec and v > spec["max"]:
        raise BadRequest(f"{label} = {v:g} is above its maximum {spec['max']:g}")
    return v


def validate_inputs(declared: Mapping[str, Mapping[str, Any]], given: Any) -> dict[str, Any]:
    given = dict(given or {})
    unknown = sorted(set(given) - set(declared))
    if unknown:
        raise BadRequest(f"Unknown input(s): {', '.join(unknown)}", details={"declared": sorted(declared)})
    out = {}
    for name, spec in declared.items():
        if name in given:
            out[name] = typed(spec["type"], spec, given[name], f"input '{name}'")
        elif "default" in spec:
            out[name] = typed(spec["type"], spec, spec["default"], f"default of input '{name}'")
        elif spec.get("required"):
            raise BadRequest(f"Input '{name}' is required")
    return out


def validate_outputs(declared: Mapping[str, Mapping[str, Any]], result: Any) -> dict[str, Any]:
    if not declared:
        raise BadRequest("This script version declares no outputs, so its result cannot be accepted")
    if not isinstance(result, Mapping):
        raise BadRequest("run() must return a dict of its declared outputs", details={"returned": type(result).__name__})
    extra, missing = sorted(set(result) - set(declared)), sorted(set(declared) - set(result))
    if extra or missing:
        parts = ([f"undeclared: {', '.join(extra)}"] if extra else []) + ([f"missing: {', '.join(missing)}"] if missing else [])
        raise BadRequest("The result does not match the declared outputs (" + "; ".join(parts) + ")", details={"undeclared": extra, "missing": missing})
    return {name: typed(spec["type"], spec, result[name], f"output '{name}'") for name, spec in declared.items()}


# ------------------------------------------------------------------------------------------------------------------- the model API
def snapshot(project: Any, reads: Any = READ_SCOPES) -> dict[str, Any]:
    """The copy of the model a script is given: levels, elements (category, level, properties, bounds, family) and families, in SI units. Nothing in it
    is a live reference; the model itself is not reachable from the script process. Only what the script version declared it may read is included, and the
    snapshot states the revision, the unit and the coordinate frame it is expressed in."""
    granted = [r for r in READ_SCOPES if r in reads]
    try:
        bounds = project.model._model.bounds_by_object() if "bounds" in granted else {}
    except Exception:                                              # bounds are an extra; a model without them still queries
        bounds = {}
    elements = []
    if "elements" in granted:
        for oid in E.element_ids(project)[:20_000]:
            obj = project.model.get(oid)
            meta = obj["metadata"]
            props = {k.split(":", 1)[1]: _plain(v) for k, v in meta.items() if k.startswith(f"{E.NS}:")}
            row = {"id": oid, "name": obj.get("name"), "category": props.get("category", "generic"), "level": props.get("level"), "properties": props}
            if obj.get("family_id"):
                row["family"] = {"id": obj["family_id"], "type": obj.get("family_type"), "overrides": _plain(obj.get("parameters", {}))}
            if oid in bounds:
                lo, hi = bounds[oid]
                row["bounds"] = {"min": [float(v) for v in lo], "max": [float(v) for v in hi]}
            elements.append(row)
    levels = [{"id": lv["id"], "name": lv.get("name"), "elevation": float(lv["elevation"])} for lv in project.manifest["levels"]] if "levels" in granted else []
    fams = [{"id": f["id"], "name": f["name"], "shape": f["shape"], "types": [t["name"] for t in f["types"]],
             "parameters": [{k: r[k] for k in ("name", "type", "unit", "default", "expression", "scope")} for r in f["parameters"]]}
            for f in FM.listing(project)] if "families" in granted else []
    return {"api": API_VERSION, "revision": project.revision, "unit": project.manifest["unit"], "frame": FRAME, "granted": granted,
            "levels": levels, "elements": elements, "families": fams}


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def snapshot_sha256(snap: Mapping[str, Any]) -> str:
    """Hash of what the script was shown. The revision counter is left out on purpose: two revisions with the same content give the same hash, which is
    what decides whether a replay is a fair comparison."""
    return hashlib.sha256(canonical({k: v for k, v in snap.items() if k != "revision"}).encode()).hexdigest()


_RUNNER_HASH: list[str] = []


def environment() -> dict[str, Any]:
    """What a result may depend on besides source, inputs and model: the interpreter, the machine, the runner and the rules it enforces. Recorded with every
    run so a later replay can say whether it ran under the same conditions."""
    if not _RUNNER_HASH:
        _RUNNER_HASH.append(hashlib.sha256(RUNNER.read_bytes()).hexdigest())
    return {"interpreter": platform.python_implementation(), "python": platform.python_version(), "system": platform.system(), "machine": platform.machine(),
            "runner_sha256": _RUNNER_HASH[0], "api": API_VERSION, "allowed_imports": list(ALLOWED_IMPORTS), "hash_seed": "0",
            "locale": "none (the environment is cleared)", "clock": "not available to scripts (time, datetime and os are not importable)",
            "random": "random.seed(run seed)", "float": "IEEE-754 binary64"}


def _plain(value: Any, depth: int = 0) -> Any:
    """JSON-safe, bounded copy of metadata."""
    if value is None or isinstance(value, (bool, int, str)):
        return value[:500] if isinstance(value, str) else value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if depth > 4:
        return None
    if isinstance(value, (list, tuple)):
        return [_plain(v, depth + 1) for v in list(value)[:500]]
    if isinstance(value, Mapping):
        return {str(k): _plain(v, depth + 1) for k, v in list(value.items())[:200]}
    return str(value)[:200]


# ------------------------------------------------------------------------------------------------------------------- isolation
_isolation_cache: dict[str, Any] = {}
_isolation_lock = threading.Lock()


def _invoke(request: dict[str, Any], timeout: float) -> dict[str, Any]:
    """Run the runner once. Never raises for a script's own failure; reports it. Output is read through a hard cap."""
    posix = os.name == "posix"
    with tempfile.TemporaryDirectory(prefix="spadra-script-") as cwd:
        req_path = Path(cwd) / "request.json"
        req_path.write_text(json.dumps(request), encoding="utf-8")
        env = {"PYTHONHASHSEED": "0", "PYTHONIOENCODING": "utf-8", "PATH": ""}
        if os.name == "nt":                                                              # Windows will not start Python reliably without its system root
            env.update({k: os.environ[k] for k in ("SYSTEMROOT", "SystemRoot", "WINDIR") if k in os.environ})
        kwargs: dict[str, Any] = {"start_new_session": True} if posix else {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
        started = time.monotonic()
        with open(req_path, "rb") as stdin:
            proc = subprocess.Popen([sys.executable, "-I", "-S", str(RUNNER)], stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    cwd=cwd, env=env, close_fds=True, **kwargs)
            buffers: dict[str, bytearray] = {"out": bytearray(), "err": bytearray()}
            over = threading.Event()

            def drain(stream: Any, key: str) -> None:
                while True:
                    chunk = stream.read(65536)
                    if not chunk:
                        return
                    buffers[key].extend(chunk)
                    if len(buffers[key]) > MAX_OUTPUT_BYTES:
                        over.set()
                        _kill(proc)
                        return

            threads = [threading.Thread(target=drain, args=(proc.stdout, "out"), daemon=True), threading.Thread(target=drain, args=(proc.stderr, "err"), daemon=True)]
            for t in threads:
                t.start()
            timed_out = False
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill(proc)
                proc.wait()
            for t in threads:
                t.join(2.0)
            for stream in (proc.stdout, proc.stderr):                                # a drain thread that stopped at the output cap leaves its pipe open
                try:
                    stream.close()
                except Exception:
                    pass
        duration = time.monotonic() - started
    reply: dict[str, Any] | None = None
    if not timed_out and not over.is_set():
        try:
            reply = json.loads(bytes(buffers["out"]).decode("utf-8"))
        except Exception:
            reply = None
    return {"reply": reply, "returncode": proc.returncode, "timed_out": timed_out, "output_too_large": over.is_set(), "duration_s": round(duration, 4),
            "stderr": bytes(buffers["err"][:2000]).decode("utf-8", "replace")}


def _kill(proc: subprocess.Popen) -> None:
    try:
        if os.name == "posix":
            import signal
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        pass


def isolation(refresh: bool = False) -> dict[str, Any]:
    """What this machine can enforce, found by trying. ``untrusted_execution`` is True only when the sandbox ran and every forbidden operation failed."""
    with _isolation_lock:
        if _isolation_cache and not refresh:
            return dict(_isolation_cache)
        info: dict[str, Any] = {"platform": sys.platform, "level": "process-isolated", "untrusted_execution": False, "mechanisms": ["separate process", "wall-clock timeout", "output cap"], "probe": None, "notes": []}
        if sys.platform != "linux":
            info["notes"].append("No operating-system sandbox is implemented for this platform: only scripts whose exact source a person approved can run, and they run as a separate process, not in a sandbox. "
                                 + ("Windows has no per-process CPU or memory limit here: only the wall-clock timeout and the output cap apply." if sys.platform == "win32" else ""))
            if sys.platform == "win32":
                info["mechanisms"] = ["separate process", "wall-clock timeout", "output cap"]
        else:
            got = _invoke({"source": "def run(model, params):\n    return 1\n", "snapshot": {}, "params": {}, "seed": 0, "limits": DEFAULT_LIMITS, "sandbox": True, "probe": True}, 20.0)
            reply = got["reply"] or {}
            probe = reply.get("probe")
            if reply.get("ok") and probe and not any(probe.values()):
                info.update(level="os-sandboxed", untrusted_execution=True, probe=probe,
                            mechanisms=info["mechanisms"] + ["resource limits"] + list(reply.get("sandbox", [])))
            else:
                info["probe"] = probe
                info["notes"].append("The sandbox could not be established or a forbidden operation was not refused" +
                                     (f": {reply.get('error')}" if reply.get("error") else "") + "; untrusted scripts stay disabled.")
        _isolation_cache.clear()
        _isolation_cache.update(info)
        return dict(info)


# ------------------------------------------------------------------------------------------------------------------- registry
def _scripts(project: Any) -> list[dict[str, Any]]:
    return project.manifest.setdefault("scripts", [])


def script(project: Any, script_id: Any) -> dict[str, Any]:
    for s in _scripts(project):
        if s["id"] == script_id:
            return s
    raise NotFound(f"There is no script {script_id}")


def version_of(entry: Mapping[str, Any], version: Any = None) -> dict[str, Any]:
    try:
        wanted = entry["current"] if version in (None, "") else int(version)
    except (TypeError, ValueError):
        raise BadRequest("version must be a whole number") from None
    for v in entry["versions"]:
        if v["version"] == wanted:
            return v
    raise NotFound(f"Script '{entry['name']}' has no version {wanted}")


def _reads(raw: Any) -> list[str]:
    """The read permissions a version declares: a list drawn from READ_SCOPES (absent means all of them, as before permissions existed)."""
    if raw is None:
        return list(READ_SCOPES)
    if not isinstance(raw, (list, tuple)) or any(not isinstance(r, str) for r in raw) or len(set(raw)) != len(raw):
        raise BadRequest("reads must be a list of distinct names from " + ", ".join(READ_SCOPES))
    unknown = sorted(set(raw) - set(READ_SCOPES))
    if unknown:
        raise BadRequest(f"Unknown read permission(s): {', '.join(unknown)}", details={"scopes": list(READ_SCOPES)})
    return [r for r in READ_SCOPES if r in raw]


def _version(source: Any, inputs: Any, outputs: Any, number_: int, note: Any = "", reads: Any = None) -> dict[str, Any]:
    check = check_source(source)
    if not check["ok"]:
        raise BadRequest("The script cannot be accepted: " + "; ".join(f"line {p['line']}: {p['message']}" for p in check["problems"][:5]),
                         details={"problems": check["problems"]})
    return {"version": number_, "sha256": sha256(source), "source": source, "inputs": _declared(inputs, "inputs", inputs=True),
            "outputs": _declared(outputs, "outputs", inputs=False), "reads": _reads(reads), "created": now(), "note": text(note, "note", 200, empty=True) if note else "", "approval": None}


def add(project: Any, p: Mapping[str, Any]) -> dict[str, Any]:
    if len(_scripts(project)) >= MAX_SCRIPTS:
        raise BadRequest(f"A project holds at most {MAX_SCRIPTS} scripts")
    name = text(p.get("name"), "name", 80)
    if any(s["name"].lower() == name.lower() for s in _scripts(project)):
        raise Conflict(f"There is already a script named '{name}'")
    entry = {"id": "scr-" + uuid.uuid4().hex[:8], "name": name, "description": text(p.get("description"), "description", 400, empty=True) if p.get("description") else "",
             "current": 1, "versions": [_version(p.get("source"), p.get("inputs"), p.get("outputs"), 1, p.get("note"), p.get("reads"))]}
    _scripts(project).append(entry)
    return {"id": entry["id"], "version": 1, "sha256": entry["versions"][0]["sha256"]}


def update(project: Any, p: Mapping[str, Any]) -> dict[str, Any]:
    """Editing never changes a version: it adds the next one, which starts unapproved."""
    entry = script(project, p.get("id"))
    cur = version_of(entry)
    if len(entry["versions"]) >= MAX_VERSIONS:
        raise BadRequest(f"A script keeps at most {MAX_VERSIONS} versions")
    new = _version(p.get("source", cur["source"]), p.get("inputs", cur["inputs"]), p.get("outputs", cur["outputs"]), max(v["version"] for v in entry["versions"]) + 1, p.get("note"),
                   p.get("reads", cur.get("reads")))
    entry["versions"].append(new)
    entry["current"] = new["version"]
    if p.get("name"):
        entry["name"] = text(p["name"], "name", 80)
    return {"id": entry["id"], "version": new["version"], "sha256": new["sha256"]}


def approve(project: Any, p: Mapping[str, Any]) -> dict[str, Any]:
    """A person approves one version by its hash. The hash must be the one they reviewed."""
    entry = script(project, p.get("id"))
    v = version_of(entry, p.get("version"))
    if p.get("sha256") != v["sha256"]:
        raise Conflict("The source hash given is not this version's hash; review the version and approve it again")
    v["approval"] = {"at": now(), "sha256": v["sha256"], "by": "user"}
    return {"id": entry["id"], "version": v["version"], "approved": True}


def revoke(project: Any, p: Mapping[str, Any]) -> dict[str, Any]:
    entry = script(project, p.get("id"))
    v = version_of(entry, p.get("version"))
    v["approval"] = None
    return {"id": entry["id"], "version": v["version"], "approved": False}


def delete(project: Any, script_id: Any) -> None:
    entry = script(project, script_id)
    _scripts(project).remove(entry)


def describe(project: Any) -> dict[str, Any]:
    rows = []
    for s in _scripts(project):
        rows.append({"id": s["id"], "name": s["name"], "description": s.get("description", ""), "current": s["current"],
                     "versions": [{"version": v["version"], "sha256": v["sha256"], "approved": bool(v["approval"]), "created": v["created"], "note": v.get("note", ""),
                                   "inputs": v["inputs"], "outputs": v["outputs"], "lines": v["source"].count("\n") + 1} for v in s["versions"]]})
    return {"scripts": rows, "isolation": isolation(), "applications": list(project.manifest.get("script_applications", []))[-50:]}


def source_of(project: Any, script_id: Any, version: Any = None) -> dict[str, Any]:
    entry = script(project, script_id)
    v = version_of(entry, version)
    return {"id": entry["id"], "name": entry["name"], **{k: v[k] for k in ("version", "sha256", "source", "inputs", "outputs")}, "approved": bool(v["approval"])}


# ------------------------------------------------------------------------------------------------------------------- runs (previews)
def _runs(project: Any) -> dict[str, dict[str, Any]]:
    return project.__dict__.setdefault("_script_runs", {})


def run(project: Any, p: Mapping[str, Any]) -> dict[str, Any]:
    """Run a script version on a copy of the model. Changes nothing in the project; the record it returns is a preview of what ``script.apply`` would use."""
    entry = script(project, p.get("script") or p.get("id"))
    v = version_of(entry, p.get("version"))
    seed = int(p.get("seed", 0))
    iso = isolation()
    env = environment()
    reads = v.get("reads", list(READ_SCOPES))
    base = {"schema": RUN_SCHEMA, "id": "run-" + uuid.uuid4().hex[:8], "script": entry["id"], "name": entry["name"], "version": v["version"], "sha256": v["sha256"],
            "revision": project.revision, "seed": seed, "isolation": iso["level"], "reads": reads, "environment": env,
            "environment_sha256": hashlib.sha256(canonical(env).encode()).hexdigest(), "created": now()}
    expected = p.get("expected_revision")
    if expected is not None and int(expected) != project.revision:
        return _remember(project, {**base, "status": "stale", "error": f"The project is at revision {project.revision}, not {int(expected)}; nothing was run. Run again on the current model."})
    if not iso["untrusted_execution"] and not (v["approval"] and v["approval"]["sha256"] == v["sha256"]):
        return _remember(project, {**base, "status": "refused", "error": "This version has not been approved, and this machine cannot sandbox untrusted scripts "
                                   f"(isolation: {iso['level']}). Review the source and approve its hash to run it."})
    try:
        inputs = validate_inputs(v["inputs"], p.get("inputs"))
    except BadRequest as exc:
        return _remember(project, {**base, "status": "invalid_input", "error": str(exc)})
    limits = dict(DEFAULT_LIMITS)
    for key in ("timeout_s", "cpu_seconds"):
        if p.get(key) is not None:
            limits[key] = min(float(p[key]), DEFAULT_LIMITS[key] * 6)
    snap = snapshot(project, reads)
    base["snapshot_sha256"], base["inputs_sha256"] = snapshot_sha256(snap), hashlib.sha256(canonical(inputs).encode()).hexdigest()
    got = _invoke({"source": v["source"], "snapshot": snap, "params": inputs, "seed": seed,
                   "limits": {"cpu_seconds": int(limits["cpu_seconds"]), "memory_bytes": limits["memory_bytes"]}, "sandbox": iso["untrusted_execution"]}, float(limits["timeout_s"]))
    rec = {**base, "inputs": inputs, "limits": {k: limits[k] for k in ("timeout_s", "cpu_seconds", "memory_bytes")}, "duration_s": got["duration_s"]}
    reply = got["reply"]
    if got["timed_out"]:
        return _remember(project, {**rec, "status": "timeout", "error": f"The script ran longer than {limits['timeout_s']:g} s and was stopped"})
    if got["output_too_large"]:
        return _remember(project, {**rec, "status": "error", "error": f"The script produced more than {MAX_OUTPUT_BYTES} bytes of output and was stopped"})
    if reply is None:
        why = "ran out of CPU time" if got["returncode"] in (-24, -9) else f"ended abnormally (exit {got['returncode']})"
        return _remember(project, {**rec, "status": "error", "error": f"The script process {why}", "stderr": got["stderr"]})
    if reply.get("refused"):
        return _remember(project, {**rec, "status": "refused", "error": reply.get("error")})
    rec["sandbox"] = reply.get("sandbox", [])
    rec["log"] = reply.get("log", "")
    if not reply.get("ok"):
        return _remember(project, {**rec, "status": "error", "error": reply.get("error")})
    try:
        outputs = validate_outputs(v["outputs"], reply.get("result"))
    except BadRequest as exc:
        return _remember(project, {**rec, "status": "invalid_output", "error": str(exc), "details": getattr(exc, "details", None)})
    rec.update(status="ok", outputs=outputs, result_sha256=hashlib.sha256(json.dumps(outputs, sort_keys=True).encode()).hexdigest())
    return _remember(project, rec)


def replay(project: Any, p: Mapping[str, Any]) -> dict[str, Any]:
    """Run a finished run again from its record (same source version, inputs and seed) and say whether it gave the same result hash. A replay is only a fair
    comparison when the model shows the script the same content as before, so a changed snapshot is reported instead of being run and compared. Differences
    in the environment (another interpreter, machine or runner) are always listed. Changes nothing in the project."""
    rec = _runs(project).get(p.get("run"))
    if rec is None:
        raise NotFound(f"There is no recorded run {p.get('run')}")
    if rec.get("status") != "ok":
        raise BadRequest(f"Run {rec['id']} did not finish ({rec.get('status')}), so there is no result to reproduce")
    entry = script(project, rec["script"])
    v = version_of(entry, rec["version"])
    if v["sha256"] != rec["sha256"]:
        raise Conflict("The stored source of that version no longer matches the run's recorded hash")
    env = environment()
    drift = sorted(k for k in env if env[k] != rec["environment"].get(k))
    out = {"run": rec["id"], "environment_changed": drift, "snapshot_sha256": rec["snapshot_sha256"], "result_sha256": rec["result_sha256"]}
    now_hash = snapshot_sha256(snapshot(project, v.get("reads", list(READ_SCOPES))))
    if now_hash != rec["snapshot_sha256"]:
        return {**out, "status": "snapshot_changed", "reproduced": None,
                "message": "The model no longer shows the script what it showed in the recorded run, so a rerun could not be compared. Nothing was run."}
    again = run(project, {"script": rec["script"], "version": rec["version"], "inputs": rec["inputs"], "seed": rec["seed"]})
    again["replay_of"] = rec["id"]
    if again["status"] != "ok":
        return {**out, "status": again["status"], "reproduced": False, "replay": again["id"], "message": again.get("error")}
    same = again["result_sha256"] == rec["result_sha256"]
    return {**out, "status": "reproduced" if same else "different", "reproduced": same, "replay": again["id"], "replay_result_sha256": again["result_sha256"]}


def _remember(project: Any, rec: dict[str, Any]) -> dict[str, Any]:
    runs = _runs(project)
    runs[rec["id"]] = rec
    while len(runs) > MAX_RUNS:
        runs.pop(next(iter(runs)))
    return rec


# ------------------------------------------------------------------------------------------------------------------- apply (the one commit)
def plan(project: Any, p: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a previewed run against the project and turn its outputs into the ordinary commands that would apply them, plus the provenance record.
    Changes nothing. ``apply`` runs this inside one command; the assistant turns it into a proposal a person approves."""
    rec = _runs(project).get(p.get("run"))
    if rec is None:
        raise NotFound("There is no such run; run the script again to preview it")
    if rec["status"] != "ok":
        raise BadRequest(f"That run did not succeed ({rec['status']}); there is nothing to apply")
    if rec["revision"] != project.revision:
        raise Conflict(f"The project changed since this run (it saw revision {rec['revision']}, the project is at {project.revision})", hint="Run the script again")
    targets = p.get("targets")
    if not isinstance(targets, list) or not targets or len(targets) > 200:
        raise BadRequest("targets must be a list of 1 to 200 entries")
    entry = script(project, rec["script"])
    declared = version_of(entry, rec["version"])["outputs"]
    steps, shown_targets = [], []
    types: dict[tuple[str, str], dict[str, Any]] = {}
    for t in targets:
        if not isinstance(t, Mapping) or t.get("output") not in rec["outputs"]:
            raise BadRequest("Each target must name one of the run's outputs", details={"outputs": sorted(rec["outputs"])})
        kind, value = declared[t["output"]]["type"], rec["outputs"][t["output"]]
        if "element" in t:
            obj = project.model.get(text(t["element"], "element", 80))
            fam = FM.family(project, obj.get("family_id"))
        else:
            fam = FM.family(project, t.get("family"))
        spec = fam["parameters"].get(t.get("parameter"))
        if spec is None:
            raise BadRequest(f"{fam['name']} has no parameter '{t.get('parameter')}'")
        if "expression" in spec or spec.get("read_only"):
            raise BadRequest(f"Parameter '{t['parameter']}' is computed by a formula and cannot be set")
        numeric = ("number", "integer")
        if spec["type"] != kind and not (spec["type"] in numeric and kind in numeric):
            raise BadRequest(f"Output '{t['output']}' is {kind} but parameter '{t['parameter']}' is {spec['type']}")
        if isinstance(value, list):
            raise BadRequest(f"Output '{t['output']}' is a list; it cannot be written to a single parameter")
        shown = FM.from_si(spec["type"], value) if spec["type"] not in ("text", "boolean") else value
        if "element" in t:
            steps.append({"op": "model.set_property", "params": {"id": t["element"], "key": "param:" + t["parameter"], "value": shown}})
        else:
            _find = FM._find_type(fam, text(t.get("type"), "type", 80))
            types.setdefault((fam["id"], _find), {})[t["parameter"]] = shown
        shown_targets.append({k: t[k] for k in ("output", "family", "type", "element", "parameter") if k in t})
    steps = [{"op": "family.type_update", "params": {"id": fid, "name": tn, "values": vals}} for (fid, tn), vals in types.items()] + steps
    record = {"run": rec["id"], "script": rec["script"], "version": rec["version"], "sha256": rec["sha256"], "seed": rec["seed"], "result_sha256": rec["result_sha256"],
              "revision": rec["revision"], "targets": shown_targets}
    return {"steps": steps, "record": record, "run": rec}


def record_application(project: Any, record: Mapping[str, Any]) -> dict[str, Any]:
    """Add the provenance of an applied run to the project. The script, version and hash must exist in this project: a record cannot name a script that is not here."""
    entry = script(project, record.get("script"))
    v = version_of(entry, record.get("version"))
    if record.get("sha256") != v["sha256"]:
        raise Conflict("The record's source hash is not the stored hash of that script version")
    clean = {k: record.get(k) for k in ("run", "script", "version", "sha256", "seed", "result_sha256", "revision", "targets")}
    clean["at"] = now()
    log = project.manifest.setdefault("script_applications", [])
    log.append(clean)
    del log[:-200]
    return {"recorded": clean["run"]}


def apply(project: Any, p: Mapping[str, Any]) -> dict[str, Any]:
    """Write a previewed run's outputs into the model as parameter values, or refuse the whole change. ``targets`` maps each output to a family type's
    parameter (``{"output", "family", "type", "parameter"}``) or an element's (``{"output", "element", "parameter"}``). Runs inside one command, so
    it is one undo step and, on any failure, nothing changes."""
    pl = plan(project, p)
    for step in pl["steps"]:
        if step["op"] == "family.type_update":
            FM.type_update(project, step["params"])
        else:
            FM.set_instance_parameter(project, step["params"]["id"], step["params"]["key"][6:], step["params"]["value"])
    record_application(project, pl["record"])
    return {"applied": len(pl["steps"]), "run": pl["run"]["id"]}
