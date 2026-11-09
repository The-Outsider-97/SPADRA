"""Scripts as replayable, permissioned computations (report 6.6, P1-02): every run record carries the hashes of its source, inputs, model snapshot and
environment; a script reads only what its version declared; a run bound to a revision refuses a model that has moved; and a replay either reproduces the
recorded result hash or says exactly why it could not be compared."""
from __future__ import annotations

import hashlib
import unittest

from applications.SPADRA import scripting as SC
from applications.tools.errors import BadRequest, NotFound

from .test_scripting import Base, IO, SANDBOX, WIDTHS

COUNT = """
def run(model, params):
    return {"n": len(model.elements()), "unit": model.unit, "frame": model.frame['coordinates']}
"""
COUNT_IO = dict(inputs={}, outputs={"n": "integer", "unit": "text", "frame": "text"})


class Records(Base):
    def approved(self, source=WIDTHS, name="Door check", io=IO, **extra):
        sid = self.x("script.add", name=name, source=source, **io, **extra)["id"]
        if not SANDBOX:
            self.x("script.approve", id=sid, version=1, sha256=SC.sha256(source))
        return sid

    def test_a_run_record_carries_source_input_snapshot_and_environment_hashes(self) -> None:
        sid = self.approved()
        rec = self.run_(sid, inputs={"width": 1.0}, seed=7)
        self.assertEqual(rec["status"], "ok", rec)
        self.assertEqual(rec["schema"], SC.RUN_SCHEMA)
        self.assertEqual(rec["sha256"], SC.sha256(WIDTHS))
        self.assertEqual(rec["inputs_sha256"], hashlib.sha256(SC.canonical(rec["inputs"]).encode()).hexdigest())
        self.assertEqual(rec["snapshot_sha256"], SC.snapshot_sha256(SC.snapshot(self.p)))
        self.assertEqual(rec["environment_sha256"], hashlib.sha256(SC.canonical(rec["environment"]).encode()).hexdigest())
        for key in ("interpreter", "python", "system", "runner_sha256", "api", "hash_seed", "locale", "clock", "random"):
            self.assertIn(key, rec["environment"])
        self.assertEqual((rec["seed"], rec["revision"], rec["reads"]), (7, self.p.revision, list(SC.READ_SCOPES)))
        self.assertIn("timeout_s", rec["limits"])

    def test_the_snapshot_hash_ignores_the_revision_counter_but_not_the_content(self) -> None:
        before_hash, before_revision = SC.snapshot_sha256(SC.snapshot(self.p)), self.p.revision
        fid = self.family()
        self.x("family.place", family=fid, level=self.level, point=[0, 0])
        self.assertNotEqual(SC.snapshot_sha256(SC.snapshot(self.p)), before_hash)               # content changed
        self.p.undo(), self.p.undo(), self.p.undo()
        self.assertGreater(self.p.revision, before_revision)                                    # the counter only moves forward...
        self.assertEqual(SC.snapshot_sha256(SC.snapshot(self.p)), before_hash)                  # ...and the content is what it was

    def test_replay_reproduces_the_result_hash_when_nothing_changed(self) -> None:
        sid = self.approved()
        rec = self.run_(sid, inputs={"width": 1.2, "frame": 0.05}, seed=3)
        out = SC.replay(self.p, {"run": rec["id"]})
        self.assertEqual((out["status"], out["reproduced"], out["environment_changed"]), ("reproduced", True, []), out)
        self.assertEqual(out["replay_result_sha256"], rec["result_sha256"])

    def test_replay_reproduces_after_the_revision_moved_but_the_content_did_not_and_refuses_when_it_did(self) -> None:
        sid = self.approved(COUNT, "Count", COUNT_IO)
        fid = self.family()
        rec = self.run_(sid)
        self.x("family.type_define", id=fid, name="D-1000", values={"width": 1000})            # the family list the script may read changes with it
        moved = SC.replay(self.p, {"run": rec["id"]})
        self.assertEqual((moved["status"], moved["reproduced"]), ("snapshot_changed", None), moved)
        self.assertNotIn("replay", moved)                                                       # nothing was run
        self.p.undo()                                                                           # same content, later revision
        again = SC.replay(self.p, {"run": rec["id"]})
        self.assertEqual(again["status"], "reproduced", again)                                  # same content again: a fair comparison, and the hash matches

    def test_a_replay_names_an_environment_that_changed_and_a_changed_result(self) -> None:
        sid = self.approved()
        rec = self.run_(sid, inputs={"width": 1.0})
        rec["environment"] = {**rec["environment"], "python": "0.0.0-recorded-elsewhere"}
        self.assertEqual(SC.replay(self.p, {"run": rec["id"]})["environment_changed"], ["python"])
        rec["result_sha256"] = "0" * 64                                                         # as if the earlier result had been different
        self.assertEqual(SC.replay(self.p, {"run": rec["id"]})["status"], "different")

    def test_only_a_finished_recorded_run_can_be_replayed(self) -> None:
        sid = self.approved()
        bad = self.run_(sid, inputs={"width": 99})
        self.assertEqual(bad["status"], "invalid_input")
        with self.assertRaises(BadRequest):
            SC.replay(self.p, {"run": bad["id"]})
        with self.assertRaises(NotFound):
            SC.replay(self.p, {"run": "run-nope"})


