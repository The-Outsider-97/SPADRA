"""IDS import and validation (with evidence), and the BIM basis ILS rules."""
from __future__ import annotations

from applications.SPADRA import bimdata as BD
from applications.SPADRA import requirements as RQ
from applications.tools.errors import BadRequest, NotFound

from .test_bim import Case

NS = 'xmlns="http://standards.buildingsmart.org/IDS" xmlns:xs="http://www.w3.org/2001/XMLSchema"'


def ids(*specs: str, title: str = "Test IDS") -> str:
    return f'<ids {NS}><info><title>{title}</title></info><specifications>{"".join(specs)}</specifications></ids>'


def spec(name: str, applicability: str, requirements: str, mn: str = "0", mx: str = "unbounded") -> str:
    return (f'<specification name="{name}" ifcVersion="IFC4X3_ADD2"><applicability minOccurs="{mn}" maxOccurs="{mx}">{applicability}</applicability>'
            f'<requirements>{requirements}</requirements></specification>')


WALLS = '<entity><name><simpleValue>IFCWALL</simpleValue></name></entity>'
EXTERNAL = ('<property cardinality="required" dataType="IFCBOOLEAN"><propertySet><simpleValue>Pset_WallCommon</simpleValue></propertySet>'
            '<baseName><simpleValue>IsExternal</simpleValue></baseName><value><simpleValue>TRUE</simpleValue></value></property>')


