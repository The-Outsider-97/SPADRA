"""BIM connection diagnostics and the agent integration around them (issue sheet 11-11-2026, section 8): analysis grounded in the model's own geometry,
corrections offered only as reviewable, revision-bound proposals, stale ones refused, results cached per revision, proximity candidates from the SLAI
Spatial Agent with a deterministic fallback, and the agent-call health report."""
from __future__ import annotations

import unittest
from unittest import mock

from applications.SPADRA import connections as CX
from applications.SPADRA import elements as E
from applications.SPADRA import gutters as GU
from applications.SPADRA.agent_hub import HUB
from applications.SPADRA.assistant import ASSISTANT
from applications.SPADRA.project import Project
from applications.tools.errors import Conflict

from .harness import ServerCase


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project.create("Connections")
        self.l0, self.l1, self.lr = (l["id"] for l in self.p.manifest["levels"])

    def cmd(self, op, **params):
        return self.p.execute(op, params)["result"]

    def building(self):
        self.cmd("model.add_floor", points=[[-1000, -1000], [9000, -1000], [9000, 7000], [-1000, 7000]], level=self.l0)
        run = self.cmd("model.add_curtainwall", points=[[0, 0], [6000, 0], [6000, 4000]], level=self.l0, height=3000, base_floor="none")["ids"]
        roof = self.cmd("model.add_roof", points=[[-500, -500], [7000, -500], [7000, 5000], [-500, 5000]], level=self.lr, roof="gable", pitch=30, thickness=200)["id"]
        gutter = self.cmd("model.add_gutters", roof=roof, edges=[0], downpipe=False)["gutters"][0]
        pipe = self.cmd("model.add_downpipe", gutter=gutter, offset=3.0)["id"]
        return run, roof, pipe

    def by_check(self, result, check):
        return [f for f in result["findings"] if f["check"] == check]


class Analysis(Case):
    def test_findings_rest_on_the_model_and_carry_their_revision_and_evidence(self):
        run, roof, pipe = self.building()
        r = CX.DIAGNOSER.diagnose(self.p)
        self.assertEqual(r["revision"], self.p.revision)
        self.assertEqual(r["checked"], {"rainwater": 1, "roof": 0, "curtainwall": 2})
        free = self.by_check(r, "downpipe.discharge")
        self.assertEqual([f["ref"] for f in free], [pipe])
        end = GU.assembly(self.p, pipe)["end"]
        self.assertEqual(free[0]["evidence"]["end"], [round(v, 4) for v in end])                       # the real end of the rebuilt assembly
        self.assertEqual({f["ref"] for f in self.by_check(r, "cw.base")}, set(run))
        self.assertEqual({f["ref"] for f in self.by_check(r, "cw.top")}, set(run))
        self.assertFalse([f for f in r["findings"] if f["severity"] == "error"])                      # the run's right-angle corner is valid

    def test_roof_edges_and_drip_edges_are_verified_on_the_cut_and_the_top_face(self):
        roof = self.cmd("model.add_roof", points=[[0, 0], [6000, 0], [6000, 4000], [0, 4000]], level=self.lr, roof="gable", pitch=40, thickness=200)["id"]
        self.cmd("model.set_roof_end", id=roof, edge=0, type="A1")
        self.cmd("model.add_roof_accessory", roof=roof, kind="roof_edge", edge="all")
        self.cmd("model.add_roof_accessory", roof=roof, kind="drip_edge", edge="1")
        r = CX.DIAGNOSER.diagnose(self.p, domains=["roof"])
        self.assertEqual(r["checked"]["roof"], 5)
        self.assertEqual(r["findings"], [], "every member is parallel to its cut and the drip edge lies on the top face")

    def test_a_panel_standing_out_of_its_frame_is_found_and_its_correction_dry_runs(self):
        cw = self.cmd("model.add_curtainwall", start=[0, 0], end=[6000, 0], level=self.l0, height=3000)["id"]
        self.cmd("model.set_property", id=cw, key="panel_offset", value=40)
        r = CX.DIAGNOSER.diagnose(self.p, [cw], ["curtainwall"])
        found = self.by_check(r, "cw.panel")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["fix"]["steps"], [{"op": "model.set_property", "params": {"id": cw, "key": "panel_offset", "value": 0}}])
        msg = CX.DIAGNOSER.propose_fix(self.p, self.p.revision, [found[0]["id"]], [cw], ["curtainwall"])
        self.assertEqual(msg["kind"], "proposal")
        self.assertEqual(msg["proposal"]["validation"]["dry_run"], "every step ran on a scratch copy without error")
        self.assertEqual(float(E.m(E.meta_of(self.p, cw), "panel_offset")), 0.04)                    # proposing changed nothing

    def test_results_are_cached_per_revision_and_recomputed_after_an_edit(self):
        self.building()
        first = CX.DIAGNOSER.diagnose(self.p)
        self.assertTrue(CX.DIAGNOSER.diagnose(self.p)["cached"])
        self.cmd("model.add_wall", start=[0, 6000], end=[3000, 6000], level=self.l0, height=3, thickness=200)
        again = CX.DIAGNOSER.diagnose(self.p)
        self.assertFalse(again["cached"])
        self.assertEqual(again["revision"], first["revision"] + 1)

    def test_without_the_spatial_agent_the_same_findings_come_from_a_scan(self):
        self.building()
        with_agent = CX.DIAGNOSER.diagnose(self.p)
        self.cmd("model.add_wall", start=[0, 6000], end=[3000, 6000], level=self.l0, height=3, thickness=200)          # a new revision: not from the cache
        with mock.patch.object(HUB, "available", return_value=False), mock.patch.object(HUB, "reason", return_value="not installed here"):
            plain = CX.DIAGNOSER.diagnose(self.p)
        self.assertEqual(plain["candidates"]["source"], "scan")
        self.assertEqual(plain["candidates"]["agent_calls"], 0)
        strip = lambda r: sorted((f["check"], f["ref"]) for f in r["findings"])                       # noqa: E731
        self.assertEqual(strip(plain), strip(with_agent))


