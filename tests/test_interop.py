"""Import and export through SLAI's spatial-format adapters, against real files made here. Formats whose adapter needs a library that is not
installed (DXF: ezdxf, IFC: IfcOpenShell) are tested for what they promise where the library is missing (a plain refusal) and skipped for the
conversion itself: a conversion that was never run is not claimed."""
from __future__ import annotations

import base64
import importlib.util
import struct
import tempfile
import unittest
from pathlib import Path

from applications.SPADRA import interop as IO
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest, DependencyMissing

HAS_EZDXF = importlib.util.find_spec("ezdxf") is not None
HAS_IFC = importlib.util.find_spec("ifcopenshell") is not None


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def cube_stl(edge: float) -> bytes:
    """A binary STL of an axis-aligned cube from the origin, ``edge`` long, outward normals, 12 triangles."""
    c = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
    quads = [((0, 3, 2, 1), (0, 0, -1)), ((4, 5, 6, 7), (0, 0, 1)), ((0, 1, 5, 4), (0, -1, 0)), ((2, 3, 7, 6), (0, 1, 0)), ((1, 2, 6, 5), (1, 0, 0)), ((3, 0, 4, 7), (-1, 0, 0))]
    out = b""
    for q, n in quads:
        for tri in ((q[0], q[1], q[2]), (q[0], q[2], q[3])):
            out += struct.pack("<12fH", *n, *[x * edge for i in tri for x in c[i]], 0)
    return struct.pack("<80sI", b"cube".ljust(80, b" "), 12) + out


def bounds(scene):
    return scene["bounds"]


