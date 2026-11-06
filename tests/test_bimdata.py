"""IFC property sets, quantity sets from geometry, and the bSDD client (with a stand-in transport: nothing here touches the network)."""
from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

from applications.SPADRA import bimdata as BD
from applications.SPADRA import elements as E
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest, DependencyMissing, NotFound

from .test_bim import Case


class Catalogue(Case):
    def test_every_set_names_its_source_and_edition(self):
        for name, s in BD.catalog()["sets"].items():
            self.assertTrue(s["source"].startswith("https://"), name)
            self.assertTrue(s["edition"], name)
            self.assertTrue(name.startswith(("Pset_", "Qto_")))

    def test_search_finds_properties_across_sets(self):
        hits = BD.search("LoadBearing")
        self.assertIn("Pset_WallCommon", {h["set"] for h in hits})
        self.assertEqual(BD.search("LoadBearing", entity="IfcDoor"), [])

    def test_builder_reads_psd_files(self):
        from applications.SPADRA.data import build_ifc_psets as B
        xml = ('<PropertySetDef><Name>Pset_Fake</Name><ApplicableClasses><ClassName>IfcWall/NOTDEFINED</ClassName></ApplicableClasses><PropertyDefs>'
               '<PropertyDef><Name>IsExternal</Name><PropertyType><TypePropertySingleValue><DataType type="IfcBoolean"/></TypePropertySingleValue></PropertyType></PropertyDef>'
               '<PropertyDef><Name>Status</Name><PropertyType><TypePropertyEnumeratedValue><EnumList><EnumItem>A</EnumItem><EnumItem>B</EnumItem></EnumList>'
               '</TypePropertyEnumeratedValue></PropertyType></PropertyDef></PropertyDefs></PropertySetDef>')
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "Pset_Fake.xml").write_text(xml)
            sets = B.from_psd(Path(d), "TEST")
        self.assertEqual(sets["Pset_Fake"]["entities"], ["IfcWall"])
        self.assertEqual(sets["Pset_Fake"]["properties"]["Status"]["enum"], ["A", "B"])
        self.assertEqual(sets["Pset_Fake"]["properties"]["IsExternal"]["kind"], "bool")


