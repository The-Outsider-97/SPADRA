"""Parametric schedules: a schedule of one family whose columns are its typed parameters and whose rows are its placed instances, read live from SGEO's resolved values.
The tests change parameters, types, formulas and instances and check the schedule follows, that every value says where it came from, and that the other places that
show schedules (the project browser, the CSV, the SLDC project report) carry it."""
from __future__ import annotations

import unittest

from applications.SPADRA import documentation as D
from applications.SPADRA import family_schedule as FS
from applications.SPADRA import reports as RP
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest, Conflict, NotFound


class Base(unittest.TestCase):
    def setUp(self):
        self.p = Project.create("Family schedule")
        self.l0, self.l1 = (self.p.manifest["levels"][i]["id"] for i in (0, 1))
        self.fid = self.x("family.define", name="Panel", shape="box",
                          parameters={"width": {"default": 1000}, "height": {"default": 2000}, "depth": {"default": 100, "scope": "type"},
                                      "area": {"type": "area", "expression": "width * height"}, "wide": {"type": "boolean", "expression": "width > 1100 mm"}})["id"]
        self.x("family.type_define", id=self.fid, name="Deep", values={"depth": 150})
        self.a = self.place([1000, 1000], level=self.l0)
        self.b = self.place([3000, 1000], level=self.l0, type="Deep", parameters={"width": 1200})
        self.c = self.place([500, 500], level=self.l1, parameters={"height": 1500})
        self.vid = self.x("view.add_family_schedule", family=self.fid)["id"]

    def x(self, op, **kw):
        return self.p.execute(op, kw)["result"]

    def place(self, point, **kw):
        return self.x("family.place", family=self.fid, point=point, **kw)["id"]

    def schedule(self):
        return self.p.schedule(self.vid)

    def row(self, oid):
        return next(r for r in self.schedule()["rows"] if r["id"] == oid)


class Contents(Base):
    def test_columns_are_the_familys_parameters_with_their_units(self):
        cols = {c["key"]: c for c in self.schedule()["columns"]}
        self.assertEqual([c["key"] for c in self.schedule()["columns"]][:4], ["no", "name", "type", "level"])
        self.assertEqual((cols["p_width"]["unit"], cols["p_depth"]["unit"], cols["p_area"]["unit"]), ("mm", "mm", "m2"))
        self.assertIn("(formula)", cols["p_area"]["label"])
        self.assertTrue(cols["p_area"]["total"] and not cols["p_width"]["total"])             # areas add up, widths do not
        self.assertEqual(cols["p_wide"]["align"], "left")

    def test_values_are_what_sgeo_resolves(self):
        s = self.schedule()
        self.assertEqual(s["count"], 3)
        ra, rb, rc = self.row(self.a), self.row(self.b), self.row(self.c)
        self.assertEqual((ra["p_width"], ra["p_height"], ra["p_depth"], ra["p_area"], ra["p_wide"]), (1000.0, 2000.0, 100.0, 2.0, "No"))
        self.assertEqual((rb["p_width"], rb["p_depth"], rb["p_area"], rb["p_wide"]), (1200.0, 150.0, 2.4, "Yes"))
        self.assertEqual((rc["p_height"], rc["p_area"]), (1500.0, 1.5))
        self.assertAlmostEqual(s["totals"]["p_area"], 5.9, places=9)

    def test_each_value_says_where_it_came_from(self):
        self.assertEqual(self.row(self.a)["sources"], {"width": "default", "height": "default", "depth": "default", "area": "formula", "wide": "formula"})
        self.assertEqual(self.row(self.b)["sources"], {"width": "instance", "height": "default", "depth": "type", "area": "formula", "wide": "formula"})
        self.assertEqual(self.row(self.c)["sources"]["height"], "instance")

    def test_rows_are_ordered_by_level_then_position_and_numbered(self):
        rows = self.schedule()["rows"]
        self.assertEqual([r["id"] for r in rows], [self.a, self.b, self.c])
        self.assertEqual([r["no"] for r in rows], ["PA01", "PA02", "PA03"])
        self.assertEqual([r["level"] for r in rows][:1], [self.p.level(self.l0)["name"]])
        self.assertEqual((self.row(self.a)["type"], self.row(self.b)["type"]), ("-", "Deep"))


