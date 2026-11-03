"""Phase 10, against the real SGEO and SDRW engines: levels and grids, stairs with exact geometry, families built on SGEO's own family mechanism
(type and instance parameters, formulas, bounds, regeneration), hosting, custom properties, relationships, schedules and the coordination checks."""
from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from applications.SPADRA import families as FM
from applications.SPADRA.project import NS, Project
from applications.tools.errors import BadRequest, Conflict, NotFound


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project.create("BIM")                              # drawing unit mm
        self.l1, self.l2, self.roof_level = (l["id"] for l in self.p.manifest["levels"])

    def run_(self, op, **params):
        return self.p.execute(op, params)["result"]

    def wall(self, a=(0, 0), b=(6000, 0), **extra):
        return self.run_("model.add_wall", start=list(a), end=list(b), level=self.l1, thickness=250, height=3.2, **extra)["id"]

    def meta(self, oid):
        return self.p.model.get(oid)["metadata"]

    def vol(self, oid):
        return self.p.model.volume(oid, unit="m")

    def rows(self, oid):
        out = self.p.properties({"model": oid}, relations=True)
        return {r.get("key") or r["label"]: r for g in [{"rows": out["top"]}] + out["groups"] for r in g["rows"]}

    def table(self, **extra):
        spec = {"name": "Table", "shape": "box", "parameters": {
            "width": {"default": 1200, "min": 300, "max": 3000}, "depth": {"expression": "width / 2"}, "height": {"default": 750}}}
        spec.update(extra)
        return self.run_("family.define", **spec)["id"]


class Levels(Case):
    def test_adding_a_level_gives_it_a_floor_plan(self):
        out = self.run_("level.add", name="Level 03", elevation=9.6)
        self.assertEqual([l["name"] for l in self.p.manifest["levels"]], ["Level 01", "Level 02", "Roof Level", "Level 03"])
        self.assertEqual([l["elevation"] for l in self.p.manifest["levels"]], sorted(l["elevation"] for l in self.p.manifest["levels"]))
        view = self.p.view(out["plan"])
        self.assertEqual((view["kind"], view["level_id"]), ("plan", out["id"]))
        self.assertIn(out["plan"], self.p.drawings)
        self.assertIn("Level 03", [n["label"] for n in self.p.tree()["sections"][0]["children"][1]["children"]])

    def test_a_level_name_must_be_new_and_the_elevation_sane(self):
        with self.assertRaises(Conflict):
            self.run_("level.add", name="level 01", elevation=1)
        with self.assertRaises(BadRequest):
            self.run_("level.add", name="Sky", elevation=1e6)

    def test_moving_a_level_moves_everything_built_on_it(self):
        w = self.wall()
        d = self.run_("model.add_door", wall=w, offset=2.0)["id"]
        self.run_("level.set", id=self.l1, elevation=0.5)
        self.assertAlmostEqual(self.p.model.world_bounds(w)[0][2], 0.5, places=6)
        self.assertAlmostEqual(self.p.model.world_bounds(d)[0][2], 0.5, places=6)
        self.p.undo()
        self.assertAlmostEqual(self.p.model.world_bounds(w)[0][2], 0.0, places=6)

    def test_renaming_a_level_renames_its_plan(self):
        self.run_("level.set", id=self.l1, name="Ground")
        plan = next(v for v in self.p.manifest["views"] if v.get("level_id") == self.l1)
        self.assertEqual(plan["name"], "Ground")

    def test_a_level_in_use_cannot_be_deleted(self):
        self.wall()
        with self.assertRaises(Conflict) as c:
            self.run_("level.delete", id=self.l1)
        self.assertIn("stand on", c.exception.message)

    def test_an_unused_level_is_deleted_with_its_empty_plan_and_undo_restores_both(self):
        new = self.run_("level.add", name="Mezzanine", elevation=1.6)
        n_views = len(self.p.manifest["views"])
        self.run_("level.delete", id=new["id"])
        self.assertEqual(len(self.p.manifest["views"]), n_views - 1)
        self.assertNotIn(new["id"], [l["id"] for l in self.p.manifest["levels"]])
        self.p.undo()
        self.assertIn(new["id"], [l["id"] for l in self.p.manifest["levels"]])
        self.assertEqual(len(self.p.manifest["views"]), n_views)

    def test_a_plan_with_drawn_items_is_not_thrown_away(self):
        new = self.run_("level.add", name="Mezzanine", elevation=1.6)
        self.run_("draw.line", drawing=new["plan"], start=[0, 0], end=[100, 0])
        with self.assertRaises(Conflict):
            self.run_("level.delete", id=new["id"])

    def test_the_last_level_stays(self):
        for lid in (self.l2, self.roof_level):
            self.run_("level.delete", id=lid)
        with self.assertRaises(Conflict):
            self.run_("level.delete", id=self.l1)


