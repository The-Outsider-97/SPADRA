"""Starting SPADRA: the environment check behind ``--check``, the client build behind ``--dev`` / ``--build``, and the packaging of
a desktop bundle (see ``bundle.py``).

What this module never does is change the interpreter. SPADRA runs on the Python that started it. If that Python is outside the range
SLAI declares, or lacks a package, the check says so and names the command that fixes it; it does not look for another Python, create a
virtual environment or install anything.

Every check reports what was *observed* (a version read from the installed distribution, an import that was really attempted), not what
``requirements.txt`` says should be there.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import importlib.util
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Sequence

from .utils.spadra_errors import StartupError

HERE = Path(__file__).resolve().parent
Status = Literal["ok", "warn", "error"]

#: (distribution, import name, minimum version, why SPADRA needs it). ``required`` ones stop start-up; the others only switch features off.
REQUIRED: tuple[tuple[str, str, str, str], ...] = (
    ("PyYAML", "yaml", "6.0", "SLAI's configuration repository (src.utils.configuration) reads every YAML file"),
    ("jsonschema", "jsonschema", "4.18", "every SGEO, SDRW and project file is validated against its JSON Schema 2020-12 when it is saved or opened (older versions lack Draft202012Validator)"),
)
OPTIONAL: tuple[tuple[str, str, str, str], ...] = (
    ("numpy", "numpy", "1.24", "the STEM, Simulation, Spatial and Optimization agents"),
    ("scipy", "scipy", "1.10", "the STEM Agent's numerical integrator and the Optimization Agent"),
    ("psutil", "psutil", "5.9", "SLAI's resource monitoring, imported by the agents"),
    ("openpyxl", "openpyxl", "3.1", "optional XLSX classification table import"),
    ("opencv-python", "cv2", "4.8", "reading raster plans in 2D-to-BIM reconstruction"),
    ("scikit-image", "skimage", "0.21", "thinning strokes of raster plans in 2D-to-BIM reconstruction"),
    ("pytesseract", "pytesseract", "0.3", "reading printed dimensions of raster plans (also needs the Tesseract program)"),
    ("pdfplumber", "pdfplumber", "0.10", "reading vector PDF plans in 2D-to-BIM reconstruction"),
)


@dataclass(frozen=True)
class Finding:
    name: str
    status: Status
    detail: str
    fix: str = ""


def _version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", text)[:3])


def _declared_python(root: Path | None) -> tuple[tuple[int, ...], tuple[int, ...]] | None:
    """``(minimum, exclusive maximum)`` from ``requires-python`` in the SLAI checkout's ``pyproject.toml``, when it states both."""
    if root is None or not (root / "pyproject.toml").is_file():
        return None
    found = re.search(r'requires-python\s*=\s*"([^"]+)"', (root / "pyproject.toml").read_text(encoding="utf-8"))
    if not found:
        return None
    low = re.search(r">=\s*([\d.]+)", found.group(1))
    high = re.search(r"<\s*([\d.]+)", found.group(1))
    if not low or not high:
        return None
    return _version_tuple(low.group(1)), _version_tuple(high.group(1))


def check_python(root: Path | None, version: tuple[int, ...] | None = None) -> Finding:
    v = version or tuple(sys.version_info[:3])
    here = ".".join(map(str, v))
    declared = _declared_python(root)
    if declared is None:
        return Finding("Python", "ok" if v >= (3, 10) else "error", f"{here} ({sys.executable}); SLAI declares no range here, SPADRA needs 3.10 or newer",
                       "" if v >= (3, 10) else "Start SPADRA with Python 3.10 or newer.")
    low, high = declared
    span = f">={'.'.join(map(str, low))},<{'.'.join(map(str, high))}"
    if v[: len(low)] < low:
        return Finding("Python", "error", f"{here} ({sys.executable}) is older than SLAI's {span}",
                       f"Run SPADRA with the SLAI environment's Python (the one `pip install -r requirements.txt` was run with) instead of {sys.executable}.")
    if v[: len(high)] >= high:
        return Finding("Python", "warn", f"{here} ({sys.executable}) is newer than SLAI's declared {span}; SLAI's own packages were not qualified on it",
                       "Use the SLAI environment's Python if anything misbehaves.")
    return Finding("Python", "ok", f"{here} ({sys.executable}), inside SLAI's {span}")


def check_environment_marker(root: Path | None) -> Finding:
    """Whether the interpreter is a virtual environment, and where SLAI's own is. Reported, never acted on."""
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    own = [root / name for name in ("venv", ".venv") if root and (root / name).is_dir()] if root else []
    if in_venv:
        inside = bool(own) and any(Path(sys.prefix).resolve() == p.resolve() for p in own)
        return Finding("Environment", "ok", f"virtual environment {sys.prefix}" + (" (SLAI's own)" if inside else ""))
    if own:
        return Finding("Environment", "warn", f"not running in a virtual environment, but SLAI has one at {own[0]}",
                       f"Activate it first ({own[0] / ('Scripts' if sys.platform == 'win32' else 'bin') / 'activate'}), then start SPADRA again.")
    return Finding("Environment", "ok", f"{sys.prefix} (no virtual environment; SLAI has none in {root or 'an unknown place'})")