class Permissions(Base):
    def run_with(self, reads, source):
        self.count = getattr(self, "count", 0) + 1
        sid = self.x("script.add", name=f"Reader {self.count}", source=source, inputs={}, outputs={"n": "integer"}, **({"reads": reads} if reads is not None else {}))["id"]
        if not SANDBOX:
            self.x("script.approve", id=sid, version=1, sha256=SC.sha256(source))
        return self.run_(sid)

    def setUp(self) -> None:
        super().setUp()
        fid = self.family()
        self.x("family.place", family=fid, level=self.level, point=[0, 0])

    def test_a_script_that_declared_nothing_extra_reads_everything_as_before(self) -> None:
        rec = self.run_with(None, "def run(model, params):\n    return {'n': len(model.elements()) + len(model.levels()) + len(model.families())}\n")
        self.assertEqual(rec["status"], "ok", rec)
        self.assertEqual(rec["reads"], list(SC.READ_SCOPES))

    def test_reading_what_was_not_declared_is_refused_inside_the_run(self) -> None:
        for reads, call, scope in (([], "model.elements()", "elements"), (["elements"], "model.levels()", "levels"), (["levels", "elements"], "model.families()", "families"),
                                   (["levels"], "model.element('x')", "elements")):
            with self.subTest(reads=reads, call=call):
                rec = self.run_with(reads, f"def run(model, params):\n    return {{'n': len({call})}}\n")
                self.assertEqual(rec["status"], "error", rec)
                self.assertIn(f"read access to '{scope}'", rec["error"])

    def test_bounds_are_a_separate_permission_and_absent_without_it(self) -> None:
        src = "def run(model, params):\n    return {'n': sum(1 for e in model.elements() if 'bounds' in e)}\n"
        with_bounds = self.run_with(["elements", "bounds"], src)
        without = self.run_with(["elements"], src)
        self.assertEqual((with_bounds["status"], without["status"], without["outputs"]["n"]), ("ok", "ok", 0), (with_bounds, without))
        self.assertGreaterEqual(with_bounds["outputs"]["n"], 0)

    def test_the_snapshot_given_to_the_script_contains_only_what_was_granted(self) -> None:
        snap = SC.snapshot(self.p, ["levels"])
        self.assertEqual((snap["granted"], snap["elements"], snap["families"]), (["levels"], [], []))
        self.assertTrue(snap["levels"])
        self.assertEqual((snap["unit"], snap["frame"]["length"], snap["frame"]["angle"]), (self.p.manifest["unit"], "m", "rad"))

    def test_bad_permission_declarations_are_refused(self) -> None:
        for bad in ("elements", ["elements", "elements"], ["write"], [1], {"elements": True}):
            with self.subTest(reads=bad), self.assertRaises(BadRequest):
                self.x("script.add", name=f"Bad {bad}", source="def run(model, params):\n    return {'n': 1}\n", inputs={}, outputs={"n": "integer"}, reads=bad)

    def test_a_new_version_keeps_the_permissions_unless_it_changes_them(self) -> None:
        sid = self.x("script.add", name="Perm", source="def run(model, params):\n    return {'n': 1}\n", inputs={}, outputs={"n": "integer"}, reads=["levels"])["id"]
        self.x("script.update", id=sid, source="def run(model, params):\n    return {'n': 2}\n")
        self.assertEqual(SC.version_of(SC.script(self.p, sid))["reads"], ["levels"])
        self.x("script.update", id=sid, reads=["levels", "elements"])
        self.assertEqual(SC.version_of(SC.script(self.p, sid))["reads"], ["levels", "elements"])


class RevisionGuard(Base):
    def test_a_run_bound_to_a_revision_refuses_a_model_that_moved(self) -> None:
        source = "def run(model, params):\n    return {'n': model.revision}\n"
        sid = self.x("script.add", name="Rev", source=source, inputs={}, outputs={"n": "integer"})["id"]
        if not SANDBOX:
            self.x("script.approve", id=sid, version=1, sha256=SC.sha256(source))
        ok = self.run_(sid, expected_revision=self.p.revision)
        self.assertEqual((ok["status"], ok["outputs"]["n"]), ("ok", self.p.revision))
        stale_for = self.p.revision
        self.x("family.define", name="X", shape="box", parameters={"width": {"default": 500}, "depth": {"default": 500}, "height": {"default": 500}})
        stale = self.run_(sid, expected_revision=stale_for)
        self.assertEqual(stale["status"], "stale", stale)
        self.assertNotIn("outputs", stale)


if __name__ == "__main__":
    unittest.main()