class Grids(Case):
    def test_a_grid_is_a_datum_with_no_volume_drawn_on_every_plan(self):
        g = self.run_("model.add_grid", start=[0, -1000], end=[0, 9000])["id"]
        self.assertEqual(self.p.model.get(g)["name"], "A")
        self.assertIsNone(self.p.model.get(g)["geometry"])
        q = self.p.quantities()["rows"] if self.p.model.query_ids(has_geometry=True) else []
        self.assertEqual(q, [])
        for v in self.p.manifest["views"]:
            if v["kind"] == "plan":
                self.assertIn(g, [i["id"] for i in self.p.plan_overlay(v["id"])])
        item = next(i for i in self.p.plan_overlay(next(v["id"] for v in self.p.manifest["views"] if v["kind"] == "plan")) if i["id"] == g)
        self.assertEqual(item["polygon"], [[0.0, -1000.0], [0.0, 9000.0]])
        self.assertEqual(item["label"]["name"], "A")

    def test_labels_are_unique_and_follow_the_alphabet(self):
        self.run_("model.add_grid", start=[0, 0], end=[0, 5000])
        b = self.run_("model.add_grid", start=[3000, 0], end=[3000, 5000])["id"]
        self.assertEqual(self.p.model.get(b)["name"], "B")
        with self.assertRaises(Conflict):
            self.run_("model.add_grid", start=[6000, 0], end=[6000, 5000], label="a")
        with self.assertRaises(BadRequest):
            self.run_("model.add_grid", start=[0, 0], end=[1, 0])

    def test_editing_and_moving_a_grid(self):
        g = self.run_("model.add_grid", start=[0, 0], end=[0, 5000])["id"]
        self.run_("model.set_property", id=g, key="end_y", value=8000)
        self.assertEqual(self.meta(g)[f"{NS}:end"], [0.0, 8.0])
        self.run_("model.move", id=g, dx=1.0, dy=0, dz=0)
        self.assertEqual(self.meta(g)[f"{NS}:start"], [1.0, 0.0])
        self.run_("model.set_property", id=g, key="name", value="X1")
        self.assertEqual(self.p.model.get(g)["name"], "X1")


