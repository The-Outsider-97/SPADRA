"""SPADRA's utils: typed errors, helpers, and the configuration / resource-plan loader, against the shipped files and hostile ones."""
from __future__ import annotations

import contextlib
import io
import re
import tempfile
import unittest
from pathlib import Path

from applications.SPADRA import app as spadra_app
from applications.SPADRA.project import Project
from applications.SPADRA.utils import config_loader as CL
from applications.SPADRA.utils.spadra_errors import BackupDamaged, ConfigError, FeatureUnavailable, PlanError, PlanLimitExceeded, SpadraError
from applications.SPADRA.utils.spadra_helpers import MM_PER, UNITS, clamp, format_bytes, now_iso, sha256_hex, slug
from applications.tools import errors as shared
from applications.tools import slai

from .harness import ServerCase


class Errors(unittest.TestCase):
    def test_they_extend_the_shared_hierarchy_so_the_http_layer_renders_them(self):
        for cls in (SpadraError, ConfigError, PlanError, PlanLimitExceeded, BackupDamaged, FeatureUnavailable):
            self.assertTrue(issubclass(cls, shared.AppError), cls.__name__)
        self.assertTrue(issubclass(PlanError, shared.BadRequest) and issubclass(BackupDamaged, shared.BadRequest))
        self.assertTrue(issubclass(PlanLimitExceeded, shared.LimitExceeded) and issubclass(FeatureUnavailable, shared.DependencyMissing))
        e = PlanLimitExceeded("Too big for the compact plan", hint="Use the standard plan", details={"limit": 5, "value": 9})
        self.assertEqual((e.status, e.to_dict()), (413, {"code": "spadra_plan_limit", "message": "Too big for the compact plan", "hint": "Use the standard plan", "details": {"limit": 5, "value": 9}}))
        self.assertEqual((BackupDamaged("x").status, PlanError("x").status, ConfigError("x").status, FeatureUnavailable("x").status), (400, 400, 500, 503))
        self.assertEqual(len({c.code for c in (SpadraError, ConfigError, PlanError, PlanLimitExceeded, BackupDamaged, FeatureUnavailable)}), 6, "codes are distinct")


