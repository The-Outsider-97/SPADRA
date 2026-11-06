"""IFC 4.3 type products and private licensed-catalog assembly crosswalk regressions.

All OmniClass values here are clearly synthetic testing fixtures, not CSI codes.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .. import classifications as CL
from .. import documentation as D
from ..project import Project
from ...tools.errors import BadRequest
from .harness import ServerCase


def fixture(*, ambiguous: bool = False) -> dict:
    mappings = [
        {"assembly_code": "TEST-A", "omniclass_code": "FAKE-T21-01", "table": 21,
         "verified": True, "source": "synthetic regression evidence"}
    ]
    if ambiguous:
        mappings.append({"assembly_code": "TEST-A", "omniclass_code": "FAKE-T21-02",
                         "table": 21, "verified": True, "source": "synthetic ambiguity"})
    return {
        "schema": CL.SCHEMA, "edition": "SYNTHETIC-TEST-ONLY",
        "tables": {
            "21": [{"code": "FAKE-T21-01", "title": "Synthetic element A"},
                   {"code": "FAKE-T21-02", "title": "Synthetic element B"}],
            "23": [{"code": "FAKE-T23-01", "title": "Synthetic product"}]},
        "assembly_mappings": mappings,
    }


class IfcCatalog(unittest.TestCase):
    def test_type_product_catalog_is_schema_backed(self):
        result = CL.info()["ifc"]
        self.assertEqual((result["schema"], result["version"], result["types"]),
                         ("IFC4X3_ADD2", "4.3.2.0", 128))
        self.assertIn("IfcWallType", [r["code"] for r in CL.ifc_search("IfcWall")])
        self.assertIn("SHEAR", CL.ifc_lookup("IfcWallType")[1]["predefined_types"])
        self.assertIsNone(CL.ifc_lookup("IfcDefinitelyNotRealType"))

    def test_type_and_predefined_type_are_validated_and_undoable(self):
        p = Project.create("Ifc")
        wall = p.execute("model.add_wall", {"start": [0, 0], "end": [4000, 0],
                   "level": p.manifest["levels"][0]["id"], "thickness": 200, "height": 3})["result"]["id"]
        def set_field(key, value):
            p.execute("model.set_property", {"id": wall, "key": key, "value": value})
        set_field("ifc_type", "ifcwalltype")
        self.assertEqual(CL.read(p.model.get(wall)["metadata"], "ifc_type"), "IfcWallType")
        set_field("ifc_predefined_type", "shear")
        self.assertEqual(CL.read(p.model.get(wall)["metadata"], "ifc_predefined_type"), "SHEAR")
        with self.assertRaises(BadRequest):
            set_field("ifc_predefined_type", "FAKE")
        with self.assertRaises(BadRequest):
            set_field("ifc_type", "IfcNonexistentType")
        self.assertEqual(CL.read(p.model.get(wall)["metadata"], "ifc_type"), "IfcWallType")
        set_field("ifc_type", "IfcDoorType")
        self.assertEqual(CL.read(p.model.get(wall)["metadata"], "ifc_predefined_type"), "")
        p.undo()
        self.assertEqual(CL.read(p.model.get(wall)["metadata"], "ifc_predefined_type"), "SHEAR")


class OmniClassWithPrivateCatalog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.catalog_file = Path(self.tmp.name) / "private.json"
        self.write(fixture())
        env = patch.dict(os.environ, {"SPADRA_OMNICLASS_CATALOG": str(self.catalog_file)})
        env.start()
        self.addCleanup(env.stop)
        self.project = Project.create("Private classifications")
        self.wall = self.project.execute("model.add_wall", {
            "start": [0, 0], "end": [4000, 0],
            "level": self.project.manifest["levels"][0]["id"],
            "thickness": 200, "height": 3,
        })["result"]["id"]

    def write(self, document):
        self.catalog_file.write_text(json.dumps(document), encoding="utf-8")

    def set(self, field, value):
        return self.project.execute("model.set_property", {"id": self.wall, "key": field, "value": value})

    def get(self, field):
        return CL.read(self.project.model.get(self.wall)["metadata"], field)

    def test_available_catalog_search_and_element_properties(self):
        self.assertTrue(CL.info()["omniclass"]["available"])
        self.assertEqual(CL.info()["omniclass"]["tables"], {"21": 2, "23": 1})
        self.assertEqual(CL.search("assembly", "TEST-A")[0]["mapped"], ["FAKE-T21-01"])
        self.assertEqual(CL.search("omniclass", "Synthetic product", table=23)[0]["code"], "FAKE-T23-01")
        names = [g["title"] for g in self.project.properties({"model": self.wall})["groups"]]
        self.assertIn("Classification (NL-SfB)", names)
        self.assertIn("Classification (OmniClass)", names)
        self.assertIn("Classification (IFC 4.3)", names)

    def test_auto_fill_in_one_undo_step_with_provenance_and_schedule(self):
        initial = self.project.revision
        self.set("assembly_code", "TEST-A")
        self.assertEqual(self.project.revision, initial + 1)
        self.assertEqual(self.get("omniclass_21"), "FAKE-T21-01")
        self.assertEqual(self.get("omniclass_21_origin"), "auto")
        self.assertIn("synthetic regression", self.get("omniclass_21_source"))
        self.assertEqual(self.get("omniclass_23"), "")
        row = next(x for x in D.schedule_data(self.project, "wall")["rows"] if x["id"] == self.wall)
        self.assertEqual(row["omniclass_21"], "FAKE-T21-01")
        self.project.undo()
        self.assertEqual(self.get("assembly_code"), "")
        self.assertEqual(self.get("omniclass_21"), "")
        self.project.redo()
        self.assertEqual(self.get("omniclass_21"), "FAKE-T21-01")

    def test_missing_crosswalk_and_manual_override_never_guess(self):
        self.set("assembly_code", "UNKNOWN-TEST")
        self.assertEqual(self.get("omniclass_21"), "")
        self.set("omniclass_21", "FAKE-T21-02")
        self.set("assembly_code", "TEST-A")
        self.assertEqual(self.get("omniclass_21"), "FAKE-T21-02")
        self.assertEqual(self.get("omniclass_21_origin"), "manual")
        self.set("assembly_code", "")
        self.assertEqual(self.get("omniclass_21"), "FAKE-T21-02")

    def test_auto_mapping_removed_when_code_changes_or_clears(self):
        self.set("assembly_code", "TEST-A")
        self.set("assembly_code", "UNKNOWN-TEST")
        self.assertEqual(self.get("omniclass_21"), "")
        self.set("assembly_code", "TEST-A")
        self.assertEqual(self.get("omniclass_21"), "FAKE-T21-01")
        self.set("assembly_code", "")
        self.assertEqual(self.get("omniclass_21"), "")

    def test_ambiguous_mapping_is_never_autoselected(self):
        self.write(fixture(ambiguous=True))
        self.set("assembly_code", "TEST-A")
        self.assertEqual(self.get("omniclass_21"), "")
        self.assertIn("Ambiguous", str(CL.groups_for(self.project.model.get(self.wall)["metadata"], "wall")))

    def test_invalid_data_is_rejected(self):
        for bad in (
            {"tables": {"21": [], "23": []}},
            {**fixture(), "assembly_mappings": [{"assembly_code": "TEST-A",
                  "omniclass_code": "FAKE-T23-01", "table": 23, "verified": True, "source": "fake"}]},
        ):
            self.write(bad)
            with self.assertRaises(BadRequest):
                CL.info()
        self.write(fixture())
        with self.assertRaises(BadRequest):
            self.set("omniclass_21", "NONEXISTENT")
        with self.assertRaises(BadRequest):
            self.set("omniclass_23", "FAKE-T21-01")

    def test_native_project_save_round_trip(self):
        self.set("assembly_code", "TEST-A")
        self.set("ifc_type", "IfcWallType")
        self.set("ifc_predefined_type", "SHEAR")
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "classified.spadra"
            self.project.save_as(file)
            reopened = Project.open(file)
            meta = reopened.model.get(self.wall)["metadata"]
            self.assertEqual(CL.read(meta, "omniclass_21"), "FAKE-T21-01")
            self.assertEqual(CL.read(meta, "ifc_type"), "IfcWallType")
            self.assertEqual(CL.read(meta, "ifc_predefined_type"), "SHEAR")


class Http(ServerCase):
    def test_ifc_search_endpoint_uses_real_schema(self):
        data = self.api.get("/api/classifications")
        self.assertEqual(data["ifc"]["version"], "4.3.2.0")
        search = self.api.get("/api/classifications/search?kind=ifc&q=IfcWallType")
        self.assertEqual(search["results"][0]["code"], "IfcWallType")


if __name__ == "__main__":
    unittest.main()