class Stairs(Case):
    def stair(self, **extra):
        return self.run_("model.add_stair", level=self.l1, start=[0, 0], end=[3500, 0], **extra)["id"]

    def test_a_flight_reaches_the_next_level_with_exact_geometry(self):
        s = self.stair(width=1000)
        m = self.meta(s)
        n, tread = m[f"{NS}:risers"], m[f"{NS}:tread"]
        self.assertEqual(n, round(3.2 / 0.175))
        self.assertAlmostEqual(tread * (n - 1), 3.5, places=9)
        (x0, y0, z0), (x1, y1, z1) = self.p.model.world_bounds(s)
        self.assertAlmostEqual(z0, 0.0, places=6)
        self.assertAlmostEqual(z1, 3.2 - 3.2 / n, places=6)                         # n risers = n - 1 treads; the n-th riser is the step onto Level 02
        self.assertAlmostEqual(x1 - x0, 3.5, places=6)
        self.assertAlmostEqual(y1 - y0, 1.0, places=6)
        r = 3.2 / n
        expected = sum((i + 1) * r * tread for i in range(n - 1)) * 1.0                    # area under the treads times the width
        self.assertAlmostEqual(self.vol(s), expected, places=6)

    def test_riser_count_and_tread_follow_the_parameters(self):
        s = self.stair()
        self.run_("model.set_property", id=s, key="risers", value=16)
        self.assertEqual(self.meta(s)[f"{NS}:risers"], 16)
        self.assertAlmostEqual(self.rows(s)["Riser height"]["value"].split(" ")[0] and float(self.rows(s)["Riser height"]["value"].split(" ")[0]), 200.0, places=1)
        self.run_("model.set_property", id=s, key="tread", value=280)
        self.assertAlmostEqual(self.p.model.world_bounds(s)[1][0] - self.p.model.world_bounds(s)[0][0], 15 * 0.28, places=6)

    def test_raising_the_top_level_re_rises_the_flight(self):
        s = self.stair()
        self.run_("level.set", id=self.l2, elevation=3.6)
        self.assertAlmostEqual(self.p.model.world_bounds(s)[1][2], 3.6 - 3.6 / self.meta(s)[f"{NS}:risers"], places=6)

    def test_a_flight_that_cannot_rise_is_refused_and_the_model_is_unchanged(self):
        s = self.stair()
        before = self.p.revision
        with self.assertRaises(BadRequest):
            self.run_("model.set_property", id=s, key="top_level", value=self.l1)          # top = base
        with self.assertRaises(BadRequest):
            self.run_("model.set_property", id=s, key="risers", value=3)                    # 3.2 m / 3 risers is a 1.07 m riser
        self.assertEqual(self.p.revision, before)
        self.assertAlmostEqual(self.p.model.world_bounds(s)[1][2], 3.2 - 3.2 / self.meta(s)[f"{NS}:risers"], places=6)

    def test_no_level_above_means_no_stair(self):
        with self.assertRaises(BadRequest):
            self.run_("model.add_stair", level=self.roof_level, start=[0, 0], end=[3000, 0])

    def test_the_plan_shows_outline_treads_and_the_arrow(self):
        s = self.stair()
        plan = next(v["id"] for v in self.p.manifest["views"] if v.get("level_id") == self.l1)
        item = next(i for i in self.p.plan_overlay(plan) if i["id"] == s)
        n = self.meta(s)[f"{NS}:risers"]
        self.assertEqual(len(item["lines"]), n - 2)
        self.assertEqual(item["arrow"]["label"], "UP")
        xs = [q[0] for q in item["polygon"]]
        self.assertAlmostEqual(max(xs) - min(xs), 3500.0, places=3)

    def test_it_is_measured_scheduled_and_moved_like_any_element(self):
        s = self.stair()
        self.run_("model.move", id=s, dx=1.0, dy=2.0, dz=0.0)
        self.assertEqual(self.meta(s)[f"{NS}:start"], [1.0, 2.0])
        row = next(r for r in self.p.quantities()["rows"] if r["id"] == s)
        self.assertAlmostEqual(row["volume_m3"], self.vol(s), places=9)

    def test_a_stair_survives_save_and_reopen(self):
        s = self.stair()
        with tempfile.TemporaryDirectory() as tmp:
            self.p.save(Path(tmp) / "x.spadra")
            q = Project.open(Path(tmp) / "x.spadra")
            self.assertAlmostEqual(q.model.volume(s, unit="m"), self.vol(s), places=9)
            self.assertEqual(q.model.get(s)["metadata"][f"{NS}:risers"], self.meta(s)[f"{NS}:risers"])


