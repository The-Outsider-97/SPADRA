"""One project carried through every parametric feature, then saved, reopened and undone.

These scenarios are named for what they do. The original assignment's workflow list (A to H) could not be recovered verbatim when this was written, so
this is not claimed to be that list; the door-family scenario is the only one matched to it by name in earlier notes.

The point is composition: features that pass alone must still work when they share a project, their commands share one undo stack, and reconcile hooks of
one feature must not disturb another."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from applications.SPADRA import animation_bridge as AB
from applications.SPADRA import families as FM
from applications.SPADRA import optimization as OPZ
from applications.SPADRA import procedural as PG
from applications.SPADRA import scripting as SC
from applications.SPADRA.assistant import Assistant
from applications.SPADRA.project import Project
from applications.tools.errors import Conflict

SCRIPT = "def run(model, params):\n    return {'width': params['w']}\n"


class OneProject(unittest.TestCase):
    def setUp(self):
        self.p = Project.create("All")
        self.l0 = self.p.manifest["levels"][0]["id"]
        self.view = next(iter(self.p.drawings))
        self.a = Assistant()

    def x(self, op, **kw):
        return self.p.execute(op, kw)["result"]

    def ids(self, p=None):
        return set((p or self.p).model.query_ids(limit=10000))

    def types(self, p, fid):
        return {t["name"]: t["values"] for t in FM.describe(p, FM.family(p, fid))["types"]}

    def approve(self, steps, source):
        prop = self.a.propose_commands(self.p, source=source, title=source, explanation=source, steps=steps)["proposal"]
        self.a.approve(self.p, prop["id"], expected_revision=self.p.revision)

    def test_every_feature_composes_and_survives_save_reopen_and_full_undo(self):
        wall = self.x("model.add_wall", start=[0, 0], end=[6000, 0], level=self.l0, thickness=200, height=3)["id"]
        door = self.x("model.add_door", wall=wall, offset=2.0, width=900)["id"]
        base_ids, mark = self.ids(), len(self.p.undo_stack)

        # 1. a typed family with a formula, named types, and a placed instance that follows its type
        fid = self.x("family.define", name="Door", shape="box", parameters={
            "width": {"default": 900, "min": 600, "max": 1800, "scope": "type"}, "height": {"default": 2100, "min": 1800, "max": 2600, "scope": "type"},
            "depth": {"default": 40}, "frame": {"default": 35, "min": 20, "max": 80},
            "clear_width": {"type": "length", "expression": "width - 2 * frame"}, "leaf_area": {"type": "area", "expression": "width * height"}})["id"]
        self.x("family.type_define", id=fid, name="D-900", values={"width": 900})
        inst = self.x("family.place", family=fid, level=self.l0, point=[0, -2000], type="D-900")["id"]
        self.x("family.type_update", id=fid, name="D-900", values={"width": 950})
        self.assertAlmostEqual(self.p.model.get(inst)["geometry"]["size"][0], 0.95, places=6)           # the instance followed its type

        # 2. a facade around the door's opening
        grp = self.x("procedural.facade", wall=wall, panel_width=1000, panel_height=1000)
        self.assertTrue(grp["members"])

        # 3. a designed stair
        stair = self.x("model.design_stair", level=self.l0, start=[0, -6000], end=[9000, -6000], width=1000)
        self.assertTrue(stair["flights"])

        # 4. drawing constraints: analyse, solve
        a = self.x("draw.line", drawing=self.view, start=[0, 0], end=[4000, 300])["id"]
        b = self.x("draw.line", drawing=self.view, start=[0, 1000], end=[4000, 1500])["id"]
        self.x("draw.constraints_set", drawing=self.view, constraints=[{"kind": "horizontal", "entities": [a]}, {"kind": "parallel", "entities": [a, b]}])
        self.assertTrue(self.x("draw.constraints_solve", drawing=self.view)["converged"])

        # 5. a script's result becomes a proposal, applied only on consent, with provenance
        sid = self.x("script.add", name="w", source=SCRIPT, inputs={"w": {"type": "length", "default": 1.0}}, outputs={"width": "length"})["id"]
        if not SC.isolation()["untrusted_execution"]:
            self.x("script.approve", id=sid, version=1, sha256=SC.sha256(SCRIPT))
        run = SC.run(self.p, {"script": sid})
        self.assertEqual(run["status"], "ok")
        plan = SC.plan(self.p, {"run": run["id"], "targets": [{"output": "width", "family": fid, "type": "D-900", "parameter": "width"}]})
        before = self.types(self.p, fid)["D-900"]["width"]
        prop = self.a.propose_commands(self.p, source="script", title="t", explanation="e", steps=plan["steps"] + [{"op": "script.record", "params": {"record": plan["record"]}}])["proposal"]
        self.assertEqual(self.types(self.p, fid)["D-900"]["width"], before)                              # a proposal changes nothing
        self.a.approve(self.p, prop["id"], expected_revision=self.p.revision)
        self.assertEqual(self.types(self.p, fid)["D-900"]["width"], 1000)
        self.assertEqual(len(self.p.manifest["script_applications"]), 1)

        # 6. optimization, then alternatives, each as a proposal
        problem = {"family": fid, "type": "D-900", "variables": {"width": {"min": 700, "max": 1400}, "frame": {"min": 25, "max": 60}},
                   "objectives": [{"expression": "leaf_area", "sense": "min"}], "constants": {"m": {"type": "length", "value": 800}}, "constraints": ["clear_width >= m"], "seed": 3}
        opt = OPZ.optimize_family(self.p, {**problem, "budget": 300})
        self.assertTrue(opt["proposal"]["feasible"])
        self.approve([{"op": c["command"], "params": c["params"]} for c in opt["proposal"]["commands"]], "optimization")
        alts = OPZ.alternatives_family(self.p, {**problem, "count": 3, "candidates": 120})
        self.assertEqual(len(alts["alternatives"]), 3)
        self.approve([{"op": o["command"]["command"], "params": o["command"]["params"]} for o in alts["alternatives"]], "alternatives")
        self.assertEqual(len(self.types(self.p, fid)), 4)                                                # D-900 and three options

        # 7. the door opens on its hinge (needs the SLAI Animation Agent)
        if AB.BRIDGE.available:
            self.x("anim.settings", duration=2)
            with self.assertRaises(Conflict):                                                         # the facade's panels stand hard against the jamb: refused at 5 mm ...
                self.x("anim.swing_door", id=door, degrees=90, start=0, end=2)
            swung = self.x("anim.swing_door", id=door, degrees=90, start=0, end=2, tolerance_mm=12)   # ... and accepted when the few mm of contact are explicitly tolerated
            self.assertTrue(swung["checked"] and swung["contacts_mm"])

        # the features did not disturb one another
        self.assertTrue(PG.describe(self.p))
        self.assertTrue(self.p.drawing(self.view).analyze_constraints()["status"] in ("under_constrained", "well_constrained"))

        # save and reopen: everything that belongs to the project is still there
        grown = self.ids()
        with tempfile.TemporaryDirectory() as t:
            self.p.save(Path(t) / "all.spadra")
            q = Project.open(Path(t) / "all.spadra")
            self.assertEqual(self.ids(q), grown)
            self.assertEqual(self.types(q, fid), self.types(self.p, fid))
            self.assertEqual([s["name"] for s in q.manifest["scripts"]], ["w"])
            self.assertEqual(len(q.manifest["script_applications"]), 1)
            self.assertEqual(len(PG.describe(q)), len(PG.describe(self.p)))
            self.assertEqual(q.drawing(self.view).analyze_constraints()["status"], self.p.drawing(self.view).analyze_constraints()["status"])
            if AB.BRIDGE.available:
                self.assertIsNotNone(q.animation_data()["animation"])

        # one undo stack: stepping all the way back removes every trace of every feature
        while len(self.p.undo_stack) > mark:
            self.p.undo()
        self.assertEqual(self.ids(), base_ids)
        self.assertEqual(self.p.model.families(), [] if isinstance(self.p.model.families(), list) else self.p.model.families().__class__())
        self.assertEqual(self.p.manifest.get("scripts") or [], [])
        self.assertEqual(self.p.manifest.get("script_applications") or [], [])
        self.assertEqual(self.p.manifest.get("procedural") or [], [])


if __name__ == "__main__":
    unittest.main()
