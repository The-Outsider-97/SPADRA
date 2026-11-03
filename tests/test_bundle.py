"""The desktop bundle: build it, unpack it somewhere else, start it with the real command, and use it. A bundle that has not passed this is not a release."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from .. import bundle as B
from ..utils.spadra_errors import StartupError
from .harness import TOKEN, Api


def library_root() -> Path | None:
    """The checkout whose ``src`` this process imports (the one with the agents), which is what the bundle must be built from."""
    spec = importlib.util.find_spec("src")
    if spec is None or not spec.submodule_search_locations:
        return None
    root = Path(next(iter(spec.submodule_search_locations))).parent
    return root if (root / "src" / "agents").is_dir() and (root / "src" / "utils" / "sldc.py").is_file() else None


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@unittest.skipIf(library_root() is None, "needs a SLAI checkout with src/agents")
class Bundle(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = library_root()
        cls.zip = B.build_bundle(cls.root, Path(cls.tmp.name) / "out")
        cls.where = Path(cls.tmp.name) / "unpacked"
        with zipfile.ZipFile(cls.zip) as z:
            cls.names = z.namelist()
            z.extractall(cls.where)
        cls.folder = cls.where / "SPADRA"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_the_bundle_is_intact_and_holds_what_is_needed_to_run(self) -> None:
        self.assertEqual(B.verify(self.folder), [])
        for needed in ("applications/SPADRA/app.py", "applications/SPADRA/dist/index.html", "applications/SPADRA/configs/spadra_config.yaml",
                       "applications/SPADRA/requirements.txt", "applications/tools/web/slai-ui.css", "applications/tools/server/http.py",
                       "src/utils/sldc.py", "src/utils/sgeo.py", "src/utils/sdrw.py", "src/agents/stem_agent.py", "src/agents/animation_agent.py"):
            self.assertTrue((self.folder / needed).is_file(), needed)

    def test_the_bundle_leaves_out_tests_sources_caches_and_dependencies(self) -> None:
        bad = [n for n in self.names if re.search(r"/(node_modules|__pycache__|tests|tests_e2e|tests_ts)/|\.pyc$|/package-lock\.json$|/tsconfig", n)]
        self.assertEqual(bad, [])
        self.assertEqual([n for n in self.names if n.endswith(".ts") or n.startswith("SPADRA/applications/SPADRA/src/")], [], "TypeScript sources are not shipped, only the built client")
        self.assertTrue([n for n in self.names if n.startswith("SPADRA/applications/SPADRA/dist/js/") and n.endswith("main.js")], "the compiled client is")

    def test_it_will_not_bundle_a_client_that_is_not_built(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            from unittest import mock
            from .. import startup
            with mock.patch.object(B, "check_client", return_value=startup.Finding("Web client", "error", "not built", "npm run build")):
                with self.assertRaises(StartupError):
                    B.build_bundle(self.root, Path(empty))

    def test_a_changed_file_is_found_by_the_check(self) -> None:
        copy = Path(self.tmp.name) / "tampered"
        with zipfile.ZipFile(self.zip) as z:
            z.extractall(copy)
        target = copy / "SPADRA" / "applications" / "SPADRA" / "geometry.py"
        target.write_text(target.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
        (copy / "SPADRA" / "applications" / "SPADRA" / "calculator.py").unlink()
        self.assertEqual(sorted(B.verify(copy / "SPADRA")), ["changed: applications/SPADRA/geometry.py", "missing: applications/SPADRA/calculator.py"])

    @staticmethod
    def stop(proc: subprocess.Popen[str]) -> None:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(10)
        proc.stdout.close()

    def start(self, *extra: str) -> subprocess.Popen[str]:
        env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "SLAI_ROOT"}}
        data = Path(self.tmp.name) / f"data-{time.monotonic_ns()}"
        self.port = free_port()
        proc = subprocess.Popen([sys.executable, "-m", "applications.SPADRA", "--no-browser", "--port", str(self.port), "--token", TOKEN, "--data-dir", str(data), *extra],
                                cwd=self.folder, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(self.stop, proc)
        self.lines: list[str] = []
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                self.fail(f"the bundle did not start (exit {proc.returncode}):\n" + "".join(self.lines))
            self.lines.append(line)
            if "Open" in line:
                return proc
        self.fail("the bundle did not print its address:\n" + "".join(self.lines))

    def test_check_from_the_unpacked_bundle_is_ready_and_names_the_bundle_as_the_checkout(self) -> None:
        env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "SLAI_ROOT"}}
        done = subprocess.run([sys.executable, "-m", "applications.SPADRA", "--check"], cwd=self.folder, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn(f"SLAI checkout: {self.folder.resolve()}", done.stdout)
        self.assertIn("Agent: Optimization Agent: imports", done.stdout)

    def test_the_unpacked_bundle_starts_with_the_documented_command_and_does_real_work(self) -> None:
        self.start()
        banner = "".join(self.lines)
        self.assertIn(f"SLAI      : {self.folder.resolve()}", banner, "it runs from the unpacked folder, not from the checkout it was built from")
        api = Api(self.port)
        status, _, page = api.raw("GET", "/")
        self.assertEqual(status, 200); self.assertIn(b"<title>", page)
        self.assertEqual(api.raw("GET", "/tools/slai-ui.css")[0], 200, "the shared UI kit is served from the bundle")
        agents = {a["key"]: a for a in api.get("/api/agents")["agents"]}
        for key in ("stem", "simulation", "spatial", "optimization", "animation"):
            self.assertTrue(agents[key]["available"], f"{key}: {agents[key]}")
        api.post("/api/project/new", {"name": "Bundled", "unit": "mm"})
        level = api.get("/api/project/tree")["levels"][0]["id"]
        for a, b in (([0, 0], [6000, 0]), ([6000, 0], [6000, 4000]), ([6000, 4000], [0, 4000]), ([0, 4000], [0, 0])):
            api.cmd("model.add_wall", {"start": a, "end": b, "level": level, "thickness": 200, "height": 3.0})
        walls = lambda: [o for o in api.get("/api/scene")["objects"] if o["category"] == "wall"]
        self.assertEqual(len(walls()), 4)
        saved = api.post("/api/project/save", {"name": "Bundled"})
        self.assertTrue(Path(saved["path"], "model.sgeo").is_file(), "a real SGEO file is written")
        api.post("/api/project/close")
        self.assertEqual(api.post("/api/project/open", {"path": saved["path"]})["name"], "Bundled")
        self.assertEqual(len(walls()), 4, "the saved project reopens with its walls")


if __name__ == "__main__":
    unittest.main()