class Proposals(Case):
    def test_a_correction_is_proposed_approved_applied_and_undone(self):
        run, roof, pipe = self.building()
        r = CX.DIAGNOSER.diagnose(self.p, [pipe], ["rainwater"])
        free = self.by_check(r, "downpipe.discharge")[0]
        rev = self.p.revision
        msg = CX.DIAGNOSER.propose_fix(self.p, rev, [free["id"]], [pipe], ["rainwater"])
        self.assertEqual(self.p.revision, rev)
        done = ASSISTANT.approve(self.p, msg["proposal"]["id"], expected_revision=rev)
        self.assertEqual(done["kind"], "success")
        inlets = [d for d in GU.drains(self.p) if E.m(E.meta_of(self.p, d), "downpipe") == pipe]
        self.assertEqual(len(inlets), 1)
        self.assertEqual(self.by_check(CX.DIAGNOSER.diagnose(self.p, [pipe], ["rainwater"]), "downpipe.discharge"), [])
        self.p.undo()
        self.assertEqual([d for d in GU.drains(self.p) if E.m(E.meta_of(self.p, d), "downpipe") == pipe], [])

    def test_a_stale_finding_is_refused(self):
        run, roof, pipe = self.building()
        r = CX.DIAGNOSER.diagnose(self.p)
        ids = [f["id"] for f in r["findings"] if f.get("fix")]
        self.cmd("model.set_property", id=run[0], key="height", value=3200)
        with self.assertRaises(Conflict):
            CX.DIAGNOSER.propose_fix(self.p, r["revision"], ids)

    def test_a_proposal_made_before_an_edit_cannot_be_applied_after_it(self):
        run, roof, pipe = self.building()
        r = CX.DIAGNOSER.diagnose(self.p, run, ["curtainwall"])
        msg = CX.DIAGNOSER.propose_fix(self.p, self.p.revision, [f["id"] for f in self.by_check(r, "cw.base")], run, ["curtainwall"])
        self.cmd("model.set_property", id=run[0], key="height", value=3200)
        with self.assertRaises(Conflict):
            ASSISTANT.approve(self.p, msg["proposal"]["id"], expected_revision=msg["proposal"]["revision"])

    def test_the_assistant_answers_and_proposes_through_its_request_forms(self):
        run, roof, pipe = self.building()
        ans = ASSISTANT.ask(self.p, "Check the drainage connections")
        self.assertEqual(ans["kind"], "analysis")
        self.assertEqual(ans["data"]["domains"], ["rainwater"])
        self.assertIn("discharges freely", ans["text"])
        prop = ASSISTANT.ask(self.p, "Fix the curtain wall connections", {"selection": run})
        self.assertEqual(prop["kind"], "proposal")
        self.assertTrue(all(s["op"] == "model.attach_wall" for s in prop["proposal"]["steps"]))
        ASSISTANT.approve(self.p, prop["proposal"]["id"], expected_revision=prop["proposal"]["revision"])
        left = CX.DIAGNOSER.diagnose(self.p, run, ["curtainwall"])
        self.assertEqual(self.by_check(left, "cw.base") + self.by_check(left, "cw.top"), [])


class Health(Case):
    def test_agent_calls_are_timed_and_summarised(self):
        self.building()
        CX.DIAGNOSER.diagnose(self.p)
        h = HUB.health()
        spatial = [r for r in h["operations"] if r["agent"] == "Spatial Agent"]
        self.assertTrue(spatial and all(r["timed"] == r["calls"] and r["mean_ms"] >= 0 for r in spatial))
        if HUB.available("observability"):
            self.assertTrue(h["observability"]["used"])
            self.assertTrue(any(k.startswith("Spatial Agent") for k in h["observability"]["subjects"]))
        else:
            self.assertFalse(h["observability"]["used"])


class OverHttp(ServerCase):
    def test_the_check_and_the_proposal_over_http(self):
        a = self.api
        a.post("/api/project/new", {"name": "Connections", "unit": "mm"})
        level = a.get("/api/project/tree")["levels"][0]["id"]
        cw = a.cmd("model.add_curtainwall", {"start": [0, 0], "end": [6000, 0], "level": level, "height": 3000})["result"]["id"]
        a.cmd("model.set_property", {"id": cw, "key": "panel_offset", "value": 40})
        r = a.post("/api/connections", {"ids": [cw], "domains": ["curtainwall"]})
        f = next(x for x in r["findings"] if x["check"] == "cw.panel")
        out = a.post("/api/connections/propose", {"findings": [f["id"]], "revision": r["revision"], "ids": [cw], "domains": ["curtainwall"]})
        self.assertEqual(out["message"]["kind"], "proposal")
        self.assertEqual(a.call("POST", "/api/connections/propose", {"findings": [f["id"]], "revision": r["revision"] - 1})[0], 409)
        self.assertIn("operations", a.get("/api/agents/health"))


if __name__ == "__main__":
    unittest.main()
