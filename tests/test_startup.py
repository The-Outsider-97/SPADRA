"""Start-up: the environment check, the client build, the launch options, and requirements.txt agreeing with what the check looks for."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from .. import startup as ST
from ..utils.spadra_errors import StartupError

APP = Path(__file__).resolve().parents[1]
CHECKOUT = Path(__file__).parents[3]                  # the folder `python -m applications.SPADRA` is run from (not resolved: the test tree links in)


def run_spadra(*args: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "SLAI_ROOT"}
    return subprocess.run([sys.executable, "-m", "applications.SPADRA", *args], cwd=CHECKOUT, capture_output=True, text=True, timeout=timeout, env=env)


class PythonCheck(unittest.TestCase):
    def root_declaring(self, spec: str) -> Path:
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        (Path(tmp.name) / "pyproject.toml").write_text(f'[project]\nrequires-python = "{spec}"\n')
        return Path(tmp.name)

    def test_inside_the_declared_range_is_ok(self) -> None:
        self.assertEqual(ST.check_python(self.root_declaring(">=3.10,<3.13"), (3, 11, 4)).status, "ok")

    def test_older_than_the_range_stops_start_up_and_names_the_fix(self) -> None:
        f = ST.check_python(self.root_declaring(">=3.10,<3.13"), (3, 9, 18))
        self.assertEqual(f.status, "error")
        self.assertIn("3.9.18", f.detail); self.assertIn(">=3.10,<3.13", f.detail); self.assertIn("SLAI environment", f.fix)

    def test_newer_than_the_range_only_warns(self) -> None:
        f = ST.check_python(self.root_declaring(">=3.10,<3.13"), (3, 13, 1))
        self.assertEqual(f.status, "warn"); self.assertIn("newer", f.detail)

    def test_without_a_declared_range_ten_is_the_floor(self) -> None:
        self.assertEqual(ST.check_python(None, (3, 10, 0)).status, "ok")
        self.assertEqual(ST.check_python(None, (3, 8, 0)).status, "error")

    def test_it_reports_the_interpreter_it_ran_on_and_never_looks_for_another(self) -> None:
        self.assertIn(sys.executable, ST.check_python(None).detail)
        source = (APP / "startup.py").read_text(encoding="utf-8")
        self.assertNotIn("venv.create", source); self.assertNotIn("os.execv", source); self.assertNotIn("pip\", \"install", source)


class Dependencies(unittest.TestCase):
    def check(self, installed: str | None, importable: bool, *, required: bool, minimum: str = "6.0"):
        return ST.check_dependency("PyYAML", "yaml", minimum, "the configuration repository", required=required,
                                   installed=lambda _: installed, importable=lambda _: importable)

    def test_missing_required_blocks_with_the_install_command(self) -> None:
        f = self.check(None, False, required=True)
        self.assertEqual(f.status, "error"); self.assertIn("pip install -r", f.fix); self.assertIn("requirements.txt", f.fix)

    def test_missing_optional_only_warns_and_says_what_still_works(self) -> None:
        f = self.check(None, False, required=False)
        self.assertEqual(f.status, "warn"); self.assertIn("still starts", f.fix)

    def test_distribution_without_an_importable_module_is_missing(self) -> None:
        self.assertEqual(self.check("6.0.3", False, required=True).status, "error")

    def test_old_version_is_flagged_with_the_upgrade_command(self) -> None:
        f = self.check("5.4.1", True, required=True)
        self.assertEqual(f.status, "error"); self.assertIn("--upgrade", f.fix)

    def test_current_version_is_ok(self) -> None:
        self.assertEqual(self.check("6.0.3", True, required=True).status, "ok")

    def test_version_comparison_is_numeric_not_textual(self) -> None:
        self.assertEqual(self.check("10.0", True, required=True, minimum="9.0").status, "ok")


class Client(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.app = Path(tmp.name) / "App"
        (self.app / "src" / "client").mkdir(parents=True)
        (self.app / "src" / "client" / "main.ts").write_text("export {};")
        (self.app / "tsconfig.build.json").write_text("{}")
        self.dist = self.app / "dist"

    def build(self) -> None:
        (self.dist / "js").mkdir(parents=True, exist_ok=True)
        (self.dist / "index.html").write_text("<!doctype html>")
        past = time.time() + 5
        os.utime(self.dist / "index.html", (past, past))

    def test_not_built_is_an_error_naming_the_command(self) -> None:
        f = ST.check_client(self.app, self.dist)
        self.assertEqual(f.status, "error"); self.assertIn("npm run build", f.fix)

    def test_built_is_ok_and_newer_sources_make_it_stale(self) -> None:
        self.build()
        self.assertEqual(ST.check_client(self.app, self.dist).status, "ok")
        later = time.time() + 60
        os.utime(self.app / "src" / "client" / "main.ts", (later, later))
        self.assertEqual(ST.check_client(self.app, self.dist).status, "warn")

    def test_a_dist_without_its_scripts_is_not_a_build(self) -> None:
        self.dist.mkdir(); (self.dist / "index.html").write_text("x")
        self.assertEqual(ST.check_client(self.app, self.dist).status, "error")


class Build(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.app = Path(tmp.name)

    def test_without_node_it_says_so(self) -> None:
        with mock.patch.object(ST, "npm_command", return_value=None):
            with self.assertRaises(StartupError) as caught:
                ST.build_client(self.app)
        self.assertIn("Node.js", caught.exception.message)

    def test_without_installed_tools_it_asks_for_npm_install(self) -> None:
        with mock.patch.object(ST, "npm_command", return_value="npm"):
            with self.assertRaises(StartupError) as caught:
                ST.build_client(self.app)
        self.assertIn("npm install", caught.exception.hint)

    def test_it_runs_npm_run_build_in_the_application_folder(self) -> None:
        (self.app / "node_modules").mkdir()
        calls = []
        def fake(cmd, **kw):
            calls.append((cmd, kw["cwd"])); return SimpleNamespace(returncode=0, stdout="", stderr="")
        with mock.patch.object(ST, "npm_command", return_value="npm.cmd"):
            ST.build_client(self.app, run=fake)
        self.assertEqual(calls, [(["npm.cmd", "run", "build"], self.app)])

    def test_a_failed_build_raises_with_the_end_of_its_output(self) -> None:
        (self.app / "node_modules").mkdir()
        out = "\n".join(f"line {i}" for i in range(100)) + "\nerror TS2322: Type 'x' is not assignable"
        with mock.patch.object(ST, "npm_command", return_value="npm"):
            with self.assertRaises(StartupError) as caught:
                ST.build_client(self.app, run=lambda *a, **k: SimpleNamespace(returncode=2, stdout=out, stderr=""))
        self.assertIn("TS2322", caught.exception.details["output"]); self.assertNotIn("line 10\n", caught.exception.details["output"])
        self.assertEqual(caught.exception.details["exit"], 2)

    def test_npm_command_is_the_cmd_script_on_windows(self) -> None:
        with mock.patch.object(sys, "platform", "win32"), mock.patch.object(ST.shutil, "which", side_effect=lambda n: n):
            self.assertEqual(ST.npm_command(), "npm.cmd")


class CommitAdapters(unittest.TestCase):
    def test_present_adapters_are_ok(self) -> None:
        with mock.patch("applications.tools.commit_host.planning_types", return_value=object()):
            self.assertEqual(ST.check_commit_adapters().status, "ok")

    def test_absent_adapters_only_warn_and_say_what_is_refused(self) -> None:
        from applications.tools.errors import DependencyMissing
        with mock.patch("applications.tools.commit_host.planning_types", side_effect=DependencyMissing("missing", details={"import_error": "No module named 'x'"})):
            found = ST.check_commit_adapters()
        self.assertEqual(found.status, "warn")                                  # SPADRA starts; only approving a proposal is refused
        self.assertIn("approving assistant proposals will be refused", found.detail)
        self.assertIn("No module named 'x'", found.fix)


class Report(unittest.TestCase):
    def test_render_counts_problems_and_shows_fixes_only_where_needed(self) -> None:
        text = ST.render([ST.Finding("A", "ok", "fine", "never shown"), ST.Finding("B", "warn", "meh", "do x"), ST.Finding("C", "error", "bad", "do y")])
        self.assertIn("Cannot start: 1 blocking problem(s), 1 warning(s).", text)
        self.assertIn("-> do x", text); self.assertIn("-> do y", text); self.assertNotIn("never shown", text)

    def test_ready_when_nothing_blocks(self) -> None:
        self.assertTrue(ST.render([ST.Finding("A", "warn", "meh")]).endswith("Ready: 0 blocking problem(s), 1 warning(s)."))


class Requirements(unittest.TestCase):
    def test_requirements_file_lists_exactly_what_the_check_looks_for(self) -> None:
        listed = {}
        for line in (APP / "requirements.txt").read_text(encoding="utf-8").splitlines():
            m = re.match(r"^([A-Za-z][\w.-]*)\s*>=\s*([\d.]+)", line)
            if m:
                listed[m.group(1).lower()] = m.group(2)
        checked = {name.lower(): minimum for name, _, minimum, _ in ST.REQUIRED + ST.OPTIONAL}
        self.assertEqual(listed, checked)

    def test_every_third_party_module_the_application_imports_is_declared(self) -> None:
        declared = {module for _, module, _, _ in ST.REQUIRED + ST.OPTIONAL}
        stdlib = set(sys.stdlib_module_names)
        seen = set()
        for path in list(APP.glob("*.py")) + list((APP / "utils").glob("*.py")) + list((APP.parent / "tools").rglob("*.py")):
            if "tests" in path.parts:
                continue
            for m in re.finditer(r"^\s*(?:import|from)\s+([A-Za-z_]\w*)", path.read_text(encoding="utf-8"), re.M):
                seen.add(m.group(1))
        third = {n for n in seen if n not in stdlib and n not in {"applications", "src", "logs", "checkpointing", "tools"}}
        self.assertEqual(third - declared, set(), "imported but not in requirements.txt / the dependency check")

    def test_npm_scripts_follow_the_shared_build_convention(self) -> None:
        scripts = json.loads((APP / "package.json").read_text(encoding="utf-8"))["scripts"]
        self.assertEqual(scripts["build"], "node ../tools/build/build-static.mjs")
        self.assertEqual(scripts["start"], "python -m applications.SPADRA")
        for name in ("start:dev", "check", "package", "typecheck", "test", "test:py", "test:e2e", "test:all"):
            self.assertIn(name, scripts)


class Launch(unittest.TestCase):
    """The real command from the SLAI root, as a person would run it."""

    def test_check_reports_ready_here(self) -> None:
        done = run_spadra("--check")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("Ready: 0 blocking problem(s)", done.stdout); self.assertIn("Agent: STEM Agent", done.stdout)

    def test_check_fails_with_the_fix_when_the_client_is_not_built(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            done = run_spadra("--check", "--client-dir", empty)
        self.assertEqual(done.returncode, 1)
        self.assertIn("Cannot start", done.stdout); self.assertIn("npm run build", done.stdout)

    def test_production_start_refuses_a_missing_client_with_a_message_and_does_not_serve(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            done = run_spadra("--no-browser", "--port", "0", "--client-dir", empty, timeout=30)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("Web client", done.stderr); self.assertIn("npm run build", done.stderr)

    def test_check_build_and_dev_exclude_each_other(self) -> None:
        self.assertEqual(run_spadra("--check", "--dev").returncode, 2)

    def test_a_failed_dev_build_says_why_and_exits_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            with mock.patch.dict(os.environ, {"PATH": empty}):
                done = run_spadra("--build")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertTrue("Node.js" in done.stderr or "npm install" in done.stderr, done.stderr)


if __name__ == "__main__":
    unittest.main()
