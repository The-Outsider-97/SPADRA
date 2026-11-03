"""``python -m applications.SPADRA.bundle``: a desktop bundle of SPADRA, one zip that runs without the rest of the SLAI checkout.

SLAI has no installer or freezing convention of its own (``deployment/`` is git and model release tooling), and every SLAI application
runs from a checkout. The practical thing for a desktop is therefore a **source bundle**: the files SPADRA reads while it runs, laid out
exactly as in the checkout, so that ``python -m applications.SPADRA`` works from the unpacked folder with the user's own Python. It is
not an executable and it does not contain Python or the packages in ``requirements.txt``; ``--check`` says what is missing.

Contents: SPADRA with its built client (``dist``), the shared server kit, the SLAI library packages SPADRA and its agents import
(``src``, ``checkpointing``, ``logs``: code and configuration only), ``applications/assets`` when the checkout has it, and ``BUNDLE.json``
with the SHA-256 of every file. Tests, TypeScript sources, ``node_modules`` and caches are left out.

A bundle is only a release once ``tests/test_bundle.py`` has unpacked one and started it; building the zip proves nothing about that.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from typing import Iterator

from . import __version__
from .startup import check_client
from .utils.spadra_errors import StartupError

HERE = Path(__file__).resolve().parent
CODE = {".py", ".yaml", ".yml"}
DATA = CODE | {".json", ".txt", ".md"}
SKIP_DIRS = {"__pycache__", "node_modules", ".git", ".backups", "tests", "tests_ts", "tests_e2e", "test", "typings"}
LIBRARY_MAX_BYTES = 2_000_000


def _files(folder: Path, suffixes: set[str] | None = None, *, skip: set[str] = frozenset()) -> Iterator[Path]:
    """Files under ``folder``; ``skip`` names folders directly inside it (the compiled client keeps a ``src`` folder of its own further down)."""
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder)
        if not path.is_file() or any(part in SKIP_DIRS for part in rel.parts[:-1]) or rel.parts[0] in skip or path.name.startswith("."):
            continue
        if suffixes is None or path.suffix.lower() in suffixes:
            yield path


def collect(root: Path) -> list[Path]:
    """Every file of the bundle, as paths in the checkout ``root``."""
    apps = root / "applications"
    app = apps / "SPADRA"            # not HERE: the checkout may reach this folder through a link
    picked: list[Path] = [apps / "__init__.py", root / "pyproject.toml"]
    picked += [p for p in _files(app, None, skip={"src", "docs"}) if p.suffix in {".py", ".yaml", ".yml", ".html", ".css", ".js", ".json", ".svg", ".png", ".txt", ".md"}
               and p.name not in {"package-lock.json", "tsconfig.json", "tsconfig.build.json", "package.json"}]
    picked += [p for p in _files(app / "docs", {".md"})]
    tools = apps / "tools"
    picked += [p for p in _files(tools, None, skip={"build", "src"}) if p.suffix in {".py", ".css", ".woff2", ".svg", ".png", ".md"}]
    if (apps / "assets").is_dir():
        picked += list(_files(apps / "assets"))
    for package in ("src", "checkpointing", "logs"):
        folder = root / package
        if folder.is_dir():
            picked += [p for p in _files(folder, DATA if package == "src" else CODE) if p.stat().st_size <= LIBRARY_MAX_BYTES]
    return [p for p in dict.fromkeys(picked) if p.is_file()]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def build_bundle(root: Path, out_dir: Path) -> Path:
    """Write ``SPADRA-<version>-bundle.zip`` in ``out_dir`` from the checkout ``root``. Refuses to bundle a client that is not built."""
    client = check_client(HERE, HERE / "dist")
    if client.status == "error":
        raise StartupError(f"There is nothing to bundle: the web client is {client.detail}.", hint=client.fix)
    files = collect(root)
    manifest = {"name": "SPADRA", "version": __version__, "python": ">=3.10", "start": "python -m applications.SPADRA",
                "files": {p.relative_to(root).as_posix(): _sha256(p) for p in files}}
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"SPADRA-{__version__}-bundle.zip"
    temp = target.with_suffix(".zip.part")
    with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as z:
        for path in files:
            z.write(path, f"SPADRA/{path.relative_to(root).as_posix()}")
        z.writestr("SPADRA/BUNDLE.json", json.dumps(manifest, indent=1, sort_keys=True))
    temp.replace(target)
    return target


def verify(folder: Path) -> list[str]:
    """Problems found when comparing an unpacked bundle with its ``BUNDLE.json`` (missing or changed files); empty when it is intact."""
    manifest = json.loads((folder / "BUNDLE.json").read_text(encoding="utf-8"))
    problems = []
    for rel, digest in manifest["files"].items():
        path = folder / rel
        if not path.is_file():
            problems.append(f"missing: {rel}")
        elif _sha256(path) != digest:
            problems.append(f"changed: {rel}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m applications.SPADRA.bundle", description="Build a SPADRA desktop bundle (a source bundle; see the module docstring).")
    parser.add_argument("--out", default="dist-bundle", help="folder to write the zip to (default: ./dist-bundle)")
    parser.add_argument("--slai-root", help="the SLAI checkout (default: found automatically)")
    args = parser.parse_args(argv)
    from ..tools import slai
    root = slai.find_slai_root(args.slai_root)
    if root is None:
        print("The SLAI checkout was not found. Run from the SLAI root or pass --slai-root.", file=sys.stderr)
        return 2
    try:
        target = build_bundle(root, Path(args.out))
    except StartupError as exc:
        print(f"{exc.message} {exc.hint or ''}".strip(), file=sys.stderr)
        return 1
    with zipfile.ZipFile(target) as z:
        count = len(z.namelist())
    print(f"Wrote {target} ({target.stat().st_size / 1e6:.1f} MB, {count} files). It has not been test-started by this command: see tests/test_bundle.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