def _installed(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def check_dependency(distribution: str, module: str, minimum: str, why: str, *, required: bool,
                     installed: Callable[[str], str | None] = _installed, importable: Callable[[str], bool] | None = None) -> Finding:
    can_import = importable or (lambda name: importlib.util.find_spec(name) is not None)
    version = installed(distribution)
    install = f"{Path(sys.executable).name} -m pip install -r {HERE / 'requirements.txt'}"
    if version is None or not can_import(module):
        return Finding(distribution, "error" if required else "warn", f"not installed; needed for {why}",
                       install if required else f"{install}   (without it SPADRA still starts; {why} is unavailable)")
    if _version_tuple(version) < _version_tuple(minimum):
        return Finding(distribution, "error" if required else "warn", f"{version} is older than the {minimum} SPADRA was tested from; needed for {why}",
                       f"{Path(sys.executable).name} -m pip install --upgrade \"{distribution}>={minimum}\"")
    return Finding(distribution, "ok", f"{version}")


def check_slai(root: Path | None) -> Finding:
    if root is None:
        return Finding("SLAI checkout", "warn", "not found, so SLDC reports, the agents and the shared icons are unavailable",
                       "Start from the SLAI root (`python -m applications.SPADRA`) or pass --slai-root.")
    return Finding("SLAI checkout", "ok", str(root))


BUILD_RECORD = "build.json"


def _input_files(app_dir: Path) -> list[Path]:
    """Every file the client build reads: the application's ``src``, the shared UI kit's ``src`` and the two TypeScript configurations."""
    tools = app_dir.parent / "tools"
    files: list[Path] = []
    for place in (app_dir / "src", tools / "web" / "src"):
        if place.is_dir():
            files += [f for f in place.rglob("*") if f.is_file() and "node_modules" not in f.parts]
    files += [f for f in (app_dir / "tsconfig.build.json", tools / "tsconfig.json") if f.is_file()]
    return sorted(files, key=lambda f: f.relative_to(app_dir.parent).as_posix())


def client_inputs(app_dir: Path) -> dict[str, str]:
    """Path (relative to ``applications/``, with ``/``) -> SHA-256 of its bytes, for every file the build reads. The build script writes the same map
    to ``dist/build.json``; the two are compared by content, so file times (clock differences between machines, files unpacked from an archive,
    copies that do not keep their times) cannot make a fresh build look stale."""
    return {f.relative_to(app_dir.parent).as_posix(): hashlib.sha256(f.read_bytes()).hexdigest() for f in _input_files(app_dir)}


def client_changes(app_dir: Path, client_dir: Path) -> list[str] | None:
    """The source files whose content differs from what the build was made from (changed, added or removed); ``None`` when the build has no record."""
    record = client_dir / BUILD_RECORD
    try:
        built = json.loads(record.read_text(encoding="utf-8"))["files"]
    except (OSError, ValueError, KeyError):
        return None
    now = client_inputs(app_dir)
    return sorted({k for k in now if built.get(k) != now[k]} | {k for k in built if k not in now})


def client_missing_outputs(app_dir: Path, client_dir: Path) -> list[str]:
    """Compiled modules that should exist in ``dist/js`` for the sources there are (a build that stopped half way, or output that was deleted)."""
    out = []
    for place, target in ((app_dir / "src" / "client", client_dir / "js" / app_dir.name / "src" / "client"), (app_dir.parent / "tools" / "web" / "src", client_dir / "js" / "tools" / "web" / "src")):
        if not place.is_dir():
            continue
        for ts in place.glob("*.ts"):
            if not ts.name.endswith(".d.ts") and not (target / (ts.stem + ".js")).is_file():
                out.append((target / (ts.stem + ".js")).relative_to(client_dir).as_posix())
    return sorted(out)


def client_sources_newer(app_dir: Path, client_dir: Path) -> bool:
    """For a build without a record (made by an older build script): True when something it was built from is newer than the built ``index.html``.
    File times are all there is to go on then; a build with a record is compared by content (``client_changes``)."""
    built = client_dir / "index.html"
    if not built.is_file():
        return True
    stamp = built.stat().st_mtime
    return any(f.stat().st_mtime > stamp for f in _input_files(app_dir))


def check_client(app_dir: Path, client_dir: Path) -> Finding:
    if not (client_dir / "index.html").is_file() or not (client_dir / "js").is_dir():
        return Finding("Web client", "error", f"not built ({client_dir / 'index.html'} is missing)", f"In {app_dir}: npm install, then npm run build (or start with --dev).")
    if not (app_dir / "src").is_dir():
        return Finding("Web client", "ok", str(client_dir))
    changed = client_changes(app_dir, client_dir)
    if changed is None:
        if client_sources_newer(app_dir, client_dir):
            return Finding("Web client", "warn", "built, but its sources are newer than the build (this build has no record, so only file times could be compared)",
                           f"In {app_dir}: npm run build (or start with --dev).")
        return Finding("Web client", "ok", str(client_dir))
    missing = client_missing_outputs(app_dir, client_dir)
    if missing:
        return Finding("Web client", "error", f"the build is incomplete: {len(missing)} compiled file(s) are missing, for example {missing[0]}", f"In {app_dir}: npm run build (or start with --dev).")
    if changed:
        shown = ", ".join(changed[:3]) + (f" and {len(changed) - 3} more" if len(changed) > 3 else "")
        return Finding("Web client", "warn", f"built, but {len(changed)} source file(s) changed since the build: {shown}", f"In {app_dir}: npm run build (or start with --dev).")
    return Finding("Web client", "ok", str(client_dir))


def check_agents() -> list[Finding]:
    """Which of the agents SPADRA integrates really import here (each is imported, not assumed)."""
    from .agent_hub import HUB, SPECS
    out: list[Finding] = []
    for spec in SPECS:
        if not spec.integrated:
            continue
        loaded = HUB.load(spec.key) is not None
        out.append(Finding(f"Agent: {spec.label}", "ok" if loaded else "warn", "imports" if loaded else (HUB.reason(spec.key) or "does not import"),
                           "" if loaded else "SPADRA still starts; the features that use it report why they are unavailable."))
    return out


def check_commit_adapters() -> Finding:
    """Whether SLAI's commit adapters are importable. Approving an assistant proposal goes through them, so without them it is refused (with this reason)."""
    from ..tools.commit_host import planning_types
    from ..tools.errors import DependencyMissing
    try:
        planning_types()
    except DependencyMissing as exc:
        return Finding("Commit adapters", "warn", "src/agents/planning/project_adapter.py is not importable; approving assistant proposals will be refused until it is",
                       str(exc.details.get("import_error", "")) if getattr(exc, "details", None) else "")
    return Finding("Commit adapters", "ok", "src/agents/planning/project_adapter.py imports")


def check_environment(root: Path | None, client_dir: Path, *, app_dir: Path = HERE, agents: bool = True) -> list[Finding]:
    found = [check_python(root), check_environment_marker(root), check_slai(root)]
    found += [check_dependency(*r, required=True) for r in REQUIRED] + [check_dependency(*r, required=False) for r in OPTIONAL]
    found.append(check_client(app_dir, client_dir))
    if root is not None:
        found.append(check_commit_adapters())
    if agents and root is not None:
        found += check_agents()
    return found


def blocking(findings: Sequence[Finding]) -> list[Finding]:
    return [f for f in findings if f.status == "error"]


def render(findings: Sequence[Finding]) -> str:
    mark = {"ok": "ok   ", "warn": "WARN ", "error": "ERROR"}
    lines = []
    for f in findings:
        lines.append(f"  [{mark[f.status]}] {f.name}: {f.detail}")
        if f.fix and f.status != "ok":
            lines.append(f"          -> {f.fix}")
    bad, warn = len(blocking(findings)), sum(f.status == "warn" for f in findings)
    lines.append(f"{'Cannot start' if bad else 'Ready'}: {bad} blocking problem(s), {warn} warning(s).")
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------------------------ the build
def npm_command() -> str | None:
    """``npm.cmd`` on Windows (a bare ``npm`` is a script there), ``npm`` elsewhere. ``None`` when Node is not installed."""
    return shutil.which("npm.cmd" if sys.platform == "win32" else "npm")


def build_client(app_dir: Path = HERE, *, run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> None:
    """``npm run build`` in the application folder: the shared static build (``applications/tools/build/build-static.mjs``)."""
    npm = npm_command()
    if npm is None:
        raise StartupError("The web client has to be built, but Node.js (npm) was not found.", hint="Install Node.js 20 or newer, then run `npm install` and `npm run build` in " + str(app_dir))
    if not (app_dir / "node_modules").is_dir():
        raise StartupError(f"The web client has to be built, but its tools are not installed ({app_dir / 'node_modules'} is missing).", hint=f"Run `npm install` in {app_dir} first.")
    done = run([npm, "run", "build"], cwd=app_dir, capture_output=True, text=True)
    if done.returncode != 0:
        tail = "\n".join((done.stdout + done.stderr).strip().splitlines()[-25:])
        raise StartupError("`npm run build` failed, so the web client was not rebuilt.", hint=f"Fix the errors below, then run `npm run build` in {app_dir}.", details={"output": tail, "exit": done.returncode})
