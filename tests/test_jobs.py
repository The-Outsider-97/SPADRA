"""Phase 21 backend: background operations (progress, results, cancelling, the project changing underneath them), error categories and the diagnostics
log, and the parsed-file cache. Jobs run on the real server over HTTP, with the real engines; the few tests that need to hold a job in a known state use
a gated runner registered for the test only."""
from __future__ import annotations

import base64
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from applications.SPADRA import diagnostics as DG
from applications.SPADRA import interop as IO
from applications.SPADRA import jobs as JB
from applications.tools.errors import BadRequest, NotFound

from .harness import ServerCase
from .test_bim import Case


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


class Diagnose(unittest.TestCase):
    def test_every_code_has_a_category_and_a_recovery(self):
        for code, info in DG.CATEGORIES.items():
            self.assertTrue(info["category"] and info["recovery"], code)
        self.assertEqual(DG.classify("storage_disk_full")["category"], "Disk")
        self.assertEqual(DG.classify("something_new"), DG.FALLBACK)

    def test_an_application_error_keeps_its_message_and_hint_and_a_stray_one_leaks_nothing(self):
        mine = DG.describe(NotFound("No such wall", hint="Pick another"))
        self.assertEqual((mine["code"], mine["category"], mine["message"], mine["recovery"]), ("not_found", "Missing", "No such wall", "Pick another"))
        stray = DG.describe(KeyError("/secret/path"))
        self.assertEqual(stray["code"], "error")
        self.assertNotIn("secret", json.dumps(stray))

    def test_the_log_is_json_lines_newest_first_rotated_and_never_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = DG.Diagnostics(Path(tmp) / "d.log")
            log.record("command:a", BadRequest("first"), project="P")
            log.record("command:b", ValueError("second"))
            entries = log.recent()
            self.assertEqual([e["source"] for e in entries], ["command:b", "command:a"])
            self.assertEqual((entries[1]["code"], entries[1]["project"]), ("bad_request", "P"))
            self.assertIn("ValueError", entries[0]["trace"])                       # only unexpected failures carry a trace
            self.assertNotIn("trace", entries[1])
            with mock.patch.object(DG, "MAX_BYTES", 10):
                log.record("command:c", BadRequest("third"))
            self.assertTrue((Path(tmp) / "d.log.1").is_file())
            self.assertEqual([e["source"] for e in log.recent()], ["command:c"])
            (Path(tmp) / "d.log").write_text("not json\n" + json.dumps({"source": "ok"}) + "\n")
            self.assertEqual([e["source"] for e in log.recent()], ["ok"])          # a damaged line is skipped
        DG.Diagnostics(None).record("x", BadRequest("y"))                           # no log file: nothing happens
        # /proc is not a protected filesystem path on Windows; use a file
        # as a parent directory to guarantee this write is impossible on every OS.
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / "regular-file"
            parent.write_text("file", encoding="utf-8")
            blocked = DG.Diagnostics(parent / "impossible.log")
            blocked.record("x", BadRequest("y"))
            self.assertEqual(blocked.recent(), [])


class ParsedFiles(unittest.TestCase):
    def setUp(self) -> None:
        IO._PARSED.clear()
        self.obj = b"o box\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"

    def test_a_file_is_parsed_once_and_each_caller_gets_its_own_copy(self):
        with mock.patch.object(IO, "_parse_model", wraps=IO._parse_model) as parse:
            a, _ = IO._read_model("obj", self.obj, name="x", unit="m")
            b, _ = IO._read_model("obj", self.obj, name="x", unit="m")
            self.assertEqual(parse.call_count, 1)
            ids = IO.E.all_ids(a)
            a.remove(ids[0])
            self.assertEqual(len(IO.E.all_ids(a)), len(ids) - 1)
            self.assertEqual(len(IO.E.all_ids(b)), len(ids))                         # the second copy is untouched by changes to the first
            IO._read_model("obj", self.obj, name="x", unit="mm")                     # another unit is another reading
            IO._read_model("obj", self.obj + b"# changed\n", name="x", unit="m")     # other bytes are other bytes
            self.assertEqual(parse.call_count, 3)

    def test_a_failed_parse_is_not_remembered_and_the_cache_is_bounded(self):
        for _ in range(2):
            with self.assertRaises(BadRequest):
                IO._read_model("obj", b"v 0 0 0\nf 1 2 3\n", name="x", unit="m")
        self.assertEqual(len(IO._PARSED), 0)
        for n in range(IO.PARSED_ENTRIES + 2):
            IO._read_model("obj", self.obj + b"# %d\n" % n, name="x", unit="m")
        self.assertEqual(len(IO._PARSED), IO.PARSED_ENTRIES)