class Source(unittest.TestCase):
    """A project with a wall, a floor and a plan line to export."""

    def setUp(self) -> None:
        self.a = Project.create("Source")
        self.level = self.a.manifest["levels"][0]["id"]
        self.wall = self.a.execute("model.add_wall", {"start": [0, 0], "end": [4000, 0], "level": self.level, "thickness": 200, "height": 3})["result"]["id"]
        self.a.execute("model.add_floor", {"points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]], "level": self.level, "thickness": 200})
        self.plan = next(v["id"] for v in self.a.manifest["views"] if v["kind"] == "plan")

    def run_import(self, project, fmt, raw, **extra):
        return project.execute("model.import", {"format": fmt, "data": b64(raw), "name": f"file.{fmt}", **extra})["result"]


class Availability(unittest.TestCase):
    def test_reports_what_is_installed_and_why_not(self):
        a = IO.availability()
        self.assertTrue(a["importModel"]["glb"]["available"] and a["importModel"]["stl"]["available"] and a["importDrawing"]["svg"]["available"])
        self.assertEqual(a["importDrawing"]["dxf"]["available"], HAS_EZDXF)
        self.assertEqual(a["exportDrawing"]["dxf"]["available"], HAS_EZDXF)
        self.assertEqual(a["importModel"]["ifc"]["available"], HAS_IFC)
        for key in ("dxf", "ifc"):
            group = a["importDrawing"] if key == "dxf" else a["importModel"]
            if not group[key]["available"]:
                self.assertIn("not installed", group[key]["reason"])
        self.assertFalse(a["exportDrawing"]["dwg"]["available"])                      # no converter is configured, and the answer says so


class ModelImport(Source):
    def test_a_glb_made_by_spadra_comes_back_with_its_geometry(self):
        glb, _ = self.a.model.to_glb()
        b = Project.create("Target")
        out = self.run_import(b, "glb", glb)
        self.assertEqual(out["objects"], 2)
        self.assertEqual(out["report"]["adapter"], "glb-native")
        self.assertIn("accuracy_class", out["report"])
        src, dst = self.a.scene_data(), b.scene_data()
        self.assertEqual(sorted(o["name"] for o in dst["objects"]), sorted(o["name"] for o in src["objects"]))
        for lo, hi in zip(bounds(src), bounds(dst)):
            for x, y in zip(lo, hi):
                self.assertAlmostEqual(x, y, places=6)                                 # GLB stores float32
        self.assertEqual(sum(len(o["vertices"]) for o in dst["objects"]), sum(len(o["vertices"]) for o in src["objects"]))
        self.assertTrue(all(o["category"] == "imported" for o in dst["objects"]))

    def test_an_import_is_one_undoable_revision_and_the_result_saves(self):
        glb, _ = self.a.model.to_glb()
        b = Project.create("Target")
        revision = b.revision
        self.run_import(b, "glb", glb)
        self.assertEqual(b.revision, revision + 1)
        self.assertEqual(len(b.scene_data()["objects"]), 2)
        b.undo()
        self.assertEqual(len(b.scene_data()["objects"]), 0)
        b.redo()
        self.assertTrue(b.check()["ok"])
        with tempfile.TemporaryDirectory() as tmp:
            b.save(Path(tmp) / "t.spadra")
            self.assertEqual(len(Project.open(Path(tmp) / "t.spadra").scene_data()["objects"]), 2)

    def test_imported_objects_have_a_properties_panel(self):
        b = Project.create("Target")
        self.run_import(b, "glb", self.a.model.to_glb()[0])
        oid = b.scene_data()["objects"][0]["id"]
        self.assertTrue(b.properties({"model": oid})["title"])

    def test_an_stl_cube_is_scaled_from_its_declared_unit_to_metres(self):
        for unit, edge in (("mm", 1.0), ("cm", 10.0), ("m", 1000.0)):                  # a 1000-unit cube, in metres
            b = Project.create("S")
            out = self.run_import(b, "stl", cube_stl(1000.0), unit=unit)
            self.assertEqual(out["objects"], 1)
            lo, hi = bounds(b.scene_data())
            for i in range(3):
                self.assertAlmostEqual(hi[i] - lo[i], edge, places=6, msg=unit)

    def test_a_file_that_is_not_what_it_says_is_refused_and_nothing_changes(self):
        b = Project.create("Target")
        revision, undo = b.revision, len(b.undo_stack)
        for fmt, data in (("glb", b64(b"this is not a glb")), ("stl", b64(b"short")), ("glb", "not base64 !!"), ("glb", ""), ("obj", b64(b"x"))):
            with self.assertRaises(BadRequest, msg=f"{fmt} {data[:10]}"):
                b.execute("model.import", {"format": fmt, "data": data})
        self.assertEqual((b.revision, len(b.undo_stack), len(b.scene_data()["objects"])), (revision, undo, 0))

    @unittest.skipIf(HAS_IFC, "IfcOpenShell is installed")
    def test_ifc_without_ifcopenshell_is_a_plain_refusal(self):
        with self.assertRaises(DependencyMissing):
            self.run_import(Project.create("T"), "ifc", b"ISO-10303-21;\nEND-ISO-10303-21;\n")


class DrawingImport(Source):
    def svg(self):
        self.a.execute("draw.line", {"drawing": self.plan, "start": [0, 0], "end": [1000, 0]})
        self.a.execute("draw.circle", {"drawing": self.plan, "center": [500, 500], "radius": 250})
        payload, _ = self.a.drawing(self.plan).to_svg()
        return payload if isinstance(payload, bytes) else payload.encode()

    def test_a_plain_svg_is_added_to_a_plan_with_its_geometry(self):
        b = Project.create("Target")
        plan = next(v["id"] for v in b.manifest["views"] if v["kind"] == "plan")
        out = b.execute("draw.import", {"drawing": plan, "format": "svg", "data": b64(self.svg()), "name": "plan.svg"})["result"]
        self.assertEqual(out["entities"], 2)
        self.assertEqual(out["report"]["adapter"], "svg-subset")
        kinds = sorted(e["kind"] for e in b.drawing_data(plan)["entities"])
        self.assertEqual(kinds, ["circle", "line"])
        line = next(e for e in b.drawing_data(plan)["entities"] if e["kind"] == "line")
        self.assertEqual((line["geometry"]["start"], line["geometry"]["end"]), ([0.0, 0.0], [1000.0, 0.0]))
        b.undo()
        self.assertEqual(b.drawing_data(plan)["entities"], [])

    def test_the_files_own_unit_is_converted_to_the_drawings(self):
        b = Project.create("Target")
        b.manifest["unit"] = "mm"
        plan = next(v["id"] for v in b.manifest["views"] if v["kind"] == "plan")
        svg = self.svg().replace(b'data-unit="mm"', b'data-unit="m"')
        b.execute("draw.import", {"drawing": plan, "format": "svg", "data": b64(svg)})
        line = next(e for e in b.drawing_data(plan)["entities"] if e["kind"] == "line")
        self.assertAlmostEqual(line["geometry"]["end"][0], 1_000_000.0, places=3)         # 1000 m -> mm

    def test_an_svg_outside_the_supported_subset_is_refused_whole(self):
        b = Project.create("Target")
        plan = next(v["id"] for v in b.manifest["views"] if v["kind"] == "plan")
        revision = b.revision
        for bad in (b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><line x1="0" y1="0" x2="1" y2="1"/></svg>',   # root attributes
                    b'<svg xmlns="http://www.w3.org/2000/svg"><g><line x1="0" y1="0" x2="1" y2="1"/></g></svg>',                      # nested content
                    b'<!DOCTYPE svg [<!ENTITY x "y">]><svg xmlns="http://www.w3.org/2000/svg"/>', b"not xml at all", b"\xff\xfe\x00"):
            with self.assertRaises(BadRequest, msg=bad[:30]):
                b.execute("draw.import", {"drawing": plan, "format": "svg", "data": b64(bad)})
        self.assertEqual((b.revision, b.drawing_data(plan)["entities"]), (revision, []))

    @unittest.skipIf(HAS_EZDXF, "ezdxf is installed")
    def test_dxf_without_ezdxf_is_a_plain_refusal_both_ways(self):
        with self.assertRaises(DependencyMissing):
            IO.export_dxf(self.a.drawing(self.plan))
        with self.assertRaises(DependencyMissing):
            self.a.execute("draw.import", {"drawing": self.plan, "format": "dxf", "data": b64(b"0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n")})

    @unittest.skipUnless(HAS_EZDXF, "ezdxf is not installed, so DXF conversion was not run here")
    def test_dxf_round_trip(self):
        self.a.execute("draw.line", {"drawing": self.plan, "start": [0, 0], "end": [1000, 0]})
        self.a.execute("draw.circle", {"drawing": self.plan, "center": [500, 500], "radius": 250})
        data, report = IO.export_dxf(self.a.drawing(self.plan))
        self.assertIn("accuracy_class", report)
        b = Project.create("Target")
        plan = next(v["id"] for v in b.manifest["views"] if v["kind"] == "plan")
        out = b.execute("draw.import", {"drawing": plan, "format": "dxf", "data": b64(data), "unit": "mm"})["result"]
        self.assertEqual(out["entities"], 2)


class ProjectOnlyData(unittest.TestCase):
    """An SGEO export is complete for the model; data SPADRA keeps only in the project (scripts, their provenance log, procedural recipes) is named, not hidden."""

    def setUp(self):
        self.p = Project.create("Only")
        self.level = self.p.manifest["levels"][0]["id"]
        self.wall = self.p.execute("model.add_wall", {"start": [0, 0], "end": [6000, 0], "level": self.level, "thickness": 200, "height": 3})["result"]["id"]

    def test_a_plain_model_exports_complete_with_nothing_lost(self):
        _, report = IO.export_model(self.p, "sgeo")
        self.assertEqual((report["status"], report["lost"]), ("complete", []))

    def test_scripts_and_procedural_recipes_are_reported_as_not_in_the_file(self):
        self.p.execute("script.add", {"name": "s", "source": "def run(model, params):\n    return {'n': 1}\n", "inputs": {}, "outputs": {"n": "integer"}})
        self.p.execute("procedural.facade", {"wall": self.wall, "panel_width": 1200, "panel_height": 1500})
        _, report = IO.export_model(self.p, "sgeo")
        self.assertEqual(report["status"], "partial")
        text = " ".join(report["lost"])
        self.assertIn("1 Python script(s)", text)
        self.assertIn("procedural group recipe", text)


if __name__ == "__main__":
    unittest.main()