class Psets(Case):
    def setUp(self) -> None:
        super().setUp()
        self.w = self.wall()
        self.w2 = self.wall(a=(0, 2000), b=(6000, 2000))

    def eff(self, oid):
        return BD.effective(self.p, oid)

    def test_a_value_is_stored_in_the_element_and_survives_undo_redo(self):
        self.run_("model.set_pset", id=self.w, set="Pset_WallCommon", property="FireRating", value="REI 60")
        self.assertEqual(self.meta(self.w)["spadra:psets"]["Pset_WallCommon"]["FireRating"], "REI 60")
        self.p.undo()
        self.assertNotIn("spadra:psets", self.meta(self.w))
        self.p.redo()
        self.assertEqual(self.eff(self.w)["Pset_WallCommon"]["FireRating"], {"value": "REI 60", "level": "occurrence"})

    def test_values_are_validated_against_the_official_data_type(self):
        for key, bad in [("IsExternal", "maybe"), ("Status", "Bogus"), ("ThermalTransmittance", -1), ("ThermalTransmittance", "x"), ("FireRating", "x" * 300)]:
            with self.assertRaises(BadRequest, msg=key):
                self.run_("model.set_pset", id=self.w, set="Pset_WallCommon", property=key, value=bad)
        self.run_("model.set_pset", id=self.w, set="Pset_WallCommon", property="Status", value="existing")
        self.assertEqual(self.eff(self.w)["Pset_WallCommon"]["Status"]["value"], "Existing")
        self.run_("model.set_pset", id=self.w, set="Pset_WallCommon", property="IsExternal", value="yes")
        self.assertIs(self.eff(self.w)["Pset_WallCommon"]["IsExternal"]["value"], True)

    def test_only_properties_the_official_set_defines_are_accepted(self):
        with self.assertRaises(BadRequest) as c:
            self.run_("model.set_pset", id=self.w, set="Pset_WallCommon", property="MyOwn", value="1")
        self.assertIn("custom", c.exception.hint.lower())
        with self.assertRaises(BadRequest):
            self.run_("model.set_pset", id=self.w, set="Pset_Invented", property="X", value="1")

    def test_a_set_must_apply_to_the_element(self):
        with self.assertRaises(BadRequest):
            self.run_("model.set_pset", id=self.w, set="Pset_DoorCommon", property="FireRating", value="30")

    def test_type_values_are_inherited_and_the_occurrence_overrides(self):
        self.run_("model.set_pset", id=self.w, set="Pset_WallCommon", property="LoadBearing", value=True, level="type")
        self.assertEqual(self.eff(self.w2)["Pset_WallCommon"]["LoadBearing"], {"value": True, "level": "type"})
        self.run_("model.set_pset", id=self.w2, set="Pset_WallCommon", property="LoadBearing", value=False)
        self.assertEqual(self.eff(self.w2)["Pset_WallCommon"]["LoadBearing"], {"value": False, "level": "occurrence"})
        self.assertEqual(self.eff(self.w)["Pset_WallCommon"]["LoadBearing"]["level"], "type")
        self.p.undo()
        self.p.undo()
        self.assertEqual(self.eff(self.w), {})

    def test_clearing_removes_the_value_and_the_empty_containers(self):
        self.run_("model.set_pset", id=self.w, set="Pset_WallCommon", property="FireRating", value="30")
        self.run_("model.set_pset", id=self.w, set="Pset_WallCommon", property="FireRating", value="")
        self.assertNotIn("spadra:psets", self.meta(self.w))

    def test_the_properties_panel_edits_and_shows_them(self):
        self.run_("model.set_property", id=self.w, key="pset:Pset_WallCommon:IsExternal", value="true")
        rows = self.rows(self.w)
        self.assertEqual(rows["pset:Pset_WallCommon:IsExternal"]["value"], "true")
        self.assertEqual(rows["pset:Pset_WallCommon:IsExternal"]["type"], "choice")
        with self.assertRaises(BadRequest):
            self.run_("model.set_property", id=self.w, key="pset:Pset_WallCommon:LoadBearing", value="perhaps")

    def test_custom_properties_are_listed_apart_from_the_official_ones(self):
        self.run_("model.set_property", id=self.w, key="custom:Colour", value="red")
        out = self.p.properties({"model": self.w})
        titles = [g["title"] for g in out["groups"]]
        self.assertIn("Custom properties (not buildingSMART)", titles)
        self.assertTrue(any(t.startswith("Pset_WallCommon") for t in titles))
        official = next(g for g in out["groups"] if g["title"].startswith("Pset_WallCommon"))
        self.assertNotIn("Colour", [r["label"] for r in official["rows"]])

    def test_the_data_is_in_the_saved_project(self):
        self.run_("model.set_pset", id=self.w, set="Pset_WallCommon", property="FireRating", value="60")
        self.run_("model.set_pset", id=self.w, set="Pset_WallCommon", property="LoadBearing", value=True, level="type")
        with tempfile.TemporaryDirectory() as d:
            self.p.save(Path(d) / "x.spadra")
            again = Project.open(Path(d) / "x.spadra")
        self.assertEqual(BD.effective(again, self.w)["Pset_WallCommon"]["FireRating"]["value"], "60")
        self.assertEqual(BD.effective(again, self.w)["Pset_WallCommon"]["LoadBearing"]["level"], "type")