class Jobs(ServerCase):
    def setUp(self) -> None:
        super().setUp()
        self.api.post("/api/project/new", {"name": "J", "unit": "mm"})
        self.level = self.api.get("/api/project/tree")["levels"][0]["id"]

    # -- helpers ------------------------------------------------------------------------------------------------------------
    def start(self, kind, params=None):
        return self.api.post("/api/jobs", {"kind": kind, "params": params or {}})

    def wait(self, job, until=("done", "failed", "cancelled")):
        end = time.time() + 30
        while time.time() < end:
            now = self.api.get(f"/api/jobs/{job['id']}")
            if now["status"] in until:
                return now
            time.sleep(0.02)
        self.fail(f"the job did not finish: {now}")

    def wall(self):
        return self.api.cmd("model.add_wall", {"start": [0, 0], "end": [4000, 0], "level": self.level})["result"]["id"]

    def revision(self):
        return self.app.project.revision

    def gated(self, commit=False):
        gate = threading.Event()

        def runner(app, job, p):
            job.step(0.2, "holding")
            if commit:
                JB._commit(app, job, "model.rebuild", {})
            while not gate.wait(0.01):
                job.step(0.4, "holding")
            if not commit:
                JB._commit(app, job, "model.rebuild", {})
            return {"released": True}
        patch = mock.patch.dict(JB.RUNNERS, {"hold": ("Hold", runner)})
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(gate.set)
        return gate

    # -- tests --------------------------------------------------------------------------------------------------------------
    def test_an_export_runs_in_the_background_and_its_file_is_the_one_a_direct_export_gives(self):
        self.wall()
        job = self.start("export.model", {"format": "obj"})
        self.assertIn(job["status"], ("queued", "running", "done"))
        done = self.wait(job)
        self.assertEqual((done["status"], done["progress"], done["hasFile"]), ("done", 1.0, True))
        self.assertEqual(done["result"]["report"]["status"], "partial")                # a mesh file cannot carry the model's parameters, and the report says so
        status, headers, data = self.api.raw("GET", f"/api/jobs/{job['id']}/file")
        self.assertEqual((status, headers["content-type"].split(";")[0]), (200, "text/plain"))
        self.assertIn("filename=", headers["content-disposition"])
        direct = self.api.raw("GET", "/api/export/model?format=obj")[2]
        self.assertEqual(data, direct)
        self.assertEqual(done["file"]["bytes"], len(data))

    def test_a_drawing_export_job(self):
        self.wall()
        plan = next(v["id"] for v in self.app.project.manifest["views"] if v["kind"] == "plan")
        done = self.wait(self.start("export.drawing", {"view": plan, "format": "svg"}))
        self.assertEqual(done["status"], "done")
        data = self.api.raw("GET", f"/api/jobs/{done['id']}/file")[2]
        self.assertEqual(data, self.api.raw("GET", f"/api/export/drawing/{plan}?format=svg")[2])

    def test_an_import_reads_off_the_lock_and_lands_as_one_undoable_revision(self):
        self.wall()
        sgeo = self.api.raw("GET", "/api/export/model?format=sgeo")[2]
        self.api.post("/api/project/new", {"name": "B", "unit": "mm", "discard": True})
        before = self.revision()
        done = self.wait(self.start("import.model", {"format": "sgeo", "data": b64(sgeo), "name": "a.sgeo"}))
        self.assertEqual(done["status"], "done", done)
        self.assertEqual((done["result"]["objects"], done["result"]["report"]["status"]), (1, "complete"))
        self.assertEqual(self.revision(), before + 1)
        self.assertEqual(len(self.app.project.model.query_ids()), 1)
        self.api.post("/api/undo", {})                                                 # one step takes the whole import back
        self.assertEqual(self.app.project.model.query_ids(), [])

    def test_a_bad_file_fails_the_job_with_a_category_and_leaves_the_project_alone(self):
        before = self.revision()
        done = self.wait(self.start("import.model", {"format": "obj", "data": b64(b"v 0 0 0\nf 1 2 3\n"), "name": "bad.obj", "unit": "m"}))
        self.assertEqual(done["status"], "failed")
        self.assertEqual((done["error"]["code"], done["error"]["category"]), ("bad_request", "Input"))
        self.assertTrue(done["error"]["recovery"])
        self.assertEqual(self.revision(), before)
        self.assertEqual([e["source"] for e in self.api.get("/api/diagnostics")["entries"]], ["job:import.model"])

    def test_a_file_that_is_not_what_it_says_is_refused_before_anything_is_applied(self):
        glb_named_obj = self.wait(self.start("import.model", {"format": "glb", "data": b64(b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"), "name": "x.glb"}))
        self.assertEqual(glb_named_obj["status"], "failed")
        self.assertIn("content is", glb_named_obj["error"]["message"])

    def test_check_report_and_rebuild_jobs(self):
        self.wall()
        checked = self.wait(self.start("check"))
        self.assertEqual(checked["status"], "done")
        self.assertIn("counts", checked["result"])
        before = self.revision()
        self.assertEqual(self.wait(self.start("rebuild"))["result"]["rebuilt"], 1)
        self.assertEqual(self.revision(), before + 1)
        made = self.wait(self.start("report", {"name": "From a job"}))
        self.assertEqual(made["status"], "done")
        self.assertIn("# From a job", self.api.get(f"/api/report/{made['result']['id']}")["markdown"])

    def test_unknown_kinds_and_bad_parameters_are_refused_at_once(self):
        self.assertEqual(self.api.call("POST", "/api/jobs", {"kind": "mine.bitcoin"})[0], 400)
        self.assertEqual(self.api.call("POST", "/api/jobs", {"kind": "check", "params": [1]})[0], 400)
        done = self.wait(self.start("export.model", {"format": "step"}))
        self.assertEqual((done["status"], done["error"]["code"]), ("failed", "bad_request"))
        self.assertEqual(self.api.call("GET", "/api/jobs/nope")[0], 404)

    def test_cancelling_a_running_job_stops_it_and_changes_nothing(self):
        self.gated()
        before = self.revision()
        job = self.start("hold")
        self.wait(job, until=("running",))
        self.assertTrue(self.api.get(f"/api/jobs/{job['id']}")["cancellable"])
        self.assertTrue(self.api.post(f"/api/jobs/{job['id']}/cancel")["id"])
        done = self.wait(job)
        self.assertEqual(done["status"], "cancelled")
        self.assertIn("nothing was changed", done["message"])
        self.assertEqual(self.revision(), before)
        self.assertEqual(self.api.call("POST", f"/api/jobs/{job['id']}/cancel", {})[0], 409)       # already over

    def test_once_a_job_is_applying_its_change_it_can_no_longer_be_cancelled(self):
        gate = self.gated(commit=True)
        job = self.start("hold")
        self.wait(job, until=("running",))
        end = time.time() + 10
        while not self.api.get(f"/api/jobs/{job['id']}")["cancellable"] is False and time.time() < end:
            time.sleep(0.02)
        status, out = self.api.call("POST", f"/api/jobs/{job['id']}/cancel", {})
        self.assertEqual(status, 409)
        self.assertIn("Undo", out["error"]["hint"])
        gate.set()
        self.assertEqual(self.wait(job)["status"], "done")

    def test_only_a_few_operations_run_at_once(self):
        self.gated()
        first, second = self.start("hold"), self.start("hold")
        status, out = self.api.call("POST", "/api/jobs", {"kind": "hold"})
        self.assertEqual(out["error"]["code"], "limit_exceeded")
        for job in (first, second):
            self.api.post(f"/api/jobs/{job['id']}/cancel")
            self.wait(job)
        self.assertTrue(self.start("check"))                                          # room again

    def test_a_job_does_not_apply_its_change_to_a_project_that_replaced_its_own(self):
        gate = self.gated()
        job = self.start("hold")
        self.wait(job, until=("running",))
        self.api.post("/api/project/new", {"name": "Other", "unit": "mm", "discard": True})
        before = self.revision()
        gate.set()
        done = self.wait(job)
        self.assertEqual(done["status"], "failed")
        self.assertIn("closed or replaced", done["error"]["message"])
        self.assertEqual(self.revision(), before)

    def test_the_interface_stays_usable_while_a_job_runs(self):
        self.gated()
        job = self.start("hold")
        self.wait(job, until=("running",))
        start = time.time()
        self.wall()                                                                    # a command is not blocked behind the job
        self.assertEqual(self.api.get("/api/jobs")["jobs"][0]["id"], job["id"])
        self.assertLess(time.time() - start, 5)

    def test_finished_jobs_can_be_dismissed_but_running_ones_cannot(self):
        self.gated()
        job = self.start("hold")
        self.wait(job, until=("running",))
        self.assertEqual(self.api.call("POST", f"/api/jobs/{job['id']}/dismiss", {})[0], 409)
        self.api.post(f"/api/jobs/{job['id']}/cancel")
        self.wait(job)
        self.assertTrue(self.api.post(f"/api/jobs/{job['id']}/dismiss")["dismissed"])
        self.assertEqual(self.api.call("GET", f"/api/jobs/{job['id']}")[0], 404)

    def test_a_file_is_not_offered_until_the_job_has_one(self):
        self.wall()
        self.gated()
        job = self.start("hold")
        status, out = self.api.call("GET", f"/api/jobs/{job['id']}/file")
        self.assertEqual((status, out["error"]["code"]), (404, "not_found"))

    def test_an_unexpected_failure_inside_a_job_ends_it_cleanly_and_is_logged_with_its_trace(self):
        def broken(app, job, p):
            raise RuntimeError("boom /private/path")
        with mock.patch.dict(JB.RUNNERS, {"broken": ("Broken", broken)}):
            done = self.wait(self.start("broken"))
        self.assertEqual((done["status"], done["error"]["category"]), ("failed", "Unexpected"))
        self.assertNotIn("private", json.dumps(done))                                  # the interface never sees the internals
        entry = self.api.get("/api/diagnostics")["entries"][0]
        self.assertIn("RuntimeError", entry["trace"])

    def test_failed_commands_are_logged_without_their_content(self):
        status, out = self.api.call("POST", "/api/command", {"op": "model.add_wall", "params": {"start": [0, 0], "end": [0, 0], "level": self.level, "note": "SECRET-TEXT"}})
        self.assertEqual(status, 400)
        entries = self.api.get("/api/diagnostics")["entries"]
        self.assertEqual((entries[0]["source"], entries[0]["category"], entries[0]["project"]), ("command:model.add_wall", "Input", "J"))
        self.assertNotIn("SECRET-TEXT", json.dumps(entries))
        self.api.call("POST", "/api/command", {"op": "model.add_wall", "params": {"start": [0, 0], "end": [0, 0], "level": self.level}})
        self.assertEqual(len(self.api.get("/api/diagnostics?limit=1")["entries"]), 1)                # the interface asks as text: ?limit=100
        self.assertEqual(len(self.api.get("/api/diagnostics?limit=2")["entries"]), 2)
        for bad in ("0", "501", "abc", "1.5", ""):
            self.assertEqual(self.api.call("GET", f"/api/diagnostics?limit={bad}")[0], 400, bad)

    def test_the_start_up_data_carries_the_error_table_and_the_job_kinds(self):
        boot = self.api.get("/api/bootstrap")
        self.assertEqual(boot["errors"]["not_found"]["category"], "Missing")
        for kind in ("import.model", "export.drawing", "report", "check", "rebuild", "simulate.beam", "optimize.beam"):
            self.assertIn(kind, boot["jobs"])


class Rebuild(Case):
    def test_rebuild_is_a_command_that_regenerates_every_element_and_is_undoable(self):
        wall = self.wall()
        self.run_("model.add_door", wall=wall, offset=2.0)
        before, volume = self.p.revision, self.vol(wall)
        self.assertEqual(self.run_("model.rebuild")["rebuilt"], 2)
        self.assertEqual((self.p.revision, self.vol(wall)), (before + 1, volume))
        self.p.undo()
        self.assertEqual(self.vol(wall), volume)


if __name__ == "__main__":
    unittest.main()
