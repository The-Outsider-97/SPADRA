"""Project reports are real SLDC documents: written and validated by the SLDC library, readable by it, stored in the project folder and brought back."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from applications.SPADRA import reports as RP
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest, Conflict, NotFound
from src.utils import sldc as S

from .test_bim import Case


class Reports(Case):
    def setUp(self) -> None:
        super().setUp()
        self.w = self.wall()
        self.run_("model.set_property", id=self.w, key="material", value="Masonry")
        self.door = self.run_("model.add_door", wall=self.w, offset=2.0)["id"]
        self.run_("view.add_schedule", category="door")
        self.run_("view.add_schedule", category="wall")
        self.plan = next(v["id"] for v in self.p.manifest["views"] if v.get("level_id") == self.l1)
        self.sheet = self.run_("sheet.create", name="Plan")["id"]
        self.run_("sheet.place_view", id=self.sheet, view_id=self.plan)
        self.run_("sheet.issue", id=self.sheet, description="First issue", by="JR", date="2026-11-02")

    def make(self, **kw):
        return self.run_("report.create", **kw)["id"]

    def test_a_report_is_an_sldc_package_that_the_library_reads_back(self):
        rid = self.make(name="Status")
        data, entry = RP.read(self.p, rid)
        info = S.inspect_package(data)
        self.assertEqual((info["container"], info["version"], info["supported"]), ("package", "1.1.0", True))
        package = S.loads_package(data)
        self.assertEqual(package.document["title"], "Status")
        self.assertEqual(package.document["metadata"]["model_revision"], str(self.p.revision - 1))           # the revision it was made from, before the command itself
        self.assertEqual(entry["sha256"], __import__("hashlib").sha256(data).hexdigest())
        self.assertEqual(entry["blocks"], len(package.document["blocks"]))

    def test_it_holds_the_projects_real_data(self):
        md = RP.view(self.p, self.make())["markdown"]
        self.assertIn("| Project | BIM |", md)
        self.assertIn("Level 01", md)
        self.assertIn("Door Schedule", md)
        self.assertIn("| D01 |", md)                                              # the door's schedule number
        self.assertIn("WL01", md)
        self.assertIn("Masonry", md)                                               # material quantities
        self.assertIn("A101", md)
        self.assertIn("First issue", md)                                           # the revision history
        self.assertIn("current", md)                                               # the sheet has not changed since issue
        self.assertIn("What this check does not do", md)
        self.assertIn("not a code-compliance or engineering check", md)

    def test_a_changed_sheet_is_reported_as_changed_in_the_report(self):
        self.run_("model.set_property", id=self.door, key="width", value=1000)
        md = RP.view(self.p, self.make())["markdown"]
        self.assertIn("changed", md)
        self.assertIn("Level 01", md)

    def test_sections_can_be_chosen_and_are_checked(self):
        md = RP.view(self.p, self.make(sections=["levels"]))["markdown"]
        self.assertIn("Elevation (m)", md)
        self.assertNotIn("Door Schedule", md)
        for bad in (["nope"], [], "levels"):
            with self.assertRaises(BadRequest):
                self.run_("report.create", sections=bad, name="x")

    def test_names_are_unique_and_the_count_is_limited(self):
        self.make(name="One")
        with self.assertRaises(Conflict):
            self.make(name="one")
        self.p.manifest["reports"] += [{"id": f"r{i}", "name": f"R{i}", "revision": 1} for i in range(RP.MAX_REPORTS)]
        with self.assertRaises(Exception):
            self.make(name="Overflow")

    def test_create_and_delete_are_undoable_commands(self):
        rid = self.make()
        reports = next(s for s in self.p.tree()["sections"] if s["id"] == "reports")
        self.assertEqual([c["report"] for c in reports["children"]], [rid])
        self.run_("report.delete", id=rid)
        self.assertEqual(self.p.manifest["reports"], [])
        with self.assertRaises(NotFound):
            RP.read(self.p, rid)
        self.p.undo()
        self.assertEqual([r["id"] for r in self.p.manifest["reports"]], [rid])
        self.assertTrue(RP.view(self.p, rid)["markdown"])
        self.p.undo()
        self.assertEqual(self.p.manifest["reports"], [])

    def test_reports_are_saved_with_the_project_reopened_backed_up_and_recovered(self):
        rid = self.make(name="Status")
        data = self.p.reports[rid]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "x.spadra"
            saved = self.p.save(root)
            self.assertIn(f"reports/{rid}.sldc", saved["files"])
            self.assertEqual((root / "reports" / f"{rid}.sldc").read_bytes(), data)
            q = Project.open(root)
            self.assertEqual(q.reports[rid], data)
            self.assertEqual(q.warnings, [])
            q.execute("report.delete", {"id": rid})
            q.save()
            self.assertFalse((root / "reports" / f"{rid}.sldc").exists())                      # the file of a deleted report does not linger
            backups = Project.list_backups(root)
            self.assertEqual(len(backups), 1)
            self.assertTrue((root / ".backups" / backups[0]["id"] / "reports" / f"{rid}.sldc").is_file())
            recovered = Project.recover(root, backups[0]["id"])
            self.assertEqual(recovered.reports[rid], data)

    def test_a_report_file_changed_or_lost_outside_spadra_is_reported_not_trusted(self):
        rid = self.make()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "x.spadra"
            self.p.save(root)
            path = root / "reports" / f"{rid}.sldc"
            path.write_bytes(path.read_bytes() + b"x")
            self.assertTrue(any("changed outside" in w for w in Project.open(root).warnings))
            path.unlink()
            q = Project.open(root)
            self.assertEqual(q.manifest["reports"], [])
            self.assertTrue(any("missing" in w for w in q.warnings))

    def test_a_scratch_copy_carries_the_reports_without_touching_the_original(self):
        rid = self.make()
        scratch = self.p.scratch()
        scratch.execute("report.delete", {"id": rid})
        self.assertEqual(len(self.p.manifest["reports"]), 1)
        self.assertIn(rid, self.p.reports)


if __name__ == "__main__":
    unittest.main()
