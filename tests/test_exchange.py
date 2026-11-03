"""Phase 18, against the real engines and real files made here: the OBJ adapter, content sniffing, pre-flight inspection, conversion reports, and
round trips of geometry, units, identity, parameters, materials, relationships and metadata through every format this installation can run.
Formats whose library is not installed (DXF: ezdxf, IFC: IfcOpenShell, STEP: Open CASCADE) are tested for what they promise without it: a plain
refusal. A conversion that was never run is not claimed."""
from __future__ import annotations

import base64
import importlib.util
import math
import tempfile
import unittest
from pathlib import Path

from applications.SPADRA import interop as IO
from applications.SPADRA.project import NS, Project
from applications.tools.errors import BadRequest, DependencyMissing, LimitExceeded
from src.utils.sdrw import SDRWDocument
from src.utils.sgeo import SGEODocument
from src.utils.spatial_formats.adapters.obj import export_obj, import_obj
from src.utils.spatial_formats.common.errors import ResourceLimitError, UnsupportedFeatureError, ValidationError

from .test_interop import cube_stl

HAS_EZDXF = importlib.util.find_spec("ezdxf") is not None


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


CUBE = {"kind": "mesh", "vertices": [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]],
        "faces": [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [2, 3, 7], [2, 7, 6], [1, 2, 6], [1, 6, 5], [3, 0, 4], [3, 4, 7]]}