class Quantities(Case):
    def setUp(self) -> None:
        super().setUp()
        self.w = self.wall(a=(0, 0), b=(6000, 0))

    def q(self, oid):
        return {r["name"]: r for r in BD.effective_quantities(self.p, oid)["quantities"]}

    def test_wall_quantities_come_from_the_geometry_and_account_for_openings(self):
        before = self.q(self.w)
        net = self.vol(self.w)
        self.assertAlmostEqual(before["NetVolume"]["value"], net, places=6)
        self.assertAlmostEqual(before["Length"]["value"], 6.0)
        self.assertAlmostEqual(before["GrossSideArea"]["value"], 6.0 * 3.2)
        d = self.run_("model.add_door", wall=self.w, offset=3.0, width=1000, height=2100)["id"]
        after = self.q(self.w)
        self.assertAlmostEqual(after["NetSideArea"]["value"], 6.0 * 3.2 - 1.0 * 2.1, places=6)
        self.assertLess(after["NetVolume"]["value"], net)
        self.assertAlmostEqual(after["GrossVolume"]["value"], net, places=5)                    # the opening is added back to the net volume
        door = self.q(d)
        self.assertAlmostEqual(door["Area"]["value"], 2.1)
        self.assertAlmostEqual(door["Perimeter"]["value"], 6.2)

    def test_quantities_are_recalculated_after_an_edit(self):
        before = self.q(self.w)["NetVolume"]["value"]
        self.run_("model.set_property", id=self.w, key="height", value=4.0)
        self.assertAlmostEqual(self.q(self.w)["Height"]["value"], 4.0)
        self.assertAlmostEqual(self.q(self.w)["NetVolume"]["value"], before * 4.0 / 3.2, places=5)

    def test_a_layered_wall_counts_every_layer_in_its_width(self):
        self.run_("model.add_layer", id=self.w)
        width = self.q(self.w)["Width"]["value"]
        self.assertGreater(width, 0.25)

    def test_an_override_is_kept_marked_and_reported_when_it_differs(self):
        self.run_("model.set_quantity", id=self.w, set="Qto_WallBaseQuantities", name="Length", value=6.5, mode="overridden")
        r = self.q(self.w)["Length"]
        self.assertEqual((r["mode"], r["value"], r["calculated"]), ("overridden", 6.5, 6.0))
        self.assertTrue(r["differs"])
        self.run_("model.set_wall_end" if False else "model.set_property", id=self.w, key="height", value=3.0)
        self.assertEqual(self.q(self.w)["Length"]["value"], 6.5)                                  # the override stays
        self.run_("model.set_quantity", id=self.w, set="Qto_WallBaseQuantities", name="Length", value=None, mode=None)
        self.assertEqual(self.q(self.w)["Length"]["mode"], "calculated")

    def test_an_imported_quantity_needs_its_source(self):
        with self.assertRaises(BadRequest):
            self.run_("model.set_quantity", id=self.w, set="Qto_WallBaseQuantities", name="Length", value=6.0, mode="imported")
        self.run_("model.set_quantity", id=self.w, set="Qto_WallBaseQuantities", name="Length", value=6.0, mode="imported", source="Revit IFC")
        r = self.q(self.w)["Length"]
        self.assertEqual((r["mode"], r["source"], r["differs"]), ("imported", "Revit IFC", False))

    def test_a_quantity_that_cannot_be_derived_is_left_out_with_the_reason(self):
        out = BD.effective_quantities(self.p, self.w)
        self.assertNotIn("NetFootPrintArea", [r["name"] for r in out["quantities"]])
        self.assertIn("NetFootPrintArea", out["omitted"])
        self.assertTrue(out["omitted"]["NetFootPrintArea"])

    def test_floor_roof_beam_column_and_room(self):
        f = self.run_("model.add_floor", level=self.l1, points=[[0, 0], [4000, 0], [4000, 3000], [0, 3000]], thickness=200)["id"]
        q = self.q(f)
        self.assertAlmostEqual(q["GrossArea"]["value"], 12.0)
        self.assertAlmostEqual(q["NetVolume"]["value"], 2.4, places=5)
        self.assertAlmostEqual(q["Length"]["value"], 4.0)
        self.assertAlmostEqual(q["Perimeter"]["value"], 14.0)
        b = self.run_("model.add_beam", start=[0, 0], end=[5000, 0], level=self.l1, top=3.0, width=200, depth=400)["id"]
        qb = self.q(b)
        self.assertAlmostEqual(qb["Length"]["value"], 5.0)
        self.assertAlmostEqual(qb["CrossSectionArea"]["value"], 0.08, places=5)
        self.assertAlmostEqual(qb["OuterSurfaceArea"]["value"], 0.6 * 2 * 5.0, places=4)
        c = self.run_("model.add_column", level=self.l1, point=[1000, 1000], base=0, top=3, width=300, depth=300)["id"]
        self.assertAlmostEqual(self.q(c)["Length"]["value"], 3.0)
        self.assertAlmostEqual(self.q(c)["CrossSectionArea"]["value"], 0.09, places=5)
        r = self.run_("model.add_roof", level=self.roof_level, roof="gable", points=[[0, 0], [6000, 0], [6000, 4000], [0, 4000]], thickness=100, pitch=30)["id"]
        qr = self.q(r)
        self.assertAlmostEqual(qr["ProjectedArea"]["value"], 24.0)
        self.assertAlmostEqual(qr["GrossArea"]["value"], 24.0 / math.cos(math.radians(30)), places=3)
        room = self.run_("model.add_room", level=self.l1, points=[[0, 0], [4000, 0], [4000, 3000], [0, 3000]])["id"]
        self.assertAlmostEqual(self.q(room)["GrossFloorArea"]["value"], 12.0)

    def test_the_schedule_and_exchange_carry_origin_and_catalogue_editions(self):
        self.run_("model.set_quantity", id=self.w, set="Qto_WallBaseQuantities", name="Length", value=7, mode="overridden")
        sch = BD.schedule(self.p, [self.w])
        self.assertEqual(sch["rows"][0]["quantities"]["Length"]["mode"], "overridden")
        self.run_("model.set_pset", id=self.w, set="Pset_WallCommon", property="IsExternal", value=True)
        ex = BD.exchange(self.p, [self.w])
        el = ex["elements"][0]
        self.assertEqual(el["ifc_entity"], "IfcWall")
        self.assertEqual(el["property_sets"]["Pset_WallCommon"]["IsExternal"]["type"], "IfcBoolean")
        self.assertEqual(el["quantity_sets"]["Qto_WallBaseQuantities"]["Length"]["origin"], "overridden")
        self.assertIn("Pset_WallCommon", ex["catalogue"]["sets"])