class Ids(Case):
    def setUp(self) -> None:
        super().setUp()
        self.a = self.wall(a=(0, 0), b=(6000, 0))
        self.b = self.wall(a=(0, 2000), b=(6000, 2000))
        self.run_("model.set_pset", id=self.a, set="Pset_WallCommon", property="IsExternal", value=True)

    def load(self, xml):
        return self.run_("bim.add_ids", xml=xml, filename="t.ids")["id"]

    def validate(self, xml):
        i = self.load(xml)
        self.run_("bim.validate_ids", id=i)
        return i, RQ.ids_report(self.p, i)["result"]

    def test_a_wall_without_the_required_property_fails_with_evidence(self):
        _, res = self.validate(ids(spec("Walls are external", WALLS, EXTERNAL)))
        sp = res["specs"][0]
        self.assertEqual((sp["status"], sp["applicable"], sp["passed"], sp["failed"]), ("fail", 2, 1, 1))
        f = sp["failures"][0]
        self.assertEqual(f["element"], self.b)
        self.assertIn("Pset_WallCommon.IsExternal", f["expected"])
        self.assertEqual(f["found"], "property absent")

    def test_fixing_the_model_and_revalidating_passes_and_the_old_result_is_marked_out_of_date(self):
        i, res = self.validate(ids(spec("Walls are external", WALLS, EXTERNAL)))
        self.run_("model.set_pset", id=self.b, set="Pset_WallCommon", property="IsExternal", value=True)
        self.assertTrue(RQ.ids_report(self.p, i)["stale"])
        self.assertTrue(any("earlier model revision" in x["message"] for x in self.p.check()["issues"] if x["category"] == "ids"))
        self.run_("bim.validate_ids", id=i)
        rep = RQ.ids_report(self.p, i)
        self.assertEqual(rep["result"]["specs"][0]["status"], "pass")
        self.assertFalse(rep["stale"])

    def test_findings_are_in_the_project_check_under_their_own_category(self):
        self.validate(ids(spec("Walls are external", WALLS, EXTERNAL)))
        found = [x for x in self.p.check()["issues"] if x["category"] == "ids"]
        self.assertTrue(any(x["ref"] == self.b for x in found))
        self.assertFalse(any(x["category"] == "geometry" for x in found))

    def test_a_specification_that_requires_elements_and_finds_none_fails(self):
        _, res = self.validate(ids(spec("Need a door", '<entity><name><simpleValue>IFCDOOR</simpleValue></name></entity>', EXTERNAL, mn="1")))
        self.assertEqual(res["specs"][0]["status"], "fail")
        self.assertTrue(res["specs"][0]["required_but_none_apply"])
        _, res2 = self.validate(ids(spec("Optional door", '<entity><name><simpleValue>IFCDOOR</simpleValue></name></entity>', EXTERNAL), title="B"))
        self.assertEqual(res2["specs"][0]["status"], "not_applicable")

    def test_restrictions_enumerations_patterns_and_ranges(self):
        r = ('<xs:restriction base="xs:string"><xs:enumeration value="60"/><xs:enumeration value="90"/></xs:restriction>')
        req = ('<property cardinality="required"><propertySet><simpleValue>Pset_WallCommon</simpleValue></propertySet><baseName><simpleValue>FireRating</simpleValue></baseName>'
               f'<value>{r}</value></property>')
        self.run_("model.set_pset", id=self.a, set="Pset_WallCommon", property="FireRating", value="60")
        self.run_("model.set_pset", id=self.b, set="Pset_WallCommon", property="FireRating", value="30")
        _, res = self.validate(ids(spec("Fire", WALLS, req)))
        self.assertEqual([f["element"] for f in res["specs"][0]["failures"]], [self.b])
        self.assertTrue(RQ.matches({"restriction": {"base": "", "enumeration": [], "pattern": [r"\d+"]}}, "123"))
        self.assertFalse(RQ.matches({"restriction": {"base": "", "enumeration": [], "pattern": [], "minInclusive": "5", "maxExclusive": "10"}}, 10))
        self.assertTrue(RQ.matches({"simple": "TRUE"}, True))

    def test_quantities_are_checked_like_properties(self):
        req = ('<property cardinality="required"><propertySet><simpleValue>Qto_WallBaseQuantities</simpleValue></propertySet><baseName><simpleValue>Length</simpleValue></baseName>'
               '<value><xs:restriction base="xs:double"><xs:minInclusive value="5.5"/></xs:restriction></value></property>')
        _, res = self.validate(ids(spec("Long walls", WALLS, req)))
        self.assertEqual(res["specs"][0]["status"], "pass")

    def test_classification_and_material_facets(self):
        self.run_("model.set_property", id=self.a, key="sfb", value="21.11")
        req = '<classification cardinality="required"><value><simpleValue>21.11</simpleValue></value><system><simpleValue>NL-SfB</simpleValue></system></classification>'
        _, res = self.validate(ids(spec("SfB", WALLS, req)))
        self.assertEqual([f["element"] for f in res["specs"][0]["failures"]], [self.b])
        mat = '<material cardinality="required"/>'
        _, res = self.validate(ids(spec("Material", WALLS, mat), title="M"))
        self.assertEqual(res["specs"][0]["status"], "pass")

    def test_what_spadra_cannot_evaluate_is_not_called_passed(self):
        req = '<attribute cardinality="required"><name><simpleValue>Description</simpleValue></name><value><simpleValue>x</simpleValue></value></attribute>'
        _, res = self.validate(ids(spec("Description", WALLS, req)))
        self.assertEqual(res["specs"][0]["status"], "not_evaluable")
        self.assertEqual(res["summary"]["pass"], 0)

    def test_attribute_and_superclass_applicability(self):
        gen = '<entity><name><simpleValue>IFCBUILDINGELEMENT</simpleValue></name></entity>'
        d = self.run_("model.add_door", wall=self.a, offset=3.0)["id"]
        _, res = self.validate(ids(spec("All built", gen, '<attribute><name><simpleValue>Name</simpleValue></name></attribute>')))
        self.assertEqual(res["specs"][0]["applicable"], 3)

    def test_unsafe_or_wrong_files_are_refused(self):
        for bad in ('<!DOCTYPE x [<!ENTITY a "b">]><ids/>', "<notids/>", "not xml", ids().replace("<specifications></specifications>", "<specifications/>")):
            with self.assertRaises(BadRequest):
                RQ.parse_ids(bad.encode())
        with self.assertRaises(BadRequest):
            RQ.parse_ids(b" " * (RQ.MAX_IDS_BYTES + 1))

    def test_the_same_file_twice_and_removal(self):
        xml = ids(spec("x", WALLS, EXTERNAL))
        i = self.load(xml)
        with self.assertRaises(BadRequest):
            self.load(xml)
        self.run_("bim.remove_ids", id=i)
        with self.assertRaises(NotFound):
            self.run_("bim.remove_ids", id=i)

    def test_ids_and_results_survive_undo_and_save(self):
        import tempfile
        from pathlib import Path
        from applications.SPADRA.project import Project
        i, _ = self.validate(ids(spec("Walls are external", WALLS, EXTERNAL)))
        with tempfile.TemporaryDirectory() as d:
            self.p.save(Path(d) / "x.spadra")
            again = Project.open(Path(d) / "x.spadra")
        self.assertEqual(RQ.ids_report(again, i)["result"]["specs"][0]["failed"], 1)
        self.p.undo()
        self.assertIsNone(RQ.ids_report(self.p, i)["result"])