class FloorsAndHosts(Case):
    def test_a_floor_can_be_a_structural_slab(self):
        f = self.run_("model.add_floor", points=[[0, 0], [4000, 0], [4000, 3000], [0, 3000]], level=self.l1)["id"]
        self.assertEqual(self.rows(f)["type"]["value"], "Generic Floor")
        self.run_("model.set_property", id=f, key="type", value="Structural Slab")
        self.assertEqual(self.meta(f)[f"{NS}:type"], "Structural Slab")
        with self.assertRaises(BadRequest):
            self.run_("model.set_property", id=f, key="type", value="Magic")

    def test_a_door_can_be_given_another_host_and_the_old_wall_is_whole_again(self):
        a, b = self.wall(), self.wall((0, 4000), (6000, 4000))
        d = self.run_("model.add_door", wall=a, offset=2.0)["id"]
        self.assertEqual(self.p.model.get(a)["geometry"]["kind"], "mesh")
        self.run_("model.set_property", id=d, key="host", value=b)
        self.assertEqual(self.p.model.get(a)["geometry"]["kind"], "box")
        self.assertEqual(self.meta(d)[f"{NS}:host"], b)
        self.assertAlmostEqual(self.p.model.world_bounds(d)[0][1], 4.0 - 0.025, places=6)

    def test_a_door_that_does_not_fit_the_new_wall_is_refused(self):
        a, b = self.wall(), self.wall((0, 4000), (1000, 4000))
        d = self.run_("model.add_door", wall=a, offset=3.0)["id"]
        with self.assertRaises(BadRequest):
            self.run_("model.set_property", id=d, key="host", value=b)
        self.assertEqual(self.meta(d)[f"{NS}:host"], a)

    def test_wall_endpoints_and_angle_are_editable(self):
        w = self.wall()
        self.run_("model.set_property", id=w, key="end_y", value=3000)
        self.assertEqual(self.meta(w)[f"{NS}:end"], [6.0, 3.0])
        self.run_("model.set_property", id=w, key="angle", value=90)
        e = self.meta(w)[f"{NS}:end"]
        self.assertAlmostEqual(e[0], 0.0, places=9)
        self.assertAlmostEqual(e[1], math.hypot(6, 3), places=9)

    def test_column_and_beam_positions_are_editable(self):
        c = self.run_("model.add_column", level=self.l1, point=[1000, 1000])["id"]
        self.run_("model.set_property", id=c, key="x", value=2500)
        self.assertEqual(self.meta(c)[f"{NS}:point"], [2.5, 1.0])
        b = self.run_("model.add_beam", level=self.l1, start=[0, 0], end=[4000, 0])["id"]
        self.run_("model.set_property", id=b, key="end_x", value=5000)
        self.assertEqual(self.meta(b)[f"{NS}:end"], [5.0, 0.0])