class Fake:
    """A stand-in bSDD transport recording the URLs it is asked for."""
    def __init__(self):
        self.calls, self.down = [], False
        self.cls = {"uri": "https://identifier.buildingsmart.org/uri/bs/ifc/4.3/class/IfcWall", "code": "IfcWall", "name": "Wall", "status": "Active",
                    "dictionaryUri": "https://identifier.buildingsmart.org/uri/bs/ifc/4.3", "dictionaryName": "IFC", "dictionaryVersion": "4.3"}

    def __call__(self, url):
        self.calls.append(url)
        if self.down:
            raise ConnectionError("no network")
        if "/Class/" in url:
            return json.dumps(self.cls).encode()
        return json.dumps({"classes": [self.cls]}).encode()


class Bsdd(Case):
    def setUp(self) -> None:
        super().setUp()
        self.w = self.wall()
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Fake()
        self.now = [1000.0]
        self.client = BD.Bsdd(self.t, Path(self.tmp.name) / "c.json", now=lambda: self.now[0])

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_search_is_cached_and_offline_mode_uses_only_the_cache(self):
        a = self.client.search("wall")
        self.assertEqual(a["source"], "network")
        b = self.client.search("wall")
        self.assertEqual((b["source"], len(self.t.calls)), ("cache", 1))
        self.t.down = True
        self.assertEqual(self.client.search("wall", offline=True)["source"], "cache")
        with self.assertRaises(BadRequest):
            self.client.search("door", offline=True)

    def test_a_stale_entry_is_served_marked_when_the_network_fails(self):
        self.client.search("wall")
        self.now[0] += BD.CACHE_TTL + 5
        self.t.down = True
        r = self.client.search("wall")
        self.assertTrue(r["stale"])
        self.assertIn("could not be reached", r["warning"])
        with self.assertRaises(DependencyMissing):
            self.client.search("window")

    def test_requests_only_go_to_the_buildingsmart_origin(self):
        with self.assertRaises(BadRequest):
            BD._fetch("https://example.com/api")

    def test_assign_records_provenance_and_allows_one_class_per_dictionary(self):
        ref = BD.assign(self.p, self.w, self.t.cls["uri"], client=self.client)
        self.assertEqual((ref["code"], ref["version"], ref["origin"], ref["dictionary_name"]), ("IfcWall", "4.3", "manual", "IFC"))
        self.assertTrue(ref["retrieved"])
        self.assertEqual(BD.assign(self.p, self.w, self.t.cls["uri"], client=self.client)["uri"], ref["uri"])        # same class again: no duplicate
        self.assertEqual(len(BD.references(self.p, self.w)), 1)
        self.t.cls = {**self.t.cls, "uri": "https://identifier.buildingsmart.org/uri/bs/ifc/4.3/class/IfcSlab", "code": "IfcSlab"}
        with self.assertRaises(BadRequest) as c:
            BD.assign(self.p, self.w, self.t.cls["uri"], client=self.client)
        self.assertIn("one class per dictionary", c.exception.hint)
        BD.unassign(self.p, self.w, "https://identifier.buildingsmart.org/uri/bs/ifc/4.3/class/IfcWall")
        self.assertEqual(BD.references(self.p, self.w), [])

    def test_a_suggestion_is_not_authoritative_and_needs_confirmation(self):
        s = BD.suggest(self.p, self.w, client=self.client)
        self.assertFalse(s["authoritative"])
        self.assertEqual(s["results"][0]["origin"], "suggested")
        self.assertEqual(BD.references(self.p, self.w), [])                                       # suggesting stores nothing
        with self.assertRaises(BadRequest):
            BD.assign(self.p, self.w, self.t.cls["uri"], origin="suggested", client=self.client)
        BD.assign(self.p, self.w, self.t.cls["uri"], origin="suggested", confirm=True, client=self.client)
        self.assertEqual(BD.references(self.p, self.w)[0]["origin"], "suggested")

    def test_an_inactive_class_needs_confirmation(self):
        self.t.cls["status"] = "Inactive"
        with self.assertRaises(BadRequest):
            BD.assign(self.p, self.w, self.t.cls["uri"], client=self.client)
        BD.assign(self.p, self.w, self.t.cls["uri"], confirm=True, client=self.client)

    def test_an_unknown_class_is_refused(self):
        self.t.cls = {}
        with self.assertRaises(NotFound):
            BD.assign(self.p, self.w, "https://identifier.buildingsmart.org/uri/x/y/1/class/none", client=self.client)

    def test_an_unreachable_service_with_nothing_cached_is_reported_not_guessed(self):
        self.t.down = True
        with self.assertRaises(DependencyMissing):
            BD.assign(self.p, self.w, self.t.cls["uri"], client=self.client)
        self.assertEqual(BD.references(self.p, self.w), [])

    def test_check_reports_a_conflicting_pair_from_an_import(self):
        meta = dict(self.meta(self.w))
        ref = {"uri": "u1", "code": "A", "dictionary_uri": "https://d/1", "version": "1", "dictionary_name": "D"}
        meta["spadra:bsdd"] = [ref, {**ref, "uri": "u2", "code": "B"}]
        self.p.model.attach_semantics(self.w, meta, replace=True)
        self.assertTrue(any(i["category"] == "bsdd" for i in self.p.check()["issues"]))
