"""Computation results become reviewable proposals: nothing changes until a person consents, a stale or rejected proposal changes nothing, and provenance is kept."""
from __future__ import annotations

import unittest

from applications.SPADRA import families as FM
from applications.SPADRA import scripting as SC
from applications.SPADRA.assistant import Assistant
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest, Conflict

SRC = "def run(model, params):\n    return {'width': params['w']}\n"


class Proposals(unittest.TestCase):
    def setUp(self):
        self.p = Project.create("Prop")
        self.a = Assistant()
        self.fid = self.p.execute("family.define", {"name": "Door", "shape": "box", "parameters": {"width": {"default": 900, "min": 600, "max": 1800, "scope": "type"},
                                                                                                    "height": {"default": 2100}, "depth": {"default": 40}}})["result"]["id"]
        self.p.execute("family.type_define", {"id": self.fid, "name": "D-900", "values": {"width": 900}})
        self.sid = self.p.execute("script.add", {"name": "w", "source": SRC, "inputs": {"w": {"type": "length", "default": 1.0}}, "outputs": {"width": "length"}})["result"]["id"]
        if not SC.isolation()["untrusted_execution"]:
            self.p.execute("script.approve", {"id": self.sid, "version": 1, "sha256": SC.sha256(SRC)})

    def width(self):
        return {t["name"]: t["values"]["width"] for t in FM.describe(self.p, FM.family(self.p, self.fid))["types"]}["D-900"]

    def proposal(self):
        rec = SC.run(self.p, {"script": self.sid})
        pl = SC.plan(self.p, {"run": rec["id"], "targets": [{"output": "width", "family": self.fid, "type": "D-900", "parameter": "width"}]})
        steps = pl["steps"] + [{"op": "script.record", "params": {"record": pl["record"]}}]
        return self.a.propose_commands(self.p, source="script:w v1", title="t", explanation="e", steps=steps, evidence_kind="script")["proposal"]

    def test_nothing_changes_until_consent_then_all_steps_apply_and_provenance_is_kept(self):
        rev = self.p.revision
        prop = self.proposal()
        self.assertEqual((self.p.revision, self.width()), (rev, 900))                  # a proposal is not a change
        self.assertEqual([c["label"] for c in prop["changes"]][:1], ["Width"] if prop["changes"] else [])
        with self.assertRaises(Conflict):                                              # a run without consent is refused
            self.a.run(self.p, prop["id"])
        self.a.approve(self.p, prop["id"], expected_revision=rev)
        self.assertEqual(self.width(), 1000)
        log = self.p.manifest["script_applications"][0]
        self.assertEqual((log["script"], log["sha256"]), (self.sid, SC.sha256(SRC)))
        for _ in range(prop["steps"].__len__()):
            self.p.undo()
        self.assertEqual(self.width(), 900)

    def test_a_stale_proposal_cannot_be_approved(self):
        prop = self.proposal()
        self.p.execute("family.type_define", {"id": self.fid, "name": "D-1000", "values": {"width": 1000}})      # the project moves on
        with self.assertRaises(Conflict):
            self.a.approve(self.p, prop["id"])
        self.assertEqual(self.width(), 900)

    def test_rejected_and_refused_proposals_change_nothing(self):
        prop = self.proposal()
        self.a.reject(self.p, prop["id"])
        with self.assertRaises(Conflict):
            self.a.approve(self.p, prop["id"])
        self.assertEqual(self.width(), 900)
        with self.assertRaises(BadRequest):                                             # a step the project would refuse never becomes a proposal
            self.a.propose_commands(self.p, source="x", title="t", explanation="e",
                                    steps=[{"op": "family.type_update", "params": {"id": self.fid, "name": "D-900", "values": {"width": 99999}}}])
        with self.assertRaises(BadRequest):
            self.a.propose_commands(self.p, source="x", title="t", explanation="e", steps=[])

    def test_a_forged_provenance_record_is_refused(self):
        rec = SC.run(self.p, {"script": self.sid})
        pl = SC.plan(self.p, {"run": rec["id"], "targets": [{"output": "width", "family": self.fid, "type": "D-900", "parameter": "width"}]})
        bad = dict(pl["record"], sha256="0" * 64)
        with self.assertRaises(Conflict):
            self.p.execute("script.record", {"record": bad})


class AlternativesProposal(unittest.TestCase):
    """Design alternatives become one proposal that adds each option as a named type: nothing until consent, then all of them, one undo each."""

    def setUp(self):
        from applications.SPADRA import optimization as OPZ
        self.OPZ = OPZ
        self.p = Project.create("Alt")
        self.a = Assistant()
        self.fid = self.p.execute("family.define", {"name": "Door", "shape": "box", "parameters": {
            "width": {"default": 900, "min": 600, "max": 1800, "scope": "type"}, "height": {"default": 2100}, "depth": {"default": 40}, "frame": {"default": 35, "min": 20, "max": 80},
            "clear_width": {"type": "length", "expression": "width - 2 * frame"}, "leaf_area": {"type": "area", "expression": "width * height"}}})["result"]["id"]

    def names(self):
        return {t["name"] for t in FM.describe(self.p, FM.family(self.p, self.fid))["types"]}

    def test_options_apply_only_after_consent_and_undo_one_by_one(self):
        out = self.OPZ.alternatives_family(self.p, {"family": self.fid, "variables": {"width": {"min": 700, "max": 1400}, "frame": {"min": 25, "max": 60}},
                                                    "objectives": [{"expression": "leaf_area", "sense": "min"}], "constants": {"m": {"type": "length", "value": 800}},
                                                    "constraints": ["clear_width >= m"], "count": 3, "candidates": 150, "seed": 2})
        steps = [{"op": x["command"]["command"], "params": x["command"]["params"]} for x in out["alternatives"] if x.get("command")]
        self.assertEqual(len(steps), 3)
        rev = self.p.revision
        prop = self.a.propose_commands(self.p, source="alternatives", title="t", explanation="e", steps=steps, evidence_kind="optimization")["proposal"]
        self.assertEqual((self.p.revision, self.names()), (rev, set()))
        with self.assertRaises(Conflict):
            self.a.run(self.p, prop["id"])
        self.a.approve(self.p, prop["id"], expected_revision=rev)
        self.assertEqual(self.names(), {x["type_name"] for x in out["alternatives"]})
        for _ in steps:
            self.p.undo()
        self.assertEqual(self.names(), set())


if __name__ == "__main__":
    unittest.main()
