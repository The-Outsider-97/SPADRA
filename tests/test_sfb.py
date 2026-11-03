"""NL-SfB: the shipped tables, search, codes on elements (stored, validated, undoable, saved, scheduled, tagged) and the HTTP routes."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from .. import documentation as D
from .. import elements as E
from .. import sfb
from ..project import Project
from ...tools.errors import BadRequest
from .harness import ServerCase


class Tables(unittest.TestCase):
    def test_all_five_tables_are_there_with_the_rows_of_the_spreadsheet(self):
        counts = sfb.info()["counts"]
        self.assertEqual(counts, {"0": 554, "1": 1010, "2": 26, "3": 152, "4": 489})
        self.assertEqual(sfb.info()["version"], "2019-12-23")

    def test_a_code_is_found_with_or_without_brackets_and_in_any_case(self):
        self.assertEqual(sfb.lookup(1, "(21.1)")["en"], "External walls, non-load bearing")
        self.assertEqual(sfb.lookup(1, "21.1")["code"], "(21.1)")
        self.assertEqual(sfb.lookup(1, "(21.1) buitenwanden; niet constructief")["code"], "(21.1)")
        self.assertEqual(sfb.lookup(2, "f")["code"], "F")
        self.assertEqual(sfb.lookup(3, "E1")["code"], "e1")
        self.assertIsNone(sfb.lookup(1, "99.99"))
        self.assertIsNone(sfb.lookup(2, "(21.1)"))                  # a code belongs to its own table

    def test_search_finds_by_number_and_by_word_in_either_language(self):
        self.assertEqual(sfb.search("21.1", 1)[0]["code"], "(21.1)")
        self.assertTrue(all(r["code"].startswith("(21.1") for r in sfb.search("21.1", 1, 5)))
        self.assertIn("(22.2)", [r["code"] for r in sfb.search("binnenwanden constructief", 1)])
        self.assertIn("(22.2)", [r["code"] for r in sfb.search("internal walls load", 1)])
        self.assertEqual(sfb.search("creche", 0)[0]["code"], "446")           # accents do not matter
        self.assertEqual(len(sfb.search("", None, 7)), 7)
        with self.assertRaises(BadRequest):
            sfb.search("x", 9)

    def test_texts_the_spreadsheet_saved_through_the_dos_code_page_are_restored(self):
        everything = " ".join(r[2] + r[3] for r in json.loads(sfb.DATA.read_text(encoding="utf-8"))["rows"])
        self.assertNotIn("├", everything)
        self.assertIn("crèches", sfb.lookup(0, "446")["nl"])

    def test_every_suggested_code_exists(self):
        for category, codes in sfb.SUGGESTED.items():
            for code in codes:
                self.assertIsNotNone(sfb.lookup(1, code), (category, code))
            self.assertEqual([s["code"] for s in sfb.suggestions(category)], list(codes))
        self.assertEqual(sfb.suggestions("grid"), [])


class OnElements(unittest.TestCase):
    def setUp(self):
        self.p = Project.create("Codes")
        self.level = self.p.manifest["levels"][0]["id"]
        self.wall = self.p.execute("model.add_wall", {"start": [0, 0], "end": [4000, 0], "level": self.level, "thickness": 200, "height": 3})["result"]["id"]
        self.room = self.p.execute("model.add_room", {"points": [[0, 0], [3000, 0], [3000, 3000], [0, 3000]], "level": self.level, "name": "Hall"})["result"]["id"]

    def set(self, oid, key, value):
        return self.p.execute("model.set_property", {"id": oid, "key": key, "value": value})

    def code(self, oid, key="sfb"):
        return sfb.read(self.p.model.get(oid)["metadata"], key)

    def test_nothing_is_assigned_on_its_own(self):
        self.assertEqual(self.code(self.wall), "")
        self.assertEqual(self.code(self.room), "")

    def test_a_code_is_stored_as_the_table_writes_it_and_is_one_undo_step(self):
        before = self.p.revision
        self.set(self.wall, "sfb", "21.1")
        self.assertEqual(self.code(self.wall), "(21.1)")
        self.assertEqual(self.p.revision, before + 1)
        self.p.undo()
        self.assertEqual(self.code(self.wall), "")
        self.p.redo()
        self.assertEqual(self.code(self.wall), "(21.1)")

    def test_a_code_that_is_not_in_the_table_is_refused_and_nothing_changes(self):
        self.set(self.wall, "sfb", "(21.1)")
        revision = self.p.revision
        for bad in ("(99.9)", "F", "zzz", 5):
            with self.assertRaises(BadRequest):
                self.set(self.wall, "sfb", bad)
        self.assertEqual((self.code(self.wall), self.p.revision), ("(21.1)", revision))

    def test_method_and_material_use_their_own_tables(self):
        self.set(self.wall, "sfb_method", "f")
        self.set(self.wall, "sfb_material", "e1")
        self.assertEqual((self.code(self.wall, "sfb_method"), self.code(self.wall, "sfb_material")), ("F", "e1"))
        with self.assertRaises(BadRequest):
            self.set(self.wall, "sfb_method", "e1")                           # a material code is not a method

    def test_a_room_is_classified_in_table_0_and_a_wall_in_table_1(self):
        self.set(self.room, "sfb", "446")
        self.assertEqual(self.code(self.room), "446")
        with self.assertRaises(BadRequest):
            self.set(self.room, "sfb", "(21.1)")
        with self.assertRaises(BadRequest):
            self.set(self.wall, "sfb", "446")

    def test_an_empty_value_clears_the_code(self):
        self.set(self.wall, "sfb", "(22.1)")
        self.set(self.wall, "sfb", "")
        self.assertEqual(self.code(self.wall), "")

    def test_the_properties_panel_shows_the_code_its_name_and_the_table_it_searches(self):
        self.set(self.wall, "sfb", "(22.2)")
        group = next(g for g in self.p.properties({"model": self.wall})["groups"] if g["title"] == "Classification (NL-SfB)")
        row = group["rows"][0]
        self.assertEqual((row["key"], row["type"], row["table"], row["value"], row["text"]), ("sfb", "sfb", 1, "(22.2)", "binnenwanden; constructief"))
        self.assertEqual([s["code"] for s in row["suggest"]], ["(21.1)", "(21.2)", "(22.1)", "(22.2)"])
        self.assertEqual([r["table"] for r in group["rows"]], [1, 2, 3])
        room = next(g for g in self.p.properties({"model": self.room})["groups"] if g["title"] == "Classification (NL-SfB)")
        self.assertEqual(room["rows"][0]["table"], 0)

    def test_the_code_is_saved_with_the_project_and_found_again(self):
        self.set(self.wall, "sfb", "(21.2)")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "codes.spadra"
            self.p.save_as(path)
            again = Project.open(path)
            self.assertEqual(sfb.read(again.model.get(self.wall)["metadata"], "sfb"), "(21.2)")

    def test_schedules_carry_the_code_and_a_missing_one_reads_a_dash(self):
        other = self.p.execute("model.add_wall", {"start": [0, 2000], "end": [4000, 2000], "level": self.level, "thickness": 200, "height": 3})["result"]["id"]
        self.set(self.wall, "sfb", "(21.2)")
        data = D.schedule_data(self.p, "wall")
        self.assertEqual(data["columns"][-1]["key"], "sfb")
        self.assertEqual({r["id"]: r["sfb"] for r in data["rows"]}, {self.wall: "(21.2)", other: "-"})
        self.assertIn("NL-SfB", D.schedule_csv(data).splitlines()[0])

    def test_a_tag_can_show_the_code(self):
        self.set(self.wall, "sfb", "(21.2)")
        from .. import annotations as A
        self.assertEqual(A.tag_text(self.p, self.wall, "sfb"), "(21.2)")
        self.assertIn("sfb", A.fields_for("wall"))
        self.assertEqual(A.tag_text(self.p, self.room, "sfb"), "-")


class Http(ServerCase):
    def test_info_and_search_routes(self):
        self.assertEqual(self.api.get("/api/sfb")["counts"]["1"], 1010)
        hits = self.api.get("/api/sfb/search?table=1&q=buitenwanden%20constructief&limit=5")["results"]
        self.assertTrue(0 < len(hits) <= 5)
        self.assertTrue(all(h["table"] == 1 for h in hits))
        self.assertEqual(self.api.get("/api/sfb/search?q=21.1&table=1")["results"][0]["code"], "(21.1)")

    def test_a_bad_table_is_a_400(self):
        import urllib.error
        with self.assertRaises(Exception) as ctx:
            self.api.get("/api/sfb/search?q=x&table=9")
        self.assertIn("table", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