class Ils(Case):
    def setUp(self) -> None:
        super().setUp()
        self.w = self.wall()
        self.f = self.run_("model.add_floor", level=self.l1, points=[[0, 0], [4000, 0], [4000, 3000], [0, 3000]], thickness=200)["id"]

    def report(self):
        return {r["id"]: r for r in RQ.ils_report(self.p)["rules"]}

    def test_the_ils_is_off_until_a_project_turns_it_on(self):
        self.assertFalse(any(i["category"] == "ils" for i in self.p.check()["issues"]))
        self.run_("bim.set_ils", enabled=True)
        found = [i for i in self.p.check()["issues"] if i["category"] == "ils"]
        self.assertTrue(any("ILS-4.4-ISEXTERNAL" in i["message"] and i["severity"] == "error" for i in found))

    def test_isexternal_is_required_on_walls_and_floors_and_met_once_set(self):
        self.assertEqual(self.report()["ILS-4.4-ISEXTERNAL"]["failed"], 2)
        self.run_("model.set_pset", id=self.w, set="Pset_WallCommon", property="IsExternal", value=False)
        self.assertEqual(self.report()["ILS-4.4-ISEXTERNAL"]["failed"], 1)

    def test_every_baseline_rule_cites_its_chapter_and_its_strength(self):
        for r in RQ.ils_report(self.p)["rules"]:
            if r["scope"] == "baseline":
                self.assertTrue(r["source"].startswith("https://www.digigo.nu/"), r["id"])
                self.assertIn(r["strength"], RQ.STRENGTH_SEVERITY)
        self.assertEqual(self.report()["ILS-4.3-LOADBEARING"]["severity"], "info")
        self.assertEqual(self.report()["ILS-4.4-ISEXTERNAL"]["severity"], "error")

    def test_sfb_notation_and_fire_rating_format(self):
        self.assertEqual(self.report()["ILS-3.6-SFB"]["failed"], 2)
        self.run_("model.set_property", id=self.w, key="sfb", value="21")
        self.assertEqual(self.report()["ILS-3.6-SFB"]["failed"], 2)                    # a coarser code is not a 4-digit code
        self.run_("model.set_property", id=self.w, key="sfb", value="21.11")
        self.assertEqual(self.report()["ILS-3.6-SFB"]["failed"], 1)
        self.run_("model.set_pset", id=self.w, set="Pset_WallCommon", property="FireRating", value="REI 60")
        self.assertEqual(self.report()["ILS-4.5-FIRERATING"]["failed"], 1)
        self.run_("model.set_pset", id=self.w, set="Pset_WallCommon", property="FireRating", value="60")
        self.assertEqual(self.report()["ILS-4.5-FIRERATING"]["failed"], 0)

    def test_a_custom_property_that_duplicates_an_official_one_is_flagged(self):
        self.run_("model.set_property", id=self.w, key="custom:LoadBearing", value="true")
        self.assertEqual(self.report()["ILS-3.7-OFFICIAL"]["failed"], 1)

    def test_a_rule_can_be_disabled_and_regraded(self):
        self.run_("bim.set_ils", enabled=True, disabled=["ILS-4.4-ISEXTERNAL"], severity={"ILS-3.6-SFB": "error"})
        self.assertFalse(self.report()["ILS-4.4-ISEXTERNAL"]["enabled"])
        self.assertEqual(self.report()["ILS-3.6-SFB"]["severity"], "error")
        with self.assertRaises(BadRequest):
            self.run_("bim.set_ils", disabled=["NOPE"])

    def test_a_custom_set_name_may_not_start_with_pset(self):
        with self.assertRaises(BadRequest):
            self.run_("bim.set_ils", custom_set_name="Pset_Mine")
        self.run_("bim.set_ils", custom_set_name="Project_Data")
        self.assertEqual(RQ.ils_config(self.p)["custom_set_name"], "Project_Data")

    def test_project_rules_must_cite_their_source_and_are_reported_apart_from_the_baseline(self):
        with self.assertRaises(BadRequest):
            self.run_("bim.add_ils_rule", id="P1", categories=["wall"], set="Pset_WallCommon", property="FireRating")
        self.run_("bim.add_ils_rule", id="P1", source="Client brief 2026-11, section 4", categories=["wall"], set="Pset_WallCommon", property="FireRating", allowed=["60", "90"])
        r = self.report()["P1"]
        self.assertEqual((r["scope"], r["applicable"], r["failed"], r["source"]), ("project", 1, 1, "Client brief 2026-11, section 4"))
        self.run_("model.set_pset", id=self.w, set="Pset_WallCommon", property="FireRating", value="60")
        self.assertEqual(self.report()["P1"]["failed"], 0)
        with self.assertRaises(BadRequest):
            self.run_("bim.add_ils_rule", id="P2", source="x", categories=["wall"], set="Pset_WallCommon", property="Invented")
        self.run_("bim.remove_ils_rule", id="P1")
        self.assertNotIn("P1", self.report())

    def test_material_name_heuristic(self):
        self.run_("model.set_property", id=self.w, key="material", value="Concrete")
        self.assertEqual(self.report()["ILS-4.7-MATERIALNAME"]["failed"], 0)