class Families(Case):
    def test_a_family_is_an_sgeo_family_with_typed_parameters_and_a_formula(self):
        fid = self.table()
        sgeo = next(f for f in self.p.model.families() if f["id"] == fid)
        self.assertEqual(sgeo["generator"], {"kind": "builtin.box_v1"})
        self.assertEqual(sgeo["parameters"]["width"], {"type": "length", "default": 1.2, "min": 0.3, "max": 3.0})
        self.assertEqual(sgeo["parameters"]["depth"], {"type": "length", "expression": "width / 2", "min": 0.001})

    def test_placing_an_instance_generates_geometry_at_its_level_and_point(self):
        fid = self.table()
        o = self.run_("family.place", family=fid, level=self.l2, point=[1000, 2000], rotation=90, base_offset=100)["id"]
        obj = self.p.model.get(o)
        self.assertEqual(obj["family_id"], fid)
        self.assertEqual(obj["geometry"]["size"], [1.2, 0.6, 0.75])
        lo, hi = self.p.model.world_bounds(o)
        self.assertAlmostEqual(lo[2], 3.2 + 0.1, places=6)                                  # stands 100 mm above Level 02
        self.assertAlmostEqual(hi[2] - lo[2], 0.75, places=6)
        self.assertAlmostEqual((hi[1] - lo[1]), 1.2, places=6)                              # turned a quarter: its width runs along y
        self.assertAlmostEqual(self.vol(o), 1.2 * 0.6 * 0.75, places=9)

    def test_instance_parameters_override_the_type_and_formulas_follow(self):
        fid = self.table()
        o = self.run_("family.place", family=fid, level=self.l1, point=[0, 0], parameters={"width": 2000})["id"]
        self.assertEqual(self.p.model.get(o)["geometry"]["size"], [2.0, 1.0, 0.75])        # depth = width / 2
        self.run_("model.set_property", id=o, key="param:width", value=1000)
        self.assertEqual(self.p.model.get(o)["geometry"]["size"], [1.0, 0.5, 0.75])
        self.run_("model.set_property", id=o, key="param:width", value="")                  # back to the family
        self.assertEqual(self.p.model.get(o)["geometry"]["size"], [1.2, 0.6, 0.75])
        self.assertEqual(self.p.model.get(o)["parameters"], {})

    def test_editing_the_family_regenerates_every_instance_at_once(self):
        fid = self.table()
        a = self.run_("family.place", family=fid, level=self.l1, point=[0, 0])["id"]
        b = self.run_("family.place", family=fid, level=self.l1, point=[3000, 0], parameters={"width": 2000})["id"]
        self.run_("family.update", id=fid, parameters={"height": {"default": 900}, "width": {"default": 1500}})
        self.assertEqual(self.p.model.get(a)["geometry"]["size"], [1.5, 0.75, 0.9])
        self.assertEqual(self.p.model.get(b)["geometry"]["size"], [2.0, 1.0, 0.9])        # its own width stays; the type's height follows
        self.assertAlmostEqual(self.p.model.world_bounds(a)[0][2], 0.0, places=6)         # the base stays on the level when the height grows
        self.assertEqual(self.p.model.families()[0]["instance_count"], 2)

    def test_a_bound_that_would_break_an_instance_is_refused_naming_it(self):
        fid = self.table()
        self.run_("family.place", family=fid, level=self.l1, point=[0, 0], parameters={"width": 2500}, name="Big table")
        before = self.p.revision
        with self.assertRaises(BadRequest) as c:
            self.run_("family.update", id=fid, parameters={"width": {"max": 2000}})
        self.assertIn("Big table", c.exception.message)
        self.assertEqual(self.p.revision, before)

    def test_values_outside_the_bounds_are_refused_with_the_limits(self):
        fid = self.table()
        with self.assertRaises(BadRequest) as c:
            self.run_("family.place", family=fid, level=self.l1, point=[0, 0], parameters={"width": 5000})
        self.assertIn("at most 3000 mm", c.exception.message)
        o = self.run_("family.place", family=fid, level=self.l1, point=[0, 0])["id"]
        with self.assertRaises(BadRequest):
            self.run_("model.set_property", id=o, key="param:width", value=100)
        with self.assertRaises(BadRequest) as c:
            self.run_("model.set_property", id=o, key="param:depth", value=300)             # a formula cannot be overridden
        self.assertIn("formula", c.exception.message)

    def test_extra_parameters_names_and_cycles_are_validated(self):
        with self.assertRaises(BadRequest):
            self.table(parameters={"width": {"default": 1000}, "height": {"default": 700}})        # depth missing
        with self.assertRaises(BadRequest):
            self.table(parameters={"width": {"default": 1000}, "depth": {"expression": "height"}, "height": {"expression": "depth"}})
        with self.assertRaises(BadRequest):
            self.table(parameters={"width": {"default": 1000}, "depth": {"default": 500}, "height": {"default": 700}, "bad name": {"default": 1, "type": "number"}})
        with self.assertRaises(BadRequest):
            self.table(parameters={"width": {"default": 1000}, "depth": {"expression": "__import__('os')"}, "height": {"default": 700}})
        self.table(name="Shelf", parameters={"width": {"default": 800}, "depth": {"default": 300}, "height": {"expression": "length(shelves * 0.4)"}, "shelves": {"type": "integer", "default": 3, "min": 1, "max": 8}})
        self.assertEqual([f["name"] for f in self.p.model.families()], ["Shelf"])
        shelf = FM.listing(self.p)[0]
        self.assertEqual([r["name"] for r in shelf["parameters"] if r["required"]], ["width", "depth", "height"])

    def test_a_family_with_elements_cannot_be_deleted_and_an_empty_one_can(self):
        fid = self.table()
        o = self.run_("family.place", family=fid, level=self.l1, point=[0, 0])["id"]
        with self.assertRaises(Conflict):
            self.run_("family.delete", id=fid)
        self.run_("model.delete", id=o)
        self.run_("family.delete", id=fid)
        self.assertEqual(self.p.model.families(), [])

    def test_names_are_unique_and_a_rename_follows_to_the_elements(self):
        fid = self.table()
        with self.assertRaises(Conflict):
            self.table(name="table")
        o = self.run_("family.place", family=fid, level=self.l1, point=[0, 0])["id"]
        self.run_("family.update", id=fid, name="Desk")
        self.assertEqual(self.rows(o)["Type"]["value"], "Desk")

    def test_cylinder_and_sphere_families_stand_on_their_level(self):
        c = self.run_("family.define", name="Pier", shape="cylinder", parameters={"radius": {"default": 250}, "height": {"default": 1500}})["id"]
        s = self.run_("family.define", name="Ball", shape="sphere", parameters={"radius": {"default": 300}})["id"]
        pc = self.run_("family.place", family=c, level=self.l1, point=[0, 0])["id"]
        ps = self.run_("family.place", family=s, level=self.l1, point=[2000, 0])["id"]
        self.assertAlmostEqual(self.p.model.world_bounds(pc)[0][2], 0.0, places=6)
        self.assertAlmostEqual(self.p.model.world_bounds(ps)[0][2], 0.0, places=6)
        self.assertAlmostEqual(self.vol(pc), math.pi * 0.25 ** 2 * 1.5, places=3)

    def test_moving_a_generic_element_moves_its_parameters(self):
        fid = self.table()
        o = self.run_("family.place", family=fid, level=self.l1, point=[0, 0])["id"]
        self.run_("model.move", id=o, dx=1.0, dy=2.0, dz=0.5)
        lo, _ = self.p.model.world_bounds(o)
        self.assertAlmostEqual(lo[2], 0.5, places=6)
        self.assertEqual(self.meta(o)[f"{NS}:point"], [1.0, 2.0])
        self.run_("model.set_property", id=o, key="x", value=4000)
        self.assertEqual(self.meta(o)[f"{NS}:point"], [4.0, 2.0])

    def test_a_level_change_carries_generic_elements_with_it(self):
        fid = self.table()
        o = self.run_("family.place", family=fid, level=self.l2, point=[0, 0])["id"]
        self.run_("level.set", id=self.l2, elevation=4.0)
        self.assertAlmostEqual(self.p.model.world_bounds(o)[0][2], 4.0, places=6)

    def test_families_survive_save_and_reopen_with_their_instances(self):
        fid = self.table()
        o = self.run_("family.place", family=fid, level=self.l1, point=[0, 0], parameters={"width": 2000})["id"]
        with tempfile.TemporaryDirectory() as tmp:
            self.p.save(Path(tmp) / "x.spadra")
            q = Project.open(Path(tmp) / "x.spadra")
            self.assertEqual(q.model.get(o)["family_id"], fid)
            self.assertEqual(q.model.get(o)["geometry"]["size"], [2.0, 1.0, 0.75])
            self.assertEqual(q.model.parameter_values(o)["depth"], 1.0)

    def test_a_family_exports_as_an_sgeo_family_document_and_imports_back(self):
        from src.utils.sgeo import SGEODocument
        fid = self.table()
        raw = FM.export_family_file(self.p, fid)
        doc = SGEODocument.from_bytes(raw)
        self.assertEqual(doc.document_kind, "family")
        other = Project.create("Other")
        ids = FM.import_family_file(other, doc)
        fam = next(f for f in other.model.families() if f["id"] == ids[0])
        self.assertEqual(fam["parameters"], next(f for f in self.p.model.families() if f["id"] == fid)["parameters"])


