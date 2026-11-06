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



class AuthorizedCsvImport(unittest.TestCase):
    def test_imports_both_tables_and_uni_format_without_committing_licensed_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            t21 = p / "table21.csv"
            t23 = p / "table23.csv"
            ass = p / "assemblies.csv"
            t13 = p / "table13.csv"
            mapping = p / "crosswalk.csv"
            target = p / "private-catalog.json"
            t21.write_text("OmniClass Number,Level 1 Title,Level 2 Title\n21-FAKE-01,Synthetic Element,\n21-FAKE-02,Synthetic Element,Synthetic Part\n", encoding="utf-8")
            t23.write_text("Code,Title\n23-FAKE-01,Synthetic Product\n", encoding="utf-8")
            t13.write_text("Code,Title\n13-FAKE-01,Synthetic Space\n", encoding="utf-8")
            ass.write_text("Assembly Code,Assembly Description\nTEST-A,Test Shell System\nTEST-B,Unmapped System\n", encoding="utf-8")
            mapping.write_text("assembly_code,omniclass_code,table,verified,source\nTEST-A,21-FAKE-01,21,true,Synthetic crosswalk\n", encoding="utf-8")
            counts = CL.import_authorized_catalog(table21=t21, table23=t23,
                edition="SYNTHETIC", output=target, assembly=ass, mapping=mapping,
                other_tables={13: t13})
            self.assertEqual(counts, {"table21": 2, "table23": 1,
                                      "assembly_codes": 2, "verified_mappings": 1})
            with patch.dict(os.environ, {"SPADRA_OMNICLASS_CATALOG": str(target),
                                          "SPADRA_CSI_CROSSWALK_KEY": ""}):
                self.assertEqual(CL.search("assembly", "Unmapped")[0]["code"], "TEST-B")
                self.assertEqual(len(CL.info()["omniclass"]["supported_tables"]), 15)
                self.assertEqual(CL.search("omniclass", "Synthetic Space", table=13)[0]["code"],
                                 "13-FAKE-01")
                self.assertEqual(CL.search("omniclass", "Synthetic Part", table=21)[0]["code"],
                                 "21-FAKE-02")
                self.assertEqual(CL.search("omniclass", "Synthetic Product", table=23)[0]["code"],
                                 "23-FAKE-01")
            before = target.read_bytes()
            mapping.write_text("assembly_code,omniclass_code,table,verified,source\nTEST-A,23-FAKE-01,23,true,Incorrect\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                CL.import_authorized_catalog(table21=t21, table23=t23,
                    edition="SYNTHETIC", output=target, mapping=mapping)
            self.assertEqual(target.read_bytes(), before)

    def test_does_not_silently_import_unverified_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "crosswalk.csv"
            path.write_text("assembly_code,omniclass_code,verified,source\nA,21-FAKE-01,false,Synthetic\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                CL._mapping_entries(path)


class CsiCrosswalkLiveAdapter(unittest.TestCase):
    """Contract tests against the CSI v3 documented GraphQL shape; no live key or dataset."""
    def setUp(self):
        self.patcher = patch.dict(os.environ, {
            "SPADRA_CSI_CROSSWALK_KEY": "FAKE-MOCKED-KEY",
            "SPADRA_OMNICLASS_CATALOG": "",
        })
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        local_missing = Path(tmp.name) / "missing.json"
        p = patch.object(CL, "_catalog_path", return_value=local_missing)
        p.start()
        self.addCleanup(p.stop)
        self.project = Project.create("CSI Contract")
        self.wall = self.project.execute("model.add_wall", {
            "start": [0, 0], "end": [4000, 0], "thickness": 200, "height": 3,
            "level": self.project.manifest["levels"][0]["id"],
        })["result"]["id"]

    def _mock_response(self, *, ambiguous=False):
        def request_handler(request, timeout):
            self.assertEqual(request.full_url, CL.CSI_API_URL)
            self.assertEqual(request.get_header("Authorization"), "FAKE-MOCKED-KEY")
            query = json.loads(request.data)["query"]
            if 'id: "uniformat"' in query:
                if "crosswalks" in query:
                    matches = [{"number": "21-FAKE-01", "title": "Synthetic wall",
                                "standards": [{"name": "OmniClass", "version": "2012"}]}]
                    if ambiguous:
                        matches.append({"number": "21-FAKE-02", "title": "Another",
                                        "standards": [{"name": "OmniClass", "version": "2012"}]})
                    classes = [{"number": "TEST-A", "title": "Test Assembly",
                                "crosswalks": matches}]
                else:
                    classes = [{"number": "TEST-A", "title": "Test Assembly"}]
            elif 'id: "omniclass"' in query:
                if 'table: "23"' in query:
                    classes = [{"number": "23-FAKE-01", "title": "Synthetic product"}]
                else:
                    classes = [{"number": "21-FAKE-01", "title": "Synthetic wall"}]
            else:
                raise AssertionError("Unexpected mocked GraphQL query")
            payload = json.dumps({"data": {"standards": [
                {"name": "OmniClass", "version": "2012", "classifications": classes}
            ]}}).encode("utf-8")
            class Response:
                def __enter__(self): return self
                def __exit__(self, *args): return None
                def read(self, _): return payload
            return Response()
        return request_handler

    def test_licensed_crosswalk_search_and_exact_validation(self):
        with patch.object(CL.urlrequest, "urlopen", side_effect=self._mock_response()):
            self.assertEqual(CL.info()["omniclass"]["provider"], "csi_crosswalk")
            self.assertEqual(CL.search("assembly", "TEST")[0]["code"], "TEST-A")
            self.assertEqual(CL.search("omniclass", "wall", table=21)[0]["code"], "21-FAKE-01")
            self.assertEqual(len(CL.info()["omniclass"]["supported_tables"]), 15)
            self.assertEqual(CL._manual_omni("21-FAKE-01", 21), "21-FAKE-01")
            with self.assertRaises(BadRequest):
                CL._manual_omni("INVALID", 21)
            self.project.execute("model.set_property", {"id": self.wall, "key": "assembly_code",
                                                         "value": "TEST-A"})
        meta = self.project.model.get(self.wall)["metadata"]
        self.assertEqual(CL.read(meta, "omniclass_21"), "21-FAKE-01")
        self.assertEqual(CL.read(meta, "omniclass_21_origin"), "auto")
        self.assertIn("CSI Crosswalk", CL.read(meta, "omniclass_21_source"))
        self.assertEqual(CL.read(meta, "omniclass_23"), "")

    def test_many_to_many_crosswalk_does_not_guess(self):
        with patch.object(CL.urlrequest, "urlopen", side_effect=self._mock_response(ambiguous=True)):
            self.project.execute("model.set_property", {"id": self.wall, "key": "assembly_code",
                                                         "value": "TEST-A"})
        self.assertEqual(CL.read(self.project.model.get(self.wall)["metadata"], "omniclass_21"), "")

    def test_bad_credentials_do_not_echo_api_key(self):
        from urllib.error import HTTPError
        from ...tools.errors import DependencyMissing
        with patch.object(CL.urlrequest, "urlopen",
                          side_effect=HTTPError(CL.CSI_API_URL, 401, "Unauthorized", {}, None)):
            with self.assertRaises(DependencyMissing) as error:
                CL.search("omniclass", "wall")
        self.assertNotIn("FAKE-MOCKED-KEY", str(error.exception))


class Http(ServerCase):
    def test_ifc_search_endpoint_uses_real_schema(self):
        data = self.api.get("/api/classifications")
        self.assertEqual(data["ifc"]["version"], "4.3.2.0")
        search = self.api.get("/api/classifications/search?kind=ifc&q=IfcWallType")
        self.assertEqual(search["results"][0]["code"], "IfcWallType")


if __name__ == "__main__":
    unittest.main()