from .harness import ServerCase                                  # noqa: E402


class Http(ServerCase):
    def setUp(self) -> None:
        super().setUp()
        self.api.post("/api/project/new", {"name": "BIM http", "unit": "mm"})
        level = self.app.require().manifest["levels"][0]["id"]
        self.wall = self.api.cmd("model.add_wall", {"start": [0, 0], "end": [4000, 0], "level": level, "thickness": 200, "height": 3})["result"]["id"]

    def test_catalogue_search_quantities_exchange(self):
        self.assertIn("Pset_WallCommon", [s["name"] for s in self.api.get("/api/bim")["sets"]])
        self.assertTrue(self.api.get("/api/bim/properties/search?q=LoadBearing&entity=IfcWall")["results"])
        q = self.api.post("/api/bim/quantities", {"ids": [self.wall]})
        self.assertAlmostEqual(q["rows"][0]["quantities"]["Length"]["value"], 4.0)
        self.api.cmd("model.set_pset", {"id": self.wall, "set": "Pset_WallCommon", "property": "IsExternal", "value": True})
        ex = self.api.post("/api/bim/exchange", {"ids": [self.wall]})
        self.assertTrue(ex["elements"][0]["property_sets"]["Pset_WallCommon"]["IsExternal"]["value"])

    def test_ids_and_ils_over_http(self):
        i = self.api.cmd("bim.add_ids", {"xml": ids(spec("Walls are external", WALLS, EXTERNAL)), "filename": "a.ids"})["result"]["id"]
        self.assertEqual(self.api.get("/api/ids")["files"][0]["id"], i)
        self.api.cmd("bim.validate_ids", {"id": i})
        rep = self.api.get(f"/api/ids/report?id={i}")
        self.assertEqual(rep["result"]["specs"][0]["status"], "fail")
        self.api.cmd("bim.set_ils", {"enabled": True})
        self.assertTrue(self.api.get("/api/ils")["config"]["enabled"])
        self.assertEqual(self.api.get("/api/classifications/browse?kind=assembly")["available"], False)

    def test_bsdd_routes_refuse_to_guess_when_the_service_is_unreachable(self):
        from unittest.mock import patch
        from applications.SPADRA import bimdata as BD
        def down(url):
            raise ConnectionError("no network")
        with patch.object(BD.CLIENT, "transport", down), patch.object(BD.CLIENT, "cache_file", self.dir / "none.json"):
            status, out = self.api.call("GET", "/api/bsdd/search?q=wall")
        self.assertNotEqual(status, 200)
        self.assertFalse(out["ok"])


class ReportSection(Case):
    def test_the_report_cites_the_requirements_and_their_results(self):
        from applications.SPADRA import reports as RP
        w = self.wall()
        self.run_("bim.set_ils", enabled=True)
        i = self.run_("bim.add_ids", xml=ids(spec("Walls are external", WALLS, EXTERNAL)), filename="a.ids")["id"]
        self.run_("bim.validate_ids", id=i)
        data, entry = RP.build(self.p, "Req", ["requirements"])
        import io
        import zipfile
        blob = zipfile.ZipFile(io.BytesIO(data)).read("document.json").decode("utf-8")
        self.assertIn("ILS-4.4-ISEXTERNAL", blob)
        self.assertIn("https://www.digigo.nu/en/ilsen-en-richtlijnen/bim-base-ids/4-4-internal-external", blob)
        self.assertIn("Walls are external", blob)
        self.assertEqual(entry["sections"], ["requirements"])