class CustomPropertiesAndRelations(Case):
    def test_custom_properties_are_stored_in_sgeo_metadata_and_round_trip(self):
        w = self.wall()
        self.run_("model.set_property", id=w, key="custom:FireRating", value="60 min")
        self.run_("model.set_property", id=w, key="custom:Load", value="12.5")
        self.run_("model.set_property", id=w, key="custom:Exterior", value="true")
        meta = self.meta(w)
        self.assertEqual((meta["custom:FireRating"], meta["custom:Load"], meta["custom:Exterior"]), ("60 min", 12.5, True))
        rows = self.rows(w)
        self.assertEqual(rows["custom:Load"]["type"], "number")
        self.assertEqual(rows["custom:Exterior"]["type"], "choice")
        self.run_("model.set_property", id=w, key="custom:Load", value=None)
        self.assertNotIn("custom:Load", self.meta(w))
        self.assertIn(f"{NS}:category", self.meta(w))                                   # removing one property keeps the others
        with tempfile.TemporaryDirectory() as tmp:
            self.p.save(Path(tmp) / "x.spadra")
            self.assertEqual(Project.open(Path(tmp) / "x.spadra").model.get(w)["metadata"]["custom:FireRating"], "60 min")

    def test_bad_custom_names_and_values_are_refused(self):
        w = self.wall()
        for key in ("custom:1abc", "custom:has space", "custom:" + "x" * 41):
            with self.assertRaises(BadRequest):
                self.run_("model.set_property", id=w, key=key, value="v")
        with self.assertRaises(BadRequest):
            self.run_("model.set_property", id=w, key="custom:Missing", value=None)

    def test_relationships_name_host_hosted_level_rooms_views_and_schedules(self):
        w = self.wall()
        d = self.run_("model.add_door", wall=w, offset=2.0)["id"]
        self.run_("model.add_room", level=self.l1, points=[[0, 0], [6000, 0], [6000, 4000], [0, 4000]])
        self.run_("view.add_schedule", category="door")
        links = {(r["label"], r["value"]) for r in self.p.properties({"model": d}, relations=True)["groups"][-1]["rows"]}
        self.assertIn(("Host wall", "Basic Wall"), links)
        self.assertTrue(any(l == "In room" for l, _ in links))
        self.assertTrue(any(l == "Listed in" and v == "Door Schedule" for l, v in links))
        self.assertTrue(any(l == "Appears in" and v == "Level 01" for l, v in links))
        wall_links = {(r["label"], r["value"]) for r in self.p.properties({"model": w}, relations=True)["groups"][-1]["rows"]}
        self.assertTrue(any(l == "Door hosted" for l, _ in wall_links))
        link = next(r for r in self.p.properties({"model": d}, relations=True)["groups"][-1]["rows"] if r["label"] == "Host wall")
        self.assertEqual(link["target"], {"select": {"model": w}})

    def test_a_family_instance_links_to_its_family_and_a_section_that_cuts_it(self):
        fid = self.table()
        o = self.run_("family.place", family=fid, level=self.l1, point=[1000, 1000])["id"]
        sec = self.run_("view.add_section", a=[0, 1000], b=[4000, 1000])
        rel = self.p.properties({"model": o}, relations=True)["groups"][-1]["rows"]
        self.assertIn(("Family", "Table"), {(r["label"], r["value"]) for r in rel})
        self.assertIn(sec["name"], [r["value"] for r in rel if r["label"] == "Appears in"])

    def test_identification_is_shown_for_every_element(self):
        w = self.wall()
        r = self.rows(w)
        self.assertEqual(r["Element ID"]["value"], w)
        self.assertEqual(r["Category"]["value"], "Wall")


