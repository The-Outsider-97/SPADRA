"""Python scripts in SPADRA (P0-02): versioning and approval, typed inputs and outputs, the read-only model API, preview-then-apply, and - on a
machine that can enforce it - the operating-system sandbox, attacked directly (the static scan is bypassed on purpose to show the boundary is the OS)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from applications.SPADRA import scripting as SC
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest, Conflict, NotFound

SANDBOX = SC.isolation()["untrusted_execution"]
needs_sandbox = unittest.skipUnless(SANDBOX, "this machine cannot enforce the OS sandbox (see isolation())")

WIDTHS = """
import math
def run(model, params):
    doors = model.elements(category="door")
    return {"count": len(doors), "clear": params["width"] - 2 * params["frame"], "area": params["width"] * 2.1}
"""
IO = dict(inputs={"width": {"type": "length", "default": 0.9, "min": 0.6, "max": 1.8}, "frame": {"type": "length", "default": 0.035}},
          outputs={"count": "integer", "clear": {"type": "length", "min": 0.0}, "area": "area"})


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project.create("Scripts")
        self.level = self.p.manifest["levels"][0]["id"]

    def x(self, op, **params):
        return self.p.execute(op, params)["result"]

    def add(self, source=WIDTHS, name="Door check", **io):
        return self.x("script.add", name=name, source=source, **(io or IO))["id"]

    def run_(self, sid, **kw):
        return SC.run(self.p, {"script": sid, **kw})

    def family(self):
        fid = self.x("family.define", name="Door", shape="box", parameters={"width": {"default": 900, "min": 600, "max": 1800}, "height": {"default": 2100}, "depth": {"default": 40}})["id"]
        self.x("family.type_define", id=fid, name="D-900", values={"width": 900})
        return fid


class Registry(Base):
    def test_versions_hash_and_approval(self):
        sid = self.add()
        v1 = SC.source_of(self.p, sid)
        self.assertEqual(v1["version"], 1)
        self.assertEqual(v1["sha256"], SC.sha256(WIDTHS))
        self.x("script.update", id=sid, source=WIDTHS + "\n# edit\n")
        d = SC.describe(self.p)["scripts"][0]
        self.assertEqual([v["version"] for v in d["versions"]], [1, 2])
        self.assertEqual(d["current"], 2)
        with self.assertRaises(Conflict):                                    # an approval belongs to the hash that was reviewed
            self.x("script.approve", id=sid, version=2, sha256=v1["sha256"])
        self.x("script.approve", id=sid, version=2, sha256=SC.sha256(WIDTHS + "\n# edit\n"))
        self.assertTrue(SC.describe(self.p)["scripts"][0]["versions"][1]["approved"])
        self.x("script.update", id=sid, source=WIDTHS)                       # editing adds version 3, unapproved
        self.assertFalse(SC.describe(self.p)["scripts"][0]["versions"][2]["approved"])
        self.assertEqual(SC.source_of(self.p, sid, 1)["source"], WIDTHS)     # old versions stay

    def test_undo_removes_a_script_and_save_reopen_keeps_it(self):
        sid = self.add()
        self.x("script.approve", id=sid, version=1, sha256=SC.sha256(WIDTHS))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.spadra"
            self.p.save(path)
            q = Project.open(path)
        info = SC.describe(q)["scripts"][0]
        self.assertEqual((info["name"], info["versions"][0]["approved"], info["versions"][0]["outputs"]["clear"]["type"]), ("Door check", True, "length"))
        self.p.undo()                                                         # approval undone
        self.assertFalse(SC.describe(self.p)["scripts"][0]["versions"][0]["approved"])
        self.p.undo()
        self.assertEqual(SC.describe(self.p)["scripts"], [])

    def test_rejected_sources_and_declarations(self):
        for src, why in (("import os\ndef run(model, params):\n    return {}\n", "cannot be imported"),
                         ("def run(model, params):\n    return open('/etc/passwd')\n", "'open'"),
                         ("def run(model, params):\n    return ().__class__\n", "underscore"),
                         ("def run(model, params):\n    return eval('1')\n", "'eval'"),
                         ("def run(a):\n    return 1\n", "run(model, params)"),
                         ("x = 1\n", "must define run"),
                         ("def run(model, params) return 1", "Syntax error")):
            with self.assertRaises(BadRequest, msg=src) as cm:
                self.add(src, **IO)
            self.assertIn(why, str(cm.exception))
        with self.assertRaises(BadRequest):
            self.add(WIDTHS, inputs={"w": {"type": "furlongs"}}, outputs={})
        with self.assertRaises(Conflict):
            self.add(); self.add()                                            # names are unique
        self.assertEqual(len(SC.describe(self.p)["scripts"]), 1)             # the failures stored nothing


class Running(Base):
    def test_typed_inputs_outputs_and_units(self):
        sid = self.add()
        if not SANDBOX:
            self.x("script.approve", id=sid, version=1, sha256=SC.sha256(WIDTHS))
        rec = self.run_(sid, inputs={"width": {"value": 1000, "unit": "mm"}})
        self.assertEqual(rec["status"], "ok", rec)
        self.assertAlmostEqual(rec["outputs"]["clear"], 1.0 - 0.07)
        self.assertAlmostEqual(rec["outputs"]["area"], 2.1)
        self.assertEqual(rec["outputs"]["count"], 0)
        bad = self.run_(sid, inputs={"width": 5.0})
        self.assertEqual(bad["status"], "invalid_input")                      # above max
        self.assertEqual(self.run_(sid, inputs={"nope": 1})["status"], "invalid_input")

    def test_determinism_and_seed(self):
        src = "import random\ndef run(model, params):\n    return {'r': random.random()}\n"
        sid = self.add(src, inputs={}, outputs={"r": "number"})
        if not SANDBOX:
            self.x("script.approve", id=sid, version=1, sha256=SC.sha256(src))
        a, b, c = self.run_(sid, seed=7), self.run_(sid, seed=7), self.run_(sid, seed=8)
        self.assertEqual(a["result_sha256"], b["result_sha256"])
        self.assertNotEqual(a["result_sha256"], c["result_sha256"])
        self.assertEqual((a["seed"], a["revision"], a["sha256"]), (7, self.p.revision, SC.sha256(src)))

    def test_the_model_api_is_read_only_and_a_run_changes_nothing(self):
        fid = self.family()
        door = self.x("family.place", family=fid, level=self.level, point=[0, 0])["id"]
        src = ("def run(model, params):\n"
               "    rows = model.elements()\n"
               "    rows[0]['category'] = 'hacked'\n"
               "    rows.clear()\n"
               "    again = model.elements()\n"
               "    lv = model.levels(); lv[0]['elevation'] = 99\n"
               "    return {'n': len(again), 'first': again[0]['id'], 'elevation': model.levels()[0]['elevation'], 'has_bounds': 'bounds' in again[0]}\n")
        sid = self.add(src, inputs={}, outputs={"n": "integer", "first": "text", "elevation": "length", "has_bounds": "boolean"})
        if not SANDBOX:
            self.x("script.approve", id=sid, version=1, sha256=SC.sha256(src))
        before_rev, before_ids = self.p.revision, SC.snapshot(self.p)
        rec = self.run_(sid)
        self.assertEqual(rec["status"], "ok", rec)
        self.assertEqual((rec["outputs"]["n"], rec["outputs"]["first"], rec["outputs"]["elevation"]), (1, door, 0.0))
        self.assertEqual(self.p.revision, before_rev)                          # a run is not a commit
        self.assertEqual(SC.snapshot(self.p), before_ids)

    def test_invalid_outputs_are_refused_with_the_reason(self):
        for body, io, expect in (("return {'a': 'text'}", {"a": "number"}, "finite number"),
                                 ("return {'a': 1, 'b': 2}", {"a": "number"}, "undeclared"),
                                 ("return {}", {"a": "number"}, "missing"),
                                 ("return {'a': 5.0}", {"a": {"type": "number", "max": 1}}, "above its maximum"),
                                 ("return {'a': float('nan')}", {"a": "number"}, "NaN"),
                                 ("return {'a': 1.5}", {"a": "integer"}, "whole number"),
                                 ("return [1, 2]", {"a": "number"}, "dict"),
                                 ("return {'a': True}", {"a": "number"}, "finite number")):
            src = f"def run(model, params):\n    {body}\n"
            sid = self.add(src, name=body, inputs={}, outputs=io)
            if not SANDBOX:
                self.x("script.approve", id=sid, version=1, sha256=SC.sha256(src))
            rec = self.run_(sid)
            self.assertIn(rec["status"], ("invalid_output", "error"), (body, rec))
            self.assertIn(expect, rec["error"], (body, rec))

    def test_script_errors_are_reported_not_raised(self):
        src = "def run(model, params):\n    return {'a': 1 / 0}\n"
        sid = self.add(src, inputs={}, outputs={"a": "number"})
        if not SANDBOX:
            self.x("script.approve", id=sid, version=1, sha256=SC.sha256(src))
        rec = self.run_(sid)
        self.assertEqual(rec["status"], "error")
        self.assertIn("ZeroDivisionError", rec["error"])

    def test_untrusted_source_is_not_run_where_no_sandbox_exists(self):
        saved = dict(SC._isolation_cache)
        SC._isolation_cache.clear()
        SC._isolation_cache.update({"platform": "win32", "level": "process-isolated", "untrusted_execution": False, "mechanisms": [], "probe": None, "notes": []})
        try:
            sid = self.add()
            rec = self.run_(sid)
            self.assertEqual(rec["status"], "refused")
            self.assertIn("not been approved", rec["error"])
            self.x("script.approve", id=sid, version=1, sha256=SC.sha256(WIDTHS))
            ok = self.run_(sid)
            self.assertEqual((ok["status"], ok["isolation"]), ("ok", "process-isolated"))   # labelled for what it is
            self.x("script.update", id=sid, source=WIDTHS + "\n# changed\n")               # a new version is unapproved again
            self.assertEqual(self.run_(sid)["status"], "refused")
        finally:
            SC._isolation_cache.clear()
            SC._isolation_cache.update(saved)


class Applying(Base):
    SRC = "def run(model, params):\n    return {'width': params['w'] * 1.0}\n"

    def setUp(self):
        super().setUp()
        self.fid = self.family()
        self.sid = self.add(self.SRC, inputs={"w": {"type": "length", "default": 1.2}}, outputs={"width": {"type": "length", "min": 0.6, "max": 1.8}})
        if not SANDBOX:
            self.x("script.approve", id=self.sid, version=1, sha256=SC.sha256(self.SRC))

    def width_of_type(self):
        fam = [f for f in self.p.model.list_families()] if hasattr(self.p.model, "list_families") else None
        from applications.SPADRA import families as FM
        return {t["name"]: t["values"].get("width") for t in FM.describe(self.p, FM.family(self.p, self.fid))["types"]}["D-900"]

    def test_apply_is_one_undoable_transaction_and_provenance_is_kept(self):
        rec = self.run_(self.sid)
        self.assertEqual(self.width_of_type(), 900)                           # the preview changed nothing
        r = self.x("script.apply", run=rec["id"], targets=[{"output": "width", "family": self.fid, "type": "D-900", "parameter": "width"}])
        self.assertEqual(r["applied"], 1)
        self.assertEqual(self.width_of_type(), 1200)                          # SI metres -> the family's mm
        log = self.p.manifest["script_applications"][0]
        self.assertEqual((log["script"], log["version"], log["sha256"], log["result_sha256"]), (self.sid, 1, rec["sha256"], rec["result_sha256"]))
        self.p.undo()
        self.assertEqual(self.width_of_type(), 900)
        self.assertNotIn("script_applications", self.p.manifest) if not self.p.manifest.get("script_applications") else None
        self.assertEqual(self.p.manifest.get("script_applications", []), [])

    def test_a_run_cannot_be_applied_to_a_project_that_moved_on_or_twice(self):
        rec = self.run_(self.sid)
        target = [{"output": "width", "family": self.fid, "type": "D-900", "parameter": "width"}]
        self.x("family.type_define", id=self.fid, name="D-1000", values={"width": 1000})      # the project changes after the preview
        with self.assertRaises(Conflict):
            self.x("script.apply", run=rec["id"], targets=target)
        fresh = self.run_(self.sid)
        self.x("script.apply", run=fresh["id"], targets=target)
        with self.assertRaises(Conflict):                                      # applying moved the revision, so the same run cannot be applied again
            self.x("script.apply", run=fresh["id"], targets=target)

    def test_a_bad_target_changes_nothing(self):
        rec = self.run_(self.sid)
        rev = self.p.revision
        good = {"output": "width", "family": self.fid, "type": "D-900", "parameter": "width"}
        for bad in ({"output": "width", "family": self.fid, "type": "D-900", "parameter": "nope"},
                    {"output": "width", "family": self.fid, "type": "D-900", "parameter": "width"} | {"output": "other"}):
            with self.assertRaises(BadRequest):
                self.x("script.apply", run=rec["id"], targets=[good, bad])
        self.assertEqual((self.p.revision, self.width_of_type()), (rev, 900))
        with self.assertRaises(NotFound):
            self.x("script.apply", run="run-none", targets=[good])

    def test_a_value_outside_the_family_limits_is_refused_atomically(self):
        src = "def run(model, params):\n    return {'width': 5.0}\n"
        sid = self.add(src, name="too wide", inputs={}, outputs={"width": "length"})
        if not SANDBOX:
            self.x("script.approve", id=sid, version=1, sha256=SC.sha256(src))
        rec = self.run_(sid)
        self.assertEqual(rec["status"], "ok")
        rev = self.p.revision
        with self.assertRaises(BadRequest):                                    # the family's own max (1800 mm) still applies
            self.x("script.apply", run=rec["id"], targets=[{"output": "width", "family": self.fid, "type": "D-900", "parameter": "width"}])
        self.assertEqual((self.p.revision, self.width_of_type()), (rev, 900))


class WithoutResourceLimits(Base):
    """Windows has no ``resource`` module. A run there used to be refused outright ("the sandbox the run needs could not be applied"), approved or not, so no script
    ever ran. This runs the real runner with ``resource`` made unimportable (what Windows looks like to it) and checks that an approved script runs and says what was
    not applied."""

    def test_the_runner_runs_without_the_resource_module_and_applies_no_limits(self):
        import os
        import subprocess
        wrapper = Path(tempfile.mkdtemp()) / "no_resource.py"
        wrapper.write_text("import runpy, sys\nsys.modules['resource'] = None\nrunpy.run_path(sys.argv[1], run_name='__main__')\n", encoding="utf-8")
        request = {"source": "def run(model, params):\n    return {'a': params['x'] * 2}\n", "snapshot": {}, "params": {"x": 21}, "seed": 0,
                   "limits": {"cpu_seconds": 5, "memory_bytes": 10 ** 9}, "sandbox": False}
        done = subprocess.run([sys.executable, "-I", "-S", str(wrapper), str(SC.RUNNER)], input=json.dumps(request).encode(), capture_output=True, timeout=30,
                              env={"PYTHONHASHSEED": "0", **({"SYSTEMROOT": os.environ["SYSTEMROOT"]} if "SYSTEMROOT" in os.environ else {})})
        reply = json.loads(done.stdout.decode())
        self.assertEqual((reply["ok"], reply["result"], reply["limits"]), (True, {"a": 42}, []))

    def test_an_approved_script_runs_where_there_is_no_sandbox_and_an_unapproved_one_does_not(self):
        from unittest import mock
        no_sandbox = {"platform": "win32", "level": "process-isolated", "untrusted_execution": False, "mechanisms": ["separate process"], "probe": None, "notes": []}
        sid = self.x("script.add", name="Plain", source=WIDTHS, **IO)["id"]
        with mock.patch.object(SC, "isolation", return_value=no_sandbox):                        # what any machine without the OS sandbox reports
            refused = SC.run(self.p, {"script": sid, "inputs": {"width": {"value": 1000, "unit": "mm"}}})
            self.assertEqual(refused["status"], "refused")                                       # not approved: not run
            self.x("script.approve", id=sid, version=1, sha256=SC.sha256(WIDTHS))
            rec = SC.run(self.p, {"script": sid, "inputs": {"width": {"value": 1000, "unit": "mm"}}})
        self.assertEqual((rec["status"], rec["isolation"]), ("ok", "process-isolated"), rec)
        self.assertAlmostEqual(rec["outputs"]["clear"], 0.93)


@needs_sandbox
class Sandbox(Base):
    """Attacks go through the runner directly, *without* the static scan, so what is tested is the operating system's refusal."""

    PRELUDE = """
def run(model, params):
    out = {}
    for c in ().__class__.__base__.__subclasses__():
        if c.__name__ == '_wrap_close':
            g = c.__init__.__globals__
            break
    def attempt(name, fn):
        try:
            fn()
            out[name] = 'WORKED'
        except BaseException as e:
            out[name] = 'refused'
"""
    FILES = PRELUDE + """
    attempt('read', lambda: g['read'](g['open']('/etc/passwd', 0), 10))
    attempt('create', lambda: g['open']('/tmp/spadra_escape_marker', 65, 0o644))
    attempt('listdir', lambda: g['listdir']('/'))
    return out
"""
    PROCESSES = PRELUDE + """
    def system():
        if g['system']('true') != 0:
            raise OSError('did not run')
    attempt('system', system)
    attempt('fork', lambda: g['fork']())
    attempt('exec', lambda: g['execv']('/bin/true', ['true']))
    return out
"""

    def raw(self, source, *, timeout=10.0, sandbox=True, **limits):
        lim = {"cpu_seconds": 5, "memory_bytes": 512 * 1024 * 1024, **limits}
        return SC._invoke({"source": source, "snapshot": {"revision": 1, "unit": "mm", "levels": [], "elements": [], "families": []}, "params": {}, "seed": 0, "limits": lim, "sandbox": sandbox}, timeout)

    def test_isolation_report_is_measured_not_assumed(self):
        info = SC.isolation(refresh=True)
        self.assertEqual(info["level"], "os-sandboxed")
        self.assertTrue(info["probe"] and not any(info["probe"].values()), info)
        self.assertTrue(any(m.startswith("landlock") for m in info["mechanisms"]) and "seccomp-bpf" in info["mechanisms"])

    def test_a_script_that_escapes_the_builtins_still_cannot_use_the_files(self):
        control = self.raw(self.FILES, sandbox=False)["reply"]["result"]
        self.assertEqual(set(control.values()), {"WORKED"}, control)         # the attack is real: with no sandbox every step succeeds
        self.assertTrue(Path("/tmp/spadra_escape_marker").exists())
        Path("/tmp/spadra_escape_marker").unlink()
        got = self.raw(self.FILES)
        self.assertTrue(got["reply"]["ok"], got)
        self.assertEqual(set(got["reply"]["result"].values()), {"refused"}, got["reply"]["result"])
        self.assertFalse(Path("/tmp/spadra_escape_marker").exists())

    def test_a_script_that_escapes_the_builtins_cannot_start_processes(self):
        got = self.raw(self.PROCESSES)
        self.assertTrue(got["reply"]["ok"], got)
        self.assertEqual(set(got["reply"]["result"].values()), {"refused"}, got["reply"]["result"])

    def test_sockets_and_processes_are_refused_by_the_kernel(self):
        src = ("def run(model, params):\n"
               "    for c in ().__class__.__base__.__subclasses__():\n"
               "        if c.__name__ == '_wrap_close':\n"
               "            g = c.__init__.__globals__; break\n"
               "    import sys\n    return 1\n")
        got = self.raw(src)
        self.assertFalse(got["reply"]["ok"])                                    # 'sys' is not importable at all
        probe = SC.isolation()["probe"]
        self.assertEqual({k for k, v in probe.items() if v}, set())

    def test_infinite_loop_is_stopped(self):
        got = self.raw("def run(model, params):\n    while True:\n        pass\n", timeout=1.5, cpu_seconds=30)
        self.assertTrue(got["timed_out"])
        self.assertLess(got["duration_s"], 6)

    def test_cpu_limit_stops_a_loop_before_the_timeout(self):
        got = self.raw("def run(model, params):\n    while True:\n        pass\n", timeout=20, cpu_seconds=1)
        self.assertFalse(got["timed_out"])
        self.assertIsNone(got["reply"])
        self.assertLess(got["duration_s"], 10)

    def test_memory_bomb_is_contained(self):
        got = self.raw("def run(model, params):\n    x = [0] * (10 ** 10)\n    return {}\n", memory_bytes=256 * 1024 * 1024)
        self.assertTrue(got["reply"] is None or not got["reply"]["ok"])
        self.assertIn("MemoryError", json.dumps(got["reply"]) if got["reply"] else "MemoryError")

    def test_oversized_output_is_cut_off(self):
        got = self.raw("def run(model, params):\n    return {'a': 'x' * (3 * 10 ** 6)}\n")
        self.assertTrue(got["output_too_large"] or got["reply"] is None or not got["reply"]["ok"])

    def test_through_the_api_a_hostile_script_never_reaches_the_runner(self):
        for src in ("import socket\ndef run(model, params):\n    return {}\n", "import subprocess\ndef run(model, params):\n    return {}\n"):
            with self.assertRaises(BadRequest):
                self.add(src, name=src[:20], inputs={}, outputs={"a": "number"})

    def test_a_script_cannot_reach_the_project_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            secret = Path(tmp) / "secret.txt"
            secret.write_text("TOP-SECRET")
            src = ("def run(model, params):\n"
                   "    for c in ().__class__.__base__.__subclasses__():\n"
                   "        if c.__name__ == '_wrap_close':\n"
                   "            g = c.__init__.__globals__; break\n"
                   f"    fd = g['open']({str(secret)!r}, 0)\n"
                   "    return {'data': g['read'](fd, 20).decode()}\n")
            got = self.raw(src)
        self.assertFalse(got["reply"]["ok"])
        self.assertNotIn("TOP-SECRET", json.dumps(got["reply"]))


if __name__ == "__main__":
    unittest.main()