class Helpers(unittest.TestCase):
    def test_slug_keeps_names_safe_for_every_file_system(self):
        self.assertEqual(slug("Community Pavilion"), "Community Pavilion")
        self.assertEqual(slug('a/b\\c:d*e?"f<g>h|i'), "abcdefghi")
        self.assertEqual(slug("..hidden.."), "hidden")
        self.assertEqual(slug("///"), "Project")
        self.assertEqual(len(slug("x" * 200)), 80)

    def test_bytes_time_hash_and_clamp(self):
        self.assertEqual([format_bytes(v) for v in (0, 1023, 1536, 150 * 1024, 5 * 1024 ** 2)], ["0 B", "1023 B", "1.5 KB", "150 KB", "5.0 MB"])
        self.assertEqual([format_bytes(v) for v in (None, -1, float("nan"), True)], ["–"] * 4)
        self.assertRegex(now_iso(), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$")
        self.assertEqual(sha256_hex(b"abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        self.assertEqual((clamp(5, 0, 3), clamp(-1, 0, 3), clamp(2, 0, 3)), (3, 0, 2))
        self.assertEqual((UNITS, MM_PER["cm"]), (("mm", "cm", "m"), 10.0))


class Loader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.n = 0
        slai.activate(slai.find_slai_root())

    def write(self, text: str) -> Path:
        self.n += 1                                              # a fresh name each time: the repository caches by file identity
        path = self.dir / f"f{self.n}.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_the_shipped_files_load_and_standard_changes_nothing(self):
        loaded = CL.load_settings_values()
        self.assertEqual(loaded.plan.name, "standard")
        self.assertEqual(loaded.plan.source, str(CL.PLANS_PATH))
        self.assertEqual({k: loaded.values[k] for k in ("port", "undo_depth", "max_scene_vertices", "max_request_bytes", "recent_projects", "backups_kept")},
                         {"port": 8770, "undo_depth": 100, "max_scene_vertices": 400_000, "max_request_bytes": 4_194_304, "recent_projects": 12, "backups_kept": 5})
        self.assertEqual(loaded.notes, [])
        plans, _ = CL.load_plans(CL.PLANS_PATH)
        self.assertEqual(sorted(plans), ["compact", "standard"])
        for plan in plans.values():
            self.assertEqual(set(plan.limits), set(CL.LIMITED))

    def test_the_compact_plan_only_lowers_and_says_what_it_lowered(self):
        loaded = CL.load_settings_values(plan="compact")
        self.assertEqual(loaded.values["undo_depth"], 50)
        self.assertEqual(loaded.values["max_scene_vertices"], 150_000)
        self.assertEqual(loaded.values["backups_kept"], 2)
        self.assertEqual(loaded.values["max_request_bytes"], 4_194_304, "below the limit, so unchanged")
        self.assertEqual(loaded.values["port"], 8770)
        self.assertEqual(len(loaded.notes), 3)
        self.assertTrue(all(n.startswith("Plan 'compact' lowered") for n in loaded.notes))
        self.assertIn("undo_depth from 100 to 50", " ".join(loaded.notes))
        self.assertEqual(CL.load_settings_values(config=self.write("app:\n  undo_depth: 10\n"), plan="compact").values["undo_depth"], 10, "a smaller value is kept")

    def test_a_plan_named_in_the_config_file_is_used_and_an_unknown_one_is_refused_with_the_choices(self):
        self.assertEqual(CL.load_settings_values(config=self.write("app:\n  plan: compact\n")).plan.name, "compact")
        with self.assertRaises(PlanError) as caught:
            CL.load_settings_values(plan="enterprise")
        self.assertEqual((caught.exception.status, caught.exception.code), (400, "spadra_plan_unknown"))
        self.assertIn("compact", caught.exception.hint)
        self.assertEqual(caught.exception.details["known"], ["compact", "standard"])

    def test_wrong_values_name_the_file_and_the_setting(self):
        for text, setting in (("app:\n  undo_depth: 0\n", "undo_depth"), ("app:\n  port: 'x'\n", "port"), ("app:\n  backups_kept: 99\n", "backups_kept"),
                              ("app:\n  plan: 5\n", "plan"), ("app: [1]\n", "app")):
            path = self.write(text)
            with self.assertRaises(ConfigError, msg=text) as caught:
                CL.load_settings_values(config=path)
            self.assertIn(setting, str(caught.exception))
            self.assertEqual(caught.exception.details["file"], str(path))

    def test_a_bad_plans_file_is_refused_and_never_half_applied(self):
        good = "      max_request_bytes: 1000000\n"
        cases = {
            "schema_version: 2\nplans:\n  a:\n    limits: {}\n": "schema_version",
            "schema_version: 1\n": "plans",
            "schema_version: 1\nplans: {}\n": "plans",
            "schema_version: 1\nplans:\n  Big Plan:\n    limits: {}\n": "plan names",
            "schema_version: 1\nplans:\n  a: 5\n": "must be a mapping",
            "schema_version: 1\nplans:\n  a:\n    display_name: A\n": "limits",
            "schema_version: 1\nplans:\n  a:\n    limits:\n      colour: 3\n": "unknown setting",
            "schema_version: 1\nplans:\n  a:\n    limits:\n      undo_depth: 0\n": "undo_depth",
            "schema_version: 1\nplans:\n  a:\n    limits:\n      undo_depth: true\n": "undo_depth",
            "schema_version: 1\nplans:\n  a:\n    limits:\n      undo_depth: 5.5\n": "undo_depth",
            "schema_version: 1\nplans:\n  a:\n    limits:\n      port: 80\n": "unknown setting",
        }
        for text, needle in cases.items():
            path = self.write(text)
            with self.assertRaises(ConfigError, msg=text) as caught:
                CL.load_plans(path)
            self.assertIn(needle, str(caught.exception), text)
        plans, _ = CL.load_plans(self.write("schema_version: 1\nplans:\n  tiny:\n    description: d\n    limits:\n      undo_depth: 3\n"))
        self.assertEqual(plans["tiny"].limits["undo_depth"], 3)
        self.assertEqual(plans["tiny"].limits["backups_kept"], 50, "a setting left out is limited only by its own range")
        self.assertEqual(plans["tiny"].display_name, "Tiny")

    def test_missing_files_fall_back_to_defaults_and_say_so(self):
        loaded = CL.load_settings_values(config=self.dir / "none.yaml", plans=self.dir / "none-plans.yaml")
        self.assertEqual((loaded.values["undo_depth"], loaded.plan.name, loaded.plan.source), (100, "standard", "built-in"))
        self.assertEqual(len(loaded.notes), 2)
        self.assertTrue(all("built-in" in n for n in loaded.notes))
        with self.assertRaises(PlanError):
            CL.load_settings_values(config=self.dir / "none.yaml", plans=self.dir / "none-plans.yaml", plan="compact")

    def test_the_old_config_name_is_read_only_when_the_new_one_is_absent(self):
        self.assertEqual(CL.config_file(), CL.CONFIG_PATH)
        self.assertEqual(CL.config_file("x.yaml"), Path("x.yaml"))
        self.assertTrue(CL.CONFIG_PATH.is_file())

    def test_the_documented_defaults_in_the_shipped_file_match_the_declared_ones(self):
        text = CL.CONFIG_PATH.read_text(encoding="utf-8")
        for key, spec in CL.SETTINGS_FIELDS.items():
            match = re.search(rf"^\s+{key}:\s+(\S+)", text, re.M)
            self.assertIsNotNone(match, key)
            self.assertEqual(str(spec.default), match.group(1), key)


class Applied(ServerCase):
    def test_settings_carry_the_plan_and_the_server_reports_it(self):
        settings = spadra_app.load_settings(plan="compact", data_dir=str(self.dir / "d"))
        self.assertEqual((settings.plan.name, settings.backups_kept, settings.undo_depth, settings.max_scene_vertices), ("compact", 2, 50, 150_000))
        self.assertIn("lowered", settings.note)
        boot = self.api.get("/api/bootstrap")
        self.assertEqual(boot["plan"]["name"], "standard")
        self.assertEqual(set(boot["plan"]["limits"]), set(CL.LIMITED))

    def test_the_backup_count_comes_from_the_settings(self):
        self.app.settings.backups_kept = 2
        self.api.post("/api/project/new", {"name": "Few"})
        self.api.post("/api/project/save", {"name": "Few"})
        for _ in range(4):
            self.api.cmd("project.set", {"description": str(_)})
            self.api.post("/api/project/save", {})
        props = self.api.get("/api/project/properties")
        self.assertEqual((len(props["backups"]), props["backups_kept"]), (2, 2))

    def test_a_damaged_backup_is_reported_with_its_own_code(self):
        self.api.post("/api/project/new", {"name": "Dmg"})
        path = Path(self.api.post("/api/project/save", {"name": "Dmg"})["path"])
        self.api.cmd("project.set", {"description": "x"})
        backup = self.api.post("/api/project/save", {})["backup"]
        (path / ".backups" / backup / "model.sgeo").write_bytes(b"damaged")
        status, out = self.api.call("POST", "/api/project/recover", {"backup": backup, "discard": True})
        self.assertEqual((status, out["error"]["code"]), (400, "spadra_backup_damaged"))

    def test_the_command_line_refuses_an_unknown_plan_with_the_choices(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = spadra_app.main(["--plan", "enterprise", "--no-browser", "--data-dir", str(self.dir / "d2"), "--port", "8799"])
        self.assertEqual(code, 1)
        self.assertIn("enterprise", err.getvalue())
        self.assertIn("compact", err.getvalue())


if __name__ == "__main__":
    unittest.main()