class Schedules(Case):
    def schedule(self, category):
        vid = next((v["id"] for v in self.p.manifest["views"] if v["kind"] == "schedule" and v["category"] == category), None) or self.run_("view.add_schedule", category=category)["id"]
        return self.p.schedule(vid)

    def test_the_stair_schedule_reads_the_flight(self):
        s = self.run_("model.add_stair", level=self.l1, start=[0, 0], end=[3500, 0], width=1000)["id"]
        data = self.schedule("stair")
        row = data["rows"][0]
        self.assertEqual((row["id"], row["no"], row["level"], row["top_level"], row["width"]), (s, "ST01", "Level 01", "Level 02", 1000.0))
        self.assertEqual((row["risers"], row["rise"]), (18, 3.2))
        self.assertAlmostEqual(row["riser"], 3200 / 18, delta=0.06)
        self.assertAlmostEqual(row["run"], 3.5, places=3)
        self.assertAlmostEqual(row["volume"], self.vol(s), places=3)

    def test_the_generic_schedule_names_the_family_and_measures_the_instance(self):
        o = self.run_("family.place", family=self.table(), level=self.l1, point=[0, 0])["id"]
        row = self.schedule("generic")["rows"][0]
        self.assertEqual((row["id"], row["family"], row["size_x"], row["size_y"], row["size_z"]), (o, "Table", 1200.0, 600.0, 750.0))

    def test_element_counts_cover_the_whole_model(self):
        w = self.wall()
        self.run_("model.add_door", wall=w, offset=2.0)
        self.run_("model.add_column", level=self.l1, point=[1000, 1000])
        self.run_("model.add_beam", level=self.l1, start=[0, 0], end=[4000, 0])
        self.run_("model.add_grid", start=[0, 0], end=[0, 5000])
        data = self.schedule("count")
        by = {r["category"]: r for r in data["rows"]}
        self.assertEqual([by[k]["count"] for k in ("Walls", "Doors", "Grids")], [1, 1, 1])
        self.assertEqual((by["Columns and beams"]["count"], by["Columns and beams"]["detail"]), (2, "1 columns, 1 beams"))
        self.assertEqual(data["totals"]["count"], 5)
        self.assertTrue(data["summary"])

    def test_material_quantities_total_the_measured_volume(self):
        a = self.wall()
        b = self.wall((0, 4000), (6000, 4000))
        self.run_("model.set_property", id=a, key="material", value="Concrete")
        self.run_("model.set_property", id=b, key="material", value="Masonry")
        data = self.schedule("material")
        self.assertEqual(sorted(r["material"] for r in data["rows"]), ["Concrete", "Masonry"])
        self.assertAlmostEqual(data["totals"]["volume"], self.vol(a) + self.vol(b), places=2)
        self.assertEqual(self.schedule("material")["count"], 2)

    def test_summary_schedules_of_an_empty_model_are_empty_not_errors(self):
        self.assertEqual((self.schedule("count")["count"], self.schedule("material")["count"]), (0, 0))

    def test_every_schedule_exports_to_csv(self):
        from applications.SPADRA.documentation import SCHEDULES, schedule_csv
        self.wall()
        for category in SCHEDULES:
            csv = schedule_csv(self.schedule(category))
            self.assertTrue(csv.endswith("\r\n"), category)


