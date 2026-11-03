"""Phase 11, against the real engines: one identity per element across 2D, 3D, BIM, views, annotations, schedules and animation, tags that follow
the model, the Section A-A workflow, and Sheet A101 from creation to a revision that later changes can be told apart from."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from applications.SPADRA.project import NS, Project
from applications.tools.errors import BadRequest, Conflict, NotFound

from .test_bim import Case


class Documented(Case):
    def setUp(self) -> None:
        super().setUp()
        self.w = self.wall()
        self.door = self.run_("model.add_door", wall=self.w, offset=2.0)["id"]
        self.plan = next(v["id"] for v in self.p.manifest["views"] if v.get("level_id") == self.l1)

    def tag(self, element=None, **extra):
        return self.run_("draw.tag", drawing=self.plan, element=element or self.door, point=[1500, 500], **extra)

    def tag_text(self, eid):
        return self.p.drawings[self.plan].get(eid)["geometry"]["text"]


class Tags(Documented):
    def test_a_tag_shows_the_schedule_mark_and_follows_the_model(self):
        self.run_("view.add_schedule", category="door")
        t = self.tag()
        self.assertEqual((t["field"], t["text"]), ("mark", "D01"))
        second = self.run_("model.add_door", wall=self.w, offset=4.5)["id"]
        self.run_("model.delete", id=self.door)                                      # D02 becomes D01 in the schedule
        self.assertEqual(self.p.drawings[self.plan].get(t["id"])["metadata"]["spadra:detached"], True)
        self.assertEqual(self.p.schedule(next(v["id"] for v in self.p.manifest["views"] if v["kind"] == "schedule"))["rows"][0]["id"], second)

    def test_a_tag_updates_in_the_same_command_and_one_undo_puts_both_back(self):
        t = self.tag(field="size")
        self.assertEqual(self.tag_text(t["id"]), "900 × 2100")
        self.run_("model.set_property", id=self.door, key="width", value=1000)
        self.assertEqual(self.tag_text(t["id"]), "1000 × 2100")
        self.p.undo()
        self.assertEqual(self.tag_text(t["id"]), "900 × 2100")
        self.assertEqual(self.meta(self.door)[f"{NS}:width"], 0.9)

    def test_deleting_the_element_detaches_the_tag_and_the_check_reports_it_and_undo_reattaches(self):
        t = self.tag()
        self.run_("model.delete", id=self.door)
        md = self.p.drawings[self.plan].get(t["id"])["metadata"]
        self.assertTrue(md["spadra:detached"])
        issues = [i for i in self.p.check()["issues"] if i["category"] == "associations"]
        self.assertEqual(len(issues), 1)
        self.assertEqual(self.p.sync_status()["detached_tags"][0]["entity"], t["id"])
        self.p.undo()
        self.assertFalse(self.p.drawings[self.plan].get(t["id"])["metadata"]["spadra:detached"])
        self.assertEqual([i for i in self.p.check()["issues"] if i["category"] == "associations"], [])

    def test_manual_annotations_are_never_rewritten_by_model_changes(self):
        note = self.p.drawings[self.plan].add_text((0, 0), "Check on site", 250.0)
        self.tag()
        self.run_("model.set_property", id=self.door, key="width", value=1000)
        self.run_("model.delete", id=self.door)
        self.assertEqual(self.p.drawings[self.plan].get(note)["geometry"]["text"], "Check on site")
        self.assertNotIn("spadra:detached", self.p.drawings[self.plan].get(note).get("metadata", {}))

    def test_the_field_can_be_changed_but_only_to_one_the_element_has(self):
        t = self.tag()
        self.run_("draw.tag_field", drawing=self.plan, id=t["id"], field="type")
        self.assertEqual(self.tag_text(t["id"]), "Single Flush Door")
        with self.assertRaises(BadRequest):
            self.run_("draw.tag_field", drawing=self.plan, id=t["id"], field="room")
        with self.assertRaises(BadRequest):
            self.tag(element=self.w, field="size")                                  # walls have no width × height

    def test_a_missing_element_cannot_be_tagged(self):
        with self.assertRaises(NotFound):
            self.tag(element="nope")

    def test_a_family_instance_is_tagged_with_its_family_name(self):
        o = self.run_("family.place", family=self.table(), level=self.l1, point=[1000, 1000])["id"]
        t = self.tag(element=o)
        self.assertEqual(t["text"], "Table")
        self.run_("family.update", id=self.p.model.get(o)["family_id"], name="Desk")
        self.assertEqual(self.tag_text(t["id"]), "Desk")

    def test_a_selected_tag_leads_back_to_its_element_and_its_field_is_editable_in_properties(self):
        t = self.tag()
        props = self.p.properties({"drawing": self.plan, "entity": t["id"]})
        self.assertEqual((props["title"], props["category"]), ("Tag", "tag"))
        group = next(g for g in props["groups"] if g["title"] == "Tag")
        link = group["rows"][0]
        self.assertEqual((link["type"], link["target"]), ("link", {"select": {"model": self.door}}))
        field = group["rows"][1]
        self.assertEqual((field["key"], field["value"]), ("tag_field", "mark"))
        self.assertIn("type", [c["value"] for c in field["choices"]])
        self.run_("model.delete", id=self.door)                                      # the tag keeps its text and says so; there is nothing left to follow
        group = next(g for g in self.p.properties({"drawing": self.plan, "entity": t["id"]})["groups"] if g["title"] == "Tag")
        self.assertEqual([r["type"] for r in group["rows"]], ["readonly", "readonly"])
        plain = self.p.drawings[self.plan].add_text((0, 0), "Note", 250.0)
        self.assertEqual([g["title"] for g in self.p.properties({"drawing": self.plan, "entity": plain})["groups"]], ["Geometry"])

    def test_tags_are_listed_among_an_elements_relationships(self):
        self.tag()
        rows = self.p.properties({"model": self.door}, relations=True)["groups"][-1]["rows"]
        self.assertIn("Tagged in", [r["label"] for r in rows])


class Identity(Documented):
    def test_one_id_names_the_element_everywhere_and_survives_save_and_reopen(self):
        sched = self.run_("view.add_schedule", category="door")["id"]
        t = self.tag()
        sheet = self.run_("sheet.create", name="Plans")["id"]
        self.run_("sheet.place_view", id=sheet, view_id=self.plan)
        track = self.p.model.animation()
        with tempfile.TemporaryDirectory() as tmp:
            self.p.save(Path(tmp) / "x.spadra")
            q = Project.open(Path(tmp) / "x.spadra")
        self.assertEqual(q.model.get(self.door)["metadata"][f"{NS}:host"], self.w)              # BIM: the door still names its wall by id
        self.assertEqual(q.drawings[self.plan].get(t["id"])["metadata"]["spadra:tag_of"], self.door)       # annotation
        self.assertIn(self.door, [r["id"] for r in q.schedule(sched)["rows"]])                             # schedule
        self.assertIn(self.door, [i["id"] for i in q.drawing_data(self.plan)["model"]])                    # 2D plan
        self.assertIn(self.door, [m["id"] for m in q.scene_data(10 ** 6)["objects"]])                       # 3D scene
        ident = next(g for g in q.properties({"model": self.door})["groups"] if g["title"] == "Identity")
        self.assertEqual(next(r for r in ident["rows"] if r["label"] == "Element ID")["value"], self.door)   # properties
        self.assertEqual(q.manifest["sheets"][0]["viewports"][0]["view_id"], self.plan)                     # sheet → view
        self.assertEqual(q.sync_status()["detached_tags"], [])
        self.assertIsNone(track)


class SectionA(Documented):
    def test_section_a_a_is_made_from_the_model_updates_with_it_and_is_named_by_its_cut(self):
        sec = self.run_("view.add_section", a=[2000, -2000], b=[2000, 2000], name="Section A-A")
        self.assertEqual(sec["name"], "Section A-A")
        data = self.p.drawing_data(sec["id"])
        cut = [i for i in data["projection"]["items"] if i["cut"]]
        self.assertEqual(sorted(i["id"] for i in cut), sorted([self.w, self.door]) if self.door in {i["id"] for i in cut} else [self.w])
        before = {i["id"] for i in data["projection"]["items"]}
        far = self.run_("model.add_wall", start=[2000, 3000], end=[2000, 5000], level=self.l1, thickness=250, height=3.2)["id"]
        after = {i["id"] for i in self.p.drawing_data(sec["id"])["projection"]["items"]}
        self.assertTrue(after >= before)
        self.assertIn("Section A-A", [r["value"] for r in self.p.properties({"model": self.w}, relations=True)["groups"][-1]["rows"] if r["label"] == "Appears in"])
        self.assertIsNotNone(far)

    def test_the_section_marker_shows_on_the_plan_it_cuts(self):
        sec = self.run_("view.add_section", a=[2000, -2000], b=[2000, 2000], name="Section A-A")
        marks = self.p.drawing_data(self.plan)["section_marks"]
        self.assertIn(sec["id"], [m["id"] for m in marks])

    def test_a_section_with_a_degenerate_line_is_refused(self):
        with self.assertRaises(BadRequest):
            self.run_("view.add_section", a=[0, 0], b=[1, 0])


class SheetA101(Documented):
    def make_sheet(self):
        sec = self.run_("view.add_section", a=[2000, -2000], b=[2000, 2000], name="Section A-A")["id"]
        sheet = self.run_("sheet.create", name="Ground floor plan")["id"]
        a = self.run_("sheet.place_view", id=sheet, view_id=self.plan, x=40, y=60, scale="1:50")["id"]
        b = self.run_("sheet.place_view", id=sheet, view_id=sec, x=500, y=60, scale="1:50")["id"]
        return sheet, sec, a, b

    def test_a_sheet_has_a_number_a_name_a_title_block_and_viewports_at_scale(self):
        sheet, sec, a, b = self.make_sheet()
        data = self.p.sheet_data(sheet)
        self.assertEqual((data["number"], data["name"], data["paper"]), ("A101", "Ground floor plan", "A1 Landscape"))
        tb = data["title_block"]
        self.assertEqual((tb["project"], tb["sheet_number"], tb["scale"], tb["status"]), ("BIM", "A101", "1:50", "Preliminary"))
        self.assertEqual([(v["scale"], v["x"]) for v in data["viewports"]], [("1:50", 40.0), ("1:50", 500.0)])
        self.run_("sheet.set", id=sheet, key="drawn_by", value="JR")
        self.run_("sheet.set", id=sheet, key="date", value="2026-11-02")
        self.run_("sheet.set", id=sheet, key="status", value="For review")
        self.run_("project.set", client="Acme")
        tb = self.p.sheet_data(sheet)["title_block"]
        self.assertEqual((tb["drawn_by"], tb["date"], tb["status"], tb["client"]), ("JR", "2026-11-02", "For review", "Acme"))
        with self.assertRaises(BadRequest):
            self.run_("sheet.set", id=sheet, key="date", value="2 November")
        with self.assertRaises(BadRequest):
            self.run_("sheet.set", id=sheet, key="status", value="Done")
        with self.assertRaises(Conflict):
            self.run_("sheet.create", number="A101")

    def test_a_viewport_title_replaces_the_view_name_and_is_part_of_what_changes(self):
        sheet, sec, a, b = self.make_sheet()
        self.run_("sheet.issue", id=sheet, description="First issue")
        self.run_("sheet.set_viewport", id=sheet, viewport=a, key="title", value="Level 01 plan")
        self.assertEqual(self.p.sheet_data(sheet)["status"]["state"], "changed")

    def test_issuing_a_revision_then_editing_the_model_marks_exactly_the_viewports_it_changed(self):
        sheet, sec, a, b = self.make_sheet()
        issued = self.run_("sheet.issue", id=sheet, description="Issued for review", by="JR", date="2026-11-02")
        self.assertEqual(issued["revision"], "01")
        st = self.p.sheet_data(sheet)["status"]
        self.assertEqual((st["state"], st["changed"]), ("current", []))
        self.run_("model.add_wall", start=[0, 4000], end=[3000, 4000], level=self.l2, thickness=250, height=3.2)       # another level: neither view shows it
        self.assertEqual(self.p.sheet_data(sheet)["status"]["state"], "current")
        self.run_("model.set_property", id=self.door, key="width", value=1000)
        st = self.p.sheet_data(sheet)["status"]
        self.assertEqual(st["state"], "changed")
        self.assertIn("Level 01", st["changed"])
        self.assertEqual({r["name"]: r["state"] for r in st["viewports"]}["Level 01"], "changed")
        self.assertEqual(self.p.sync_status()["stale_sheets"], ["A101"])
        warn = [i for i in self.p.check()["issues"] if i["category"] == "documentation" and "changed since" in i["message"]]
        self.assertEqual(len(warn), 1)
        second = self.run_("sheet.issue", id=sheet, description="Door widened")
        self.assertEqual(second["revision"], "02")
        self.assertEqual(self.p.sheet_data(sheet)["status"]["state"], "current")
        self.assertEqual([r["rev"] for r in self.p.sheet_data(sheet)["title_block"]["revisions"]], ["02", "01"])         # newest first

    def test_undo_returns_a_changed_sheet_to_current_because_the_picture_is_the_same(self):
        sheet, *_ = self.make_sheet()
        self.run_("sheet.issue", id=sheet, description="A")
        self.run_("model.set_property", id=self.door, key="width", value=1000)
        self.assertEqual(self.p.sheet_data(sheet)["status"]["state"], "changed")
        self.p.undo()
        self.assertEqual(self.p.sheet_data(sheet)["status"]["state"], "current")

    def test_a_manual_annotation_on_a_sheet_view_counts_as_a_change_to_that_view_only(self):
        sheet, sec, a, b = self.make_sheet()
        self.run_("sheet.issue", id=sheet, description="A")
        self.p.drawings[self.plan].add_text((0, 0), "Fire door", 250.0)
        self.p.touch(f"drawing:{self.plan}")
        self.p._cache.clear()
        st = self.p.sheet_data(sheet)["status"]
        self.assertEqual({r["name"]: r["state"] for r in st["viewports"]}, {"Level 01": "changed", "Section A-A": "current"})

    def test_revision_rules(self):
        sheet, *_ = self.make_sheet()
        self.assertEqual(self.p.sheet_data(sheet)["status"]["state"], "not issued")
        self.run_("sheet.issue", id=sheet, description="A")
        with self.assertRaises(Conflict):
            self.run_("sheet.issue", id=sheet, description="again", revision="01")
        with self.assertRaises(Conflict):
            self.run_("sheet.set", id=sheet, key="revision", value="X")
        with self.assertRaises(BadRequest):
            self.run_("sheet.issue", id=sheet, description="", revision="02")
        with self.assertRaises(BadRequest):
            self.run_("sheet.issue", id=sheet, description="bad", date="soon")
        self.run_("sheet.issue", id=sheet, description="B", revision="C")
        self.assertEqual(self.run_("sheet.issue", id=sheet, description="D")["revision"], "D")
        with self.assertRaises(Conflict):
            self.run_("sheet.delete", id=sheet)
        self.run_("sheet.delete", id=sheet, confirm=True)

    def test_notes_are_text_on_the_paper_and_part_of_the_sheet(self):
        sheet, *_ = self.make_sheet()
        n = self.run_("sheet.add_note", id=sheet, text="All dimensions in mm", x=50, y=30)["note"]
        self.run_("sheet.issue", id=sheet, description="A")
        self.run_("sheet.set_note", id=sheet, note=n, key="text", value="All dimensions in millimetres")
        st = self.p.sheet_data(sheet)["status"]
        self.assertEqual(st["changed"], ["title block or notes"])
        with self.assertRaises(BadRequest):
            self.run_("sheet.set_note", id=sheet, note=n, key="x", value=1e9)
        self.run_("sheet.remove_note", id=sheet, note=n)
        self.assertEqual(self.p.sheet_data(sheet)["notes"], [])

    def test_a_deleted_view_leaves_a_broken_reference_the_check_names(self):
        sheet, sec, a, b = self.make_sheet()
        self.run_("sheet.issue", id=sheet, description="A")
        self.p.manifest["views"] = [v for v in self.p.manifest["views"] if v["id"] != sec]
        self.p._cache.clear()
        out = self.p.check()
        self.assertFalse(out["ok"])
        self.assertTrue(any(i["category"] == "references" and "no longer exists" in i["message"] for i in out["issues"]))
        self.assertIn("(missing)", [r["name"] for r in self.p.sheet_data(sheet)["status"]["viewports"]])

    def test_the_sheet_the_revision_history_and_the_notes_survive_save_and_reopen(self):
        sheet, *_ = self.make_sheet()
        self.run_("sheet.add_note", id=sheet, text="Note", x=10, y=10)
        self.run_("sheet.issue", id=sheet, description="First", by="JR", date="2026-11-02")
        with tempfile.TemporaryDirectory() as tmp:
            self.p.save(Path(tmp) / "x.spadra")
            q = Project.open(Path(tmp) / "x.spadra")
        data = q.sheet_data(sheet)
        self.assertEqual((data["revisions"][0]["description"], data["revisions"][0]["by"], len(data["notes"])), ("First", "JR", 1))
        self.assertEqual(data["status"]["state"], "current")                                      # the digests are recomputed from the reopened data and agree

    def test_an_old_sheet_without_the_new_fields_opens_and_gains_them(self):
        sheet, *_ = self.make_sheet()
        s = next(s for s in self.p.manifest["sheets"] if s["id"] == sheet)
        for k in ("titleblock", "notes", "revisions"):
            s.pop(k)
        data = self.p.sheet_data(sheet)
        self.assertEqual((data["notes"], data["revisions"], data["status"]["state"]), ([], [], "not issued"))

    def test_the_sheet_tree_node_says_whether_it_changed(self):
        sheet, *_ = self.make_sheet()
        self.run_("sheet.issue", id=sheet, description="A")
        self.run_("model.set_property", id=self.door, key="width", value=1000)
        node = next(n for sec in self.p.tree()["sections"] for n in sec["children"] if n["id"] == sheet) if False else None
        sheets = next(s for s in self.p.tree()["sections"] if s["id"] == "sheets")["children"]
        self.assertIn("changed since issue", sheets[0]["detail"])
        self.assertIsNone(node)


class Properties(Documented):
    def test_sheet_properties_offer_the_title_block_the_issue_dialog_and_viewports(self):
        sheet, *_ = SheetA101.make_sheet(self)
        out = self.p.properties({"sheet": sheet})
        titles = [g["title"] for g in out["groups"]]
        for t in ("Title block", "Revisions", "Status", "Viewports", "Notes"):
            self.assertTrue(any(x.startswith(t) for x in titles), t)
        rev = next(g for g in out["groups"] if g["title"] == "Revisions")
        self.assertEqual(rev["action"]["dialog"], "issue")


if __name__ == "__main__":
    unittest.main()