class ObjAdapter(unittest.TestCase):
    def test_round_trip_keeps_names_and_exact_coordinates(self):
        data = export_obj([("Wall one", CUBE), ("Ball", {"kind": "mesh", "vertices": [[0.1, 0.2, 0.3], [1.5, 0, 0], [0, 2.25, 0]], "faces": [[0, 1, 2]]})])
        groups, info = import_obj(data)
        self.assertEqual([g[0] for g in groups], ["Wall_one", "Ball"])
        self.assertEqual(groups[0][1]["vertices"], CUBE["vertices"])
        self.assertEqual(groups[1][1]["vertices"][0], [0.1, 0.2, 0.3])                    # repr() round-trips a float exactly
        self.assertEqual((len(groups[0][1]["faces"]), info["triangulated"], info["skipped"]), (12, 0, {}))

    def test_polygons_negative_indices_slashes_continuations_and_comments(self):
        text = b"# a square and a triangle\nv 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0 \nvt 0 0\nvn 0 0 1\ng top\nf 1/1/1 2/1/1 \\\n 3/1/1 4/1/1\nusemtl m\ng other\nf -4//1 -3//1 -2//1\n"
        groups, info = import_obj(text)
        self.assertEqual([(n, len(m["faces"])) for n, m in groups], [("top", 2), ("other", 1)])
        self.assertEqual(info["triangulated"], 1)
        self.assertEqual(info["skipped"], {"texture coordinates": 1, "vertex normals": 1, "material assignments": 1})

    def test_faces_before_any_name_form_an_unnamed_first_object(self):
        groups, info = import_obj(b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
        self.assertEqual(groups[0][0], "")
        self.assertTrue(info["unnamed"])

    def test_malformed_files_are_refused_with_a_reason(self):
        bad = {b"": "invalid OBJ", b"v 0 0\nf 1 1 1\n": "three coordinates", b"v a b c\n": "not a number", b"v 0 0 0\nf 1 2 3\n": "does not exist", b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 0 1 2\n": "does not exist",
               b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2\n": "at least three", b"v 0 0 0\n": "no faces", b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 x 3\n": "not an integer",
               b"\xff\xfe": "UTF-8", b"v 1e999 0 0\nv 0 0 0\nv 0 1 0\nf 1 2 3\n": "finite"}
        for raw, why in bad.items():
            with self.assertRaises((ValidationError, UnsupportedFeatureError), msg=raw) as c:
                import_obj(raw)
            self.assertIn(why, str(c.exception), raw)

    def test_free_form_geometry_is_refused_not_skipped(self):
        with self.assertRaises(UnsupportedFeatureError):
            import_obj(b"v 0 0 0\nv 1 0 0\nv 0 1 0\nv 1 1 0\ncstype bezier\ndeg 1 1\nsurf 0 1 0 1 1 2 3 4\nend\n")

    def test_degenerate_triangles_are_dropped_and_counted(self):
        groups, info = import_obj(b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\nf 1 1 2\n")
        self.assertEqual((len(groups[0][1]["faces"]), info["skipped"]), (1, {"degenerate triangles": 1}))

    def test_only_triangle_meshes_can_be_written(self):
        with self.assertRaises(UnsupportedFeatureError):
            export_obj([("box", {"kind": "box", "size": [1, 1, 1], "center": [0, 0, 0]})])
        with self.assertRaises(UnsupportedFeatureError):
            export_obj([])

    def test_document_level_conversion_reports_what_it_drops(self):
        doc = SGEODocument.new("t", unit="m")
        a = doc.add_box(width=1, depth=2, height=3, name="Box")
        doc.add_group("Group")
        doc.attach_semantics(a, {"note": "x"}, namespace="spadra")
        data, report = doc.to_obj()
        r = report.as_dict()
        self.assertEqual(r["adapter"], "wavefront-obj")
        self.assertIn("semantic metadata", r["dropped"])
        self.assertIn("scene hierarchy (objects are listed flat)", r["dropped"])
        back, _ = SGEODocument.from_obj(data, unit="m")
        self.assertAlmostEqual(back.volume(back.query_ids()[0], unit="m"), 6.0, places=9)

    def test_a_selection_exports_only_what_was_chosen(self):
        doc = SGEODocument.new("t", unit="m")
        a = doc.add_box(width=1, depth=1, height=1, name="A")
        doc.add_box(width=1, depth=1, height=1, name="B")
        data, _ = doc.to_obj([a])
        self.assertEqual(data.decode().count("\no "), 1)


class Sniffing(unittest.TestCase):
    def test_every_format_is_recognised_by_content(self):
        p = Project.create("S")
        l = p.manifest["levels"][0]["id"]
        p.execute("model.add_wall", {"start": [0, 0], "end": [3000, 0], "level": l})
        plan = next(v["id"] for v in p.manifest["views"] if v["kind"] == "plan")
        p.execute("draw.line", {"drawing": plan, "start": [0, 0], "end": [100, 0]})
        glb, _ = p.model.to_glb()
        cases = {"glb": glb, "stl": cube_stl(1.0), "obj": p.model.to_obj()[0], "sgeo": p.model.dumps(), "sdrw": p.drawing(plan).dumps(), "svg": p.drawing(plan).to_svg()[0].encode(),
                 "stl-ascii": b"solid x\n facet normal 0 0 1\n endfacet\nendsolid x\n", "pdf": b"%PDF-1.7\n", "dwg": b"AC1032\x00\x00",
                 "dxf": b"0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nEOF\n", "ifc": b"ISO-10303-21;\nHEADER;\nFILE_SCHEMA(('IFC4'));\nENDSEC;\n", "step": b"ISO-10303-21;\nHEADER;\nFILE_SCHEMA(('AUTOMOTIVE_DESIGN'));\nENDSEC;\n"}
        for want, raw in cases.items():
            raw = raw if isinstance(raw, bytes) else raw.encode()
            self.assertEqual(IO.sniff(raw), want, want)
        self.assertIsNone(IO.sniff(b"hello world"))
        self.assertIsNone(IO.sniff(b"PK\x03\x04garbage"))

    def test_a_renamed_file_is_caught_by_inspection(self):
        out = IO.inspect_file("model", "glb", cube_stl(1.0), name="x.glb")
        self.assertFalse(out["valid"])
        self.assertEqual(out["detected"], "stl")
        self.assertIn("content is a STL file", out["issues"][0]["message"])
        ascii_stl = IO.inspect_file("model", "stl", b"solid x\n facet normal 0 0 1\n endfacet\nendsolid x\n", name="x.stl")
        self.assertIn("ASCII STL", ascii_stl["issues"][0]["message"])
        self.assertFalse(IO.inspect_file("model", "txt", b"abc", name="x")["valid"])
        self.assertFalse(IO.inspect_file("drawing", "stl", cube_stl(1.0), name="x")["valid"])


class Inspection(unittest.TestCase):
    def test_an_stl_says_what_it_holds_and_that_its_unit_is_an_assumption(self):
        out = IO.inspect_file("model", "stl", cube_stl(1000.0), name="cube.stl", unit="mm")
        self.assertTrue(out["valid"])
        self.assertEqual(out["unit"], {"value": "mm", "declared": False})
        self.assertEqual((out["counts"]["objects"], out["counts"]["mesh_triangles"], out["counts"]["mesh_vertices"]), (1, 12, 8))
        self.assertEqual(out["extent"]["size"], [1000.0, 1000.0, 1000.0])
        self.assertAlmostEqual(out["extent"]["metres"], 1.0, places=6)
        self.assertTrue(any("do not state a unit" in i["message"] for i in out["issues"]))
        self.assertEqual(out["report"]["status"], "partial")                     # STL drops names and units

    def test_a_suspicious_size_is_flagged_as_a_probable_wrong_unit(self):
        out = IO.inspect_file("model", "stl", cube_stl(1000.0), name="big.stl", unit="m")                # a 1000 m cube
        self.assertTrue(out["valid"])
        out = IO.inspect_file("model", "stl", cube_stl(1e9), name="big.stl", unit="m")
        self.assertTrue(any("unit may be wrong" in i["message"] for i in out["issues"]))
        tiny = IO.inspect_file("model", "stl", cube_stl(0.001), name="tiny.stl", unit="mm")
        self.assertTrue(any("unit may be wrong" in i["message"] for i in tiny["issues"]))

    def test_a_damaged_file_is_invalid_with_a_reason_and_nothing_raised(self):
        p = Project.create("G")
        p.execute("model.add_wall", {"start": [0, 0], "end": [3000, 0], "level": p.manifest["levels"][0]["id"]})
        raw = p.model.to_glb()[0]
        out = IO.inspect_file("model", "glb", raw[: len(raw) // 2], name="cut.glb")
        self.assertFalse(out["valid"])
        self.assertTrue(out["issues"][0]["message"])
        flipped = bytearray(p.model.dumps())
        flipped[len(flipped) // 2] ^= 0xFF
        self.assertFalse(IO.inspect_file("model", "sgeo", bytes(flipped), name="x.sgeo")["valid"])

    def test_a_native_file_reports_version_unit_and_where_it_came_from(self):
        p = Project.create("N")
        p.execute("model.add_wall", {"start": [0, 0], "end": [3000, 0], "level": p.manifest["levels"][0]["id"]})
        data, _ = IO.export_model(p, "sgeo")
        out = IO.inspect_file("model", "sgeo", data, name="n.sgeo")
        self.assertTrue(out["valid"])
        self.assertEqual((out["unit"]["declared"], out["metadata"]["spadra_project"]), (True, True))
        self.assertTrue(out["version"])
        self.assertEqual(out["report"]["status"], "complete")

    def test_a_drawing_reports_entities_layers_and_units(self):
        p = Project.create("D")
        plan = next(v["id"] for v in p.manifest["views"] if v["kind"] == "plan")
        p.execute("draw.line", {"drawing": plan, "start": [0, 0], "end": [1000, 0]})
        p.execute("draw.circle", {"drawing": plan, "center": [500, 500], "radius": 250})
        out = IO.inspect_file("drawing", "sdrw", p.drawing(plan).dumps(), name="plan.sdrw")
        self.assertTrue(out["valid"])
        self.assertEqual((out["counts"]["entities"], out["counts"]["kinds"], out["unit"]), (2, {"line": 1, "circle": 1}, {"value": "mm", "declared": True}))
        svg = IO.inspect_file("drawing", "svg", p.drawing(plan).to_svg()[0].encode(), name="plan.svg")
        self.assertTrue(svg["valid"])

    def test_the_size_limit_is_enforced_before_parsing(self):
        out = IO.inspect_file("model", "glb", b"glTF" + bytes(IO.MAX_IMPORT_BYTES), name="huge.glb")
        self.assertFalse(out["valid"])
        self.assertIn("larger than", out["issues"][0]["message"])
        with self.assertRaises(LimitExceeded):
            IO.decode("A" * (IO.MAX_IMPORT_BYTES * 2))

    def test_a_missing_library_is_reported_as_missing_not_as_a_bad_file(self):
        if importlib.util.find_spec("ifcopenshell"):
            self.skipTest("IfcOpenShell is installed")
        out = IO.inspect_file("model", "ifc", b"ISO-10303-21;\nHEADER;\nFILE_SCHEMA(('IFC4'));\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n", name="x.ifc")
        self.assertFalse(out["valid"])
        self.assertTrue(out["dependency_missing"])


class Source(unittest.TestCase):
    """A project that has one of everything a building model has."""

    def setUp(self) -> None:
        p = self.p = Project.create("Origin")
        self.l1, self.l2, self.roof = (l["id"] for l in p.manifest["levels"])
        run = lambda op, **kw: p.execute(op, kw)["result"]                                                  # noqa: E731
        self.wall = run("model.add_wall", start=[0, 0], end=[6000, 0], level=self.l1, thickness=250, height=3.2)["id"]
        self.wall2 = run("model.add_wall", start=[6000, 0], end=[6000, 4000], level=self.l1, thickness=250, height=3.2)["id"]
        self.door = run("model.add_door", wall=self.wall, offset=2.0)["id"]
        self.window = run("model.add_window", wall=self.wall2, offset=1.5)["id"]
        self.floor = run("model.add_floor", points=[[0, 0], [6000, 0], [6000, 4000], [0, 4000]], level=self.l1, thickness=200)["id"]
        self.room = run("model.add_room", level=self.l1, points=[[125, 125], [5875, 125], [5875, 3875], [125, 3875]])["id"]
        self.col = run("model.add_column", level=self.l1, point=[3000, 2000])["id"]
        self.stair = run("model.add_stair", level=self.l1, start=[500, 1000], end=[4000, 1000], width=1000)["id"]
        self.grid = run("model.add_grid", start=[0, -1000], end=[0, 5000])["id"]
        fam = run("family.define", name="Table", shape="box", parameters={"width": {"default": 1200, "min": 300, "max": 3000}, "depth": {"expression": "width / 2"}, "height": {"default": 750}})["id"]
        self.table = run("family.place", family=fam, level=self.l1, point=[2000, 2500], parameters={"width": 1800}, rotation=30)["id"]
        run("model.set_property", id=self.wall, key="material", value="Masonry")
        run("model.set_property", id=self.wall, key="custom:FireRating", value="60 min")
        run("model.set_property", id=self.col, key="custom:Load", value="12.5")
        self.volumes = {i: p.model.volume(i, unit="m") for i in E_ids(p) if p.model.get(i).get("geometry")}


def solids(p):
    return [i for i in E_ids(p) if p.model.get(i).get("geometry")]


def E_ids(p):
    from applications.SPADRA import elements as E
    return E.element_ids(p)


class NativeSgeo(Source):
    def roundtrip(self, target=None):
        data, report = IO.export_model(self.p, "sgeo")
        target = target or Project.create("Target")
        out = target.execute("model.import", {"format": "sgeo", "data": b64(data), "name": "origin.sgeo"})["result"]
        return data, report, target, out

    def test_the_export_is_complete_and_says_what_it_carried(self):
        data, report = IO.export_model(self.p, "sgeo")
        self.assertEqual((report["status"], report["accuracy_class"], report["lost"], report["unsupported"]), ("complete", "exact", [], []))
        self.assertEqual((report["converted"]["objects"], report["converted"]["levels"], report["converted"]["families"]), (len(E_ids(self.p)), 3, 1))
        self.assertEqual(report["destination"]["format"], "sgeo")
        self.assertEqual(IO.sniff(data), "sgeo")

    def test_identity_parameters_geometry_and_relationships_survive(self):
        _, report, t, out = self.roundtrip()
        self.assertEqual((out["report"]["status"], out["objects"]), ("complete", len(E_ids(self.p))))
        self.assertEqual(sorted(E_ids(t)), sorted(E_ids(self.p)))                                    # the same ids, because they were free
        self.assertTrue(t.check()["ok"], t.check()["issues"])
        for oid in E_ids(self.p):
            a, b = self.p.model.get(oid), t.model.get(oid)
            self.assertEqual(a["name"], b["name"], oid)
            ma, mb = dict(a["metadata"]), dict(b["metadata"])
            for key in ("level", "top_level"):
                ma.pop(f"{NS}:{key}", None), mb.pop(f"{NS}:{key}", None)                             # levels are matched, not copied: compared below
            self.assertEqual(ma, mb, oid)
            if a.get("geometry"):
                self.assertAlmostEqual(self.p.model.volume(oid, unit="m"), t.model.volume(oid, unit="m"), places=9, msg=oid)
                self.assertEqual(self.p.model.world_bounds(oid), t.model.world_bounds(oid), oid)
        self.assertEqual(t.model.get(self.door)["metadata"][f"{NS}:host"], self.wall)                # relationship
        self.assertEqual(t.model.get(self.table)["family_id"], self.p.model.get(self.table)["family_id"])
        self.assertEqual(t.model.parameter_values(self.table), self.p.model.parameter_values(self.table))
        self.assertEqual(t.model.get(self.wall)["metadata"]["custom:FireRating"], "60 min")           # custom property
        self.assertEqual(t.model.get(self.wall)["metadata"][f"{NS}:material"], "Masonry")             # material
        for oid in (self.wall, self.stair):
            lvl = t.model.get(oid)["metadata"][f"{NS}:level"]
            self.assertEqual(t.level(lvl)["name"], "Level 01")
        self.assertEqual(t.model.get(self.stair)["metadata"][f"{NS}:top_level"], t.manifest["levels"][1]["id"])

    def test_the_unit_is_kept_as_metres_and_converted_to_the_projects_model_unit(self):
        data, _ = IO.export_model(self.p, "sgeo")
        src = SGEODocument.from_bytes(data)
        self.assertEqual(src.unit, self.p.model.unit)
        t = Project.create("Target", unit="m")
        t.execute("model.import", {"format": "sgeo", "data": b64(data)})
        self.assertAlmostEqual(t.model.volume(self.wall, unit="m"), self.volumes[self.wall], places=9)

    def test_levels_missing_from_the_target_are_created_with_their_plans(self):
        t = Project.create("Target")
        t.execute("level.set", {"id": t.manifest["levels"][2]["id"], "name": "Attic"})
        _, _, t, out = self.roundtrip(t)
        names = [l["name"] for l in t.manifest["levels"]]
        self.assertIn("Roof Level", names)
        self.assertEqual(out["report"]["converted"]["levels_created"], 1)
        roof = next(l for l in t.manifest["levels"] if l["name"] == "Roof Level")
        self.assertTrue(any(v.get("level_id") == roof["id"] for v in t.manifest["views"]))

    def test_a_level_at_another_height_is_added_beside_it_with_a_warning(self):
        t = Project.create("Target")
        t.execute("level.set", {"id": t.manifest["levels"][0]["id"], "elevation": 0.5})
        _, _, t, out = self.roundtrip(t)
        self.assertTrue(any("Level 01" in w and "separate level" in w for w in out["report"]["warnings"]))
        self.assertIn("Level 01 (imported)", [l["name"] for l in t.manifest["levels"]])
        self.assertTrue(t.check()["ok"])

    def test_importing_into_the_same_project_gives_new_ids_and_rewrites_references(self):
        data, _ = IO.export_model(self.p, "sgeo")
        n = len(E_ids(self.p))
        out = self.p.execute("model.import", {"format": "sgeo", "data": b64(data)})["result"]
        self.assertEqual(len(E_ids(self.p)), 2 * n)
        self.assertTrue(any("already in use" in w for w in out["report"]["warnings"]))
        new_doors = [i for i in E_ids(self.p) if self.p.model.get(i)["metadata"].get(f"{NS}:category") == "door" and i != self.door]
        self.assertEqual(len(new_doors), 1)
        host = self.p.model.get(new_doors[0])["metadata"][f"{NS}:host"]
        self.assertNotEqual(host, self.wall)                                                         # the copy's door hangs in the copy's wall
        self.assertEqual(self.p.model.get(host)["metadata"][f"{NS}:category"], "wall")
        self.assertEqual(len(self.p.model.families()), 1)                                            # an identical family is reused
        self.assertTrue(self.p.check()["ok"], self.p.check()["issues"])

    def test_an_import_is_one_undoable_revision_and_saves_and_reopens(self):
        data, _ = IO.export_model(self.p, "sgeo")
        t = Project.create("Target")
        revision = t.revision
        t.execute("model.import", {"format": "sgeo", "data": b64(data)})
        self.assertEqual(t.revision, revision + 1)
        t.undo()
        self.assertEqual(E_ids(t), [])
        self.assertEqual(len(t.manifest["levels"]), 3)
        t.redo()
        with tempfile.TemporaryDirectory() as tmp:
            t.save(Path(tmp) / "t.spadra")
            r = Project.open(Path(tmp) / "t.spadra")
        self.assertEqual(sorted(E_ids(r)), sorted(E_ids(self.p)))
        self.assertTrue(r.check()["ok"])

    def test_a_foreign_sgeo_comes_in_as_plain_imported_objects(self):
        doc = SGEODocument.new("Foreign", unit="m")
        doc.add_box(width=1, depth=1, height=1, name="Crate", metadata={"acme:sku": "X1"})
        t = Project.create("Target")
        out = t.execute("model.import", {"format": "sgeo", "data": b64(doc.dumps())})["result"]
        oid = E_ids(t)[0]
        meta = t.model.get(oid)["metadata"]
        self.assertEqual((meta[f"{NS}:category"], meta["acme:sku"], out["objects"]), ("imported", "X1", 1))
        self.assertTrue(any("did not come from SPADRA" in a for a in out["report"]["assumptions"]))

    def test_materials_and_animation_in_a_foreign_file_are_reported_lost(self):
        doc = SGEODocument.new("Foreign", unit="m")
        box = doc.add_box(width=1, depth=1, height=1, name="Crate")
        doc.set_material(box, doc.add_material("Paint", base_color=[1, 0, 0, 1]))
        t = Project.create("Target")
        out = t.execute("model.import", {"format": "sgeo", "data": b64(doc.dumps())})["result"]
        self.assertEqual(out["report"]["status"], "partial")
        self.assertTrue(any("material" in l for l in out["report"]["lost"]))

    def test_a_partial_selection_cannot_be_exported_natively(self):
        with self.assertRaises(BadRequest):
            IO.export_model(self.p, "sgeo", [self.wall])

    def test_a_damaged_native_file_changes_nothing(self):
        data, _ = IO.export_model(self.p, "sgeo")
        t = Project.create("Target")
        before = t.revision
        with self.assertRaises(BadRequest):
            t.execute("model.import", {"format": "sgeo", "data": b64(data[: len(data) // 2])})
        self.assertEqual((t.revision, E_ids(t)), (before, []))


class MeshFormats(Source):
    def test_glb_stl_and_obj_exports_state_what_they_cannot_carry(self):
        for fmt in ("glb", "stl", "obj"):
            data, report = IO.export_model(self.p, fmt)
            self.assertEqual(IO.sniff(data), fmt)
            self.assertEqual(report["status"], "partial", fmt)
            self.assertTrue(any("semantic metadata" in l for l in report["lost"]), fmt)
            self.assertTrue(any("room" in u and "grid" in u or "room(s)" in u for u in report["unsupported"]) or any("grid" in u for u in report["unsupported"]), fmt)
            self.assertTrue(any("famil" in l for l in report["lost"]), fmt)
        stl = IO.export_model(self.p, "stl")[1]
        self.assertTrue(any("object identity" in l for l in stl["lost"]))

    def test_obj_round_trip_keeps_names_geometry_and_the_unit_the_person_declares(self):
        data, report = IO.export_model(self.p, "obj")
        t = Project.create("Target")
        out = t.execute("model.import", {"format": "obj", "data": b64(data), "unit": "m"})["result"]
        meshes = [i for i in self.volumes]
        self.assertEqual(out["objects"], len(meshes))
        self.assertEqual(sorted(t.model.get(i)["name"].replace("_", " ") for i in E_ids(t)), sorted(self.p.model.get(i)["name"] for i in meshes))
        src = sorted(round(v, 6) for v in self.volumes.values())
        dst = sorted(round(t.model.volume(i, unit="m"), 6) for i in solids(t))
        for a, b in zip(src, dst):
            self.assertAlmostEqual(a, b, delta=1e-5 * max(1, a))
        self.assertTrue(all(t.model.get(i)["metadata"][f"{NS}:category"] == "imported" for i in E_ids(t)))
        self.assertTrue(any("interpreted as m" in a for a in out["report"]["assumptions"]))

    def test_glb_round_trip_keeps_world_geometry_within_float32(self):
        data, _ = IO.export_model(self.p, "glb")
        t = Project.create("Target")
        out = t.execute("model.import", {"format": "glb", "data": b64(data)})["result"]
        self.assertEqual(out["report"]["adapter"], "glb-native")
        lo, hi = self.p.model.bounds(), t.model.bounds()
        for a, b in zip(lo[0] + lo[1], hi[0] + hi[1]):
            self.assertAlmostEqual(a, b, places=4)
        self.assertAlmostEqual(sum(t.model.volume(i, unit="m") for i in solids(t)), sum(self.volumes.values()), delta=1e-3 * sum(self.volumes.values()))

    def test_stl_merges_everything_into_one_object_and_says_so(self):
        data, report = IO.export_model(self.p, "stl")
        t = Project.create("Target")
        out = t.execute("model.import", {"format": "stl", "data": b64(data), "unit": "m"})["result"]       # the export report says: coordinates are in the scene unit, metres
        self.assertEqual(out["objects"], 1)
        self.assertEqual(report["adapter"], "binary-stl")
        self.assertTrue(any("'m'" in a for a in report["assumptions"]))
        geometry = t.model.get(E_ids(t)[0])["geometry"]
        verts = geometry["vertices"]
        signed = sum(sum(verts[a][i] * (verts[b][(i + 1) % 3] * verts[c][(i + 2) % 3] - verts[b][(i + 2) % 3] * verts[c][(i + 1) % 3]) for i in range(3)) / 6
                     for a, b, c in geometry["faces"])                  # the signed volume of the triangles themselves: the solids' sum
        self.assertAlmostEqual(signed, sum(self.volumes.values()), delta=1e-3 * sum(self.volumes.values()))
        # walls that meet in a mitre share a face, so the welded surface has edges with four faces and SGEO cannot call it one closed solid
        with self.assertRaises(Exception):
            t.model.volume(E_ids(t)[0], unit="m")

    def test_a_selection_exports_only_those_elements(self):
        data, report = IO.export_model(self.p, "obj", [self.wall, self.col])
        self.assertEqual(report["converted"]["objects"], 2)
        self.assertEqual(data.decode().count("\no "), 2)
        with self.assertRaises(BadRequest):
            IO.export_model(self.p, "obj", ["nope"])
        with self.assertRaises(BadRequest):
            IO.export_model(self.p, "obj", [self.room])                                                # a room has no geometry: nothing to export
        with self.assertRaises(BadRequest):
            IO.export_model(self.p, "step")

    def test_units_survive_a_different_target_unit(self):
        data, _ = IO.export_model(self.p, "glb")                                                       # glb is metres
        t = Project.create("Target", unit="m")
        t.execute("model.import", {"format": "glb", "data": b64(data)})
        self.assertAlmostEqual(sum(t.model.volume(i, unit="m") for i in solids(t)), sum(self.volumes.values()), delta=1e-3 * sum(self.volumes.values()))


class Drawings(Source):
    def setUp(self) -> None:
        super().setUp()
        self.plan = next(v["id"] for v in self.p.manifest["views"] if v.get("level_id") == self.l1)
        self.p.execute("draw.line", {"drawing": self.plan, "start": [0, 0], "end": [1000, 0]})
        self.p.execute("draw.circle", {"drawing": self.plan, "center": [500, 500], "radius": 250})
        self.p.execute("draw.tag", {"drawing": self.plan, "element": self.door, "point": [1500, 500]})

    def target(self):
        t = Project.create("T")
        return t, next(v["id"] for v in t.manifest["views"] if v["kind"] == "plan")

    def test_sdrw_round_trip_is_complete_and_keeps_layers_and_annotation_metadata(self):
        data, report = IO.export_drawing(self.p.drawing(self.plan), "sdrw", name="Plan")
        self.assertEqual((report["status"], report["lost"]), ("complete", []))
        t, plan = self.target()
        out = t.execute("draw.import", {"drawing": plan, "format": "sdrw", "data": b64(data)})["result"]
        self.assertEqual(out["entities"], 3)
        kinds = sorted(e["kind"] for e in t.drawing_data(plan)["entities"])
        self.assertEqual(kinds, ["circle", "line", "text"])
        tag = next(e for e in t.drawing(plan).query_ids(kind="text"))
        self.assertEqual(t.drawing(plan).get(tag)["metadata"]["spadra:tag_of"], self.door)
        self.assertIn("Tags", [l["name"] for l in t.drawing(plan).layers()])

    def test_sdrw_converts_between_drawing_units(self):
        data, _ = IO.export_drawing(self.p.drawing(self.plan), "sdrw", name="Plan")
        t = Project.create("T", unit="m")
        plan = next(v["id"] for v in t.manifest["views"] if v["kind"] == "plan")
        t.execute("draw.import", {"drawing": plan, "format": "sdrw", "data": b64(data)})
        line = next(e for e in t.drawing_data(plan)["entities"] if e["kind"] == "line")
        self.assertAlmostEqual(line["geometry"]["end"][0], 1.0, places=9)

    def test_svg_export_import_round_trip_of_the_supported_subset(self):
        t0, plan0 = self.target()
        t0.execute("draw.line", {"drawing": plan0, "start": [0, 0], "end": [1000, 0]})
        t0.execute("draw.circle", {"drawing": plan0, "center": [500, 500], "radius": 250})
        data, report = IO.export_drawing(t0.drawing(plan0), "svg", name="Plan")
        self.assertEqual(report["adapter"], "svg-subset")
        t, plan = self.target()
        out = t.execute("draw.import", {"drawing": plan, "format": "svg", "data": b64(data)})["result"]
        self.assertEqual(out["entities"], 2)
        circle = next(e for e in t.drawing_data(plan)["entities"] if e["kind"] == "circle")
        self.assertEqual((circle["geometry"]["center"], circle["geometry"]["radius"]), ([500.0, 500.0], 250.0))

    def test_text_in_a_drawing_is_reported_by_the_svg_adapter_not_silently_dropped(self):
        try:
            data, report = IO.export_drawing(self.p.drawing(self.plan), "svg", name="Plan")
        except BadRequest as exc:                                                                      # the subset refuses what it cannot write: that is also honest
            self.assertTrue(str(exc))
            return
        self.assertTrue(report["lost"] or report["approximated"] or report["warnings"] or b"<text" in data)

    @unittest.skipUnless(HAS_EZDXF, "ezdxf is not installed, so DXF conversion was not run here")
    def test_dxf_round_trip_keeps_geometry(self):
        data, report = IO.export_drawing(self.p.drawing(self.plan), "dxf", name="Plan")
        t, plan = self.target()
        out = t.execute("draw.import", {"drawing": plan, "format": "dxf", "data": b64(data), "unit": "mm"})["result"]
        self.assertGreaterEqual(out["entities"], 2)

    @unittest.skipIf(HAS_EZDXF, "ezdxf is installed")
    def test_dxf_and_dwg_without_their_libraries_are_plain_refusals(self):
        with self.assertRaises(DependencyMissing):
            IO.export_drawing(self.p.drawing(self.plan), "dxf", name="Plan")
        with self.assertRaises(BadRequest):
            IO.export_drawing(self.p.drawing(self.plan), "dwg", name="Plan")
        self.assertFalse(IO.availability()["exportDrawing"]["dwg"]["available"])


class Availability(unittest.TestCase):
    def test_every_format_is_accounted_for(self):
        a = IO.availability()
        self.assertEqual(sorted(a["importModel"]), sorted(IO.MODEL_IMPORT))
        self.assertEqual(sorted(a["exportModel"]), sorted(IO.MODEL_EXPORT))
        self.assertEqual(sorted(a["importDrawing"]), sorted(IO.DRAWING_IMPORT))
        self.assertTrue(all(a["importModel"][f]["available"] for f in ("sgeo", "glb", "stl", "obj")))
        self.assertTrue(a["importModel"]["sgeo"]["native"] and a["exportDrawing"]["sdrw"]["native"] and a["report"]["sldc"]["native"])
        for name in ("ifcExport", "step", "gltf", "pdf"):
            self.assertFalse(a["investigated"][name]["available"])
            self.assertTrue(a["investigated"][name]["reason"])
        self.assertTrue(a["exportModel"]["obj"]["limits"])                                             # the adapter's own limitations are passed on


if __name__ == "__main__":
    unittest.main()
