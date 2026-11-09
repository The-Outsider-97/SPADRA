"""Named family types, typed parameters and formula assistance in SPADRA (P0-01/04/05): the door workflow end to end, through the real
SGEO engine, Project.execute, undo/redo, save and reopen."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from applications.SPADRA import families as FM
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest, Conflict, NotFound


class Doors(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project.create("Doors")                              # drawing unit mm
        self.level = self.p.manifest["levels"][0]["id"]
        self.fid = self.run_("family.define", name="Single Leaf Door", shape="box", parameters={
            "width": {"default": 900, "min": 600, "max": 1800, "scope": "type"},
            "height": {"default": 2100, "min": 1800, "max": 2600, "scope": "type"},
            "thickness": {"default": 40, "min": 20, "max": 100},
            "frame": {"default": 35},
            "depth": {"expression": "thickness"},
            "clear_width": {"type": "length", "expression": "width - 2 * frame"},
            "swing": {"type": "angle", "default": 90, "min": 0, "max": 180},
            "leaf_mass": {"type": "mass", "default": 25},
            "closer_force": {"type": "force", "default": 0.05},
            "finish": {"type": "enumeration", "choices": ["paint", "stain", "veneer"], "default": "paint"}})["id"]
        for name, w, h in (("D-800", 800, 2100), ("D-900", 900, 2100), ("D-1000", 1000, 2300)):
            self.run_("family.type_define", id=self.fid, name=name, values={"width": w, "height": h})

    def run_(self, op, **params):
        return self.p.execute(op, params)["result"]

    def place(self, x=0, **extra):
        return self.run_("family.place", family=self.fid, level=self.level, point=[x, 0], **extra)["id"]

    def size_mm(self, oid):
        return [round(v * 1000, 6) for v in self.p.model.get(oid)["geometry"]["size"]]

    def info(self):
        return FM.describe(self.p, FM.family(self.p, self.fid))

    # ---------------------------------------------------------------------------------------------------- the report's door
    def test_the_door_workflow(self):
        a, b, c = self.place(0, type="D-800"), self.place(2000, type="D-900"), self.place(4000, type="D-1000")
        self.assertEqual([self.size_mm(i) for i in (a, b, c)], [[800, 40, 2100], [900, 40, 2100], [1000, 40, 2300]])
        self.assertAlmostEqual(FM.values(self.p, c)["clear_width"], 0.93)                           # the derived clear width: 1000 - 2 * 35
        self.run_("model.set_property", id=b, key="param:thickness", value=60)                       # a permitted instance override
        self.assertEqual(self.size_mm(b), [900, 60, 2100])
        self.run_("family.type_update", id=self.fid, name="D-900", values={"width": 950})           # editing a type moves its elements
        self.assertEqual(self.size_mm(b), [950, 60, 2100])
        self.assertEqual(self.size_mm(a), [800, 40, 2100])
        types = {t["name"]: t for t in self.info()["types"]}
        self.assertEqual(types["D-900"]["instances"], 1) and self.assertEqual(types["D-900"]["values"]["width"], 950)
        # reset the override, then undo and redo the type edit
        self.assertEqual(self.run_("family.reset_overrides", id=b)["reset"], ["thickness"])
        self.assertEqual(self.size_mm(b), [950, 40, 2100])
        self.p.undo()
        self.assertEqual(self.size_mm(b), [950, 60, 2100])
        self.p.undo()
        self.assertEqual(self.size_mm(b), [900, 60, 2100])
        self.p.redo()
        self.p.redo()
        self.assertEqual(self.size_mm(b), [950, 40, 2100])

    def test_save_and_reopen_keep_types_assignments_and_overrides(self):
        a = self.place(0, type="D-1000", parameters={"thickness": 55})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "doors.spadra"
            self.p.save(path)
            q = Project.open(path)
        info = FM.describe(q, FM.family(q, self.fid))
        self.assertEqual([t["name"] for t in info["types"]], ["D-800", "D-900", "D-1000"])           # natural order, before and after a save
        self.assertEqual(q.model.get(a)["family_type"], "D-1000")
        self.assertEqual([round(v * 1000, 6) for v in q.model.get(a)["geometry"]["size"]], [1000, 55, 2300])

    # -------------------------------------------------------------------------------------------------------- rules and refusals
    def test_scope_formulas_and_bounds_protect_the_type(self):
        a = self.place(0, type="D-800")
        rev = self.p.revision
        for bad in ({"key": "param:width", "value": 700}, {"key": "param:thickness", "value": 5}):
            with self.assertRaises(BadRequest):
                self.run_("model.set_property", id=a, **bad)
        with self.assertRaisesRegex(BadRequest, "type parameter"):
            self.run_("family.place", family=self.fid, level=self.level, point=[0, 0], parameters={"width": 700})
        with self.assertRaisesRegex(BadRequest, "follows a formula"):
            self.run_("family.type_define", id=self.fid, name="X", values={"clear_width": 500})
        with self.assertRaises(BadRequest):
            self.run_("family.type_define", id=self.fid, name="Too wide", values={"width": 5000})
        with self.assertRaises(Conflict):
            self.run_("family.type_define", id=self.fid, name="d-800")                              # names are not case-sensitive
        self.assertEqual(self.p.revision, rev)                                                       # every refusal left the project untouched

    def test_a_type_in_use_cannot_be_deleted_and_an_unused_one_can(self):
        self.place(0, type="D-800")
        with self.assertRaisesRegex(Conflict, "1 element uses the type 'D-800'"):
            self.run_("family.type_delete", id=self.fid, name="D-800")
        self.run_("family.type_delete", id=self.fid, name="D-1000")
        self.assertEqual([t["name"] for t in self.info()["types"]], ["D-800", "D-900"])
        with self.assertRaises(NotFound):
            self.place(0, type="D-1000")

    def test_renaming_a_type_follows_its_elements_and_undo_restores_the_name(self):
        a = self.place(0, type="D-800")
        self.run_("family.type_rename", id=self.fid, name="D-800", new_name="D-0800")
        self.assertEqual(self.p.model.get(a)["family_type"], "D-0800")
        self.p.undo()
        self.assertEqual(self.p.model.get(a)["family_type"], "D-800")
        self.assertEqual([t["name"] for t in self.info()["types"]], ["D-800", "D-900", "D-1000"])

    def test_duplicate_assign_and_return_to_family_defaults(self):
        out = self.run_("family.type_duplicate", id=self.fid, name="D-900", new_name="D-900 FD30")
        self.run_("family.type_update", id=self.fid, name="D-900 FD30", values={"thickness": None, "width": 920})
        a = self.place(0, type="D-900")
        self.run_("family.assign_type", id=a, type=out["type"])
        self.assertEqual(self.size_mm(a)[0], 920)
        self.run_("family.assign_type", id=a, type="")
        self.assertEqual(self.size_mm(a), [900, 40, 2100])                                           # the family defaults
        self.assertNotIn("family_type", self.p.model.get(a))
        with self.assertRaises(NotFound):
            self.run_("family.assign_type", id=a, type="Ghost")

    def test_a_failed_type_edit_rolls_back_every_instance(self):
        ids = [self.place(i * 1000, type="D-900") for i in range(3)]
        before = [self.size_mm(i) for i in ids]
        rev = self.p.revision
        with self.assertRaises(BadRequest):
            self.run_("family.type_update", id=self.fid, name="D-900", values={"width": 400})
        self.assertEqual([self.size_mm(i) for i in ids], before)
        self.assertEqual(self.p.revision, rev)

    def test_fault_injected_into_regeneration_leaves_model_and_history_untouched(self):
        """P0-03: a failure after some elements were already re-placed must not leave a partial update, a revision bump or an undo entry."""
        ids = [self.place(i * 1000, type="D-900") for i in range(4)]
        before = {i: self.p.model.get(i) for i in ids}
        rev, depth = self.p.revision, len(self.p.undo_stack)
        calls = []
        original = FM._placement

        def flaky(project, oid):
            calls.append(oid)
            if len(calls) == 3:
                raise RuntimeError("injected failure")
            return original(project, oid)

        FM._placement = flaky
        try:
            with self.assertRaises(RuntimeError):
                self.run_("family.type_update", id=self.fid, name="D-900", values={"width": 1100})
        finally:
            FM._placement = original
        self.assertEqual(len(calls), 3)
        self.assertEqual({i: self.p.model.get(i) for i in ids}, before)
        self.assertEqual((self.p.revision, len(self.p.undo_stack)), (rev, depth))
        self.assertEqual(FM.family(self.p, self.fid)["types"]["D-900"], {"width": 0.9, "height": 2.1})

    def test_a_fault_in_a_later_batch_undoes_the_batches_already_applied(self):
        """Placements are applied in batches of at most FM.BATCH; the second batch failing must leave the first one's work undone too."""
        ids = [self.place(i * 1000, type="D-900") for i in range(5)]
        before = {i: self.p.model.get(i) for i in ids}
        rev, depth = self.p.revision, len(self.p.undo_stack)
        real_apply, calls, old_batch = self.p.model.apply, [], FM.BATCH

        def flaky(operations, **kw):
            calls.append(len(operations))
            if len(calls) == 3 and operations and operations[0]["op"] == "transform":
                raise RuntimeError("injected failure in batch")
            return real_apply(operations, **kw)

        FM.BATCH = 2
        self.p.model.apply = flaky
        try:
            with self.assertRaises(Exception):
                self.run_("family.type_update", id=self.fid, name="D-900", values={"width": 1100})
        finally:
            self.p.model.apply = real_apply
            FM.BATCH = old_batch
        self.assertEqual({i: self.p.model.get(i) for i in ids}, before)
        self.assertEqual((self.p.revision, len(self.p.undo_stack)), (rev, depth))

    def test_a_type_update_re_places_every_instance_in_batches(self):
        ids = [self.place(i * 1000, type="D-900") for i in range(5)]
        old = FM.BATCH
        FM.BATCH = 2
        try:
            self.run_("family.type_update", id=self.fid, name="D-900", values={"width": 1100})
        finally:
            FM.BATCH = old
        self.assertEqual([self.size_mm(i) for i in ids], [[1100, 40, 2100]] * 5)
        self.p.undo()
        self.assertEqual([self.size_mm(i) for i in ids], [[900, 40, 2100]] * 5)

    def test_family_update_cannot_strand_a_type_value(self):
        self.place(0, type="D-1000")
        with self.assertRaises(BadRequest):
            self.run_("family.update", id=self.fid, parameters={"height": {"max": 2200}})           # D-1000 fixes 2300, above the new bound
        self.assertEqual(FM.family(self.p, self.fid)["parameters"]["height"]["max"], 2.6)

    # ----------------------------------------------------------------------------------------------------------- the table
    def test_the_parameter_table_describes_scope_units_dependencies_and_types(self):
        info = self.info()
        rows = {r["name"]: r for r in info["parameters"]}
        self.assertEqual(rows["width"]["scope"], "type")
        self.assertEqual(rows["thickness"]["scope"], "instance")
        self.assertEqual(rows["clear_width"]["scope"], "derived")
        self.assertTrue(rows["clear_width"]["read_only"])
        self.assertEqual(rows["clear_width"]["depends_on"], ["frame", "width"])
        self.assertEqual(rows["width"]["dependents"], ["clear_width"])
        self.assertEqual((rows["leaf_mass"]["unit"], rows["leaf_mass"]["default"]), ("kg", 25))
        self.assertEqual((rows["closer_force"]["unit"], rows["closer_force"]["default"]), ("kN", 0.05))
        self.assertEqual((rows["swing"]["unit"], rows["swing"]["default"]), ("°", 90))
        self.assertEqual(rows["finish"]["choices"], ["paint", "stain", "veneer"])
        self.assertIn(["clear_width", "width"], info["graph"]["edges"])
        sg = FM.family(self.p, self.fid)["parameters"]
        self.assertEqual(sg["closer_force"]["default"], 50.0)                                        # stored in newtons
        self.assertAlmostEqual(sg["swing"]["default"], 1.5707963267948966)

    def test_the_properties_panel_shows_type_scope_and_derived_rows(self):
        a = self.place(0, type="D-900", parameters={"thickness": 50})
        out = self.p.properties({"model": a}, relations=True)
        rows = {r.get("key") or r["label"]: r for r in out["top"]}
        self.assertEqual(rows["family_type"]["value"], "D-900")
        self.assertEqual([c["value"] for c in rows["family_type"]["choices"]], ["", "D-800", "D-900", "D-1000"])
        self.assertEqual(rows["param:thickness"]["label"], "thickness")                            # overridden here
        self.assertIn("param:frame", rows)
        self.assertEqual(rows["Width (type D-900)"]["type"], "readonly")                            # a type parameter cannot be edited on the element
        self.assertTrue(any(k == "clear_width (formula)" for k in rows))
        self.run_("model.set_property", id=a, key="family_type", value="D-1000")
        self.assertEqual(self.size_mm(a)[0], 1000)

    def test_enumeration_and_vector_parameters_validate(self):
        a = self.place(0)
        self.run_("model.set_property", id=a, key="param:finish", value="stain")
        self.assertEqual(FM.values(self.p, a)["finish"], "stain")
        with self.assertRaises(BadRequest):
            self.run_("model.set_property", id=a, key="param:finish", value="chrome")
        fid = self.run_("family.define", name="Marker", shape="cylinder", parameters={
            "radius": {"default": 50}, "height": {"default": 100},
            "origin": {"type": "coordinate", "default": [1000, 2000, 0]}})["id"]
        self.assertEqual(FM.family(self.p, fid)["parameters"]["origin"]["default"], [1.0, 2.0, 0.0])
        self.assertEqual(next(r for r in FM.describe(self.p, FM.family(self.p, fid))["parameters"] if r["name"] == "origin")["default"], [1000.0, 2000.0, 0.0])

    # ------------------------------------------------------------------------------------------------------ formula assistance
    def test_formula_check_previews_in_the_users_units_and_names_the_problem(self):
        ok = FM.check_formula(self.p, {"family": self.fid, "parameter": "clear_width", "expression": "width - 2 * frame * 2"})
        self.assertTrue(ok["ok"]) and self.assertAlmostEqual(ok["value"], 760.0) and self.assertEqual(ok["unit"], "mm")
        self.assertEqual(ok["dependencies"], ["frame", "width"])
        bad = FM.check_formula(self.p, {"family": self.fid, "parameter": "clear_width", "expression": "width * frame"})
        self.assertFalse(bad["ok"]) and self.assertEqual((bad["error"]["expected"], bad["error"]["actual"]), ("length", "area"))
        cyc = FM.check_formula(self.p, {"family": self.fid, "parameter": "width", "expression": "clear_width + length(1)", "type": "length"})
        self.assertFalse(cyc["ok"]) and self.assertEqual(set(cyc["error"]["cycle"]), {"width", "clear_width"})
        new = FM.check_formula(self.p, {"family": self.fid, "parameter": "leaf_area", "type": "area", "expression": "width * height"})
        self.assertTrue(new["ok"]) and self.assertAlmostEqual(new["value"], 0.9 * 2.1) and self.assertEqual(new["unit"], "m2")
        with self.assertRaises(BadRequest):
            FM.check_formula(self.p, {"family": self.fid, "parameter": "x", "type": "string", "expression": "1"})
        self.assertNotIn("leaf_area", FM.family(self.p, self.fid)["parameters"])                    # the check changed nothing
        self.assertTrue(any(f["name"] == "atan2" for f in FM.functions()))

    def test_a_cyclic_formula_edit_is_rejected_with_the_path_and_the_family_survives(self):
        rev = self.p.revision
        with self.assertRaisesRegex(BadRequest, "cyclic parameter dependencies"):
            self.run_("family.update", id=self.fid, parameters={"frame": {"expression": "clear_width / 10"}})
        self.assertEqual(self.p.revision, rev)
        self.assertEqual(FM.family(self.p, self.fid)["parameters"]["frame"]["default"], 0.035)

    # ------------------------------------------------------------------------------------------------------------ file exchange
    def test_a_family_file_carries_its_types(self):
        from src.utils.sgeo import SGEODocument
        data = FM.export_family_file(self.p, self.fid)
        doc = SGEODocument.from_bytes(data)
        self.assertEqual(set(doc.families()[0]["types"]), {"D-800", "D-900", "D-1000"})
        q = Project.create("Other")
        ids = FM.import_family_file(q, doc)
        self.assertEqual(set(FM.family(q, ids[0]["id"])["types"]), {"D-800", "D-900", "D-1000"})


if __name__ == "__main__":
    unittest.main()
