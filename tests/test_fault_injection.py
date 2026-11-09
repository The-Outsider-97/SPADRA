"""P0-03 (report Table 16): a fault injected at *each stage* of a model command leaves the committed model, every drawing, the manifest, the revision and
the undo/redo history exactly as they were, and the same command then succeeds when the fault is gone.

A command runs in stages: the handler itself, then the reconcilers that make dependent things follow the change (foundations, arrays, façades, voids,
attachments, rooms, tags, linked dimensions). Each one is failed in turn, *after* it has done its work (the partial-update case, the worst one), while the
scenario has something for every stage to change: a wall with a plan dimension on its face, a room, a tag and a family instance."""
from __future__ import annotations

import hashlib
import json
import unittest
from unittest import mock

from applications.SPADRA import families as FM
from applications.SPADRA import project as PJ

from .test_bim import Case

STAGES = [("foundations", "FD", "reconcile"), ("arrays", "AY", "reconcile"), ("facades", "PG", "reconcile"), ("voids", "FX", "reconcile"),
          ("attachments", "AT", "reconcile"), ("rooms", "AR", "reconcile"), ("tags", "AN", "sync_tags"), ("dimensions", "DL", "sync")]


def fingerprint(p) -> dict:
    """Everything a person could observe: SGEO model, each SDRW drawing, the manifest, the revision and the history depth."""
    def digest(obj) -> str:
        return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()
    return {"model": digest(p.model.as_dict()), "drawings": {k: digest(d.as_dict()) for k, d in sorted(p.drawings.items())},
            "manifest": digest(p.manifest), "revision": p.revision, "undo": len(p.undo_stack), "redo": len(p.redo_stack)}


class FaultAtEveryStage(Case):
    def setUp(self) -> None:
        super().setUp()
        self.south = self.wall(a=(0, 0), b=(6000, 0))
        self.north = self.wall(a=(0, 4000), b=(6000, 4000))
        self.west = self.wall(a=(0, 0), b=(0, 4000))
        self.east = self.wall(a=(6000, 0), b=(6000, 4000))
        self.plan = next(v["id"] for v in self.p.manifest["views"] if v.get("level_id") == self.l1)
        self.dim = self.run_("draw.dimension", drawing=self.plan, p1=[3000, 125], p2=[3000, 3875], dimline_point=[3500, 2000])["id"]
        self.fid = self.table()
        self.table_id = self.run_("family.place", family=self.fid, level=self.l1, point=[3000, 2000])["id"]

    def command(self):
        return self.p.execute("model.add_layer", {"id": self.south, "side": "left", "thickness": 50, "material": "Plasterboard"})

    def test_the_scenario_really_exercises_the_stages(self) -> None:
        """Without a fault the command changes the model, the plan dimension and the history, so a rollback has real work to undo."""
        before = fingerprint(self.p)
        self.command()
        after = fingerprint(self.p)
        self.assertNotEqual(before["model"], after["model"])
        self.assertNotEqual(before["drawings"][self.plan], after["drawings"][self.plan])
        self.assertEqual((after["revision"], after["undo"]), (before["revision"] + 1, before["undo"] + 1))

    def test_a_fault_in_any_stage_changes_nothing_and_a_retry_then_succeeds(self) -> None:
        for label, module, name in STAGES:
            with self.subTest(stage=label):
                before = fingerprint(self.p)
                target = getattr(PJ, module)
                real = getattr(target, name)

                def failing_after_its_work(project, _real=real, _label=label):
                    _real(project)
                    raise RuntimeError(f"injected fault after {_label}")

                with mock.patch.object(target, name, failing_after_its_work):
                    with self.assertRaisesRegex(RuntimeError, f"injected fault after {label}"):
                        self.command()
                self.assertEqual(fingerprint(self.p), before, f"a fault after the {label} stage left a partial update")
                self.p.undo_stack.clear()                                                    # a retry on a clean slate: the fault was the only difference
                ok = self.command()
                self.assertEqual(ok["revision"], before["revision"] + 1)
                self.p.undo()                                                                  # back to the starting model for the next stage
                self.p.redo_stack.clear()

    def test_a_fault_inside_the_handler_after_it_changed_things_changes_nothing(self) -> None:
        before = fingerprint(self.p)
        real = FM._placement

        def flaky(*args, **kwargs):
            out = real(*args, **kwargs)
            raise RuntimeError("injected fault in regeneration")

        with mock.patch.object(FM, "_placement", flaky):
            with self.assertRaisesRegex(RuntimeError, "injected fault in regeneration"):
                self.p.execute("family.update", {"id": self.fid, "parameters": {"height": {"default": 900}}})
        self.assertEqual(fingerprint(self.p), before)

    def test_a_fault_while_a_drawing_command_has_touched_the_model_changes_nothing(self) -> None:
        """A drawing edit that also changes the model (a dimension linked to a wall) is one transaction across documents."""
        before = fingerprint(self.p)
        real = PJ.DL.bind

        def failing(project):
            real(project)
            raise RuntimeError("injected fault while linking")

        with mock.patch.object(PJ.DL, "bind", failing):
            with self.assertRaises(RuntimeError):
                self.p.execute("draw.dimension", {"drawing": self.plan, "p1": [1000, 125], "p2": [1000, 3875], "dimline_point": [1500, 2000]})
        self.assertEqual(fingerprint(self.p), before)

    def test_a_refused_command_leaves_the_same_fingerprint(self) -> None:
        before = fingerprint(self.p)
        for op, params in (("model.add_layer", {"id": self.south, "side": "left", "thickness": -5, "material": "x"}),
                           ("model.add_layer", {"id": "no-such-wall", "side": "left", "thickness": 50, "material": "x"}),
                           ("family.define", {"name": "Bad", "shape": "box", "parameters": {"width": {"type": "nonsense", "default": 1}}})):
            with self.subTest(op=op, params=params), self.assertRaises(Exception):
                self.p.execute(op, params)
            self.assertEqual(fingerprint(self.p), before)


if __name__ == "__main__":
    unittest.main()
