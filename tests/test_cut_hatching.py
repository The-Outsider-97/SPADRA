"""Cut elements are hatched by material in sections and plans. The patterns are SPADRA's configurable ones: none of them may claim to be NEN 47."""
from __future__ import annotations

import tempfile
from pathlib import Path

from applications.SPADRA import documentation as D
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest

from .test_documentation import Room


class CutHatching(Room):
    def sec(self):
        if not hasattr(self, "_sec"):
            self._sec = self.cmd("view.add_section", a=[3000, -2000], b=[3000, 6000])["id"]          # made once: a second command would be the one Undo takes back
        return D.project_view(self.p, self.p.view(self._sec), 1000.0)

    def item(self, pr, oid):
        return next(i for i in pr["items"] if i["id"] == oid)

    def view_id(self, kind="plan"):
        return next(v["id"] for v in self.p.manifest["views"] if v["kind"] == kind)

    def test_no_pattern_claims_to_be_a_standard(self):
        self.assertTrue(all(spec["kind"] in ("none", "solid", "lines", "cross", "dots") for spec in D.HATCH_PATTERNS.values()))
        self.assertIn("not NEN 47", D.HATCH_NOTE)
        for material in D.DEFAULT_HATCH:
            self.assertIsNone(D.hatch_pattern(self.p, material)["standard"])

    def test_a_cut_wall_is_hatched_by_its_material(self):
        pr = self.sec()
        wall = self.item(pr, self.walls[0])
        self.assertEqual(wall["cut_hatch"]["pattern"], "lines-45")             # the default for concrete
        self.assertEqual(wall["cut_hatch"]["kind"], "lines")
        self.assertGreater(wall["cut_hatch"]["spacing_mm"], 0)

    def test_a_pattern_is_assigned_to_a_material_for_the_whole_project_and_undone(self):
        self.sec()
        self.cmd("hatch.set", material="Concrete", pattern="cross-45")
        self.assertEqual(self.item(self.sec(), self.walls[0])["cut_hatch"]["pattern"], "cross-45")
        plan = {w["id"]: w for w in self.p.plan_overlay(self.view_id("plan")) if w["category"] == "wall"}
        self.assertEqual(plan[self.walls[0]]["hatch"]["pattern"], "cross-45")    # the plan agrees with the section
        self.p.undo()
        self.assertEqual(self.item(self.sec(), self.walls[0])["cut_hatch"]["pattern"], "lines-45")

    def test_going_back_to_the_default_forgets_the_assignment(self):
        self.cmd("hatch.set", material="Concrete", pattern="dots")
        self.cmd("hatch.set", material="Concrete", pattern=None)
        self.assertNotIn("Concrete", self.p.manifest.get("hatching", {}))

    def test_an_unknown_pattern_or_material_is_refused(self):
        with self.assertRaises(BadRequest):
            self.cmd("hatch.set", material="Concrete", pattern="nen47-concrete")
        with self.assertRaises(BadRequest):
            self.cmd("hatch.set", material="Unobtainium", pattern="dots")

    def test_a_view_can_turn_material_hatching_off(self):
        sec = self.cmd("view.add_section", a=[3000, -2000], b=[3000, 6000])["id"]
        self.cmd("view.set_option", id=sec, key="material_hatching", value="hidden")
        pr = D.project_view(self.p, self.p.view(sec), 1000.0)
        self.assertTrue(all("cut_hatch" not in i for i in pr["items"]))
        self.cmd("view.set_option", id=self.view_id("plan"), key="material_hatching", value="hidden")
        self.assertTrue(all("hatch" not in w for w in self.p.plan_overlay(self.view_id("plan")) if w["category"] == "wall"))
        self.assertTrue(any("cut_hatch" in i for i in self.sec()["items"]))      # other views keep theirs

    def test_glass_and_plasterboard_are_outlined_only_and_steel_is_solid(self):
        self.assertEqual(self.item(self.section((4500, -2000), (4500, 6000))[1], self.window)["cut_hatch"]["kind"], "none")
        self.assertEqual(D.hatch_pattern(self.p, "Steel")["kind"], "solid")
        self.assertEqual(D.hatch_pattern(self.p, "Plasterboard")["kind"], "none")

    def test_the_assignment_is_saved_with_the_project(self):
        self.cmd("hatch.set", material="Masonry", pattern="lines-90")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "p.spadra"
            self.p.save(target)
            self.assertEqual(D.hatch_pattern(Project.open(target), "Masonry")["pattern"], "lines-90")

    def test_the_properties_panel_lists_every_material_and_says_what_the_patterns_are(self):
        rows = {r.get("key") or r["label"]: r for g in self.p.properties({"view": self.view_id("elevation")})["groups"] for r in g["rows"]}
        self.assertEqual(rows["hatch:Concrete"]["value"], "lines-45")
        self.assertIn("material_hatching", rows)
        self.assertIn("not NEN 47", rows["Note"]["value"])