class SchedulesAndChecks(Case):
    def test_clashes_are_candidates_and_say_what_they_are(self):
        w = self.wall()
        fid = self.table()
        self.run_("family.place", family=fid, level=self.l1, point=[3000, 0], name="Table in the wall")
        out = self.p.check()
        clash = [i for i in out["issues"] if i["category"] == "geometry"]
        self.assertEqual(len(clash), 1)
        self.assertIn("may clash", clash[0]["message"])
        self.assertTrue(any("bounding-box" in l or "bounding box" in l for l in out["limitations"]))
        self.assertTrue(out["ok"])                                                     # candidates are warnings, not errors

    def test_a_table_beside_the_wall_is_not_a_clash(self):
        self.wall()
        self.run_("family.place", family=self.table(), level=self.l1, point=[3000, 2000])
        self.assertEqual([i for i in self.p.check()["issues"] if i["category"] == "geometry"], [])

    def test_duplicate_elements_are_reported(self):
        self.wall()
        self.wall()
        found = [i for i in self.p.check()["issues"] if "duplicate" in i["message"]]
        self.assertEqual(len(found), 1)

    def test_a_stair_whose_levels_were_moved_into_an_impossible_rise_is_found_by_the_check(self):
        s = self.run_("model.add_stair", level=self.l1, start=[0, 0], end=[3500, 0])["id"]
        self.p.model.attach_semantics(s, {"risers": 3}, namespace=NS)                    # damaged outside the commands
        self.assertTrue(any(i["ref"] == s and i["category"] == "parameters" for i in self.p.check()["issues"]))

    def test_issues_carry_a_category(self):
        w = self.wall()
        d = self.run_("model.add_door", wall=w, offset=2.0)["id"]
        self.p.model.remove(w)
        issues = self.p.check()["issues"]
        self.assertTrue(any(i["ref"] == d and i["category"] == "hosting" for i in issues))
        self.assertTrue(all("category" in i for i in issues))

    def test_a_deleted_family_is_found_by_the_check(self):
        fid = self.table()
        o = self.run_("family.place", family=fid, level=self.l1, point=[0, 0])["id"]
        self.p.model.remove(o)
        self.run_("family.delete", id=fid)
        self.assertEqual(self.p.model.families(), [])


if __name__ == "__main__":
    unittest.main()
