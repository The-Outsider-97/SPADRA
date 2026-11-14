"""End-to-end property, expression, and appearance contracts using the real SPADRA project."""
from __future__ import annotations

import tempfile
import base64
import unittest
from pathlib import Path

from applications.SPADRA import elements as E
from applications.SPADRA import bimdata as BD
from applications.SPADRA import relations as RL
from applications.SPADRA import interop as IO
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest


class PropertyWorkflow(unittest.TestCase):
    def setUp(self):
        self.project = Project.create("Property workflow")
        self.level = self.project.manifest["levels"][0]["id"]

    def cmd(self, op, **params):
        return self.project.execute(op, params)["result"]

    def wall(self):
        return self.cmd("model.add_wall", start=[0, 0], end=[5000, 0], level=self.level,
                        height=3.0, thickness=200)["id"]

    def test_custom_types_rename_delete_and_undo(self):
        wall = self.wall()
        self.cmd("model.set_property", id=wall, key="custom:BuildingID", value={"type": "text", "value": "0012"})
        self.cmd("model.set_property", id=wall, key="custom:Angle", value={"type": "angle", "value": 12.5})
        self.cmd("model.set_property", id=wall, key="custom:Inspected", value={"type": "boolean", "value": "false"})
        rows = next(g["rows"] for g in self.project.properties({"model": wall})["groups"] if g["title"].startswith("Custom properties"))
        by_key = {row["key"]: row for row in rows}
        self.assertEqual(by_key["custom:BuildingID"]["value"], "0012")
        self.assertEqual(by_key["custom:Angle"]["unit"], "°")
        self.assertEqual(by_key["custom:Inspected"]["value"], "false")
        self.cmd("model.set_property", id=wall, key="custom:BuildingID", value={"rename": "AssetID"})
        self.assertEqual(E.meta_of(self.project, wall)["custom:AssetID"], "0012")
        self.cmd("model.set_property", id=wall, key="custom:AssetID", value=None)
        self.assertNotIn("custom:AssetID", E.meta_of(self.project, wall))
        self.project.undo()
        self.assertEqual(E.meta_of(self.project, wall)["custom:AssetID"], "0012")

    def test_custom_rename_updates_formula_references(self):
        wall = self.wall()
        self.cmd("model.set_property", id=wall, key="custom:Span", value={"type": "length", "value": 3000})
        self.cmd("model.set_property", id=wall, key="thickness", value="=Span / 10")
        self.cmd("model.set_property", id=wall, key="material_rule:condition", value="Span > 2m")
        self.cmd("model.set_property", id=wall, key="material_rule:enabled", value="true")
        self.cmd("model.set_property", id=wall, key="custom:Span", value={"rename": "ClearSpan"})
        meta = E.meta_of(self.project, wall)
        self.assertEqual(E.m(meta, "formulas")["thickness"], "ClearSpan / 10")
        self.assertEqual(E.m(meta, "material_rule")["condition"], "ClearSpan > 2m")
        self.assertAlmostEqual(E.m(meta, "thickness"), 0.3)

    def test_shared_type_family_project_properties_and_instance_precedence(self):
        first, second = self.wall(), self.wall()
        self.cmd("model.set_property", id=first, key="custom:FireRating", value={"scope": "type", "type": "text", "value": "EI60"})
        self.cmd("model.set_property", id=first, key="custom:Discipline", value={"scope": "family", "type": "text", "value": "Architecture"})
        self.cmd("model.set_property", id=first, key="custom:ProjectCode", value={"scope": "project", "type": "text", "value": "SP-001"})
        effective = RL.effective_custom(self.project, second)
        self.assertEqual((effective["FireRating"]["value"], effective["FireRating"]["scope"]), ("EI60", "type"))
        self.assertEqual(effective["Discipline"]["scope"], "family")
        self.assertEqual(effective["ProjectCode"]["scope"], "project")
        self.cmd("model.set_property", id=second, key="custom:FireRating", value={"type": "text", "value": "EI90"})
        self.assertEqual(RL.effective_custom(self.project, second)["FireRating"]["value"], "EI90")
        self.assertEqual(RL.effective_custom(self.project, first)["FireRating"]["value"], "EI60")
        own_row = next(r for g in self.project.properties({"model": first})["groups"] for r in g["rows"] if r.get("key", "").startswith("customdef:") and r["label"].startswith("FireRating"))
        self.cmd("model.set_property", id=first, key=own_row["key"], value={"rename": "FireClass"})
        self.assertIn("FireClass", RL.effective_custom(self.project, first))
        self.project.undo()
        self.assertIn("FireRating", RL.effective_custom(self.project, second))

    def test_schedule_custom_columns_filters_and_shared_values(self):
        first, second = self.wall(), self.wall()
        self.cmd("model.set_property", id=first, key="custom:Rating", value={"scope": "type", "type": "text", "value": "EI60"})
        self.cmd("model.set_property", id=second, key="custom:Rating", value={"type": "text", "value": "EI90"})
        schedule = self.cmd("view.add_schedule", category="wall")["id"]
        self.cmd("view.set_schedule", id=schedule, key="custom_columns", value="Rating")
        self.cmd("view.set_schedule", id=schedule, key="custom_filters", value='[{"name":"Rating","op":"==","value":"EI90"}]')
        data = self.project.schedule(schedule)
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["rows"][0]["id"], second)
        self.assertEqual(data["rows"][0]["custom:Rating"], "EI90")
        self.assertIn("custom:Rating", [c["key"] for c in data["columns"]])

    def test_curtainwall_new_editable_controls(self):
        cw = self.cmd("model.add_curtainwall", start=[0, 0], end=[6000, 0], level=self.level, height=3000)["id"]
        panel = self.project.properties({"model": cw})
        fields = {r["key"] for r in panel["top"] + [r for g in panel["groups"] for r in g["rows"]] if r.get("key")}
        for key in ("mullion_depth", "transom_depth", "lean", "fin_depth", "fin_side", "shelf_depth", "shelf_side", "band_rows", "band_cols"):
            self.assertIn(key, fields)
        self.cmd("model.set_property", id=cw, key="lean", value=9)
        self.cmd("model.set_property", id=cw, key="fin_depth", value=110)
        self.cmd("model.set_property", id=cw, key="fin_side", value="both")
        self.assertEqual(E.m(E.meta_of(self.project, cw), "lean"), 9)
        self.assertAlmostEqual(E.m(E.meta_of(self.project, cw), "fin_depth"), 0.11)
        self.assertEqual(E.m(E.meta_of(self.project, cw), "fin_side"), "both")

    def test_curtainwall_part_selection_keeps_element_custom_properties(self):
        cw = self.cmd("model.add_curtainwall", start=[0, 0], end=[6000, 0], level=self.level, height=3000)["id"]
        self.cmd("model.set_property", id=cw, key="custom:Asset", value={"type": "text", "value": "CW-01"})
        part = self.project.scene_data()["objects"]
        part = next(o for o in part if o["id"] == cw)["components"][0]["part"]
        props = self.project.properties({"model": cw, "part": part})
        custom = next(g for g in props["groups"] if g["title"].startswith("Custom properties"))
        self.assertEqual(next(r for r in custom["rows"] if r["key"] == "custom:Asset")["value"], "CW-01")
        self.assertEqual(custom["add"], "custom")

    def test_numeric_formula_preserves_expression_and_recalculates(self):
        wall = self.wall()
        self.cmd("model.set_property", id=wall, key="thickness", value="=height / 10")
        self.assertAlmostEqual(E.m(E.meta_of(self.project, wall), "thickness"), 0.3)
        self.cmd("model.set_property", id=wall, key="height", value=4.0)
        self.assertAlmostEqual(E.m(E.meta_of(self.project, wall), "thickness"), 0.4)
        props = self.project.properties({"model": wall})
        row = next(row for row in props["top"] + [r for group in props["groups"] for r in group["rows"]]
                   if row.get("key") == "thickness")
        self.assertEqual(row["expression"], "height / 10")
        with self.assertRaises(BadRequest):
            self.cmd("model.set_property", id=wall, key="height", value="=height + 1m")
        self.assertAlmostEqual(E.m(E.meta_of(self.project, wall), "height"), 4.0)

    def test_wall_driving_length_formula_updates_actual_geometry(self):
        wall = self.wall()
        self.cmd("model.set_property", id=wall, key="length", value="=height * 2")
        props = self.project.properties({"model": wall})
        length = next(r for r in props["top"] + [r for g in props["groups"] for r in g["rows"]]
                      if r.get("key") == "length")
        self.assertAlmostEqual(length["value"], 6)
        self.cmd("model.set_property", id=wall, key="height", value=4)
        props = self.project.properties({"model": wall})
        length = next(r for r in props["top"] + [r for g in props["groups"] for r in g["rows"]]
                      if r.get("key") == "length")
        self.assertAlmostEqual(length["value"], 8)

    def test_nested_layer_and_curtainwall_panel_formulas(self):
        wall = self.wall()
        self.cmd("model.add_layer", id=wall)
        props = self.project.properties({"model": wall})
        layer = next(r["key"] for g in props["groups"] for r in g["rows"] if r.get("key", "").startswith("layer:") and r["key"].endswith(":thickness"))
        self.cmd("model.set_property", id=wall, key=layer, value="=height * 0.05")
        self.cmd("model.set_property", id=wall, key="height", value=4)
        props = self.project.properties({"model": wall})
        self.assertAlmostEqual(next(r for g in props["groups"] for r in g["rows"] if r.get("key") == layer)["value"], 200)

        cw = self.cmd("model.add_curtainwall", start=[0, 0], end=[6000, 0], level=self.level, height=3000)["id"]
        self.cmd("model.set_property", id=cw, key="panel:0:0:thickness", value="=height / 100")
        self.assertEqual(E.m(E.meta_of(self.project, cw), "formulas")["panel:0:0:thickness"], "height / 100")
        self.cmd("model.set_property", id=cw, key="height", value=3500)
        from applications.SPADRA import curtainwall as CW
        part_id = CW.cells(CW.spec(E.meta_of(self.project, cw)))[0]["key"]
        part = self.project.properties({"model": cw, "part": "cell:" + part_id})
        panel_thickness = next(r for r in part["top"] if r.get("key") == "panel:0:0:thickness")
        self.assertAlmostEqual(panel_thickness["value"], 35)
        self.assertEqual(panel_thickness["expression"], "height / 100")

    def test_height_condition_and_roof_gradient(self):
        wall = self.wall()
        self.cmd("model.set_property", id=wall, key="material_rule:condition", value="height > 1m")
        self.cmd("model.set_property", id=wall, key="material_rule:top", value="#ee1ef2")
        self.cmd("model.set_property", id=wall, key="material_rule:enabled", value="true")
        self.assertEqual(E.appearance_color(self.project, E.meta_of(self.project, wall), wall), "#EE1EF2")
        self.cmd("model.set_property", id=wall, key="height", value=0.8)
        self.assertNotEqual(E.appearance_color(self.project, E.meta_of(self.project, wall), wall), "#EE1EF2")
        self.cmd("model.set_property", id=wall, key="height", value=1.0)
        self.assertNotEqual(E.appearance_color(self.project, E.meta_of(self.project, wall), wall), "#EE1EF2")
        self.cmd("model.set_property", id=wall, key="height", value=1.2)
        self.assertEqual(E.appearance_color(self.project, E.meta_of(self.project, wall), wall), "#EE1EF2")

        roof = self.cmd("model.add_roof", points=[[0, 0], [5000, 0], [5000, 4000], [0, 4000]],
                        level=self.level, roof="hip", pitch=30, thickness=200)["id"]
        for field, value in (("condition", "slopeAngle > 5deg"), ("top", "#123abc"), ("bottom", "#fffe12"),
                             ("mode", "gradient"), ("transition", "abrupt"), ("enabled", "true")):
            self.cmd("model.set_property", id=roof, key="material_rule:" + field, value=value)
        scene = next(o for o in self.project.scene_data()["objects"] if o["id"] == roof)
        self.assertEqual(scene["gradient"]["top"], "#123ABC")
        self.assertEqual(scene["gradient"]["bottom"], "#FFFE12")
        self.assertEqual(scene["gradient"]["transition"], "abrupt")
        self.cmd("model.set_property", id=roof, key="pitch", value=5)
        self.assertIsNone(next(o for o in self.project.scene_data()["objects"] if o["id"] == roof)["gradient"])
        self.cmd("model.set_property", id=roof, key="pitch", value=5.1)
        self.assertIsNotNone(next(o for o in self.project.scene_data()["objects"] if o["id"] == roof)["gradient"])

    def test_conditional_color_can_reset_to_inherited_material(self):
        wall = self.wall()
        self.cmd("model.set_property", id=wall, key="material_rule:top", value="#EE1EF2")
        self.cmd("model.set_property", id=wall, key="material_rule:top", value="")
        self.assertEqual(E.m(E.meta_of(self.project, wall), "material_rule")["top"],
                         E.MATERIAL_COLORS.get(E.m(E.meta_of(self.project, wall), "material"), "#BFBFC7"))

    def test_shared_definition_and_independent_layer_gradients(self):
        first, second = self.wall(), self.wall()
        material = E.m(E.meta_of(self.project, first), "material")
        for key, value in (("condition", "height > 1m"), ("top", "#123abc"), ("bottom", "#fffe12"),
                           ("mode", "gradient"), ("enabled", "true")):
            self.cmd("model.set_property", id=first, key=f"material_def:{material}:{key}", value=value)
        scenes = {s["id"]: s for s in self.project.scene_data()["objects"]}
        self.assertEqual(scenes[second]["layer_gradients"][0]["top"], "#123ABC")

        self.cmd("model.add_layer", id=first)
        from applications.SPADRA import layers as LY
        layer = LY.stack(self.project, first)[0]["id"]
        for key, value in (("condition", "height > 1m"), ("top", "#ee1ef2"), ("mode", "gradient"), ("enabled", "true")):
            self.cmd("model.set_property", id=first, key=f"material_layer:{layer}:{key}", value=value)
        scenes = {s["id"]: s for s in self.project.scene_data()["objects"]}
        self.assertEqual(scenes[first]["layer_gradients"][0]["top"], "#EE1EF2")
        self.assertEqual(scenes[second]["layer_gradients"][0]["top"], "#123ABC")

    def test_save_open_preserves_custom_types_formulas_and_material_rules(self):
        wall = self.wall()
        self.cmd("model.set_property", id=wall, key="custom:Code", value={"type": "text", "value": "0003"})
        self.cmd("model.set_property", id=wall, key="thickness", value="=height / 10")
        self.cmd("model.set_property", id=wall, key="material_rule:condition", value="height > 1m")
        self.cmd("model.set_property", id=wall, key="material_rule:top", value="#ee1ef2")
        self.cmd("model.set_property", id=wall, key="material_rule:enabled", value="true")
        exchanged = BD.exchange(self.project, [wall])["elements"][0]
        self.assertEqual(exchanged["custom_property_types"]["Code"], "text")
        self.assertEqual(exchanged["parametric_formulas"]["thickness"], "height / 10")
        self.assertEqual(exchanged["conditional_material"]["top"], "#EE1EF2")
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "Properties.spadra"
            self.project.save(folder)
            reopened = Project.open(folder)
            meta = E.meta_of(reopened, wall)
            self.assertEqual(meta["custom:Code"], "0003")
            self.assertEqual(E.m(meta, "custom_types")["Code"], "text")
            self.assertEqual(E.m(meta, "formulas")["thickness"], "height / 10")
            self.assertEqual(E.appearance_color(reopened, meta, wall), "#EE1EF2")
            reopened.execute("model.set_property", {"id": wall, "key": "height", "value": 2})
            self.assertAlmostEqual(E.m(E.meta_of(reopened, wall), "thickness"), 0.2)

    def test_native_sgeo_roundtrip_retains_shared_scopes_and_gradient_definitions(self):
        wall = self.wall()
        self.cmd("model.set_property", id=wall, key="custom:FireClass", value={"scope": "type", "type": "text", "value": "EI60"})
        self.cmd("model.set_property", id=wall, key="material_def:Concrete:condition", value="height > 1m")
        self.cmd("model.set_property", id=wall, key="material_def:Concrete:mode", value="gradient")
        self.cmd("model.set_property", id=wall, key="material_def:Concrete:top", value="#123abc")
        self.cmd("model.set_property", id=wall, key="material_def:Concrete:enabled", value="true")
        raw, report = IO.export_model(self.project, "sgeo")
        self.assertEqual(report["status"], "complete")
        recipient = Project.create("Imported")
        recipient.execute("model.import", {"format": "sgeo", "data": base64.b64encode(raw).decode("ascii"), "name": "native.sgeo", "unit": "m"})
        imported = next(item["id"] for item in recipient.scene_data()["objects"] if item["category"] == "wall")
        self.assertEqual(RL.effective_custom(recipient, imported)["FireClass"]["value"], "EI60")
        self.assertEqual(next(o for o in recipient.scene_data()["objects"] if o["id"] == imported)["layer_gradients"][0]["top"], "#123ABC")


if __name__ == "__main__":
    unittest.main()