class Live(Base):
    def test_an_instance_edit_updates_the_row_and_the_derived_value(self):
        self.x("model.set_property", id=self.a, key="param:width", value=1500)
        r = self.row(self.a)
        self.assertEqual((r["p_width"], r["p_area"], r["p_wide"], r["sources"]["width"]), (1500.0, 3.0, "Yes", "instance"))

    def test_a_type_edit_updates_every_instance_of_the_type(self):
        self.x("family.type_update", id=self.fid, name="Deep", values={"depth": 220})
        self.assertEqual(self.row(self.b)["p_depth"], 220.0)
        self.assertEqual(self.row(self.a)["p_depth"], 100.0)

    def test_a_formula_edit_changes_the_derived_column(self):
        self.x("family.update", id=self.fid, parameters={"area": {"expression": "width * height * 2"}})
        self.assertAlmostEqual(self.row(self.a)["p_area"], 4.0)

    def test_a_new_parameter_becomes_a_column(self):
        self.x("family.update", id=self.fid, parameters={"fin": {"default": 40}})
        self.assertIn("p_fin", [c["key"] for c in self.schedule()["columns"]])
        self.assertEqual(self.row(self.a)["p_fin"], 40.0)

    def test_deleting_an_instance_removes_its_row_and_undo_brings_it_back(self):
        self.x("model.delete", id=self.b)
        self.assertEqual([r["id"] for r in self.schedule()["rows"]], [self.a, self.c])
        self.p.undo()
        self.assertEqual(self.schedule()["count"], 3)

    def test_placing_another_instance_adds_a_row(self):
        d = self.place([5000, 1000], level=self.l0)
        self.assertEqual(self.schedule()["count"], 4)
        self.assertEqual(self.row(d)["p_area"], 2.0)


class Elsewhere(Base):
    def test_the_view_is_a_schedule_in_the_project_browser(self):
        self.assertIn("Panel Schedule", repr(self.p.tree()))

    def test_the_csv_has_units_in_the_headers_and_the_total(self):
        csv = D.schedule_csv(self.schedule())
        lines = csv.strip().split("\r\n")
        self.assertIn("Width (mm)", lines[0])
        self.assertIn("Area (formula) (m2)", lines[0])
        self.assertEqual(len(lines), 1 + 3 + 1)
        self.assertTrue(lines[-1].startswith("Total"))
        self.assertIn("5.9", lines[-1])

    def test_the_project_report_carries_it(self):
        rid = self.x("report.create", name="R", sections=["schedules"])["id"]
        md = RP.view(self.p, rid)["markdown"]
        self.assertIn("Panel Schedule", md)
        self.assertIn("Area (formula) (m2)", md)

    def test_the_properties_of_the_view_count_the_rows(self):
        rows = [r for g in self.p.properties({"view": self.vid})["groups"] for r in g["rows"] if r["label"] in ("Lists", "Rows")]
        self.assertEqual({r["label"]: r["value"] for r in rows}, {"Lists": "instances of one family", "Rows": "3"})

    def test_a_schedule_view_survives_save_and_reopen(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "project.spadra"
            self.p.save(target)
            q = Project.open(target)
            s = q.schedule(self.vid)
            self.assertEqual((s["count"], s["category"], s["family"]), (3, "family", self.fid))
            self.assertAlmostEqual(s["totals"]["p_area"], 5.9, places=9)


class Refusals(Base):
    def test_one_schedule_per_family(self):
        with self.assertRaises(Conflict):
            self.x("view.add_family_schedule", family=self.fid)

    def test_unknown_family(self):
        with self.assertRaises((NotFound, BadRequest)):
            self.x("view.add_family_schedule", family="nope")

    def test_a_deleted_family_leaves_an_empty_schedule_that_says_so(self):
        self.x("model.delete", id=self.a)
        self.x("model.delete", id=self.b)
        self.x("model.delete", id=self.c)
        self.x("family.delete", id=self.fid)
        s = self.schedule()
        self.assertEqual((s["count"], s["rows"]), (0, []))
        self.assertIn("no longer exists", s["note"])
        self.assertEqual(self.p.properties({"view": self.vid})["groups"][-1]["rows"][-1]["value"], "0")           # the browser still opens it

    def test_the_ordinary_schedule_dialog_is_unchanged(self):
        self.assertNotIn("family", D.SCHEDULES)
        self.x("view.add_schedule", category="door")
        self.assertEqual(self.p.schedule([v for v in self.p.manifest["views"] if v.get("category") == "door"][0]["id"])["category"], "door")


if __name__ == "__main__":
    unittest.main()
